"""Local HTTP JSON API wrapping the live SpaceInvasion Chrome debug session."""

from __future__ import annotations

from dataclasses import asdict, replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from spaceinvasion.auth import SpaceInvasionAuthError, load_credentials
from spaceinvasion.browser import (
    DEFAULT_DEBUG_PORT,
    BrowserConfig,
    is_debug_port_open,
    login_with_browser,
)
from spaceinvasion.client import GameBrowserClient
from spaceinvasion.config import (
    StrategyConfig,
    load_strategy_config,
    save_strategy_config,
    strategy_config_from_dict,
)
from spaceinvasion.executor import ActionExecutor
from spaceinvasion.fleet import plan_escape_flight
from spaceinvasion.logging_utils import append_api_log, redact_sensitive
from spaceinvasion.ranking import (
    build_buildings_eta,
    build_combat_overview,
    build_ranking_evolution,
    load_rank_history,
    record_rank_snapshot,
    simulate_combat_matchup,
)
from spaceinvasion.state import GameState, PlanetState, parse_economy_rates
from spaceinvasion.storage import GameDatabase
from spaceinvasion.strategy import PlannedAction, plan_actions

UI_DIR = Path(__file__).resolve().parent / "ui"
_UI_ASSETS: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}

ALLOWED_MENU_ACTIONS = frozenset(
    {
        "internalHome",
        "internalBuildings",
        "internalEconomy",
        "internalResearch",
        "internalWeaponFactory",
        "internalDefense",
        "internalFleet",
        "internalGalaxy",
        "internalMessages",
        "internalTransmitter",
        "internalAlien",
        "internalSynth",
        "internalSpecs",
        "internalUniverse",
    }
)


class ApiRequestError(ValueError):
    """Raised when an API request payload is malformed or unsupported."""


def ensure_debug_browser_ready(
    *,
    debug_port: int = DEFAULT_DEBUG_PORT,
    env_file: Path = Path(".env"),
    account: str = "default",
) -> None:
    """Ensure Chrome stays open on `debug_port` and logged into the configured universe."""
    if not is_debug_port_open(debug_port):
        creds = load_credentials(env_path=env_file, account=account)
        cfg = BrowserConfig(
            profile_dir=Path(".browser_profile") / creds.account_name,
            debug_port=debug_port,
            detach=True,
        )
        login_with_browser(creds=creds, config=cfg, require_in_game=True)
        return

    try:
        with GameBrowserClient.attach(port=debug_port) as client:
            client.ensure_in_game()
    except SpaceInvasionAuthError:
        creds = load_credentials(env_path=env_file, account=account)
        cfg = BrowserConfig(
            profile_dir=Path(".browser_profile") / creds.account_name,
            debug_port=debug_port,
            attach=True,
            detach=True,
        )
        login_with_browser(creds=creds, config=cfg, require_in_game=True)


