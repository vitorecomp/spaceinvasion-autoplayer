"""Planet selection, frame navigation, DOM link retry, and post-action state merging collaborator."""

from __future__ import annotations

from collections.abc import Callable

from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By

from backend.core.model import PlannedAction
from backend.parsing.models import GameState
from backend.session.executor._dom import mark_frame_nav, safe_click
from backend.session.executor._planet_nav import navigate_and_select_planet_impl, select_planet_if_needed_impl
from backend.session.executor._planet_state import (
    ActionInput,
    as_planned_action,
    build_updated_state_impl,
    confirmed_planet_mismatch_id,
    format_planet_mismatch_message,
    pace_client_action,
    sleep_action_delay,
)
from backend.session.executor._protocols import BrowserClientLike, ExecutorContext
from backend.session.executor._types import ExecutionResult, safe_str


class PlanetSelector:
    """Explicit collaborator owning planet switching, menu nav, DOM link retry, and state rebuilding."""

    def __init__(
        self,
        ctx: ExecutorContext,
        *,
        nav_hook: Callable[..., str] | None = None,
    ) -> None:
        self._ctx = ctx
        self._nav_hook = nav_hook
        self.last_economy_rates: dict[int, dict[str, int]] = {}

    @property
    def ctx(self) -> ExecutorContext:
        return self._ctx

    def build_updated_state(
        self,
        client: BrowserClientLike,
        prev_state: GameState | None,
        action: ActionInput,
        updated_overview: object,
        *,
        page_html: str = "",
        is_home: bool = False,
        is_fleet: bool = False,
        include_buildings: bool = False,
        include_research: bool = False,
        include_shipyard: bool = False,
    ) -> GameState | None:
        """Build a merged `GameState` from refreshed overview + current page HTML and cached rates."""
        return build_updated_state_impl(
            self._ctx,
            self.last_economy_rates,
            client,
            prev_state,
            action,
            updated_overview,
            page_html=page_html,
            is_home=is_home,
            is_fleet=is_fleet,
            include_buildings=include_buildings,
            include_research=include_research,
            include_shipyard=include_shipyard,
        )

    def select_planet_if_needed(
        self,
        client: BrowserClientLike,
        planet_id: int,
        *,
        html: str = "",
        require_confirm: bool = False,
    ) -> tuple[bool, str | None]:
        """Switch active planet in `F_Hauptframe` using `<select name='planet'>` `newPlanet=` options."""
        return select_planet_if_needed_impl(
            self._ctx,
            client,
            planet_id,
            html=html,
            require_confirm=require_confirm,
        )

    def navigate_and_select_planet_impl(
        self,
        client: BrowserClientLike,
        state: GameState,
        menu_action: str,
        planet_id: int,
        *,
        active_planet_confirmed: bool = True,
    ) -> str:
        """Navigate `F_Hauptframe` to `menu_action` and switch to `planet_id` when required."""
        return navigate_and_select_planet_impl(
            self._ctx,
            client,
            state,
            menu_action,
            planet_id,
            active_planet_confirmed=active_planet_confirmed,
        )

    def navigate_and_select_planet(
        self,
        client: BrowserClientLike,
        state: GameState,
        menu_action: str,
        planet_id: int,
        *,
        active_planet_confirmed: bool = True,
    ) -> str:
        """Navigate `F_Hauptframe` to `menu_action`, honoring optional facade hook patches."""
        if self._nav_hook is not None:
            return self._nav_hook(
                client,
                state,
                menu_action,
                planet_id,
                active_planet_confirmed=active_planet_confirmed,
            )
        return self.navigate_and_select_planet_impl(
            client,
            state,
            menu_action,
            planet_id,
            active_planet_confirmed=active_planet_confirmed,
        )

    def planet_mismatch_abort(self, action: PlannedAction) -> ExecutionResult | None:
        """Return an abort `ExecutionResult` if confirmed active planet mismatches `action.planet_id`."""
        mismatch_pid = confirmed_planet_mismatch_id(self._ctx, action.planet_id)
        if mismatch_pid is None:
            return None
        return ExecutionResult(
            executed=False,
            dry_run=False,
            action=action,
            message=format_planet_mismatch_message(mismatch_pid, action.planet_id),
            observed_active_planet_id=mismatch_pid,
            observed_active_planet_confirmed=True,
        )

    def click_dom_link_with_retry(
        self,
        client: BrowserClientLike,
        action: ActionInput,
        initial_html: str,
        selector: str,
        is_ready_fn: Callable[[str], bool],
    ) -> tuple[str | None, ExecutionResult | None]:
        """Verify offer availability and click `selector`, retrying once on countdown reload."""
        act = as_planned_action(action)
        tname_s = safe_str(act.target_name)
        html = initial_html
        for attempt in range(2):
            if not is_ready_fn(html):
                return None, ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=act,
                    message=f"Aborted: {tname_s} is no longer actionable on page.",
                )
            try:
                client._switch_frame("F_Hauptframe")
                btn = client.driver.find_element(By.CSS_SELECTOR, selector)
                pace_client_action(self._ctx, client, priority="interactive")
                nav_token = mark_frame_nav(client)
                sleep_action_delay(self._ctx)
                safe_click(client, btn)
                after_html = client._wait_frame_ready("F_Hauptframe", nav_token=nav_token)
                self._ctx.executed_timestamps.append(self._ctx.clock())
                return after_html, None
            except StaleElementReferenceException:
                if attempt == 1:
                    return None, ExecutionResult(
                        executed=False,
                        dry_run=False,
                        action=act,
                        message=f"Aborted: action button for {tname_s} became stale.",
                    )
                html = client._wait_frame_ready("F_Hauptframe")
            except NoSuchElementException:
                return None, ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=act,
                    message=f"Aborted: action button for {tname_s} not found in DOM.",
                )
            except (ElementNotInteractableException, WebDriverException) as exc:
                return None, ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=act,
                    message=f"Aborted: action button for {tname_s} not interactable ({type(exc).__name__}).",
                )
        return None, ExecutionResult(
            executed=False,
            dry_run=False,
            action=act,
            message=f"Aborted: failed to click {tname_s}.",
        )
