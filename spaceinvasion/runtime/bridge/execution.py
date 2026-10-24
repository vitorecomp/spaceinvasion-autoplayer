"""Action execution orchestrator: preflight guards, executor dispatch, side-effects, and summary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from spaceinvasion.core.model import GameState, PlanetState, PlannedAction
from spaceinvasion.planner.eta import attach_action_etas
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._exec_after_state import (
    build_execution_summary,
    refresh_after_state_and_plan,
)
from spaceinvasion.runtime.bridge._exec_postprocess import (
    ExecutionHooks,
    apply_fleet_and_target_block_effects,
    apply_live_economy_and_galaxy_effects,
    apply_raid_transport_ship_colony_effects,
    apply_transmitter_and_throttle_effects,
    record_action_and_manual_queue,
)
from spaceinvasion.runtime.bridge._exec_preflight import (
    check_recycle_live_galaxy_preflight,
    check_spacia_live_galaxy_preflight,
    check_spy_reserved_slots_preflight,
)
from spaceinvasion.runtime.bridge._helpers import sanitize_json_dict
from spaceinvasion.runtime.bridge._helpers_sig import supports_kwarg
from spaceinvasion.runtime.bridge._intel_messages import extract_client_current_url
from spaceinvasion.runtime.bridge.intel_sync import IntelSyncService
from spaceinvasion.runtime.bridge.lifecycle import BridgeLifecycleService
from spaceinvasion.runtime.bridge.state_cache import StateCacheService
from spaceinvasion.session.client import GameBrowserClient
from spaceinvasion.session.executor import ExecutionResult


class ActionExecutionService:
    """Owns preflight verification, live/dry execution, side-effect persistence, and summary building."""

    def __init__(
        self,
        ctx: BridgeContext,
        state_cache: StateCacheService,
        intel_sync: IntelSyncService,
        lifecycle: BridgeLifecycleService,
    ) -> None:
        self._ctx = ctx
        self._state_cache = state_cache
        self._intel_sync = intel_sync
        self._lifecycle = lifecycle
        self._hooks = ExecutionHooks()

    def bind_hooks(
        self,
        *,
        on_plan_with_context: Callable[[GameState], list[PlannedAction]] | None = None,
        on_serialize_action: Callable[[PlannedAction, GameState | None], dict[str, object]] | None = None,
        on_note_transmitter_activation: Callable[..., dict[str, object]] | None = None,
        on_record_transport_dispatch: Callable[..., None] | None = None,
        on_record_infra_transport_spend: Callable[[PlannedAction, int], None] | None = None,
        on_record_spare_ship_spend: Callable[[PlannedAction, int], None] | None = None,
        on_record_observed_fleet_slots: Callable[[PlanetState | None, int | None], None] | None = None,
        on_apply_fleet_slot_cooldowns: Callable[..., list[PlannedAction]] | None = None,
        on_record_manual_queue_completion: Callable[..., None] | None = None,
        on_publish_snapshot: Callable[[], None] | None = None,
        on_attach_raid_stats: Callable[[GameState], GameState] | None = None,
    ) -> None:
        """Bind explicit collaborator callbacks for planning, serialization, ledgers, and snapshots."""
        if on_plan_with_context is not None:
            self._hooks.on_plan_with_context = on_plan_with_context
        if on_serialize_action is not None:
            self._hooks.on_serialize_action = on_serialize_action
        if on_note_transmitter_activation is not None:
            self._hooks.on_note_transmitter_activation = on_note_transmitter_activation
        if on_record_transport_dispatch is not None:
            self._hooks.on_record_transport_dispatch = on_record_transport_dispatch
        if on_record_infra_transport_spend is not None:
            self._hooks.on_record_infra_transport_spend = on_record_infra_transport_spend
        if on_record_spare_ship_spend is not None:
            self._hooks.on_record_spare_ship_spend = on_record_spare_ship_spend
        if on_record_observed_fleet_slots is not None:
            self._hooks.on_record_observed_fleet_slots = on_record_observed_fleet_slots
        if on_apply_fleet_slot_cooldowns is not None:
            self._hooks.on_apply_fleet_slot_cooldowns = on_apply_fleet_slot_cooldowns
        if on_record_manual_queue_completion is not None:
            self._hooks.on_record_manual_queue_completion = on_record_manual_queue_completion
        if on_publish_snapshot is not None:
            self._hooks.on_publish_snapshot = on_publish_snapshot
        if on_attach_raid_stats is not None:
            self._hooks.on_attach_raid_stats = on_attach_raid_stats

    def _serialize_action(self, action: PlannedAction, state: GameState | None = None) -> dict[str, object]:
        if self._hooks.on_serialize_action is not None:
            return self._hooks.on_serialize_action(action, state)
        return sanitize_json_dict(asdict(action))

    def _run_preflight_guards(
        self,
        cmd: str,
        client: GameBrowserClient | None,
        state: GameState,
        planned: PlannedAction,
    ) -> tuple[PlannedAction, dict[str, object] | None]:
        spy_abort = check_spy_reserved_slots_preflight(
            self._ctx, cmd, state, planned, serialize_action_fn=self._serialize_action
        )
        if spy_abort is not None:
            return planned, spy_abort
        spacia_abort = check_spacia_live_galaxy_preflight(
            self._ctx, self._intel_sync, client, cmd, state, planned, serialize_action_fn=self._serialize_action
        )
        if spacia_abort is not None:
            return planned, spacia_abort
        return check_recycle_live_galaxy_preflight(
            self._ctx,
            self._state_cache,
            self._intel_sync,
            client,
            cmd,
            state,
            planned,
            serialize_action_fn=self._serialize_action,
        )

    def execute_and_summarize(
        self,
        cmd: str,
        client: GameBrowserClient | None,
        state: GameState,
        action: PlannedAction,
        *,
        dry_run: bool,
        return_home: bool = False,
        active_planet_confirmed: bool = True,
        manual_queue_id: int | None = None,
        source: str = "api",
    ) -> dict[str, object]:
        """Run preflight checks, execute `action`, persist side-effects, refresh state, and summarize."""
        ctx = self._ctx
        if client is None and not dry_run:
            raise RuntimeError("Live action execution requires an active GameBrowserClient.")
        planned_list = attach_action_etas(state, [action])
        planned: PlannedAction = planned_list[0] if planned_list else action

        if not dry_run:
            planned, abort_resp = self._run_preflight_guards(cmd, client, state, planned)
            if abort_resp is not None:
                return abort_resp

        executor = ctx.pick_executor(dry_run)
        eff_confirmed = bool(active_planet_confirmed and ctx.active_planet_confirmed)
        res: ExecutionResult | None = None
        try:
            if supports_kwarg(executor.execute_action, "active_planet_confirmed"):
                res = executor.execute_action(
                    client,  # type: ignore[arg-type]
                    state,
                    planned,
                    active_planet_confirmed=eff_confirmed,
                )
            else:
                res = executor.execute_action(
                    client,  # type: ignore[arg-type]
                    state,
                    planned,
                )
        finally:
            if res is None and not dry_run:
                raw_pid = executor.last_observed_active_planet_id
                raw_conf = executor.last_observed_active_planet_confirmed
                self._state_cache.sync_observed_active_planet_state(
                    state,
                    obs_pid=raw_pid if isinstance(raw_pid, int) and not isinstance(raw_pid, bool) else None,
                    obs_confirmed=raw_conf if isinstance(raw_conf, bool) else None,
                )

        if client is not None:
            url_now = extract_client_current_url(client)
            if url_now:
                ctx.last_url = url_now

        if not dry_run:
            apply_live_economy_and_galaxy_effects(ctx, self._intel_sync, executor, state, res)
            apply_transmitter_and_throttle_effects(ctx, self._lifecycle, self._hooks, executor, state, res)
            apply_fleet_and_target_block_effects(ctx, self._state_cache, self._hooks, state, res)
            apply_raid_transport_ship_colony_effects(
                ctx, self._state_cache, self._intel_sync, self._lifecycle, self._hooks, state, res, cmd
            )

        record_action_and_manual_queue(
            ctx,
            self._lifecycle,
            self._hooks,
            cmd,
            res,
            dry_run=dry_run,
            manual_queue_id=manual_queue_id,
            source=source,
        )
        after_state, after_plan = refresh_after_state_and_plan(
            ctx,
            self._state_cache,
            self._hooks,
            client,
            state,
            res,
            dry_run=dry_run,
            return_home=return_home,
        )
        if self._hooks.on_publish_snapshot is not None:
            self._hooks.on_publish_snapshot()
        return build_execution_summary(
            ctx,
            self._lifecycle,
            self._hooks,
            cmd,
            res,
            after_state,
            after_plan,
            serialize_action_fn=self._serialize_action,
        )
