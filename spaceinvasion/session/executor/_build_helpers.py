"""Decomposed DOM submit and post-action verification helpers for `BuildOps`."""

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
from spaceinvasion.parsing.models import GameState
from spaceinvasion.session.executor._dom import mark_frame_nav, safe_click, set_text_input_value
from spaceinvasion.session.executor._planet_state import pace_client_action, sleep_action_delay
from spaceinvasion.session.executor._protocols import BrowserClientLike, ExecutorContext
from spaceinvasion.session.executor._types import ExecutionResult, safe_str

_DEFAULT_TUTORIAL_SUBMIT_CSS = 'form[action*="internalHome"] input[name="tutorialComplete"] ~ input[type="submit"]'
_FALLBACK_TUTORIAL_SUBMIT_CSS = (
    'form:has(input[name="tutorialComplete"]) input[type="submit"], '
    'form:has(input[name="tutorialComplete"]) button[type="submit"], '
    'input[name="tutorialComplete"] ~ input[type="submit"], '
    'input[name="tutorialComplete"] ~ button[type="submit"]'
)


def resolve_tutorial_form_locators(home_html: str) -> tuple[str, bool, bool]:
    """Inspect `internalHome` HTML for `#tutorial.text` and `tutorialComplete` submit selector."""
    submit_css = _DEFAULT_TUTORIAL_SUBMIT_CSS
    has_tut_div = True
    has_complete = False
    if home_html:
        home_soup = BeautifulSoup(home_html, "html.parser")
        has_tut_div = home_soup.find(id="tutorial.text") is not None
        has_complete = home_soup.select_one('input[name="tutorialComplete"]') is not None
        if has_complete and home_soup.select_one(submit_css) is None:
            submit_css = _FALLBACK_TUTORIAL_SUBMIT_CSS
    return submit_css, has_tut_div, has_complete


def submit_tutorial_complete_dom(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    action: PlannedAction,
    submit_css: str,
    *,
    has_tut_div: bool,
    has_complete: bool,
) -> tuple[str | None, ExecutionResult | None]:
    """Expand `#tutorial.text` if collapsed and submit the tutorial completion button."""
    nav_token = mark_frame_nav(client)
    paced = False
    for attempt in range(2):
        try:
            if has_tut_div or not has_complete:
                tut_div = client.driver.find_element(By.ID, "tutorial.text")
                if not tut_div.is_displayed():
                    try:
                        safe_click(client, client.driver.find_element(By.ID, "tutorial.show"))
                        ctx.sleeper(0.3)
                    except (NoSuchElementException, ElementNotInteractableException, WebDriverException):
                        client.driver.execute_script("arguments[0].style.display='block';", tut_div)

            submit_btn = client.driver.find_element(By.CSS_SELECTOR, submit_css)
            if not paced:
                pace_client_action(ctx, client, priority="interactive")
                nav_token = mark_frame_nav(client)
                sleep_action_delay(ctx)
                paced = True
            safe_click(client, submit_btn)
            return nav_token, None
        except StaleElementReferenceException:
            if attempt == 0:
                continue
            return None, ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Aborted: tutorial completion DOM controls became stale.",
            )
        except NoSuchElementException:
            return None, ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Aborted: tutorial completion DOM controls not found.",
            )
        except (ElementNotInteractableException, WebDriverException) as exc:
            return None, ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: tutorial completion DOM controls not interactable ({type(exc).__name__}).",
            )
    return nav_token, None


