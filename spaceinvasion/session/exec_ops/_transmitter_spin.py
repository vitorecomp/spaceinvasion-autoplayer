"""Interdimensional Transmitter Homeworld selection, free-source switch, and DOM spin helpers."""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
from dataclasses import replace

from selenium.webdriver.common.by import By

from spaceinvasion.core.model import PlannedAction
from spaceinvasion.parsing.account_pages import parse_transmitter_page
from spaceinvasion.parsing.models import GameState
from spaceinvasion.session.browser_client._protocols import SELENIUM_NAV_ERRORS
from spaceinvasion.session.exec_ops._dom import mark_frame_nav, safe_click
from spaceinvasion.session.exec_ops._planet_state import (
    extract_selected_planet_id,
    homeworld_planet_id_from_state,
    pace_client_action,
    try_build_internal_url,
)
from spaceinvasion.session.exec_ops._protocols import BrowserClientLike, ExecutorContext
from spaceinvasion.session.exec_ops._types import call_with_interactive_priority, safe_str
from spaceinvasion.session.exec_ops.planet_select import PlanetSelector

_TM_DOM_GUARD_JS = (
    "var form = document.getElementById('tm.form');"
    "var btn = document.getElementById('tm.submit');"
    "if (!form || !btn) return {ok: false, reason: 'missing_form_or_btn'};"
    "var src = form.querySelector('input[name=\"idtSrc\"]');"
    "var play = document.getElementById('tm.play') || form.querySelector('input[name=\"play\"]');"
    "var hasUp = Boolean(btn.querySelector('.urplasma-icon-30, .urplasma-base, [class*=\"urplasma\"]'));"
    "var txt = (btn.innerText || btn.textContent || '').toLowerCase();"
    "if (src && String(src.value) !== '1') return {ok: false, reason: 'idtSrc=' + src.value};"
    "if (play && String(play.value) !== '1') play.value = '1';"
    "if (hasUp || txt.indexOf('urplasma') !== -1 || txt.indexOf('activate for') !== -1) {"
    "  return {ok: false, reason: 'urplasma_button'};"
    "}"
    "return {ok: true};"
)
_TM_SUBMIT_FALLBACK_JS = (
    "var f = document.getElementById('tm.form');"
    "if (!f) return false;"
    "var src = f.querySelector('input[name=\"idtSrc\"]');"
    "if (src && String(src.value) !== '1') return false;"
    "var p = document.getElementById('tm.play') || f.querySelector('input[name=\"play\"]');"
    "if (p) p.value = '1';"
    "f.submit(); return true;"
)


def resolve_homeworld_target(
    state: GameState,
    action: PlannedAction,
) -> tuple[int | None, str, bool, PlannedAction]:
    """Resolve the Pos. 1 Homeworld target ID, coordinates, multi-planet flag, and effective action."""
    hw_pid = homeworld_planet_id_from_state(state)
    if (
        hw_pid is None
        and isinstance(action.planet_id, int)
        and not isinstance(action.planet_id, bool)
        and action.planet_id > 0
    ):
        hw_pid = action.planet_id
    if (
        hw_pid is None
        and isinstance(state.active_planet_id, int)
        and not isinstance(state.active_planet_id, bool)
        and state.active_planet_id > 0
    ):
        hw_pid = state.active_planet_id
    multi_planet = len(state.planets) > 1
    hw_planet = next((p for p in state.planets if p.planet_id == hw_pid), None)
    hw_coords = safe_str((hw_planet.coords if hw_planet is not None else "") or action.planet_coords).strip()
    effective_action = (
        replace(action, planet_id=hw_pid, planet_coords=hw_coords or action.planet_coords)
        if hw_pid is not None and (action.planet_id != hw_pid or (hw_coords and action.planet_coords != hw_coords))
        else action
    )
    return hw_pid, hw_coords, multi_planet, effective_action


