"""Diagnostics, anti-bot DOM visibility probe, shell probing, and renderer timeout helpers."""

from __future__ import annotations

import logging
import time
from typing import Protocol, runtime_checkable

from bs4 import BeautifulSoup, Tag
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By

from spaceinvasion.session.auth import SpaceInvasionAuthError
from spaceinvasion.session.browser import find_and_switch_game_tab
from spaceinvasion.session.browser_client._protocols import (
    _EMPTY_PAGEVIEW_MODAL_RE,
    SELENIUM_NAV_ERRORS,
    is_real_selenium_driver,
    safe_positive_timeout,
)
from spaceinvasion.session.browser_client._state import ClientSessionState
from spaceinvasion.session.limiter import PageViewLimiter, RateLimitError

_LOGGER = logging.getLogger(__name__)

_ANTIBOT_VISIBILITY_JS: str = (
    "function isVis(el) {"
    "  if (!el) return false;"
    "  var cur = el;"
    "  while (cur && cur.nodeType === 1) {"
    "    if (cur.hidden || cur.getAttribute('aria-hidden') === 'true') return false;"
    "    var st = window.getComputedStyle(cur);"
    "    if (!st || st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;"
    "    cur = cur.parentElement;"
    "  }"
    "  var r = el.getBoundingClientRect();"
    "  return r.width > 0 && r.height > 0;"
    "}"
    "var sels = ['#si-captcha-gate', '.cf-turnstile', '.cf-challenge', '.g-recaptcha', '.h-captcha',"
    "  'iframe[src*=\"challenges.cloudflare.com\"]'];"
    "for (var i = 0; i < sels.length; i++) {"
    "  var nodes = document.querySelectorAll(sels[i]);"
    "  for (var j = 0; j < nodes.length; j++) {"
    "    if (isVis(nodes[j])) return true;"
    "  }"
    "}"
    "var t = (document.title || '').toLowerCase();"
    "if (t.indexOf('just a moment') !== -1 || t.indexOf('attention required!') !== -1) return true;"
    "var bodyTxt = (document.body && document.body.innerText ? document.body.innerText : '').toLowerCase();"
    "if (bodyTxt.indexOf('sicherheitsprüfung') !== -1 || bodyTxt.indexOf('sicherheitspruefung') !== -1"
    "  || bodyTxt.indexOf('confirm that you are not a bot') !== -1"
    "  || bodyTxt.indexOf('dass du kein bot bist') !== -1) return true;"
    "return false;"
)


@runtime_checkable
class SupportsScriptTimeout(Protocol):
    """Structural protocol for WebDriver objects supporting `set_script_timeout`."""

    def set_script_timeout(self, time_to_wait: float) -> None: ...


def build_rate_limit_error(
    limiter: PageViewLimiter,
    message: str,
    *,
    retry_after: float,
    server_throttle: bool = True,
    record_limiter_id: bool = True,
) -> RateLimitError:
    """Create a `RateLimitError` optionally tagged with `recorded_limiter_id`."""
    err = RateLimitError(message, retry_after=retry_after, server_throttle=server_throttle)
    if record_limiter_id:
        err.recorded_limiter_id = id(limiter)
    return err


def is_renderer_timeout(exc: BaseException) -> bool:
    """Return True when `exc` is a ChromeDriver/Selenium renderer or script timeout."""
    if isinstance(exc, TimeoutException):
        return True
    low = str(exc or "").lower()
    return "timed out receiving message from renderer" in low or "timeout: timed out" in low or "script timeout" in low


def recover_renderer_timeout(state: ClientSessionState) -> None:
    """Abort stuck subresource/page loads (`window.stop()`) to unblock ChromeDriver's renderer queue."""
    restore_timeout = safe_positive_timeout(state.timeout, 20.0, min_val=5.0, max_val=300.0)
    driver = state.driver
    timeout_driver = driver if isinstance(driver, SupportsScriptTimeout) else None
    if timeout_driver is not None:
        try:
            timeout_driver.set_script_timeout(3.0)
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("Failed to lower script timeout during renderer recovery: %s", exc)
    try:
        try:
            driver.execute_script("try { window.stop(); } catch (e) {}")
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("window.stop() in current frame failed: %s", exc)
        try:
            driver.switch_to.default_content()
            driver.execute_script("try { window.stop(); } catch (e) {}")
        except SELENIUM_NAV_ERRORS as exc:
            _LOGGER.debug("window.stop() in default_content failed: %s", exc)
    finally:
        if timeout_driver is not None:
            try:
                timeout_driver.set_script_timeout(restore_timeout)
            except SELENIUM_NAV_ERRORS as exc:
                _LOGGER.debug("Failed to restore script timeout: %s", exc)
    time.sleep(0.25)


def is_antibot_visible_in_live_dom(state: ClientSessionState) -> bool | None:
    """Read-only visibility check for real Selenium drivers to ignore hidden/dismissed modal residue."""
    if not is_real_selenium_driver(state.driver):
        return None
    try:
        res = state.driver.execute_script(_ANTIBOT_VISIBILITY_JS)
        return res if isinstance(res, bool) else None
    except SELENIUM_NAV_ERRORS as exc:
        _LOGGER.debug("Live DOM antibot visibility check failed: %s", exc)
        return None