def verify_build_outcome(
    state: GameState,
    updated: GameState,
    action: PlannedAction,
    after_html: str,
) -> tuple[str, bool, str | None]:
    """Verify whether a clicked building upgrade entered queue, spent resources, or leveled up."""
    after_planet = next((p for p in updated.planets if p.planet_id == action.planet_id), updated.active_planet())
    new_lvl = after_planet.building_level(catalog.safe_int(action.target_id, 0)) if after_planet else None
    after_lower = after_html.lower()
    tgt_lvl_opt = catalog.safe_optional_int(action.target_level, None)
    before_planet = next((p for p in state.planets if p.planet_id == action.planet_id), None)
    res_spent = (
        before_planet is not None
        and after_planet is not None
        and (
            after_planet.resources.pig_iron < before_planet.resources.pig_iron
            or after_planet.resources.metal < before_planet.resources.metal
            or after_planet.resources.kryptonite < before_planet.resources.kryptonite
            or after_planet.resources.spice < before_planet.resources.spice
        )
    )
    in_queue = (
        "construction queue" in after_lower
        or "abort" in after_lower
        or "cancel" in after_lower
        or any(q.category == "building" for q in updated.active_queues)
        or res_spent
        or (new_lvl is not None and tgt_lvl_opt is not None and new_lvl >= tgt_lvl_opt)
    )
    is_validated = bool(in_queue or len(after_html.strip()) < 200)
    val_err = (
        None
        if is_validated
        else f"Build {safe_str(action.target_name)} -> L{tgt_lvl_opt} was clicked, yet neither construction queue, building level, nor resources changed."
    )
    tgt_txt = catalog.fmt_compact_int(tgt_lvl_opt) if tgt_lvl_opt is not None else "None"
    new_lvl_txt = catalog.fmt_compact_int(new_lvl) if new_lvl is not None else "None"
    detail = (
        f"Built/queued {safe_str(action.target_name)} -> L{tgt_txt} "
        f"(verified={'ok' if in_queue else 'submitted'}, current_lvl={new_lvl_txt})."
    )
    return detail, is_validated, val_err


def verify_research_outcome(
    updated: GameState,
    action: PlannedAction,
    after_html: str,
) -> tuple[str, bool, str | None]:
    """Verify whether a clicked research upgrade entered queue or leveled up."""
    rkey = f"r{catalog.cap_json_int(catalog.safe_int(action.target_id, 0))}"
    new_lvl = catalog.safe_int(updated.researches.get(rkey, 0), 0)
    after_lower = after_html.lower()
    tgt_lvl_opt = catalog.safe_optional_int(action.target_level, None)
    verified = (
        "cancel" in after_lower
        or "abort" in after_lower
        or any(q.category == "research" for q in updated.active_queues)
        or (tgt_lvl_opt is not None and new_lvl >= tgt_lvl_opt)
    )
    res_validated = bool(verified or len(after_html.strip()) < 200)
    res_val_err = (
        None
        if res_validated
        else f"Research {safe_str(action.target_name)} -> L{tgt_lvl_opt} was clicked, yet neither research queue nor level changed."
    )
    tgt_txt = catalog.fmt_compact_int(tgt_lvl_opt) if tgt_lvl_opt is not None else "None"
    detail = (
        f"Started research {safe_str(action.target_name)} -> L{tgt_txt} "
        f"(verified={'ok' if verified else 'submitted'}, current_lvl={catalog.fmt_compact_int(new_lvl)})."
    )
    return detail, res_validated, res_val_err


def submit_shipyard_order_dom(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    action: PlannedAction,
    input_name: str,
    qty: int,
    tname_s: str,
) -> tuple[str | None, ExecutionResult | None]:
    """Fill ship quantity input and submit the Weapons Factory `buildShip=1` form."""
    client._switch_frame("F_Hauptframe")
    nav_token = mark_frame_nav(client)
    paced = False
    for attempt in range(2):
        try:
            qty_input = client.driver.find_element(By.NAME, input_name)
            set_text_input_value(client, qty_input, str(qty))
            form = client.driver.find_element(By.CSS_SELECTOR, 'form[action*="buildShip=1"]')
            if not paced:
                pace_client_action(ctx, client, priority="interactive")
                nav_token = mark_frame_nav(client)
                sleep_action_delay(ctx)
                paced = True
            submit_btn = form.find_element(By.CSS_SELECTOR, 'input[type="submit"], button[type="submit"]')
            safe_click(client, submit_btn)
            return nav_token, None
        except StaleElementReferenceException:
            if attempt == 0:
                continue
            return None, ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: shipyard form controls for {tname_s} became stale.",
            )
        except NoSuchElementException:
            return None, ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: shipyard form controls for {tname_s} not found.",
            )
        except (ElementNotInteractableException, WebDriverException) as exc:
            return None, ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: shipyard form controls for {tname_s} not interactable ({type(exc).__name__}).",
            )
    return nav_token, None