def navigate_transmitter_on_homeworld(
    selector: PlanetSelector,
    client: BrowserClientLike,
    state: GameState,
    hw_pid: int | None,
    hw_coords: str,
    multi_planet: bool,
) -> tuple[str, str | None]:
    """Navigate to `internalTransmitter` while strictly enforcing Homeworld planet selection."""
    if hw_pid is None or hw_pid <= 0:
        nav_res = call_with_interactive_priority(client.navigate_menu, "internalTransmitter")
        return (nav_res if isinstance(nav_res, str) else ""), None

    html = selector.navigate_and_select_planet(
        client,
        state,
        "internalTransmitter",
        hw_pid,
        active_planet_confirmed=False,
    )
    if not html:
        return "", (
            f"Could not select Homeworld [{hw_coords or hw_pid}] on internalTransmitter; "
            "aborted so the free charge is not claimed on a secondary colony."
        )
    if multi_planet and "newplanet=" not in html.lower() and state.active_planet_id != hw_pid:
        b_html = selector.navigate_and_select_planet(
            client,
            state,
            "internalBuildings",
            hw_pid,
            active_planet_confirmed=False,
        )
        if not b_html:
            return "", (
                f"Could not switch to Homeworld [{hw_coords or hw_pid}] before opening internalTransmitter; aborted."
            )
        nav_res = call_with_interactive_priority(client.navigate_menu, "internalTransmitter")
        html = nav_res if isinstance(nav_res, str) else ""
    return html, None


def switch_transmitter_to_free_src(
    ctx: ExecutorContext,
    selector: PlanetSelector,
    client: BrowserClientLike,
    html: str,
    info: Mapping[str, object],
    hw_pid: int | None,
    multi_planet: bool,
) -> tuple[str, dict[str, object]]:
    """Switch `internalTransmitter` to `idtSrc=1` (free charges) when currently on a paid source."""
    out_info: dict[str, object] = dict(info)
    if not (out_info.get("needs_src_switch_to_free") and out_info.get("free_src_href")):
        return html, out_info
    raw_href = safe_str(out_info["free_src_href"]).strip()
    built_url = try_build_internal_url(client, raw_href.split("?", 1)[1]) if "?" in raw_href else None
    target_url = built_url or raw_href
    pace_client_action(ctx, client, priority="interactive")
    nav_tok_sw = mark_frame_nav(client, "F_Hauptframe")
    with contextlib.suppress(AttributeError):
        client._switch_frame("F_Hauptframe")
    client.driver.execute_script("location.href = arguments[0];", target_url)
    try:
        html = client._wait_frame_ready("F_Hauptframe", expected_action="internalTransmitter", nav_token=nav_tok_sw)
    except AttributeError:
        html = str(client.driver.page_source or html)
    if multi_planet and hw_pid is not None and html:
        obs_after_src = extract_selected_planet_id(html, "internalTransmitter")
        if obs_after_src is not None and obs_after_src != hw_pid:
            sw_ok, sw_html = selector.select_planet_if_needed(client, hw_pid, html=html)
            if sw_ok and sw_html:
                html = sw_html
    return html, dict(parse_transmitter_page(html))


def check_transmitter_live_dom_guard(client: BrowserClientLike) -> str | None:
    """Inspect live `#tm.form` / `#tm.submit` DOM to guarantee zero Urplasma spend."""
    with contextlib.suppress(AttributeError):
        client._switch_frame("F_Hauptframe")
    dom_check: object = None
    with contextlib.suppress(*SELENIUM_NAV_ERRORS):
        dom_check = client.driver.execute_script(_TM_DOM_GUARD_JS)
    if isinstance(dom_check, Mapping) and not dom_check.get("ok", True):
        return f"Live DOM guard blocked Interdimensional Transmitter ({dom_check.get('reason')})."
    return None


def click_free_transmitter_submit(ctx: ExecutorContext, client: BrowserClientLike) -> tuple[bool, str]:
    """Click `#tm.submit` (or guarded `idtSrc=1` JS fallback) and wait for reloaded transmitter HTML."""
    pace_client_action(ctx, client, priority="interactive")
    nav_token = mark_frame_nav(client, "F_Hauptframe")
    clicked = False
    with contextlib.suppress(*SELENIUM_NAV_ERRORS):
        btn_el = client.driver.find_element(By.ID, "tm.submit")
        safe_click(client, btn_el)
        clicked = True
    if not clicked:
        with contextlib.suppress(*SELENIUM_NAV_ERRORS):
            clicked = bool(client.driver.execute_script(_TM_SUBMIT_FALLBACK_JS))
    if not clicked:
        return False, ""
    try:
        after_html = client._wait_frame_ready(
            "F_Hauptframe", expected_action="internalTransmitter", nav_token=nav_token
        )
    except AttributeError:
        after_html = str(client.driver.page_source or "")
    return True, (after_html if isinstance(after_html, str) else "")
