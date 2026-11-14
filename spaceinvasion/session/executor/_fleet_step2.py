"""Step 2 (`internalSendFleet`) transport cargo input filling and LAUNCH form submission."""

from __future__ import annotations

from bs4 import BeautifulSoup
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

from spaceinvasion.core import catalog
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.core.redaction import redact_sensitive
from spaceinvasion.parsing.models import Resources
from spaceinvasion.session.client._protocols import WebElementLike
from spaceinvasion.session.executor._dom import safe_click, set_text_input_value
from spaceinvasion.session.executor._fleet_errors import log_fleet_diag, pick_send_fleet_form_css
from spaceinvasion.session.executor._fleet_radio import match_mission_radio
from spaceinvasion.session.executor._fleet_step1 import pace_and_mark_nav
from spaceinvasion.session.executor._fleet_step2_checks import build_missing_radio_result
from spaceinvasion.session.executor._protocols import BrowserClientLike, ExecutorContext
from spaceinvasion.session.executor._types import ExecutionResult

_TRANSPORT_INPUT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "pig_iron": (
        "fleet_roh",
        "fleet_roheisen",
        "roheisen",
        "roh",
        "pig_iron",
        "pigiron",
        "res1",
        "res_1",
        "resource1",
        "resource_1",
        "r1",
        "eisen",
    ),
    "metal": (
        "fleet_met",
        "fleet_metall",
        "fleet_metal",
        "metall",
        "met",
        "metal",
        "res2",
        "res_2",
        "resource2",
        "resource_2",
        "r2",
    ),
    "kryptonite": (
        "fleet_kry",
        "fleet_krypt",
        "fleet_kryp",
        "fleet_kryptonit",
        "fleet_kryptonite",
        "kryptonit",
        "krypt",
        "kryp",
        "kry",
        "kryptonite",
        "res3",
        "res_3",
        "resource3",
        "resource_3",
        "r3",
    ),
    "spice": (
        "fleet_spi",
        "fleet_spice",
        "spice",
        "spi",
        "res4",
        "res_4",
        "resource4",
        "resource_4",
        "r4",
    ),
}


def build_launch_css(form_css: str) -> str:
    """Build the multi-dialect CSS selector for the Step 2 fleet LAUNCH submit button."""
    return (
        f'{form_css} input[type="submit"], '
        f'{form_css} button[type="submit"], '
        f"{form_css} input#goButton, "
        f"{form_css} input.fleet-start-button, "
        f'{form_css} input[onclick*="startFleet"]'
    )


def fill_transport_cargo_inputs(
    client: BrowserClientLike,
    form_css: str,
    cargo_resources: Resources,
) -> tuple[bool, list[str]]:
    """Populate the 4 resource inputs on `internalSendFleet` after selecting the Transport radio."""
    desired = {
        "pig_iron": max(0, int(cargo_resources.pig_iron)),
        "metal": max(0, int(cargo_resources.metal)),
        "kryptonite": max(0, int(cargo_resources.kryptonite)),
        "spice": max(0, int(cargo_resources.spice)),
    }
    if sum(desired.values()) <= 0:
        return True, []

    form_inputs = client.driver.find_elements(
        By.CSS_SELECTOR,
        f'{form_css} input[type="text"], {form_css} input[type="number"], {form_css} input:not([type])',
    )
    by_key: dict[str, WebElementLike] = {}
    seen_names: list[str] = []
    for inp in form_inputs:
        nm = str(inp.get_attribute("name") or "").strip().lower()
        iid = str(inp.get_attribute("id") or "").strip().lower()
        if nm:
            seen_names.append(nm)
        if nm in ("gal", "sun", "pla", "sid", "todo") or any(k in nm for k in ("ship", "procent", "percent", "speed")):
            continue
        for rk, cands in _TRANSPORT_INPUT_CANDIDATES.items():
            if rk in by_key:
                continue
            if nm in cands or iid in cands or any(c in (nm.replace("-", "_"), iid.replace("-", "_")) for c in cands):
                by_key[rk] = inp
                break

    if any(val > 0 and rk not in by_key for rk, val in desired.items()):
        return False, seen_names

    for rk, val in desired.items():
        el = by_key.get(rk)
        if el is None:
            continue
        if val <= 0 and str(el.get_attribute("value") or "").strip() in ("", "0"):
            continue
        set_text_input_value(client, el, str(max(0, catalog.cap_json_int(val))))
    return True, seen_names


