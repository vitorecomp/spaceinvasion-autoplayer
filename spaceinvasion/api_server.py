"""Local HTTP JSON API wrapping the live SpaceInvasion Chrome debug session."""

from __future__ import annotations

from dataclasses import asdict
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
from spaceinvasion.config import StrategyConfig, load_strategy_config
from spaceinvasion.executor import ActionExecutor
from spaceinvasion.fleet import plan_escape_flight
from spaceinvasion.logging_utils import append_api_log
from spaceinvasion.state import GameState, PlanetState
from spaceinvasion.strategy import PlannedAction, plan_actions

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
        login_with_browser(creds=creds, config=cfg)
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
        login_with_browser(creds=creds, config=cfg)


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
                        current_url = str(getattr(client.driver, "current_url", "") or "")
                        client.ensure_in_game()
                        in_game = True
                except SpaceInvasionAuthError:
                    in_game = False
            return {
                "ok": port_open,
                "debug_port": self.debug_port,
                "debug_port_open": port_open,
                "in_game": in_game,
                "url": current_url,
                "remaining_hourly_actions": self._live_executor.remaining_hourly_budget(),
            }

    def get_state(self, *, include_buildings: bool = True) -> dict[str, Any]:
        with self._lock:
            with self._client_factory(self.debug_port) as client:
                state = client.read_full_snapshot(include_buildings=include_buildings)
            return asdict(state)

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
        res = self._pick_executor(dry_run).execute_action(client, state, planned)
        if res.executed:
            after_state = client.read_full_snapshot(include_buildings=True, return_home=False)
            after_plan = plan_actions(after_state, config=self.strategy_config)
        else:
            after_plan = plan_actions(state, config=self.strategy_config)
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

        with self._lock:
            if cmd == "ensure_session":
                self._ensure_session_fn()
                return {"ok": True, "command": cmd, "message": "Session verified."}

            with self._client_factory(self.debug_port) as client:
                if cmd == "plan":
                    state = client.read_full_snapshot(include_buildings=True)
                    actions = plan_actions(state, config=self.strategy_config)
                    return {
                        "ok": True,
                        "command": cmd,
                        "actions": [asdict(a) for a in actions],
                    }

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

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/health":
                    self._send_json(HTTPStatus.OK, bridge.health())
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