class GameApiBridge:
    """Thread-safe facade connecting HTTP API calls to the Chrome debug port."""

    def __init__(
        self,
        *,
        debug_port: int = DEFAULT_DEBUG_PORT,
        env_file: Path = Path(".env"),
        account: str = "default",
        config_path: Path = Path("config.yaml"),
        max_actions_per_hour: Optional[int] = None,
        log_dir: Optional[Path] = Path("logs"),
        db_path: Optional[Path] = None,
        client_factory: Optional[Callable[[int], Any]] = None,
        ensure_session_fn: Optional[Callable[[], None]] = None,
    ) -> None:
        self.debug_port = debug_port
        self.env_file = env_file
        self.account = account
        self.config_path = config_path
        self.strategy_config: StrategyConfig = load_strategy_config(config_path)
        self.log_dir = log_dir
        self._lock = threading.Lock()
        self._last_state: Optional[GameState] = None
        self._last_url: Optional[str] = None
        self._economy_rates: dict[int, dict[str, int]] = {}
        self._rank_history_path: Optional[Path] = (
            (log_dir.parent / "state" / "rank_history.json") if log_dir is not None else None
        )
        resolved_db = (
            db_path
            if db_path is not None
            else ((log_dir.parent / "state" / "spaceinvasion.db") if log_dir is not None else None)
        )
        self._db: Optional[GameDatabase] = (
            GameDatabase(resolved_db, legacy_rank_json=self._rank_history_path)
            if resolved_db is not None
            else None
        )
        if self._db is not None:
            self._rank_samples = self._db.get_rank_samples()
            self._economy_rates.update(self._db.get_all_economy_rates())
        else:
            self._rank_samples = load_rank_history(self._rank_history_path)
        self._bot_control: dict[str, Any] = {"paused": False, "mode_override": None}
        budget = (
            max_actions_per_hour
            if max_actions_per_hour is not None
            else self.strategy_config.max_actions_per_hour
        )
        self._live_executor = ActionExecutor(
            dry_run=False,
            max_actions_per_hour=budget,
            allow_fleetsave=self.strategy_config.allow_fleetsave,
        )
        self._dry_executor = ActionExecutor(
            dry_run=True,
            max_actions_per_hour=budget,
            allow_fleetsave=self.strategy_config.allow_fleetsave,
        )
        self._client_factory = client_factory or (
            lambda port: GameBrowserClient.attach(port=port)
        )
        self._ensure_session_fn = ensure_session_fn or (
            lambda: ensure_debug_browser_ready(
                debug_port=self.debug_port,
                env_file=self.env_file,
                account=self.account,
            )
        )

    def health(self) -> dict[str, Any]:
        with self._lock:
            port_open = is_debug_port_open(self.debug_port)
            in_game = False
            current_url: Optional[str] = None
            if port_open:
                try:
                    with self._client_factory(self.debug_port) as client:
                        try:
                            client.ensure_in_game()
                            in_game = True
                        except SpaceInvasionAuthError:
                            in_game = False
                        current_url = str(getattr(client.driver, "current_url", "") or "")
                except SpaceInvasionAuthError:
                    in_game = False
            self._last_url = current_url
            return {
                "ok": port_open,
                "debug_port": self.debug_port,
                "debug_port_open": port_open,
                "in_game": in_game,
                "url": redact_sensitive(current_url) if current_url else None,
                "remaining_hourly_actions": self._live_executor.remaining_hourly_budget(),
                "bot_control": dict(self._bot_control),
            }

    def get_bot_control(self) -> dict[str, Any]:
        return {"ok": True, **dict(self._bot_control)}

    def _read_log_tail(self, filename: str, lines: int = 40) -> str:
        if self.log_dir is None:
            return ""
        target = self.log_dir / filename
        if not target.exists():
            return ""
        raw_lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        return str(redact_sensitive("\n".join(raw_lines[-lines:])))

    def _merge_economy_rates(self, state: GameState) -> GameState:
        for pid, rates in getattr(self._live_executor, "last_economy_rates", {}).items():
            if rates:
                self._economy_rates[int(pid)] = dict(rates)

        changed = False
        updated_planets: list[PlanetState] = []
        for planet in state.planets:
            if planet.economy_rates:
                self._economy_rates[planet.planet_id] = dict(planet.economy_rates)
                updated_planets.append(planet)
            elif planet.planet_id in self._economy_rates:
                changed = True
                updated_planets.append(
                    replace(planet, economy_rates=dict(self._economy_rates[planet.planet_id]))
                )
            else:
                updated_planets.append(planet)
        return replace(state, planets=tuple(updated_planets)) if changed else state

    def _record_state(self, state: GameState) -> GameState:
        merged = self._merge_economy_rates(state)
        self._last_state = merged
        if self._db is not None:
            self._rank_samples = self._db.record_state_snapshot(merged)
        else:
            self._rank_samples = record_rank_snapshot(self._rank_history_path, merged)
        return merged

    def dashboard(self, *, refresh: bool = False) -> dict[str, Any]:
        """Return combined UI telemetry, plan, buildings ETAs, combat, ranking, config, and logs."""
        with self._lock:
            port_open = is_debug_port_open(self.debug_port)
            in_game = self._last_state is not None
            current_url = self._last_url
            if port_open and (refresh or self._last_state is None):
                try:
                    with self._client_factory(self.debug_port) as client:
                        snap = client.read_full_snapshot(include_buildings=True)
                        self._record_state(snap)
                        current_url = str(getattr(client.driver, "current_url", "") or "")
                        self._last_url = current_url
                        in_game = True
                except SpaceInvasionAuthError:
                    in_game = False

            actions = (
                plan_actions(self._last_state, config=self.strategy_config)
                if self._last_state is not None
                else []
            )
            ranking = (
                build_ranking_evolution(self._last_state, self._rank_samples)
                if self._last_state is not None
                else None
            )
            buildings_eta = (
                build_buildings_eta(self._last_state)
                if self._last_state is not None
                else None
            )
            combat = (
                build_combat_overview(
                    self._last_state,
                    safety_margin=self.strategy_config.combat_safety_margin,
                )
                if self._last_state is not None
                else None
            )
            return {
                "ok": True,
                "health": {
                    "ok": port_open,
                    "debug_port": self.debug_port,
                    "debug_port_open": port_open,
                    "in_game": in_game,
                    "url": redact_sensitive(current_url) if current_url else None,
                    "remaining_hourly_actions": self._live_executor.remaining_hourly_budget(),
                },
                "bot_control": dict(self._bot_control),
                "config": asdict(self.strategy_config),
                "state": redact_sensitive(asdict(self._last_state)) if self._last_state else None,
                "plan": redact_sensitive([asdict(a) for a in actions]),
                "buildings_eta": buildings_eta,
                "combat": combat,
                "ranking": ranking,
                "db_summary": self._db.summary() if self._db is not None else None,
                "logs": {
                    "commander": self._read_log_tail("commander.log"),
                    "game_api": self._read_log_tail("game_api.log"),
                },
            }

    def get_state(self, *, include_buildings: bool = True) -> dict[str, Any]:
        with self._lock:
            with self._client_factory(self.debug_port) as client:
                state = client.read_full_snapshot(include_buildings=include_buildings)
                merged = self._record_state(state)
            return asdict(merged)

    def _pick_executor(self, dry_run: bool) -> ActionExecutor:
        return self._dry_executor if dry_run else self._live_executor

    @staticmethod
    def _resolve_planet(state: GameState, planet_id: Optional[int]) -> PlanetState:
        planet = (
            next((p for p in state.planets if p.planet_id == planet_id), None)
            if planet_id is not None
            else state.active_planet()
        )
        if planet is None:
            raise ApiRequestError(f"Planet {planet_id} not found in state.")
        return planet

    def _find_build_action(
        self,
        state: GameState,
        building_id: int,
        planet_id: Optional[int],
    ) -> PlannedAction:
        planet = self._resolve_planet(state, planet_id)
        offer = next((o for o in planet.building_offers if o.building_id == building_id), None)
        if offer is None:
            from spaceinvasion import game_data
            from spaceinvasion.strategy import _resources_from_dict

            nxt_lvl = planet.building_level(building_id) + 1
            wiki_cost = _resources_from_dict(game_data.building_cost(building_id, nxt_lvl))
            remote_ok = (
                planet.planet_id != state.active_planet_id
                and not wiki_cost.is_zero()
                and planet.resources.can_afford(wiki_cost)
            )
            return PlannedAction(
                priority="P2",
                kind="BUILD",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=building_id,
                target_name=planet.building_names.get(f"b{building_id}", f"Building {building_id}"),
                target_level=nxt_lvl,
                cost=wiki_cost,
                missing=planet.resources.missing(wiki_cost),
                reason="API build command (cross-planet check)" if remote_ok else "API build command (offer unavailable/not unlocked)",
                executable_now=remote_ok,
            )
        can_exec = offer.can_build and planet.resources.can_afford(offer.cost)
        return PlannedAction(
            priority="P2",
            kind="BUILD",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=offer.building_id,
            target_name=offer.name,
            target_level=offer.target_level,
            cost=offer.cost,
            missing=planet.resources.missing(offer.cost),
            reason="API build command",
            executable_now=can_exec,
            action_href=offer.build_href if can_exec else None,
        )

    def _find_research_action(
        self,
        state: GameState,
        research_id: int,
        planet_id: Optional[int],
    ) -> PlannedAction:
        planet = self._resolve_planet(state, planet_id)
        offer = next((o for o in state.research_offers if o.research_id == research_id), None)
        if offer is None:
            return PlannedAction(
                priority="P2",
                kind="RESEARCH",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=research_id,
                target_name=state.research_names.get(f"r{research_id}", f"Research {research_id}"),
                target_level=int(state.researches.get(f"r{research_id}", 0)) + 1,
                reason="API research command (offer unavailable/slot busy)",
                executable_now=False,
            )
        can_exec = offer.can_research and planet.resources.can_afford(offer.cost)
        return PlannedAction(
            priority="P2",
            kind="RESEARCH",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=offer.research_id,
            target_name=offer.name,
            target_level=offer.target_level,
            cost=offer.cost,
            missing=planet.resources.missing(offer.cost),
            reason="API research command",
            executable_now=can_exec,
            action_href=offer.research_href if can_exec else None,
        )

    def _find_ship_action(
        self,
        state: GameState,
        ship_id: int,
        quantity: int,
        planet_id: Optional[int],
    ) -> PlannedAction:
        planet = self._resolve_planet(state, planet_id)
        offer = next((o for o in planet.ship_offers if o.unit_id == ship_id), None)
        if offer is None or not offer.can_build:
            return PlannedAction(
                priority="P2",
                kind="BUILD_SHIP",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=ship_id,
                target_name=f"Ship {ship_id}",
                target_level=max(1, quantity),
                reason="API build_ship command (offer unavailable/not unlocked)",
                executable_now=False,
            )
        can_exec = planet.resources.can_afford(offer.cost)
        return PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=offer.unit_id,
            target_name=offer.name,
            target_level=max(1, quantity),
            cost=offer.cost,
            missing=planet.resources.missing(offer.cost),
            reason="API build_ship command",
            executable_now=can_exec,
        )

    def _execute_and_summarize(
        self,
        client: Any,
        state: GameState,
        cmd: str,
        planned: PlannedAction,
        dry_run: bool,
    ) -> dict[str, Any]:
        executor = self._pick_executor(dry_run)
        res = executor.execute_action(client, state, planned)
        if not dry_run:
            for pid, rates in getattr(executor, "last_economy_rates", {}).items():
                if rates:
                    self._economy_rates[int(pid)] = dict(rates)
                    if self._db is not None:
                        coords = next((p.coords for p in state.planets if p.planet_id == int(pid)), "")
                        self._db.save_economy_rates(int(pid), coords, rates)
        if self._db is not None:
            self._db.record_action(
                source="api",
                command=cmd,
                action=res.action,
                executed=res.executed,
                dry_run=res.dry_run,
                message=res.message,
            )
        if res.executed:
            after_state = self._record_state(
                client.read_full_snapshot(include_buildings=True, return_home=False)
            )
            after_plan = plan_actions(after_state, config=self.strategy_config)
        else:
            merged_state = self._record_state(state)
            after_plan = plan_actions(merged_state, config=self.strategy_config)
        next_action = asdict(after_plan[0]) if after_plan else None
        client.navigate_menu("internalHome")
        return {
            "ok": True,
            "command": cmd,
            "executed": res.executed,
            "dry_run": res.dry_run,
            "message": res.message,
            "action": asdict(res.action),
            "next_action": next_action,
        }

    def handle_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or not payload.get("command"):
            raise ApiRequestError("Request JSON must include a non-empty 'command' field.")

        cmd = str(payload["command"]).strip().lower()
        dry_run = bool(payload.get("dry_run", False))

        if cmd == "config":
            return {"ok": True, "command": cmd, "config": asdict(self.strategy_config)}

        if cmd == "db_summary":
            with self._lock:
                return {
                    "ok": True,
                    "command": cmd,
                    "db": self._db.summary() if self._db is not None else None,
                }

        if cmd == "bot_control":
            with self._lock:
                if "paused" in payload:
                    self._bot_control["paused"] = bool(payload["paused"])
                if "mode_override" in payload:
                    raw_mode = payload["mode_override"]
                    if raw_mode not in (None, "live", "dry-run"):
                        raise ApiRequestError("mode_override must be null, 'live', or 'dry-run'.")
                    self._bot_control["mode_override"] = raw_mode
                return {"ok": True, "command": cmd, "bot_control": dict(self._bot_control)}

        if cmd == "update_config":
            raw_cfg = payload.get("config")
            if not isinstance(raw_cfg, dict):
                raise ApiRequestError("'update_config' requires a 'config' dictionary.")
            merged = {**asdict(self.strategy_config), **raw_cfg}
            new_cfg = strategy_config_from_dict(merged)
            with self._lock:
                save_strategy_config(new_cfg, self.config_path)
                self.strategy_config = new_cfg
                self._live_executor.allow_fleetsave = new_cfg.allow_fleetsave
            return {"ok": True, "command": cmd, "config": asdict(new_cfg)}

        if cmd == "simulate_combat":
            with self._lock:
                state_for_sim = self._last_state or GameState(
                    universe_id=0,
                    user_id=0,
                    nickname="simulator",
                    timestamp=0.0,
                )
                atk_ships = payload.get("attacker_ships") or {}
                def_ships = payload.get("defender_ships") or {}
                def_defense = payload.get("defender_defense") or {}
                def_researches = payload.get("defender_researches") or {}
                tgt_resources = payload.get("target_resources") or {}
                tgt_coords = str(payload.get("target_coords") or "01:001:01")
                margin = float(
                    payload.get("safety_margin", self.strategy_config.combat_safety_margin)
                )
                sim = simulate_combat_matchup(
                    state_for_sim,
                    attacker_ships=atk_ships,
                    defender_ships=def_ships,
                    defender_defense=def_defense,
                    defender_researches=def_researches,
                    target_resources=tgt_resources,
                    target_coords=tgt_coords,
                    safety_margin=margin,
                )
            return {"ok": True, "command": cmd, "simulation": sim}

        with self._lock:
            if cmd == "ensure_session":
                self._ensure_session_fn()
                return {"ok": True, "command": cmd, "message": "Session verified."}

            if cmd == "buildings_eta" and not bool(payload.get("refresh", False)) and self._last_state is not None:
                pid = int(payload["planet_id"]) if payload.get("planet_id") is not None else None
                planet = self._resolve_planet(self._last_state, pid) if pid is not None else self._last_state.active_planet()
                return {
                    "ok": True,
                    "command": cmd,
                    "buildings_eta": build_buildings_eta(self._last_state, planet),
                }

            if cmd == "combat_overview" and not bool(payload.get("refresh", False)) and self._last_state is not None:
                pid = int(payload["planet_id"]) if payload.get("planet_id") is not None else None
                planet = self._resolve_planet(self._last_state, pid) if pid is not None else self._last_state.active_planet()
                return {
                    "ok": True,
                    "command": cmd,
                    "combat": build_combat_overview(
                        self._last_state,
                        planet,
                        safety_margin=self.strategy_config.combat_safety_margin,
                    ),
                }

            with self._client_factory(self.debug_port) as client:
                if cmd == "plan":
                    state = client.read_full_snapshot(include_buildings=True)
                    self._record_state(state)
                    actions = plan_actions(state, config=self.strategy_config)
                    return {
                        "ok": True,
                        "command": cmd,
                        "actions": [asdict(a) for a in actions],
                    }

                if cmd == "buildings_eta":
                    state = self._record_state(client.read_full_snapshot(include_buildings=True))
                    pid = int(payload["planet_id"]) if payload.get("planet_id") is not None else None
                    planet = self._resolve_planet(state, pid) if pid is not None else state.active_planet()
                    return {
                        "ok": True,
                        "command": cmd,
                        "buildings_eta": build_buildings_eta(state, planet),
                    }

                if cmd == "combat_overview":
                    state = self._record_state(
                        client.read_full_snapshot(
                            include_buildings=True,
                            include_shipyard=True,
                            return_home=True,
                        )
                    )
                    pid = int(payload["planet_id"]) if payload.get("planet_id") is not None else None
                    planet = self._resolve_planet(state, pid) if pid is not None else state.active_planet()
                    return {
                        "ok": True,
                        "command": cmd,
                        "combat": build_combat_overview(
                            state,
                            planet,
                            safety_margin=self.strategy_config.combat_safety_margin,
                        ),
                    }

                if cmd == "ranking_evolution":
                    state = self._record_state(client.read_full_snapshot(include_buildings=True))
                    return {
                        "ok": True,
                        "command": cmd,
                        "ranking": build_ranking_evolution(state, self._rank_samples),
                    }

                if cmd == "tick_once":
                    state = client.read_full_snapshot(include_buildings=True, return_home=False)
                    self._record_state(state)
                    actions = plan_actions(state, config=self.strategy_config)
                    ready = next((a for a in actions if a.executable_now), None)
                    if ready is None:
                        client.navigate_menu("internalHome")
                        return {
                            "ok": True,
                            "command": cmd,
                            "executed": False,
                            "dry_run": dry_run,
                            "message": (
                                f"Waiting: {actions[0].target_name} ({actions[0].reason})"
                                if actions
                                else "No pending actions."
                            ),
                            "action": asdict(actions[0]) if actions else None,
                            "next_action": asdict(actions[1]) if len(actions) > 1 else None,
                        }
                    return self._execute_and_summarize(client, state, cmd, ready, dry_run)

                if cmd == "menu":
                    client.ensure_in_game()
                    client._switch_frame("F_Navi")
                    nav_soup = BeautifulSoup(client.driver.page_source or "", "html.parser")
                    items = [
                        {
                            "text": a.get_text(" ", strip=True),
                            "href": str(a.get("href") or "").split("sid=")[0],
                            "onclick": str(a.get("onclick") or ""),
                        }
                        for a in nav_soup.find_all("a", href=True)
                    ]
                    return {"ok": True, "command": cmd, "items": items}

                if cmd == "navigate":
                    action = str(payload.get("action") or "").strip()
                    if action not in ALLOWED_MENU_ACTIONS:
                        raise ApiRequestError(
                            f"Unsupported menu action {action!r}; allowed: {sorted(ALLOWED_MENU_ACTIONS)}"
                        )
                    client.ensure_in_game()
                    html = client.navigate_menu(action)
                    if action == "internalEconomy":
                        rates = parse_economy_rates(html)
                        if rates and self._last_state is not None:
                            active_p = self._last_state.active_planet()
                            if active_p is not None:
                                self._economy_rates[active_p.planet_id] = dict(rates)
                                self._record_state(self._last_state)
                    res_payload: dict[str, Any] = {
                        "ok": True,
                        "command": cmd,
                        "action": action,
                        "html_bytes": len(html),
                    }
                    if payload.get("include_html"):
                        res_payload["html"] = html
                    if payload.get("return_home", False):
                        client.navigate_menu("internalHome")
                    return res_payload

                if cmd == "build":
                    raw_bid = payload.get("building_id")
                    if raw_bid is None:
                        raise ApiRequestError("'build' command requires 'building_id'.")
                    pid = int(payload["planet_id"]) if payload.get("planet_id") is not None else None
                    state = client.read_full_snapshot(include_buildings=True, return_home=False)
                    planned = self._find_build_action(state, int(raw_bid), pid)
                    return self._execute_and_summarize(client, state, cmd, planned, dry_run)

                if cmd == "research":
                    raw_rid = payload.get("research_id")
                    if raw_rid is None:
                        raise ApiRequestError("'research' command requires 'research_id'.")
                    pid = int(payload["planet_id"]) if payload.get("planet_id") is not None else None
                    state = client.read_full_snapshot(
                        include_buildings=False,
                        include_research=True,
                        include_shipyard=False,
                        return_home=False,
                    )
                    planned = self._find_research_action(state, int(raw_rid), pid)
                    return self._execute_and_summarize(client, state, cmd, planned, dry_run)

                if cmd == "build_ship":
                    raw_sid = payload.get("ship_id")
                    if raw_sid is None:
                        raise ApiRequestError("'build_ship' command requires 'ship_id'.")
                    qty = max(1, int(payload.get("quantity", 1)))
                    pid = int(payload["planet_id"]) if payload.get("planet_id") is not None else None
                    state = client.read_full_snapshot(
                        include_buildings=False,
                        include_research=False,
                        include_shipyard=True,
                        return_home=False,
                    )
                    planned = self._find_ship_action(state, int(raw_sid), qty, pid)
                    return self._execute_and_summarize(client, state, cmd, planned, dry_run)

                if cmd == "throttle_building":
                    bid = int(payload.get("building_id", 2))
                    if payload.get("percentage") is None:
                        raise ApiRequestError("'throttle_building' command requires 'percentage' (0..100).")
                    pct = max(0, min(100, (int(payload["percentage"]) // 10) * 10))
                    pid = int(payload["planet_id"]) if payload.get("planet_id") is not None else None
                    state = client.read_full_snapshot(
                        include_buildings=False,
                        include_research=False,
                        include_shipyard=False,
                        return_home=False,
                    )
                    planet = self._resolve_planet(state, pid)
                    label = "Blast furnace" if bid == 2 else planet.building_names.get(f"b{bid}", f"Building b{bid}")
                    planned = PlannedAction(
                        priority="P1",
                        kind="THROTTLE_BUILDING",
                        planet_id=planet.planet_id,
                        planet_coords=planet.coords,
                        target_id=bid,
                        target_name=f"{label} {pct}%",
                        target_level=pct,
                        reason=f"Set {label} rate to {pct}% in internalEconomy",
                        executable_now=True,
                    )
                    return self._execute_and_summarize(client, state, cmd, planned, dry_run)

                if cmd == "fleetsave_plan":
                    pid = int(payload["planet_id"]) if payload.get("planet_id") is not None else None
                    state = client.read_full_snapshot(
                        include_buildings=False,
                        include_research=False,
                        include_shipyard=False,
                        return_home=False,
                    )
                    planet = self._resolve_planet(state, pid)
                    target_coords = str(payload.get("target_coords") or "").strip()
                    if not target_coords:
                        target_coords = next(
                            (p.coords for p in state.planets if p.planet_id != planet.planet_id),
                            planet.coords,
                        )
                    escape = plan_escape_flight(
                        planet,
                        target_coords=target_coords,
                        fuel_reserve_spice=self.strategy_config.fleet_save_fuel_reserve_spice,
                    )
                    return {"ok": True, "command": cmd, "escape_plan": asdict(escape)}

                if cmd == "claim_tutorial":
                    state = client.read_full_snapshot(include_buildings=False, return_home=False)
                    if not (state.tutorial and state.tutorial.can_complete):
                        return {
                            "ok": True,
                            "command": cmd,
                            "executed": False,
                            "dry_run": dry_run,
                            "message": "Tutorial step is not ready to claim.",
                        }
                    planet = state.active_planet()
                    planned = PlannedAction(
                        priority="P2",
                        kind="CLAIM_TUTORIAL",
                        planet_id=planet.planet_id if planet else 0,
                        planet_coords=planet.coords if planet else "",
                        target_name=f"Tutorial {state.tutorial.step}/{state.tutorial.total_steps} Reward",
                        reason="API claim_tutorial command",
                        executable_now=True,
                    )
                    return self._execute_and_summarize(client, state, cmd, planned, dry_run)

                raise ApiRequestError(f"Unknown command {cmd!r}.")


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def create_api_server(
    bridge: GameApiBridge,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> ThreadingHTTPServer:
    """Create a local `ThreadingHTTPServer` bound to `host:port` for `bridge`."""

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_static(self, filename: str, content_type: str) -> None:
            asset_path = UI_DIR / filename
            if not asset_path.exists():
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Missing UI asset {filename}"})
                return
            raw = asset_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path in _UI_ASSETS:
                    filename, ctype = _UI_ASSETS[parsed.path]
                    self._send_static(filename, ctype)
                    return
                if parsed.path == "/health":
                    self._send_json(HTTPStatus.OK, bridge.health())
                    return
                if parsed.path == "/bot_status":
                    self._send_json(HTTPStatus.OK, bridge.get_bot_control())
                    return
                if parsed.path == "/dashboard":
                    qs = parse_qs(parsed.query)
                    ref = qs.get("refresh", ["0"])[0].lower() in ("1", "true", "yes")
                    self._send_json(HTTPStatus.OK, bridge.dashboard(refresh=ref))
                    return
                if parsed.path == "/state":
                    qs = parse_qs(parsed.query)
                    inc = qs.get("include_buildings", ["1"])[0].lower() not in ("0", "false", "no")
                    self._send_json(HTTPStatus.OK, bridge.get_state(include_buildings=inc))
                    return
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Unknown route {parsed.path}"})
            except SpaceInvasionAuthError as exc:
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/command":
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"Unknown route {parsed.path}"})
                return
            payload: Optional[dict[str, Any]] = None
            status_code = HTTPStatus.OK
            body: dict[str, Any] = {}
            try:
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length) if length > 0 else b"{}"
                payload = json.loads(raw.decode("utf-8"))
                body = bridge.handle_command(payload)
            except (ValueError, ApiRequestError) as exc:
                status_code = HTTPStatus.BAD_REQUEST
                body = {"ok": False, "error": str(exc)}
            except SpaceInvasionAuthError as exc:
                status_code = HTTPStatus.SERVICE_UNAVAILABLE
                body = {"ok": False, "error": str(exc)}
            except Exception as exc:
                status_code = HTTPStatus.INTERNAL_SERVER_ERROR
                body = {"ok": False, "error": str(exc)}

            if bridge.log_dir is not None:
                append_api_log(
                    bridge.log_dir,
                    method="POST",
                    path=parsed.path,
                    status=int(status_code),
                    payload=payload,
                    response=body,
                )
            self._send_json(status_code, body)

    return _ReusableThreadingHTTPServer((host, port), Handler)
