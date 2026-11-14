"""Planets overview, menu status, and full empire snapshot reader (`StateSnapshotReader`)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import cast

from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from spaceinvasion.parsing.account_pages import parse_daily_login_bonus
from spaceinvasion.parsing.models import FleetAlert, GameState, QueueJob, TutorialStatus
from spaceinvasion.parsing.overview import (
    parse_home_metadata,
    parse_menu_status,
    parse_planets_overview_json,
    parse_unread_banner_count,
)
from spaceinvasion.parsing.tutorial import parse_tutorial_from_home
from spaceinvasion.session.auth import SpaceInvasionAuthError, detect_antibot_challenge
from spaceinvasion.session.client._protocols import (
    SELENIUM_NAV_ERRORS,
    call_with_optional_priority,
    client_monotonic,
    client_sleep,
    safe_positive_timeout,
)
from spaceinvasion.session.client._reader_enrich import (
    apply_passive_metal_ticker,
    enrich_buildings,
    enrich_economy,
    enrich_research,
    enrich_shipyard,
    extract_selected_planet_id,
)
from spaceinvasion.session.client._state import ClientSessionState
from spaceinvasion.session.client.frames import FrameNavigator
from spaceinvasion.session.client.guards import PageGuardChecker
from spaceinvasion.session.limiter import is_server_rate_limit_html

__all__ = ["StateSnapshotReader", "extract_selected_planet_id"]


class StateSnapshotReader:
    """Reads `F_planets`, `F_Navi`, and `F_Hauptframe` pages into a typed `GameState`."""

    def __init__(self, state: ClientSessionState, frames: FrameNavigator, guards: PageGuardChecker) -> None:
        self._state = state
        self._frames = frames
        self._guards = guards

    def _reload_planets_frame(self, *, frame_src: str, wrong_frame_page: bool, priority: str) -> None:
        self._state.pace(priority=priority)
        with self._state.driver_lock:
            self._frames.switch_frame("F_planets")
            if wrong_frame_page:
                self._state.driver.execute_script(
                    "window.__siReloadPending = 1; location.replace(arguments[0]);",
                    frame_src,
                )
            else:
                self._state.driver.execute_script("window.__siReloadPending = 1; location.reload();")
        wait_budget = safe_positive_timeout(self._state.timeout, 12.0, min_val=0.05, max_val=12.0)
        deadline = client_monotonic() + wait_budget
        while True:
            html_to_check: str | None = None
            ready_with_json = False
            with self._state.driver_lock:
                self._frames.switch_frame("F_planets")
                pending = self._state.driver.execute_script("return Boolean(window.__siReloadPending);")
                ready = self._state.driver.execute_script("return document.readyState;")
                if not pending and ready == "complete":
                    html_to_check = self._state.driver.page_source or ""
                    ready_with_json = bool(
                        self._state.driver.execute_script("return Boolean(document.getElementById('json-container'));")
                    )
            if html_to_check is not None:
                self._guards.check_page_guards(html_to_check)
                self._guards.check_portal_redirect(
                    html_to_check,
                    context_msg="Browser redirected to BitMeUp portal/login while reading planets overview.",
                )
                if ready_with_json:
                    break
            if client_monotonic() >= deadline:
                break
            client_sleep(0.2)

    def _read_json_container_locked(self) -> tuple[str, NoSuchElementException | None, str]:
        try:
            jc = self._state.driver.find_element(By.ID, "json-container")
            json_text = jc.get_attribute("textContent") or ""
            self._state.dom_healthy_epoch += 1
            return json_text, None, ""
        except NoSuchElementException as exc:
            return "", exc, (self._state.driver.page_source or "")

    def read_planets_overview(self, *, refresh: bool = False, priority: str = "normal") -> GameState:
        if self._state.hooks is not None:
            return cast(GameState, self._state.hooks.read_planets_overview(refresh=refresh, priority=priority))
        return self.read_planets_overview_impl(refresh=refresh, priority=priority)

    def read_planets_overview_impl(self, *, refresh: bool = False, priority: str = "normal") -> GameState:
        """Read `#json-container` from `F_planets`, optionally reloading the iframe first."""
        json_text, missing_html = "", ""
        missing_exc: NoSuchElementException | None = None
        frame_src = ""
        wrong_frame_page = False

        with self._state.driver_lock:
            self._state.driver.switch_to.default_content()
            planets_frame = self._state.driver.find_element(By.ID, "F_planets")
            try:
                frame_src = str(planets_frame.get_attribute("src") or "")
            except SELENIUM_NAV_ERRORS:
                frame_src = ""
            self._state.driver.switch_to.frame(planets_frame)
            inner_href = str(self._state.driver.execute_script("return location.href;") or "")
            wrong_frame_page = (
                bool(frame_src)
                and "internalplanetsoverview" not in inner_href.lower()
                and "internalplanetsoverview" in frame_src.lower()
            )
            need_reload = bool(refresh or wrong_frame_page)
            if not need_reload:
                json_text, missing_exc, missing_html = self._read_json_container_locked()

        if need_reload:
            self._reload_planets_frame(frame_src=frame_src, wrong_frame_page=wrong_frame_page, priority=priority)
            with self._state.driver_lock:
                self._frames.switch_frame("F_planets")
                json_text, missing_exc, missing_html = self._read_json_container_locked()

        if missing_exc is not None:
            self._guards.check_page_guards(missing_html)
            self._guards.check_portal_redirect(
                missing_html,
                context_msg="Browser redirected to BitMeUp portal/login while reading planets overview.",
            )
            raise SpaceInvasionAuthError(
                "SpaceInvasion session expired (missing #json-container in F_planets)."
            ) from missing_exc
        return parse_planets_overview_json(json_text)

    def read_menu_status(self) -> tuple[dict[str, int], int | None, int | None]:
        if self._state.hooks is not None:
            return self._state.hooks.read_menu_status()
        return self.read_menu_status_impl()

    def read_menu_status_impl(self) -> tuple[dict[str, int], int | None, int | None]:
        """Read unread message counters, server points, and rank from `F_Navi`."""
        with self._state.driver_lock:
            self._frames.switch_frame("F_Navi")
            html = self._state.driver.page_source or ""
        return parse_menu_status(html)

    def _resolve_home_html(
        self,
        main_url: str,
        raw_main_html: str,
        *,
        include_home: bool,
        priority: str,
    ) -> tuple[str, bool]:
        raw_low = raw_main_html.lower()
        has_portal_redirect = "bitmeup-login" in raw_low or "sip-tab-universe" in raw_low
        if "action=internalHome" in main_url:
            is_stale = (
                not raw_low.strip()
                or is_server_rate_limit_html(raw_main_html)
                or bool(detect_antibot_challenge(200, raw_main_html))
                or has_portal_redirect
            )
            if not is_stale:
                self._guards.check_page_guards(raw_main_html)
                return raw_main_html, has_portal_redirect
            if include_home:
                try:
                    return self._frames.navigate_menu("internalHome", priority=priority), has_portal_redirect
                except NoSuchElementException:
                    sid_query = self._frames.resolve_sid_query()
                    nav_token = self._frames.mark_frame_navigation("F_Hauptframe")
                    if sid_query:
                        target_url = self._frames.build_internal_url(f"action=internalHome&{sid_query}")
                        self._state.driver.execute_script("location.href = arguments[0];", target_url)
                    else:
                        self._state.driver.execute_script("window.__siReloadPending = 1; location.reload();")
                    return (
                        self._frames.wait_frame_ready(
                            "F_Hauptframe", expected_action="internalHome", nav_token=nav_token
                        ),
                        has_portal_redirect,
                    )
            if raw_main_html.strip():
                self._guards.check_page_guards(raw_main_html)
                if has_portal_redirect:
                    raise SpaceInvasionAuthError(
                        "Browser redirected to BitMeUp portal/login while reading game snapshot."
                    )
            return "", has_portal_redirect

        if include_home:
            return self._frames.navigate_menu("internalHome", priority=priority), has_portal_redirect
        if raw_main_html.strip():
            self._guards.check_page_guards(raw_main_html)
            if has_portal_redirect:
                raise SpaceInvasionAuthError("Browser redirected to BitMeUp portal/login while reading game snapshot.")
        return "", has_portal_redirect

    def _read_main_frame_context(
        self,
        *,
        include_home: bool,
        priority: str,
    ) -> tuple[str, str, str, bool]:
        with self._state.driver_lock:
            self._frames.switch_frame("F_Hauptframe")
            main_url = str(self._state.driver.execute_script("return location.href;") or "")
            raw_main_html = str(self._state.driver.page_source or "")
        home_html, has_portal_redirect = self._resolve_home_html(
            main_url, raw_main_html, include_home=include_home, priority=priority
        )
        return main_url, raw_main_html, home_html, has_portal_redirect

    def _parse_home_fields(
        self,
        home_html: str,
        raw_main_html: str,
        main_url: str,
    ) -> tuple[TutorialStatus | None, int | None, float | None, bool, tuple[QueueJob, ...], tuple[FleetAlert, ...]]:
        if home_html:
            tutorial = parse_tutorial_from_home(home_html)
            active_pid, speedup, newbie, queues, alerts = parse_home_metadata(home_html)
            self._state.last_home_daily_bonus_present = bool(parse_daily_login_bonus(home_html).get("present", False))
            return tutorial, active_pid, speedup, newbie, queues, alerts
        self._state.last_home_daily_bonus_present = None
        return None, extract_selected_planet_id(raw_main_html, main_url), None, False, (), ()

    def read_full_snapshot(
        self,
        *,
        include_buildings: bool = True,
        include_research: bool = True,
        include_shipyard: bool = True,
        include_economy: bool = False,
        include_home: bool = True,
        return_home: bool = True,
        priority: str = "normal",
        ensure_in_game_fn: Callable[..., object] | None = None,
    ) -> GameState:
        """Collect an empire snapshot across `F_planets`, `F_Navi`, and `F_Hauptframe`."""
        if ensure_in_game_fn is not None:
            call_with_optional_priority(ensure_in_game_fn, priority=priority)
        else:
            self._guards.ensure_in_game(priority=priority, refresh_planets_fn=self.read_planets_overview)

        state = self.read_planets_overview(refresh=True, priority=priority)
        unread, account_points, account_rank = self.read_menu_status()
        main_url, raw_main_html, home_html, has_portal_redirect = self._read_main_frame_context(
            include_home=include_home, priority=priority
        )
        tutorial, active_pid, speedup, newbie, queues, alerts = self._parse_home_fields(
            home_html, raw_main_html, main_url
        )

        merged_queues = list(queues)
        planets = list(state.planets)
        target_pid = active_pid or (planets[0].planet_id if planets else None)
        active_idx: int | None = next(
            (i for i, p in enumerate(planets) if p.planet_id == target_pid), 0 if planets else None
        )

        b_html = ""
        if include_buildings and planets and active_idx is not None:
            b_html, active_pid, active_idx, merged_queues, tutorial = enrich_buildings(
                self._state,
                self._frames,
                planets,
                active_pid,
                active_idx,
                main_url,
                raw_main_html,
                include_home=include_home,
                has_portal_redirect=has_portal_redirect,
                priority=priority,
                merged_queues=merged_queues,
                tutorial=tutorial,
            )

        apply_passive_metal_ticker(planets, active_idx, b_html, home_html, raw_main_html, main_url)
        if include_economy:
            active_pid, active_idx = enrich_economy(self._frames, planets, active_pid, active_idx, priority=priority)

        research_offers = state.research_offers
        if include_research:
            research_offers, active_pid, active_idx, merged_queues = enrich_research(
                self._frames,
                state,
                planets,
                active_pid,
                active_idx,
                priority=priority,
                merged_queues=merged_queues,
                tutorial=tutorial,
            )
        if include_shipyard:
            active_pid, active_idx = enrich_shipyard(self._frames, planets, active_pid, active_idx, priority=priority)
        if return_home and (include_buildings or include_research or include_shipyard or include_economy):
            self._frames.navigate_menu("internalHome", priority=priority)

        banner_unread = parse_unread_banner_count(b_html or home_html or raw_main_html)
        if banner_unread > 0:
            unread = {**unread, "total": max(unread.get("total", 0), banner_unread)}

        self._state.last_snapshot_observed_active_planet = active_pid is not None
        with self._state.driver_lock:
            self._state.driver.switch_to.default_content()
        return replace(
            state,
            planets=tuple(planets),
            active_planet_id=(active_pid or target_pid) or state.active_planet_id,
            unread_messages=unread,
            tutorial=tutorial,
            active_queues=tuple(merged_queues),
            fleet_alerts=alerts,
            research_offers=research_offers,
            speedup_factor=speedup,
            newbie_protection=newbie,
            account_points=account_points,
            account_rank=account_rank,
        )
