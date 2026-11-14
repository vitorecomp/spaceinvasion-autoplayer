"""Portal universe discovery, DOM clicking, and universe-entry wait helpers."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any, Optional, Protocol

from spaceinvasion.core import catalog as game_data
from spaceinvasion.session._browser_driver import (
    BROWSER_DOM_ERRORS,
    By,
    _finite_bounded_timeout,
)
from spaceinvasion.session._browser_tabs import (
    _element_if_visible,
    _is_portal_authenticated,
    find_and_switch_game_tab,
)
from spaceinvasion.session.auth import (
    BASE_URL,
    AntiBotChallengeError,
    UniverseOption,
    detect_antibot_challenge,
    extract_universes,
    universes_from_portal_data,
)


class BrowserConfigLike(Protocol):
    """Minimal protocol for `BrowserConfig` consumed by portal helpers."""

    @property
    def headless(self) -> bool: ...

    @property
    def timeout(self) -> float: ...

    @property
    def typing_delay_range(self) -> tuple[float, float]: ...


def _resolve_universe_choice(
    universes: tuple[UniverseOption, ...],
    target: str,
) -> Optional[UniverseOption]:
    """Match a configured universe name or numeric ID case-insensitively."""
    needle = target.strip().lower()
    for uni in universes:
        if uni.universe_id is not None and game_data.fmt_compact_int(uni.universe_id) == needle:
            return uni
        if uni.label.strip().lower() == needle:
            return uni
    for uni in universes:
        if needle in uni.label.strip().lower():
            return uni
    return None


def _handle_antibot_or_raise(
    driver: Any,
    config: BrowserConfigLike,
    wait_for_manual_gate: Optional[Callable[[str], None]],
) -> None:
    """Stop automation if a CAPTCHA/anti-bot gate appears; let user act in visible UI."""
    html = driver.page_source or ""
    issue = detect_antibot_challenge(200, html)
    if not issue:
        return
    if not config.headless and wait_for_manual_gate is not None:
        wait_for_manual_gate(f"{issue}. Complete the verification directly in the Chrome window")
        still_blocked = detect_antibot_challenge(200, driver.page_source or "")
        if not still_blocked:
            return
    raise AntiBotChallengeError(f"{issue}. Automation stopped; complete verification manually in a browser.")


def _extract_portal_universes(driver: Any, wait_seconds: float = 2.5) -> tuple[UniverseOption, ...]:
    """Read portal universes from live `window.SI_TPL.startPortal` JS or HTML."""
    deadline = time.monotonic() + _finite_bounded_timeout(wait_seconds, default=2.5, min_val=0.0, max_val=60.0)
    while True:
        try:
            js_data = driver.execute_script(
                "var p = window.SI_TPL && window.SI_TPL.startPortal;"
                "if (!p || !Array.isArray(p.instances) || p.instances.length === 0) return null;"
                "return {sid: String(p.SID || ''), instances: p.instances};"
            )
        except BROWSER_DOM_ERRORS:
            js_data = None

        cur_url = getattr(driver, "current_url", "") or BASE_URL
        raw_instances = js_data.get("instances") if isinstance(js_data, dict) else None
        if isinstance(raw_instances, (list, tuple)) and raw_instances:
            parsed = universes_from_portal_data(
                game_data.safe_key(js_data.get("sid") or ""),
                list(raw_instances),
                cur_url,
            )
            if parsed:
                return parsed

        parsed_html = extract_universes(getattr(driver, "page_source", "") or "", cur_url)
        if parsed_html:
            return parsed_html

        if time.monotonic() >= deadline or not _is_portal_authenticated(driver):
            return ()
        time.sleep(0.2)


_CLICK_PORTAL_UNIVERSE_JS = """
var needle = arguments[0];
var uidStr = arguments[1] !== null ? String(arguments[1]) : null;
var nodes = Array.from(document.querySelectorAll('a, button, [role="button"], .sip-uni, [class*="instance"], [class*="universe"], tr, li, div'));
for (var i = 0; i < nodes.length; i++) {
  var el = nodes[i];
  var txt = (el.innerText || el.textContent || '').trim().toLowerCase();
  if (!txt || txt.indexOf(needle) === -1) continue;
  var rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
  if (rect && (rect.width === 0 || rect.height === 0)) continue;
  var target = el.querySelector && (
    el.querySelector('a[href*="externalLogin"], a[href*="instance="], button, a, .play, [class*="play"]')
  );
  if (target) {
    target.click();
    return true;
  }
  if (el.tagName === 'A' || el.tagName === 'BUTTON' || el.getAttribute('onclick') || (uidStr && (el.innerHTML || '').indexOf(uidStr) !== -1)) {
    el.click();
    return true;
  }
}
return false;
"""


def _click_portal_universe_dom(driver: Any, matched: UniverseOption) -> bool:
    """Try clicking the universe card or Play button directly in the portal DOM."""
    label_needle = matched.label.strip().lower()
    uid = matched.universe_id
    selectors: list[str] = []
    if uid is not None:
        selectors.extend(
            [
                f'a[href*="instance={uid}"]',
                f'[data-instance="{uid}"] a',
                f'[data-instance="{uid}"] button',
                f'[data-instance="{uid}"]',
                f'[onclick*="instance={uid}"]',
                f'[onclick*="({uid})"]',
                f'[onclick*="({uid},"]',
            ]
        )
    for sel in selectors:
        try:
            el = driver.find_element(By.CSS_SELECTOR, sel)
            if el is not None:
                try:
                    el.click()
                except BROWSER_DOM_ERRORS:
                    driver.execute_script("arguments[0].click();", el)
                return True
        except BROWSER_DOM_ERRORS:
            continue

    try:
        return bool(driver.execute_script(_CLICK_PORTAL_UNIVERSE_JS, label_needle, uid))
    except BROWSER_DOM_ERRORS:
        return False


def _wait_for_universe_entry(
    driver: Any,
    config: BrowserConfigLike,
    wait_for_manual_gate: Optional[Callable[[str], None]],
    *,
    previous_handles: Optional[list[str]] = None,
) -> bool:
    """Wait until universe navigation settles on `indexGame.es` (across open tabs)."""
    deadline = time.monotonic() + _finite_bounded_timeout(config.timeout, default=15.0, min_val=0.05, max_val=15.0)
    prev_set = set(previous_handles or [])
    while time.monotonic() < deadline:
        _handle_antibot_or_raise(driver, config, wait_for_manual_gate)
        if find_and_switch_game_tab(driver):
            return True

        handles = list(getattr(driver, "window_handles", None) or [])
        for handle in handles:
            if handle in prev_set:
                continue
            try:
                driver.switch_to.window(handle)
                cur_new = (getattr(driver, "current_url", "") or "").lower()
                if "indexgame.es" in cur_new or "uni" in cur_new:
                    if find_and_switch_game_tab(driver) or "action=externallogin" not in cur_new:
                        return True
            except BROWSER_DOM_ERRORS:
                continue

        cur = (getattr(driver, "current_url", "") or "").lower()
        if "indexgame.es" in cur or "uni" in cur:
            if _element_if_visible(driver, By.ID, "F_Hauptframe") is not None:
                return True
            if "action=externallogin" not in cur:
                return True
        time.sleep(0.2)
    return False


__all__ = [
    "BrowserConfigLike",
    "_click_portal_universe_dom",
    "_extract_portal_universes",
    "_handle_antibot_or_raise",
    "_resolve_universe_choice",
    "_wait_for_universe_entry",
]
