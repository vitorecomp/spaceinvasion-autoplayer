"""Inbox navigation, collapsed report expansion, and persisted message deletion (`MessageInboxOps`)."""

from __future__ import annotations

from collections.abc import Sequence
import logging
import re
from typing import Protocol, runtime_checkable

from selenium.common.exceptions import ElementClickInterceptedException
from selenium.webdriver.common.by import By

from backend.core.coercion import coerce_int
from backend.session.auth import AntiBotChallengeError, SpaceInvasionAuthError
from backend.session.client._guards_diag import SupportsScriptTimeout
from backend.session.client._messages_js import (
    CLICK_COLLAPSED_TRIGGERS_JS,
    EXEC_DELETE_JS,
    EXPAND_MESSAGES_ASYNC_JS,
    PROBE_DELETE_MODE_JS,
)
from backend.session.client._protocols import SELENIUM_NAV_ERRORS, WebElementLike, client_sleep
from backend.session.client._state import ClientSessionState
from backend.session.client.frames import FrameNavigator
from backend.session.limiter import RateLimitError

_LOGGER = logging.getLogger(__name__)
_DELETE_WAIT_FALLBACK_ERRORS: tuple[type[BaseException], ...] = (SpaceInvasionAuthError,) + SELENIUM_NAV_ERRORS


@runtime_checkable
class _SupportsAsyncScript(Protocol):
    def execute_async_script(self, script: str, *args: object) -> object: ...


