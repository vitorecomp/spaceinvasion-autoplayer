"""Isolated E2E test harness: FakeClock, FakeSpaceInvasionServer, FakeBrowserShell, HTTP bridge, and Node UI runner."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict
from html.parser import HTMLParser
import importlib
import inspect
import json
from pathlib import Path
import pkgutil
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time as _real_time
from typing import Any, Callable, Optional
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By

import commander
import spaceinvasion.runtime.api as api_pkg
from spaceinvasion.runtime.api.http_server import create_api_server
import spaceinvasion.runtime.bridge as bridge_pkg
from spaceinvasion.runtime.bridge import GameApiBridge
import spaceinvasion.runtime.commander as commander_pkg
import spaceinvasion.session.browser as browser_mod
import spaceinvasion.session.client as client_mod
from spaceinvasion.session.client import GameBrowserClient
from spaceinvasion.runtime.commander_client import GameApiClient
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.db.config_store import save_strategy_config
import spaceinvasion.session.executor as executor_mod
import spaceinvasion.combat.flight as fleet_mod
import spaceinvasion.combat._rank_history as rank_history_mod
from spaceinvasion.core import catalog as game_data
import spaceinvasion.db.storage as storage_mod
import spaceinvasion.planner.heuristics._fleet_context as fleet_ctx_mod
import spaceinvasion.planner.heuristics.blast_furnace as blast_furnace_mod


def _fmt_dot_int(val: int) -> str:
    return f"{int(val):,}".replace(",", ".")


class FakeClock:
    """Thread-safe virtual clock that never corrupts stdlib `selectors` timeouts."""

    def __init__(self, initial: float = 1_700_000_000.0) -> None:
        self._lock = threading.RLock()
        self._base = float(initial)
        self._start_real = _real_time.monotonic()
        self._virtual_offset = 0.0
        self.sleep_calls: list[float] = []

    def monotonic(self) -> float:
        with self._lock:
            return self._base + (_real_time.monotonic() - self._start_real) + self._virtual_offset

    def time(self) -> float:
        return self.monotonic()

    def advance(self, seconds: float) -> float:
        with self._lock:
            self._virtual_offset += float(seconds)
            return self.monotonic()

    def sleep(self, seconds: float) -> None:
        dt = float(seconds)
        with self._lock:
            self.sleep_calls.append(dt)
            if dt > 0:
                self._virtual_offset += dt


class _ClockTimeProxy:
    """Module-level drop-in replacement for `time` scoped strictly to spaceinvasion modules."""

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock

    def monotonic(self) -> float:
        return self._clock.monotonic()

    def time(self) -> float:
        return self._clock.time()

    def sleep(self, seconds: float) -> None:
        self._clock.sleep(seconds)

    def __getattr__(self, name: str) -> Any:
        return getattr(_real_time, name)


def _default_planets() -> dict[int, dict[str, Any]]:
    return {
        12476: {
            "planet_id": 12476,
            "name": "Homeworld",
            "coords": "02:380:12",
            "planet_type": "normal",
            "temperature": 31,
            "fields_max": 230,
            "resources": [12000, 8000, 6000, 3000],
            "storage": [100000, 100000, 100000, 100000],
            "energy": {"produced": 250, "used": 140},
            "buildings": {
                "b1": 5,
                "b2": 4,
                "b3": 4,
                "b4": 2,
                "b5": 7,
                "b7": 3,
                "b8": 2,
                "b9": 2,
            },
            "ships": {"s1": 6, "s4": 15, "s10": 5},
            "defense": {"d1": 2},
            "economy_rates": {
                "resource1": 100,
                "resource2": 100,
                "resource3": 100,
                "resource4": 100,
                "resource5": 100,
            },
            "building_queue": [],
            "building_offers": None,
            "ship_offers": None,
        }
    }


def _default_galaxy_systems() -> dict[tuple[int, int], list[dict[str, Any]]]:
    return {
        (2, 380): [
            {
                "position": 4,
                "name": "IdlePrime",
                "owner": "SleepyOne",
                "status_color": "yellow",
                "has_debris": False,
                "is_spacia": False,
                "spy_href": "indexInternal.es?action=internalFleet&mission=spio&gal=2&sun=380&pla=4",
            },
            {
                "position": 6,
                "name": "RivalBase",
                "owner": "PlayerX",
                "status_color": "light_red",
                "has_debris": False,
                "is_spacia": False,
                "spy_href": "indexInternal.es?action=internalFleet&mission=spio&gal=2&sun=380&pla=6",
            },
            {
                "position": 9,
                "name": "Spacia Outpost",
                "owner": "Spacia",
                "status_color": "yellow",
                "has_debris": True,
                "is_spacia": True,
                "spy_href": "indexInternal.es?action=internalFleet&mission=spio&gal=2&sun=380&pla=9",
            },
            {
                "position": 12,
                "name": "Homeworld",
                "owner": "ventkx86",
                "status_color": "white",
                "has_debris": False,
                "is_spacia": False,
                "spy_href": None,
            },
        ],
        (2, 381): [
            {
                "position": 5,
                "name": "DustyMoon",
                "owner": "Idle381",
                "status_color": "yellow",
                "has_debris": False,
                "is_spacia": False,
                "spy_href": "indexInternal.es?action=internalFleet&mission=spio&gal=2&sun=381&pla=5",
            },
            {
                "position": 11,
                "name": "Spacia Forward",
                "owner": "Spacia",
                "status_color": "yellow",
                "has_debris": True,
                "is_spacia": True,
                "spy_href": "indexInternal.es?action=internalFleet&mission=spio&gal=2&sun=381&pla=11",
            },
        ],
        (2, 382): [
            {
                "position": 7,
                "name": "QuietColony",
                "owner": "Idle382",
                "status_color": "yellow",
                "has_debris": True,
                "is_spacia": False,
                "spy_href": "indexInternal.es?action=internalFleet&mission=spio&gal=2&sun=382&pla=7",
            },
        ],
    }


class FakeSpaceInvasionServer:
    """Stateful simulated BitMeUp server supporting multi-planet DOM dialects and rolling throttles."""

    def __init__(
        self,
        *,
        clock: Optional[FakeClock] = None,
        universe_id: int = 31,
        user_id: int = 103603,
        nickname: str = "ventkx86",
        sid: str = "abc123def456",
        sid_mode: str = "prefixed",
        p_galaxy_mode: str = "in_frame",
        galaxy_layout: str = "classic_table",
        fleet_form_dialect: str = "standard",
        speedup_factor: float = 1.0,
        newbie_protection: bool = True,
        account_points: int = 1250,
        account_rank: int = 393,
        planets: Optional[dict[int, dict[str, Any]]] = None,
        active_planet_id: Optional[int] = None,
        researches: Optional[dict[str, int]] = None,
        research_offers: Optional[list[dict[str, Any]]] = None,
        tutorial: Optional[dict[str, Any]] = None,
        hostile_alerts: Optional[list[str]] = None,
        unread_messages: Optional[dict[str, int]] = None,
        galaxy_systems: Optional[dict[tuple[int, int], list[dict[str, Any]]]] = None,
        spy_reports: Optional[dict[str, dict[str, Any]]] = None,
        rate_limit_threshold: Optional[int] = None,
        server_cooldown_s: float = 60.0,
        redirect_on_rate_limit: bool = False,
        rate_limit_on_frame: Optional[str] = None,
        async_nav_polls: int = 0,
        async_href_lag: bool = False,
        instant_builds: bool = True,
        deduct_dispatched_ships: bool = False,
        render_yield_s: float = 0.0,
    ) -> None:
        self.clock = clock or FakeClock()
        self.universe_id = universe_id
        self.user_id = user_id
        self.nickname = nickname
        self.sid = sid
        self.sid_mode = sid_mode
        self.p_galaxy_mode = p_galaxy_mode
        self.galaxy_layout = galaxy_layout
        self.fleet_form_dialect = fleet_form_dialect
        self.speedup_factor = speedup_factor
        self.newbie_protection = newbie_protection
        self.account_points = account_points
        self.account_rank = account_rank

        self.planets: dict[int, dict[str, Any]] = planets if planets is not None else _default_planets()
        self.active_planet_id: int = (
            active_planet_id if active_planet_id is not None else next(iter(self.planets.keys()), 0)
        )
        self.researches: dict[str, int] = dict(researches or {"r1": 1, "r2": 2, "r3": 2, "r4": 4, "r7": 2})
        self.research_offers: Optional[list[dict[str, Any]]] = research_offers
        self.tutorial: Optional[dict[str, Any]] = tutorial
        self.hostile_alerts: list[str] = list(hostile_alerts or [])
        self.unread_messages: dict[str, int] = dict(unread_messages or {"total": 0, "spio": 0})
        self.galaxy_systems: dict[tuple[int, int], list[dict[str, Any]]] = (
            galaxy_systems if galaxy_systems is not None else _default_galaxy_systems()
        )
        self.spy_reports: dict[str, dict[str, Any]] = dict(spy_reports or {})
        self.spio_messages: list[str] = []

        self.rate_limit_threshold = rate_limit_threshold
        self.server_cooldown_s = float(server_cooldown_s)
        self.force_rate_limit_remaining = 0
        self.throttle_until = 0.0
        self.redirect_on_rate_limit = redirect_on_rate_limit
        self.rate_limit_on_frame = rate_limit_on_frame
        self.inject_antibot: Optional[str] = None
        self.inject_antibot_on_action: Optional[str] = None
        self.inject_session_expired: bool = False
        self.async_nav_polls = int(async_nav_polls)
        self.async_href_lag = bool(async_href_lag)
        self.inject_intercepted_clicks = 0
        self.inject_stale_clicks = 0
        self.instant_builds = instant_builds
        self.deduct_dispatched_ships = deduct_dispatched_ships
        self.render_yield_s = float(render_yield_s)
        self._render_counter_lock = threading.Lock()
        self._active_renders = 0
        self.concurrent_render_collisions = 0

        self.launched_fleets: list[dict[str, Any]] = []
        self.page_view_log: list[dict[str, Any]] = []
        self.action_log: list[dict[str, Any]] = []
        self._view_timestamps: deque[float] = deque()
        self._planets_snapshot_json: str = self._serialize_planets_json()
        self._last_fleet_payload: dict[str, Any] = {}

    def sid_query(self) -> str:
        if not self.sid or self.sid_mode == "empty":
            return ""
        if self.sid.startswith("sid="):
            return self.sid
        return f"sid={self.sid}"

    def menu_sid_js_value(self) -> str:
        if not self.sid or self.sid_mode == "empty":
            return ""
        if self.sid_mode == "raw":
            return self.sid.removeprefix("sid=")
        if self.sid.startswith("sid="):
            return self.sid
        return f"sid={self.sid}"

    def clear_throttle(self) -> None:
        self.force_rate_limit_remaining = 0
        self.throttle_until = 0.0
        self._view_timestamps.clear()

    def sync_planets_snapshot(self) -> None:
        self._planets_snapshot_json = self._serialize_planets_json()

    def _serialize_planets_json(self) -> str:
        p_list: list[dict[str, Any]] = []
        for pid, p in self.planets.items():
            b_map: dict[str, dict[str, Any]] = {}
            for bkey, lvl in (p.get("buildings") or {}).items():
                bid = int(str(bkey).lstrip("b")) if str(bkey).lstrip("b").isdigit() else 0
                bname = (game_data.BUILDING_BY_ID.get(bid) or {}).get("name", f"Building {bid}")
                b_map[str(bkey)] = {"name": bname, "level": int(lvl)}
            s_map: dict[str, dict[str, Any]] = {}
            for skey, qty in (p.get("ships") or {}).items():
                slug = game_data.SHIP_SLUG_BY_KEY.get(str(skey))
                sname = (game_data.SHIPS.get(slug or "") or {}).get("name", str(skey))
                s_map[str(skey)] = {"name": sname, "level": int(qty)}
            d_map: dict[str, dict[str, Any]] = {}
            for dkey, qty in (p.get("defense") or {}).items():
                slug = game_data.DEFENSE_SLUG_BY_KEY.get(str(dkey))
                dname = (game_data.DEFENSES.get(slug or "") or {}).get("name", str(dkey))
                d_map[str(dkey)] = {"name": dname, "level": int(qty)}
            p_list.append(
                {
                    "id": int(pid),
                    "type": str(p.get("planet_type", "normal")),
                    "name": str(p.get("name", f"Planet {pid}")),
                    "coords": str(p.get("coords", "01:001:01")),
                    "temperature": int(p.get("temperature", 20)),
                    "fields": int(p.get("fields_max", 230)),
                    "resources": list(p.get("resources", [0, 0, 0, 0])),
                    "storage": list(p.get("storage", [100000, 100000, 100000, 100000])),
                    "energy": dict(p.get("energy", {"produced": 0, "used": 0})),
                    "buildings": b_map,
                    "ships": s_map,
                    "defense": d_map,
                }
            )
        r_map: dict[str, dict[str, Any]] = {}
        for rkey, lvl in self.researches.items():
            rname = (game_data.RESEARCH_BY_ID.get(rkey) or {}).get("name", rkey)
            r_map[rkey] = {"name": rname, "level": int(lvl)}
        payload = {
            "info": {
                "si": "classic",
                "v": 1,
                "user": {"userID": self.user_id, "nickname": self.nickname},
                "type": "planet-overview",
                "universe": {"id": self.universe_id},
                "ts": self.clock.time(),
            },
            "planets": p_list,
            "researches": r_map,
            "specs": {"miner": 0, "fleeter": 0},
        }
        return json.dumps(payload)

    def _active_planet(self) -> dict[str, Any]:
        if self.active_planet_id in self.planets:
            return self.planets[self.active_planet_id]
        if not self.planets:
            return {
                "planet_id": 0,
                "name": "Empty",
                "coords": "01:001:01",
                "planet_type": "normal",
                "temperature": 20,
                "fields_max": 230,
                "resources": [0, 0, 0, 0],
                "storage": [100000, 100000, 100000, 100000],
                "energy": {"produced": 0, "used": 0},
                "buildings": {},
                "ships": {},
                "defense": {},
                "building_queue": [],
            }
        first_id = next(iter(self.planets.keys()))
        self.active_planet_id = first_id
        return self.planets[first_id]

    def _apply_new_planet_param(self, qs: dict[str, list[str]]) -> None:
        raw_np = qs.get("newPlanet", [None])[0]
        if raw_np and str(raw_np).isdigit():
            pid = int(raw_np)
            if pid in self.planets:
                self.active_planet_id = pid

    def _record_server_view(self, frame_id: str, action: str, url: str) -> Optional[tuple[str, str]]:
        now = self.clock.monotonic()
        cutoff = now - 60.0
        while self._view_timestamps and self._view_timestamps[0] < cutoff:
            self._view_timestamps.popleft()

        self.page_view_log.append(
            {
                "ts": now,
                "frame": frame_id,
                "action": action,
                "url": url,
            }
        )

        if self.inject_session_expired:
            return (
                "https://spaceinvasion.bitmeup.com/?locale=en",
                "<html><body><div>Session expired. Please log in again.</div></body></html>",
            )

        if self.inject_antibot and (self.inject_antibot_on_action is None or self.inject_antibot_on_action == action):
            if self.inject_antibot == "turnstile":
                return (
                    "https://spaceinvasion.bitmeup.com/cdn-cgi/challenge",
                    '<html><body><div class="cf-turnstile">Cloudflare Turnstile security verification</div></body></html>',
                )
            return (
                url,
                '<html><body><div class="g-recaptcha">Please solve the CAPTCHA challenge</div></body></html>',
            )

        frame_matches = self.rate_limit_on_frame is None or self.rate_limit_on_frame == frame_id
        should_throttle = False
        if frame_matches:
            if self.force_rate_limit_remaining > 0:
                self.force_rate_limit_remaining -= 1
                should_throttle = True
            elif now < self.throttle_until:
                should_throttle = True
            elif self.rate_limit_threshold is not None and len(self._view_timestamps) >= self.rate_limit_threshold:
                should_throttle = True

        self._view_timestamps.append(now)

        if should_throttle:
            self.throttle_until = max(self.throttle_until, now + self.server_cooldown_s)
            err_url = "indexInternal.es?error=ratelimit" if self.redirect_on_rate_limit else url
            return (
                err_url,
                "<html><body><div class='error'>You've made more than 30 page views at last minute. "
                "Please wait a bit before continuing.</div></body></html>",
            )
        return None

    def _planet_switcher_html(self, action: str) -> str:
        sq = self.sid_query()
        opts = []
        for pid, p in self.planets.items():
            sel = ' selected="selected"' if pid == self.active_planet_id else ""
            val = f"indexInternal.es?action={action}&newPlanet={pid}" + (f"&{sq}" if sq else "")
            opts.append(f'<option value="{val}"{sel}>{p["coords"]} {p["name"]}</option>')
        return f'<select name="planet">{"".join(opts)}</select>'

    def render_frame(self, frame_id: str, url: str, form_data: Optional[dict[str, Any]] = None) -> tuple[str, str]:
        with self._render_counter_lock:
            self._active_renders += 1
            if self._active_renders > 1:
                self.concurrent_render_collisions += 1
        try:
            return self._render_frame_inner(frame_id, url, form_data=form_data)
        finally:
            with self._render_counter_lock:
                self._active_renders -= 1

    def _render_frame_inner(
        self, frame_id: str, url: str, form_data: Optional[dict[str, Any]] = None
    ) -> tuple[str, str]:
        if self.render_yield_s > 0.0:
            _real_time.sleep(self.render_yield_s)
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        action = qs.get("action", [""])[0]
        if not action:
            if frame_id == "F_Navi":
                action = "internalMenu"
            elif frame_id == "F_planets":
                action = "internalPlanetsOverview"
            else:
                action = "internalHome"

        fault = self._record_server_view(frame_id, action, url)
        if fault is not None:
            return fault

        self._apply_new_planet_param(qs)
        sq = self.sid_query()
        sq_amp = f"&{sq}" if sq else ""

        if frame_id == "F_Navi":
            sid_js = json.dumps(self.menu_sid_js_value())
            pts_str = _fmt_dot_int(self.account_points)
            spio_cnt = int(self.unread_messages.get("spio", 0))
            msg_links = (
                f'<a href="indexInternal.es?action=internalMessages{sq_amp}">no new messages</a>'
                f'<a href="indexInternal.es?action=internalMessages&type=spio{sq_amp}">Spying ({spio_cnt})</a>'
            )
            nav_items = [
                ("internalHome", "Command central"),
                ("internalBuildings", "Buildings"),
                ("internalEconomy", "Industry"),
                ("internalResearch", "Research"),
                ("internalWeaponFactory", "Weapons factory"),
                ("internalDefense", "Defence"),
                ("internalFleet", "Fleet commands"),
                ("internalUniverse", "Overview"),
            ]
            nav_links = " ".join(f'<a href="indexInternal.es?action={act}{sq_amp}">{lbl}</a>' for act, lbl in nav_items)
            gal_link = '<a href="#" onclick="p_galaxy()">Galaxy</a>'
            html = (
                f"<html><body>"
                f"<script>window.SI_TPL = window.SI_TPL || {{}}; window.SI_TPL.menu = {{sid: {sid_js}}};</script>"
                f"<div>12:00:00 {self.nickname} Points {pts_str} # {self.account_rank}</div>"
                f"{msg_links} {nav_links} {gal_link}"
                f"</body></html>"
            )
            return url, html

        if frame_id == "F_planets":
            self.sync_planets_snapshot()
            return (
                url,
                f'<html><body><script id="json-container" type="application/json">{self._planets_snapshot_json}</script></body></html>',
            )

        # F_Hauptframe routes
        if action == "internalHome":
            if form_data and "tutorialComplete" in form_data and self.tutorial:
                self.tutorial["can_complete"] = False
                self.tutorial["step"] = int(self.tutorial.get("step", 1)) + 1
                self.tutorial["tasks"] = []
                ap = self._active_planet()
                ap["resources"][0] += 5000
                self.action_log.append({"action": "claim_tutorial", "step": self.tutorial["step"]})

            ap = self._active_planet()
            newbie_html = (
                "<div>Up to 10.000 points,no attacks can be launched on you!</div>" if self.newbie_protection else ""
            )
            alerts_html = "".join(
                f'<div class="attack-alert" id="attack_alert_{i}">{msg}</div>'
                for i, msg in enumerate(self.hostile_alerts, start=1)
            )
            tut_html = ""
            if self.tutorial is not None:
                step = self.tutorial.get("step", 1)
                total = self.tutorial.get("total_steps", 11)
                disp = "" if self.tutorial.get("displayed", True) else ' style="display:none"'
                tasks_html = " ".join(str(t.get("description") or "") for t in (self.tutorial.get("tasks") or []))
                reward_txt = self.tutorial.get("reward_text", "")
                claim_form = ""
                if self.tutorial.get("can_complete"):
                    claim_form = (
                        f'<form action="indexInternal.es?action=internalHome{sq_amp}" method="POST">'
                        '<input name="tutorialComplete" type="hidden" value="1"/>'
                        '<input type="submit" value="Mission complete"/></form>'
                    )
                tut_html = (
                    f"<div>Tutorial {step} / {total}</div>"
                    '<a id="tutorial.show" href="#">Show</a>'
                    f'<div id="tutorial.text"{disp}>{tasks_html} '
                    f"You receive the following resources: {reward_txt} {claim_form}</div>"
                )
            q_html = ""
            if ap.get("building_queue"):
                q_txt = ", ".join(f"{q['name']} (Level {q['target_level']})" for q in ap["building_queue"])
                q_html = f'<div id="sp-own-building">{q_txt}</div>'
            html = (
                f"<html><body>"
                f"<script>window.SI_TPL = window.SI_TPL || {{}}; SI_TPL.headerLoad={{planetID:{ap['planet_id']}}};</script>"
                f"{newbie_html}<div>active factor: x{self.speedup_factor}</div>"
                f"{alerts_html}{tut_html}{q_html}"
                f"</body></html>"
            )
            return url, html

        if action == "internalBuildItem":
            ap = self._active_planet()
            if "buildingID" in qs:
                bid = int(qs["buildingID"][0])
                bkey = f"b{bid}"
                cur = int(ap["buildings"].get(bkey, 0))
                nxt = cur + 1
                cost = game_data.building_cost(bid, nxt)
                res = ap["resources"]
                if (
                    res[0] >= cost.get("pig_iron", 0)
                    and res[1] >= cost.get("metal", 0)
                    and res[2] >= cost.get("kryptonite", 0)
                    and res[3] >= cost.get("spice", 0)
                ):
                    res[0] -= cost.get("pig_iron", 0)
                    res[1] -= cost.get("metal", 0)
                    res[2] -= cost.get("kryptonite", 0)
                    res[3] -= cost.get("spice", 0)
                    if self.instant_builds:
                        ap["buildings"][bkey] = nxt
                        edelta = game_data.building_energy_delta(bid, nxt)
                        if edelta >= 0:
                            ap["energy"]["produced"] += edelta
                        else:
                            ap["energy"]["used"] += -edelta
                    else:
                        bname = (game_data.BUILDING_BY_ID.get(bid) or {}).get("name", f"Building {bid}")
                        ap.setdefault("building_queue", []).append(
                            {"name": bname, "building_id": bid, "target_level": nxt}
                        )
                    self.action_log.append({"action": "build", "planet_id": ap["planet_id"], "bid": bid, "level": nxt})
                # Note: intentionally do NOT update _planets_snapshot_json (stale F_planets fidelity)
                _, b_html = self._render_buildings_html(ap, sq_amp, built_msg="Construction queue: Cancel")
                return f"indexInternal.es?action=internalBuildings{sq_amp}", b_html

            if "researchID" in qs:
                rid = int(qs["researchID"][0])
                rkey = f"r{rid}"
                cur = int(self.researches.get(rkey, 0))
                nxt = cur + 1
                info = game_data.RESEARCH_BY_ID.get(rkey) or {}
                cost = ((info.get("levels") or {}).get(nxt) or {}).get("cost") or {}
                res = ap["resources"]
                if (
                    res[0] >= cost.get("pig_iron", 0)
                    and res[1] >= cost.get("metal", 0)
                    and res[2] >= cost.get("kryptonite", 0)
                    and res[3] >= cost.get("spice", 0)
                ):
                    res[0] -= cost.get("pig_iron", 0)
                    res[1] -= cost.get("metal", 0)
                    res[2] -= cost.get("kryptonite", 0)
                    res[3] -= cost.get("spice", 0)
                    if self.instant_builds:
                        self.researches[rkey] = nxt
                    self.action_log.append({"action": "research", "rid": rid, "level": nxt})
                _, r_html = self._render_research_html(ap, sq_amp, active_msg="In research (Cancel)")
                return f"indexInternal.es?action=internalResearch{sq_amp}", r_html

            if "buildShip" in qs and form_data is not None:
                for k, v in form_data.items():
                    m = re.fullmatch(r"ship\[(\d+)\]", str(k))
                    if not m:
                        continue
                    qty = max(0, int(v or 0))
                    if qty <= 0:
                        continue
                    sid_num = int(m.group(1))
                    skey = f"s{sid_num}"
                    slug = game_data.SHIP_SLUG_BY_KEY.get(skey)
                    cost = (game_data.SHIPS.get(slug or "") or {}).get("base_cost") or {}
                    res = ap["resources"]
                    res[0] = max(0, res[0] - int(cost.get("pig_iron", 0)) * qty)
                    res[1] = max(0, res[1] - int(cost.get("metal", 0)) * qty)
                    res[2] = max(0, res[2] - int(cost.get("kryptonite", 0)) * qty)
                    res[3] = max(0, res[3] - int(cost.get("spice", 0)) * qty)
                    ap.setdefault("ships", {})[skey] = int(ap["ships"].get(skey, 0)) + qty
                    self.action_log.append(
                        {"action": "build_ship", "planet_id": ap["planet_id"], "sid": sid_num, "qty": qty}
                    )
                return f"indexInternal.es?action=internalWeaponFactory{sq_amp}", self._render_weapon_factory_html(
                    ap, sq_amp
                )

        if action == "internalBuildings":
            ap = self._active_planet()
            _, b_html = self._render_buildings_html(ap, sq_amp)
            return url, b_html

        if action == "internalResearch":
            ap = self._active_planet()
            _, r_html = self._render_research_html(ap, sq_amp)
            return url, r_html

        if action == "internalWeaponFactory":
            ap = self._active_planet()
            return url, self._render_weapon_factory_html(ap, sq_amp)

        if action == "internalEconomy":
            ap = self._active_planet()
            if form_data:
                rates = ap.setdefault("economy_rates", {})
                for i in range(1, 6):
                    rk = f"resource{i}"
                    if rk in form_data:
                        rates[rk] = int(form_data[rk])
                self.action_log.append({"action": "economy", "planet_id": ap["planet_id"], "rates": dict(rates)})
            return url, self._render_economy_html(ap, sq_amp)

        if action == "internalGalaxy":
            ap = self._active_planet()
            parts = [p.strip() for p in ap["coords"].split(":")]
            def_gal = int(parts[0]) if len(parts) >= 2 else 1
            def_sys = int(parts[1]) if len(parts) >= 2 else 1
            fd = form_data or {}
            raw_gal = (
                fd.get("galaxy") or fd.get("gal") or qs.get("galaxy", [None])[0] or qs.get("gal", [None])[0] or def_gal
            )
            raw_sys = (
                fd.get("system") or fd.get("sun") or qs.get("system", [None])[0] or qs.get("sun", [None])[0] or def_sys
            )
            gal = max(1, int(raw_gal))
            sys_num = max(1, int(raw_sys))
            return url, self._render_galaxy_html(gal, sys_num, sq_amp)

        if action == "internalFleet":
            ap = self._active_planet()
            # Quick-spy link support if mission=spio in query
            if qs.get("mission", [""])[0] == "spio" and qs.get("gal") and qs.get("sun") and qs.get("pla"):
                t_coords = f"{int(qs['gal'][0]):02d}:{int(qs['sun'][0]):03d}:{int(qs['pla'][0]):02d}"
                self._execute_spy_mission(ap, t_coords, probe_count=1)
            return url, self._render_fleet_step1_html(ap, sq_amp)

        if action == "internalSendFleet":
            ap = self._active_planet()
            step = qs.get("step", [""])[0]
            if step == "launch" and form_data is not None:
                return self._handle_fleet_launch(ap, form_data, sq_amp)
            # Step 1 -> Step 2 transition
            if form_data is not None:
                self._last_fleet_payload = dict(form_data)
            return f"indexInternal.es?action=internalSendFleet{sq_amp}", self._render_fleet_step2_html(ap, sq_amp)

        if action == "internalMessages":
            mtype = qs.get("type", ["normal"])[0]
            if mtype == "spio":
                return url, self._render_spio_messages_html()
            return url, "<html><body><div>Messages inbox</div></body></html>"

        return url, f"<html><body><div>Screen: {action}</div></body></html>"

    def _render_buildings_html(
        self,
        ap: dict[str, Any],
        sq_amp: str,
        *,
        built_msg: str = "",
    ) -> tuple[str, str]:
        switcher = self._planet_switcher_html("internalBuildings")
        q_items = ap.get("building_queue") or []
        q_rows = "".join(f"<tr><td>{q['name']} (Level {q['target_level']})</td></tr>" for q in q_items)
        q_table = (
            f'<table class="ecoshadow"><tr><td>Construction queue ( {len(q_items)} Elements )</td></tr>{q_rows}</table>'
        )
        if built_msg:
            q_table += f"<div>{built_msg}</div>"

        custom_offers = ap.get("building_offers")
        cards: list[str] = []
        if custom_offers is not None:
            for o in custom_offers:
                bid = int(o["building_id"])
                name = o["name"]
                cost = o["cost"]
                cost_str = f"{_fmt_dot_int(cost[0])} Pig iron {_fmt_dot_int(cost[1])} Metal {_fmt_dot_int(cost[2])} Kryptonite {_fmt_dot_int(cost[3])} Spice"
                if o.get("can_build", False):
                    act_html = f'<a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID={bid}{sq_amp}">Build level {o["target_level"]}</a>'
                else:
                    act_html = f"<span>{o.get('status_text', 'Not enough resources')}</span>"
                cards.append(
                    f'<div class="ecoshadow build-card b-{bid}">'
                    f'<a href="indexInternal.es?action=internalDetail&type=building&id={bid}{sq_amp}">{name}</a> '
                    f"Required: {cost_str} {act_html}</div>"
                )
        else:
            res = ap["resources"]
            for bid in sorted(game_data.BUILDING_BY_ID.keys()):
                info = game_data.BUILDING_BY_ID[bid]
                if ap.get("planet_type") == "asteroid" and not info.get("asteroid_ok", False):
                    continue
                cur = int(ap["buildings"].get(f"b{bid}", 0))
                nxt = cur + 1
                cost = game_data.building_cost(bid, nxt)
                if not cost:
                    continue
                unmet = game_data.missing_prerequisites(
                    "buildings", bid, buildings=ap["buildings"], researches=self.researches
                )
                cost_str = (
                    f"{_fmt_dot_int(cost.get('pig_iron', 0))} Pig iron "
                    f"{_fmt_dot_int(cost.get('metal', 0))} Metal "
                    f"{_fmt_dot_int(cost.get('kryptonite', 0))} Kryptonite "
                    f"{_fmt_dot_int(cost.get('spice', 0))} Spice"
                )
                afford = (
                    res[0] >= cost.get("pig_iron", 0)
                    and res[1] >= cost.get("metal", 0)
                    and res[2] >= cost.get("kryptonite", 0)
                    and res[3] >= cost.get("spice", 0)
                )
                if not unmet and afford:
                    act_html = f'<a class="bau" href="indexInternal.es?action=internalBuildItem&buildingID={bid}{sq_amp}">Build level {nxt}</a>'
                elif unmet:
                    act_html = "<span>Prerequisites unmet</span>"
                else:
                    act_html = "<span>Not enough resources</span>"
                cards.append(
                    f'<div class="ecoshadow build-card b-{bid}">'
                    f'<a href="indexInternal.es?action=internalDetail&type=building&id={bid}{sq_amp}">{info["name"]}</a> '
                    f"Required: {cost_str} {act_html}</div>"
                )
        return "", f"<html><body>{switcher}{q_table}{''.join(cards)}</body></html>"

    def _render_research_html(
        self,
        ap: dict[str, Any],
        sq_amp: str,
        *,
        active_msg: str = "",
    ) -> tuple[str, str]:
        switcher = self._planet_switcher_html("internalResearch")
        res = ap["resources"]
        cards: list[str] = []
        if self.research_offers is not None:
            for o in self.research_offers:
                rid = int(o["research_id"])
                c = o["cost"]
                cost_str = f"{_fmt_dot_int(c[0])} Pig iron {_fmt_dot_int(c[1])} Metal {_fmt_dot_int(c[2])} Kryptonite {_fmt_dot_int(c[3])} Spice"
                if o.get("can_research", False):
                    act_html = f'<a class="bau" href="indexInternal.es?action=internalBuildItem&researchID={rid}{sq_amp}">Research level {o["target_level"]}</a>'
                else:
                    act_html = f"<span>{o.get('status_text', 'Not enough resources')}</span>"
                cards.append(
                    f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=research&id={rid}{sq_amp}">{o["name"]}</a> '
                    f"Required: {cost_str} {act_html}</td></tr></table>"
                )
        else:
            for rkey, info in game_data.RESEARCH_BY_ID.items():
                rid = int(rkey[1:])
                cur = int(self.researches.get(rkey, 0))
                nxt = cur + 1
                c = ((info.get("levels") or {}).get(nxt) or {}).get("cost") or {}
                if not c:
                    continue
                unmet = game_data.missing_prerequisites(
                    "research", rkey, buildings=ap["buildings"], researches=self.researches
                )
                cost_str = (
                    f"{_fmt_dot_int(c.get('pig_iron', 0))} Pig iron "
                    f"{_fmt_dot_int(c.get('metal', 0))} Metal "
                    f"{_fmt_dot_int(c.get('kryptonite', 0))} Kryptonite "
                    f"{_fmt_dot_int(c.get('spice', 0))} Spice"
                )
                afford = (
                    res[0] >= c.get("pig_iron", 0)
                    and res[1] >= c.get("metal", 0)
                    and res[2] >= c.get("kryptonite", 0)
                    and res[3] >= c.get("spice", 0)
                )
                if not unmet and afford:
                    act_html = f'<a class="bau" href="indexInternal.es?action=internalBuildItem&researchID={rid}{sq_amp}">Research level {nxt}</a>'
                elif unmet:
                    act_html = "<span>Prerequisites unmet</span>"
                else:
                    act_html = "<span>Not enough resources</span>"
                cards.append(
                    f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=research&id={rid}{sq_amp}">{info["name"]}</a> '
                    f"Required: {cost_str} {act_html}</td></tr></table>"
                )
        extra = f"<div>{active_msg}</div>" if active_msg else ""
        return "", f"<html><body>{switcher}{extra}{''.join(cards)}</body></html>"

    def _render_weapon_factory_html(self, ap: dict[str, Any], sq_amp: str) -> str:
        switcher = self._planet_switcher_html("internalWeaponFactory")
        res = ap["resources"]
        cards: list[str] = []
        custom = ap.get("ship_offers")
        if custom is not None:
            for o in custom:
                sid_num = int(o["unit_id"])
                c = o["cost"]
                cost_str = f"{_fmt_dot_int(c[0])} Pig iron {_fmt_dot_int(c[1])} Metal {_fmt_dot_int(c[2])} Kryptonite {_fmt_dot_int(c[3])} Spice"
                inp_html = (
                    f'<input type="text" name="ship[{sid_num}]" value="0"/>'
                    if o.get("can_build", True)
                    else "<span>Locked</span>"
                )
                cards.append(
                    f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=ship&id={sid_num}{sq_amp}">{o["name"]}</a> '
                    f"Available: {o.get('owned', 0)} Required: {cost_str} {inp_html}</td></tr></table>"
                )
        else:
            for skey, slug in game_data.SHIP_SLUG_BY_KEY.items():
                sid_num = int(skey[1:])
                info = game_data.SHIPS.get(slug) or {}
                c = info.get("base_cost") or {}
                owned = int((ap.get("ships") or {}).get(skey, 0))
                unmet = game_data.missing_prerequisites(
                    "ships", slug, buildings=ap["buildings"], researches=self.researches
                )
                cost_str = (
                    f"{_fmt_dot_int(c.get('pig_iron', 0))} Pig iron "
                    f"{_fmt_dot_int(c.get('metal', 0))} Metal "
                    f"{_fmt_dot_int(c.get('kryptonite', 0))} Kryptonite "
                    f"{_fmt_dot_int(c.get('spice', 0))} Spice"
                )
                afford = (
                    res[0] >= c.get("pig_iron", 0)
                    and res[1] >= c.get("metal", 0)
                    and res[2] >= c.get("kryptonite", 0)
                    and res[3] >= c.get("spice", 0)
                )
                inp_html = (
                    f'<input type="text" name="ship[{sid_num}]" value="0"/>'
                    if (not unmet and afford)
                    else "<span>Locked / insufficient</span>"
                )
                cards.append(
                    f'<table><tr><td><a href="indexInternal.es?action=internalDetail&type=ship&id={sid_num}{sq_amp}">{info.get("name", slug)}</a> '
                    f"Available: {owned} Required: {cost_str} {inp_html}</td></tr></table>"
                )
        return (
            f"<html><body>{switcher}"
            f'<form action="indexInternal.es?action=internalBuildItem&buildShip=1{sq_amp}" method="POST">'
            f"{''.join(cards)}"
            f'<input type="submit" value="Build"/></form></body></html>'
        )

    def _render_economy_html(self, ap: dict[str, Any], sq_amp: str) -> str:
        switcher = self._planet_switcher_html("internalEconomy")
        rates = ap.get("economy_rates") or {}
        selects: list[str] = []
        for i in range(1, 6):
            rk = f"resource{i}"
            cur = int(rates.get(rk, 100))
            opts = "".join(
                f'<option value="{pct}"{' selected="selected"' if pct == cur else ""}>{pct}%</option>'
                for pct in range(100, -1, -10)
            )
            selects.append(f'<select name="{rk}">{opts}</select>')
        return (
            f"<html><body>{switcher}"
            f'<form id="ress" action="indexInternal.es?action=internalEconomy{sq_amp}" method="POST">'
            f"{''.join(selects)}"
            f'<input type="submit" value="Calculate"/></form></body></html>'
        )

    def _render_galaxy_html(self, gal: int, sys_num: int, sq_amp: str) -> str:
        by_pos = {int(s["position"]): s for s in self.galaxy_systems.get((gal, sys_num), [])}
        if self.galaxy_layout == "empty":
            by_pos = {}
        header = f'<div class="galaxy-header">Galaxy [{gal:02d}:{sys_num:03d}]</div>'
        nav_form = (
            f'<form action="indexInternal.es?action=internalGalaxy{sq_amp}" method="POST">'
            f'<input type="text" name="galaxy" value="{gal}"/>'
            f'<input type="text" name="system" value="{sys_num}"/>'
            f'<input type="submit" value="Show"/>'
            f"</form>"
        )
        if self.galaxy_layout == "modern_div":
            rows = []
            for pos in range(1, 17):
                s = by_pos.get(pos)
                if s:
                    color = s.get("status_color", "yellow")
                    debris = '<span class="debris">Debris</span>' if s.get("has_debris") else ""
                    spy = f'<a href="{s["spy_href"]}{sq_amp}">Spy</a>' if s.get("spy_href") else ""
                    rows.append(
                        f'<div class="galaxy-row galaxy_{color}" data-pos="{pos}">'
                        f'<span class="pos">{pos}</span> <span class="pname">{s["name"]}</span> '
                        f'<span class="owner">{s["owner"]}</span> {debris} {spy}</div>'
                    )
                else:
                    rows.append(f'<div class="galaxy-row" data-pos="{pos}"><span class="pos">{pos}</span></div>')
            return f"<html><body>{header}{nav_form}{''.join(rows)}</body></html>"

        trs: list[str] = []
        for pos in range(1, 17):
            s = by_pos.get(pos)
            if s:
                color = s.get("status_color", "yellow")
                debris = " debris" if s.get("has_debris") else ""
                spy = f'<a href="{s["spy_href"]}{sq_amp}">Spy</a>' if s.get("spy_href") else ""
                if self.galaxy_layout == "icon_table":
                    trs.append(
                        f'<tr class="galaxy_{color}">'
                        f'<td><img src="p.png" alt="planet"/></td>'
                        f"<td>[{pos:02d}]</td>"
                        f'<td><span class="galaxy_{color}">{s["name"]}</span>{debris}</td>'
                        f"<td>{s['owner']}</td>"
                        f"<td>{spy}</td></tr>"
                    )
                else:
                    trs.append(
                        f'<tr class="galaxy_{color}">'
                        f"<td>{pos}</td>"
                        f'<td><span class="galaxy_{color}">{s["name"]}</span>{debris}</td>'
                        f"<td>{s['owner']}</td>"
                        f"<td>{spy}</td></tr>"
                    )
            else:
                if self.galaxy_layout == "icon_table":
                    trs.append(f"<tr><td></td><td>[{pos:02d}]</td><td></td><td></td><td></td></tr>")
                else:
                    trs.append(f"<tr><td>{pos}</td><td></td><td></td><td></td></tr>")
        return f"<html><body>{header}{nav_form}<table>{''.join(trs)}</table></body></html>"

    def _render_fleet_step1_html(self, ap: dict[str, Any], sq_amp: str) -> str:
        ships = ap.get("ships") or {}
        if self.fleet_form_dialect == "named_fleet":
            inputs = "".join(
                f'<label>{skey}: <input type="text" name="ship_{skey[1:]}" value="0"/></label>'
                for skey, qty in ships.items()
                if int(qty or 0) > 0
            )
            return (
                "<html><body>"
                f'<form name="fleet" action="indexInternal.es?action=internalSendFleet{sq_amp}" method="POST">'
                f"{inputs}"
                '<input type="text" name="gal" value="2"/><input type="text" name="sun" value="380"/><input type="text" name="pla" value="4"/>'
                '<input type="submit" value="Continue"/></form></body></html>'
            )

        # Standard SpaceInvasion dialect:
        # 1) <select name="planet"> holds TARGET FAVOURITES inside #fav_form BEFORE the dispatch form
        # 2) Origin switcher links use ?action=internalFleet&newPlanet=<pid> (matching REQ-UI-012 safely)
        # 3) Dispatch form has action="...internalSendFleet...", ship[N] inputs, gal/sun/pla
        origin_links = " ".join(
            f'<a class="origin-planet-link" href="indexInternal.es?action=internalFleet&newPlanet={pid}{sq_amp}">{p["coords"]} {p["name"]}</a>'
            for pid, p in self.planets.items()
        )
        fav_opts = (
            "".join(f'<option value="{p["coords"]}">Fav {p["name"]}</option>' for p in self.planets.values())
            + '<option value="02:380:04">Fav IdlePrime</option>'
        )
        fav_form = (
            f'<form id="fav_form" action="indexInternal.es?action=internalFleet{sq_amp}" method="POST">'
            f'<select name="planet">{fav_opts}</select>'
            '<input type="submit" value="Load Favourite"/></form>'
        )
        ship_inputs = "".join(
            f'<label>{skey} (max {int(qty)}): <input type="text" name="ship[{skey[1:]}" value="0"/></label>'
            if False
            else f'<label>{skey} (max {int(qty)}): <input type="text" name="ship[{skey[1:]}]" value="0"/></label>'
            for skey, qty in ships.items()
            if int(qty or 0) > 0
        )
        send_form = (
            f'<form action="indexInternal.es?action=internalSendFleet{sq_amp}" method="POST">'
            f"{ship_inputs}"
            '<input type="text" name="gal" value="2"/>'
            '<input type="text" name="sun" value="380"/>'
            '<input type="text" name="pla" value="1"/>'
            '<input type="submit" value="Continue"/>'
            "</form>"
        )
        return (
            f"<html><body>"
            f'<div class="active-origin">Origin: [{ap["coords"]}] id={ap["planet_id"]}</div>'
            f"<div class='origin-switcher'>{origin_links}</div>"
            f"{fav_form}{send_form}</body></html>"
        )

    def _render_fleet_step2_html(self, ap: dict[str, Any], sq_amp: str) -> str:
        if self.fleet_form_dialect == "named_fleet":
            return (
                "<html><body>"
                f'<form action="indexInternal.es?action=internalSendFleet&step=launch{sq_amp}" method="POST">'
                '<label><input type="radio" id="mission_1" name="mission" value="1"/> Attack</label>'
                '<label><input type="radio" id="mission_6" name="mission" value="6"/> Spying</label>'
                '<input type="submit" value="Launch Fleet"/></form></body></html>'
            )
        # Standard dialect includes decoy #fav_form + speed radio value="1" before mission radios!
        return (
            "<html><body>"
            f'<form id="fav_form" action="indexInternal.es?action=internalFleet{sq_amp}" method="POST">'
            '<input type="submit" value="Save Fav"/></form>'
            f'<form id="send_fleet_form" action="indexInternal.es?action=internalSendFleet&step=launch{sq_amp}" method="POST">'
            '<label><input type="radio" name="speed" value="1"/> 10%</label>'
            '<label><input type="radio" name="speed" value="10" checked="checked"/> 100%</label>'
            '<label><input type="radio" id="mission_1" name="mission" value="1"/> Attack</label>'
            '<label><input type="radio" id="mission_3" name="mission" value="3"/> Transport</label>'
            '<label><input type="radio" id="mission_5" name="mission" value="5"/> Escape flight</label>'
            '<label><input type="radio" id="mission_6" name="mission" value="6"/> Spying</label>'
            '<input type="submit" value="Launch Fleet"/></form></body></html>'
        )

    def _execute_spy_mission(self, ap: dict[str, Any], target_coords: str, probe_count: int = 1) -> None:
        rep = self.spy_reports.get(target_coords)
        if rep is None:
            rep = {
                "owner": "Unknown",
                "resources": {"pig_iron": 15000, "metal": 10000, "kryptonite": 5000, "spice": 2000},
                "fleet": {},
                "defense": {"d1": 1},
                "dug_in": False,
                "is_spacia": False,
            }
        res = rep.get("resources") or {}
        parts = [
            f"Espionage report for {target_coords}",
            f"{'Spacia' if rep.get('is_spacia') else rep.get('owner', '')}",
            f"Pig iron: {_fmt_dot_int(res.get('pig_iron', 0))}",
            f"Metal: {_fmt_dot_int(res.get('metal', 0))}",
            f"Kryptonite: {_fmt_dot_int(res.get('kryptonite', 0))}",
            f"Spice: {_fmt_dot_int(res.get('spice', 0))}",
        ]
        for skey, qty in (rep.get("fleet") or {}).items():
            slug = game_data.SHIP_SLUG_BY_KEY.get(skey)
            uname = (game_data.SHIPS.get(slug or "") or {}).get("name", skey)
            parts.append(f"{uname}: {_fmt_dot_int(qty)}")
        for dkey, qty in (rep.get("defense") or {}).items():
            slug = game_data.DEFENSE_SLUG_BY_KEY.get(dkey)
            uname = (game_data.DEFENSES.get(slug or "") or {}).get("name", dkey)
            parts.append(f"{uname}: {_fmt_dot_int(qty)}")
        if rep.get("dug_in") is True:
            parts.append("Defenses are dug-in")
        elif rep.get("dug_in") is False:
            parts.append("Defenses are not dug-in")
        if rep.get("uninhabited_fields") is not None:
            parts.append(f"approx. {rep['uninhabited_fields']} fields")

        msg_html = f'<div class="spio-report">{" ".join(parts)}</div>'
        self.spio_messages.append(msg_html)
        self.unread_messages["spio"] = int(self.unread_messages.get("spio", 0)) + 1
        self.launched_fleets.append(
            {
                "ts": self.clock.time(),
                "origin_planet_id": ap["planet_id"],
                "origin_coords": ap["coords"],
                "target_coords": target_coords,
                "mission": "6",
                "ships": {"s10": probe_count},
            }
        )

    def _handle_fleet_launch(self, ap: dict[str, Any], form_data: dict[str, Any], sq_amp: str) -> tuple[str, str]:
        p1 = self._last_fleet_payload or {}
        gal = int(p1.get("gal") or form_data.get("gal") or 1)
        sun = int(p1.get("sun") or form_data.get("sun") or 1)
        pla = int(p1.get("pla") or form_data.get("pla") or 1)
        target_coords = f"{gal:02d}:{sun:03d}:{pla:02d}"
        mission = str(form_data.get("mission") or "1")

        ships_sent: dict[str, int] = {}
        for k, v in p1.items():
            m = re.fullmatch(r"ship(?:_(\d+)|\[(\d+)\])", str(k))
            if not m:
                continue
            sid_num = int(m.group(1) or m.group(2))
            qty = max(0, int(v or 0))
            if qty > 0:
                skey = f"s{sid_num}"
                ships_sent[skey] = qty
                if self.deduct_dispatched_ships:
                    ap["ships"][skey] = max(0, int(ap["ships"].get(skey, 0)) - qty)

        if mission == "6":
            self._execute_spy_mission(ap, target_coords, probe_count=ships_sent.get("s10", 1))
        else:
            self.launched_fleets.append(
                {
                    "ts": self.clock.time(),
                    "origin_planet_id": ap["planet_id"],
                    "origin_coords": ap["coords"],
                    "target_coords": target_coords,
                    "mission": mission,
                    "ships": ships_sent,
                }
            )
        return (
            f"indexInternal.es?action=internalFleet{sq_amp}",
            f"<html><body><div class='success'>Fleet dispatched to {target_coords} (mission={mission}).</div></body></html>",
        )

    def _render_spio_messages_html(self) -> str:
        self.unread_messages["spio"] = 0
        body = "".join(self.spio_messages) if self.spio_messages else "<div>No espionage messages.</div>"
        return f"<html><body>{body}</body></html>"


class _FrameState:
    def __init__(self, frame_id: str, url: str, html: str) -> None:
        self.frame_id = frame_id
        self.url = url
        self.previous_url = url
        self.html = html
        self.soup = BeautifulSoup(html, "html.parser")
        self.gen_id = 1
        self.ready_polls_remaining = 0
        self.pending_url: Optional[str] = None
        self.pending_html: Optional[str] = None
        self.window_vars: dict[str, Any] = {}
        self.hidden_ids: set[str] = set()
        self._init_hidden()

    def _init_hidden(self) -> None:
        self.hidden_ids.clear()
        for el in self.soup.find_all(id=True):
            style = str(el.get("style") or "").replace(" ", "").lower()
            if "display:none" in style:
                self.hidden_ids.add(str(el["id"]))

    def commit_now(self, new_url: str, new_html: str) -> None:
        self.previous_url = self.url
        self.url = new_url
        self.html = new_html
        self.soup = BeautifulSoup(new_html, "html.parser")
        self.gen_id += 1
        self.window_vars.clear()
        self.ready_polls_remaining = 0
        self.pending_url = None
        self.pending_html = None
        self._init_hidden()

    def start_async(self, new_url: str, new_html: str, polls: int) -> None:
        self.pending_url = new_url
        self.pending_html = new_html
        self.ready_polls_remaining = polls

    def maybe_advance_poll(self) -> None:
        if self.ready_polls_remaining <= 0:
            return
        self.ready_polls_remaining -= 1
        if self.ready_polls_remaining == 0 and self.pending_url is not None and self.pending_html is not None:
            self.commit_now(self.pending_url, self.pending_html)


def _find_tags(root: Any, by: str, value: str) -> list[Any]:
    if by == By.ID:
        hit = root.find(id=value)
        return [hit] if hit is not None else []
    if by == By.NAME:
        return list(root.find_all(attrs={"name": value}))
    if by == By.TAG_NAME:
        return list(root.find_all(value))
    if by == By.CLASS_NAME:
        return list(root.select(f".{value}"))
    if by == By.CSS_SELECTOR:
        return list(root.select(value))
    if by == By.LINK_TEXT:
        return [a for a in root.find_all("a") if a.get_text(strip=True) == value]
    if by == By.PARTIAL_LINK_TEXT:
        return [a for a in root.find_all("a") if value in a.get_text(strip=True)]
    raise ValueError(f"Unsupported By selector: {by!r}")


class FakeWebElement:
    """bs4-backed Selenium WebElement supporting Select, forms, inputs, clicks, and staleness."""

    def __init__(self, driver: FakeDriver, frame_id: Optional[str], gen_id: int, tag: Any) -> None:
        self._driver = driver
        self._frame_id = frame_id
        self._gen_id = gen_id
        self._tag = tag

    def _assert_fresh(self) -> Optional[_FrameState]:
        if self._frame_id is None:
            return None
        frame = self._driver._frames[self._frame_id]
        if self._gen_id != frame.gen_id:
            raise StaleElementReferenceException(f"Stale element in {self._frame_id}")
        return frame

    @property
    def tag_name(self) -> str:
        self._assert_fresh()
        return str(self._tag.name or "").lower()

    @property
    def text(self) -> str:
        self._assert_fresh()
        return " ".join(self._tag.get_text(" ", strip=True).split())

    def get_dom_attribute(self, name: str) -> Optional[str]:
        self._assert_fresh()
        val = self._tag.get(name)
        if val is None:
            return None
        if isinstance(val, list):
            return " ".join(str(x) for x in val)
        return str(val)

    def get_attribute(self, name: str) -> Optional[str]:
        self._assert_fresh()
        if name == "textContent":
            return self._tag.get_text()
        val = self._tag.get(name)
        if name in ("selected", "checked", "disabled", "multiple"):
            return "true" if self._tag.has_attr(name) else None
        if name == "value" and self._tag.name == "option" and val is None:
            return self._tag.get_text(strip=True)
        if val is None:
            return None
        if isinstance(val, list):
            return " ".join(str(x) for x in val)
        return str(val)

    def is_displayed(self) -> bool:
        frame = self._assert_fresh()
        el_id = str(self._tag.get("id") or "")
        if frame is not None and el_id in frame.hidden_ids:
            return False
        style = str(self._tag.get("style") or "").replace(" ", "").lower()
        return "display:none" not in style

    def is_enabled(self) -> bool:
        self._assert_fresh()
        return not self._tag.has_attr("disabled")

    def is_selected(self) -> bool:
        self._assert_fresh()
        return self._tag.has_attr("selected") or self._tag.has_attr("checked")

    def find_element(self, by: str, value: str) -> FakeWebElement:
        self._assert_fresh()
        hits = _find_tags(self._tag, by, value)
        if not hits:
            raise NoSuchElementException(f"No element matching {by}={value!r}")
        return FakeWebElement(self._driver, self._frame_id, self._gen_id, hits[0])

    def find_elements(self, by: str, value: str) -> list[FakeWebElement]:
        self._assert_fresh()
        return [FakeWebElement(self._driver, self._frame_id, self._gen_id, t) for t in _find_tags(self._tag, by, value)]

    def clear(self) -> None:
        self._assert_fresh()
        self._tag["value"] = ""

    def send_keys(self, keys: str) -> None:
        self._assert_fresh()
        cur = str(self._tag.get("value") or "")
        self._tag["value"] = cur + str(keys)

    def click(self) -> None:
        self._assert_fresh()
        if self._driver.server.inject_stale_clicks > 0 and self._frame_id == "F_Hauptframe":
            self._driver.server.inject_stale_clicks -= 1
            if self._frame_id is not None:
                self._driver._frames[self._frame_id].gen_id += 1
            raise StaleElementReferenceException("Simulated stale click from countdown auto-reload")
        if self._driver.server.inject_intercepted_clicks > 0 and self._frame_id == "F_Hauptframe":
            self._driver.server.inject_intercepted_clicks -= 1
            raise ElementClickInterceptedException("Simulated rahmen border click interception")
        self._perform_click()

    def _js_click(self) -> None:
        self._assert_fresh()
        self._perform_click()

    def _perform_click(self) -> None:
        frame = self._assert_fresh()
        tag_name = str(self._tag.name or "").lower()

        if tag_name == "option":
            parent_sel = self._tag.find_parent("select")
            if parent_sel is not None:
                for opt in parent_sel.find_all("option"):
                    if "selected" in opt.attrs:
                        del opt.attrs["selected"]
            self._tag["selected"] = "selected"
            opt_val = str(self._tag.get("value") or "")
            if parent_sel is not None and parent_sel.get("name") == "planet":
                if "newPlanet=" in opt_val:
                    self._driver._navigate_frame("F_Hauptframe", opt_val)
                else:
                    # Target favourites dropdown: populate gal/sun/pla in nearest/sibling dispatch form
                    m = re.fullmatch(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", opt_val.strip())
                    if m and frame is not None:
                        for fname, fval in (("gal", m.group(1)), ("sun", m.group(2)), ("pla", m.group(3))):
                            inp = frame.soup.find("input", attrs={"name": fname})
                            if inp is not None:
                                inp["value"] = str(int(fval))
            return

        if tag_name == "input" and str(self._tag.get("type") or "").lower() == "radio":
            rname = self._tag.get("name")
            form = self._tag.find_parent("form")
            scope = form or (frame.soup if frame else None)
            if scope is not None and rname:
                for r in scope.find_all("input", attrs={"type": "radio", "name": rname}):
                    if "checked" in r.attrs:
                        del r.attrs["checked"]
            self._tag["checked"] = "checked"
            return

        if tag_name == "input" and str(self._tag.get("type") or "").lower() == "checkbox":
            if self._tag.has_attr("checked"):
                del self._tag.attrs["checked"]
            else:
                self._tag["checked"] = "checked"
            return

        if tag_name == "a":
            if str(self._tag.get("id") or "") == "tutorial.show" and frame is not None:
                frame.hidden_ids.discard("tutorial.text")
                return
            onclick = str(self._tag.get("onclick") or "")
            if "p_galaxy" in onclick:
                if self._driver.server.p_galaxy_mode == "in_frame":
                    sq = self._driver.server.sid_query()
                    sq_amp = f"&{sq}" if sq else ""
                    self._driver._navigate_frame("F_Hauptframe", f"indexInternal.es?action=internalGalaxy{sq_amp}")
                return
            href = str(self._tag.get("href") or "")
            if href and href != "#" and not href.startswith("javascript:"):
                target_frame = (
                    "F_Hauptframe"
                    if self._frame_id in ("F_Navi", "F_Hauptframe")
                    else (self._frame_id or "F_Hauptframe")
                )
                self._driver._navigate_frame(target_frame, href)
            return

        if (tag_name == "input" and str(self._tag.get("type") or "").lower() == "submit") or (tag_name == "button"):
            form = self._tag.find_parent("form")
            if form is not None:
                form_action = str(
                    form.get("action") or (frame.url if frame else "indexInternal.es?action=internalHome")
                )
                data: dict[str, Any] = {}
                for inp in form.find_all("input"):
                    itype = str(inp.get("type") or "text").lower()
                    iname = str(inp.get("name") or "")
                    if not iname:
                        continue
                    if itype in ("radio", "checkbox"):
                        if inp.has_attr("checked"):
                            data[iname] = str(inp.get("value") or "on")
                    elif itype != "submit":
                        data[iname] = str(inp.get("value") or "")
                for sel in form.find_all("select"):
                    sname = str(sel.get("name") or "")
                    if not sname:
                        continue
                    s_opt = sel.find("option", selected=True) or sel.find("option")
                    if s_opt is not None:
                        data[sname] = str(s_opt.get("value") or s_opt.get_text(strip=True))
                self._driver._navigate_frame("F_Hauptframe", form_action, form_data=data)
            return


class FakeSwitchTo:
    def __init__(self, driver: FakeDriver) -> None:
        self._driver = driver

    def default_content(self) -> None:
        self._driver._current_frame = None

    def frame(self, frame_ref: FakeWebElement | str) -> None:
        if isinstance(frame_ref, FakeWebElement):
            fid = str(frame_ref._tag.get("id") or frame_ref._tag.get("name") or "")
        else:
            fid = str(frame_ref)
        if fid not in self._driver._frames:
            raise NoSuchElementException(f"Frame {fid!r} not found")
        self._driver._current_frame = fid

    def window(self, handle: str) -> None:
        if handle not in self._driver.window_handles:
            raise ValueError(f"Unknown window handle {handle!r}")
        self._driver.current_window_handle = handle


def _normalize_fake_url(url: str) -> str:
    raw = str(url or "").strip()
    if raw.startswith(("http://", "https://")):
        return raw
    return f"https://spaceinvasion.bitmeup.com/{raw.lstrip('/')}"


class FakeDriver:
    """3-iframe Selenium-compatible driver wired to `FakeSpaceInvasionServer`."""

    def __init__(self, server: FakeSpaceInvasionServer) -> None:
        self.server = server
        self.switch_to = FakeSwitchTo(self)
        self.window_handles = ["game_tab"]
        self.current_window_handle = "game_tab"
        self._current_frame: Optional[str] = None
        sq = server.sid_query()
        sq_amp = f"&{sq}" if sq else ""
        self._frames: dict[str, _FrameState] = {}
        for fid, action in (
            ("F_Navi", "internalMenu"),
            ("F_planets", "internalPlanetsOverview"),
            ("F_Hauptframe", "internalHome"),
        ):
            url = _normalize_fake_url(f"indexInternal.es?action={action}{sq_amp}")
            r_url, r_html = server.render_frame(fid, url)
            self._frames[fid] = _FrameState(fid, _normalize_fake_url(r_url), r_html)

    @property
    def current_url(self) -> str:
        sq = self.server.sid_query()
        q = f"?{sq}" if sq else ""
        return f"https://spaceinvasion.bitmeup.com/indexGame.es{q}"

    def frame_url(self, frame_id: str) -> str:
        """Return current URL of iframe `frame_id`."""
        return self._frames[frame_id].url

    def frame_html(self, frame_id: str) -> str:
        """Return current HTML of iframe `frame_id`."""
        return self._frames[frame_id].html

    @property
    def page_source(self) -> str:
        if self._current_frame is None:
            sq = self.server.sid_query()
            sq_amp = f"&{sq}" if sq else ""
            return (
                "<html><body>"
                f'<iframe id="F_Navi" src="indexInternal.es?action=internalMenu{sq_amp}"></iframe>'
                f'<iframe id="F_planets" src="indexInternal.es?action=internalPlanetsOverview{sq_amp}"></iframe>'
                f'<iframe id="F_Hauptframe" src="indexInternal.es?action=internalHome{sq_amp}"></iframe>'
                "</body></html>"
            )
        return self._frames[self._current_frame].html

    def _active_soup(self) -> BeautifulSoup:
        if self._current_frame is None:
            return BeautifulSoup(self.page_source, "html.parser")
        return self._frames[self._current_frame].soup

    def _active_gen_id(self) -> int:
        if self._current_frame is None:
            return 1
        return self._frames[self._current_frame].gen_id

    def find_element(self, by: str, value: str) -> FakeWebElement:
        hits = _find_tags(self._active_soup(), by, value)
        if not hits:
            raise NoSuchElementException(f"No element {by}={value!r} in frame={self._current_frame}")
        return FakeWebElement(self, self._current_frame, self._active_gen_id(), hits[0])

    def find_elements(self, by: str, value: str) -> list[FakeWebElement]:
        return [
            FakeWebElement(self, self._current_frame, self._active_gen_id(), t)
            for t in _find_tags(self._active_soup(), by, value)
        ]

    def _navigate_frame(
        self,
        frame_id: str,
        url: str,
        *,
        form_data: Optional[dict[str, Any]] = None,
    ) -> None:
        frame = self._frames[frame_id]
        new_url, new_html = self.server.render_frame(frame_id, _normalize_fake_url(url), form_data=form_data)
        norm_url = _normalize_fake_url(new_url)
        if self.server.async_nav_polls > 0:
            frame.start_async(norm_url, new_html, self.server.async_nav_polls)
        else:
            frame.commit_now(norm_url, new_html)

    def execute_script(self, script: str, *args: Any) -> Any:
        text = " ".join(script.split())
        frame = self._frames[self._current_frame] if self._current_frame else None

        # Handle element helpers passed in args
        if args and isinstance(args[0], FakeWebElement):
            el = args[0]
            if ".scrollIntoView" in text:
                return None
            if ".click(" in text:
                el._js_click()
                return None
            if ".submit(" in text:
                form_tag = el._tag if el._tag.name == "form" else el._tag.find_parent("form")
                if form_tag is not None:
                    form_action = str(
                        form_tag.get("action") or (frame.url if frame else "indexInternal.es?action=internalHome")
                    )
                    data: dict[str, Any] = {}
                    for inp in form_tag.find_all("input"):
                        iname = str(inp.get("name") or "")
                        if iname:
                            data[iname] = str(inp.get("value") or "")
                    self._navigate_frame("F_Hauptframe", form_action, form_data=data)
                return None
            if ".style.display" in text and "block" in text:
                el_id = str(el._tag.get("id") or "")
                if frame is not None and el_id:
                    frame.hidden_ids.discard(el_id)
                el._tag["style"] = "display:block"
                return None

        # Handle compound ready/href/token probe from `_wait_frame_ready`
        if "return {" in text and "document.readyState" in text and "location.href" in text:
            if frame is None:
                return {"ready": "complete", "href": self.current_url, "token": None}
            if frame.ready_polls_remaining > 0:
                frame.maybe_advance_poll()
                if frame.ready_polls_remaining > 0:
                    ready_val = "complete" if self.server.async_href_lag else "loading"
                    href_val = frame.url
                    token_val = frame.window_vars.get("__siNavToken")
                    return {"ready": ready_val, "href": href_val, "token": token_val}
            return {
                "ready": "complete",
                "href": frame.url,
                "token": frame.window_vars.get("__siNavToken"),
            }

        # Handle window.<var> = <literal> statements before navigation or return
        for m in re.finditer(r"window\.([A-Za-z0-9_$]+)\s*=\s*([^;]+);", text):
            var_name = m.group(1)
            raw_val = m.group(2).strip()
            if raw_val.startswith("arguments["):
                idx_m = re.match(r"arguments\[(\d+)\]", raw_val)
                val: Any = args[int(idx_m.group(1))] if idx_m else None
            elif (raw_val.startswith("'") and raw_val.endswith("'")) or (
                raw_val.startswith('"') and raw_val.endswith('"')
            ):
                val = raw_val[1:-1]
            elif raw_val.isdigit():
                val = int(raw_val)
            elif raw_val == "true":
                val = True
            elif raw_val == "false":
                val = False
            else:
                val = raw_val
            if frame is not None:
                frame.window_vars[var_name] = val

        for m in re.finditer(r"delete\s+window\.([A-Za-z0-9_$]+)", text):
            if frame is not None:
                frame.window_vars.pop(m.group(1), None)

        # Navigation commands
        if "location.reload()" in text and frame is not None:
            sq = self.server.sid_query()
            sq_amp = f"&{sq}" if sq else ""
            reload_url = frame.url or f"indexInternal.es?action=internalPlanetsOverview{sq_amp}"
            self._navigate_frame(frame.frame_id, reload_url)
            return None

        m_rep = re.search(r"location\.replace\(\s*arguments\[(\d+)\]\s*\)", text)
        if m_rep and frame is not None:
            target_url = str(args[int(m_rep.group(1))])
            self._navigate_frame(frame.frame_id, target_url)
            return None

        m_href = re.search(r"location\.href\s*=\s*arguments\[(\d+)\]", text)
        if m_href and frame is not None:
            target_url = str(args[int(m_href.group(1))])
            self._navigate_frame(frame.frame_id, target_url)
            return None

        # Query commands
        if "document.readyState" in text:
            if frame is None:
                return "complete"
            if frame.ready_polls_remaining > 0:
                frame.maybe_advance_poll()
                return "complete" if self.server.async_href_lag else "loading"
            return "complete"

        if "return location.href" in text or "window.location.href" in text:
            if frame is None:
                return self.current_url
            if frame.ready_polls_remaining > 0 and self.server.async_href_lag:
                return frame.url
            return frame.url

        if "SI_TPL.menu.sid" in text:
            return self.server.menu_sid_js_value()

        m_id_chk = re.search(r"document\.getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)", text)
        if m_id_chk:
            hit = self._active_soup().find(id=m_id_chk.group(1)) is not None
            return bool(hit) if "Boolean(" in text else hit

        m_bool_win = re.search(r"return\s+Boolean\(\s*window\.([A-Za-z0-9_$]+)\s*\)", text)
        if m_bool_win:
            if frame is None:
                return False
            return bool(frame.window_vars.get(m_bool_win.group(1)))

        m_ret_win = re.search(
            r"return\s+(?:\(\s*)?window\.([A-Za-z0-9_$]+)(?:\s*\|\|\s*['\"]([^'\"]*)['\"])?\s*\)?", text
        )
        if m_ret_win:
            if frame is None:
                return m_ret_win.group(2) if m_ret_win.group(2) is not None else None
            default_val = m_ret_win.group(2) if m_ret_win.group(2) is not None else None
            return frame.window_vars.get(m_ret_win.group(1), default_val)

        return None


class E2ETestHarness:
    """Isolated temporary runtime with ephemeral HTTP API server, SQLite DB, config.json, and FakeBrowserShell."""

    def __init__(
        self,
        *,
        config: Optional[StrategyConfig] = None,
        raw_config_text: Optional[str] = None,
        server: Optional[FakeSpaceInvasionServer] = None,
        clock: Optional[FakeClock] = None,
        debug_port_open: bool = True,
        min_nav_delay: tuple[float, float] = (0.0, 0.0),
        client_timeout: float = 5.0,
        http_timeout: float = 15.0,
        max_actions_per_hour: Optional[int] = None,
        patch_clocks: bool = True,
        executor_max_limiter_wait: Optional[float] = None,
    ) -> None:
        self.clock = clock or (server.clock if server is not None else FakeClock())
        self.server = server or FakeSpaceInvasionServer(clock=self.clock)
        self.driver = FakeDriver(self.server)
        self.initial_config = config or StrategyConfig()
        self.raw_config_text = raw_config_text
        self.debug_port_open = debug_port_open
        self.min_nav_delay = min_nav_delay
        self.client_timeout = client_timeout
        self.http_timeout = http_timeout
        self.max_actions_per_hour = max_actions_per_hour
        self.patch_clocks = patch_clocks
        self.executor_max_limiter_wait: Optional[float] = (
            float(executor_max_limiter_wait)
            if isinstance(executor_max_limiter_wait, (int, float)) and not isinstance(executor_max_limiter_wait, bool)
            else None
        )

        self.tmp_dir: Optional[Path] = None
        self.config_path: Optional[Path] = None
        self.log_dir: Optional[Path] = None
        self.db_path: Optional[Path] = None
        self.env_file: Optional[Path] = None
        self.bridge: Optional[GameApiBridge] = None
        self.http_server: Optional[Any] = None
        self.server_thread: Optional[threading.Thread] = None
        self.port: int = 0
        self.base_url: str = ""
        self.api: Optional[GameApiClient] = None
        self.ensure_session_calls: list[float] = []
        self._patches: list[Any] = []
        self._created_clients: list[GameBrowserClient] = []
        self.page_limiter: Optional[Any] = None

    def _make_client(self, _port: int) -> GameBrowserClient:
        sig = inspect.signature(GameBrowserClient.__init__)
        kwargs: dict[str, Any] = {
            "detach_on_close": True,
            "min_nav_delay": self.min_nav_delay,
            "timeout": self.client_timeout,
        }
        chosen_limiter = self.page_limiter
        if self.bridge is not None:
            for attr_name in (
                "limiter",
                "_limiter",
                "page_view_limiter",
                "_page_view_limiter",
                "page_limiter",
                "_page_limiter",
                "rate_limiter",
            ):
                lim = getattr(self.bridge, attr_name, None)
                if lim is not None:
                    chosen_limiter = lim
                    break
        if chosen_limiter is not None:
            for param_name in ("page_limiter", "limiter", "page_view_limiter", "rate_limiter"):
                if param_name in sig.parameters:
                    kwargs[param_name] = chosen_limiter
                    break
        if "max_limiter_wait" in sig.parameters:
            kwargs["max_limiter_wait"] = None
        client = GameBrowserClient(self.driver, **kwargs)
        self._created_clients.append(client)
        return client

    def _ensure_session(self) -> None:
        self.ensure_session_calls.append(self.clock.monotonic())

    def _sync_bridge_limiter_clock(self) -> None:
        if self.bridge is None or not self.patch_clocks:
            return
        for attr_name in (
            "limiter",
            "_limiter",
            "page_view_limiter",
            "_page_view_limiter",
            "page_limiter",
            "_page_limiter",
            "rate_limiter",
        ):
            lim = getattr(self.bridge, attr_name, None)
            if lim is not None and hasattr(lim, "_clock"):
                lim._clock = self.clock.monotonic
                lim._sleeper = self.clock.sleep

    def get_dashboard(self, refresh: int = 0) -> dict[str, Any]:
        """Fetch `/dashboard?refresh=<0|1>` over HTTP from the ephemeral API server."""
        import requests

        resp = requests.get(
            f"{self.base_url}/dashboard?refresh={int(refresh)}",
            timeout=self.http_timeout,
        )
        resp.raise_for_status()
        return dict(resp.json())

    def _start_bridge_and_server(self) -> None:
        assert (
            self.config_path is not None
            and self.log_dir is not None
            and self.db_path is not None
            and self.env_file is not None
        )
        bridge_sig = inspect.signature(GameApiBridge.__init__)
        bridge_kwargs: dict[str, Any] = {
            "debug_port": 0,
            "env_file": self.env_file,
            "account": "e2e_test",
            "config_path": self.config_path,
            "max_actions_per_hour": self.max_actions_per_hour,
            "log_dir": self.log_dir,
            "db_path": self.db_path,
            "client_factory": self._make_client,
            "ensure_session_fn": self._ensure_session,
        }
        if self.page_limiter is not None and (
            "page_limiter" in bridge_sig.parameters
            or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in bridge_sig.parameters.values())
        ):
            bridge_kwargs["page_limiter"] = self.page_limiter
        if "executor_max_limiter_wait" in bridge_sig.parameters:
            bridge_kwargs["executor_max_limiter_wait"] = self.executor_max_limiter_wait
        elif self.executor_max_limiter_wait is not None and "max_limiter_wait" in bridge_sig.parameters:
            bridge_kwargs["max_limiter_wait"] = self.executor_max_limiter_wait
        self.bridge = GameApiBridge(**bridge_kwargs)
        self._sync_bridge_limiter_clock()
        self.bridge._live_executor.action_delay_range = (0.0, 0.0)
        self.bridge._dry_executor.action_delay_range = (0.0, 0.0)
        self.bridge._live_executor.max_limiter_wait = self.executor_max_limiter_wait
        self.bridge._dry_executor.max_limiter_wait = self.executor_max_limiter_wait
        self.http_server = create_api_server(self.bridge, host="127.0.0.1", port=0)
        self.port = int(self.http_server.server_address[1])
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.server_thread = threading.Thread(
            target=self.http_server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.server_thread.start()
        self.api = GameApiClient(base_url=self.base_url, timeout=self.http_timeout)

    def _stop_bridge_and_server(self) -> None:
        if self.http_server is not None:
            try:
                self.http_server.shutdown()
            except Exception:
                pass
            try:
                self.http_server.server_close()
            except Exception:
                pass
            self.http_server = None
        if self.server_thread is not None:
            self.server_thread.join(timeout=2.0)
            self.server_thread = None
        if self.bridge is not None and self.bridge._db is not None:
            try:
                self.bridge._db.close()
            except Exception:
                pass
        self.bridge = None

    def restart_bridge(self) -> None:
        """Shut down the current API bridge/server and boot a fresh instance against the same temp state."""
        self._stop_bridge_and_server()
        self._start_bridge_and_server()

    def __enter__(self) -> E2ETestHarness:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="si_e2e_"))
        try:
            self.config_path = self.tmp_dir / "config.json"
            self.log_dir = self.tmp_dir / "logs"
            self.db_path = self.tmp_dir / "state" / "spaceinvasion.db"
            self.env_file = self.tmp_dir / ".env"
            self.env_file.write_text("# isolated e2e dummy env\n", encoding="utf-8")

            if self.raw_config_text is not None:
                self.config_path.write_text(self.raw_config_text, encoding="utf-8")
            else:
                save_strategy_config(self.initial_config, self.config_path)

            p_dbg = patch.object(
                browser_mod,
                "is_debug_port_open",
                side_effect=lambda *_a, **_kw: self.debug_port_open,
            )
            p_dbg.start()
            self._patches.append(p_dbg)

            if self.patch_clocks:
                proxy = _ClockTimeProxy(self.clock)
                clock_mods: list[Any] = [
                    client_mod,
                    executor_mod,
                    storage_mod,
                    fleet_mod,
                    rank_history_mod,
                    fleet_ctx_mod,
                    blast_furnace_mod,
                    commander,
                ]
                import spaceinvasion.db as db_pkg

                for pkg in (bridge_pkg, api_pkg, commander_pkg, client_mod, executor_mod, db_pkg):
                    clock_mods.append(pkg)
                    if hasattr(pkg, "__path__"):
                        for mod_info in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg.__name__}."):
                            if ".migrations." in mod_info.name or mod_info.name.endswith(".migrations"):
                                continue
                            sub = sys.modules.get(mod_info.name)
                            if sub is None:
                                sub = importlib.import_module(mod_info.name)
                            clock_mods.append(sub)
                seen_ids: set[int] = set()
                for mod in clock_mods:
                    if id(mod) in seen_ids or "time" not in getattr(mod, "__dict__", {}):
                        continue
                    seen_ids.add(id(mod))
                    p_time = patch.object(mod, "time", proxy)
                    p_time.start()
                    self._patches.append(p_time)

            if hasattr(client_mod, "PageViewLimiter"):
                limiter_kwargs: dict[str, Any] = {"min_delay_range": self.min_nav_delay}
                if self.patch_clocks:
                    limiter_kwargs["clock"] = self.clock.monotonic
                    limiter_kwargs["sleeper"] = self.clock.sleep
                self.page_limiter = client_mod.PageViewLimiter(**limiter_kwargs)
                if hasattr(client_mod, "_DEFAULT_PAGE_VIEW_LIMITER"):
                    p_lim = patch.object(client_mod, "_DEFAULT_PAGE_VIEW_LIMITER", self.page_limiter)
                    p_lim.start()
                    self._patches.append(p_lim)
                if hasattr(GameBrowserClient, "page_limiter"):
                    p_cls_lim = patch.object(GameBrowserClient, "page_limiter", self.page_limiter)
                    p_cls_lim.start()
                    self._patches.append(p_cls_lim)

            self._start_bridge_and_server()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_exc: Any) -> None:
        self._stop_bridge_and_server()
        for p in reversed(self._patches):
            try:
                p.stop()
            except Exception:
                pass
        self._patches.clear()
        self._created_clients.clear()
        self.page_limiter = None
        if self.tmp_dir is not None:
            shutil.rmtree(self.tmp_dir, ignore_errors=True)
            self.tmp_dir = None


class _DOMNodeBuilder(HTMLParser):
    """Builds a serializable DOM JSON tree from real `GET /` HTML via Python `html.parser`."""

    VOID_TAGS = frozenset({"meta", "link", "input", "br", "hr", "img"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict[str, Any] = {"tag": "document", "attrs": {}, "children": []}
        self._stack: list[dict[str, Any]] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        node = {
            "tag": tag.lower(),
            "attrs": {str(k): ("" if v is None else str(v)) for k, v in attrs},
            "children": [],
        }
        self._stack[-1]["children"].append(node)
        if tag.lower() not in self.VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        node = {
            "tag": tag.lower(),
            "attrs": {str(k): ("" if v is None else str(v)) for k, v in attrs},
            "children": [],
        }
        self._stack[-1]["children"].append(node)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        for idx in range(len(self._stack) - 1, 0, -1):
            if self._stack[idx]["tag"] == t:
                del self._stack[idx:]
                break

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1]["children"].append({"tag": "#text", "text": data})


def inspect_ui_html(html_text: str) -> dict[str, Any]:
    """Parse real `GET /` HTML via stdlib `html.parser` and inspect table headers, colspans, and config controls."""
    parser = _DOMNodeBuilder()
    parser.feed(html_text)
    parser.close()

    def _walk(node: dict[str, Any]) -> list[dict[str, Any]]:
        out = [node]
        for ch in node.get("children", []):
            if ch.get("tag") != "#text":
                out.extend(_walk(ch))
        return out

    all_nodes = _walk(parser.root)

    def _find_by_id(tid: str) -> Optional[dict[str, Any]]:
        for n in all_nodes:
            if n.get("attrs", {}).get("id") == tid:
                return n
        return None

    def _text(node: dict[str, Any]) -> str:
        if node.get("tag") == "#text":
            return str(node.get("text", ""))
        return "".join(_text(ch) for ch in node.get("children", []))

    def _find_table_info(tbody_id: str) -> dict[str, Any]:
        for n in all_nodes:
            if n.get("tag") != "table":
                continue
            sub = _walk(n)
            if not any(s.get("attrs", {}).get("id") == tbody_id for s in sub):
                continue
            ths = [" ".join(_text(s).split()) for s in sub if s.get("tag") == "th"]
            tbody = next(s for s in sub if s.get("attrs", {}).get("id") == tbody_id)
            tbody_sub = _walk(tbody)
            placeholder_colspans = [
                int(s["attrs"]["colspan"])
                for s in tbody_sub
                if s.get("tag") == "td" and str(s.get("attrs", {}).get("colspan", "")).isdigit()
            ]
            return {
                "found": True,
                "headers": ths,
                "th_count": len(ths),
                "placeholder_colspans": placeholder_colspans,
            }
        return {"found": False, "headers": [], "th_count": 0, "placeholder_colspans": []}

    planner_sel = _find_by_id("cfg-planner_mode")
    planner_opts = []
    if planner_sel is not None:
        for ch in _walk(planner_sel):
            if ch.get("tag") == "option":
                planner_opts.append(ch.get("attrs", {}).get("value", ""))

    return {
        "tree": parser.root,
        "dash_plan": _find_table_info("dash-plan-tbody"),
        "ops_plan": _find_table_info("plan-tbody"),
        "has_cfg_planner_mode": planner_sel is not None,
        "cfg_planner_mode_options": planner_opts,
        "has_feedback_banner": _find_by_id("feedback-banner") is not None,
        "has_planet_select": _find_by_id("planet-select") is not None,
        "has_save_config": _find_by_id("btn-save-config") is not None,
    }


_NODE_HARNESS_SCRIPT = r"""
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const payload = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const baseUrl = String(payload.baseUrl || "http://127.0.0.1:8787").replace(/\/$/, "");
const html = String(payload.html || "<!doctype html><html><body><div id='root'></div></body></html>");
const appJs = String(payload.appJs || "");
const scenarioJs = String(payload.scenarioJs || "");
const fetchPatchJs = String(payload.fetchPatchJs || "");
const jsdomPath = payload.jsdomPath || path.resolve(process.cwd(), "frontend/node_modules/jsdom");

