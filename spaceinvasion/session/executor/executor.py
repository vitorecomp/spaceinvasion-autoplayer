"""Thin `ActionExecutor` facade wiring explicit `exec_ops` collaborators via constructors."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import random

from spaceinvasion.core.model import PlannedAction
from spaceinvasion.parsing.models import GalaxySlot, GameState, Resources
from spaceinvasion.session.executor._compat import ExecutorCompatMixin
from spaceinvasion.session.executor._protocols import (
    BrowserClientLike,
    ExecutorContext,
    PlannedActionLike,
    _exec_monotonic,
    _exec_sleep,
    coerce_planned_action,
)
from spaceinvasion.session.executor._types import (
    ExecutionResult,
    safe_str,
)
from spaceinvasion.session.executor._types import (
    format_dry_run_suffix as _format_dry_run_suffix,
)
from spaceinvasion.session.executor.build import BuildOps
from spaceinvasion.session.executor.colony import ColonyOps
from spaceinvasion.session.executor.fleet_dispatch import FleetMissionDispatcher
from spaceinvasion.session.executor.missions import MissionRunner
from spaceinvasion.session.executor.planet_select import PlanetSelector
from spaceinvasion.session.executor.settings_sync import SettingsSyncOps
from spaceinvasion.session.executor.transmitter import TransmitterOps
from spaceinvasion.session.limiter import PageViewLimiter

_SUPPORTED_KINDS: frozenset[str] = frozenset(
    {
        "BUILD",
        "DEMOLISH_BUILDING",
        "ABANDON_COLONY",
        "CLAIM_TUTORIAL",
        "ACTIVATE_TRANSMITTER",
        "RESEARCH",
        "BUILD_SHIP",
        "THROTTLE_BUILDING",
        "ESCAPE_FLIGHT",
        "SCAN_GALAXY",
        "SPY",
        "TRANSPORT",
        "STATION",
        "RECYCLE",
        "COLONIZE",
        "ATTACK",
    }
)
_TARGET_ID_REQUIRED_KINDS: frozenset[str] = frozenset(
    {"BUILD", "DEMOLISH_BUILDING", "RESEARCH", "BUILD_SHIP", "THROTTLE_BUILDING"}
)


class ActionExecutor(ExecutorCompatMixin):
    """Executes approved `PlannedAction` items via explicit `executor` collaborators."""

    def __init__(
        self,
        *,
        dry_run: bool = True,
        max_actions_per_hour: int = 30,
        enforce_hourly_cap: bool = True,
        action_delay_range: tuple[float, float] = (0.4, 0.9),
        allow_fleetsave: bool = False,
        allow_raids: bool = False,
        allow_colonize: bool = False,
        allow_transport: bool = False,
        allow_transmitter: bool = True,
        page_limiter: PageViewLimiter | None = None,
        max_limiter_wait: float | None = None,
        clock: Callable[[], float] = _exec_monotonic,
        sleeper: Callable[[float], None] = _exec_sleep,
        rng: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._ctx = ExecutorContext(
            dry_run=dry_run,
            max_actions_per_hour=max_actions_per_hour,
            enforce_hourly_cap=enforce_hourly_cap,
            action_delay_range=action_delay_range,
            page_limiter=page_limiter,
            max_limiter_wait=max_limiter_wait,
            clock=clock,
            sleeper=sleeper,
            rng=rng,
        )
        self.allow_fleetsave = allow_fleetsave
        self.allow_raids = allow_raids
        self.allow_colonize = allow_colonize
        self.allow_transport = allow_transport
        self.allow_transmitter = allow_transmitter

        self._selector = PlanetSelector(self._ctx, nav_hook=self._nav_hook)
        self._build_ops = BuildOps(self._ctx, self._selector)
        self._fleet_dispatcher = FleetMissionDispatcher(
            self._ctx,
            navigate_and_select_fn=self._nav_hook,
            select_planet_fn=self._select_hook,
            build_updated_state_fn=self._build_state_hook,
        )
        self._missions = MissionRunner(
            self._ctx,
            self._fleet_dispatcher,
            navigate_and_select_fn=self._nav_hook,
            select_planet_fn=self._select_hook,
            build_updated_state_fn=self._build_state_hook,
        )
        self._transmitter_ops = TransmitterOps(self._ctx, self._selector)
        self._colony_ops = ColonyOps(self._ctx, self._selector)
        self._settings_sync = SettingsSyncOps(self._ctx, self._selector)

    def _nav_hook(
        self,
        client: BrowserClientLike,
        state: GameState,
        menu_action: str,
        planet_id: int,
        *,
        active_planet_confirmed: bool = True,
    ) -> str:
        return self._navigate_and_select_planet(
            client, state, menu_action, planet_id, active_planet_confirmed=active_planet_confirmed
        )

    def _select_hook(
        self,
        client: BrowserClientLike,
        planet_id: int,
        *,
        html: str = "",
        require_confirm: bool = False,
    ) -> tuple[bool, str | None]:
        return self._select_planet_if_needed(client, planet_id, html=html, require_confirm=require_confirm)

    def _build_state_hook(
        self,
        client: BrowserClientLike,
        prev_state: GameState | None,
        action: PlannedAction | PlannedActionLike,
        updated_overview: object,
        *,
        page_html: str = "",
        is_home: bool = False,
        is_fleet: bool = False,
        include_buildings: bool = False,
        include_research: bool = False,
        include_shipyard: bool = False,
    ) -> GameState | None:
        return self._build_updated_state(
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

    @property
    def page_limiter(self) -> PageViewLimiter | None:
        return self._ctx.page_limiter

    @page_limiter.setter
    def page_limiter(self, value: PageViewLimiter | None) -> None:
        self._ctx.page_limiter = value

    @property
    def dry_run(self) -> bool:
        return self._ctx.dry_run

    @dry_run.setter
    def dry_run(self, value: bool) -> None:
        self._ctx.dry_run = bool(value)

    @property
    def max_actions_per_hour(self) -> int:
        return self._ctx.max_actions_per_hour

    @max_actions_per_hour.setter
    def max_actions_per_hour(self, value: int) -> None:
        self._ctx.max_actions_per_hour = value

    @property
    def enforce_hourly_cap(self) -> bool:
        return self._ctx.enforce_hourly_cap

    @enforce_hourly_cap.setter
    def enforce_hourly_cap(self, value: bool) -> None:
        self._ctx.enforce_hourly_cap = bool(value)

    @property
    def action_delay_range(self) -> tuple[float, float]:
        return self._ctx.action_delay_range

    @action_delay_range.setter
    def action_delay_range(self, value: tuple[float, float]) -> None:
        self._ctx.action_delay_range = value

    @property
    def max_limiter_wait(self) -> float | None:
        return self._ctx.max_limiter_wait

    @max_limiter_wait.setter
    def max_limiter_wait(self, value: float | None) -> None:
        self._ctx.max_limiter_wait = value

    @property
    def last_economy_rates(self) -> dict[int, dict[str, int]]:
        return self._selector.last_economy_rates

    @property
    def last_galaxy_slots(self) -> list[GalaxySlot]:
        return self._missions.last_galaxy_slots

    @property
    def last_transmitter_info(self) -> dict[str, object] | None:
        return self._transmitter_ops.last_transmitter_info

    @property
    def last_observed_active_planet_id(self) -> int | None:
        return self._ctx.observed_active_planet_id

    @property
    def last_observed_active_planet_confirmed(self) -> bool | None:
        return self._ctx.observed_active_planet_confirmed

    def remaining_hourly_budget(self) -> int:
        return self._ctx.remaining_hourly_budget()

    def _check_pre_dispatch_guards(self, action: PlannedAction) -> ExecutionResult | None:
        if not action.executable_now:
            return ExecutionResult(
                executed=False,
                dry_run=self.dry_run,
                action=action,
                message="Skipped: action is not currently executable.",
            )
        if action.kind not in _SUPPORTED_KINDS:
            return ExecutionResult(
                executed=False,
                dry_run=self.dry_run,
                action=action,
                message=f"Skipped: unsupported live action kind {action.kind}.",
            )
        if action.kind in _TARGET_ID_REQUIRED_KINDS and action.target_id is None:
            return ExecutionResult(
                executed=False,
                dry_run=self.dry_run,
                action=action,
                message=f"Skipped: {action.kind} action missing target_id.",
            )
        if self.dry_run:
            suffix = _format_dry_run_suffix(action)
            return ExecutionResult(
                executed=False,
                dry_run=True,
                action=action,
                message=(
                    f"[DRY-RUN] Would execute {action.kind}: "
                    f"{safe_str(action.target_name)}{suffix} on [{safe_str(action.planet_coords)}]."
                ),
            )
        perm_guards: dict[str, tuple[bool, str]] = {
            "ESCAPE_FLIGHT": (self.allow_fleetsave, "allow_fleetsave=true"),
            "COLONIZE": (self.allow_colonize, "allow_colonize=true"),
            "TRANSPORT": (self.allow_transport, "allow_transport=true"),
            "STATION": (self.allow_transport, "allow_transport=true"),
            "ABANDON_COLONY": (self.allow_colonize, "allow_colonize=true"),
            "ATTACK": (self.allow_raids, "allow_raids=true"),
            "ACTIVATE_TRANSMITTER": (self.allow_transmitter, "allow_transmitter=true"),
        }
        if action.kind in perm_guards:
            allowed, flag_txt = perm_guards[action.kind]
            if not allowed:
                return ExecutionResult(
                    executed=False,
                    dry_run=False,
                    action=action,
                    message=f"Guarded: live {action.kind} requires {flag_txt} in config.",
                )
        if self.enforce_hourly_cap and action.kind != "SCAN_GALAXY" and self.remaining_hourly_budget() <= 0:
            return ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Paused: hourly action rate cap reached.",
            )
        return None

    def execute_action(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: PlannedActionLike,
        *,
        active_planet_confirmed: bool = True,
    ) -> ExecutionResult:
        """Execute one approved action with rate, permission, and active-planet guards."""
        self._ctx.reset_observed_planet()
        act = coerce_planned_action(action)
        guarded = self._check_pre_dispatch_guards(act)
        if guarded is not None:
            return guarded

        kind = act.kind
        if kind == "CLAIM_TUTORIAL":
            res = self._build_ops.execute_claim_tutorial(client, act, state=state)
        elif kind == "ACTIVATE_TRANSMITTER":
            res = self._transmitter_ops.execute_activate_transmitter(client, state, act)
        elif kind == "BUILD":
            res = self._build_ops.execute_build(client, state, act)
        elif kind == "DEMOLISH_BUILDING":
            res = self._build_ops.execute_demolish_building(client, state, act)
        elif kind == "ABANDON_COLONY":
            res = self._colony_ops.execute_abandon_colony(client, state, act)
        elif kind == "RESEARCH":
            res = self._build_ops.execute_research(client, state, act)
        elif kind == "BUILD_SHIP":
            res = self._build_ops.execute_build_ship(client, state, act)
        elif kind == "THROTTLE_BUILDING":
            res = self._build_ops.execute_throttle_building(client, state, act)
        elif kind == "SCAN_GALAXY":
            res = self._missions.execute_scan_galaxy(client, state, act)
        elif kind == "SPY":
            res = self._missions.execute_spy(client, state, act, active_planet_confirmed=active_planet_confirmed)
        elif kind == "RECYCLE":
            res = self._missions.execute_recycle(client, state, act, active_planet_confirmed=active_planet_confirmed)
        elif kind == "COLONIZE":
            res = self._missions.execute_colonize(client, state, act, active_planet_confirmed=active_planet_confirmed)
        elif kind == "TRANSPORT":
            res = self._missions.execute_transport(client, state, act, active_planet_confirmed=active_planet_confirmed)
        elif kind == "STATION":
            res = self._missions.execute_station(client, state, act, active_planet_confirmed=active_planet_confirmed)
        elif kind == "ATTACK":
            res = self._missions.execute_attack(client, state, act, active_planet_confirmed=active_planet_confirmed)
        else:
            res = self._missions.execute_escape_flight(
                client, state, act, active_planet_confirmed=active_planet_confirmed
            )
        return self._ctx.attach_observed_planet(res)

    def _dispatch_fleet_mission(
        self,
        client: BrowserClientLike,
        state: GameState,
        action: PlannedActionLike,
        *,
        ships_to_send: Mapping[str, int],
        mission_kind: str,
        active_planet_confirmed: bool = True,
        cargo_resources: Resources | None = None,
    ) -> ExecutionResult:
        return self._fleet_dispatcher.dispatch_fleet_mission(
            client,
            state,
            action,
            ships_to_send=ships_to_send,
            mission_kind=mission_kind,
            active_planet_confirmed=active_planet_confirmed,
            cargo_resources=cargo_resources,
        )

    def sync_planet_names_in_game(
        self,
        client: BrowserClientLike,
        state: GameState,
        target_names: Mapping[int, str],
    ) -> dict[str, object]:
        """Update owned planet names in SpaceInvasion Classic via `internalSettings` (never touches delete buttons)."""
        return self._settings_sync.sync_planet_names_in_game(client, state, target_names)
