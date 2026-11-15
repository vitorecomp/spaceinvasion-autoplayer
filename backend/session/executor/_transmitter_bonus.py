"""Daily Login Bonus popup collection helpers on Command Central (`internalHome`)."""

from __future__ import annotations

from collections.abc import Mapping
import contextlib

from selenium.webdriver.common.by import By

from backend.parsing.account_pages import parse_daily_login_bonus
from backend.session.client._protocols import SELENIUM_NAV_ERRORS
from backend.session.executor._dom import mark_frame_nav, safe_click
from backend.session.executor._planet_state import (
    pace_client_action,
    try_build_internal_url,
    try_resolve_sid_query,
)
from backend.session.executor._protocols import BrowserClientLike, ExecutorContext

_SELECT_TPID_JS = (
    "var sel = document.getElementById('tpid') || document.querySelector('select[name=\"tpid\"]');"
    "if (!sel || !sel.options) return false;"
    "var tgt = String(arguments[0]);"
    "for (var i = 0; i < sel.options.length; i++) {"
    "  if (String(sel.options[i].value) === tgt) {"
    "    sel.selectedIndex = i;"
    "    sel.value = tgt;"
    "    return true;"
    "  }"
    "}"
    "return false;"
)
_SEND_DAILY_LOGIN_JS = "if (typeof sendDailyLogin === 'function') { sendDailyLogin(); return true; }return false;"
_BONUS_TRIGGER_SELECTORS: tuple[str, ...] = (
    '[onclick*="sendDailyLogin"]',
    '[onclick*="getLoginBonus=1"]',
    'a[href*="getLoginBonus=1"]',
)


def _resolve_target_tpid(hw_pid: int | None, daily_info: Mapping[str, object]) -> int | None:
    raw_opts = daily_info.get("tpid_options")
    tpid_opts = raw_opts if isinstance(raw_opts, (list, tuple)) else ()
    if hw_pid is not None and hw_pid > 0 and (not tpid_opts or hw_pid in tpid_opts):
        return hw_pid
    def_tpid = daily_info.get("default_tpid")
    if isinstance(def_tpid, int) and not isinstance(def_tpid, bool) and def_tpid > 0:
        return def_tpid
    return None


def _select_tpid_js(client: BrowserClientLike, target_tpid: int | None) -> None:
    if target_tpid is None:
        return
    with contextlib.suppress(*SELENIUM_NAV_ERRORS):
        client.driver.execute_script(_SELECT_TPID_JS, str(target_tpid))


def _click_daily_bonus_dom_trigger(client: BrowserClientLike) -> bool:
    for sel in _BONUS_TRIGGER_SELECTORS:
        with contextlib.suppress(*SELENIUM_NAV_ERRORS):
            candidates = client.driver.find_elements(By.CSS_SELECTOR, sel)
            if not isinstance(candidates, list):
                continue
            for cand in candidates:
                attr_txt = (
                    str(cand.get_attribute("onclick") or "") + " " + str(cand.get_attribute("href") or "")
                ).lower()
                if "hideloginbonus" in attr_txt:
                    continue
                safe_click(client, cand)
                return True
    return False


def _trigger_send_daily_login_js(client: BrowserClientLike, target_tpid: int | None) -> bool:
    with contextlib.suppress(*SELENIUM_NAV_ERRORS):
        if client.driver.execute_script(_SEND_DAILY_LOGIN_JS) is True:
            return True
    sid_q = try_resolve_sid_query(client).strip()
    tpid_part = f"&tpid={target_tpid}" if target_tpid is not None else ""
    sid_part = f"&{sid_q}" if sid_q else ""
    url = try_build_internal_url(client, f"action=internalHome&getLoginBonus=1{tpid_part}{sid_part}")
    if url:
        with contextlib.suppress(*SELENIUM_NAV_ERRORS):
            client.driver.execute_script("location.href = arguments[0];", url)
            return True
    return False


def collect_daily_login_bonus_impl(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    hw_pid: int | None,
    daily_info: Mapping[str, object],
) -> tuple[bool, str]:
    """Collect the Command Central (`internalHome`) Daily Login Bonus popup targeting Homeworld `#tpid`."""
    target_tpid = _resolve_target_tpid(hw_pid, daily_info)
    after_html = ""
    for attempt in range(2):
        with contextlib.suppress(AttributeError):
            client._switch_frame("F_Hauptframe")
        _select_tpid_js(client, target_tpid)
        pace_client_action(ctx, client, priority="interactive")
        nav_tok = mark_frame_nav(client, "F_Hauptframe")

        triggered = _click_daily_bonus_dom_trigger(client) if attempt == 0 else False
        if not triggered:
            triggered = _trigger_send_daily_login_js(client, target_tpid)
        if not triggered:
            break

        try:
            after_html = client._wait_frame_ready("F_Hauptframe", expected_action="internalHome", nav_token=nav_tok)
        except AttributeError:
            after_html = str(client.driver.page_source or "")

        after_str = after_html if isinstance(after_html, str) else ""
        if not parse_daily_login_bonus(after_str).get("present", False):
            return True, after_str

    return False, (after_html if isinstance(after_html, str) else "")