const { JSDOM, VirtualConsole } = require(jsdomPath);

const activeIntervals = new Set();
const activeTimeouts = new Set();
const recordedTimeoutDelays = [];
const errors = [];
const windowErrors = [];
const fetchLog = [];
let reloadCount = 0;

const virtualConsole = new VirtualConsole();
virtualConsole.on("jsdomError", (err) => {
  const msg = String((err && err.message) || err || "");
  if (msg.includes("Not implemented: navigation")) {
    reloadCount += 1;
    return;
  }
  windowErrors.push(msg);
});

const dom = new JSDOM(html, {
  url: `${baseUrl}/`,
  pretendToBeVisual: true,
  runScripts: "outside-only",
  virtualConsole,
});

const { window } = dom;
const ctx = dom.getInternalVMContext();

// Preserve unsanitized invalid strings (e.g. "abc") on <input type="number"> for validation tests
const origInputValueDesc = Object.getOwnPropertyDescriptor(
  window.HTMLInputElement.prototype,
  "value"
);
if (origInputValueDesc && origInputValueDesc.get && origInputValueDesc.set) {
  Object.defineProperty(window.HTMLInputElement.prototype, "value", {
    configurable: true,
    enumerable: origInputValueDesc.enumerable,
    get() {
      const cur = origInputValueDesc.get.call(this);
      if (this.type === "number" && cur === "" && this.__siRawUnsanitizedValue !== undefined) {
        return this.__siRawUnsanitizedValue;
      }
      return cur;
    },
    set(val) {
      const strVal = val === null || val === undefined ? "" : String(val);
      delete this.__siRawUnsanitizedValue;
      origInputValueDesc.set.call(this, strVal);
      if (this.type === "number" && strVal !== "" && origInputValueDesc.get.call(this) === "") {
        this.__siRawUnsanitizedValue = strVal;
      }
    },
  });
}