def _fill_step2_cargo_or_abort(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    *,
    action: PlannedAction,
    form_css: str,
    tname_s: str,
    cargo_resources: Resources,
) -> ExecutionResult | None:
    ctx.sleeper(0.15)
    cargo_ok, seen_cargo_inputs = fill_transport_cargo_inputs(client, form_css, cargo_resources)
    if cargo_ok:
        return None
    log_fleet_diag(
        "transport_cargo_inputs_missing",
        {
            "target": tname_s,
            "form_css": form_css,
            "seen_inputs": seen_cargo_inputs[:30],
            "cargo": {
                "pig_iron": cargo_resources.pig_iron,
                "metal": cargo_resources.metal,
                "kryptonite": cargo_resources.kryptonite,
                "spice": cargo_resources.spice,
            },
        },
    )
    return ExecutionResult(
        executed=False,
        dry_run=False,
        action=action,
        message=(
            "Aborted: Transport resource inputs could not be confirmed on internalSendFleet "
            f"(found inputs: {', '.join(seen_cargo_inputs[:10]) or 'none'})."
        ),
        validated=False,
        validation_error="Transport resource inputs missing on internalSendFleet.",
    )


def _log_missing_launch_button(
    client: BrowserClientLike,
    *,
    send_html: str,
    mission_kind: str,
    tname_s: str,
    form_css: str,
    mission_radio: WebElementLike | None,
) -> None:
    post_click_html = str(client.driver.page_source or send_html or "")
    post_soup = BeautifulSoup(post_click_html, "html.parser")
    log_fleet_diag(
        "launch_button_missing",
        {
            "mission_kind": mission_kind,
            "target": tname_s,
            "selected_radio_val": str(mission_radio.get_attribute("value") or "") if mission_radio else None,
            "selected_radio_id": str(mission_radio.get_attribute("id") or "") if mission_radio else None,
            "selected_radio_name": str(mission_radio.get_attribute("name") or "") if mission_radio else None,
            "form_css": form_css,
            "forms_tail": [str(redact_sensitive(str(f)))[-3500:] for f in post_soup.find_all("form")],
            "page_text": " ".join(post_soup.get_text(" ", strip=True).split())[:1500],
        },
    )


def submit_step2_launch(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    *,
    action: PlannedAction,
    send_html: str,
    send_soup: BeautifulSoup,
    form_css: str,
    mission_kind: str,
    tname_s: str,
    cargo_resources: Resources | None,
) -> tuple[str | None, ExecutionResult | None]:
    """Select the Step 2 mission radio, fill optional Transport cargo, and click LAUNCH."""
    client._switch_frame("F_Hauptframe")
    launch_css = build_launch_css(form_css)
    nav_token2: str | None = None
    paced2 = False
    mission_radio: WebElementLike | None = None

    for attempt in range(2):
        try:
            radios = client.driver.find_elements(By.CSS_SELECTOR, f'{form_css} input[type="radio"]')
            mission_radio = match_mission_radio(send_soup, form_css, radios, mission_kind)
            if mission_radio is None:
                return (
                    None,
                    build_missing_radio_result(
                        send_html,
                        send_soup,
                        radios,
                        action=action,
                        mission_kind=mission_kind,
                        tname_s=tname_s,
                        form_css=form_css,
                    ),
                )
            if not mission_radio.is_selected():
                safe_click(client, mission_radio)
            if mission_kind == "TRANSPORT" and cargo_resources is not None and not cargo_resources.is_zero():
                cargo_err = _fill_step2_cargo_or_abort(
                    ctx, client, action=action, form_css=form_css, tname_s=tname_s, cargo_resources=cargo_resources
                )
                if cargo_err is not None:
                    return None, cargo_err
            launch_btn = client.driver.find_element(By.CSS_SELECTOR, launch_css)
            if not paced2:
                nav_token2 = pace_and_mark_nav(ctx, client)
                paced2 = True
            safe_click(client, launch_btn)
            return nav_token2, None
        except StaleElementReferenceException:
            if attempt == 1:
                msg = f"Aborted: {mission_kind.title()} mission controls became stale on internalSendFleet."
                return None, ExecutionResult(False, False, action, msg)
            client._switch_frame("F_Hauptframe")
            refreshed_html = str(client.driver.page_source or "")
            if refreshed_html.strip():
                send_html = refreshed_html
                send_soup = BeautifulSoup(send_html, "html.parser")
                form_css = pick_send_fleet_form_css(send_soup)
                launch_css = build_launch_css(form_css)
        except NoSuchElementException:
            _log_missing_launch_button(
                client,
                send_html=send_html,
                mission_kind=mission_kind,
                tname_s=tname_s,
                form_css=form_css,
                mission_radio=mission_radio,
            )
            msg = f"Aborted: {mission_kind.title()} mission launch button not found on internalSendFleet."
            return None, ExecutionResult(False, False, action, msg)
        except (ElementNotInteractableException, WebDriverException) as exc:
            msg = (
                f"Aborted: {mission_kind.title()} mission controls not interactable on internalSendFleet "
                f"({type(exc).__name__})."
            )
            return None, ExecutionResult(False, False, action, msg)
    return nav_token2, None
