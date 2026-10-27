"""Protocols and explicit shared execution context for `exec_ops` collaborators."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
import random
from typing import Protocol

from spaceinvasion.core.model import PlannedAction
from spaceinvasion.core.model import Resources as ModelResources
from spaceinvasion.parsing.models import GameState, Resources
from spaceinvasion.session.client._protocols import WebDriverLike
from spaceinvasion.session.executor._types import ExecutionResult, safe_hourly_cap, safe_optional_wait_limit
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
    def cost(self) -> Resources | ModelResources: ...
    @property
    def missing(self) -> Resources | ModelResources: ...
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
    def navigate_menu(self, action: str, *, priority: str = "normal") -> str: ...
    def open_galaxy_with_coords(
        self,
        *,
        galaxy: int | None = None,
        system: int | None = None,
        priority: str = "interactive",
    ) -> tuple[str, int, int]: ...
    def open_galaxy(
        self,
        *,
        galaxy: int | None = None,
        system: int | None = None,
        priority: str = "interactive",
    ) -> str: ...
    def dispatch_galaxy_spy(
        self,
        *,
        galaxy: int,
        system: int,
        position: int,
        quantity: int = 1,
        is_asteroid: bool = False,
        priority: str = "interactive",
    ) -> dict[str, object]: ...
    def open_building_detail(self, building_id: int, *, priority: str = "interactive") -> str: ...
    def read_planets_overview(self, *, refresh: bool = False, priority: str = "normal") -> GameState: ...
    def read_menu_status(self) -> tuple[dict[str, int], int | None, int | None]: ...
    def _switch_frame(self, frame_id: str) -> None: ...
    def _mark_frame_navigation(self, frame_id: str) -> str: ...
    def _wait_frame_ready(
        self,
        frame_id: str,
        expected_action: str | None = None,
        *,
        nav_token: str | None = None,
    ) -> str: ...
    def _check_page_guards(self, html: str) -> None: ...
    def _pace(self, *, priority: str = "normal", cost: int = 1) -> float: ...
    @staticmethod
    def _extract_selected_planet_id(html: str, url: str = "") -> int | None: ...


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


def _exec_monotonic() -> float:
    import spaceinvasion.session.executor as exec_pkg

    return float(exec_pkg.time.monotonic())


def _exec_sleep(seconds: float) -> None:
    import spaceinvasion.session.executor as exec_pkg

    exec_pkg.time.sleep(seconds)


@dataclass
class ExecutorContext:
    """Mutable execution state shared by all `executor` collaborators via explicit constructor injection."""

    dry_run: bool = True
    max_actions_per_hour: int = 30
    enforce_hourly_cap: bool = True
    action_delay_range: tuple[float, float] = (0.4, 0.9)
    page_limiter: PageViewLimiter | None = None
    max_limiter_wait: float | None = None
    clock: Callable[[], float] = _exec_monotonic
    sleeper: Callable[[float], None] = _exec_sleep
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

    def attach_observed_planet(self, result: ExecutionResult) -> ExecutionResult:
        obs_pid = result.observed_active_planet_id
        obs_conf = result.observed_active_planet_confirmed
        if obs_pid is None:
            obs_pid = self.observed_active_planet_id
        if obs_conf is None:
            obs_conf = self.observed_active_planet_confirmed
        if obs_pid is result.observed_active_planet_id and obs_conf is result.observed_active_planet_confirmed:
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

    def pace_action(
        self,
        client: BrowserClientLike | None = None,
        *,
        cost: int = 1,
        priority: str = "interactive",
    ) -> None:
        raw_lim: object = None
        if client is not None:
            try:
                raw_lim = client.page_limiter
            except AttributeError:
                raw_lim = None
        limiter = raw_lim if isinstance(raw_lim, PageViewLimiter) else self.page_limiter
        if isinstance(limiter, PageViewLimiter):
            max_wait = self.max_limiter_wait
            if max_wait is None and client is not None:
                try:
                    raw_cw: object = client.max_limiter_wait
                except AttributeError:
                    raw_cw = None
                if isinstance(raw_cw, (int, float)) and not isinstance(raw_cw, bool):
                    max_wait = float(raw_cw)
            limiter.acquire(cost=cost, priority=priority, delay_range=self.action_delay_range, max_wait=max_wait)
            self.last_action_at = self.clock()
            return
        low, high = self.action_delay_range
        if high > 0:
            self.sleeper(self.rng(low, high))
        self.last_action_at = self.clock()
