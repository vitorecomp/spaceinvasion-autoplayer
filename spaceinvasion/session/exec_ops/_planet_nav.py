"""Decomposed planet-dropdown switching and menu navigation helpers for `PlanetSelector`."""

from __future__ import annotations

from dataclasses import dataclass
import re

from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

from spaceinvasion.core import catalog
from spaceinvasion.parsing.models import GameState
from spaceinvasion.session.browser_client._protocols import (
    SELENIUM_NAV_ERRORS,
    WebElementLike,
    is_real_selenium_driver,
)
from spaceinvasion.session.exec_ops._dom import find_newplanet_switch_target, mark_frame_nav, safe_click
from spaceinvasion.session.exec_ops._planet_state import (
    confirmed_planet_mismatch_id,
    extract_selected_planet_id,
    pace_client_action,
    record_observed_planet,
)
from spaceinvasion.session.exec_ops._protocols import BrowserClientLike, ExecutorContext
from spaceinvasion.session.exec_ops._types import call_with_interactive_priority
from spaceinvasion.session.limiter import is_server_rate_limit_html

_ANY_NEWPLANET_RE = re.compile(r"newplanet=(\d+)", re.IGNORECASE | re.ASCII)
_HIDDEN_SELECT_SWITCH_JS = (
    "var sel = arguments[0], val = arguments[1]; "
    "if (!sel || !val || !sel.options) return false; "
    "var matched = false; "
    "for (var i = 0; i < sel.options.length; i++) { "
    "  if (sel.options[i].value === val) { sel.selectedIndex = i; matched = true; break; } "
    "} "
    "if (!matched) return false; "
    "if (typeof sel.onchange === 'function') { sel.onchange(); return true; } "
    "sel.dispatchEvent(new Event('change', {bubbles: true})); "
    "return true;"
)


@dataclass(frozen=True)
class _OptionScanResult:
    chosen_val: str | None
    chosen_opt: WebElementLike | None
    dom_selected_pid: int | None
    dom_has_newplanet: bool
    already_selected: bool


def _scan_planet_options(
    select_el: WebElementLike,
    target_re: re.Pattern[str],
    initial_val: str | None,
) -> _OptionScanResult:
    chosen_val = initial_val
    chosen_opt: WebElementLike | None = None
    dom_selected_pid: int | None = None
    dom_has_newplanet = False
    for opt in select_el.find_elements(By.TAG_NAME, "option"):
        val = str(opt.get_attribute("value") or "")
        if "newplanet=" in val.lower():
            dom_has_newplanet = True
        try:
            raw_sel = opt.is_selected()
            is_sel = isinstance(raw_sel, (bool, int)) and bool(raw_sel)
        except StaleElementReferenceException:
            raise
        except SELENIUM_NAV_ERRORS:
            is_sel = False
        if is_sel:
            m_dom = _ANY_NEWPLANET_RE.search(val)
            if m_dom:
                pid_s = m_dom.group(1)
                if pid_s.isascii() and pid_s.isdigit() and len(pid_s) <= 18:
                    dom_selected_pid = int(pid_s)
        if target_re.search(val):
            if is_sel:
                return _OptionScanResult(
                    chosen_val=val,
                    chosen_opt=opt,
                    dom_selected_pid=dom_selected_pid,
                    dom_has_newplanet=dom_has_newplanet,
                    already_selected=True,
                )
            chosen_val = val
            chosen_opt = opt
    return _OptionScanResult(
        chosen_val=chosen_val,
        chosen_opt=chosen_opt,
        dom_selected_pid=dom_selected_pid,
        dom_has_newplanet=dom_has_newplanet,
        already_selected=False,
    )