// Array-method ergonomics on NodeList / HTMLCollection for legacy test scenarios
for (const proto of [window.NodeList?.prototype, window.HTMLCollection?.prototype]) {
  if (!proto) continue;
  for (const method of ["map", "filter", "some", "every", "find", "reduce"]) {
    if (typeof proto[method] !== "function") {
      Object.defineProperty(proto, method, {
        configurable: true,
        writable: true,
        value(...args) {
          return Array.from(this)[method](...args);
        },
      });
    }
  }
}

// Seamless synthetic + legacy string/object dispatchEvent with React _valueTracker & scheduler sync
const microQueue = [];
const macroQueue = [];
let flushingReact = false;
let scenarioFinished = false;

function flushReactScheduler() {
  if (flushingReact || scenarioFinished) return;
  flushingReact = true;
  try {
    let guard = 0;
    while ((microQueue.length > 0 || macroQueue.length > 0) && guard++ < 50) {
      while (microQueue.length > 0) {
        const fn = microQueue.shift();
        fn();
      }
      if (macroQueue.length > 0) {
        const fn = macroQueue.shift();
        fn();
      }
    }
  } finally {
    flushingReact = false;
  }
}

window.queueMicrotask = (fn) => {
  if (scenarioFinished) return;
  microQueue.push(fn);
  queueMicrotask(flushReactScheduler);
};
window.setImmediate = (fn) => {
  if (scenarioFinished) return 0;
  macroQueue.push(fn);
  setImmediate(flushReactScheduler);
  return 1;
};

