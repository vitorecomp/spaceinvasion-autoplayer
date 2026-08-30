"""Safe action executor for SpaceInvasion Classic Selenium browser control."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
import contextlib
from dataclasses import dataclass, replace
import inspect
import math
import os
import random
import re
import time
from typing import Any, Callable, Deque, Optional

from bs4 import BeautifulSoup, NavigableString, Tag
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

from spaceinvasion.core import game_data
from spaceinvasion.session.client import GameBrowserClient, PageViewLimiter
from spaceinvasion.combat.fleet import (
    GalaxySlot,
    parse_galaxy_system_html,
    parse_send_fleet_mission_block,
)
from spaceinvasion.core.logging_utils import append_crash_debug_log, redact_sensitive
from spaceinvasion.core.state import (
    GameState,
    QueueJob,
    homeworld_planet_id,
    is_undersized_fresh_colony,
    normalize_planet_type,
    parse_building_queue,
    parse_buildings_page,
    parse_economy_rates,
    parse_home_metadata,
    parse_research_page,
    parse_transmitter_page,
    parse_tutorial_from_home,
    parse_unit_factory_page,
)
from spaceinvasion.planner.strategy import PlannedAction


def _call_with_optional_priority(
    fn: Callable[..., Any],
    *args: Any,
    priority: str = "interactive",
    **kwargs: Any,
) -> Any:
    call_kwargs = dict(kwargs)
    targets: list[Any] = [fn]
    for attr in ("side_effect", "_mock_wraps"):
        wrapped = getattr(fn, attr, None)
        if callable(wrapped) and not isinstance(wrapped, type):
            targets.append(wrapped)
    accepts_priority = True
    for target in targets:
        try:
            sig = inspect.signature(target)
        except (TypeError, ValueError):
            accepts_priority = False
            break
        if "priority" not in sig.parameters and not any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        ):
            accepts_priority = False
            break
    if accepts_priority:
        call_kwargs["priority"] = priority
    return fn(*args, **call_kwargs)


def _safe_optional_wait_limit(raw: Any, default: float = 60.0) -> Optional[float]:
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        if raw.bit_length() > 128:
            return 3600.0 if raw > 0 else 0.0
        val = float(raw)
    else:
        val = float(raw)
    if not math.isfinite(val):
        return default
    return max(0.0, min(3600.0, val))


def _safe_str(
    val: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> str:
    if val is None:
        return ""
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except Exception:
            return f"<{type(val).__name__}>"
        if val is None:
            return ""
        if isinstance(val, os.PathLike):
            return f"<{type(val).__name__}>"
    if isinstance(val, str):
        return val
    if isinstance(val, (bytes, bytearray)):
        return bytes(val).decode("utf-8", errors="replace")
    if isinstance(val, int) and not isinstance(val, bool):
        return game_data.fmt_compact_int(val) if abs(val).bit_length() >= 12_000 else str(val)
    if isinstance(val, BaseException):
        str_fn = getattr(type(val), "__str__", BaseException.__str__)
        if str_fn not in (BaseException.__str__, KeyError.__str__):
            try:
                return str(val)[:4096]
            except Exception:
                pass
        args = getattr(val, "args", ())
        if not isinstance(args, (tuple, list)) or not args:
            return "" if str_fn is BaseException.__str__ else type(val).__name__
        seen = _seen if _seen is not None else set()
        budget = _budget if _budget is not None else [256]
        vid = id(val)
        if vid in seen or _depth >= 16 or budget[0] <= 0:
            return f"<{type(val).__name__}>"
        budget[0] -= 1
        seen.add(vid)
        try:
            if len(args) == 1:
                a0 = args[0]
                if isinstance(a0, str):
                    return (repr(a0) if isinstance(val, KeyError) else a0)[:4096]
                return _safe_str(a0, _depth=_depth + 1, _seen=seen, _budget=budget)[:4096]
            parts: list[str] = []
            for a in args[:8]:
                if budget[0] <= 0:
                    parts.append("...")
                    break
                if isinstance(a, str):
                    parts.append(repr(a))
                else:
                    parts.append(_safe_str(a, _depth=_depth + 1, _seen=seen, _budget=budget))
            if len(args) > 8:
                parts.append(f"...+{len(args) - 8}")
            res = "(" + ", ".join(parts) + ")"
            return res[:4096]
        finally:
            seen.discard(vid)
    if isinstance(val, (tuple, list, set, frozenset, Mapping)):
        try:
            return game_data.safe_key(val)
        except Exception:
            return f"<{type(val).__name__}>"
    try:
        return str(val)
    except Exception:
        return f"<{type(val).__name__}>"


def _safe_hourly_cap(raw: Any, default: int = 30) -> int:
    cap = game_data.safe_int(raw, default)
    return max(0, min(1_000_000, cap))


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of attempting one planned action."""

    executed: bool
    dry_run: bool
    action: PlannedAction
    message: str
    updated_state: Optional[GameState] = None
    observed_active_planet_id: Optional[int] = None
    observed_active_planet_confirmed: Optional[bool] = None
    target_cooldown_seconds: Optional[float] = None
    target_block_reason: Optional[str] = None
    target_is_bash_block: bool = False
    clear_target_debris: bool = False
    validated: bool = True
    validation_error: Optional[str] = None