def _trigger_option_switch(
    client: BrowserClientLike,
    select_el: WebElementLike,
    chosen_val: str | None,
    chosen_opt: WebElementLike | None,
) -> tuple[bool, bool]:
    """Attempt Selenium `Select`, direct option click, then hidden-select JS onchange trigger."""
    switched = False
    stale_during_switch = False
    if chosen_val is not None:
        try:
            Select(select_el).select_by_value(chosen_val)  # type: ignore[arg-type]
            switched = True
        except StaleElementReferenceException:
            stale_during_switch = True
        except SELENIUM_NAV_ERRORS:
            switched = False
    if not switched and chosen_opt is not None:
        try:
            safe_click(client, chosen_opt)
            switched = True
            stale_during_switch = False
        except (
            StaleElementReferenceException,
            NoSuchElementException,
            ElementNotInteractableException,
            WebDriverException,
        ) as exc:
            stale_during_switch = isinstance(exc, StaleElementReferenceException)
    if not switched and chosen_opt is not None and chosen_val and not stale_during_switch:
        try:
            if client.driver.execute_script(_HIDDEN_SELECT_SWITCH_JS, select_el, chosen_val) is True:
                switched = True
        except StaleElementReferenceException:
            stale_during_switch = True
        except SELENIUM_NAV_ERRORS:
            switched = False
    return switched, stale_during_switch


def _record_fallback_pid(ctx: ExecutorContext, dom_selected_pid: int | None, obs_html_pid: int | None) -> None:
    fallback_pid = dom_selected_pid if dom_selected_pid is not None else obs_html_pid
    if fallback_pid is not None:
        record_observed_planet(ctx, fallback_pid, confirmed=True)


def _current_page_or(client: BrowserClientLike, updated_html: str | None) -> str | None:
    cur_page_html = str(client.driver.page_source or "")
    return cur_page_html if cur_page_html.strip() else updated_html


def select_planet_if_needed_impl(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    planet_id: int,
    *,
    html: str = "",
    require_confirm: bool = False,
) -> tuple[bool, str | None]:
    """Switch active planet in `F_Hauptframe` using `<select name='planet'>` `newPlanet=` options."""
    _ = require_confirm
    obs_html_pid = extract_selected_planet_id(html)
    if obs_html_pid is not None:
        record_observed_planet(ctx, obs_html_pid, confirmed=True)
    pid = catalog.safe_optional_int(planet_id, None)
    if pid is None or pid < 0 or pid > catalog.MAX_JSON_SAFE_INT:
        return False, None
    needs_switch, target_value = find_newplanet_switch_target(html, pid)
    if html and not needs_switch and target_value is None:
        return False, None

    client._switch_frame("F_Hauptframe")
    target_re = re.compile(rf"newplanet={pid}(?!\d)", re.IGNORECASE | re.ASCII)
    paced = False
    updated_html: str | None = None
    dom_selected_pid: int | None = None

    for attempt in range(2):
        client._switch_frame("F_Hauptframe")
        try:
            select_el = client.driver.find_element(By.CSS_SELECTOR, 'select[name="planet"]')
            scan = _scan_planet_options(select_el, target_re, target_value)
            dom_selected_pid = scan.dom_selected_pid
        except NoSuchElementException:
            return False, updated_html
        except (StaleElementReferenceException, ElementNotInteractableException, WebDriverException) as exc:
            if isinstance(exc, StaleElementReferenceException) and attempt == 0:
                continue
            _record_fallback_pid(ctx, dom_selected_pid, obs_html_pid)
            return False, updated_html

        if scan.already_selected:
            record_observed_planet(ctx, pid, confirmed=True)
            return (True, _current_page_or(client, updated_html)) if attempt > 0 else (False, None)
        if dom_selected_pid is not None:
            record_observed_planet(ctx, dom_selected_pid, confirmed=True)
        if (scan.chosen_val is None and scan.chosen_opt is None) or (
            scan.dom_has_newplanet and scan.chosen_opt is None
        ):
            return False, updated_html
        if scan.chosen_opt is None and dom_selected_pid == pid:
            return (True, _current_page_or(client, updated_html)) if attempt > 0 else (False, None)

        if not paced:
            pace_client_action(ctx, client, priority="interactive")
            paced = True
        record_observed_planet(ctx, None, confirmed=False)
        nav_token = mark_frame_nav(client)
        switched, stale_during_switch = _trigger_option_switch(client, select_el, scan.chosen_val, scan.chosen_opt)
        if switched:
            updated_html = client._wait_frame_ready("F_Hauptframe", nav_token=nav_token)
            obs_after = extract_selected_planet_id(updated_html or "")
            if obs_after is not None:
                record_observed_planet(ctx, obs_after, confirmed=True)
                if obs_after == pid:
                    return True, updated_html
                if attempt == 0:
                    continue
                return False, updated_html
            record_observed_planet(ctx, pid, confirmed=True)
            return True, updated_html
        if not (stale_during_switch and attempt == 0):
            break

    _record_fallback_pid(ctx, dom_selected_pid, obs_html_pid)
    return False, updated_html