const origClick = window.HTMLElement.prototype.click;
window.HTMLElement.prototype.click = function patchedClick() {
  const res = origClick.call(this);
  flushReactScheduler();
  return res;
};

const origDispatchEvent = window.EventTarget.prototype.dispatchEvent;
window.EventTarget.prototype.dispatchEvent = function patchedDispatchEvent(evOrType) {
  let ev = evOrType;
  if (typeof evOrType === "string") {
    ev = new window.Event(evOrType, { bubbles: true, cancelable: true });
  } else if (evOrType && !(evOrType instanceof window.Event) && typeof evOrType === "object") {
    const type = String(evOrType.type || "custom");
    if (
      type.startsWith("mouse") ||
      type.startsWith("pointer") ||
      type === "click" ||
      type === "dblclick"
    ) {
      ev = new window.MouseEvent(type, {
        bubbles: evOrType.bubbles !== undefined ? Boolean(evOrType.bubbles) : true,
        cancelable: evOrType.cancelable !== undefined ? Boolean(evOrType.cancelable) : true,
        clientX: Number(evOrType.clientX || 0),
        clientY: Number(evOrType.clientY || 0),
        button: evOrType.button !== undefined ? Number(evOrType.button) : 0,
      });
    } else if (type.startsWith("key")) {
      ev = new window.KeyboardEvent(type, {
        bubbles: evOrType.bubbles !== undefined ? Boolean(evOrType.bubbles) : true,
        cancelable: evOrType.cancelable !== undefined ? Boolean(evOrType.cancelable) : true,
        key: String(evOrType.key || ""),
      });
    } else {
      ev = new window.Event(type, {
        bubbles: evOrType.bubbles !== undefined ? Boolean(evOrType.bubbles) : true,
        cancelable: evOrType.cancelable !== undefined ? Boolean(evOrType.cancelable) : true,
      });
    }
  }
  if (
    ev &&
    (ev.type === "input" || ev.type === "change") &&
    this &&
    this._valueTracker &&
    typeof this._valueTracker.setValue === "function"
  ) {
    this._valueTracker.setValue(`__si_force_diff__${Math.random()}`);
  }
  const out = origDispatchEvent.call(this, ev);
  flushReactScheduler();
  return out;
};

