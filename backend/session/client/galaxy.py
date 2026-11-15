"""High-level Galaxy map navigation, stepping, and quick-spy dispatch (`GalaxyNavigator`)."""

from __future__ import annotations

from collections.abc import Callable
import logging

from selenium.common.exceptions import ElementClickInterceptedException
from selenium.webdriver.common.by import By

from backend.core.coercion import cap_json_int, coerce_int
from backend.parsing.galaxy import extract_galaxy_system_coords
from backend.session.auth import SpaceInvasionAuthError
from backend.session.client._galaxy_dom import GalaxyDomOps
from backend.session.client._galaxy_js import (
    CHECK_SI_GETDATA_JS,
    DISPATCH_SPY_JS,
    POLL_SPY_OUTCOME_JS,
    PROBE_SI_META_JS,
)
from backend.session.client._galaxy_step import GalaxyStepper
from backend.session.client._protocols import (
    ARROW_NAV_MAX_DISTANCE,
    SELENIUM_NAV_ERRORS,
    client_monotonic,
    client_sleep,
    is_real_selenium_driver,
)
from backend.session.client._state import ClientSessionState
from backend.session.client.frames import FrameNavigator
from backend.session.client.guards import PageGuardChecker
from backend.session.limiter import RateLimitError

_LOGGER = logging.getLogger(__name__)
_GALAXY_LOAD_ERRORS: tuple[type[BaseException], ...] = (SpaceInvasionAuthError, RateLimitError) + SELENIUM_NAV_ERRORS


