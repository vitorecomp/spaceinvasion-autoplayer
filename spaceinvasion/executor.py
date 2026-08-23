"""Safe action executor for SpaceInvasion Classic Selenium browser control."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import random
import time
from typing import Callable, Deque, Optional

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    StaleElementReferenceException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

from spaceinvasion.client import GameBrowserClient
from spaceinvasion.state import (
    GameState,
    parse_buildings_page,
    parse_economy_rates,
    parse_research_page,
    parse_unit_factory_page,
)
from spaceinvasion.strategy import PlannedAction


@dataclass(frozen=True)
class ExecutionResult:
    """Outcome of attempting one planned action."""

    executed: bool
    dry_run: bool
    action: PlannedAction
    message: str


class ActionExecutor:
    """Executes approved `PlannedAction` items in Chrome with rate and queue guards."""

    def __init__(
        self,
        *,
        dry_run: bool = True,
        max_actions_per_hour: int = 30,
        action_delay_range: tuple[float, float] = (0.4, 0.9),
        allow_fleetsave: bool = False,
    ) -> None:
        self.dry_run = dry_run
        self.max_actions_per_hour = max_actions_per_hour
        self.action_delay_range = action_delay_range
        self.allow_fleetsave = allow_fleetsave
        self._recent_timestamps: Deque[float] = deque()
        self.last_economy_rates: dict[int, dict[str, int]] = {}

    def _prune_hour_window(self, now: float) -> None:
        cutoff = now - 3600.0
        while self._recent_timestamps and self._recent_timestamps[0] < cutoff:
            self._recent_timestamps.popleft()

    def remaining_hourly_budget(self) -> int:
        now = time.monotonic()
        self._prune_hour_window(now)
        return max(0, self.max_actions_per_hour - len(self._recent_timestamps))

    def _select_planet_if_needed(self, client: GameBrowserClient, planet_id: int) -> None:
        """Switch active planet in `F_Hauptframe` if `select[name='planet']` doesn't match."""
        client._switch_frame("F_Hauptframe")
        try:
            select_el = client.driver.find_element(By.CSS_SELECTOR, 'select[name="planet"]')
        except NoSuchElementException:
            return
        options = select_el.find_elements(By.TAG_NAME, "option")
        for opt in options:
            val = opt.get_attribute("value") or ""
            if f"newPlanet={planet_id}" in val:
                if not opt.is_selected():
                    opt.click()
                    time.sleep(1.0)
                return

    def _navigate_and_select_planet(
        self,
        client: GameBrowserClient,
        state: GameState,
        menu_action: str,
        planet_id: int,
    ) -> str:
        client.ensure_in_game()
        html = client.navigate_menu(menu_action)
        if state.active_planet_id != planet_id:
            self._select_planet_if_needed(client, planet_id)
            html = client._wait_frame_ready("F_Hauptframe")
        return html

    def _click_dom_link_with_retry(
        self,
        client: GameBrowserClient,
        action: PlannedAction,
        initial_html: str,
        selector: str,
        is_ready_fn: Callable[[str], bool],
    ) -> tuple[Optional[str], Optional[ExecutionResult]]:
        """Verify offer availability and click `selector`, retrying once on countdown reload."""
        html = initial_html
        for attempt in range(2):
            if not is_ready_fn(html):
                return None, ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: {action.target_name} is no longer actionable on page.",
                )
            try:
                client._switch_frame("F_Hauptframe")
                btn = client.driver.find_element(By.CSS_SELECTOR, selector)
                client.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});",
                    btn,
                )
                time.sleep(random.uniform(*self.action_delay_range))
                try:
                    btn.click()
                except ElementClickInterceptedException:
                    client.driver.execute_script("arguments[0].click();", btn)
                after_html = client._wait_frame_ready("F_Hauptframe")
                self._recent_timestamps.append(time.monotonic())
                return after_html, None
            except StaleElementReferenceException:
                if attempt == 1:
                    raise
                html = client._wait_frame_ready("F_Hauptframe")
            except NoSuchElementException:
                return None, ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Aborted: action button for {action.target_name} not found in DOM.",
                )
        return None, ExecutionResult(
            executed=False,
            dry_run=False,
            action=action,
            message=f"Aborted: failed to click {action.target_name}.",
        )

    def _execute_claim_tutorial(
        self,
        client: GameBrowserClient,
        action: PlannedAction,
    ) -> ExecutionResult:
        """Expand `#tutorial.text` if collapsed and submit `Mission complete` in `internalHome`."""
        client.ensure_in_game()
        client.navigate_menu("internalHome")
        client._switch_frame("F_Hauptframe")

        tut_div = client.driver.find_element(By.ID, "tutorial.text")
        if not tut_div.is_displayed():
            try:
                client.driver.find_element(By.ID, "tutorial.show").click()
                time.sleep(0.3)
            except NoSuchElementException:
                client.driver.execute_script("arguments[0].style.display='block';", tut_div)

        submit_btn = client.driver.find_element(
            By.CSS_SELECTOR,
            'form[action*="internalHome"] input[name="tutorialComplete"] ~ input[type="submit"]',
        )
        time.sleep(random.uniform(*self.action_delay_range))
        submit_btn.click()
        client._wait_frame_ready("F_Hauptframe")
        self._recent_timestamps.append(time.monotonic())
        client.read_planets_overview(refresh=True)
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=action,
            message=f"Claimed {action.target_name}.",
        )

    def _execute_build(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        html = self._navigate_and_select_planet(client, state, "internalBuildings", action.planet_id)
        planet_map = next(
            (p.buildings for p in state.planets if p.planet_id == action.planet_id),
            None,
        )
        selector = f'a.bau[href*="internalBuildItem"][href*="buildingID={action.target_id}"]'

        def _ready(page_html: str) -> bool:
            offers = parse_buildings_page(page_html, planet_map)
            m = next((o for o in offers if o.building_id == action.target_id), None)
            return bool(m and m.can_build)

        after_html, err = self._click_dom_link_with_retry(client, action, html, selector, _ready)
        if err is not None:
            return err
        assert after_html is not None

        updated = client.read_planets_overview(refresh=True)
        after_planet = next(
            (p for p in updated.planets if p.planet_id == action.planet_id),
            updated.active_planet(),
        )
        new_lvl = after_planet.building_level(int(action.target_id or 0)) if after_planet else None
        after_lower = after_html.lower()
        in_queue = (
            "construction queue" in after_lower
            or "abort" in after_lower
            or "cancel" in after_lower
            or (new_lvl is not None and action.target_level is not None and new_lvl >= action.target_level)
        )
        detail = (
            f"Built/queued {action.target_name} -> L{action.target_level} "
            f"(verified={'ok' if in_queue else 'submitted'}, current_lvl={new_lvl})."
        )
        return ExecutionResult(executed=True, dry_run=False, action=action, message=detail)

    def _execute_research(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        html = self._navigate_and_select_planet(client, state, "internalResearch", action.planet_id)
        selector = f'a.bau[href*="internalBuildItem"][href*="researchID={action.target_id}"]'

        def _ready(page_html: str) -> bool:
            offers = parse_research_page(page_html, state.researches)
            m = next((o for o in offers if o.research_id == action.target_id), None)
            return bool(m and m.can_research)

        after_html, err = self._click_dom_link_with_retry(client, action, html, selector, _ready)
        if err is not None:
            return err
        assert after_html is not None

        updated = client.read_planets_overview(refresh=True)
        new_lvl = int(updated.researches.get(f"r{action.target_id}", 0))
        after_lower = after_html.lower()
        verified = (
            "cancel" in after_lower
            or "abort" in after_lower
            or (action.target_level is not None and new_lvl >= action.target_level)
        )
        detail = (
            f"Started research {action.target_name} -> L{action.target_level} "
            f"(verified={'ok' if verified else 'submitted'}, current_lvl={new_lvl})."
        )
        return ExecutionResult(executed=True, dry_run=False, action=action, message=detail)

    def _execute_build_ship(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        html = self._navigate_and_select_planet(client, state, "internalWeaponFactory", action.planet_id)
        planet = next((p for p in state.planets if p.planet_id == action.planet_id), None)
        offers = parse_unit_factory_page(
            html,
            unit_type="ship",
            owned_counts=planet.ships if planet else None,
        )
        matching = next((o for o in offers if o.unit_id == action.target_id), None)
        if matching is None or not matching.can_build or not matching.input_name:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: ship {action.target_name} input unavailable in Weapons factory.",
            )

        qty = max(1, int(action.target_level or 1))
        client._switch_frame("F_Hauptframe")
        try:
            qty_input = client.driver.find_element(By.NAME, matching.input_name)
            qty_input.clear()
            qty_input.send_keys(str(qty))
            form = client.driver.find_element(By.CSS_SELECTOR, 'form[action*="buildShip=1"]')
            time.sleep(random.uniform(*self.action_delay_range))
            submit_btn = form.find_element(By.CSS_SELECTOR, 'input[type="submit"], button[type="submit"]')
            submit_btn.click()
        except NoSuchElementException:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: shipyard form controls for {action.target_name} not found.",
            )

        client._wait_frame_ready("F_Hauptframe")
        self._recent_timestamps.append(time.monotonic())
        client.read_planets_overview(refresh=True)
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=action,
            message=f"Ordered {qty}x {action.target_name} on [{action.planet_coords}].",
        )

    def _execute_throttle_building(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        bid = int(action.target_id or 2)
        if bid not in (1, 2, 3, 4, 5, 6):
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: building b{bid} does not support internalEconomy rate control.",
            )

        field_name = f"resource{bid}" if bid <= 4 else "resource5"
        raw_pct = 0 if action.target_level is None else int(action.target_level)
        target_pct = max(0, min(100, (raw_pct // 10) * 10))

        html = self._navigate_and_select_planet(client, state, "internalEconomy", action.planet_id)
        cur_rates = parse_economy_rates(html)
        if cur_rates:
            self.last_economy_rates[action.planet_id] = dict(cur_rates)
        if field_name in cur_rates and cur_rates[field_name] == target_pct:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"No-op: {action.target_name} ({field_name}) is already at {target_pct}% on [{action.planet_coords}].",
            )

        client._switch_frame("F_Hauptframe")
        try:
            select_el = client.driver.find_element(By.NAME, field_name)
            Select(select_el).select_by_value(str(target_pct))
            submit_btn = client.driver.find_element(
                By.CSS_SELECTOR,
                'form#ress input[type="submit"], form[action*="internalEconomy"] input[type="submit"], form input[type="submit"]',
            )
            time.sleep(random.uniform(*self.action_delay_range))
            submit_btn.click()
        except NoSuchElementException:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: internalEconomy control {field_name} not found.",
            )

        updated_html = client._wait_frame_ready("F_Hauptframe")
        updated_rates = parse_economy_rates(updated_html)
        if not updated_rates:
            updated_rates = dict(cur_rates)
        updated_rates[field_name] = target_pct
        self.last_economy_rates[action.planet_id] = dict(updated_rates)

        self._recent_timestamps.append(time.monotonic())
        client.read_planets_overview(refresh=True)
        return ExecutionResult(
            executed=True,
            dry_run=False,
            action=action,
            message=f"Set {action.target_name} ({field_name}) to {target_pct}% on [{action.planet_coords}].",
        )

    def execute_action(
        self,
        client: GameBrowserClient,
        state: GameState,
        action: PlannedAction,
    ) -> ExecutionResult:
        """Execute one approved action (`BUILD`, `CLAIM_TUTORIAL`, `RESEARCH`, `BUILD_SHIP`, `THROTTLE_BUILDING`)."""
        if not action.executable_now:
            return ExecutionResult(
                executed=False,
                dry_run=self.dry_run,
                action=action,
                message="Skipped: action is not currently executable.",
            )

        supported = (
            "BUILD",
            "CLAIM_TUTORIAL",
            "RESEARCH",
            "BUILD_SHIP",
            "THROTTLE_BUILDING",
            "ESCAPE_FLIGHT",
        )
        if action.kind not in supported:
            return ExecutionResult(
                executed=False,
                dry_run=self.dry_run,
                action=action,
                message=f"Skipped: unsupported live action kind {action.kind}.",
            )
        if (
            action.kind in ("BUILD", "RESEARCH", "BUILD_SHIP", "THROTTLE_BUILDING")
            and action.target_id is None
        ):
            return ExecutionResult(
                executed=False,
                dry_run=self.dry_run,
                action=action,
                message=f"Skipped: {action.kind} action missing target_id.",
            )

        if self.dry_run:
            suffix = ""
            if action.kind == "THROTTLE_BUILDING" and action.target_level is not None:
                suffix = f" -> {action.target_level}%"
            elif action.kind == "BUILD_SHIP" and action.target_level:
                suffix = f" x{action.target_level}"
            elif action.target_level:
                suffix = f" -> L{action.target_level}"
            return ExecutionResult(
                executed=False,
                dry_run=True,
                action=action,
                message=f"[DRY-RUN] Would execute {action.kind}: {action.target_name}{suffix} on [{action.planet_coords}].",
            )

        if action.kind == "ESCAPE_FLIGHT" and not self.allow_fleetsave:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Guarded: live ESCAPE_FLIGHT requires allow_fleetsave=true in config.",
            )

        if self.remaining_hourly_budget() <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Paused: hourly action rate cap reached.",
            )

        if action.kind == "CLAIM_TUTORIAL":
            return self._execute_claim_tutorial(client, action)
        if action.kind == "BUILD":
            return self._execute_build(client, state, action)
        if action.kind == "RESEARCH":
            return self._execute_research(client, state, action)
        if action.kind == "BUILD_SHIP":
            return self._execute_build_ship(client, state, action)
        if action.kind == "THROTTLE_BUILDING":
            return self._execute_throttle_building(client, state, action)

        return ExecutionResult(
            executed=False,
            dry_run=False,
            action=action,
            message=f"Escape flight planned ({action.reason}); inspect internalFleet before dispatch.",
        )