const nativeFetch = globalThis.fetch;
async function wrappedFetch(url, opts = {}) {
  if (scenarioFinished) {
    return { ok: true, status: 200, text: async () => "{}", json: async () => ({}) };
  }
  const urlStr = String(url);
  const fullUrl = urlStr.startsWith("http")
    ? urlStr
    : `${baseUrl}${urlStr.startsWith("/") ? "" : "/"}${urlStr}`;
  const reqInfo = { url: urlStr, method: String(opts.method || "GET").toUpperCase(), body: null };
  if (opts.body) {
    try {
      reqInfo.body = JSON.parse(String(opts.body));
    } catch {
      reqInfo.body = String(opts.body);
    }
  }
  fetchLog.push(reqInfo);
  if (window.__siFetchHook) {
    const custom = await window.__siFetchHook(urlStr, opts, fullUrl, nativeFetch);
    if (custom !== undefined) return custom;
  }
  return nativeFetch(fullUrl, opts);
}

function trackedSetTimeout(fn, ms = 0, ...args) {
  if (scenarioFinished) return 0;
  const numMs = Number(ms || 0);
  recordedTimeoutDelays.push(numMs);
  const id = setTimeout(() => {
    activeTimeouts.delete(id);
    if (scenarioFinished) return;
    fn(...args);
    flushReactScheduler();
  }, Math.max(0, numMs));
  activeTimeouts.add(id);
  return id;
}
function trackedClearTimeout(id) {
  activeTimeouts.delete(id);
  clearTimeout(id);
}
function trackedSetInterval(fn, ms = 0, ...args) {
  if (scenarioFinished) return 0;
  const id = setInterval(() => {
    if (scenarioFinished) return;
    fn(...args);
    flushReactScheduler();
  }, Math.max(50, Number(ms || 0)));
  activeIntervals.add(id);
  return id;
}
function trackedClearInterval(id) {
  activeIntervals.delete(id);
  clearInterval(id);
}

