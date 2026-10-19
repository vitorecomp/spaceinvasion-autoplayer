"""Protocols and explicit shared execution context for `exec_ops` collaborators."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
import random
import time
from typing import Protocol

from spaceinvasion.core.model import PlannedAction
from spaceinvasion.core.model import Resources as ModelResources
from spaceinvasion.parsing.models import GameState, Resources
from spaceinvasion.session.browser_client._protocols import WebDriverLike
from spaceinvasion.session.exec_ops._types import ExecutionResult, safe_hourly_cap, safe_optional_wait_limit
from spaceinvasion.session.limiter import PageViewLimiter


class PlannedActionLike(Protocol):
    """Structural protocol satisfied by both `core.model.PlannedAction` and legacy `strategy.PlannedAction`."""

    @property
    def priority(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def planet_id(self) -> int: ...
    @property
    def planet_coords(self) -> str: ...
    @property
    def target_name(self) -> str: ...
    @property
    def reason(self) -> str: ...
    @property
    def target_id(self) -> int | None: ...
    @property
    def target_level(self) -> int | None: ...
    @property
    def cost(self) -> Resources: ...
    @property
    def missing(self) -> Resources: ...
    @property
    def executable_now(self) -> bool: ...
    @property
    def action_href(self) -> str | None: ...


class BrowserClientLike(Protocol):
    """Structural protocol satisfied by both `browser_client.GameBrowserClient` and legacy `client.GameBrowserClient`."""

    driver: WebDriverLike
    page_limiter: PageViewLimiter
    timeout: float
    max_limiter_wait: float | None
    last_galaxy_coords: tuple[int, int] | None
    last_home_daily_bonus_present: bool | None

    def ensure_in_game(self, *, priority: str = "normal") -> None: ...
    def navigate_menu(self, action_key: str, *, priority: str = "normal") -> str: ...
    def open_galaxy_with_coords(
        self, galaxy: int, system: int, *, priority: str = "normal", force_submit: bool = False
    ) -> str: ...
    def open_galaxy(self, *, priority: str = "normal") -> str: ...
    def dispatch_galaxy_spy(
        self, galaxy: int, system: int, position: int, *, priority: str = "interactive"
    ) -> tuple[bool, str, str]: ...
    def open_building_detail(self, building_id: int, *, priority: str = "normal") -> str: ...
    def read_planets_overview(self, *, priority: str = "normal") -> GameState: ...
    def read_full_snapshot(self, *, include_pages: tuple[str, ...] = ..., priority: str = "normal") -> GameState: ...
    def _switch_frame(self, frame_id: str) -> None: ...
    def _mark_frame_navigation(self, frame_id: str = "F_Hauptframe") -> str | None: ...
    def _wait_frame_ready(
        self,
        frame_id: str = "F_Hauptframe",
        *,
        timeout: float | None = None,
        expected_action: str | None = None,
        nav_token: str | None = None,
    ) -> str: ...
    def _check_page_guards(self, html: str) -> None: ...
    def _pace(self, *, priority: str = "normal", cost: int = 1) -> float: ...
    def _extract_selected_planet_id(self) -> int | None: ...


def _to_model_resources(res: Resources | ModelResources) -> ModelResources:
    if isinstance(res, ModelResources):
        return res
    return ModelResources(
        pig_iron=res.pig_iron,
        metal=res.metal,
        kryptonite=res.kryptonite,
        spice=res.spice,
    )


def coerce_planned_action(action: PlannedActionLike) -> PlannedAction:
    """Return a canonical `core.model.PlannedAction` instance."""
    if isinstance(action, PlannedAction):
        return action
    return PlannedAction(
        priority=action.priority,
        kind=action.kind,
        planet_id=action.planet_id,
        planet_coords=action.planet_coords,
        target_name=action.target_name,
        reason=action.reason,
        target_id=action.target_id,
        target_level=action.target_level,
        cost=_to_model_resources(action.cost),
        missing=_to_model_resources(action.missing),
        executable_now=action.executable_now,
        action_href=action.action_href,
    )


@dataclass
class ExecutorContext:
    """Mutable execution state shared by all `exec_ops` collaborators via explicit constructor injection."""

    dry_run: bool = True
    max_actions_per_hour: int = 30
    enforce_hourly_cap: bool = True
    action_delay_range: tuple[float, float] = (0.4, 0.9)
    max_limiter_wait: float | None = None
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] = time.sleep
    rng: Callable[[float, float], float] = random.uniform
    executed_timestamps: deque[float] = field(default_factory=deque)
    observed_active_planet_id: int | None = None
    observed_active_planet_confirmed: bool | None = None
    last_action_at: float = 0.0

    def __post_init__(self) -> None:
        self.max_actions_per_hour = safe_hourly_cap(self.max_actions_per_hour, 30)
        self.max_limiter_wait = safe_optional_wait_limit(self.max_limiter_wait, 60.0)

    def reset_observed_planet(self) -> None:
        self.observed_active_planet_id = None
        self.observed_active_planet_confirmed = None

    def record_observed_planet(self, planet_id: int | None, *, confirmed: bool) -> None:
        if isinstance(planet_id, int) and not isinstance(planet_id, bool) and planet_id > 0:
            self.observed_active_planet_id = planet_id
            self.observed_active_planet_confirmed = bool(confirmed)

    def attach_observed_planet(self, result: ExecutionResult) -> ExecutionResult:
        obs_pid = result.observed_active_planet_id
        obs_conf = result.observed_active_planet_confirmed
        if obs_pid is None and self.observed_active_planet_id is not None:
            obs_pid = self.observed_active_planet_id
            obs_conf = self.observed_active_planet_confirmed
        elif obs_pid is None and result.updated_state is not None:
            active_p = result.updated_state.active_planet()
            if active_p is not None:
                obs_pid = active_p.planet_id
                obs_conf = True
        if obs_pid == result.observed_active_planet_id and obs_conf == result.observed_active_planet_confirmed:
            return result
        from dataclasses import replace

        return replace(result, observed_active_planet_id=obs_pid, observed_active_planet_confirmed=obs_conf)

    def confirmed_planet_mismatch(self, target_planet_id: int) -> bool:
        return (
            self.observed_active_planet_confirmed is True
            and self.observed_active_planet_id is not None
            and self.observed_active_planet_id != target_planet_id
        )

    def prune_hour_window(self) -> None:
        cutoff = self.clock() - 3600.0
        while self.executed_timestamps and self.executed_timestamps[0] < cutoff:
            self.executed_timestamps.popleft()

    def remaining_hourly_budget(self) -> int:
        if not self.enforce_hourly_cap:
            return 999_999
        self.prune_hour_window()
        return max(0, self.max_actions_per_hour - len(self.executed_timestamps))

    def pace_action(self, client: BrowserClientLike | None = None, *, cost: int = 1) -> None:
        limiter = client.page_limiter if client is not None else None
        if isinstance(limiter, PageViewLimiter):
            max_wait = (
                self.max_limiter_wait
                if self.max_limiter_wait is not None
                else (client.max_limiter_wait if client is not None else None)
            )
            limiter.acquire(cost=cost, priority="interactive", delay_range=self.action_delay_range, max_wait=max_wait)
            self.last_action_at = self.clock()
            return
        low, high = self.action_delay_range
        if high > 0:
            self.sleeper(self.rng(low, high))
        self.last_action_at = self.clock()
