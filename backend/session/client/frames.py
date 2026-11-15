"""Frame switching, navigation readiness polling, SID resolution, and menu navigation (`FrameNavigator`)."""

from __future__ import annotations

from collections.abc import Callable
import logging
import re
import secrets
from urllib.parse import urljoin

from selenium.common.exceptions import ElementClickInterceptedException
from selenium.webdriver.common.by import By

from backend.core.coercion import cap_json_int, coerce_int
from backend.session.auth import SpaceInvasionAuthError
from backend.session.client._guards_diag import is_renderer_timeout, recover_renderer_timeout
from backend.session.client._protocols import (
    SELENIUM_NAV_ERRORS,
    client_monotonic,
    client_sleep,
    safe_positive_timeout,
)
from backend.session.client._state import ClientSessionState
from backend.session.client.guards import PageGuardChecker
from backend.session.limiter import RateLimitError

_LOGGER = logging.getLogger(__name__)
_SID_QUERY_RE: re.Pattern[str] = re.compile(r"\bsid=([0-9a-zA-Z_-]+)")
_SID_CLEAN_RE: re.Pattern[str] = re.compile(r"(?:sid=)?[0-9a-zA-Z_-]+", re.IGNORECASE)

_FRAME_PROBE_JS: str = (
    "return {"
    "ready: String(document.readyState || ''), "
    "href: String(location.href || ''), "
    "token: (typeof window.__siNavToken === 'string') ? window.__siNavToken : null"
    "};"
)

_HF_SID_JS: str = (
    "if (window.sid) return String(window.sid);"
    "if (window.SI_TPL && SI_TPL.galaxyMap && SI_TPL.galaxyMap.sid) return String(SI_TPL.galaxyMap.sid);"
    "var a = document.querySelector('a[href*=\"sid=\"]');"
    "if (a && a.href) return String(a.href);"
    "var oc = document.querySelector('[onclick*=\"sid=\"]');"
    "if (oc) return String(oc.getAttribute('onclick') || '');"
    "return '';"
)


def normalize_sid_query(raw_sid: str) -> str:
    """Normalize a raw SpaceInvasion session identifier into `sid=<token>` or empty string."""
    cleaned = str(raw_sid or "").strip().lstrip("?&")
    if not cleaned:
        return ""
    if cleaned.lower().startswith("sid="):
        token = cleaned[4:].strip()
        return f"sid={token}" if token else ""
    return f"sid={cleaned}"


def frame_matches_expected_action(expected_action: str | None, href: str, html: str = "") -> bool:
    """Return True when the frame URL/HTML corresponds to `expected_action`."""
    if not expected_action or expected_action in href:
        return True
    href_l = href.lower()
    if expected_action == "internalSendFleet" and "fleeterr." in href_l:
        return True
    return (
        expected_action == "internalTransmitter"
        and "indexinternal.es" in href_l
        and ('id="tm.form"' in html or 'value="internalTransmitter"' in html or "action=internalTransmitter" in html)
    )