class GalaxyNavigator:
    """Navigates `internalGalaxy`, steps across systems/galaxies, and dispatches 2D quick-spies."""

    def __init__(
        self,
        state: ClientSessionState,
        frames: FrameNavigator,
        guards: PageGuardChecker,
        dom: GalaxyDomOps | None = None,
        stepper: GalaxyStepper | None = None,
    ) -> None:
        self._state = state
        self._frames = frames
        self._guards = guards
        self._dom = dom if dom is not None else GalaxyDomOps(state, frames, guards)
        self._stepper = stepper if stepper is not None else GalaxyStepper(state, frames, guards, self._dom)

    def _current_frame_url(self) -> str:
        try:
            return str(self._state.driver.execute_script("return location.href;") or "")
        except SELENIUM_NAV_ERRORS:
            return ""

    def _load_galaxy_page(self, gal_arg: int | None, sys_arg: int | None) -> str:
        sid_query = self._frames.resolve_sid_query()
        if sid_query:
            p_gal_js: object = None
            if self._state.detach_on_close and is_real_selenium_driver(self._state.driver):
                try:
                    self._frames.switch_frame("F_Navi")
                    p_gal_js = self._state.driver.execute_script(
                        "return (typeof window.p_galaxy === 'function') ? String(window.p_galaxy) : null;"
                    )
                except SELENIUM_NAV_ERRORS as exc:
                    p_gal_js = f"<err:{exc}>"
            params = ["action=internalGalaxy", sid_query]
            if gal_arg is not None:
                params.append(f"galaxy={gal_arg}")
            if sys_arg is not None:
                params.append(f"system={sys_arg}")
            if gal_arg is not None:
                params.append(f"g={gal_arg}")
            if sys_arg is not None:
                params.append(f"s={sys_arg}")
            target_url = self._frames.build_internal_url("&".join(params))
            try:
                return self._frames.open_frame_url_with_retry(target_url, expected_action="internalGalaxy")
            except _GALAXY_LOAD_ERRORS as exc:
                if self._state.detach_on_close and is_real_selenium_driver(self._state.driver):
                    _LOGGER.warning(
                        "open_galaxy_failed target_url=%s p_galaxy_js=%s error=%s diag=%s",
                        target_url,
                        p_gal_js,
                        exc,
                        self._guards.capture_dom_diag(),
                    )
                raise

        nav_token = self._frames.mark_frame_navigation("F_Hauptframe")
        self._frames.switch_frame("F_Navi")
        gal_a = self._state.driver.find_element(By.CSS_SELECTOR, 'a[onclick*="p_galaxy"]')
        self._state.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", gal_a)
        try:
            gal_a.click()
        except ElementClickInterceptedException:
            self._state.driver.execute_script("arguments[0].click();", gal_a)
        return self._frames.wait_frame_ready("F_Hauptframe", expected_action="internalGalaxy", nav_token=nav_token)

    def _read_si_2d_meta(self, obs_max_g: int | None) -> tuple[bool, int | None]:
        try:
            si_meta = self._state.driver.execute_script(PROBE_SI_META_JS)
            if isinstance(si_meta, dict) and si_meta.get("ok"):
                obs_max_g, _ = self._dom.record_universe_bounds(
                    max_galaxies=si_meta.get("maxG"),
                    max_system=si_meta.get("maxS"),
                )
                return True, obs_max_g
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("2D Galaxy metadata probe failed: %s", exc)
        return False, obs_max_g

    def open_galaxy_with_coords(
        self,
        *,
        galaxy: int | None = None,
        system: int | None = None,
        priority: str = "interactive",
    ) -> tuple[str, int, int]:
        """Open `internalGalaxy` inside `F_Hauptframe` and return `(html, actual_galaxy, actual_system)`."""
        self._state.pace(priority=priority)
        gal_arg = cap_json_int(max(1, coerce_int(galaxy, 1))) if galaxy is not None else None
        sys_arg = cap_json_int(max(1, coerce_int(system, 1))) if system is not None else None

        html = self._dom.wait_galaxy_ajax_ready(self._load_galaxy_page(gal_arg, sys_arg))
        self._frames.switch_frame("F_Hauptframe")
        cur_url = self._current_frame_url()
        obs_max_g, _ = self._dom.record_universe_bounds(html=html)
        has_si_2d, obs_max_g = self._read_si_2d_meta(obs_max_g)

        fallback_gal = gal_arg if gal_arg is not None else 1
        fallback_sys = sys_arg if sys_arg is not None else 1
        actual_gal, actual_sys = extract_galaxy_system_coords(html, url=cur_url, default=(fallback_gal, fallback_sys))

        want_gal = gal_arg if gal_arg is not None else actual_gal
        want_sys = sys_arg if sys_arg is not None else actual_sys
        if obs_max_g is not None and obs_max_g >= 1 and want_gal > obs_max_g:
            want_gal = ((want_gal - 1) % obs_max_g) + 1

        if (galaxy is not None or system is not None) and (actual_gal != want_gal or actual_sys != want_sys):
            if has_si_2d:
                html, actual_gal, actual_sys = self.step_galaxy_system(
                    galaxy=want_gal,
                    system=want_sys,
                    pace=False,
                    priority=priority,
                    _allow_full_load=False,
                )
            else:
                switched = self._dom.submit_galaxy_coords_form(
                    galaxy=want_gal if galaxy is not None else None,
                    system=system,
                    priority=priority,
                )
                if switched is not None:
                    html = self._dom.wait_galaxy_ajax_ready(switched)
                    self._dom.record_universe_bounds(html=html)
                    self._frames.switch_frame("F_Hauptframe")
                    cur_url = self._current_frame_url()
                    actual_gal, actual_sys = extract_galaxy_system_coords(
                        html, url=cur_url, default=(want_gal, want_sys)
                    )

        self._state.last_galaxy_coords = (actual_gal, actual_sys)
        return html, actual_gal, actual_sys

    def open_galaxy(
        self,
        *,
        galaxy: int | None = None,
        system: int | None = None,
        priority: str = "interactive",
    ) -> str:
        """Open `internalGalaxy` inside `F_Hauptframe` using `F_Navi`'s live `SI_TPL.menu.sid`."""
        html, _, _ = self.open_galaxy_with_coords(galaxy=galaxy, system=system, priority=priority)
        return html

    def step_galaxy_system(
        self,
        *,
        galaxy: int,
        system: int,
        pace: bool = False,
        priority: str = "normal",
        max_arrow_distance: int = ARROW_NAV_MAX_DISTANCE,
        on_intermediate_page: Callable[[str, int, int], None] | None = None,
        _allow_full_load: bool = True,
    ) -> tuple[str, int, int]:
        if self._state.hooks is not None:
            return self._state.hooks.step_galaxy_system(
                galaxy=galaxy,
                system=system,
                pace=pace,
                priority=priority,
                max_arrow_distance=max_arrow_distance,
                on_intermediate_page=on_intermediate_page,
                _allow_full_load=_allow_full_load,
            )
        return self.step_galaxy_system_impl(
            galaxy=galaxy,
            system=system,
            pace=pace,
            priority=priority,
            max_arrow_distance=max_arrow_distance,
            on_intermediate_page=on_intermediate_page,
            _allow_full_load=_allow_full_load,
        )

    def step_galaxy_system_impl(
        self,
        *,
        galaxy: int,
        system: int,
        pace: bool = False,
        priority: str = "normal",
        max_arrow_distance: int = ARROW_NAV_MAX_DISTANCE,
        on_intermediate_page: Callable[[str, int, int], None] | None = None,
        _allow_full_load: bool = True,
    ) -> tuple[str, int, int]:
        """Step to `(galaxy, system)` in-place on `internalGalaxy`, prioritizing arrow clicks."""
        gal_arg = cap_json_int(max(1, min(1000, coerce_int(galaxy, 1))))
        sys_arg = cap_json_int(max(1, min(1000, coerce_int(system, 1))))
        eff_arrow_max = max(1, coerce_int(max_arrow_distance, ARROW_NAV_MAX_DISTANCE))
        self._state.last_galaxy_nav_method = ""
        self._state.last_galaxy_step_clicks = 0

        can_ajax, cur_g, cur_s, max_sys, max_gal = self._stepper.probe_ajax_step_state()
        if not can_ajax:
            self._state.last_galaxy_nav_method = "full_load"
            self._state.last_galaxy_step_clicks = 1
            if _allow_full_load:
                return self.open_galaxy_with_coords(galaxy=gal_arg, system=sys_arg, priority=priority)
            self._frames.switch_frame("F_Hauptframe")
            self._dom.sync_galaxy_json_textarea()
            html_fb = self._state.driver.page_source or ""
            self._guards.check_page_guards(html_fb)
            self._dom.record_universe_bounds(html=html_fb)
            actual_gal, actual_sys = extract_galaxy_system_coords(html_fb, default=(gal_arg, sys_arg))
            self._state.last_galaxy_coords = (actual_gal, actual_sys)
            return html_fb, actual_gal, actual_sys

        eff_gal = ((gal_arg - 1) % max_gal) + 1 if (max_gal > 0 and gal_arg > max_gal) else gal_arg
        if pace:
            self._state.pace(priority=priority)

        landed = False
        landed_coords: tuple[int, int] | None = None
        if cur_g == eff_gal and 1 <= cur_s <= max_sys and 1 <= sys_arg <= max_sys:
            landed, landed_coords = self._stepper.step_via_system_arrows(
                eff_gal=eff_gal,
                cur_s=cur_s,
                sys_arg=sys_arg,
                max_sys=max_sys,
                max_gal=max_gal,
                eff_arrow_max=eff_arrow_max,
                pace=pace,
                priority=priority,
                on_intermediate_page=on_intermediate_page,
            )
        if (
            not landed
            and cur_s == sys_arg
            and 1 <= cur_s <= max_sys
            and cur_g > 0
            and eff_gal > 0
            and abs(eff_gal - cur_g) == 1
        ):
            landed, landed_coords = self._stepper.step_via_galaxy_arrow(
                cur_g=cur_g, cur_s=cur_s, eff_gal=eff_gal, sys_arg=sys_arg, max_gal=max_gal
            )
        if not landed:
            landed, landed_coords = self._stepper.step_via_si_navigate(
                cur_g=cur_g, cur_s=cur_s, eff_gal=eff_gal, sys_arg=sys_arg, max_gal=max_gal
            )
        if not landed and _allow_full_load:
            self._state.last_galaxy_nav_method = "full_load"
            self._state.last_galaxy_step_clicks += 1
            return self.open_galaxy_with_coords(galaxy=eff_gal, system=sys_arg, priority=priority)

        self._frames.switch_frame("F_Hauptframe")
        self._dom.sync_galaxy_json_textarea()
        html = self._state.driver.page_source or ""
        self._guards.check_page_guards(html)
        self._dom.record_universe_bounds(html=html)
        if landed_coords is not None:
            actual_gal, actual_sys = landed_coords
        else:
            actual_gal, actual_sys = extract_galaxy_system_coords(
                html, url=self._current_frame_url(), default=(eff_gal, sys_arg)
            )
        self._state.last_galaxy_coords = (actual_gal, actual_sys)
        return html, actual_gal, actual_sys

    def _poll_spy_outcome(self) -> dict[str, object]:
        deadline = client_monotonic() + min(4.5, max(0.5, self._state.timeout * 0.4))
        outcome: dict[str, object] = {"supported": True, "started": False, "error": ""}
        while client_monotonic() < deadline:
            client_sleep(0.1)
            self._frames.switch_frame("F_Hauptframe")
            try:
                st = self._state.driver.execute_script(POLL_SPY_OUTCOME_JS)
            except SELENIUM_NAV_ERRORS as exc:
                outcome["error"] = str(exc)
                break
            if not isinstance(st, dict):
                continue
            if st.get("throttled"):
                self._guards.check_page_guards(self._state.driver.page_source or "")
                raise RateLimitError(
                    "SpaceInvasion 2D Galaxy AJAX per-minute page-view throttle hit.",
                    server_throttle=True,
                )
            if st.get("busy"):
                continue
            fs = st.get("flightStarted")
            outcome["started"] = bool(fs)
            outcome["flight_started"] = fs
            outcome["error"] = str(st.get("err") or "").strip()
            break
        return outcome

    def dispatch_galaxy_spy(
        self,
        *,
        galaxy: int,
        system: int,
        position: int,
        quantity: int = 1,
        is_asteroid: bool = False,
        priority: str = "interactive",
    ) -> dict[str, object]:
        """Dispatch a 2D Galaxy quick-spy (`startFleetAction=spy`) via `window.si.getData(...)`."""
        gal_arg = cap_json_int(max(1, min(1000, coerce_int(galaxy, 1))))
        sys_arg = cap_json_int(max(1, min(1000, coerce_int(system, 1))))
        pos_arg = cap_json_int(max(1, min(16, coerce_int(position, 1))))
        qty_arg = cap_json_int(max(1, min(9999, coerce_int(quantity, 1))))

        html, actual_gal, actual_sys = self.step_galaxy_system(
            galaxy=gal_arg, system=sys_arg, pace=True, priority=priority
        )
        self._frames.switch_frame("F_Hauptframe")
        try:
            has_si = bool(self._state.driver.execute_script(CHECK_SI_GETDATA_JS))
        except SELENIUM_NAV_ERRORS:
            has_si = False
        if not has_si:
            return {"supported": False, "started": False, "error": "2D Galaxy si.getData unavailable", "html": html}

        self._state.pace(priority=priority)
        self._frames.switch_frame("F_Hauptframe")
        ta_part = "&ta=1" if is_asteroid else ""
        param = (
            f"&startFleet=1&fleets={qty_arg}&startFleetAction=spy&gal={gal_arg}&sun={sys_arg}&p={pos_arg}{ta_part}&m=1"
        )
        self._state.driver.execute_script(DISPATCH_SPY_JS, gal_arg, sys_arg, pos_arg, qty_arg, param)

        outcome = self._poll_spy_outcome()
        self._frames.switch_frame("F_Hauptframe")
        self._dom.sync_galaxy_json_textarea()
        post_html = self._state.driver.page_source or html
        self._guards.check_page_guards(post_html)
        outcome["html"] = post_html
        outcome["galaxy"] = actual_gal
        outcome["system"] = actual_sys
        return outcome
