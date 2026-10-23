"""Low-level Galaxy DOM/AJAX interactions: textarea sync, bounds, form submit, arrow clicks."""

from __future__ import annotations

import logging
import time
from typing import Protocol, runtime_checkable

from selenium.common.exceptions import ElementClickInterceptedException
from selenium.webdriver.common.by import By

from spaceinvasion.core.coercion import cap_json_int, coerce_int
from spaceinvasion.parsing.galaxy import extract_galaxy_universe_bounds
from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError
from spaceinvasion.session.browser_client._galaxy_js import (
    CLICK_GALAXY_ARROW_JS,
    CLICK_SYSTEM_ARROW_JS,
    FALLBACK_GALAXY_ARROW_JS,
    FALLBACK_SYSTEM_ARROW_JS,
    SYNC_GALAXY_JSON_JS,
    WAIT_GALAXY_AJAX_READY_JS,
    WAIT_GALAXY_STEP_LANDED_JS,
)
from spaceinvasion.session.browser_client._protocols import (
    SELENIUM_NAV_ERRORS,
    WebElementLike,
    is_real_selenium_driver,
)
from spaceinvasion.session.browser_client._state import ClientSessionState
from spaceinvasion.session.browser_client.frames import FrameNavigator
from spaceinvasion.session.browser_client.guards import PageGuardChecker
from spaceinvasion.session.limiter import RateLimitError

_LOGGER = logging.getLogger(__name__)

_GALAXY_SUBMIT_SELECTORS: tuple[str, ...] = (
    'form:has(input[name="galaxy"]) input[type="submit"]',
    'form:has(input[name="galaxy"]) button[type="submit"]',
    'form:has(input[name="gal"]) input[type="submit"]',
    'form:has(input[name="gal"]) button[type="submit"]',
    'form:has(input[name="g"]) input[type="submit"]',
    'form:has(input[name="g"]) button[type="submit"]',
    'form:has(input[name="system"]) input[type="submit"]',
    'form:has(input[name="system"]) button[type="submit"]',
    'form:has(input[name="sun"]) input[type="submit"]',
    'form:has(input[name="sun"]) button[type="submit"]',
    'form:has(input[name="s"]) input[type="submit"]',
    'form:has(input[name="s"]) button[type="submit"]',
    'form input[type="submit"]',
    'form button[type="submit"]',
    'input[type="submit"]',
)


@runtime_checkable
class _ClickableElement(Protocol):
    def click(self) -> None: ...


@runtime_checkable
class _HasFindElements(Protocol):
    def find_elements(self, by: str = ..., value: str | None = None) -> list[WebElementLike]: ...


