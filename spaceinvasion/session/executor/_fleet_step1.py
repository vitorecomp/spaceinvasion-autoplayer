"""Step 1 (`internalFleet`) origin verification, form scoping, ship filling, and submit helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import contextlib
from dataclasses import dataclass
import re

from bs4 import BeautifulSoup, Tag
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

from spaceinvasion.core import catalog
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.parsing.fleet_page import (
    extract_internal_fleet_planet_coords,
    parse_internal_fleet_slot_status,
)
from spaceinvasion.parsing.models import GameState
from spaceinvasion.session.client._protocols import SELENIUM_NAV_ERRORS, WebElementLike
from spaceinvasion.session.executor._dom import mark_frame_nav, safe_click, set_text_input_value
from spaceinvasion.session.executor._planet_state import record_observed_planet
from spaceinvasion.session.executor._protocols import BrowserClientLike, ExecutorContext
from spaceinvasion.session.executor._types import ExecutionResult, call_with_interactive_priority

type NavigateAndSelectFn = Callable[..., str | tuple[str, bool, str]]
type SelectPlanetFn = Callable[..., tuple[bool, str | None]]
type BuildUpdatedStateFn = Callable[..., GameState | None]

_SAFE_DOM_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


@dataclass(frozen=True)
class Step1Dom:
    """Parsed `internalFleet` Step 1 DOM structure and form-scoping selectors."""

    fleet_soup: BeautifulSoup
    step1_form_soup: Tag | None
    step1_form_css: str
    soup_input_names: set[str]
    step1_submit_css: str


def normalize_nav_html(nav_result: str | tuple[str, bool, str] | None) -> str:
    """Extract the HTML payload from either legacy `str` or `(html, ok, msg)` navigation callbacks."""
    if isinstance(nav_result, tuple):
        return str(nav_result[0] or "") if nav_result else ""
    return str(nav_result or "")


def confirmed_mismatch_pid(ctx: ExecutorContext, target_planet_id: int) -> int | None:
    """Return the confirmed active planet ID when it differs from `target_planet_id`, else `None`."""
    return ctx.observed_active_planet_id if ctx.confirmed_planet_mismatch(target_planet_id) else None


def mismatch_abort_result(action: PlannedAction, mismatch_pid: int) -> ExecutionResult:
    """Build the canonical abort result when active origin planet differs from `action.planet_id`."""
    return ExecutionResult(
        executed=False,
        dry_run=False,
        action=action,
        message=(
            f"Aborted: active planet is {mismatch_pid}, "
            f"not requested origin {catalog.fmt_compact_int(action.planet_id)}."
        ),
        observed_active_planet_id=mismatch_pid,
        observed_active_planet_confirmed=True,
    )


def pace_and_mark_nav(ctx: ExecutorContext, client: BrowserClientLike) -> str | None:
    """Acquire interactive limiter pacing, stamp a pre-navigation frame token, and apply action jitter."""
    ctx.pace_action(client)
    nav_token = mark_frame_nav(client)
    low, high = ctx.action_delay_range
    if high > 0:
        ctx.sleeper(ctx.rng(low, high))
    return nav_token


def parse_step1_dom(raw_fhtml: str) -> Step1Dom:
    """Locate the Step 1 `internalFleet` form, its CSS scope, input names, and submit selector."""
    f_soup = BeautifulSoup(raw_fhtml or "", "html.parser")
    s_form = f_soup.select_one('form[action*="internalSendFleet"]')
    n_form = f_soup.select_one('form[name="fleet"]')
    st1_soup = s_form or n_form
    if st1_soup is None:
        for cand_f in f_soup.find_all("form"):
            if all(cand_f.select_one(f'[name="{fn}"]') is not None for fn in ("gal", "sun", "pla")):
                st1_soup = cand_f
                break
    st1_css = ""
    if s_form is not None:
        st1_css = 'form[action*="internalSendFleet"]'
    elif n_form is not None:
        st1_css = 'form[name="fleet"]'
    elif st1_soup is not None:
        fid = str(st1_soup.get("id") or "").strip()
        faction = str(st1_soup.get("action") or "").strip()
        if fid and _SAFE_DOM_ID_RE.fullmatch(fid):
            st1_css = f"form#{fid}"
        elif faction:
            st1_css = f'form[action="{faction.replace(chr(34), chr(92) + chr(34))}"]'
    sc_soup = st1_soup if st1_soup is not None else f_soup
    in_names = {str(inp.get("name") or "") for inp in sc_soup.find_all("input") if inp.get("name")}
    sub_css = (
        f'{st1_css} input[type="submit"], {st1_css} button[type="submit"]'
        if st1_css
        else 'form[name="fleet"] input[type="submit"], form[name="fleet"] button[type="submit"]'
    )
    return Step1Dom(f_soup, st1_soup, st1_css, in_names, sub_css)


def _field_needs_scoping(dom: Step1Dom, fname: str) -> bool:
    if not dom.step1_form_css or dom.step1_form_soup is None:
        return False
    esc_fn = fname.replace("\\", "\\\\").replace('"', '\\"')
    sel = f'[name="{esc_fn}"]'
    all_matches = dom.fleet_soup.select(sel)
    if len(all_matches) > 1:
        return True
    return bool(all_matches and len(dom.step1_form_soup.select(sel)) != len(all_matches))


def _scoped_field_css(dom: Step1Dom, fname: str) -> str:
    esc_fn = fname.replace('"', '\\"')
    inp_css = f'{dom.step1_form_css} input[name="{esc_fn}"]'
    return inp_css if dom.fleet_soup.select_one(inp_css) is not None else f'{dom.step1_form_css} [name="{esc_fn}"]'


def _find_step1_field(client: BrowserClientLike, dom: Step1Dom, fname: str) -> WebElementLike:
    if _field_needs_scoping(dom, fname):
        return client.driver.find_element(By.CSS_SELECTOR, _scoped_field_css(dom, fname))
    return client.driver.find_element(By.NAME, fname)


def _fill_step1_ships(client: BrowserClientLike, dom: Step1Dom, ships_to_send: Mapping[str, int]) -> bool:
    filled_any = False
    for skey, qty in ships_to_send.items():
        num = skey[1:]
        candidates = (f"ship_{num}", f"ship[{num}]", f"s{num}", f"ship{num}")
        matched_in_html = next((c for c in candidates if c in dom.soup_input_names), None)
        if matched_in_html is not None:
            els = (
                client.driver.find_elements(By.CSS_SELECTOR, _scoped_field_css(dom, matched_in_html))
                if _field_needs_scoping(dom, matched_in_html)
                else client.driver.find_elements(By.NAME, matched_in_html)
            )
        else:
            els = []
            for cname in candidates:
                els = client.driver.find_elements(By.NAME, cname)
                if els:
                    break
        if els:
            qty_safe = max(1, catalog.cap_json_int(catalog.safe_int(qty, 1)))
            set_text_input_value(client, els[0], str(qty_safe))
            filled_any = True
    return filled_any


def _fill_step1_target_controls(
    client: BrowserClientLike,
    dom: Step1Dom,
    *,
    t_gal: int,
    t_sys: int,
    t_pos: int,
    is_asteroid: bool,
    mission_kind: str,
) -> None:
    for fname, fval in (("gal", str(t_gal)), ("sun", str(t_sys)), ("pla", str(t_pos))):
        set_text_input_value(client, _find_step1_field(client, dom, fname), fval)
    if is_asteroid and "astro" in dom.soup_input_names:
        astro_cb = _find_step1_field(client, dom, "astro")
        if not astro_cb.is_selected():
            safe_click(client, astro_cb)
    if mission_kind == "RECYCLE":
        for rb_name in ("rubblefield", "rubble", "debris", "tf"):
            if rb_name in dom.soup_input_names:
                rb_cb = _find_step1_field(client, dom, rb_name)
                if str(rb_cb.get_attribute("type") or "").strip().lower() == "checkbox" and not rb_cb.is_selected():
                    safe_click(client, rb_cb)
                break


def _record_observed_from_fleet_html(ctx: ExecutorContext, state: GameState, fleet_html: str) -> int | None:
    obs_coords = extract_internal_fleet_planet_coords(fleet_html)
    if not obs_coords:
        return None
    obs_planet = next((p for p in state.planets if normalize_coords(p.coords) == obs_coords), None)
    if obs_planet is None:
        return None
    record_observed_planet(ctx, obs_planet.planet_id, confirmed=True)
    return obs_planet.planet_id


def ensure_fleet_origin_planet(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    state: GameState,
    action: PlannedAction,
    *,
    navigate_and_select_fn: NavigateAndSelectFn,
    active_planet_confirmed: bool,
) -> tuple[str, bool, ExecutionResult | None]:
    """Navigate to `internalFleet` on `action.planet_id`, reconfirming on multi-planet mismatch."""
    did_force_reconfirm = not active_planet_confirmed
    fleet_html = normalize_nav_html(
        navigate_and_select_fn(
            client, state, "internalFleet", action.planet_id, active_planet_confirmed=active_planet_confirmed
        )
    )
    if len(state.planets) > 1:
        obs_pid = _record_observed_from_fleet_html(ctx, state, fleet_html)
        if obs_pid is not None and obs_pid != action.planet_id and not did_force_reconfirm:
            did_force_reconfirm = True
            fleet_html = normalize_nav_html(
                navigate_and_select_fn(client, state, "internalFleet", action.planet_id, active_planet_confirmed=False)
            )
            _record_observed_from_fleet_html(ctx, state, fleet_html)
    mismatch_pid = confirmed_mismatch_pid(ctx, action.planet_id)
    if mismatch_pid is not None:
        return fleet_html, did_force_reconfirm, mismatch_abort_result(action, mismatch_pid)
    return fleet_html, did_force_reconfirm, None


def _build_missing_ships_abort(
    client: BrowserClientLike,
    state: GameState,
    action: PlannedAction,
    *,
    coords_s: str,
    slot_status: tuple[int, int] | None,
    build_updated_state_fn: BuildUpdatedStateFn,
) -> ExecutionResult:
    obs_avail = slot_status[0] if slot_status is not None else None
    obs_filled = slot_status[1] if slot_status is not None else None
    if slot_status is not None and slot_status[1] >= slot_status[0]:
        return ExecutionResult(
            executed=False,
            dry_run=False,
            action=action,
            message=(
                f"Aborted: Fleet bases full ({slot_status[1]}/{slot_status[0]} filled on [{coords_s}]) "
                "— no ship input fields found on internalFleet."
            ),
            observed_fleet_slots_available=obs_avail,
            observed_fleet_slots_filled=obs_filled,
        )
    refreshed_abort_state: GameState | None = None
    with contextlib.suppress(*SELENIUM_NAV_ERRORS):
        ov_abort = call_with_interactive_priority(client.read_planets_overview, refresh=True)
        refreshed_abort_state = build_updated_state_fn(client, state, action, ov_abort, is_fleet=True)
    return ExecutionResult(
        executed=False,
        dry_run=False,
        action=action,
        message="Aborted: no ship input fields found on internalFleet.",
        updated_state=refreshed_abort_state,
        observed_fleet_slots_available=obs_avail,
        observed_fleet_slots_filled=obs_filled,
    )


def _handle_missing_step1_ships(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    state: GameState,
    action: PlannedAction,
    *,
    attempt: int,
    did_force_reconfirm: bool,
    slot_status: tuple[int, int] | None,
    coords_s: str,
    navigate_and_select_fn: NavigateAndSelectFn,
    build_updated_state_fn: BuildUpdatedStateFn,
) -> tuple[Step1Dom | None, tuple[int, int] | None, bool, ExecutionResult | None]:
    slots_full = slot_status is not None and slot_status[1] >= slot_status[0]
    if not slots_full and not did_force_reconfirm and len(state.planets) > 1 and attempt == 0:
        re_html = normalize_nav_html(
            navigate_and_select_fn(client, state, "internalFleet", action.planet_id, active_planet_confirmed=False)
        )
        mismatch_retry = confirmed_mismatch_pid(ctx, action.planet_id)
        if mismatch_retry is not None:
            return None, slot_status, True, mismatch_abort_result(action, mismatch_retry)
        client._switch_frame("F_Hauptframe")
        return parse_step1_dom(re_html), parse_internal_fleet_slot_status(re_html), True, None
    abort = _build_missing_ships_abort(
        client, state, action, coords_s=coords_s, slot_status=slot_status, build_updated_state_fn=build_updated_state_fn
    )
    return None, slot_status, did_force_reconfirm, abort


def submit_step1_form(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    state: GameState,
    action: PlannedAction,
    *,
    fleet_html: str,
    did_force_reconfirm: bool,
    ships_to_send: Mapping[str, int],
    t_gal: int,
    t_sys: int,
    t_pos: int,
    is_asteroid: bool,
    mission_kind: str,
    coords_s: str,
    navigate_and_select_fn: NavigateAndSelectFn,
    build_updated_state_fn: BuildUpdatedStateFn,
) -> tuple[str | None, tuple[int, int] | None, ExecutionResult | None]:
    """Fill Step 1 ship counts, target coords, checkboxes, and click Continue (with stale retry)."""
    dom = parse_step1_dom(fleet_html)
    slot_status = parse_internal_fleet_slot_status(fleet_html)
    client._switch_frame("F_Hauptframe")
    nav_token1: str | None = None
    paced1 = False

    for attempt in range(2):
        try:
            if not _fill_step1_ships(client, dom, ships_to_send):
                new_dom, slot_status, did_force_reconfirm, abort = _handle_missing_step1_ships(
                    ctx,
                    client,
                    state,
                    action,
                    attempt=attempt,
                    did_force_reconfirm=did_force_reconfirm,
                    slot_status=slot_status,
                    coords_s=coords_s,
                    navigate_and_select_fn=navigate_and_select_fn,
                    build_updated_state_fn=build_updated_state_fn,
                )
                if abort is not None:
                    return None, slot_status, abort
                if new_dom is not None:
                    dom = new_dom
                    continue
            _fill_step1_target_controls(
                client,
                dom,
                t_gal=t_gal,
                t_sys=t_sys,
                t_pos=t_pos,
                is_asteroid=is_asteroid,
                mission_kind=mission_kind,
            )
            cont_btn = client.driver.find_element(By.CSS_SELECTOR, dom.step1_submit_css)
            if not paced1:
                nav_token1 = pace_and_mark_nav(ctx, client)
                paced1 = True
            safe_click(client, cont_btn)
            return nav_token1, slot_status, None
        except StaleElementReferenceException:
            if attempt == 1:
                return (
                    None,
                    slot_status,
                    ExecutionResult(False, False, action, "Aborted: internalFleet form controls became stale."),
                )
            client._switch_frame("F_Hauptframe")
        except NoSuchElementException:
            return (
                None,
                slot_status,
                ExecutionResult(False, False, action, "Aborted: internalFleet target or submit controls not found."),
            )
        except (ElementNotInteractableException, WebDriverException) as exc:
            msg = f"Aborted: internalFleet form controls not interactable ({type(exc).__name__})."
            return None, slot_status, ExecutionResult(False, False, action, msg)
    return nav_token1, slot_status, None