def _extract_visible_text_preview(cleaned_html: str) -> tuple[str, str]:
    try:
        soup = BeautifulSoup(cleaned_html, "html.parser")
        for hidden_el in soup.find_all(["script", "style", "template", "noscript"]):
            hidden_el.decompose()
        for el in list(soup.find_all(True)):
            if not isinstance(el, Tag) or el.attrs is None:
                continue
            if el.has_attr("hidden") or str(el.get("aria-hidden") or "").strip().lower() == "true":
                el.decompose()
                continue
            st_inline = str(el.get("style") or "").lower().replace(" ", "")
            if "display:none" in st_inline or "visibility:hidden" in st_inline:
                el.decompose()
        return "visible_text_preview", " ".join(soup.get_text(" ", strip=True).split())[:1000]
    except (ValueError, TypeError, AttributeError):
        return "raw_preview", cleaned_html[:1000]


def capture_dom_diag(state: ClientSessionState, html: str = "") -> dict[str, object]:
    """Collect bounded diagnostic metadata for anti-bot and rate-limit warnings."""
    diag: dict[str, object] = {
        "page_views_last_60s": state.page_limiter.views_last_60s(),
        "rate_limit_remaining": state.page_limiter.rate_limit_remaining(),
    }
    try:
        diag["top_url"] = str(state.driver.current_url or "")
    except SELENIUM_NAV_ERRORS as exc:
        diag["top_url_err"] = str(exc)
    try:
        handles = state.driver.window_handles
        diag["window_handles_count"] = len(handles) if isinstance(handles, (list, tuple)) else None
    except SELENIUM_NAV_ERRORS as exc:
        _LOGGER.debug("Failed reading window_handles in dom diag: %s", exc)
    try:
        diag["frame_href"] = str(state.driver.execute_script("return String(location.href || '');") or "")
    except SELENIUM_NAV_ERRORS as exc:
        _LOGGER.debug("Failed reading frame_href in dom diag: %s", exc)
    if html:
        cleaned = _EMPTY_PAGEVIEW_MODAL_RE.sub("", html)
        low = cleaned.lower()
        idx_m = low.find("made more than")
        idx_p = low.find("page views")
        diag["html_len"] = len(html)
        diag["matched_made_more_than"] = cleaned[max(0, idx_m - 160) : idx_m + 320] if idx_m >= 0 else None
        diag["matched_page_views"] = cleaned[max(0, idx_p - 160) : idx_p + 320] if idx_p >= 0 else None
        key, preview = _extract_visible_text_preview(cleaned)
        diag[key] = preview
    return diag


def dom_recovered_before_record(state: ClientSessionState, dom_epoch_seen: int) -> bool:
    """Re-check under `driver_lock` (without holding limiter lock) if DOM recovered."""
    with state.driver_lock:
        if state.dom_healthy_epoch != dom_epoch_seen:
            return True
        try:
            state.driver.switch_to.default_content()
            f_planets = state.driver.find_element(By.ID, "F_planets")
            state.driver.find_element(By.ID, "F_Hauptframe")
            state.driver.find_element(By.ID, "F_Navi")
            state.driver.switch_to.frame(f_planets)
            state.driver.find_element(By.ID, "json-container")
            state.dom_healthy_epoch += 1
            return True
        except NoSuchElementException:
            return False
        except StaleElementReferenceException:
            return True
        finally:
            try:
                state.driver.switch_to.default_content()
            except SELENIUM_NAV_ERRORS as exc:
                _LOGGER.debug("default_content cleanup failed after DOM re-check: %s", exc)


def probe_game_shell(
    state: ClientSessionState,
    renderer_recoveries: int,
) -> tuple[bool, str, str, NoSuchElementException | None, int, bool, int]:
    """Probe the 3-iframe game shell and `#json-container` inside `F_planets`."""
    in_planets_frame = False
    frame_html = ""
    top_shell_html = ""
    missing_exc: NoSuchElementException | None = None
    dom_epoch_seen = state.dom_healthy_epoch
    renderer_timed_out = False
    with state.driver_lock:
        try:
            state.driver.switch_to.default_content()
            try:
                top_src = state.driver.page_source
                top_shell_html = top_src if isinstance(top_src, str) else ""
            except SELENIUM_NAV_ERRORS as src_exc:
                if is_renderer_timeout(src_exc):
                    raise
                top_shell_html = ""
            try:
                f_planets = state.driver.find_element(By.ID, "F_planets")
                state.driver.find_element(By.ID, "F_Hauptframe")
                state.driver.find_element(By.ID, "F_Navi")
                state.driver.switch_to.frame(f_planets)
                in_planets_frame = True
                state.driver.find_element(By.ID, "json-container")
                state.driver.switch_to.default_content()
                state.dom_healthy_epoch += 1
            except NoSuchElementException as exc:
                missing_exc = exc
                dom_epoch_seen = state.dom_healthy_epoch
                try:
                    frame_html = str(state.driver.page_source or "")
                except SELENIUM_NAV_ERRORS:
                    frame_html = ""
        except (RateLimitError, SpaceInvasionAuthError):
            raise
        except SELENIUM_NAV_ERRORS as drv_exc:
            if not is_renderer_timeout(drv_exc):
                raise
            if renderer_recoveries >= 1:
                raise SpaceInvasionAuthError("Renderer timed out while verifying game session.") from drv_exc
            renderer_recoveries += 1
            recover_renderer_timeout(state)
            renderer_timed_out = True
    return (
        in_planets_frame,
        frame_html,
        top_shell_html,
        missing_exc,
        dom_epoch_seen,
        renderer_timed_out,
        renderer_recoveries,
    )


def try_switch_popup_tab(state: ClientSessionState) -> bool:
    """Switch focus back from a popup window (e.g. `p_galaxy()`) to the `indexGame.es` tab."""
    with state.driver_lock:
        try:
            handles = list(state.driver.window_handles or ())
            return len(handles) > 1 and bool(find_and_switch_game_tab(state.driver))
        except SELENIUM_NAV_ERRORS:
            return False