class GalaxyDomOps:
    """Encapsulates low-level Galaxy DOM/AJAX mechanics inside `F_Hauptframe`."""

    def __init__(self, state: ClientSessionState, frames: FrameNavigator, guards: PageGuardChecker) -> None:
        self._state = state
        self._frames = frames
        self._guards = guards

    def sync_galaxy_json_textarea(self) -> None:
        """Mirror live 2D Galaxy `window.si.result` into `<textarea id="json-element">`."""
        try:
            self._state.driver.execute_script(SYNC_GALAXY_JSON_JS)
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("sync_galaxy_json_textarea skipped: %s", exc)

    def record_universe_bounds(
        self,
        *,
        max_galaxies: object = None,
        max_system: object = None,
        html: str = "",
    ) -> tuple[int | None, int | None]:
        """Record dynamically observed universe `(max_galaxies, max_system)` bounds."""
        mg = coerce_int(max_galaxies, 0) if max_galaxies is not None else 0
        ms = coerce_int(max_system, 0) if max_system is not None else 0
        if (mg <= 0 or ms <= 0) and html:
            html_g, html_s = extract_galaxy_universe_bounds(html)
            if mg <= 0 and html_g is not None:
                mg = html_g
            if ms <= 0 and html_s is not None:
                ms = html_s
        if 1 <= mg <= 50:
            self._state.last_galaxy_max_galaxies = mg
        if 1 <= ms <= 1000:
            self._state.last_galaxy_max_system = ms
        return self._state.last_galaxy_max_galaxies, self._state.last_galaxy_max_system

    def wait_galaxy_ajax_ready(self, html: str) -> str:
        """Wait briefly for SpaceInvasion 2D/Modern Galaxy (`SI_TPL.galaxy`) to populate planets."""
        if "SI_TPL.galaxy" not in html:
            return html
        self.record_universe_bounds(html=html)
        ajax_deadline = time.monotonic() + min(3.5, max(0.2, self._state.timeout * 0.25))
        while time.monotonic() < ajax_deadline:
            self._frames.switch_frame("F_Hauptframe")
            try:
                rendered = bool(self._state.driver.execute_script(WAIT_GALAXY_AJAX_READY_JS))
            except SELENIUM_NAV_ERRORS:
                rendered = True
            if rendered:
                self.sync_galaxy_json_textarea()
                html = self._state.driver.page_source or html
                self._guards.check_page_guards(html)
                break
            time.sleep(0.15)
        self.record_universe_bounds(html=html)
        return html

    def _find_named_input(self, names: tuple[str, ...]) -> WebElementLike | None:
        for name in names:
            try:
                els = self._state.driver.find_elements(By.NAME, name)
                if els:
                    return els[0]
            except SELENIUM_NAV_ERRORS as exc:
                _LOGGER.debug("find_elements(NAME=%s) failed: %s", name, exc)
        return None

    def _locate_form_and_submit(
        self,
        gal_inp: WebElementLike | None,
        sys_inp: WebElementLike | None,
    ) -> tuple[object, WebElementLike | None]:
        target_form: object = None
        for anchor in (gal_inp, sys_inp):
            if anchor is None:
                continue
            try:
                target_form = anchor.find_element(By.XPATH, "ancestor::form[1]")
                if target_form is not None:
                    break
            except SELENIUM_NAV_ERRORS as exc:
                _LOGGER.debug("ancestor::form lookup failed: %s", exc)
            except AttributeError as exc:
                _LOGGER.debug("ancestor::form missing find_element: %s", exc)
            try:
                candidate = self._state.driver.execute_script(
                    "return arguments[0] && arguments[0].form ? arguments[0].form : null;",
                    anchor,
                )
                if candidate is not None:
                    target_form = candidate
                    break
            except SELENIUM_NAV_ERRORS as exc:
                _LOGGER.debug("JS anchor.form lookup failed: %s", exc)

        submit_btn: WebElementLike | None = None
        if isinstance(target_form, _HasFindElements):
            for css in ('input[type="submit"]', 'button[type="submit"]'):
                try:
                    els = target_form.find_elements(By.CSS_SELECTOR, css)
                    if els:
                        submit_btn = els[0]
                        break
                except SELENIUM_NAV_ERRORS as exc:
                    _LOGGER.debug("Scoped submit lookup failed: %s", exc)
        if submit_btn is None and target_form is None:
            for css in _GALAXY_SUBMIT_SELECTORS:
                try:
                    els = self._state.driver.find_elements(By.CSS_SELECTOR, css)
                    if els:
                        submit_btn = els[0]
                        break
                except SELENIUM_NAV_ERRORS as exc:
                    _LOGGER.debug("Fallback submit lookup %s failed: %s", css, exc)
        return target_form, submit_btn

    def submit_galaxy_coords_form(
        self,
        *,
        galaxy: int | None,
        system: int | None,
        priority: str,
    ) -> str | None:
        self._frames.switch_frame("F_Hauptframe")
        gal_inp = self._find_named_input(("galaxy", "gal", "g"))
        sys_inp = self._find_named_input(("system", "sun", "s"))
        if gal_inp is None and sys_inp is None:
            return None

        self._state.pace(priority=priority)
        nav_token = self._frames.mark_frame_navigation("F_Hauptframe")
        if galaxy is not None and gal_inp is not None:
            gal_inp.clear()
            gal_inp.send_keys(str(cap_json_int(max(1, coerce_int(galaxy, 1)))))
        if system is not None and sys_inp is not None:
            sys_inp.clear()
            sys_inp.send_keys(str(cap_json_int(max(1, coerce_int(system, 1)))))

        target_form, submit_btn = self._locate_form_and_submit(gal_inp, sys_inp)
        if submit_btn is not None:
            self._state.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", submit_btn)
            try:
                submit_btn.click()
            except ElementClickInterceptedException:
                self._state.driver.execute_script("arguments[0].click();", submit_btn)
        elif target_form is not None:
            self._state.driver.execute_script("arguments[0].submit();", target_form)
        else:
            form_els = self._state.driver.find_elements(By.TAG_NAME, "form")
            if not form_els:
                return None
            self._state.driver.execute_script("arguments[0].submit();", form_els[0])
        return self._frames.wait_frame_ready("F_Hauptframe", expected_action="internalGalaxy", nav_token=nav_token)

    def wait_galaxy_step_landed(
        self,
        *,
        expected_galaxy: int,
        expected_system: int,
        before_coords: tuple[int, int] | None = None,
        bail_if_idle_unchanged: bool = False,
        max_galaxy: int | None = None,
    ) -> tuple[bool, tuple[int, int] | None]:
        """Poll `window.si` inside `F_Hauptframe` until `(expected_galaxy, expected_system)` lands."""
        started = time.monotonic()
        deadline = started + min(4.0, max(0.4, self._state.timeout * 0.35))
        while time.monotonic() < deadline:
            time.sleep(0.06)
            self._frames.switch_frame("F_Hauptframe")
            try:
                status = self._state.driver.execute_script(WAIT_GALAXY_STEP_LANDED_JS)
            except SELENIUM_NAV_ERRORS:
                break
            if not isinstance(status, dict):
                continue
            if status.get("throttled"):
                self._guards.check_page_guards(self._state.driver.page_source or "")
                raise RateLimitError(
                    "SpaceInvasion 2D Galaxy AJAX per-minute page-view throttle hit.",
                    server_throttle=True,
                )
            if status.get("missing"):
                break
            busy = bool(status.get("busy"))
            animating = bool(status.get("animating"))
            res_g = coerce_int(status.get("resG"), 0)
            res_s = coerce_int(status.get("resS"), 0)
            cur_g = coerce_int(status.get("g"), 0)
            cur_s = coerce_int(status.get("s"), 0)
            if not busy and res_g == expected_galaxy and res_s == expected_system:
                return True, (expected_galaxy, expected_system)
            if (
                not busy
                and max_galaxy is not None
                and max_galaxy >= 1
                and expected_galaxy > max_galaxy
                and 1 <= res_g <= max_galaxy
                and res_s == expected_system
            ):
                return True, (res_g, res_s)
            if bail_if_idle_unchanged and not busy and not animating and (time.monotonic() - started) >= 0.90:
                if before_coords is not None and (cur_g, cur_s) == before_coords and (res_g, res_s) == before_coords:
                    return False, None
                if res_g > 0 and res_g != expected_galaxy:
                    return False, None
        return False, None

    def _finalize_arrow_click(self, res: object, *, fallback_js: str, step_dir: int) -> str:
        if not isinstance(res, dict):
            return "none"
        clicked = str(res.get("clicked") or "none")
        if clicked != "element":
            return clicked
        el = res.get("el")
        if el is None:
            return "none"
        try:
            self._state.driver.execute_script(
                "if (arguments[0] && arguments[0].scrollIntoView)"
                " arguments[0].scrollIntoView({block: 'nearest', inline: 'nearest'});",
                el,
            )
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("Arrow scrollIntoView failed: %s", exc)
        try:
            if isinstance(el, _ClickableElement):
                el.click()
                return "dom"
        except (SpaceInvasionAuthError, AntiBotChallengeError, RateLimitError):
            raise
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("Selenium arrow click failed, trying JS fallback: %s", exc)
        try:
            self._state.driver.execute_script("arguments[0].click();", el)
            return "dom"
        except SELENIUM_NAV_ERRORS:
            try:
                return "fn" if bool(self._state.driver.execute_script(fallback_js, step_dir)) else "none"
            except SELENIUM_NAV_ERRORS:
                return "none"

    def click_system_arrow(self, delta: int) -> str:
        step_dir = 1 if int(delta) >= 0 else -1
        use_selenium_click = is_real_selenium_driver(self._state.driver)
        self._frames.switch_frame("F_Hauptframe")
        res = self._state.driver.execute_script(CLICK_SYSTEM_ARROW_JS, step_dir, use_selenium_click)
        return self._finalize_arrow_click(res, fallback_js=FALLBACK_SYSTEM_ARROW_JS, step_dir=step_dir)

    def click_galaxy_arrow(self, delta: int) -> str:
        step_dir = 1 if int(delta) >= 0 else -1
        use_selenium_click = is_real_selenium_driver(self._state.driver)
        self._frames.switch_frame("F_Hauptframe")
        res = self._state.driver.execute_script(CLICK_GALAXY_ARROW_JS, step_dir, use_selenium_click)
        return self._finalize_arrow_click(res, fallback_js=FALLBACK_GALAXY_ARROW_JS, step_dir=step_dir)