class MessageInboxOps:
    """Manages SpaceInvasion `internalMessages` folder opening, report expansion, and deletion."""

    def __init__(self, state: ClientSessionState, frames: FrameNavigator) -> None:
        self._state = state
        self._frames = frames

    def _open_messages_via_menu_click(self, clean_type: str) -> str:
        nav_token = self._frames.mark_frame_navigation("F_Hauptframe")
        self._frames.switch_frame("F_Navi")
        selectors: tuple[str, ...] = (
            (
                f'a[href*="action=internalMessages"][href*="type={clean_type}"]',
                'a[href*="action=internalMessages"]',
            )
            if (clean_type and clean_type.lower() != "all")
            else ('a[href*="action=internalMessages"]',)
        )
        link: WebElementLike | None = None
        for css in selectors:
            try:
                els = self._state.driver.find_elements(By.CSS_SELECTOR, css)
                if els:
                    link = els[0]
                    break
            except SELENIUM_NAV_ERRORS as exc:
                _LOGGER.debug("Message menu selector %s failed: %s", css, exc)
        if link is None:
            link = self._state.driver.find_element(By.CSS_SELECTOR, 'a[href*="action=internalMessages"]')
        self._state.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
        try:
            link.click()
        except ElementClickInterceptedException:
            self._state.driver.execute_script("arguments[0].click();", link)
        return self._frames.wait_frame_ready("F_Hauptframe", expected_action="internalMessages", nav_token=nav_token)

    def _select_message_subtype_if_needed(self, clean_type: str, html: str) -> str:
        if not clean_type or clean_type.lower() == "all":
            return html
        try:
            self._frames.switch_frame("F_Hauptframe")
            cur_loc = str(self._state.driver.execute_script("return String(location.href || '');") or "")
            if f"type={clean_type}" in cur_loc:
                return html
            sub_el: WebElementLike | None = None
            for sub_css in (
                f'[onclick*="action=internalMessages"][onclick*="type={clean_type}"]',
                f'a[href*="action=internalMessages"][href*="type={clean_type}"]',
            ):
                els = self._state.driver.find_elements(By.CSS_SELECTOR, sub_css)
                if els:
                    sub_el = els[0]
                    break
            if sub_el is None:
                return html
            sub_tok = self._frames.mark_frame_navigation("F_Hauptframe")
            try:
                sub_el.click()
            except ElementClickInterceptedException:
                self._state.driver.execute_script("arguments[0].click();", sub_el)
            return self._frames.wait_frame_ready("F_Hauptframe", expected_action="internalMessages", nav_token=sub_tok)
        except (RateLimitError, SpaceInvasionAuthError):
            raise
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("Message subtype click skipped: %s", exc)
            return html

    def open_messages(self, msg_type: str = "spio", *, priority: str = "interactive") -> str:
        """Open `internalMessages` (e.g. `type=spio` espionage reports or main inbox) in `F_Hauptframe`."""
        raw_type = str(msg_type) if msg_type is not None else "spio"
        clean_type = re.sub(r"[^a-zA-Z0-9_-]", "", raw_type.strip())
        self._state.pace(priority=priority)
        sid_query = self._frames.resolve_sid_query()
        if sid_query:
            q_str = (
                f"action=internalMessages&type={clean_type}&{sid_query}"
                if (clean_type and clean_type.lower() != "all")
                else f"action=internalMessages&{sid_query}"
            )
            return self._frames.open_frame_url_with_retry(
                self._frames.build_internal_url(q_str),
                expected_action="internalMessages",
            )
        html = self._open_messages_via_menu_click(clean_type)
        return self._select_message_subtype_if_needed(clean_type, html)

    def _compute_expand_fetch_cap(self, max_fetches: int) -> int:
        cap_fetches = max(1, min(25, coerce_int(max_fetches, 12)))
        try:
            avail = max(1, self._state.page_limiter.available_budget("normal") - 2)
            return max(1, min(cap_fetches, avail))
        except SELENIUM_NAV_ERRORS:
            return cap_fetches

    def _run_async_message_fetches(self, sid_query: str, cap_fetches: int) -> tuple[int, int]:
        driver = self._state.driver
        if not isinstance(driver, _SupportsAsyncScript):
            return 0, 0
        if isinstance(driver, SupportsScriptTimeout):
            try:
                driver.set_script_timeout(30.0)
            except SELENIUM_NAV_ERRORS as exc:
                _LOGGER.debug("set_script_timeout(30) skipped: %s", exc)
        try:
            ares = driver.execute_async_script(EXPAND_MESSAGES_ASYNC_JS, sid_query, cap_fetches)
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("Async message expansion skipped: %s", exc)
            return 0, 0
        if isinstance(ares, dict):
            return coerce_int(ares.get("expanded"), 0), coerce_int(ares.get("fetches"), 0)
        return 0, 0

    def expand_collapsed_messages_on_page(self, *, max_fetches: int = 12) -> str | None:
        """Expand collapsed AJAX/sub-report message rows on `internalMessages` (`spio`, `battle`, `fleet`, etc.)."""
        sid_query = ""
        try:
            sid_query = self._frames.resolve_sid_query()
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("SID lookup before message expansion failed: %s", exc)
        cap_fetches = self._compute_expand_fetch_cap(max_fetches)

        expanded = 0
        fetches = 0
        with self._state.driver_lock:
            try:
                self._frames.switch_frame("F_Hauptframe")
                res = self._state.driver.execute_script(CLICK_COLLAPSED_TRIGGERS_JS)
                if isinstance(res, int) and not isinstance(res, bool) and res > 0:
                    expanded += res
            except SELENIUM_NAV_ERRORS:
                return None
            async_exp, async_fetches = self._run_async_message_fetches(sid_query, cap_fetches)
            expanded += async_exp
            fetches += async_fetches

        if fetches > 0:
            try:
                self._state.page_limiter.record_view(fetches)
            except SELENIUM_NAV_ERRORS as exc:
                _LOGGER.debug("Recording message expand views failed: %s", exc)

        if expanded <= 0:
            return None
        if fetches <= 0:
            client_sleep(0.25)
        with self._state.driver_lock:
            try:
                self._frames.switch_frame("F_Hauptframe")
                return str(self._state.driver.page_source or "")
            except SELENIUM_NAV_ERRORS:
                return None

    def _probe_delete_mode(self, clean_ids: list[str], all_on_page_persisted: bool) -> tuple[str, str]:
        current_html = ""
        with self._state.driver_lock:
            try:
                self._frames.switch_frame("F_Hauptframe")
                current_html = str(self._state.driver.page_source or "")
                res_mode = self._state.driver.execute_script(
                    PROBE_DELETE_MODE_JS, clean_ids, bool(all_on_page_persisted)
                )
                return (res_mode if isinstance(res_mode, str) else ""), current_html
            except SELENIUM_NAV_ERRORS:
                return "", current_html

    def _trigger_delete_action(self, clean_ids: list[str], all_on_page_persisted: bool, mode_found: str) -> bool:
        with self._state.driver_lock:
            try:
                self._frames.switch_frame("F_Hauptframe")
                return bool(
                    self._state.driver.execute_script(
                        EXEC_DELETE_JS, clean_ids, bool(all_on_page_persisted), mode_found
                    )
                )
            except SELENIUM_NAV_ERRORS:
                return False

    def delete_messages_on_current_page(
        self,
        server_msg_ids: Sequence[str] = (),
        *,
        all_on_page_persisted: bool = False,
        priority: str = "normal",
    ) -> tuple[bool, str]:
        """Delete already-persisted messages on `internalMessages` inside `F_Hauptframe`."""
        clean_ids = [
            str(mid).strip() for mid in (server_msg_ids or ()) if str(mid).strip() and str(mid).strip().isascii()
        ]
        if not clean_ids and not all_on_page_persisted:
            return (False, "")

        mode_found, current_html = self._probe_delete_mode(clean_ids, all_on_page_persisted)
        if not mode_found:
            return (False, current_html)

        self._state.pace(priority=priority)
        nav_token = self._frames.mark_frame_navigation("F_Hauptframe")
        if not self._trigger_delete_action(clean_ids, all_on_page_persisted, mode_found):
            return (False, current_html)
        try:
            new_html = self._frames.wait_frame_ready(
                "F_Hauptframe", expected_action="internalMessages", nav_token=nav_token
            )
            return (True, new_html)
        except (RateLimitError, AntiBotChallengeError):
            raise
        except _DELETE_WAIT_FALLBACK_ERRORS:
            with self._state.driver_lock:
                try:
                    self._frames.switch_frame("F_Hauptframe")
                    return (True, str(self._state.driver.page_source or ""))
                except SELENIUM_NAV_ERRORS:
                    return (True, current_html)