def _try_reuse_current_hauptframe(client: BrowserClientLike, menu_action: str) -> str:
    if not is_real_selenium_driver(client.driver):
        return ""
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
            return cur_html
    except SELENIUM_NAV_ERRORS:
        return ""
    return ""


def _navigate_fleet_planet(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    state: GameState,
    planet_id: int,
    *,
    active_planet_confirmed: bool,
) -> str:
    needs_confirm = (not active_planet_confirmed) and len(state.planets) > 1
    if state.active_planet_id != planet_id or needs_confirm:
        b_raw = call_with_interactive_priority(client.navigate_menu, "internalBuildings")
        b_html_str = b_raw if isinstance(b_raw, str) else ""
        obs_pid = extract_selected_planet_id(b_html_str, "internalBuildings")
        if obs_pid is not None:
            record_observed_planet(ctx, obs_pid, confirmed=True)
        b_needs_switch, _ = find_newplanet_switch_target(b_html_str, planet_id)
        switched, _ = select_planet_if_needed_impl(ctx, client, planet_id, html=b_html_str, require_confirm=True)
        confirmed_target = ctx.observed_active_planet_id == planet_id and ctx.observed_active_planet_confirmed is True
        if confirmed_planet_mismatch_id(ctx, planet_id) is not None or (
            b_needs_switch and not switched and not confirmed_target
        ):
            return ""
    fleet_res = call_with_interactive_priority(client.navigate_menu, "internalFleet")
    return fleet_res if isinstance(fleet_res, str) else ""


def navigate_and_select_planet_impl(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    state: GameState,
    menu_action: str,
    planet_id: int,
    *,
    active_planet_confirmed: bool = True,
) -> str:
    """Navigate `F_Hauptframe` to `menu_action` and switch to `planet_id` if needed."""
    call_with_interactive_priority(client.ensure_in_game)
    if menu_action == "internalFleet":
        return _navigate_fleet_planet(ctx, client, state, planet_id, active_planet_confirmed=active_planet_confirmed)

    html_str = _try_reuse_current_hauptframe(client, menu_action)
    if not html_str:
        nav_res = call_with_interactive_priority(client.navigate_menu, menu_action)
        html_str = nav_res if isinstance(nav_res, str) else ""
    html = html_str

    obs_pid = extract_selected_planet_id(html_str, menu_action)
    if obs_pid is not None:
        record_observed_planet(ctx, obs_pid, confirmed=True)
    needs_switch, _ = find_newplanet_switch_target(html_str, planet_id)
    switched = False
    if needs_switch or (state.active_planet_id != planet_id and "newplanet=" in html_str.lower()):
        switched, updated_html = select_planet_if_needed_impl(ctx, client, planet_id, html=html_str)
        if switched and updated_html:
            html = updated_html

    confirmed_target = ctx.observed_active_planet_id == planet_id and ctx.observed_active_planet_confirmed is True
    if confirmed_planet_mismatch_id(ctx, planet_id) is not None or (
        needs_switch and not switched and not confirmed_target
    ):
        return ""
    return html
