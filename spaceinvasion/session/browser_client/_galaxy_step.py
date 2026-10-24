"""In-place 2D Galaxy AJAX/arrow stepping collaborator (`GalaxyStepper`)."""

from __future__ import annotations

from collections.abc import Callable
import time

from spaceinvasion.core.coercion import coerce_int
from spaceinvasion.session.auth import SpaceInvasionAuthError
from spaceinvasion.session.browser_client._galaxy_dom import GalaxyDomOps
from spaceinvasion.session.browser_client._galaxy_js import (
    POLL_BUSY_STATE_JS,
    PROBE_STEP_STATE_JS,
    TRIGGER_SI_NAV_JS,
)
from spaceinvasion.session.browser_client._protocols import SELENIUM_NAV_ERRORS
from spaceinvasion.session.browser_client._state import ClientSessionState
from spaceinvasion.session.browser_client.frames import FrameNavigator
from spaceinvasion.session.browser_client.guards import PageGuardChecker
from spaceinvasion.session.limiter import RateLimitError


class GalaxyStepper:
    """Executes in-place 2D Galaxy probe, arrow clicks, and `window.si.navigate` steps."""

    def __init__(
        self,
        state: ClientSessionState,
        frames: FrameNavigator,
        guards: PageGuardChecker,
        dom: GalaxyDomOps,
    ) -> None:
        self._state = state
        self._frames = frames
        self._guards = guards
        self._dom = dom

    def probe_ajax_step_state(self) -> tuple[bool, int, int, int, int]:
        cur_g, cur_s, max_sys, max_gal = 0, 0, 400, 0
        try:
            self._frames.switch_frame("F_Hauptframe")
            probe = self._state.driver.execute_script(PROBE_STEP_STATE_JS)
            if probe == "captcha":
                self._guards.check_page_guards(self._state.driver.page_source or "")
            if not (isinstance(probe, dict) and probe.get("ok")):
                return False, cur_g, cur_s, max_sys, max_gal
            cur_g = coerce_int(probe.get("g"), 0)
            cur_s = coerce_int(probe.get("s"), 0)
            max_sys = max(1, min(1000, coerce_int(probe.get("maxS"), 400)))
            raw_max_g = coerce_int(probe.get("maxG"), 0)
            if 1 <= raw_max_g <= 50:
                max_gal = raw_max_g
            self._dom.record_universe_bounds(max_galaxies=max_gal if max_gal > 0 else None, max_system=max_sys)
            if probe.get("busy"):
                wait_busy_until = time.monotonic() + min(1.5, max(0.2, self._state.timeout * 0.15))
                while time.monotonic() < wait_busy_until:
                    time.sleep(0.06)
                    try:
                        b_probe = self._state.driver.execute_script(POLL_BUSY_STATE_JS)
                    except SELENIUM_NAV_ERRORS:
                        break
                    if isinstance(b_probe, dict) and not b_probe.get("busy"):
                        cur_g = coerce_int(b_probe.get("g"), cur_g)
                        cur_s = coerce_int(b_probe.get("s"), cur_s)
                        break
            return True, cur_g, cur_s, max_sys, max_gal
        except (SpaceInvasionAuthError, RateLimitError):
            raise
        except SELENIUM_NAV_ERRORS:
            return False, cur_g, cur_s, max_sys, max_gal

    def step_via_system_arrows(
        self,
        *,
        eff_gal: int,
        cur_s: int,
        sys_arg: int,
        max_sys: int,
        max_gal: int,
        eff_arrow_max: int,
        pace: bool,
        priority: str,
        on_intermediate_page: Callable[[str, int, int], None] | None,
    ) -> tuple[bool, tuple[int, int] | None]:
        fwd_steps = (sys_arg - cur_s) % max_sys
        bwd_steps = (cur_s - sys_arg) % max_sys
        circ_dist = min(fwd_steps, bwd_steps)
        if not (1 <= circ_dist < eff_arrow_max):
            return False, None
        step_dir = 1 if fwd_steps <= bwd_steps else -1
        step_cur_s = cur_s
        for step_i in range(circ_dist):
            if step_i > 0:
                if pace:
                    self._state.pace(priority=priority)
                elif self._state.min_nav_delay[1] > 0.0:
                    time.sleep(min(0.25, max(0.08, self._state.min_nav_delay[0] * 0.15)))
            exp_s = ((step_cur_s - 1 + step_dir) % max_sys) + 1
            if self._dom.click_system_arrow(step_dir) == "none":
                return False, None
            self._state.last_galaxy_step_clicks += 1
            step_ok, _ = self._dom.wait_galaxy_step_landed(
                expected_galaxy=eff_gal,
                expected_system=exp_s,
                before_coords=(eff_gal, step_cur_s),
                bail_if_idle_unchanged=True,
                max_galaxy=max_gal if max_gal > 0 else None,
            )
            if not step_ok:
                return False, None
            step_cur_s = exp_s
            if step_i < circ_dist - 1 and callable(on_intermediate_page):
                self._frames.switch_frame("F_Hauptframe")
                self._dom.sync_galaxy_json_textarea()
                inter_html = self._state.driver.page_source or ""
                self._guards.check_page_guards(inter_html)
                on_intermediate_page(inter_html, eff_gal, exp_s)
        self._state.last_galaxy_nav_method = "arrow"
        return True, (eff_gal, sys_arg)

    def step_via_galaxy_arrow(
        self,
        *,
        cur_g: int,
        cur_s: int,
        eff_gal: int,
        sys_arg: int,
        max_gal: int,
    ) -> tuple[bool, tuple[int, int] | None]:
        gal_dir = 1 if eff_gal > cur_g else -1
        if self._dom.click_galaxy_arrow(gal_dir) == "none":
            return False, None
        self._state.last_galaxy_step_clicks += 1
        gal_ok, gal_coords = self._dom.wait_galaxy_step_landed(
            expected_galaxy=eff_gal,
            expected_system=sys_arg,
            before_coords=(cur_g, cur_s),
            bail_if_idle_unchanged=True,
            max_galaxy=max_gal if max_gal > 0 else None,
        )
        if not gal_ok:
            return False, None
        self._state.last_galaxy_nav_method = "arrow"
        return True, gal_coords or (eff_gal, sys_arg)

    def step_via_si_navigate(
        self,
        *,
        cur_g: int,
        cur_s: int,
        eff_gal: int,
        sys_arg: int,
        max_gal: int,
    ) -> tuple[bool, tuple[int, int] | None]:
        self._frames.switch_frame("F_Hauptframe")
        self._state.driver.execute_script(TRIGGER_SI_NAV_JS, eff_gal, sys_arg)
        self._state.last_galaxy_step_clicks += 1
        landed, landed_coords = self._dom.wait_galaxy_step_landed(
            expected_galaxy=eff_gal,
            expected_system=sys_arg,
            max_galaxy=max_gal if max_gal > 0 else None,
        )
        if landed:
            self._state.last_galaxy_nav_method = (
                "ajax_refresh" if (cur_g == eff_gal and cur_s == sys_arg) else "ajax_navigate"
            )
        return landed, landed_coords