window.fetch = wrappedFetch;
window.AbortController = AbortController;
window.setTimeout = trackedSetTimeout;
window.clearTimeout = trackedClearTimeout;
window.setInterval = trackedSetInterval;
window.clearInterval = trackedClearInterval;
window.fetchLog = fetchLog;
window.recordedTimeoutDelays = recordedTimeoutDelays;
Object.defineProperty(window, "reloadCount", {
  configurable: true,
  enumerable: true,
  get() {
    return reloadCount;
  },
});
window.console = {
  log() {},
  info() {},
  debug() {},
  warn() {},
  error(...a) {
    if (!scenarioFinished) {
      errors.push(a.map(String).join(" "));
    }
  },
};
window.__waitUntil = async (pred, timeoutMs = 10000) => {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    flushReactScheduler();
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 20));
  }
  flushReactScheduler();
  return Boolean(pred());
};
window.__sleep = async (ms) => {
  await new Promise((r) => setTimeout(r, ms));
  flushReactScheduler();
};
window.__serializeTable = (tbodyId) => {
  flushReactScheduler();
  const tb = window.document.getElementById(tbodyId);
  if (!tb) return [];
  return Array.from(tb.children)
    .filter((c) => c.nodeType === 1 && c.tagName === "TR")
    .map((tr) =>
      Array.from(tr.children)
        .filter((td) => td.nodeType === 1 && td.tagName === "TD")
        .map((td) => ({
          text: (td.textContent || "").replace(/\s+/g, " ").trim(),
          className: typeof td.className === "string" ? td.className : td.getAttribute("class") || "",
          colSpan: td.colSpan || 1,
          pills: Array.from(td.querySelectorAll(".eta-pill")).map((p) => ({
            text: (p.textContent || "").trim(),
            className: typeof p.className === "string" ? p.className : p.getAttribute("class") || "",
          })),
          buttons: Array.from(td.querySelectorAll("button")).map((b) =>
            (b.textContent || "").trim()
          ),
        }))
    );
};
window.__snapshotElement = (id) => {
  flushReactScheduler();
  const node = window.document.getElementById(id);
  if (!node) return null;
  return {
    id: node.id,
    tagName: node.tagName,
    className: typeof node.className === "string" ? node.className : node.getAttribute("class") || "",
    hidden: Boolean(node.hidden),
    disabled: Boolean(node.disabled),
    value: node.value ?? "",
    checked: Boolean(node.checked),
    textContent: (node.textContent || "").replace(/\s+/g, " ").trim(),
    options:
      node.tagName === "SELECT"
        ? Array.from(node.querySelectorAll("option")).map((o) => ({
            value: o.value,
            text: (o.textContent || "").trim(),
            selected: Boolean(o.selected),
          }))
        : undefined,
  };
};

