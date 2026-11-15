"""Explicit post-construction collaborator hook wiring for `ModularGameApiBridge`."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from backend.core.config import StrategyConfig
from backend.core.model import GameState, PlanetState, PlannedAction
from backend.parsing import InboxMessage
from backend.runtime.bridge._context import BridgeContext
from backend.runtime.bridge.execution import ActionExecutionService
from backend.runtime.bridge.galaxy_sweep import GalaxySweepService
from backend.runtime.bridge.intel_sync import IntelSyncService
from backend.runtime.bridge.ledgers import LedgerTelemetryService
from backend.runtime.bridge.lifecycle import BridgeLifecycleService
from backend.runtime.bridge.plan_assembly import PlanAssemblyService
from backend.runtime.bridge.planner_jobs import PlannerJobService
from backend.runtime.bridge.state_cache import StateCacheService
from backend.runtime.bridge.views import DashboardViewService
from backend.session.client import GameBrowserClient


def _wire_state_and_planning(
    *,
    ctx: BridgeContext,
    lifecycle: BridgeLifecycleService,
    state_cache: StateCacheService,
    ledgers: LedgerTelemetryService,
    plan_assembly: PlanAssemblyService,
    planner_jobs: PlannerJobService,
    intel_sync: IntelSyncService,
    galaxy_sweep: GalaxySweepService,
    publish_void: Callable[[], None],
) -> None:
    def _recompute_transport_void(now_ts: float) -> None:
        ledgers.recompute_transport_utilization_locked(now_ts)

    def _evaluate_gate_pos(state: GameState, cfg: StrategyConfig) -> dict[str, object]:
        return planner_jobs.evaluate_ai_planner_gate_locked(state, cfg=cfg)

    def _sync_spio_hook(client: GameBrowserClient, state: GameState, priority: str) -> int:
        return intel_sync.sync_spio_if_unread(client, state, priority=priority)

    def _on_note_daily_bonus(present: bool) -> None:
        ledgers.note_daily_login_bonus_seen_locked(present)

    def _on_record_hourly_production_void(state: GameState) -> None:
        ledgers.record_hourly_production_locked(state)

    def _on_transport_delivery(msg: InboxMessage) -> None:
        _ = msg
        if ctx.last_state is not None:
            ledgers.observe_transport_utilization_locked(ctx.last_state)

    lifecycle.bind_callbacks(
        prune_ledgers_fn=ledgers.prune_all_ledgers,
        recompute_transport_util_fn=_recompute_transport_void,
        merge_economy_fn=state_cache.merge_economy_rates,
        stop_sweep_fn=galaxy_sweep.stop_galaxy_sweep_worker,
    )
    ledgers.bind_hooks(on_persist_runtime_state=lifecycle.persist_runtime_state)
    state_cache.bind_hooks(
        on_note_daily_bonus=_on_note_daily_bonus,
        on_capture_outbound_transports=ledgers.capture_observed_outbound_transports_locked,
        on_record_hourly_production=_on_record_hourly_production_void,
        on_record_new_colonies=intel_sync.record_new_colonies_locked,
        on_record_adaptive_idle_if_due=planner_jobs.record_adaptive_idle_if_due,
        on_sync_spio_if_unread=_sync_spio_hook,
        on_attach_raid_stats=ledgers.attach_raid_stats,
        on_is_galaxy_scan_blocking=galaxy_sweep.is_galaxy_scan_blocking_locked,
        on_galaxy_scan_busy_msg=galaxy_sweep.galaxy_scan_busy_error_message,
        on_maybe_trigger_ai_drift_replan=planner_jobs.maybe_trigger_ai_drift_replan,
        on_publish_snapshot=publish_void,
    )
    plan_assembly.bind_planner_callbacks(
        planner_jobs=planner_jobs,
        evaluate_gate_fn=_evaluate_gate_pos,
        upsert_field_conflict_fn=planner_jobs.upsert_field_conflict_locked,
        compute_live_attack_telemetry_fn=planner_jobs.compute_live_attack_telemetry_locked,
        on_persist_runtime_state=lifecycle.persist_runtime_state,
    )
    planner_jobs.bind_hooks(
        on_publish_snapshot=publish_void,
        on_persist_runtime_state=lifecycle.persist_runtime_state,
        on_antibot_pause=lifecycle.record_antibot_pause,
        on_is_galaxy_scan_blocking=galaxy_sweep.is_galaxy_scan_blocking_locked,
        on_galaxy_scan_busy_msg=galaxy_sweep.galaxy_scan_busy_error_message,
    )
    intel_sync.bind_hooks(
        on_transport_delivery_fn=_on_transport_delivery,
        on_publish_snapshot=publish_void,
        on_persist_runtime_state=lifecycle.persist_runtime_state,
        on_plan_smart_sweep=galaxy_sweep.plan_smart_galaxy_sweep_locked,
        on_store_smart_summary=galaxy_sweep.store_smart_sweep_summary_locked,
        on_get_sweep_state=galaxy_sweep.get_galaxy_sweep_state_locked,
    )


def _wire_execution_and_views(
    *,
    ctx: BridgeContext,
    ledgers: LedgerTelemetryService,
    plan_assembly: PlanAssemblyService,
    planner_jobs: PlannerJobService,
    galaxy_sweep: GalaxySweepService,
    execution: ActionExecutionService,
    views: DashboardViewService,
    publish_void: Callable[[], None],
) -> None:
    def _on_record_infra_spend(action: PlannedAction, qty: int) -> None:
        ledgers.record_infra_transport_spend_locked(action, quantity=qty)

    def _on_record_spare_spend(action: PlannedAction, qty: int) -> None:
        ledgers.record_spare_ship_spend_locked(action, quantity=qty)

    def _on_record_fleet_slots(planet: PlanetState | None, available: int | None) -> None:
        if available is not None:
            ledgers.record_observed_fleet_slots_locked(planet, available)

    def _on_record_manual_queue_completion(queue_id: int, msg: str) -> None:
        if ctx.db is not None:
            ctx.db.complete_manual_queue_item(queue_id, message=msg)

    def _on_build_requests(
        state: GameState | None,
        actions: list[PlannedAction],
        early_needs: Mapping[int, dict[str, object]],
    ) -> list[dict[str, object]]:
        if state is None:
            return []
        return ledgers.build_building_resource_requests_locked(state, actions, early_needs)

    def _on_has_urgent_p0() -> tuple[bool, str]:
        return plan_assembly.has_urgent_p0_escape_flight_locked()

    galaxy_sweep.bind_hooks(on_has_urgent_p0_escape=_on_has_urgent_p0, on_publish_snapshot=publish_void)
    execution.bind_hooks(
        on_plan_with_context=plan_assembly.plan_with_context,
        on_serialize_action=plan_assembly.serialize_action,
        on_note_transmitter_activation=ledgers.note_transmitter_activation_locked,
        on_record_transport_dispatch=ledgers.record_transport_dispatch_locked,
        on_record_infra_transport_spend=_on_record_infra_spend,
        on_record_spare_ship_spend=_on_record_spare_spend,
        on_record_observed_fleet_slots=_on_record_fleet_slots,
        on_apply_fleet_slot_cooldowns=ledgers.apply_fleet_slot_cooldowns,
        on_record_manual_queue_completion=_on_record_manual_queue_completion,
        on_publish_snapshot=publish_void,
        on_attach_raid_stats=ledgers.attach_raid_stats,
    )

    def _on_plan_with_summary(
        state: GameState,
        *,
        config: StrategyConfig | None = None,
        include_manual_queue: bool = True,
        already_enriched: bool = False,
    ) -> tuple[list[PlannedAction], dict[str, object] | None]:
        return plan_assembly.plan_with_summary(
            state,
            config=config,
            include_manual_queue=include_manual_queue,
            already_enriched=already_enriched,
        )

    views.bind_hooks(
        on_plan_with_summary=_on_plan_with_summary,
        on_serialize_action=plan_assembly.serialize_action,
        on_get_co_building_goals=planner_jobs.get_config_optimizer_building_goals_locked,
        on_get_active_ai_planner=planner_jobs.get_active_ai_planner_locked,
        on_evaluate_ai_gate=planner_jobs.evaluate_ai_planner_gate_locked,
        on_get_planner_jobs_status=planner_jobs.get_planner_jobs_snapshot_locked,
        on_get_transmitter_status=ledgers.get_transmitter_status_locked,
        on_get_galaxy_sweep_state=galaxy_sweep.get_galaxy_sweep_state_locked,
        on_is_galaxy_scan_blocking=galaxy_sweep.is_galaxy_scan_blocking_locked,
        on_has_urgent_p0_escape=_on_has_urgent_p0,
        on_get_scanned_systems=ledgers.get_scanned_systems,
        on_get_throttle_cooldowns=ledgers.get_throttle_cooldowns,
        on_get_fleet_slot_cooldowns=ledgers.get_fleet_slot_cooldowns,
        on_get_hourly_production_history=ledgers.get_hourly_production_history_locked,
        on_get_loot_recycle_hourly=ledgers.get_loot_recycle_hourly_locked,
        on_build_building_resource_requests=_on_build_requests,
        on_attach_raid_stats=ledgers.attach_raid_stats,
        on_get_raid_stats_cached=ledgers.get_raid_stats_cached,
        on_record_hourly_production=ledgers.record_hourly_production_locked,
        on_maybe_trigger_ai_drift_replan=planner_jobs.maybe_trigger_ai_drift_replan,
    )


def wire_bridge_collaborators(
    *,
    ctx: BridgeContext,
    lifecycle: BridgeLifecycleService,
    state_cache: StateCacheService,
    ledgers: LedgerTelemetryService,
    plan_assembly: PlanAssemblyService,
    planner_jobs: PlannerJobService,
    intel_sync: IntelSyncService,
    galaxy_sweep: GalaxySweepService,
    execution: ActionExecutionService,
    views: DashboardViewService,
) -> None:
    """Connect explicit typed collaborator callbacks once during bridge initialization."""

    def _publish_void() -> None:
        views.publish_snapshot()

    _wire_state_and_planning(
        ctx=ctx,
        lifecycle=lifecycle,
        state_cache=state_cache,
        ledgers=ledgers,
        plan_assembly=plan_assembly,
        planner_jobs=planner_jobs,
        intel_sync=intel_sync,
        galaxy_sweep=galaxy_sweep,
        publish_void=_publish_void,
    )
    _wire_execution_and_views(
        ctx=ctx,
        ledgers=ledgers,
        plan_assembly=plan_assembly,
        planner_jobs=planner_jobs,
        galaxy_sweep=galaxy_sweep,
        execution=execution,
        views=views,
        publish_void=_publish_void,
    )