class FrameNavigator:
    """Manages SpaceInvasion 3-iframe navigation, readiness polling, SID lookup, and menu clicks."""

    def __init__(self, state: ClientSessionState, guards: PageGuardChecker) -> None:
        self._state = state
        self._guards = guards

    def switch_frame_impl(self, frame_id: str) -> None:
        with self._state.driver_lock:
            self._state.driver.switch_to.default_content()
            frame = self._state.driver.find_element(By.ID, frame_id)
            self._state.driver.switch_to.frame(frame)

    def switch_frame(self, frame_id: str) -> None:
        if self._state.hooks is not None:
            self._state.hooks._switch_frame(frame_id)
            return
        self.switch_frame_impl(frame_id)

    def mark_frame_navigation(self, frame_id: str) -> str:
        token = secrets.token_hex(6)
        self.switch_frame(frame_id)
        try:
            self._state.driver.execute_script("window.__siNavToken = arguments[0];", token)
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("Setting __siNavToken failed on %s: %s", frame_id, exc)
        return token

    def recover_renderer_timeout_impl(self) -> None:
        recover_renderer_timeout(self._state)

    def recover_renderer_timeout(self) -> None:
        if self._state.hooks is not None:
            self._state.hooks._recover_renderer_timeout()
            return
        self.recover_renderer_timeout_impl()

    def _probe_frame_state(self, frame_id: str) -> tuple[object, str, object]:
        self.switch_frame(frame_id)
        probe: object = None
        try:
            probe = self._state.driver.execute_script(_FRAME_PROBE_JS)
        except SELENIUM_NAV_ERRORS as probe_exc:
            if is_renderer_timeout(probe_exc):
                raise
            probe = None
        if isinstance(probe, dict):
            return probe.get("ready"), str(probe.get("href") or ""), probe.get("token")
        ready = self._state.driver.execute_script("return document.readyState;")
        href = str(self._state.driver.execute_script("return location.href;") or "")
        return ready, href, None

    def try_read_landed_frame(
        self,
        frame_id: str,
        expected_action: str | None,
        *,
        nav_token: str | None = None,
    ) -> str | None:
        """After `window.stop()`, return validated frame HTML if `expected_action` already landed."""
        try:
            ready, href, cur_token = self._probe_frame_state(frame_id)
            if str(ready or "") not in ("interactive", "complete"):
                return None
            if nav_token is not None and cur_token == nav_token:
                return None
            html = self._state.driver.page_source or ""
            if len(html) < 64 or not frame_matches_expected_action(expected_action, href, html):
                return None
        except (RateLimitError, SpaceInvasionAuthError):
            raise
        except SELENIUM_NAV_ERRORS:
            return None
        self._guards.check_page_guards(html)
        self._guards.check_portal_redirect(
            html,
            context_msg="Browser redirected to BitMeUp portal/login while waiting for game frame.",
        )
        return html

    def wait_frame_ready(
        self,
        frame_id: str,
        expected_action: str | None = None,
        *,
        nav_token: str | None = None,
    ) -> str:
        wait_budget = safe_positive_timeout(self._state.timeout, 20.0, min_val=0.05, max_val=300.0)
        deadline = client_monotonic() + wait_budget
        recovered_timeout = False
        while True:
            try:
                ready, href, cur_token = self._probe_frame_state(frame_id)
                is_ready = ready == "complete" or (recovered_timeout and ready == "interactive")
                if is_ready and (nav_token is None or cur_token != nav_token):
                    html = self._state.driver.page_source or ""
                    self._guards.check_page_guards(html)
                    self._guards.check_portal_redirect(
                        html,
                        context_msg="Browser redirected to BitMeUp portal/login while waiting for game frame.",
                    )
                    if frame_matches_expected_action(expected_action, href, html):
                        return html
            except (RateLimitError, SpaceInvasionAuthError):
                raise
            except SELENIUM_NAV_ERRORS as wait_exc:
                if not is_renderer_timeout(wait_exc):
                    raise
                if recovered_timeout:
                    raise SpaceInvasionAuthError(
                        f"Renderer timed out waiting for frame {frame_id} ({expected_action or 'ready'})."
                    ) from wait_exc
                recovered_timeout = True
                self.recover_renderer_timeout()
                landed = self.try_read_landed_frame(frame_id, expected_action, nav_token=nav_token)
                if landed is not None:
                    return landed
                if client_monotonic() >= deadline:
                    deadline = client_monotonic() + min(5.0, wait_budget)
                continue
            if client_monotonic() >= deadline:
                break
            client_sleep(0.2)
        raise SpaceInvasionAuthError(f"Timed out waiting for frame {frame_id} ({expected_action or 'ready'}).")

    def _extract_sid_from_hauptframe(self) -> str:
        try:
            self.switch_frame("F_Hauptframe")
            hf_sid = self._state.driver.execute_script(_HF_SID_JS)
        except SELENIUM_NAV_ERRORS:
            return ""
        if not isinstance(hf_sid, str) or not hf_sid.strip():
            return ""
        hf_clean = hf_sid.strip().lstrip("?&")
        match = _SID_QUERY_RE.search(hf_clean)
        if match:
            return f"sid={match.group(1)}"
        if _SID_CLEAN_RE.fullmatch(hf_clean):
            return normalize_sid_query(hf_clean)
        return ""

    def resolve_sid_query(self) -> str:
        if self._state.hooks is not None:
            return self._state.hooks._resolve_sid_query()
        return self.resolve_sid_query_impl()

    def resolve_sid_query_impl(self) -> str:
        self.switch_frame("F_Navi")
        try:
            raw_sid = self._state.driver.execute_script(
                "return (window.SI_TPL && SI_TPL.menu && SI_TPL.menu.sid) ? SI_TPL.menu.sid : '';"
            )
        except SELENIUM_NAV_ERRORS:
            raw_sid = ""
        if isinstance(raw_sid, str) and (norm := normalize_sid_query(raw_sid)):
            return norm

        try:
            for link in self._state.driver.find_elements(By.CSS_SELECTOR, 'a[href*="sid="]'):
                match = _SID_QUERY_RE.search(str(link.get_attribute("href") or ""))
                if match:
                    return f"sid={match.group(1)}"
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("F_Navi link SID scan failed: %s", exc)

        try:
            nav_href = str(self._state.driver.execute_script("return String(location.href || '');") or "")
            match = _SID_QUERY_RE.search(nav_href)
            if match:
                return f"sid={match.group(1)}"
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("F_Navi location.href SID scan failed: %s", exc)

        try:
            top_url = str(self._state.driver.current_url or "")
            match = _SID_QUERY_RE.search(top_url)
            if match:
                return f"sid={match.group(1)}"
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("Top URL SID scan failed: %s", exc)

        return self._extract_sid_from_hauptframe()

    def build_internal_url(self, query: str) -> str:
        if self._state.hooks is not None:
            return self._state.hooks._build_internal_url(query)
        return self.build_internal_url_impl(query)

    def build_internal_url_impl(self, query: str) -> str:
        base_href = ""
        for frame_id in ("F_Hauptframe", "F_Navi"):
            try:
                self.switch_frame(frame_id)
                href = str(self._state.driver.execute_script("return String(location.href || '');") or "")
                if href.startswith(("http://", "https://")):
                    base_href = href
                    break
            except SELENIUM_NAV_ERRORS as exc:
                _LOGGER.debug("Base href read failed on %s: %s", frame_id, exc)
        if not base_href:
            try:
                top_url = str(self._state.driver.current_url or "")
                if top_url.startswith(("http://", "https://")):
                    base_href = top_url
            except SELENIUM_NAV_ERRORS as exc:
                _LOGGER.debug("Top URL fallback read failed: %s", exc)
        if not base_href:
            base_href = "https://spaceinvasion.bitmeup.com/indexInternal.es"
        return urljoin(base_href, f"indexInternal.es?{query}")

    def open_frame_url_with_retry(
        self,
        target_url: str,
        *,
        expected_action: str,
        frame_id: str = "F_Hauptframe",
    ) -> str:
        if self._state.hooks is not None:
            return self._state.hooks._open_frame_url_with_retry(
                target_url, expected_action=expected_action, frame_id=frame_id
            )
        return self.open_frame_url_with_retry_impl(target_url, expected_action=expected_action, frame_id=frame_id)

    def open_frame_url_with_retry_impl(
        self,
        target_url: str,
        *,
        expected_action: str,
        frame_id: str = "F_Hauptframe",
    ) -> str:
        """Navigate `frame_id` to `target_url` via `location.href`, recovering once on renderer timeout."""
        for attempt in range(2):
            nav_token: str | None = None
            try:
                nav_token = self.mark_frame_navigation(frame_id)
                self.switch_frame(frame_id)
                self._state.driver.execute_script("location.href = arguments[0];", target_url)
                return self.wait_frame_ready(frame_id, expected_action=expected_action, nav_token=nav_token)
            except (RateLimitError, SpaceInvasionAuthError):
                raise
            except SELENIUM_NAV_ERRORS as nav_exc:
                if not is_renderer_timeout(nav_exc):
                    raise
                if attempt >= 1:
                    raise SpaceInvasionAuthError(f"Renderer timed out loading {expected_action}.") from nav_exc
                self.recover_renderer_timeout()
                landed = self.try_read_landed_frame(frame_id, expected_action, nav_token=nav_token)
                if landed is not None:
                    return landed
        raise SpaceInvasionAuthError(f"Renderer timed out loading {expected_action}.")

    def navigate_menu(
        self,
        action: str,
        *,
        priority: str = "normal",
        open_galaxy_fn: Callable[..., str] | None = None,
    ) -> str:
        """Click a navigation link in `F_Navi` and wait for `F_Hauptframe` to load."""
        if action == "internalGalaxy" and open_galaxy_fn is not None:
            return open_galaxy_fn(priority=priority)
        for attempt in range(2):
            self._state.pace(priority=priority)
            nav_token: str | None = None
            try:
                nav_token = self.mark_frame_navigation("F_Hauptframe")
                self.switch_frame("F_Navi")
                link = self._state.driver.find_element(By.CSS_SELECTOR, f'a[href*="action={action}"]')
                self._state.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
                try:
                    link.click()
                except ElementClickInterceptedException:
                    self._state.driver.execute_script("arguments[0].click();", link)
                return self.wait_frame_ready("F_Hauptframe", expected_action=action, nav_token=nav_token)
            except (RateLimitError, SpaceInvasionAuthError):
                raise
            except SELENIUM_NAV_ERRORS as nav_exc:
                if not is_renderer_timeout(nav_exc):
                    raise
                if attempt >= 1:
                    raise SpaceInvasionAuthError(f"Renderer timed out navigating to {action}.") from nav_exc
                self.recover_renderer_timeout()
                landed = self.try_read_landed_frame("F_Hauptframe", action, nav_token=nav_token)
                if landed is not None:
                    return landed
        raise SpaceInvasionAuthError(f"Renderer timed out navigating to {action}.")

    def open_building_detail(self, building_id: int, *, priority: str = "interactive") -> str:
        """Open `internalDetail&type=building&id=<building_id>` in `F_Hauptframe`."""
        bid = max(1, cap_json_int(coerce_int(building_id, 1)))
        self._state.pace(priority=priority)
        sid_query = self.resolve_sid_query()
        if sid_query:
            target_url = self.build_internal_url(f"action=internalDetail&type=building&id={bid}&{sid_query}")
            return self.open_frame_url_with_retry(target_url, expected_action="internalDetail")
        self.navigate_menu("internalBuildings", priority=priority)
        nav_token = self.mark_frame_navigation("F_Hauptframe")
        self.switch_frame("F_Hauptframe")
        link = self._state.driver.find_element(By.CSS_SELECTOR, f'a[href*="action=internalDetail"][href*="id={bid}"]')
        self._state.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
        try:
            link.click()
        except ElementClickInterceptedException:
            self._state.driver.execute_script("arguments[0].click();", link)
        return self.wait_frame_ready("F_Hauptframe", expected_action="internalDetail", nav_token=nav_token)

    def page_source_safe(self) -> str:
        with self._state.driver_lock:
            html = str(self._state.driver.page_source or "")
        self._guards.check_page_guards(html)
        return html
