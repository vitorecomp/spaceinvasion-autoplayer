"""Safety checks and `internalSettings` modal DOM helpers for `ColonyOps`."""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
import re

from bs4 import BeautifulSoup, Tag
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

from spaceinvasion.core import catalog
from spaceinvasion.core.coercion import coerce_int, safe_key
from spaceinvasion.core.coords import extract_coords_from_text
from spaceinvasion.core.model import normalize_planet_type
from spaceinvasion.parsing.models import GameState, PlanetState
from spaceinvasion.session.browser_client._protocols import SELENIUM_NAV_ERRORS, WebElementLike
from spaceinvasion.session.exec_ops._dom import mark_frame_nav, safe_click
from spaceinvasion.session.exec_ops._planet_state import (
    homeworld_planet_id_from_state,
    pace_client_action,
    sleep_action_delay,
)
from spaceinvasion.session.exec_ops._protocols import BrowserClientLike, ExecutorContext
from spaceinvasion.session.exec_ops._types import safe_str

_POS1_RE = re.compile(r"\bpos\.?\s*1\b", re.IGNORECASE)
_FIELDS_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*(?:fields|felder)", re.IGNORECASE | re.ASCII)
_COORD_TRIPLE_RE = re.compile(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", re.ASCII)
_MODAL_CHECK_JS = (
    "return {"
    "pid: (typeof window._planet_deleteID !== 'undefined' ? window._planet_deleteID : null), "
    "coords: (document.getElementById('planet-delete-coords') ? document.getElementById('planet-delete-coords').textContent : '')"
    "};"
)


def is_undersized_fresh_colony_state(
    planet: PlanetState | None,
    state: GameState | None,
    min_colony_fields: int = 300,
) -> bool:
    """Return True iff `planet` is a newly settled secondary normal colony with `< min_colony_fields` and zero development."""
    if planet is None or state is None or normalize_planet_type(planet) != "normal":
        return False
    normal_count = sum(1 for p in state.planets if normalize_planet_type(p) == "normal")
    if normal_count < 2:
        return False
    pid = coerce_int(planet.planet_id, 0)
    hw_id = homeworld_planet_id_from_state(state)
    if pid <= 0 or (hw_id is not None and pid == hw_id):
        return False
    pname_l = safe_str(planet.name).strip().lower()
    if "homeworld" in pname_l or "heimatplanet" in pname_l:
        return False
    min_tgt = max(50, coerce_int(min_colony_fields, 300))
    f_max = max(0, coerce_int(planet.fields_max, 0))
    if f_max <= 0 or f_max >= min_tgt or max(0, coerce_int(planet.fields_used, 0)) > 0:
        return False
    if sum(max(0, coerce_int(v, 0)) for v in planet.buildings.values()) > 0:
        return False
    if sum(max(0, coerce_int(v, 0)) for k, v in planet.ships.items() if safe_key(k) != "s9") > 0:
        return False
    if sum(max(0, coerce_int(v, 0)) for v in planet.defense.values()) > 0:
        return False
    p_orig = extract_coords_from_text(planet.coords)
    active_pid = coerce_int(state.active_planet_id, 0)
    for job in state.active_queues:
        if job.category != "building":
            continue
        q_orig = extract_coords_from_text(job.origin_coords)
        if (q_orig and p_orig and q_orig == p_orig) or (not q_orig and pid == active_pid):
            return False
    return True


def _norm_coord_triple(raw: str) -> str:
    m = _COORD_TRIPLE_RE.search(raw)
    if not m:
        return ""
    return f"{int(m.group(1)):02d}:{int(m.group(2)):03d}:{int(m.group(3)):02d}"


def validate_settings_colony_row(
    soup: BeautifulSoup,
    planet: PlanetState,
    coords_s: str,
    min_fields: int,
) -> str | None:
    """Validate `internalSettings` row for `planet_{pid}` before clicking any delete trigger."""
    inp = soup.find("input", attrs={"name": f"planet_{planet.planet_id}"})
    if not isinstance(inp, Tag):
        return f"Aborted: input planet_{planet.planet_id} ([{coords_s}]) not found on internalSettings."
    row_tr = inp.find_parent("tr")
    row_text = " ".join(row_tr.get_text(" ", strip=True).split()) if isinstance(row_tr, Tag) else ""
    if _POS1_RE.search(row_text):
        return f"Aborted safety check: refusing to delete Pos. 1 Homeworld ([{coords_s}]) on internalSettings."
    m_fields = _FIELDS_RE.search(row_text)
    if m_fields is not None:
        dom_used = int(m_fields.group(1))
        dom_max = int(m_fields.group(2))
        if dom_used > 0 or dom_max <= 0 or dom_max >= min_fields:
            return (
                f"Aborted safety check: internalSettings reports {dom_used}/{dom_max} Fields on [{coords_s}] "
                f"(expected 0/{planet.fields_max} < {min_fields})."
            )
    if coords_s:
        norm_dom_c = _norm_coord_triple(row_text)
        norm_pl_c = _norm_coord_triple(coords_s)
        if norm_dom_c and norm_pl_c and norm_dom_c != norm_pl_c:
            return (
                f"Aborted safety check: planet_{planet.planet_id} coords mismatch on internalSettings "
                f"({norm_dom_c} != {norm_pl_c})."
            )
    return None


def _el_is_displayed(el: WebElementLike) -> bool:
    try:
        return bool(el.is_displayed())
    except AttributeError:
        return True
    except SELENIUM_NAV_ERRORS:
        return False


def _click_allow_js(client: BrowserClientLike, el: WebElementLike) -> None:
    if not _el_is_displayed(el):
        client.driver.execute_script("arguments[0].click();", el)
        return
    try:
        safe_click(client, el)
    except (ElementNotInteractableException, ElementClickInterceptedException):
        client.driver.execute_script("arguments[0].click();", el)


def _find_delete_trigger(client: BrowserClientLike, planet_id: int) -> WebElementLike | None:
    pid_token_re = re.compile(rf"\b{int(planet_id)}\b", re.ASCII)
    del_candidates = client.driver.find_elements(
        By.CSS_SELECTOR,
        '[onclick*="confirmPlanetDeletion"], [onclick*="deletePlanet"]',
    )
    visible_matches: list[WebElementLike] = []
    hidden_matches: list[WebElementLike] = []
    for el in del_candidates:
        oc = str(el.get_attribute("onclick") or "")
        if pid_token_re.search(oc):
            if _el_is_displayed(el):
                visible_matches.append(el)
            else:
                hidden_matches.append(el)
    if visible_matches:
        return visible_matches[0]
    return hidden_matches[0] if hidden_matches else None


def _wait_for_delete_confirm_button(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    del_trigger: WebElementLike,
) -> WebElementLike | None:
    retried_js_trigger = False
    start_wait = ctx.clock()
    deadline = start_wait + 2.5
    while ctx.clock() < deadline:
        btns = client.driver.find_elements(By.CSS_SELECTOR, '#delete-confirm [onclick*="executeDeletePlanet"]')
        vis_btns = [b for b in btns if _el_is_displayed(b)]
        if vis_btns:
            return vis_btns[0]
        if not retried_js_trigger and (ctx.clock() - start_wait) >= 0.45:
            retried_js_trigger = True
            with contextlib.suppress(*SELENIUM_NAV_ERRORS):
                client.driver.execute_script("arguments[0].click();", del_trigger)
        ctx.sleeper(0.05)
    return None


def _verify_delete_modal_target(client: BrowserClientLike, planet_id: int, coords_s: str) -> str | None:
    modal_check: object = None
    with contextlib.suppress(*SELENIUM_NAV_ERRORS):
        modal_check = client.driver.execute_script(_MODAL_CHECK_JS)
    if not isinstance(modal_check, Mapping):
        return None
    js_pid = catalog.safe_optional_int(modal_check.get("pid"), None)
    if js_pid is not None and js_pid > 0 and js_pid != int(planet_id):
        return (
            f"Aborted safety check: #delete-confirm targets planet_id={js_pid} instead of {planet_id} ([{coords_s}])."
        )
    js_coords_raw = str(modal_check.get("coords") or "").strip()
    norm_target_coords = _norm_coord_triple(coords_s) if coords_s else ""
    if js_coords_raw and norm_target_coords:
        norm_mc = _norm_coord_triple(js_coords_raw)
        if norm_mc and norm_mc != norm_target_coords:
            return f"Aborted safety check: #delete-confirm shows [{norm_mc}] instead of [{norm_target_coords}]."
    return None


def trigger_colony_deletion_dom(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    planet_id: int,
    coords_s: str,
) -> tuple[str | None, str | None]:
    """Click `confirmPlanetDeletion` -> `#delete-confirm` `executeDeletePlanet` with modal safety checks."""
    client._switch_frame("F_Hauptframe")
    nav_token = mark_frame_nav(client)
    click_stage = "find_trigger"
    try:
        del_trigger = _find_delete_trigger(client, planet_id)
        if del_trigger is None:
            return None, (
                f"Aborted: confirmPlanetDeletion control for planet_{planet_id} ([{coords_s}]) "
                "not found on internalSettings."
            )
        oc_trigger = str(del_trigger.get_attribute("onclick") or "")
        if "confirmPlanetDeletion" in oc_trigger:
            click_stage = "del_trigger"
            _click_allow_js(client, del_trigger)
            click_stage = "wait_confirm"
            confirm_btn = _wait_for_delete_confirm_button(ctx, client, del_trigger)
            if confirm_btn is None:
                return (
                    None,
                    f"Aborted: #delete-confirm executeDeletePlanet button did not become visible for [{coords_s}].",
                )
            modal_err = _verify_delete_modal_target(client, planet_id, coords_s)
            if modal_err is not None:
                return None, modal_err
            pace_client_action(ctx, client, priority="interactive")
            nav_token = mark_frame_nav(client)
            sleep_action_delay(ctx)
            click_stage = "confirm_btn"
            _click_allow_js(client, confirm_btn)
        else:
            pace_client_action(ctx, client, priority="interactive")
            nav_token = mark_frame_nav(client)
            sleep_action_delay(ctx)
            click_stage = "direct_delete"
            _click_allow_js(client, del_trigger)
        return nav_token, None
    except (
        StaleElementReferenceException,
        NoSuchElementException,
        ElementNotInteractableException,
        WebDriverException,
    ) as exc:
        return None, (
            f"Aborted: internalSettings planet deletion controls failed for [{coords_s}] "
            f"at {click_stage} ({type(exc).__name__})."
        )