class ActionExecutor:
    """Executes approved `PlannedAction` items in Chrome with rate and queue guards."""

    def __init__(
        self,
        *,
        dry_run: bool = True,
        max_actions_per_hour: int = 30,
        enforce_hourly_cap: bool = True,
        action_delay_range: tuple[float, float] = (0.4, 0.9),
        allow_fleetsave: bool = False,
        allow_raids: bool = False,
        allow_colonize: bool = False,
        allow_transmitter: bool = True,
        page_limiter: Optional[PageViewLimiter] = None,
        max_limiter_wait: Optional[float] = None,
    ) -> None:
        self.dry_run = dry_run
        self.max_actions_per_hour = _safe_hourly_cap(max_actions_per_hour, 30)
        self.enforce_hourly_cap = bool(enforce_hourly_cap)
        self.action_delay_range = action_delay_range
        self.allow_fleetsave = allow_fleetsave
        self.allow_raids = allow_raids
        self.allow_colonize = allow_colonize
        self.allow_transmitter = allow_transmitter
        self.page_limiter = page_limiter
        self.max_limiter_wait: Optional[float] = _safe_optional_wait_limit(max_limiter_wait)
        self._recent_timestamps: Deque[float] = deque()
        self.last_economy_rates: dict[int, dict[str, int]] = {}
        self.last_galaxy_slots: list[GalaxySlot] = []
        self.last_transmitter_info: Optional[dict[str, Any]] = None
        self.last_observed_active_planet_id: Optional[int] = None
        self.last_observed_active_planet_confirmed: Optional[bool] = None

    def _reset_observed_planet_tracking(self) -> None:
        self.last_observed_active_planet_id = None
        self.last_observed_active_planet_confirmed = None

    def _record_observed_planet(
        self,
        planet_id: Optional[int],
        *,
        confirmed: Optional[bool],
    ) -> None:
        if isinstance(planet_id, int) and not isinstance(planet_id, bool):
            self.last_observed_active_planet_id = planet_id
        elif confirmed is False:
            self.last_observed_active_planet_id = None
        self.last_observed_active_planet_confirmed = confirmed

    def _attach_observed_planet(self, result: ExecutionResult) -> ExecutionResult:
        obs_pid = result.observed_active_planet_id
        obs_conf = result.observed_active_planet_confirmed
        if obs_pid is None:
            obs_pid = self.last_observed_active_planet_id
        if obs_conf is None:
            obs_conf = self.last_observed_active_planet_confirmed
        if (
            obs_pid is result.observed_active_planet_id
            and obs_conf is result.observed_active_planet_confirmed
        ):
            return result
        return replace(
            result,
            observed_active_planet_id=obs_pid,
            observed_active_planet_confirmed=obs_conf,
        )

    def _confirmed_planet_mismatch(self, planet_id: int) -> Optional[int]:
        if (
            self.last_observed_active_planet_confirmed is True
            and isinstance(self.last_observed_active_planet_id, int)
            and not isinstance(self.last_observed_active_planet_id, bool)
            and self.last_observed_active_planet_id != planet_id
        ):
            return self.last_observed_active_planet_id
        return None

    def _pace_action(self, client: GameBrowserClient, *, priority: str = "interactive") -> None:
        wait_limit = _safe_optional_wait_limit(self.max_limiter_wait)
        if wait_limit is None:
            wait_limit = _safe_optional_wait_limit(getattr(client, "max_limiter_wait", None))

        if self.page_limiter is not None:
            self.page_limiter.acquire(priority=priority, max_wait=wait_limit)
            return
        pace_fn = getattr(client, "_pace", None)
        if callable(pace_fn):
            pace_fn(priority=priority)

    @staticmethod
    def _mark_frame_nav(client: GameBrowserClient, frame_id: str = "F_Hauptframe") -> Optional[str]:
        mark_fn = getattr(client, "_mark_frame_navigation", None)
        if callable(mark_fn):
            tok = mark_fn(frame_id)
            return tok if isinstance(tok, str) else None
        return None

    @staticmethod
    def _safe_click(client: GameBrowserClient, el) -> None:
        try:
            client.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});",
                el,
            )
        except Exception:
            pass
        try:
            el.click()
        except ElementClickInterceptedException:
            client.driver.execute_script("arguments[0].click();", el)

    @staticmethod
    def _set_text_input_value(client: GameBrowserClient, el: Any, value: str) -> None:
        el.clear()
        el.send_keys(value)
        with contextlib.suppress(Exception):
            cur_val = el.get_attribute("value")
            if (
                isinstance(cur_val, (str, int, float))
                and not isinstance(cur_val, bool)
                and str(cur_val) != value
            ):
                client.driver.execute_script(
                    "arguments[0].value = arguments[1]; "
                    "arguments[0].dispatchEvent(new Event('input', {bubbles: true})); "
                    "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                    el,
                    value,
                )

    @staticmethod
    def _find_newplanet_switch_target(html: str, planet_id: int) -> tuple[bool, Optional[str]]:
        """Inspect `<select name='planet'>` in `html` via BS4; return `(needs_switch, target_value)`."""
        pid = game_data.safe_optional_int(planet_id, None)
        if pid is None or pid < 0 or pid > game_data.MAX_JSON_SAFE_INT:
            return False, None
        if not html or "newplanet=" not in html.lower():
            return False, None
        soup = BeautifulSoup(html, "html.parser")
        select_el = soup.find("select", attrs={"name": "planet"})
        if select_el is None:
            return False, None
        target_re = re.compile(rf"newplanet={pid}(?!\d)", re.IGNORECASE | re.ASCII)
        target_value: Optional[str] = None
        selected_value: Optional[str] = None
        has_newplanet = False
        for opt in select_el.find_all("option"):
            val = str(opt.get("value") or "")
            if "newplanet=" in val.lower():
                has_newplanet = True
            if target_re.search(val):
                target_value = val
            if opt.has_attr("selected"):
                selected_value = val
        if not has_newplanet or target_value is None:
            return False, None
        if selected_value is not None and target_re.search(selected_value):
            return False, None
        return True, target_value

    @staticmethod
    def _extract_selected_planet_id(html: str, url: str = "") -> Optional[int]:
        if not html or "internalfleet" in (url or "").lower() or "newplanet=" not in html.lower():
            return None
        soup = BeautifulSoup(html, "html.parser")
        select_el = soup.find("select", attrs={"name": "planet"})
        if select_el is None:
            return None
        for opt in select_el.find_all("option"):
            val = str(opt.get("value") or "")
            if "action=internalfleet" in val.lower():
                return None
            if opt.has_attr("selected"):
                m = re.search(r"newplanet=(\d+)", val, re.IGNORECASE | re.ASCII)
                if m:
                    pid_s = m.group(1)
                    if pid_s.isascii() and pid_s.isdigit() and len(pid_s) <= 18:
                        return int(pid_s)
                    return None
        return None

    def _build_updated_state(
        self,
        client: Any,
        prev_state: Optional[GameState],
        action: PlannedAction,
        updated_overview: Any,
        *,
        page_html: str = "",
        is_home: bool = False,
        is_fleet: bool = False,
        include_buildings: bool = False,
        include_research: bool = False,
        include_shipyard: bool = False,
    ) -> Optional[GameState]:
        if not isinstance(updated_overview, GameState):
            return None

        planets = list(updated_overview.planets)
        planet_ids = {p.planet_id for p in planets}

        unread = dict(prev_state.unread_messages) if prev_state is not None else {}
        account_points = prev_state.account_points if prev_state is not None else None
        account_rank = prev_state.account_rank if prev_state is not None else None
        read_menu_fn = getattr(client, "read_menu_status", None)
        if callable(read_menu_fn):
            try:
                menu_res = read_menu_fn()
                if (
                    isinstance(menu_res, tuple)
                    and len(menu_res) == 3
                    and isinstance(menu_res[0], dict)
                ):
                    unread = dict(menu_res[0])
                    if menu_res[1] is not None:
                        account_points = menu_res[1]
                    if menu_res[2] is not None:
                        account_rank = menu_res[2]
            except Exception:
                pass

        tutorial = prev_state.tutorial if prev_state is not None else None
        speedup = prev_state.speedup_factor if prev_state is not None else None
        newbie = prev_state.newbie_protection if prev_state is not None else False
        merged_queues = list(prev_state.active_queues) if prev_state is not None else []
        alerts = prev_state.fleet_alerts if prev_state is not None else ()
        research_offers = prev_state.research_offers if prev_state is not None else ()

        active_pid: Optional[int] = None
        if is_home and page_html:
            home_pid, h_speedup, h_newbie, h_queues, h_alerts = parse_home_metadata(page_html)
            if home_pid is not None and home_pid in planet_ids:
                active_pid = home_pid
            if h_speedup is not None:
                speedup = h_speedup
            newbie = bool(h_newbie)
            merged_queues = list(h_queues)
            alerts = h_alerts
            tutorial = parse_tutorial_from_home(page_html)
        elif not is_fleet and page_html:
            sel_pid = self._extract_selected_planet_id(page_html)
            if sel_pid is not None and sel_pid in planet_ids:
                active_pid = sel_pid

        if active_pid is None:
            if (
                isinstance(self.last_observed_active_planet_id, int)
                and not isinstance(self.last_observed_active_planet_id, bool)
                and self.last_observed_active_planet_id in planet_ids
            ):
                active_pid = self.last_observed_active_planet_id
            elif action.planet_id in planet_ids:
                active_pid = action.planet_id
            elif prev_state is not None and prev_state.active_planet_id in planet_ids:
                active_pid = prev_state.active_planet_id
            else:
                active_pid = updated_overview.active_planet_id

        active_idx = next(
            (i for i, p in enumerate(planets) if p.planet_id == active_pid),
            0 if planets else None,
        )

        if include_buildings and page_html and active_idx is not None:
            parsed_tut = parse_tutorial_from_home(page_html)
            if parsed_tut is not None:
                tutorial = parsed_tut
            b_queues = parse_building_queue(page_html)
            if b_queues:
                merged_queues = [q for q in merged_queues if q.category != "building"] + list(b_queues)
            elif re.search(r"construction\s+queue\s*\(\s*0\b", page_html, re.IGNORECASE):
                merged_queues = [q for q in merged_queues if q.category != "building"]
            planet = planets[active_idx]
            offers = parse_buildings_page(page_html, planet.buildings)
            planets[active_idx] = replace(planet, building_offers=offers)

        if include_research and page_html:
            parsed_offers = parse_research_page(page_html, updated_overview.researches)
            research_offers = parsed_offers
            active_res = [o for o in parsed_offers if o.active]
            if active_res:
                res_jobs = [
                    QueueJob(category="research", name=o.name, target_level=o.target_level)
                    for o in active_res
                ]
                merged_queues = [q for q in merged_queues if q.category != "research"] + res_jobs
            elif parsed_offers:
                merged_queues = [q for q in merged_queues if q.category != "research"]

        if include_shipyard and page_html and active_idx is not None:
            planet = planets[active_idx]
            ship_offers = parse_unit_factory_page(
                page_html,
                unit_type="ship",
                owned_counts=planet.ships,
            )
            planets[active_idx] = replace(planet, ship_offers=ship_offers)

        if prev_state is not None:
            prev_rates = {p.planet_id: p.economy_rates for p in prev_state.planets if p.economy_rates}
            for i, p in enumerate(planets):
                rates = self.last_economy_rates.get(p.planet_id) or prev_rates.get(p.planet_id)
                if rates and not p.economy_rates:
                    planets[i] = replace(p, economy_rates=dict(rates))

        return replace(
            updated_overview,
            planets=tuple(planets),
            active_planet_id=active_pid,
            unread_messages=unread,
            tutorial=tutorial,
            active_queues=tuple(merged_queues),
            fleet_alerts=alerts,
            research_offers=research_offers,
            speedup_factor=speedup,
            newbie_protection=newbie,
            account_points=account_points,
            account_rank=account_rank,
            raid_targets=prev_state.raid_targets if prev_state is not None else updated_overview.raid_targets,
            raid_stats=prev_state.raid_stats if prev_state is not None else updated_overview.raid_stats,
        )

    def _prune_hour_window(self, now: float) -> None:
        cutoff = now - 3600.0
        while self._recent_timestamps and self._recent_timestamps[0] < cutoff:
            self._recent_timestamps.popleft()

    def remaining_hourly_budget(self) -> int:
        now = time.monotonic()
        self._prune_hour_window(now)
        cap = _safe_hourly_cap(self.max_actions_per_hour, 0)
        return max(0, cap - len(self._recent_timestamps))

    def _select_planet_if_needed(
        self,
        client: GameBrowserClient,
        planet_id: int,
        *,
        html: str = "",
        require_confirm: bool = False,
    ) -> tuple[bool, Optional[str]]:
        """Switch active planet in `F_Hauptframe` using `<select name='planet'>` `newPlanet=` options."""
        obs_html_pid = self._extract_selected_planet_id(html)
        if obs_html_pid is not None:
            self._record_observed_planet(obs_html_pid, confirmed=True)
        pid = game_data.safe_optional_int(planet_id, None)
        if pid is None or pid < 0 or pid > game_data.MAX_JSON_SAFE_INT:
            return False, None
        needs_switch, target_value = self._find_newplanet_switch_target(html, pid)
        if html and not needs_switch and target_value is None:
            return False, None

        client._switch_frame("F_Hauptframe")
        target_re = re.compile(rf"newplanet={pid}(?!\d)", re.IGNORECASE | re.ASCII)
        any_np_re = re.compile(r"newplanet=(\d+)", re.IGNORECASE | re.ASCII)
        paced = False
        nav_token: Optional[str] = None
        switched = False
        dom_selected_pid: Optional[int] = None

        for attempt in range(2):
            client._switch_frame("F_Hauptframe")
            try:
                select_el = client.driver.find_element(By.CSS_SELECTOR, 'select[name="planet"]')
            except NoSuchElementException:
                return False, None
            except (StaleElementReferenceException, ElementNotInteractableException, WebDriverException) as exc:
                if isinstance(exc, StaleElementReferenceException) and attempt == 0:
                    continue
                fallback_pid = dom_selected_pid if dom_selected_pid is not None else obs_html_pid
                if fallback_pid is not None:
                    self._record_observed_planet(fallback_pid, confirmed=True)
                return False, None

            chosen_val = target_value
            chosen_opt = None
            dom_selected_pid = None
            dom_has_newplanet = False
            try:
                for opt in select_el.find_elements(By.TAG_NAME, "option"):
                    val = str(opt.get_attribute("value") or "")
                    if "newplanet=" in val.lower():
                        dom_has_newplanet = True
                    try:
                        raw_sel = opt.is_selected()
                        is_sel = isinstance(raw_sel, (bool, int)) and bool(raw_sel)
                    except StaleElementReferenceException:
                        raise
                    except Exception:
                        is_sel = False
                    if is_sel:
                        m_dom = any_np_re.search(val)
                        if m_dom:
                            pid_s = m_dom.group(1)
                            if pid_s.isascii() and pid_s.isdigit() and len(pid_s) <= 18:
                                dom_selected_pid = int(pid_s)
                    if target_re.search(val):
                        if is_sel:
                            self._record_observed_planet(pid, confirmed=True)
                            return False, None
                        chosen_val = val
                        chosen_opt = opt
            except (StaleElementReferenceException, ElementNotInteractableException, WebDriverException) as exc:
                if isinstance(exc, StaleElementReferenceException) and attempt == 0:
                    continue
                fallback_pid = dom_selected_pid if dom_selected_pid is not None else obs_html_pid
                if fallback_pid is not None:
                    self._record_observed_planet(fallback_pid, confirmed=True)
                return False, None

            if dom_selected_pid is not None:
                self._record_observed_planet(dom_selected_pid, confirmed=True)
            if chosen_val is None and chosen_opt is None:
                return False, None
            if dom_has_newplanet and chosen_opt is None:
                return False, None
            if chosen_opt is None and dom_selected_pid is not None and dom_selected_pid == pid:
                return False, None

            if not paced:
                self._pace_action(client, priority="interactive")
                self._record_observed_planet(None, confirmed=False)
                nav_token = self._mark_frame_nav(client)
                paced = True

            switched = False
            stale_during_switch = False
            if chosen_val is not None:
                try:
                    Select(select_el).select_by_value(chosen_val)
                    switched = True
                except StaleElementReferenceException:
                    switched = False
                    stale_during_switch = True
                except Exception:
                    switched = False
            if not switched and chosen_opt is not None:
                try:
                    self._safe_click(client, chosen_opt)
                    switched = True
                    stale_during_switch = False
                except (
                    StaleElementReferenceException,
                    NoSuchElementException,
                    ElementNotInteractableException,
                    WebDriverException,
                ) as exc:
                    switched = False
                    stale_during_switch = isinstance(exc, StaleElementReferenceException)
            if not switched and chosen_opt is not None and chosen_val and not stale_during_switch:
                # SpaceInvasion RETRO (data-theme="new") wraps <select name="planet"> inside
                # <div style="display:none;"> (replacing it visually with #planet-chooser /
                # div.planet-entry), so Selenium Select & option.click() raise
                # ElementNotInteractableException. Trigger the hidden <select>'s matching
                # option + onchange handler via JS to switch planets in-place on F_Hauptframe.
                try:
                    js_res = client.driver.execute_script(
                        "var sel = arguments[0], val = arguments[1]; "
                        "if (!sel || !val || !sel.options) return false; "
                        "var matched = false; "
                        "for (var i = 0; i < sel.options.length; i++) { "
                        "  if (sel.options[i].value === val) { sel.selectedIndex = i; matched = true; break; } "
                        "} "
                        "if (!matched) return false; "
                        "if (typeof sel.onchange === 'function') { sel.onchange(); return true; } "
                        "sel.dispatchEvent(new Event('change', {bubbles: true})); "
                        "return true;",
                        select_el,
                        chosen_val,
                    )
                    if js_res is True:
                        switched = True
                        stale_during_switch = False
                except StaleElementReferenceException:
                    switched = False
                    stale_during_switch = True
                except Exception:
                    switched = False
            if switched:
                break
            if stale_during_switch and attempt == 0:
                continue
            break

        updated_html: Optional[str] = None
        if switched:
            updated_html = client._wait_frame_ready("F_Hauptframe", nav_token=nav_token)
            obs_after = self._extract_selected_planet_id(updated_html or "")
            if obs_after is not None:
                self._record_observed_planet(obs_after, confirmed=True)
                if obs_after != pid:
                    switched = False
            else:
                self._record_observed_planet(pid, confirmed=True)
        else:
            fallback_pid = dom_selected_pid if dom_selected_pid is not None else obs_html_pid
            if fallback_pid is not None:
                self._record_observed_planet(fallback_pid, confirmed=True)
        return switched, updated_html

    def _navigate_and_select_planet(
        self,
        client: GameBrowserClient,
        state: GameState,
        menu_action: str,
        planet_id: int,
        *,
        active_planet_confirmed: bool = True,
    ) -> str:
        _call_with_optional_priority(client.ensure_in_game, priority="interactive")
        # On `internalFleet`, `select[name="planet"]` is target favourites (`REQ-UI-080`), NOT planet switcher!
        if menu_action == "internalFleet":
            planets = getattr(state, "planets", ()) or ()
            needs_confirm = (not active_planet_confirmed) and len(planets) > 1
            if state.active_planet_id != planet_id or needs_confirm:
                b_html = _call_with_optional_priority(
                    client.navigate_menu,
                    "internalBuildings",
                    priority="interactive",
                )
                b_html_str = b_html if isinstance(b_html, str) else ""
                obs_pid = self._extract_selected_planet_id(b_html_str, "internalBuildings")
                if obs_pid is not None:
                    self._record_observed_planet(obs_pid, confirmed=True)
                b_needs_switch, _ = self._find_newplanet_switch_target(b_html_str, planet_id)
                switched, _ = self._select_planet_if_needed(
                    client,
                    planet_id,
                    html=b_html_str,
                    require_confirm=True,
                )
                if self._confirmed_planet_mismatch(planet_id) is not None or (b_needs_switch and not switched):
                    return ""
            return _call_with_optional_priority(
                client.navigate_menu,
                "internalFleet",
                priority="interactive",
            )

        is_real_selenium = getattr(
            getattr(client, "driver", None), "__class__", object
        ).__module__.startswith("selenium")
        html_str = ""
        if is_real_selenium:
            try:
                client._switch_frame("F_Hauptframe")
                cur_url = str(client.driver.execute_script("return location.href;") or "")
                cur_html = str(client.driver.page_source or "")
                cur_url_l = cur_url.lower()
                if (
                    f"action={menu_action}".lower() in cur_url_l
                    and "internalbuilditem" not in cur_url_l
                    and cur_html.strip()
                    and not is_server_rate_limit_html(cur_html)
                    and "bitmeup-login" not in cur_html.lower()
                ):
                    html_str = cur_html
            except Exception:
                html_str = ""

        if not html_str:
            html = _call_with_optional_priority(
                client.navigate_menu,
                menu_action,
                priority="interactive",
            )
            html_str = html if isinstance(html, str) else ""
        else:
            html = html_str
        obs_pid = self._extract_selected_planet_id(html_str, menu_action)
        if obs_pid is not None:
            self._record_observed_planet(obs_pid, confirmed=True)
        needs_switch, _ = self._find_newplanet_switch_target(html_str, planet_id)
        switched = False
        if needs_switch or (state.active_planet_id != planet_id and "newplanet=" in html_str.lower()):
            switched, updated_html = self._select_planet_if_needed(client, planet_id, html=html_str)
            if switched and updated_html:
                html = updated_html
        if self._confirmed_planet_mismatch(planet_id) is not None or (needs_switch and not switched):
            return ""
        return html

    def _click_dom_link_with_retry(
        self,
        client: GameBrowserClient,
        action: PlannedAction,
        initial_html: str,
        selector: str,
        is_ready_fn: Callable[[str], bool],
    ) -> tuple[Optional[str], Optional[ExecutionResult]]:
        """Verify offer availability and click `selector`, retrying once on countdown reload."""
        tname_s = _safe_str(action.target_name)
        html = initial_html
        for attempt in range(2):
            if not is_ready_fn(html):
                return None, ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: {tname_s} is no longer actionable on page.",
                )
            try:
                client._switch_frame("F_Hauptframe")
                btn = client.driver.find_element(By.CSS_SELECTOR, selector)
                self._pace_action(client, priority="interactive")
                nav_token = self._mark_frame_nav(client)
                time.sleep(random.uniform(*self.action_delay_range))
                self._safe_click(client, btn)
                after_html = client._wait_frame_ready("F_Hauptframe", nav_token=nav_token)
                self._recent_timestamps.append(time.monotonic())
                return after_html, None
            except StaleElementReferenceException:
                if attempt == 1:
                    return None, ExecutionResult(
                        executed=False,
                        dry_run=False,
                        action=action,
                        message=f"Aborted: action button for {tname_s} became stale.",
                    )
                html = client._wait_frame_ready("F_Hauptframe")
            except NoSuchElementException:
                return None, ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: action button for {tname_s} not found in DOM.",
                )
            except (ElementNotInteractableException, WebDriverException) as exc:
                return None, ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: action button for {tname_s} not interactable ({type(exc).__name__}).",
                )
        return None, ExecutionResult(
            executed=False,
            dry_run=False,
            action=action,
            message=f"Aborted: failed to click {tname_s}.",
        )

    def _execute_claim_tutorial(
        self,
        client: GameBrowserClient,
        action: PlannedAction,
        state: Optional[GameState] = None,
    ) -> ExecutionResult:
        """Expand `#tutorial.text` if collapsed and submit `Mission complete` in `internalHome`."""
        _call_with_optional_priority(client.ensure_in_game, priority="interactive")
        home_html = _call_with_optional_priority(client.navigate_menu, "internalHome", priority="interactive")
        if isinstance(home_html, str) and home_html:
            home_pid, *_ = parse_home_metadata(home_html)
            if home_pid is not None:
                self._record_observed_planet(home_pid, confirmed=True)
        client._switch_frame("F_Hauptframe")

        submit_css = 'form[action*="internalHome"] input[name="tutorialComplete"] ~ input[type="submit"]'
        has_tut_div_in_html = True
        has_complete_in_html = False
        if isinstance(home_html, str) and home_html:
            home_soup = BeautifulSoup(home_html, "html.parser")
            has_tut_div_in_html = home_soup.find(id="tutorial.text") is not None
            has_complete_in_html = home_soup.select_one('input[name="tutorialComplete"]') is not None
            if has_complete_in_html and home_soup.select_one(submit_css) is None:
                submit_css = (
                    'form:has(input[name="tutorialComplete"]) input[type="submit"], '
                    'form:has(input[name="tutorialComplete"]) button[type="submit"], '
                    'input[name="tutorialComplete"] ~ input[type="submit"], '
                    'input[name="tutorialComplete"] ~ button[type="submit"]'
                )

        nav_token = self._mark_frame_nav(client)
        paced = False
        for attempt in range(2):
            try:
                if has_tut_div_in_html or not has_complete_in_html:
                    tut_div = client.driver.find_element(By.ID, "tutorial.text")
                    if not tut_div.is_displayed():
                        try:
                            self._safe_click(client, client.driver.find_element(By.ID, "tutorial.show"))
                            time.sleep(0.3)
                        except (NoSuchElementException, ElementNotInteractableException, WebDriverException):
                            client.driver.execute_script("arguments[0].style.display='block';", tut_div)

                submit_btn = client.driver.find_element(
                    By.CSS_SELECTOR,
                    submit_css,
                )
                if not paced:
                    self._pace_action(client, priority="interactive")
                    nav_token = self._mark_frame_nav(client)
                    time.sleep(random.uniform(*self.action_delay_range))
                    paced = True
                self._safe_click(client, submit_btn)
                break
            except StaleElementReferenceException:
                if attempt == 0:
                    continue
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message="Aborted: tutorial completion DOM controls became stale.",
                )
            except NoSuchElementException:
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message="Aborted: tutorial completion DOM controls not found.",
                )
            except (ElementNotInteractableException, WebDriverException) as exc:
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: tutorial completion DOM controls not interactable ({type(exc).__name__}).",
                )
        after_html = client._wait_frame_ready("F_Hauptframe", nav_token=nav_token)
        if isinstance(after_html, str) and after_html:
            after_pid, *_ = parse_home_metadata(after_html)
            if after_pid is not None:
                self._record_observed_planet(after_pid, confirmed=True)
        self._recent_timestamps.append(time.monotonic())
        updated = _call_with_optional_priority(
            client.read_planets_overview,
            refresh=True,
            priority="interactive",
        )
        updated_state = self._build_updated_state(
            client,
            state,
            action,
            updated,
            page_html=after_html if isinstance(after_html, str) else "",
            is_home=True,
        )
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=action,
            message=f"Claimed {_safe_str(action.target_name)}.",
            updated_state=updated_state,
        )

    def _execute_build(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        html = self._navigate_and_select_planet(client, state, "internalBuildings", action.planet_id)
        mismatch_pid = self._confirmed_planet_mismatch(action.planet_id)
        if mismatch_pid is not None:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=(
                    f"Aborted: active planet is {mismatch_pid}, "
                    f"not requested planet {game_data.fmt_compact_int(action.planet_id)}."
                ),
                observed_active_planet_id=mismatch_pid,
                observed_active_planet_confirmed=True,
            )
        planet_map = next(
            (p.buildings for p in state.planets if p.planet_id == action.planet_id),
            None,
        )
        tid = game_data.cap_json_int(game_data.safe_int(action.target_id, 0))
        selector = f'a.bau[href*="internalBuildItem"][href*="buildingID={tid}"]'

        def _ready(page_html: str) -> bool:
            offers = parse_buildings_page(page_html, planet_map)
            m = next((o for o in offers if o.building_id == action.target_id), None)
            return bool(m and m.can_build)

        after_html, err = self._click_dom_link_with_retry(client, action, html, selector, _ready)
        if err is not None:
            return err
        assert after_html is not None

        updated = _call_with_optional_priority(
            client.read_planets_overview,
            refresh=True,
            priority="interactive",
        )
        after_planet = next(
            (p for p in updated.planets if p.planet_id == action.planet_id),
            updated.active_planet(),
        )
        new_lvl = after_planet.building_level(game_data.safe_int(action.target_id, 0)) if after_planet else None
        after_lower = after_html.lower()
        tgt_lvl_opt = game_data.safe_optional_int(action.target_level, None)
        before_planet = next(
            (p for p in state.planets if p.planet_id == action.planet_id),
            None,
        )
        res_spent = (
            before_planet is not None
            and after_planet is not None
            and (
                after_planet.resources.pig_iron < before_planet.resources.pig_iron
                or after_planet.resources.metal < before_planet.resources.metal
                or after_planet.resources.kryptonite < before_planet.resources.kryptonite
                or after_planet.resources.spice < before_planet.resources.spice
            )
        )
        in_queue = (
            "construction queue" in after_lower
            or "abort" in after_lower
            or "cancel" in after_lower
            or any(getattr(q, "category", "") == "building" for q in (updated.active_queues or ()))
            or res_spent
            or (new_lvl is not None and tgt_lvl_opt is not None and new_lvl >= tgt_lvl_opt)
        )
        is_validated = bool(in_queue or len(after_html.strip()) < 200)
        val_err = (
            None
            if is_validated
            else f"Build {_safe_str(action.target_name)} -> L{tgt_lvl_opt} was clicked, yet neither construction queue, building level, nor resources changed."
        )
        tgt_txt = (
            game_data.fmt_compact_int(tgt_lvl_opt)
            if tgt_lvl_opt is not None
            else "None"
        )
        new_lvl_txt = game_data.fmt_compact_int(new_lvl) if new_lvl is not None else "None"
        detail = (
            f"Built/queued {_safe_str(action.target_name)} -> L{tgt_txt} "
            f"(verified={'ok' if in_queue else 'submitted'}, current_lvl={new_lvl_txt})."
        )
        updated_state = self._build_updated_state(
            client,
            state,
            action,
            updated,
            page_html=after_html if isinstance(after_html, str) else "",
            include_buildings=True,
        )
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=action,
            message=detail,
            updated_state=updated_state,
            validated=is_validated,
            validation_error=val_err,
        )

    def _execute_demolish_building(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        html = self._navigate_and_select_planet(client, state, "internalBuildings", action.planet_id)
        mismatch_pid = self._confirmed_planet_mismatch(action.planet_id)
        if mismatch_pid is not None:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=(
                    f"Aborted: active planet is {mismatch_pid}, "
                    f"not requested planet {game_data.fmt_compact_int(action.planet_id)}."
                ),
                observed_active_planet_id=mismatch_pid,
                observed_active_planet_confirmed=True,
            )
        planet = next((p for p in state.planets if p.planet_id == action.planet_id), None)
        tid = game_data.cap_json_int(game_data.safe_int(action.target_id, 0))
        if planet is not None and planet.building_level(tid) <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: {_safe_str(action.target_name)} is already at level 0.",
            )

        open_detail_fn = getattr(client, "open_building_detail", None)
        detail_html = html
        if callable(open_detail_fn) and f"destructableid={tid}" not in (html or "").lower():
            d_res = _call_with_optional_priority(open_detail_fn, tid, priority="interactive")
            if isinstance(d_res, str) and d_res:
                detail_html = d_res

        selector = (
            f'input[onclick*="destructableID={tid}"], '
            f'button[onclick*="destructableID={tid}"], '
            f'a[href*="destructableID={tid}"], '
            f'a[onclick*="destructableID={tid}"]'
        )

        def _ready(page_html: str) -> bool:
            return f"destructableid={tid}" in (page_html or "").lower()

        after_html, err = self._click_dom_link_with_retry(client, action, detail_html, selector, _ready)
        if err is not None:
            return err
        assert after_html is not None

        updated = _call_with_optional_priority(
            client.read_planets_overview,
            refresh=True,
            priority="interactive",
        )
        after_planet = next(
            (p for p in updated.planets if p.planet_id == action.planet_id),
            updated.active_planet(),
        )
        new_lvl = after_planet.building_level(tid) if after_planet else None
        tgt_lvl_opt = game_data.safe_optional_int(action.target_level, None)
        tgt_txt = game_data.fmt_compact_int(tgt_lvl_opt) if tgt_lvl_opt is not None else "0"
        new_lvl_txt = game_data.fmt_compact_int(new_lvl) if new_lvl is not None else "None"
        detail = (
            f"Demolished/queued downgrade of {_safe_str(action.target_name)} -> L{tgt_txt} "
            f"(current_lvl={new_lvl_txt})."
        )
        updated_state = self._build_updated_state(
            client,
            state,
            action,
            updated,
            page_html=after_html if isinstance(after_html, str) else "",
            include_buildings=True,
        )
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=action,
            message=detail,
            updated_state=updated_state,
        )

    def _execute_research(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        html = self._navigate_and_select_planet(client, state, "internalResearch", action.planet_id)
        mismatch_pid = self._confirmed_planet_mismatch(action.planet_id)
        if mismatch_pid is not None:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=(
                    f"Aborted: active planet is {mismatch_pid}, "
                    f"not requested planet {game_data.fmt_compact_int(action.planet_id)}."
                ),
                observed_active_planet_id=mismatch_pid,
                observed_active_planet_confirmed=True,
            )
        tid = game_data.cap_json_int(game_data.safe_int(action.target_id, 0))
        selector = f'a.bau[href*="internalBuildItem"][href*="researchID={tid}"]'

        def _ready(page_html: str) -> bool:
            offers = parse_research_page(page_html, state.researches)
            m = next((o for o in offers if o.research_id == action.target_id), None)
            return bool(m and m.can_research)

        after_html, err = self._click_dom_link_with_retry(client, action, html, selector, _ready)
        if err is not None:
            return err
        assert after_html is not None

        updated = _call_with_optional_priority(
            client.read_planets_overview,
            refresh=True,
            priority="interactive",
        )
        new_lvl = game_data.safe_int(
            updated.researches.get(
                f"r{game_data.cap_json_int(game_data.safe_int(action.target_id, 0))}",
                0,
            ),
            0,
        )
        after_lower = after_html.lower()
        tgt_lvl_opt = game_data.safe_optional_int(action.target_level, None)
        verified = (
            "cancel" in after_lower
            or "abort" in after_lower
            or any(getattr(q, "category", "") == "research" for q in (updated.active_queues or ()))
            or (tgt_lvl_opt is not None and new_lvl >= tgt_lvl_opt)
        )
        res_validated = bool(verified or len(after_html.strip()) < 200)
        res_val_err = (
            None
            if res_validated
            else f"Research {_safe_str(action.target_name)} -> L{tgt_lvl_opt} was clicked, yet neither research queue nor level changed."
        )
        tgt_txt = (
            game_data.fmt_compact_int(tgt_lvl_opt)
            if tgt_lvl_opt is not None
            else "None"
        )
        detail = (
            f"Started research {_safe_str(action.target_name)} -> L{tgt_txt} "
            f"(verified={'ok' if verified else 'submitted'}, current_lvl={game_data.fmt_compact_int(new_lvl)})."
        )
        updated_state = self._build_updated_state(
            client,
            state,
            action,
            updated,
            page_html=after_html if isinstance(after_html, str) else "",
            include_research=True,
        )
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=action,
            message=detail,
            updated_state=updated_state,
            validated=res_validated,
            validation_error=res_val_err,
        )

    def _execute_build_ship(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        html = self._navigate_and_select_planet(client, state, "internalWeaponFactory", action.planet_id)
        mismatch_pid = self._confirmed_planet_mismatch(action.planet_id)
        if mismatch_pid is not None:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=(
                    f"Aborted: active planet is {mismatch_pid}, "
                    f"not requested planet {game_data.fmt_compact_int(action.planet_id)}."
                ),
                observed_active_planet_id=mismatch_pid,
                observed_active_planet_confirmed=True,
            )
        planet = next((p for p in state.planets if p.planet_id == action.planet_id), None)
        offers = parse_unit_factory_page(
            html,
            unit_type="ship",
            owned_counts=planet.ships if planet else None,
        )
        tname_s = _safe_str(action.target_name)
        coords_s = _safe_str(action.planet_coords)
        matching = next((o for o in offers if o.unit_id == action.target_id), None)
        if matching is None or not matching.can_build or not matching.input_name:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: ship {tname_s} input unavailable in Weapons factory.",
            )

        qty = max(1, game_data.cap_json_int(game_data.safe_int(action.target_level, 1)))
        client._switch_frame("F_Hauptframe")
        nav_token = self._mark_frame_nav(client)
        paced = False
        for attempt in range(2):
            try:
                qty_input = client.driver.find_element(By.NAME, matching.input_name)
                self._set_text_input_value(client, qty_input, str(qty))
                form = client.driver.find_element(By.CSS_SELECTOR, 'form[action*="buildShip=1"]')
                if not paced:
                    self._pace_action(client, priority="interactive")
                    nav_token = self._mark_frame_nav(client)
                    time.sleep(random.uniform(*self.action_delay_range))
                    paced = True
                submit_btn = form.find_element(By.CSS_SELECTOR, 'input[type="submit"], button[type="submit"]')
                self._safe_click(client, submit_btn)
                break
            except StaleElementReferenceException:
                if attempt == 0:
                    continue
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: shipyard form controls for {tname_s} became stale.",
                )
            except NoSuchElementException:
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: shipyard form controls for {tname_s} not found.",
                )
            except (ElementNotInteractableException, WebDriverException) as exc:
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: shipyard form controls for {tname_s} not interactable ({type(exc).__name__}).",
                )

        after_html = client._wait_frame_ready("F_Hauptframe", nav_token=nav_token)
        self._recent_timestamps.append(time.monotonic())
        updated = _call_with_optional_priority(
            client.read_planets_overview,
            refresh=True,
            priority="interactive",
        )
        updated_state = self._build_updated_state(
            client,
            state,
            action,
            updated,
            page_html=after_html if isinstance(after_html, str) else "",
            include_shipyard=True,
        )
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=action,
            message=f"Ordered {game_data.fmt_compact_int(qty)}x {tname_s} on [{coords_s}].",
            updated_state=updated_state,
        )

    def _execute_throttle_building(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        planet_obj = next(
            (
                p
                for p in getattr(state, "planets", ())
                if getattr(p, "planet_id", None) == action.planet_id
            ),
            None,
        )
        if planet_obj is not None:
            ptype_norm = normalize_planet_type(planet_obj)
            if ptype_norm in ("asteroid", "raid"):
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: internalEconomy throttling is not supported on {ptype_norm} planets.",
                )
        bid = 2 if action.target_id is None else game_data.safe_int(action.target_id, -1)
        if bid not in (1, 2, 3, 4, 5, 6):
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: building b{game_data.fmt_compact_int(bid)} does not support internalEconomy rate control.",
            )

        field_name = f"resource{bid}"
        raw_pct = 0 if action.target_level is None else game_data.safe_int(action.target_level, 0)
        target_pct = max(0, min(100, (raw_pct // 10) * 10))

        html = self._navigate_and_select_planet(client, state, "internalEconomy", action.planet_id)
        mismatch_pid = self._confirmed_planet_mismatch(action.planet_id)
        if mismatch_pid is not None:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=(
                    f"Aborted: active planet is {mismatch_pid}, "
                    f"not requested planet {game_data.fmt_compact_int(action.planet_id)}."
                ),
                observed_active_planet_id=mismatch_pid,
                observed_active_planet_confirmed=True,
            )
        if not str(html or "").strip():
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: failed to load internalEconomy for planet {game_data.fmt_compact_int(action.planet_id)}.",
            )
        tname_s = _safe_str(action.target_name)
        coords_s = _safe_str(action.planet_coords)
        cur_rates = parse_economy_rates(html)
        if bid == 6 and "resource6" not in cur_rates and "resource5" in cur_rates:
            field_name = "resource5"
        if cur_rates:
            self.last_economy_rates[action.planet_id] = dict(cur_rates)
        if field_name in cur_rates and cur_rates[field_name] == target_pct:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"No-op: {tname_s} ({field_name}) is already at {target_pct}% on [{coords_s}].",
            )

        econ_soup = BeautifulSoup(html, "html.parser")
        if econ_soup.select_one('form[name="economy"] input[type="submit"][value="Calculate"]') is not None:
            submit_css = 'form[name="economy"] input[type="submit"][value="Calculate"]'
        elif econ_soup.select_one('form[name="economy"] input[type="submit"], form[name="economy"] button[type="submit"]') is not None:
            submit_css = 'form[name="economy"] input[type="submit"], form[name="economy"] button[type="submit"]'
        else:
            submit_css = 'form#ress input[type="submit"], form[action*="internalEconomy"] input[type="submit"], form input[type="submit"]'

        client._switch_frame("F_Hauptframe")
        nav_token = self._mark_frame_nav(client)
        paced = False
        for attempt in range(2):
            try:
                select_el = client.driver.find_element(By.NAME, field_name)
                Select(select_el).select_by_value(str(target_pct))
                submit_btn = client.driver.find_element(
                    By.CSS_SELECTOR,
                    submit_css,
                )
                if not paced:
                    self._pace_action(client, priority="interactive")
                    nav_token = self._mark_frame_nav(client)
                    time.sleep(random.uniform(*self.action_delay_range))
                    paced = True
                self._safe_click(client, submit_btn)
                break
            except StaleElementReferenceException:
                if attempt == 0:
                    continue
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: internalEconomy control {field_name} became stale.",
                )
            except NoSuchElementException:
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: internalEconomy control {field_name} not found.",
                )
            except (ElementNotInteractableException, WebDriverException) as exc:
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: internalEconomy control {field_name} not interactable ({type(exc).__name__}).",
                )

        updated_html = client._wait_frame_ready("F_Hauptframe", nav_token=nav_token)
        post_rates = parse_economy_rates(updated_html)
        if field_name in post_rates and post_rates[field_name] != target_pct:
            self.last_economy_rates[action.planet_id] = dict(post_rates)
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=(
                    f"Aborted: internalEconomy Calculate submit did not persist "
                    f"{field_name}={target_pct}% (still {post_rates[field_name]}%) on [{coords_s}]."
                ),
            )
        updated_rates = dict(post_rates) if post_rates else dict(cur_rates)
        updated_rates[field_name] = target_pct
        self.last_economy_rates[action.planet_id] = dict(updated_rates)

        self._recent_timestamps.append(time.monotonic())
        updated = _call_with_optional_priority(
            client.read_planets_overview,
            refresh=True,
            priority="interactive",
        )
        updated_state = self._build_updated_state(
            client,
            state,
            action,
            updated,
            page_html=updated_html if isinstance(updated_html, str) else "",
        )
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=action,
            message=f"Set {tname_s} ({field_name}) to {target_pct}% on [{coords_s}].",
            updated_state=updated_state,
        )

    def _execute_scan_galaxy(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        tname_s = _safe_str(action.target_name)
        coords_s = _safe_str(action.planet_coords)
        m = re.search(r"(\d{1,2}):(\d{1,3})", tname_s, re.ASCII) or re.search(
            r"(\d{1,2}):(\d{1,3})", coords_s, re.ASCII
        )
        gal, sys_num = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
        _call_with_optional_priority(client.ensure_in_game, priority="interactive")
        html = client.open_galaxy(galaxy=gal, system=sys_num)
        actual_coords = getattr(client, "last_galaxy_coords", None)
        if (
            isinstance(actual_coords, tuple)
            and len(actual_coords) == 2
            and all(isinstance(v, int) for v in actual_coords)
        ):
            gal, sys_num = actual_coords
        slots = parse_galaxy_system_html(html, galaxy=gal, system=sys_num)
        self.last_galaxy_slots = list(slots)
        # Read-only SCAN_GALAXY does not consume mutation hourly budget
        occupied = [s for s in slots if s.status_color != "free"]
        yellows = [s for s in slots if s.status_color == "yellow"]
        spacias = [s for s in slots if s.is_spacia]
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=action,
            message=(
                f"Scanned Galaxy [{gal:02d}:{sys_num:03d}]: "
                f"{len(occupied)} occupied ({len(yellows)} inactive yellow, {len(spacias)} Spacia)."
            ),
        )

    @staticmethod
    def _pick_send_fleet_form_css(send_soup: BeautifulSoup) -> str:
        if send_soup.select_one('form[action*="internalSendFleetFinish"]'):
            return 'form[action*="internalSendFleetFinish"]'
        send_forms = send_soup.select('form[action*="internalSendFleet"]')
        if send_forms and any(f.select('input[type="radio"]') for f in send_forms):
            if len(send_forms) == 1:
                return 'form[action*="internalSendFleet"]'
            for f in send_forms:
                if f.select('input[type="radio"]'):
                    fid = str(f.get("id") or "").strip()
                    if fid and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", fid):
                        return f"form#{fid}"
        if send_soup.select_one('form[name="fleet"]') and send_soup.select_one('form[name="fleet"] input[type="radio"]'):
            return 'form[name="fleet"]'
        if send_forms:
            return 'form[action*="internalSendFleet"]'
        if send_soup.select_one('form[name="fleet"]'):
            return 'form[name="fleet"]'
        for form_el in send_soup.find_all("form"):
            txt = form_el.get_text(" ", strip=True).lower()
            radios = form_el.select('input[type="radio"]')
            if radios and any(
                tok in txt for tok in ("attack", "spying", "espionage", "spionage", "mission", "transport")
            ):
                fid = str(form_el.get("id") or "").strip()
                if fid and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", fid):
                    return f"form#{fid}"
                faction = str(form_el.get("action") or "").strip()
                if faction:
                    return f'form[action="{faction}"]'
        return "form"

    _STRUCTURAL_RADIO_STOP_TAGS = (
        "br",
        "td",
        "th",
        "div",
        "tr",
        "p",
        "li",
        "hr",
        "table",
        "tbody",
        "thead",
        "tfoot",
        "fieldset",
        "section",
        "form",
        "legend",
        "caption",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    )
    _NON_LABEL_CONTAINER_TAGS = (
        "tr",
        "form",
        "table",
        "tbody",
        "thead",
        "tfoot",
        "fieldset",
        "section",
        "ul",
        "ol",
        "dl",
        "[document]",
    )
    _WRAPPER_WALK_STOP_TAGS = (
        "br",
        "td",
        "th",
        "tr",
        "p",
        "li",
        "hr",
        "table",
        "tbody",
        "thead",
        "tfoot",
        "fieldset",
        "section",
        "form",
        "legend",
        "caption",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "[document]",
    )
    _FORM_CONTROL_TAGS = ("input", "select", "textarea", "button")
    _TR_HEADER_LABEL_RE = re.compile(
        r"^(?:mission|auftrag|speed|geschwindigkeit|route|aktion|action|options?|select|choose)\b",
        re.IGNORECASE,
    )
    _TR_OPTION_TOKEN_RE = re.compile(
        r"(?:(?<!\d)\d{1,3}\s*%(?!\w)"
        r"|\b(?:attack|angriff|angreif(?:en)?|raid|spy(?:ing)?|spio(?:nage|nier(?:en)?)?|espionage"
        r"|transport(?:ier(?:en)?)?|station(?:ing|ieren)?|deploy|colonize|kolonisier(?:en)?"
        r"|recycl(?:e|ing)|abbau(?:en)?|harvest|escape(?:\s+flight)?|flucht|expedition"
        r"|explore|erkund(?:ung|en)?|hold|halt(?:en)?|escort|defend|direct|direkt|detour|umweg)\b)",
        re.IGNORECASE | re.ASCII,
    )
    _TR_UTILITY_CELL_RE = re.compile(
        r"^(?:help|hilfe|info|hint|hinweis|note|send|submit|absenden|ok|cancel|abort|abbrechen|back|zur[uü]ck|close|schlie[sß]en)\b|^\?$",
        re.IGNORECASE,
    )

    @classmethod
    def _is_visible_form_control(cls, tag: Tag) -> bool:
        if tag.name in ("select", "textarea", "button"):
            return True
        if tag.name == "input":
            return str(tag.get("type") or "").strip().lower() != "hidden"
        return False

    @classmethod
    def _has_visible_form_control(cls, tag: Tag) -> bool:
        if cls._is_visible_form_control(tag):
            return True
        return any(cls._is_visible_form_control(c) for c in tag.find_all(cls._FORM_CONTROL_TAGS))

    @classmethod
    def _has_other_visible_form_control(cls, container: Tag, target_radio: Tag) -> bool:
        for ctrl in container.find_all(cls._FORM_CONTROL_TAGS):
            if ctrl is target_radio:
                continue
            if cls._is_visible_form_control(ctrl):
                return True
        return False

    @classmethod
    def _collect_adjacent_radio_text(
        cls,
        node: Tag,
        *,
        reverse: bool = False,
        target_id: str = "",
    ) -> str:
        parts: list[str] = []
        siblings = node.previous_siblings if reverse else node.next_siblings
        for sib in siblings:
            if isinstance(sib, NavigableString):
                txt = " ".join(str(sib).split())
                if txt:
                    parts.append(txt)
            elif isinstance(sib, Tag):
                if sib.name in cls._STRUCTURAL_RADIO_STOP_TAGS:
                    break
                if sib.name == "input" and str(sib.get("type") or "").strip().lower() == "hidden":
                    continue
                if sib.name == "label":
                    lbl_for = str(sib.get("for") or "").strip().lower()
                    if lbl_for and lbl_for != target_id:
                        if reverse:
                            parts.clear()
                        break
                if cls._has_visible_form_control(sib):
                    if reverse:
                        parts.clear()
                    break
                txt = " ".join(sib.get_text(" ", strip=True).split())
                if txt:
                    parts.append(txt)
        if reverse:
            parts.reverse()
        return " ".join(parts)

    @classmethod
    def _is_tr_header_cell(cls, cell_tag: Tag, txt: str) -> bool:
        if cell_tag.name == "th":
            return True
        if txt.endswith(":"):
            return True
        if cls._TR_OPTION_TOKEN_RE.search(txt):
            return False
        return bool(cls._TR_HEADER_LABEL_RE.match(txt))

    @classmethod
    def _is_tr_option_label(cls, cell_tag: Tag, txt: str) -> bool:
        if not txt or cls._is_tr_header_cell(cell_tag, txt):
            return False
        if re.search(r"[A-Za-z]\s*:", txt) or len(txt.split()) > 5:
            return False
        return bool(cls._TR_OPTION_TOKEN_RE.search(txt))

    @classmethod
    def _cell_labels_foreign_id(cls, cell_tag: Tag, target_radio_id: str) -> bool:
        lbls = [lbl for lbl in cell_tag.find_all("label") if str(lbl.get("for") or "").strip()]
        if not lbls:
            return False
        for_ids = {str(lbl.get("for") or "").strip().lower() for lbl in lbls}
        if target_radio_id and target_radio_id in for_ids:
            return False
        lbl_txt = " ".join(" ".join(l.get_text(" ", strip=True).split()) for l in lbls).strip()
        full_txt = " ".join(cell_tag.get_text(" ", strip=True).split())
        return bool(lbl_txt) and lbl_txt == full_txt

    @classmethod
    def _is_pure_link_cell(cls, cell_tag: Tag, txt: str) -> bool:
        links = cell_tag.find_all("a")
        if not links:
            return False
        lnk_txt = " ".join(" ".join(a.get_text(" ", strip=True).split()) for a in links).strip()
        return bool(lnk_txt) and lnk_txt == txt

    @classmethod
    def _extract_multi_radio_tr_cell_label(cls, tr: Tag, cell: Tag) -> str:
        cells = tr.find_all(["td", "th"], recursive=False)
        if cell not in cells and cell.parent is not None:
            cells = cell.parent.find_all(["td", "th"], recursive=False)
        if cell not in cells:
            nxt_td = cell.find_next_sibling(["td", "th"])
            if (
                nxt_td is not None
                and not nxt_td.select('input[type="radio"]')
                and not cls._has_visible_form_control(nxt_td)
            ):
                return " ".join(nxt_td.get_text(" ", strip=True).split())
            return ""

        cell_idx = cells.index(cell)
        has_radio = [bool(c.select('input[type="radio"]')) for c in cells]
        cell_txts = [" ".join(c.get_text(" ", strip=True).split()) for c in cells]
        is_label = [
            (not has_radio[i])
            and (not cls._has_visible_form_control(cells[i]))
            and bool(cell_txts[i])
            for i in range(len(cells))
        ]
        radio_ids: dict[int, str] = {}
        needs_sib: list[int] = []
        for i, c in enumerate(cells):
            radios_in_c = c.select('input[type="radio"]')
            if len(radios_in_c) == 1 and not cell_txts[i]:
                needs_sib.append(i)
                radio_ids[i] = str(radios_in_c[0].get("id") or "").strip().lower()

        def _can_pair(r_idx: int, lbl_idx: int) -> bool:
            if not (0 <= lbl_idx < len(cells)) or not is_label[lbl_idx]:
                return False
            return not cls._cell_labels_foreign_id(cells[lbl_idx], radio_ids.get(r_idx, ""))

        after_hits = sum(1 for i in needs_sib if _can_pair(i, i + 1))
        before_hits = sum(1 for i in needs_sib if _can_pair(i, i - 1))

        direction = "after"
        if before_hits > after_hits:
            direction = "before"
        elif before_hits == after_hits and before_hits > 0 and needs_sib:
            def _dir_score(offset: int) -> tuple[int, int, int, int, int]:
                paired = [i + offset for i in needs_sib if _can_pair(i, i + offset)]
                opt_hits = sum(1 for j in paired if cls._is_tr_option_label(cells[j], cell_txts[j]))
                hdr_hits = sum(1 for j in paired if cls._is_tr_header_cell(cells[j], cell_txts[j]))
                pure_lnk_hits = sum(1 for j in paired if cls._is_pure_link_cell(cells[j], cell_txts[j]))
                any_lnk_hits = sum(1 for j in paired if cells[j].find("a") is not None)
                util_hits = sum(1 for j in paired if cls._TR_UTILITY_CELL_RE.match(cell_txts[j]))
                return (opt_hits, -hdr_hits, -pure_lnk_hits, -any_lnk_hits, -util_hits)

            if _dir_score(-1) > _dir_score(1):
                direction = "before"
            else:
                direction = "after"

        if direction == "before":
            if _can_pair(cell_idx, cell_idx - 1):
                return cell_txts[cell_idx - 1]
            return ""
        if _can_pair(cell_idx, cell_idx + 1):
            return cell_txts[cell_idx + 1]
        return ""

    @classmethod
    def _match_mission_radio(
        cls,
        send_soup: BeautifulSoup,
        form_css: str,
        radios: list,
        mission_kind: str,
    ):
        # Collect BS4 label/row text per radio input inside the selected form without bleeding across siblings
        scoped_form = send_soup.select_one(form_css) if form_css else None
        radio_labels: list[tuple[str, str, str, str, bool]] = []
        if scoped_form is not None:
            for inp in scoped_form.select('input[type="radio"]'):
                rid = str(inp.get("id") or "").strip().lower()
                rval = str(inp.get("value") or "").strip().lower()
                rname = str(inp.get("name") or "").strip().lower()
                inp_disabled = inp.has_attr("disabled")
                lbl_parts: list[str] = []
                if rid:
                    lbl = scoped_form.find("label", attrs={"for": inp.get("id")})
                    if (
                        lbl is not None
                        and len(lbl.select('input[type="radio"]')) <= 1
                        and not cls._has_other_visible_form_control(lbl, inp)
                    ):
                        ltxt = " ".join(lbl.get_text(" ", strip=True).split())
                        if ltxt:
                            lbl_parts.append(ltxt)
                parent_lbl = inp.find_parent("label")
                if (
                    parent_lbl is not None
                    and len(parent_lbl.select('input[type="radio"]')) <= 1
                    and not cls._has_other_visible_form_control(parent_lbl, inp)
                ):
                    ltxt = " ".join(parent_lbl.get_text(" ", strip=True).split())
                    if ltxt:
                        lbl_parts.append(ltxt)
                if not lbl_parts:
                    cell = inp.find_parent(["td", "th"])
                    if cell is not None and scoped_form not in cell.parents:
                        cell = None
                    tr = cell.find_parent("tr") if cell is not None else None
                    if tr is not None and scoped_form not in tr.parents:
                        tr = None
                    if (
                        cell is not None
                        and tr is not None
                        and not cell.find("table")
                        and len(cell.select('input[type="radio"]')) == 1
                        and not cls._has_other_visible_form_control(cell, inp)
                    ):
                        ctxt = " ".join(cell.get_text(" ", strip=True).split())
                        if ctxt:
                            lbl_parts.append(ctxt)
                        elif (
                            len(tr.select('input[type="radio"]')) == 1
                            and not cls._has_other_visible_form_control(tr, inp)
                        ):
                            lbl_parts.append(" ".join(tr.get_text(" ", strip=True).split()))
                        else:
                            tr_txt = cls._extract_multi_radio_tr_cell_label(tr, cell)
                            if tr_txt:
                                lbl_parts.append(tr_txt)
                    else:
                        wrapper = inp
                        while (
                            wrapper.parent is not None
                            and isinstance(wrapper.parent, Tag)
                            and wrapper.parent is not scoped_form
                            and wrapper.parent.name not in cls._WRAPPER_WALK_STOP_TAGS
                            and not cls._has_other_visible_form_control(wrapper.parent, inp)
                            and len(wrapper.parent.select('input[type="radio"]')) == 1
                            and not " ".join(wrapper.parent.get_text(" ", strip=True).split())
                        ):
                            wrapper = wrapper.parent
                        parent = wrapper.parent
                        if (
                            parent is not None
                            and isinstance(parent, Tag)
                            and parent is not scoped_form
                            and parent.name not in cls._NON_LABEL_CONTAINER_TAGS
                            and parent.name not in ("td", "th")
                            and len(parent.select('input[type="radio"]')) == 1
                            and not cls._has_other_visible_form_control(parent, inp)
                        ):
                            ptxt = " ".join(parent.get_text(" ", strip=True).split())
                            if ptxt:
                                lbl_parts.append(ptxt)
                            else:
                                sib_txt = (
                                    cls._collect_adjacent_radio_text(parent, target_id=rid)
                                    or cls._collect_adjacent_radio_text(parent, reverse=True, target_id=rid)
                                )
                                if sib_txt:
                                    lbl_parts.append(sib_txt)
                        else:
                            sib_txt = (
                                cls._collect_adjacent_radio_text(wrapper, target_id=rid)
                                or cls._collect_adjacent_radio_text(wrapper, reverse=True, target_id=rid)
                            )
                            if sib_txt:
                                lbl_parts.append(sib_txt)
                radio_labels.append((rval, rid, rname, " ".join(lbl_parts).lower(), inp_disabled))

        if mission_kind == "ATTACK":
            want_tokens: tuple[str, ...] = ("attack", "angriff", "angreif")
        elif mission_kind == "ESCAPE":
            want_tokens = ("escape", "flucht", "save")
        elif mission_kind == "RECYCLE":
            want_tokens = (
                "recycle",
                "recycl",
                "rubble",
                "debris",
                "trümmer",
                "truemmer",
                "abbau",
                "harvest",
                "sammeln",
                "tf",
            )
        elif mission_kind == "COLONIZE":
            want_tokens = (
                "colonize",
                "colonisation",
                "colonization",
                "kolonisier",
                "colony",
                "siedel",
            )
        else:
            want_tokens = ("spy", "spio", "spionier", "espionage")

        def _is_speed_or_route(r_name: str, lbl: str) -> bool:
            return (
                "procent" in r_name
                or "percent" in r_name
                or "geschwindigkeit" in r_name
                or "speed" in r_name
                or "route" in r_name
                or "%" in lbl
            )

        candidates: list[tuple[object, str, str, str, str]] = []
        for idx, r in enumerate(radios):
            r_val = str(r.get_attribute("value") or "").strip().lower()
            r_id = str(r.get_attribute("id") or "").strip().lower()
            r_name = str(r.get_attribute("name") or "").strip().lower()
            raw_dis = r.get_attribute("disabled")
            is_dis = (isinstance(raw_dis, str) and raw_dis.strip().lower() in ("true", "disabled", "1")) or raw_dis is True
            label_txt = ""
            matched_label = False
            if len(radios) == len(radio_labels) and idx < len(radio_labels):
                b_val, b_id, b_name, b_lbl, b_dis = radio_labels[idx]
                if (
                    (not r_id or not b_id or r_id == b_id)
                    and (not r_name or not b_name or r_name == b_name)
                    and (not r_val or not b_val or r_val == b_val)
                ):
                    label_txt = b_lbl
                    is_dis = is_dis or b_dis
                    matched_label = True
            if not matched_label:
                for b_val, b_id, b_name, b_lbl, b_dis in radio_labels:
                    if r_id and b_id and r_id != b_id:
                        continue
                    if (r_id and r_id == b_id) or (
                        r_val and r_val == b_val and (not r_name or not b_name or r_name == b_name)
                    ):
                        label_txt = b_lbl
                        is_dis = is_dis or b_dis
                        matched_label = True
                        break
            if not matched_label and idx < len(radio_labels):
                label_txt = radio_labels[idx][3]
                is_dis = is_dis or radio_labels[idx][4]
            if is_dis or _is_speed_or_route(r_name, label_txt):
                continue
            candidates.append((r, r_val, r_id, r_name, label_txt))

        # Pass 1: explicit keyword match across ALL non-speed/non-route radios
        for r, r_val, r_id, r_name, label_txt in candidates:
            if any(tok in r_val or tok in r_id or tok in label_txt for tok in want_tokens):
                return r

        # Pass 2: numeric value="1" fallback ONLY for ATTACK when Pass 1 found nothing
        if mission_kind == "ATTACK":
            other_missions = (
                "transport",
                "transportier",
                "station",
                "stationier",
                "spy",
                "spio",
                "spionier",
                "espionage",
                "colonize",
                "kolonisier",
                "recycle",
                "recycl",
                "harvest",
                "sammeln",
                "sammel",
                "abbau",
                "escape",
                "flucht",
                "halt",
                "hold",
                "defend",
                "verteidig",
                "escort",
                "eskort",
                "deploy",
                "expedition",
                "explore",
                "erkund",
            )
            for r, r_val, r_id, r_name, label_txt in candidates:
                if (r_val == "1" or ("mission" in r_name and r_val == "1")) and not any(
                    m in label_txt or m in r_id for m in other_missions
                ):
                    return r
        return None

    _PROTECTION_AND_AZ_KEYWORDS: tuple[str, ...] = (
        "attackzone",
        "attack zone",
        "angriffszone",
        "vacation",
        "urlaub",
        "urlaubsmodus",
        "newbie",
        "noob",
        "bash",
        "can't be attacked",
        "cannot be attacked",
        "can not be attacked",
        "can't be spied",
        "cannot be spied",
        "nicht angegriffen",
        "nicht ausspioniert",
        "protection",
    )

    @classmethod
    def _is_unattackable_target_html(cls, html_or_text: str) -> bool:
        if not html_or_text:
            return False
        low = str(html_or_text).lower()
        return any(kw in low for kw in cls._PROTECTION_AND_AZ_KEYWORDS)

    @classmethod
    def _extract_mission_forbidden_reason(cls, html: str, mission_kind: str = "ATTACK") -> Optional[str]:
        if not html:
            return None
        parsed_blk = parse_send_fleet_mission_block(html, mission_kind)
        if parsed_blk is not None:
            return parsed_blk[0]
        srv_err = cls._extract_fleet_server_error(html)
        if srv_err and cls._is_unattackable_target_html(srv_err):
            return srv_err
        soup = BeautifulSoup(html, "html.parser")
        for raw_ln in soup.get_text("\n", strip=True).splitlines():
            ln = " ".join(raw_ln.split()).strip()
            if not ln:
                continue
            low_ln = ln.lower()
            if ("(forbidden)" in low_ln or "(verboten)" in low_ln) and "transport" not in low_ln:
                return ln[:240]
            if cls._is_unattackable_target_html(low_ln) and len(ln) <= 240:
                return ln
        return srv_err

    @staticmethod
    def _extract_fleet_server_error(confirm_html: str) -> Optional[str]:
        if not confirm_html:
            return None
        m_err = re.search(r"\bfleetErr\.[A-Za-z0-9_]+", confirm_html)
        if m_err:
            return m_err.group(0)
        soup = BeautifulSoup(confirm_html, "html.parser")
        for sel in (
            ".error",
            ".errormessage",
            ".fehler",
            ".alert-danger",
            'font[color="red"]',
            '[style*="color:red"]',
            '[style*="color: red"]',
            '[style*="color:#ff0000"]',
            '[style*="color: #ff0000"]',
        ):
            el = soup.select_one(sel)
            if el is not None:
                txt = " ".join(el.get_text(" ", strip=True).split())
                if txt and len(txt) <= 200:
                    low_el = txt.lower()
                    if sel in (".error", ".errormessage", ".fehler", ".alert-danger") or any(
                        kw in low_el
                        for kw in (
                            "can't",
                            "cannot",
                            "can not",
                            "error",
                            "forbidden",
                            "verboten",
                            "kann nicht",
                            "not enough",
                            "protection",
                            "vacation",
                            "urlaubsmodus",
                            "attackzone",
                            "attack zone",
                            "angriffszone",
                            "bash",
                        )
                    ):
                        return txt[:160]
        low = " ".join(soup.get_text(" ", strip=True).split()).lower()
        for phrase in (
            "this user can't be attacked!",
            "this user can't be attacked",
            "this user cannot be attacked",
            "this player can't be attacked",
            "this player cannot be attacked",
            "can't be attacked",
            "cannot be attacked",
            "can not be attacked",
            "dieser spieler kann nicht angegriffen werden",
            "kann nicht angegriffen werden",
            "not enough spice",
            "not enough deuterium",
            "not enough ships",
            "no free fleet slot",
            "too many fleets",
            "newbie protection",
            "noob protection",
            "vacation mode",
            "urlaubsmodus",
            "out of your attackzone range",
            "out of your attack zone range",
            "outside your attack zone",
            "outside attack zone",
            "attackzone",
            "angriffszone",
            "bash protection",
            "cannot attack",
            "can't attack",
            "cannot spy",
            "can't spy",
        ):
            if phrase in low:
                return phrase
        return None

    def _dispatch_fleet_mission(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
        *,
        ships_to_send: dict[str, int],
        mission_kind: str,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        tname_s = _safe_str(action.target_name)
        reason_s = _safe_str(action.reason)
        coords_s = _safe_str(action.planet_coords)
        m = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", tname_s, re.ASCII)
        if not m:
            bad_tname = None if action.target_name is None else tname_s
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: could not parse target coordinates from {bad_tname!r}.",
            )
        t_gal, t_sys, t_pos = int(m.group(1)), int(m.group(2)), int(m.group(3))
        is_asteroid = bool(re.search(r"\[\s*a\s*\]|\basteroid\b", f"{tname_s} {reason_s}", re.IGNORECASE))

        fleet_html = self._navigate_and_select_planet(
            client,
            state,
            "internalFleet",
            action.planet_id,
            active_planet_confirmed=active_planet_confirmed,
        )
        mismatch_pid = self._confirmed_planet_mismatch(action.planet_id)
        if mismatch_pid is not None:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=(
                    f"Aborted: active planet is {mismatch_pid}, "
                    f"not requested origin {game_data.fmt_compact_int(action.planet_id)}."
                ),
                observed_active_planet_id=mismatch_pid,
                observed_active_planet_confirmed=True,
            )
        fleet_soup = BeautifulSoup(fleet_html or "", "html.parser")
        send_form_soup = fleet_soup.select_one('form[action*="internalSendFleet"]')
        fleet_named_soup = fleet_soup.select_one('form[name="fleet"]')
        step1_form_soup = send_form_soup or fleet_named_soup
        if step1_form_soup is None:
            for cand_form in fleet_soup.find_all("form"):
                if all(cand_form.find(attrs={"name": fn}) is not None for fn in ("gal", "sun", "pla")):
                    step1_form_soup = cand_form
                    break

        step1_form_css = ""
        if send_form_soup is not None:
            step1_form_css = 'form[action*="internalSendFleet"]'
        elif fleet_named_soup is not None:
            step1_form_css = 'form[name="fleet"]'
        elif step1_form_soup is not None:
            fid = str(step1_form_soup.get("id") or "").strip()
            faction = str(step1_form_soup.get("action") or "").strip()
            if fid and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", fid):
                step1_form_css = f"form#{fid}"
            elif faction:
                esc_action = faction.replace('"', '\\"')
                step1_form_css = f'form[action="{esc_action}"]'

        scope_soup = step1_form_soup if step1_form_soup is not None else fleet_soup
        soup_input_names = {
            str(inp.get("name") or "")
            for inp in scope_soup.find_all("input")
            if inp.get("name")
        }
        step1_submit_css = (
            f'{step1_form_css} input[type="submit"], {step1_form_css} button[type="submit"]'
            if step1_form_css
            else 'form[name="fleet"] input[type="submit"], form[name="fleet"] button[type="submit"]'
        )

        def _field_needs_scoping(fname: str) -> bool:
            if not step1_form_css or step1_form_soup is None:
                return False
            all_matches = fleet_soup.find_all(attrs={"name": fname})
            if len(all_matches) > 1:
                return True
            if all_matches and len(step1_form_soup.find_all(attrs={"name": fname})) != len(all_matches):
                return True
            return False

        def _scoped_field_css(fname: str) -> str:
            esc_fn = fname.replace('"', '\\"')
            inp_css = f'{step1_form_css} input[name="{esc_fn}"]'
            return inp_css if fleet_soup.select_one(inp_css) is not None else f'{step1_form_css} [name="{esc_fn}"]'

        client._switch_frame("F_Hauptframe")
        nav_token1: Optional[str] = None
        paced1 = False
        for attempt in range(2):
            try:
                filled_any = False
                for skey, qty in ships_to_send.items():
                    num = skey[1:]
                    candidates = (f"ship_{num}", f"ship[{num}]", f"s{num}", f"ship{num}")
                    matched_in_html = next((c for c in candidates if c in soup_input_names), None)
                    if matched_in_html is not None:
                        if _field_needs_scoping(matched_in_html):
                            els = client.driver.find_elements(By.CSS_SELECTOR, _scoped_field_css(matched_in_html))
                        else:
                            els = client.driver.find_elements(By.NAME, matched_in_html)
                    else:
                        els = []
                        for cname in candidates:
                            els = client.driver.find_elements(By.NAME, cname)
                            if els:
                                break
                    if els:
                        qty_safe = max(1, game_data.cap_json_int(game_data.safe_int(qty, 1)))
                        self._set_text_input_value(client, els[0], str(qty_safe))
                        filled_any = True
                if not filled_any:
                    return ExecutionResult(
                        executed=False,
                        dry_run=False,
                        action=action,
                        message="Aborted: no ship input fields found on internalFleet.",
                    )
                for fname, fval in (("gal", str(t_gal)), ("sun", str(t_sys)), ("pla", str(t_pos))):
                    if _field_needs_scoping(fname):
                        coord_inp = client.driver.find_element(By.CSS_SELECTOR, _scoped_field_css(fname))
                    else:
                        coord_inp = client.driver.find_element(By.NAME, fname)
                    self._set_text_input_value(client, coord_inp, fval)
                if is_asteroid and "astro" in soup_input_names:
                    if _field_needs_scoping("astro"):
                        astro_cb = client.driver.find_element(By.CSS_SELECTOR, _scoped_field_css("astro"))
                    else:
                        astro_cb = client.driver.find_element(By.NAME, "astro")
                    if not astro_cb.is_selected():
                        self._safe_click(client, astro_cb)
                if mission_kind == "RECYCLE":
                    for rb_name in ("rubblefield", "rubble", "debris", "tf"):
                        if rb_name in soup_input_names:
                            if _field_needs_scoping(rb_name):
                                rb_cb = client.driver.find_element(By.CSS_SELECTOR, _scoped_field_css(rb_name))
                            else:
                                rb_cb = client.driver.find_element(By.NAME, rb_name)
                            if str(rb_cb.get_attribute("type") or "").strip().lower() == "checkbox" and not rb_cb.is_selected():
                                self._safe_click(client, rb_cb)
                            break

                cont_btn = client.driver.find_element(By.CSS_SELECTOR, step1_submit_css)
                if not paced1:
                    self._pace_action(client, priority="interactive")
                    nav_token1 = self._mark_frame_nav(client)
                    time.sleep(random.uniform(*self.action_delay_range))
                    paced1 = True
                self._safe_click(client, cont_btn)
                break
            except StaleElementReferenceException:
                if attempt == 1:
                    return ExecutionResult(
                        executed=False,
                        dry_run=False,
                        action=action,
                        message="Aborted: internalFleet form controls became stale.",
                    )
                client._switch_frame("F_Hauptframe")
            except NoSuchElementException:
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message="Aborted: internalFleet target or submit controls not found.",
                )
            except (ElementNotInteractableException, WebDriverException) as exc:
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: internalFleet form controls not interactable ({type(exc).__name__}).",
                )

        send_html = client._wait_frame_ready(
            "F_Hauptframe",
            expected_action="internalSendFleet",
            nav_token=nav_token1,
        )
        send_soup = BeautifulSoup(send_html or "", "html.parser")
        step1_block = parse_send_fleet_mission_block(send_html, mission_kind)
        has_any_step2_radio = send_soup.select_one('input[type="radio"]') is not None
        if step1_block is not None and (
            not has_any_step2_radio
            or "(forbidden)" in step1_block[0].lower()
            or "(verboten)" in step1_block[0].lower()
            or mission_kind == "RECYCLE"
        ):
            blk_reason, blk_cd, blk_bash = step1_block
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: {blk_reason}.",
                target_cooldown_seconds=blk_cd,
                target_block_reason=blk_reason,
                target_is_bash_block=blk_bash,
                clear_target_debris=(mission_kind == "RECYCLE"),
                validated=False,
                validation_error=blk_reason,
            )
        step1_err = self._extract_fleet_server_error(send_html)
        if step1_err and (
            send_soup.select_one('input[type="radio"]') is None
            or self._is_unattackable_target_html(step1_err)
        ):
            is_prot_err = self._is_unattackable_target_html(step1_err)
            append_crash_debug_log(
                component="executor._dispatch_fleet_mission",
                event="step1_rejected",
                details={
                    "mission_kind": mission_kind,
                    "target": tname_s,
                    "ships": ships_to_send,
                    "server_err": step1_err,
                    "page_text": " ".join(send_soup.get_text(" ", strip=True).split())[:800],
                },
            )
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: fleet step 1 rejected by server ({step1_err}).",
                target_cooldown_seconds=21600.0 if is_prot_err else None,
                target_block_reason=step1_err if is_prot_err else None,
                target_is_bash_block=("bash" in step1_err.lower()),
                validated=False,
                validation_error=step1_err,
            )
        form_css = self._pick_send_fleet_form_css(send_soup)

        client._switch_frame("F_Hauptframe")
        nav_token2: Optional[str] = None
        launch_css = (
            f'{form_css} input[type="submit"], '
            f'{form_css} button[type="submit"], '
            f'{form_css} input#goButton, '
            f'{form_css} input.fleet-start-button, '
            f'{form_css} input[onclick*="startFleet"]'
        )
        paced2 = False
        for attempt in range(2):
            try:
                radios = client.driver.find_elements(By.CSS_SELECTOR, f'{form_css} input[type="radio"]')
                mission_radio = self._match_mission_radio(send_soup, form_css, radios, mission_kind)
                if mission_radio is None:
                    mission_block = parse_send_fleet_mission_block(send_html, mission_kind)
                    if mission_block is not None:
                        blk_reason, blk_cd, blk_bash = mission_block
                        return ExecutionResult(
                            executed=False,
                            dry_run=False,
                            action=action,
                            message=f"Aborted: {blk_reason}.",
                            target_cooldown_seconds=blk_cd,
                            target_block_reason=blk_reason,
                            target_is_bash_block=blk_bash,
                            clear_target_debris=(mission_kind == "RECYCLE"),
                            validated=False,
                            validation_error=blk_reason,
                        )
                    has_step2_controls = bool(
                        radios
                        or send_soup.select_one(
                            'form#fleet, form[action*="internalSendFleetFinish"], input[name="todo"], input[type="radio"]'
                        )
                        is not None
                        or self._is_unattackable_target_html(send_html)
                    )
                    append_crash_debug_log(
                        component="executor._dispatch_fleet_mission",
                        event="mission_radio_missing",
                        details={
                            "mission_kind": mission_kind,
                            "target": tname_s,
                            "form_css": form_css,
                            "radios_html": [redact_sensitive(str(r))[:300] for r in send_soup.select('input[type="radio"]')],
                            "forms_html": [redact_sensitive(str(f))[:1200] for f in send_soup.find_all("form")],
                            "page_text": " ".join(send_soup.get_text(" ", strip=True).split())[:1000],
                        },
                    )
                    if mission_kind in ("ATTACK", "SPY") and has_step2_controls:
                        inline_forb = self._extract_mission_forbidden_reason(send_html, mission_kind)
                        avail_vals: list[str] = []
                        for r_el in send_soup.select('input[type="radio"]'):
                            r_nm = str(r_el.get("name") or "").strip().lower()
                            r_vl = str(r_el.get("value") or "").strip()
                            if r_vl and not any(tok in r_nm for tok in ("procent", "percent", "speed", "route", "geschwindigkeit")):
                                if r_vl not in avail_vals:
                                    avail_vals.append(r_vl)
                        avail_hint = (
                            f"available radios: {', '.join(avail_vals[:6])}"
                            if avail_vals
                            else "no mission radios rendered"
                        )
                        uninhab_hint = (
                            " — target slot appears uninhabited/despawned"
                            if any(v.upper() in ("KOLO", "COLONIZE", "KOLONISIEREN") for v in avail_vals)
                            or (avail_vals and all(v.upper() == "SAVE" for v in avail_vals))
                            else ""
                        )
                        verb_unavail = "attacked" if mission_kind == "ATTACK" else "spied"
                        blk_msg = inline_forb or (
                            f"Target cannot be {verb_unavail} on internalSendFleet "
                            f"({mission_kind} mission unavailable; {avail_hint}{uninhab_hint})"
                        )
                        out_msg = (
                            f"Aborted: {inline_forb}."
                            if inline_forb
                            else f"Aborted: {blk_msg}."
                        )
                        return ExecutionResult(
                            executed=False,
                            dry_run=False,
                            action=action,
                            message=out_msg,
                            target_cooldown_seconds=6 * 3600.0,
                            target_block_reason=blk_msg,
                            target_is_bash_block=("bash" in blk_msg.lower()),
                            validated=False,
                            validation_error=blk_msg,
                        )
                    return ExecutionResult(
                        executed=False,
                        dry_run=False,
                        action=action,
                        message=(
                            f"Aborted: {mission_kind.title()} mission radio button not confirmed on internalSendFleet "
                            f"(page bytes={len(send_html)})."
                        ),
                    )
                if not mission_radio.is_selected():
                    self._safe_click(client, mission_radio)
                launch_btn = client.driver.find_element(By.CSS_SELECTOR, launch_css)
                if not paced2:
                    self._pace_action(client, priority="interactive")
                    nav_token2 = self._mark_frame_nav(client)
                    time.sleep(random.uniform(*self.action_delay_range))
                    paced2 = True
                self._safe_click(client, launch_btn)
                break
            except StaleElementReferenceException:
                if attempt == 1:
                    return ExecutionResult(
                        executed=False,
                        dry_run=False,
                        action=action,
                        message=f"Aborted: {mission_kind.title()} mission controls became stale on internalSendFleet.",
                    )
                client._switch_frame("F_Hauptframe")
                refreshed_html = str(getattr(client.driver, "page_source", "") or "")
                if refreshed_html.strip():
                    send_html = refreshed_html
                    send_soup = BeautifulSoup(send_html, "html.parser")
                    form_css = self._pick_send_fleet_form_css(send_soup)
                    launch_css = (
                        f'{form_css} input[type="submit"], '
                        f'{form_css} button[type="submit"], '
                        f'{form_css} input#goButton, '
                        f'{form_css} input.fleet-start-button, '
                        f'{form_css} input[onclick*="startFleet"]'
                    )
            except NoSuchElementException:
                post_click_html = str(getattr(client.driver, "page_source", "") or send_html or "")
                post_soup = BeautifulSoup(post_click_html, "html.parser")
                append_crash_debug_log(
                    component="executor._dispatch_fleet_mission",
                    event="launch_button_missing",
                    details={
                        "mission_kind": mission_kind,
                        "target": tname_s,
                        "selected_radio_val": str(mission_radio.get_attribute("value") or "") if mission_radio else None,
                        "selected_radio_id": str(mission_radio.get_attribute("id") or "") if mission_radio else None,
                        "selected_radio_name": str(mission_radio.get_attribute("name") or "") if mission_radio else None,
                        "form_css": form_css,
                        "forms_tail": [redact_sensitive(str(f))[-3500:] for f in post_soup.find_all("form")],
                        "page_text": " ".join(post_soup.get_text(" ", strip=True).split())[:1500],
                    },
                )
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: {mission_kind.title()} mission launch button not found on internalSendFleet.",
                )
            except (ElementNotInteractableException, WebDriverException) as exc:
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: {mission_kind.title()} mission controls not interactable on internalSendFleet ({type(exc).__name__}).",
                )

        confirm_html = client._wait_frame_ready("F_Hauptframe", nav_token=nav_token2)
        confirm_block = parse_send_fleet_mission_block(confirm_html, mission_kind)
        if confirm_block is not None:
            blk_reason, blk_cd, blk_bash = confirm_block
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: {blk_reason}.",
                target_cooldown_seconds=blk_cd,
                target_block_reason=blk_reason,
                target_is_bash_block=blk_bash,
                clear_target_debris=(mission_kind == "RECYCLE"),
                validated=False,
                validation_error=blk_reason,
            )
        server_err = self._extract_fleet_server_error(confirm_html)
        if server_err:
            is_prot_err2 = self._is_unattackable_target_html(server_err)
            append_crash_debug_log(
                component="executor._dispatch_fleet_mission",
                event="step2_rejected",
                details={
                    "mission_kind": mission_kind,
                    "target": tname_s,
                    "ships": ships_to_send,
                    "server_err": server_err,
                },
            )
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: fleet launch rejected by server ({server_err}).",
                target_cooldown_seconds=21600.0 if is_prot_err2 else None,
                target_block_reason=server_err if is_prot_err2 else None,
                target_is_bash_block=("bash" in server_err.lower()),
                validated=False,
                validation_error=server_err,
            )
        confirm_soup = BeautifulSoup(confirm_html or "", "html.parser")
        if confirm_soup.select_one('form[action*="internalSendFleetFinish"] input[type="radio"][name="todo"]') is not None:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: {mission_kind.title()} fleet remained on internalSendFleet after clicking LAUNCH.",
                validated=False,
                validation_error="Fleet remained on internalSendFleet after clicking LAUNCH.",
            )

        self._recent_timestamps.append(time.monotonic())
        updated = _call_with_optional_priority(
            client.read_planets_overview,
            refresh=True,
            priority="interactive",
        )
        updated_state = self._build_updated_state(
            client,
            state,
            action,
            updated,
            is_fleet=True,
        )
        ship_desc = ", ".join(
            f"{game_data.fmt_compact_int(qty)}x {k}" for k, qty in ships_to_send.items()
        )
        if mission_kind == "SPY":
            verb = "spy probe"
        elif mission_kind == "ESCAPE":
            verb = "escape flight"
        elif mission_kind == "RECYCLE":
            verb = "recycler harvest"
        elif mission_kind == "COLONIZE":
            verb = "colonization fleet"
        else:
            verb = "attack"
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=action,
            message=(
                f"Dispatched {verb} ({ship_desc}) to [{t_gal:02d}:{t_sys:03d}:{t_pos:02d}] "
                f"from [{coords_s}]."
            ),
            updated_state=updated_state,
            clear_target_debris=(mission_kind == "RECYCLE"),
        )

    def _execute_escape_flight(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        planet = next((p for p in state.planets if p.planet_id == action.planet_id), None) or state.active_planet()
        preferred_order = (
            "s1",
            "s10",
            "s2",
            "s4",
            "s5",
            "s11",
            "s3",
            "s6",
            "s7",
            "s8",
            "s12",
            "s13",
            "s14",
            "s15",
            "s16",
        )
        stationed: dict[str, int] = {}
        if planet is not None:
            for k in preferred_order:
                qty = game_data.cap_json_int(game_data.safe_int((planet.ships or {}).get(k, 0), 0))
                if qty > 0:
                    stationed[k] = qty
            for raw_k, raw_v in (planet.ships or {}).items():
                sk = _safe_str(raw_k).strip()
                if sk == "s9" or sk in stationed:
                    continue
                if sk.startswith("s") and sk[1:].isascii() and sk[1:].isdigit():
                    qty = game_data.cap_json_int(game_data.safe_int(raw_v, 0))
                    if qty > 0:
                        stationed[sk] = qty
        if not stationed:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: no stationed mobile ships on [{_safe_str(action.planet_coords)}].",
            )
        is_tutorial_escape = "tutorial" in _safe_str(action.reason).lower()
        if is_tutorial_escape:
            first_key = next(iter(stationed))
            ships_to_send = {first_key: 1}
        else:
            ships_to_send = dict(stationed)
        return self._dispatch_fleet_mission(
            client,
            state,
            action,
            ships_to_send=ships_to_send,
            mission_kind="ESCAPE",
            active_planet_confirmed=active_planet_confirmed,
        )

    def _execute_attack(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        planet = next((p for p in state.planets if p.planet_id == action.planet_id), None) or state.active_planet()
        attack_keys = ("s4", "s5", "s6", "s7", "s13", "s14", "s15", "s16", "s1", "s2", "s3")
        ships_to_send = (
            {
                k: qty
                for k in attack_keys
                if (qty := game_data.cap_json_int(game_data.safe_int(planet.ships.get(k, 0), 0))) > 0
            }
            if planet is not None
            else {}
        )
        if not ships_to_send:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: no stationed combat or transport ships on [{_safe_str(action.planet_coords)}].",
            )
        return self._dispatch_fleet_mission(
            client,
            state,
            action,
            ships_to_send=ships_to_send,
            mission_kind="ATTACK",
            active_planet_confirmed=active_planet_confirmed,
        )

    def _execute_spy(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        planet = next((p for p in state.planets if p.planet_id == action.planet_id), None) or state.active_planet()
        avail = game_data.cap_json_int(game_data.safe_int(planet.ships.get("s10", 0), 0)) if planet is not None else 0
        if avail <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: no spy probes (s10) stationed on [{_safe_str(action.planet_coords)}].",
            )
        probe_count = min(avail, max(1, game_data.cap_json_int(game_data.safe_int(action.target_level, 1))))
        tname_s = _safe_str(action.target_name)
        reason_s = _safe_str(action.reason)
        is_free_slot_probe = (
            "(free slot)" in tname_s.lower()
            or "probe free slot" in reason_s.lower()
        )
        m_coords = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", tname_s, re.ASCII)
        dispatch_fn = getattr(client, "dispatch_galaxy_spy", None)
        if (
            is_free_slot_probe
            and m_coords is not None
            and callable(dispatch_fn)
            and getattr(dispatch_fn, "__module__", "").startswith("spaceinvasion")
        ):
            t_gal, t_sys, t_pos = int(m_coords.group(1)), int(m_coords.group(2)), int(m_coords.group(3))
            if len(state.planets) > 1 or not active_planet_confirmed or state.active_planet_id != action.planet_id:
                self._navigate_and_select_planet(
                    client,
                    state,
                    "internalHome",
                    action.planet_id,
                    active_planet_confirmed=active_planet_confirmed,
                )
                mismatch_pid = self._confirmed_planet_mismatch(action.planet_id)
                if mismatch_pid is not None:
                    return ExecutionResult(
                        executed=False,
                        dry_run=False,
                        action=action,
                        message=(
                            f"Aborted: active planet is {mismatch_pid}, "
                            f"not requested origin {game_data.fmt_compact_int(action.planet_id)}."
                        ),
                        observed_active_planet_id=mismatch_pid,
                        observed_active_planet_confirmed=True,
                    )
            is_ast = bool(re.search(r"\[\s*a\s*\]|\basteroid\b", f"{tname_s} {reason_s}", re.IGNORECASE))
            qspy = dispatch_fn(
                galaxy=t_gal,
                system=t_sys,
                position=t_pos,
                quantity=probe_count,
                is_asteroid=is_ast,
                priority="interactive",
            )
            if isinstance(qspy, Mapping):
                q_html = str(qspy.get("html") or "")
                parsed_q_slots: list[GalaxySlot] = []
                if q_html:
                    try:
                        parsed_q_slots = list(
                            parse_galaxy_system_html(
                                q_html,
                                galaxy=int(qspy.get("galaxy") or t_gal),
                                system=int(qspy.get("system") or t_sys),
                            )
                        )
                        self.last_galaxy_slots = parsed_q_slots
                    except Exception:
                        pass
                if qspy.get("started"):
                    self._recent_timestamps.append(time.monotonic())
                    updated = _call_with_optional_priority(
                        client.read_planets_overview,
                        refresh=True,
                        priority="interactive",
                    )
                    updated_state = self._build_updated_state(
                        client,
                        state,
                        action,
                        updated,
                        is_fleet=True,
                    )
                    return ExecutionResult(
                        executed=True,
                        dry_run=False,
                        action=action,
                        message=(
                            f"Dispatched spy probe ({probe_count}x s10) via 2D Galaxy to "
                            f"[{t_gal:02d}:{t_sys:03d}:{t_pos:02d}] from [{_safe_str(action.planet_coords)}]."
                        ),
                        updated_state=updated_state,
                    )
                matched_free_slot = next(
                    (
                        sl
                        for sl in parsed_q_slots
                        if sl.position == t_pos
                        and sl.status_color == "free"
                        and (sl.uninhabited_fields is not None or sl.colonize_max_fields is not None)
                    ),
                    None,
                )
                if matched_free_slot is not None:
                    self._recent_timestamps.append(time.monotonic())
                    if matched_free_slot.uninhabited_fields is not None:
                        eco_desc = f"{matched_free_slot.uninhabited_fields} fields"
                    else:
                        c_min_f = matched_free_slot.colonize_min_fields or matched_free_slot.colonize_max_fields
                        eco_desc = f"{c_min_f}–{matched_free_slot.colonize_max_fields} fields"
                    if matched_free_slot.temperature is not None:
                        eco_desc += f" ({matched_free_slot.temperature}°C)"
                    free_cnt = sum(
                        1
                        for sl in parsed_q_slots
                        if sl.status_color == "free"
                        and (sl.uninhabited_fields is not None or sl.colonize_max_fields is not None)
                    )
                    return ExecutionResult(
                        executed=True,
                        dry_run=False,
                        action=action,
                        message=(
                            f"Probed free slot [{t_gal:02d}:{t_sys:03d}:{t_pos:02d}] ecology via 2D Galaxy: "
                            f"{eco_desc} ({free_cnt} free slot(s) graded in [{t_gal:02d}:{t_sys:03d}])."
                        ),
                        validated=True,
                    )
                q_err = str(qspy.get("error") or "").strip()
                if q_err and self._is_unattackable_target_html(q_err):
                    return ExecutionResult(
                        executed=False,
                        dry_run=False,
                        action=action,
                        message=f"Aborted: {q_err}.",
                        target_cooldown_seconds=21600.0,
                        target_block_reason=q_err,
                        validated=False,
                        validation_error=q_err,
                    )
        return self._dispatch_fleet_mission(
            client,
            state,
            action,
            ships_to_send={"s10": probe_count},
            mission_kind="SPY",
            active_planet_confirmed=active_planet_confirmed,
        )

    def _execute_recycle(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        planet = next((p for p in state.planets if p.planet_id == action.planet_id), None) or state.active_planet()
        avail = game_data.cap_json_int(game_data.safe_int(planet.ships.get("s11", 0), 0)) if planet is not None else 0
        if avail <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: no recyclers (s11) stationed on [{_safe_str(action.planet_coords)}].",
            )
        req_qty = game_data.safe_optional_int(action.target_level, None)
        rec_count = min(avail, max(1, game_data.cap_json_int(req_qty))) if req_qty is not None else avail
        return self._dispatch_fleet_mission(
            client,
            state,
            action,
            ships_to_send={"s11": rec_count},
            mission_kind="RECYCLE",
            active_planet_confirmed=active_planet_confirmed,
        )

    def _execute_colonize(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        planet = next((p for p in state.planets if p.planet_id == action.planet_id), None) or state.active_planet()
        avail = game_data.cap_json_int(game_data.safe_int(planet.ships.get("s8", 0), 0)) if planet is not None else 0
        if avail <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: no colony ship (s8) stationed on [{_safe_str(action.planet_coords)}].",
            )
        return self._dispatch_fleet_mission(
            client,
            state,
            action,
            ships_to_send={"s8": 1},
            mission_kind="COLONIZE",
            active_planet_confirmed=active_planet_confirmed,
        )

    def _execute_activate_transmitter(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        self.last_transmitter_info = None
        _call_with_optional_priority(client.ensure_in_game, priority="interactive")
        html = _call_with_optional_priority(
            client.navigate_menu,
            "internalTransmitter",
            priority="interactive",
        )
        info = parse_transmitter_page(html)
        if info.get("needs_src_switch_to_free") and info.get("free_src_href"):
            build_url_fn = getattr(client, "_build_internal_url", None)
            raw_href = _safe_str(info["free_src_href"]).strip()
            target_url = (
                build_url_fn(raw_href.split("?", 1)[1])
                if callable(build_url_fn) and "?" in raw_href
                else raw_href
            )
            self._pace_action(client, priority="interactive")
            nav_tok_sw = self._mark_frame_nav(client, "F_Hauptframe")
            switch_fn = getattr(client, "_switch_frame", None)
            if callable(switch_fn):
                switch_fn("F_Hauptframe")
            client.driver.execute_script("location.href = arguments[0];", target_url)
            wait_fn = getattr(client, "_wait_frame_ready", None)
            if callable(wait_fn):
                html = wait_fn(
                    "F_Hauptframe",
                    expected_action="internalTransmitter",
                    nav_token=nav_tok_sw,
                )
            else:
                html = str(getattr(client.driver, "page_source", "") or html)
            info = parse_transmitter_page(html)

        self.last_transmitter_info = {**info, "activated": False}
        free_before = int(info.get("free_spins") or 0)
        if free_before <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Skipped: 0 free Interdimensional Transmitter charges available today (Urplasma spin blocked).",
                validated=True,
            )

        if not info.get("can_activate_free") or info.get("has_urplasma_cost"):
            err_msg = (
                "Interdimensional Transmitter spin is not free "
                f"(idt_src={info.get('idt_src')}, button={info.get('button_label')!r}); blocked to protect Urplasma."
            )
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: {err_msg}",
                validated=False,
                validation_error=err_msg,
            )

        switch_fn = getattr(client, "_switch_frame", None)
        if callable(switch_fn):
            switch_fn("F_Hauptframe")

        dom_check = None
        with contextlib.suppress(Exception):
            dom_check = client.driver.execute_script(
                "var form = document.getElementById('tm.form');"
                "var btn = document.getElementById('tm.submit');"
                "if (!form || !btn) return {ok: false, reason: 'missing_form_or_btn'};"
                "var src = form.querySelector('input[name=\"idtSrc\"]');"
                "var play = document.getElementById('tm.play') || form.querySelector('input[name=\"play\"]');"
                "var hasUp = Boolean(btn.querySelector('.urplasma-icon-30, .urplasma-base, [class*=\"urplasma\"]'));"
                "var txt = (btn.innerText || btn.textContent || '').toLowerCase();"
                "if (src && String(src.value) !== '1') return {ok: false, reason: 'idtSrc=' + src.value};"
                "if (play && String(play.value) !== '1') play.value = '1';"
                "if (hasUp || txt.indexOf('urplasma') !== -1 || txt.indexOf('activate for') !== -1) {"
                "  return {ok: false, reason: 'urplasma_button'};"
                "}"
                "return {ok: true};"
            )
        if isinstance(dom_check, dict) and not dom_check.get("ok", True):
            err_dom = f"Live DOM guard blocked Interdimensional Transmitter ({dom_check.get('reason')})."
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: {err_dom}",
                validated=False,
                validation_error=err_dom,
            )

        self._pace_action(client, priority="interactive")
        nav_token = self._mark_frame_nav(client, "F_Hauptframe")
        clicked = False
        with contextlib.suppress(Exception):
            btn_el = client.driver.find_element(By.ID, "tm.submit")
            self._safe_click(client, btn_el)
            clicked = True
        if not clicked:
            with contextlib.suppress(Exception):
                submitted_js = client.driver.execute_script(
                    "var f = document.getElementById('tm.form');"
                    "if (!f) return false;"
                    "var src = f.querySelector('input[name=\"idtSrc\"]');"
                    "if (src && String(src.value) !== '1') return false;"
                    "var p = document.getElementById('tm.play') || f.querySelector('input[name=\"play\"]');"
                    "if (p) p.value = '1';"
                    "f.submit(); return true;"
                )
                clicked = bool(submitted_js)
        if not clicked:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Aborted: could not find free #tm.submit control on internalTransmitter.",
                validated=False,
                validation_error="Missing #tm.submit button on internalTransmitter.",
            )

        wait_fn = getattr(client, "_wait_frame_ready", None)
        if callable(wait_fn):
            after_html = wait_fn(
                "F_Hauptframe",
                expected_action="internalTransmitter",
                nav_token=nav_token,
            )
        else:
            after_html = str(getattr(client.driver, "page_source", "") or "")
        after_info = parse_transmitter_page(after_html)
        free_after = int(after_info.get("free_spins") or 0)
        reward_txt = _safe_str(after_info.get("latest_reward") or "").strip()
        prev_reward = _safe_str(info.get("latest_reward") or "").strip()
        prev_date = _safe_str(info.get("latest_date") or "").strip()
        after_date = _safe_str(after_info.get("latest_date") or "").strip()

        spin_verified = (
            not after_html
            or free_after < free_before
            or (bool(reward_txt) and (reward_txt != prev_reward or after_date != prev_date))
        )
        if not spin_verified:
            self.last_transmitter_info = {**after_info, "activated": False}
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=(
                    f"Aborted: Interdimensional Transmitter free charges did not decrease "
                    f"(before={free_before}, after={free_after})."
                ),
                validated=False,
                validation_error=f"Free spin count unchanged ({free_before} -> {free_after}).",
            )

        self.last_transmitter_info = {
            **after_info,
            "activated": True,
            "before_free_spins": free_before,
        }
        self._recent_timestamps.append(time.monotonic())
        updated_state: Optional[GameState] = None
        if hasattr(client, "read_planets_overview"):
            with contextlib.suppress(Exception):
                updated_planets = _call_with_optional_priority(
                    client.read_planets_overview,
                    refresh=True,
                    priority="interactive",
                )
                updated_state = self._build_updated_state(
                    client,
                    state,
                    action,
                    updated_planets,
                )
        reward_note = f" — Won: {reward_txt}" if reward_txt else ""
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=action,
            message=(
                f"Activated free daily Interdimensional Transmitter spin "
                f"(charges {free_before} -> {free_after}){reward_note}."
            ),
            updated_state=updated_state,
            validated=True,
        )

    def execute_action(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        """Execute one approved action (`BUILD`, `DEMOLISH_BUILDING`, `CLAIM_TUTORIAL`, `RESEARCH`, `BUILD_SHIP`, `THROTTLE_BUILDING`, `SCAN_GALAXY`, `SPY`, `RECYCLE`, `COLONIZE`, `ATTACK`)."""
        self._reset_observed_planet_tracking()
        if not action.executable_now:
            return ExecutionResult(
                executed=False,
                dry_run=self.dry_run,
                action=action,
                message="Skipped: action is not currently executable.",
            )

        supported = (
            "BUILD",
            "DEMOLISH_BUILDING",
            "ABANDON_COLONY",
            "CLAIM_TUTORIAL",
            "ACTIVATE_TRANSMITTER",
            "RESEARCH",
            "BUILD_SHIP",
            "THROTTLE_BUILDING",
            "ESCAPE_FLIGHT",
            "SCAN_GALAXY",
            "SPY",
            "RECYCLE",
            "COLONIZE",
            "ATTACK",
        )
        if action.kind not in supported:
            return ExecutionResult(
                executed=False,
                dry_run=self.dry_run,
                action=action,
                message=f"Skipped: unsupported live action kind {action.kind}.",
            )
        if (
            action.kind in ("BUILD", "DEMOLISH_BUILDING", "RESEARCH", "BUILD_SHIP", "THROTTLE_BUILDING")
            and action.target_id is None
        ):
            return ExecutionResult(
                executed=False,
                dry_run=self.dry_run,
                action=action,
                message=f"Skipped: {action.kind} action missing target_id.",
            )

        if self.dry_run:
            suffix = ""
            tgt_lvl_opt = game_data.safe_optional_int(action.target_level, None)
            if action.kind == "THROTTLE_BUILDING" and tgt_lvl_opt is not None:
                suffix = f" -> {game_data.fmt_compact_int(tgt_lvl_opt)}%"
            elif action.kind == "BUILD_SHIP" and tgt_lvl_opt:
                suffix = f" x{game_data.fmt_compact_int(tgt_lvl_opt)}"
            elif action.kind == "SPY":
                suffix = f" ({max(1, game_data.cap_json_int(game_data.safe_int(action.target_level, 1)))}x s10)"
            elif action.kind == "RECYCLE":
                suffix = f" ({max(1, game_data.cap_json_int(game_data.safe_int(action.target_level, 1)))}x s11)"
            elif action.kind == "COLONIZE":
                suffix = " (1x s8)"
            elif action.kind == "ABANDON_COLONY":
                min_f_dry = max(50, game_data.safe_int(action.target_level, 300))
                suffix = f" (< {game_data.fmt_compact_int(min_f_dry)} fields)"
            elif tgt_lvl_opt is not None and action.kind == "DEMOLISH_BUILDING":
                suffix = f" -> L{game_data.fmt_compact_int(tgt_lvl_opt)}"
            elif tgt_lvl_opt:
                suffix = f" -> L{game_data.fmt_compact_int(tgt_lvl_opt)}"
            return ExecutionResult(
                executed=False,
                dry_run=True,
                action=action,
                message=f"[DRY-RUN] Would execute {action.kind}: {_safe_str(action.target_name)}{suffix} on [{_safe_str(action.planet_coords)}].",
            )

        if action.kind == "ESCAPE_FLIGHT" and not self.allow_fleetsave:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Guarded: live ESCAPE_FLIGHT requires allow_fleetsave=true in config.",
            )

        if action.kind == "COLONIZE" and not self.allow_colonize:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Guarded: live COLONIZE requires allow_colonize=true in config.",
            )

        if action.kind == "ABANDON_COLONY" and not self.allow_colonize:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Guarded: live ABANDON_COLONY requires allow_colonize=true in config.",
            )

        if action.kind == "ATTACK" and not self.allow_raids:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Guarded: live ATTACK requires allow_raids=true in config.",
            )

        if action.kind == "ACTIVATE_TRANSMITTER" and not self.allow_transmitter:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Guarded: live ACTIVATE_TRANSMITTER requires allow_transmitter=true in config.",
            )

        if self.enforce_hourly_cap and action.kind != "SCAN_GALAXY" and self.remaining_hourly_budget() <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Paused: hourly action rate cap reached.",
            )

        if action.kind == "CLAIM_TUTORIAL":
            res = self._execute_claim_tutorial(client, action, state=state)
        elif action.kind == "ACTIVATE_TRANSMITTER":
            res = self._execute_activate_transmitter(client, state, action)
        elif action.kind == "BUILD":
            res = self._execute_build(client, state, action)
        elif action.kind == "DEMOLISH_BUILDING":
            res = self._execute_demolish_building(client, state, action)
        elif action.kind == "ABANDON_COLONY":
            res = self._execute_abandon_colony(client, state, action)
        elif action.kind == "RESEARCH":
            res = self._execute_research(client, state, action)
        elif action.kind == "BUILD_SHIP":
            res = self._execute_build_ship(client, state, action)
        elif action.kind == "THROTTLE_BUILDING":
            res = self._execute_throttle_building(client, state, action)
        elif action.kind == "SCAN_GALAXY":
            res = self._execute_scan_galaxy(client, state, action)
        elif action.kind == "SPY":
            res = self._execute_spy(
                client,
                state,
                action,
                active_planet_confirmed=active_planet_confirmed,
            )
        elif action.kind == "RECYCLE":
            res = self._execute_recycle(
                client,
                state,
                action,
                active_planet_confirmed=active_planet_confirmed,
            )
        elif action.kind == "COLONIZE":
            res = self._execute_colonize(
                client,
                state,
                action,
                active_planet_confirmed=active_planet_confirmed,
            )
        elif action.kind == "ATTACK":
            res = self._execute_attack(
                client,
                state,
                action,
                active_planet_confirmed=active_planet_confirmed,
            )
        else:
            res = self._execute_escape_flight(
                client,
                state,
                action,
                active_planet_confirmed=active_planet_confirmed,
            )
        return self._attach_observed_planet(res)

    def _execute_abandon_colony(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        """Delete an undersized newly settled secondary colony (`< min_colony_fields`, 0 buildings/ships) via `internalSettings`."""
        planet = next((p for p in state.planets if p.planet_id == action.planet_id), None)
        if planet is None:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: colony planet_id={action.planet_id} no longer exists in empire state.",
            )
        raw_min = game_data.safe_optional_int(action.target_level, None)
        min_fields = max(50, int(raw_min)) if (raw_min is not None and raw_min >= 50) else 300
        coords_s = _safe_str(planet.coords or action.planet_coords)
        if not is_undersized_fresh_colony(planet, state, min_fields):
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=(
                    f"Aborted safety check: refusing to delete planet [{coords_s}] "
                    f"({planet.fields_used}/{planet.fields_max} fields) — Homeworld, developed, stationed fleet/defense, or >= {min_fields} fields."
                ),
            )

        settings_html = _call_with_optional_priority(
            client.navigate_menu,
            "internalSettings",
            priority="interactive",
        )
        soup = BeautifulSoup(settings_html if isinstance(settings_html, str) else "", "html.parser")
        inp = soup.find("input", attrs={"name": f"planet_{planet.planet_id}"})
        if inp is None:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: input planet_{planet.planet_id} ([{coords_s}]) not found on internalSettings.",
            )
        row_tr = inp.find_parent("tr")
        row_text = " ".join(row_tr.get_text(" ", strip=True).split()) if row_tr is not None else ""
        if re.search(r"\bpos\.?\s*1\b", row_text, re.IGNORECASE):
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted safety check: refusing to delete Pos. 1 Homeworld ([{coords_s}]) on internalSettings.",
            )
        m_fields = re.search(r"(\d+)\s*/\s*(\d+)\s*(?:fields|felder)", row_text, re.IGNORECASE | re.ASCII)
        if m_fields is not None:
            dom_used = int(m_fields.group(1))
            dom_max = int(m_fields.group(2))
            if dom_used > 0 or dom_max <= 0 or dom_max >= min_fields:
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=(
                        f"Aborted safety check: internalSettings reports {dom_used}/{dom_max} Fields on [{coords_s}] "
                        f"(expected 0/{planet.fields_max} < {min_fields})."
                    ),
                )
        if coords_s:
            m_rc = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", row_text, re.ASCII)
            m_pc = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", coords_s, re.ASCII)
            if m_rc and m_pc:
                norm_dom_c = f"{int(m_rc.group(1)):02d}:{int(m_rc.group(2)):03d}:{int(m_rc.group(3)):02d}"
                norm_pl_c = f"{int(m_pc.group(1)):02d}:{int(m_pc.group(2)):03d}:{int(m_pc.group(3)):02d}"
                if norm_dom_c != norm_pl_c:
                    return ExecutionResult(
                        executed=False,
                        dry_run=False,
                        action=action,
                        message=f"Aborted safety check: planet_{planet.planet_id} coords mismatch on internalSettings ({norm_dom_c} != {norm_pl_c}).",
                    )

        pid_token_re = re.compile(rf"\b{int(planet.planet_id)}\b", re.ASCII)
        norm_target_coords = ""
        if coords_s:
            m_tc = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", coords_s, re.ASCII)
            if m_tc:
                norm_target_coords = f"{int(m_tc.group(1)):02d}:{int(m_tc.group(2)):03d}:{int(m_tc.group(3)):02d}"

        def _el_is_displayed(el: Any) -> bool:
            fn = getattr(el, "is_displayed", None)
            if not callable(fn):
                return True
            try:
                return bool(fn())
            except Exception:
                return False

        def _click_allow_js(el: Any) -> None:
            if not _el_is_displayed(el):
                client.driver.execute_script("arguments[0].click();", el)
                return
            try:
                self._safe_click(client, el)
            except (ElementNotInteractableException, ElementClickInterceptedException):
                client.driver.execute_script("arguments[0].click();", el)

        client._switch_frame("F_Hauptframe")
        nav_token = self._mark_frame_nav(client)
        click_stage = "find_trigger"
        try:
            del_candidates = client.driver.find_elements(
                By.CSS_SELECTOR,
                '[onclick*="confirmPlanetDeletion"], [onclick*="deletePlanet"]',
            )
            visible_matches: list[Any] = []
            hidden_matches: list[Any] = []
            for el in del_candidates:
                oc = str(el.get_attribute("onclick") or "")
                if pid_token_re.search(oc):
                    if _el_is_displayed(el):
                        visible_matches.append(el)
                    else:
                        hidden_matches.append(el)
            del_trigger = visible_matches[0] if visible_matches else (hidden_matches[0] if hidden_matches else None)
            if del_trigger is None:
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: confirmPlanetDeletion control for planet_{planet.planet_id} ([{coords_s}]) not found on internalSettings.",
                )
            oc_trigger = str(del_trigger.get_attribute("onclick") or "")
            if "confirmPlanetDeletion" in oc_trigger:
                click_stage = "del_trigger"
                _click_allow_js(del_trigger)
                click_stage = "wait_confirm"
                confirm_btn = None
                retried_js_trigger = False
                start_wait = time.monotonic()
                deadline = start_wait + 2.5
                while time.monotonic() < deadline:
                    btns = client.driver.find_elements(
                        By.CSS_SELECTOR,
                        '#delete-confirm [onclick*="executeDeletePlanet"]',
                    )
                    vis_btns = [b for b in btns if _el_is_displayed(b)]
                    if vis_btns:
                        confirm_btn = vis_btns[0]
                        break
                    if not retried_js_trigger and (time.monotonic() - start_wait) >= 0.45:
                        retried_js_trigger = True
                        with contextlib.suppress(Exception):
                            client.driver.execute_script("arguments[0].click();", del_trigger)
                    time.sleep(0.05)
                if confirm_btn is None:
                    return ExecutionResult(
                        executed=False,
                        dry_run=False,
                        action=action,
                        message=f"Aborted: #delete-confirm executeDeletePlanet button did not become visible for [{coords_s}].",
                    )
                # Extra live safety check: confirm modal target matches planet_id / coords when exposed by JS.
                modal_check: Any = None
                with contextlib.suppress(Exception):
                    modal_check = client.driver.execute_script(
                        "return {"
                        "pid: (typeof window._planet_deleteID !== 'undefined' ? window._planet_deleteID : null), "
                        "coords: (document.getElementById('planet-delete-coords') ? document.getElementById('planet-delete-coords').textContent : '')"
                        "};"
                    )
                if isinstance(modal_check, dict):
                    js_pid = game_data.safe_optional_int(modal_check.get("pid"), None)
                    if js_pid is not None and js_pid > 0 and js_pid != int(planet.planet_id):
                        return ExecutionResult(
                            executed=False,
                            dry_run=False,
                            action=action,
                            message=(
                                f"Aborted safety check: #delete-confirm targets planet_id={js_pid} "
                                f"instead of {planet.planet_id} ([{coords_s}])."
                            ),
                        )
                    js_coords_raw = str(modal_check.get("coords") or "").strip()
                    if js_coords_raw and norm_target_coords:
                        m_mc = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", js_coords_raw, re.ASCII)
                        if m_mc:
                            norm_mc = f"{int(m_mc.group(1)):02d}:{int(m_mc.group(2)):03d}:{int(m_mc.group(3)):02d}"
                            if norm_mc != norm_target_coords:
                                return ExecutionResult(
                                    executed=False,
                                    dry_run=False,
                                    action=action,
                                    message=(
                                        f"Aborted safety check: #delete-confirm shows [{norm_mc}] "
                                        f"instead of [{norm_target_coords}]."
                                    ),
                                )
                self._pace_action(client, priority="interactive")
                nav_token = self._mark_frame_nav(client)
                time.sleep(random.uniform(*self.action_delay_range))
                click_stage = "confirm_btn"
                _click_allow_js(confirm_btn)
            else:
                self._pace_action(client, priority="interactive")
                nav_token = self._mark_frame_nav(client)
                time.sleep(random.uniform(*self.action_delay_range))
                click_stage = "direct_delete"
                _click_allow_js(del_trigger)
        except (StaleElementReferenceException, NoSuchElementException, ElementNotInteractableException, WebDriverException) as exc:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: internalSettings planet deletion controls failed for [{coords_s}] at {click_stage} ({type(exc).__name__}).",
            )

        after_html = client._wait_frame_ready(
            "F_Hauptframe",
            expected_action="internalSettings",
            nav_token=nav_token,
        )
        self._recent_timestamps.append(time.monotonic())
        after_soup = BeautifulSoup(after_html if isinstance(after_html, str) else "", "html.parser")
        gone_on_settings = after_soup.find("input", attrs={"name": f"planet_{planet.planet_id}"}) is None

        hw_pid = homeworld_planet_id(state)
        if hw_pid is not None and hw_pid != planet.planet_id and hasattr(client, "select_planet"):
            with contextlib.suppress(Exception):
                _call_with_optional_priority(client.select_planet, hw_pid, priority="interactive")

        updated = _call_with_optional_priority(
            client.read_planets_overview,
            refresh=True,
            priority="interactive",
        )
        gone_in_overview = all(p.planet_id != planet.planet_id for p in (updated.planets or ()))
        deleted_ok = bool(gone_on_settings and gone_in_overview)
        return ExecutionResult(
            executed=deleted_ok,
            dry_run=False,
            action=action,
            message=(
                f"Deleted undersized colony [{coords_s}] ({planet.fields_max} fields < {min_fields} target) to re-colonize."
                if deleted_ok
                else f"Failed to verify deletion of colony [{coords_s}] (planet_{planet.planet_id} still present)."
            ),
            updated_state=updated,
            validated=deleted_ok,
            validation_error=(
                None
                if deleted_ok
                else f"Colony planet_{planet.planet_id} ([{coords_s}]) still present after executeDeletePlanet."
            ),
        )


def _is_unattackable_target_html(html_or_text: str) -> bool:
    return ActionExecutor._is_unattackable_target_html(html_or_text)


def _extract_mission_forbidden_reason(html: str, mission_kind: str = "ATTACK") -> Optional[str]:
    return ActionExecutor._extract_mission_forbidden_reason(html, mission_kind)