if (fetchPatchJs) {
  window.__siFetchHook = vm.runInContext(`(${fetchPatchJs})`, ctx);
}

(async () => {
  let exitCode = 0;
  let outStr = "";
  try {
    await new Promise((r) => setImmediate(r));
    vm.runInContext(appJs, ctx, { filename: "app.js" });
    flushReactScheduler();
    if (typeof window.loadDashboard === "function") {
      const origLoadDash = window.loadDashboard;
      window.loadDashboard = async (...args) => {
        const res = await origLoadDash(...args);
        flushReactScheduler();
        return res;
      };
    }
    await window.__sleep(120);
    let scenarioOut = null;
    if (scenarioJs) {
      scenarioOut = await vm.runInContext(
        `(async () => {\n${scenarioJs}\n})()`,
        ctx,
        { filename: "scenario.js" }
      );
      flushReactScheduler();
    }
    outStr = JSON.stringify({
      ok: true,
      errors,
      windowErrors,
      reloadCount,
      fetchLog,
      recordedTimeoutDelays,
      scenario: scenarioOut,
    });
  } catch (err) {
    windowErrors.push(err && err.message ? err.message : String(err));
    outStr = JSON.stringify({
      ok: false,
      fatal: err && err.stack ? err.stack : String(err),
      errors,
      windowErrors,
      reloadCount,
      fetchLog,
      recordedTimeoutDelays,
    });
    exitCode = 1;
  } finally {
    scenarioFinished = true;
    for (const id of activeIntervals) clearInterval(id);
    for (const id of activeTimeouts) clearTimeout(id);
  }
  fs.writeSync(1, outStr);
  process.exit(exitCode);
})();
"""


def run_ui_in_node(
    base_url: str,
    scenario_js: str = "",
    *,
    fetch_patch_js: str = "",
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Fetch real `GET /` and `GET /app.js` over HTTP and execute inside real `jsdom` against `base_url`."""
    import requests

    html_resp = requests.get(f"{base_url}/", timeout=5.0)
    html_resp.raise_for_status()
    js_resp = requests.get(f"{base_url}/app.js", timeout=5.0)
    js_resp.raise_for_status()

    inspected = inspect_ui_html(html_resp.text)
    jsdom_path = Path(__file__).resolve().parents[2] / "frontend" / "node_modules" / "jsdom"
    with tempfile.TemporaryDirectory(prefix="si_ui_node_") as tmp:
        tmp_path = Path(tmp)
        runner_js = tmp_path / "runner.cjs"
        payload_json = tmp_path / "payload.json"
        runner_js.write_text(_NODE_HARNESS_SCRIPT, encoding="utf-8")
        payload_json.write_text(
            json.dumps(
                {
                    "baseUrl": base_url.rstrip("/"),
                    "html": html_resp.text,
                    "domTree": inspected["tree"],
                    "appJs": js_resp.text,
                    "scenarioJs": scenario_js,
                    "fetchPatchJs": fetch_patch_js,
                    "jsdomPath": str(jsdom_path),
                }
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            ["node", str(runner_js), str(payload_json)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"Node UI runner failed (rc={proc.returncode}): {proc.stderr}")
    data = json.loads(proc.stdout)
    data["htmlInspection"] = {k: v for k, v in inspected.items() if k != "tree"}
    if not data.get("ok"):
        raise AssertionError(f"Node UI JS execution failed: {data.get('fatal') or data}")
    return data
