"""Decomposed `internalEconomy` building-throttling helpers for `BuildOps`."""

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
from spaceinvasion.core.model import PlannedAction, normalize_planet_type
from spaceinvasion.parsing.models import GameState
from spaceinvasion.parsing.pages import parse_economy_rates
from spaceinvasion.session.executor._dom import mark_frame_nav, safe_click
from spaceinvasion.session.executor._planet_state import (
    pace_client_action,
    read_planets_overview_safe,
    sleep_action_delay,
)
from spaceinvasion.session.executor._protocols import BrowserClientLike, ExecutorContext
from spaceinvasion.session.executor._types import ExecutionResult, safe_str
from spaceinvasion.session.executor.planet_select import PlanetSelector

_ECON_CALC_CSS = 'form[name="economy"] input[type="submit"][value="Calculate"]'
_ECON_ANY_SUBMIT_CSS = 'form[name="economy"] input[type="submit"], form[name="economy"] button[type="submit"]'
_ECON_FALLBACK_SUBMIT_CSS = (
    'form#ress input[type="submit"], form[action*="internalEconomy"] input[type="submit"], form input[type="submit"]'
)


def _validate_throttle_request(
    state: GameState,
    action: PlannedAction,
) -> tuple[int, int, ExecutionResult | None]:
    planet_obj = next((p for p in state.planets if p.planet_id == action.planet_id), None)
    if planet_obj is not None:
        ptype_norm = normalize_planet_type(planet_obj)
        if ptype_norm in ("asteroid", "raid"):
            return (
                0,
                0,
                ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: internalEconomy throttling is not supported on {ptype_norm} planets.",
                ),
            )
    bid = 2 if action.target_id is None else catalog.safe_int(action.target_id, -1)
    if bid not in (1, 2, 3, 4, 5, 6):
        return (
            0,
            0,
            ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: building b{catalog.fmt_compact_int(bid)} does not support internalEconomy rate control.",
            ),
        )
    raw_pct = 0 if action.target_level is None else catalog.safe_int(action.target_level, 0)
    target_pct = max(0, min(100, (raw_pct // 10) * 10))
    return bid, target_pct, None


def _resolve_economy_submit_css(html: str) -> str:
    econ_soup = BeautifulSoup(html, "html.parser")
    if econ_soup.select_one(_ECON_CALC_CSS) is not None:
        return _ECON_CALC_CSS
    if econ_soup.select_one(_ECON_ANY_SUBMIT_CSS) is not None:
        return _ECON_ANY_SUBMIT_CSS
    return _ECON_FALLBACK_SUBMIT_CSS


def _submit_throttle_select_dom(
    ctx: ExecutorContext,
    client: BrowserClientLike,
    action: PlannedAction,
    field_name: str,
    target_pct: int,
    submit_css: str,
) -> tuple[str | None, ExecutionResult | None]:
    client._switch_frame("F_Hauptframe")
    nav_token = mark_frame_nav(client)
    paced = False
    for attempt in range(2):
        try:
            select_el = client.driver.find_element(By.NAME, field_name)
            import spaceinvasion.session.executor as exec_pkg

            exec_pkg.Select(select_el).select_by_value(str(target_pct))  # type: ignore[arg-type]
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
                message=f"Aborted: internalEconomy control {field_name} became stale.",
            )
        except NoSuchElementException:
            return None, ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: internalEconomy control {field_name} not found.",
            )
        except (ElementNotInteractableException, WebDriverException) as exc:
            return None, ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: internalEconomy control {field_name} not interactable ({type(exc).__name__}).",
            )
    return nav_token, None


def execute_throttle_building_impl(
    ctx: ExecutorContext,
    selector: PlanetSelector,
    client: BrowserClientLike,
    state: GameState,
    action: PlannedAction,
) -> ExecutionResult:
    """Set a resource building's production rate on `internalEconomy` and verify persistence."""
    bid, target_pct, pre_err = _validate_throttle_request(state, action)
    if pre_err is not None:
        return pre_err

    field_name = f"resource{bid}"
    html = selector.navigate_and_select_planet(client, state, "internalEconomy", action.planet_id)
    mismatch_res = selector.planet_mismatch_abort(action)
    if mismatch_res is not None:
        return mismatch_res
    if not str(html or "").strip():
        return ExecutionResult(
            executed=False,
            dry_run=False,
            action=action,
            message=f"Aborted: failed to load internalEconomy for planet {catalog.fmt_compact_int(action.planet_id)}.",
        )

    tname_s = safe_str(action.target_name)
    coords_s = safe_str(action.planet_coords)
    cur_rates = parse_economy_rates(html)
    if bid == 6 and "resource6" not in cur_rates and "resource5" in cur_rates:
        field_name = "resource5"
    if cur_rates:
        selector.last_economy_rates[action.planet_id] = dict(cur_rates)
    if field_name in cur_rates and cur_rates[field_name] == target_pct:
        return ExecutionResult(
            executed=False,
            dry_run=False,
            action=action,
            message=f"No-op: {tname_s} ({field_name}) is already at {target_pct}% on [{coords_s}].",
        )

    submit_css = _resolve_economy_submit_css(html)
    nav_token, dom_err = _submit_throttle_select_dom(ctx, client, action, field_name, target_pct, submit_css)
    if dom_err is not None:
        return dom_err

    updated_html = client._wait_frame_ready("F_Hauptframe", nav_token=nav_token)
    post_rates = parse_economy_rates(updated_html)
    if field_name in post_rates and post_rates[field_name] != target_pct:
        selector.last_economy_rates[action.planet_id] = dict(post_rates)
        return ExecutionResult(
            executed=False,
            dry_run=False,
            action=action,
            message=(
                f"Aborted: internalEconomy Calculate submit did not persist "
                f"{field_name}={target_pct}% (still {post_rates[field_name]}%) on [{coords_s}]."
            ),
        )
    updated_rates = dict(post_rates) if post_rates else dict(cur_rates)
    updated_rates[field_name] = target_pct
    selector.last_economy_rates[action.planet_id] = dict(updated_rates)

    ctx.executed_timestamps.append(ctx.clock())
    updated = read_planets_overview_safe(client)
    updated_state = selector.build_updated_state(
        client,
        state,
        action,
        updated,
        page_html=updated_html if isinstance(updated_html, str) else "",
    )
    return ExecutionResult(
        executed=True,
        dry_run=False,
        action=action,
        message=f"Set {tname_s} ({field_name}) to {target_pct}% on [{coords_s}].",
        updated_state=updated_state,
    )
