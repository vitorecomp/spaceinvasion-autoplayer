"""Hybrid AI-Planner action synthesis, manual queue merge, and queue-position Metal review helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import math

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, Resources
from spaceinvasion.planner import ai_planner as ai_mod
from spaceinvasion.planner.strategy import (
    PlannedAction,
    merge_manual_queue_actions,
    plan_ai_blast_furnace_throttles,
    plan_fleet_and_attack_actions,
)
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import safe_api_float, safe_api_int
from spaceinvasion.runtime.bridge._plan_helpers import (
    check_demolish_energy_safety,
    dedupe_and_sort_hybrid_actions,
    extract_ai_resource_targets,
)
from spaceinvasion.runtime.bridge.ledgers import LedgerTelemetryService


def _filter_demolish_actions(
    enriched_state: GameState,
    raw_ai_actions: Sequence[PlannedAction],
    active_plan_result: Mapping[str, object],
) -> list[PlannedAction]:
    summary_m = active_plan_result.get("summary")
    meta_m = active_plan_result.get("meta_parameters")
    plan_allows_demolition = bool(
        active_plan_result.get("allow_demolition", False)
        or (isinstance(summary_m, Mapping) and summary_m.get("allow_demolition", False))
        or (isinstance(meta_m, Mapping) and meta_m.get("allow_demolition", False))
    )
    if not plan_allows_demolition:
        return [a for a in raw_ai_actions if a.kind != "DEMOLISH_BUILDING"]
    planets_by_pid = {int(p.planet_id): p for p in enriched_state.planets}
    out: list[PlannedAction] = []
    for act in raw_ai_actions:
        if act.kind != "DEMOLISH_BUILDING":
            out.append(act)
            continue
        p_obj = planets_by_pid.get(safe_api_int(act.planet_id, 0))
        if (
            p_obj is not None
            and p_obj.fields_max > 0
            and p_obj.fields_used >= p_obj.fields_max
            and act.target_id is not None
            and check_demolish_energy_safety(enriched_state, p_obj, int(act.target_id))[0]
        ):
            out.append(act)
    return out


def _collect_early_transport_needs(
    ctx: BridgeContext,
    enriched_state: GameState,
    active_plan_result: Mapping[str, object],
    ai_actions: Sequence[PlannedAction],
    cfg: StrategyConfig,
    reserved_cost: Resources,
    priority_resource_need: Mapping[int, Resources],
) -> tuple[Resources, dict[int, object]]:
    merged_needs: dict[int, object] = dict(priority_resource_need)
    raw_early = ai_mod.compute_early_transport_needs(enriched_state, active_plan_result, ai_actions, config=cfg)
    early_needs_map: dict[int, dict[str, object]] = {}
    if isinstance(raw_early, Mapping):
        for pid_k, entry in raw_early.items():
            if not isinstance(entry, Mapping):
                continue
            pid_int = safe_api_int(pid_k, 0)
            if pid_int <= 0:
                continue
            clean_entry: dict[str, object] = dict(entry)
            early_needs_map[pid_int] = clean_entry
            merged_needs[pid_int] = clean_entry
    ctx.last_early_transport_needs = {int(k): dict(v) for k, v in early_needs_map.items()}
    ctx.last_early_transport_needs_sig = id(enriched_state)
    if reserved_cost.is_zero() and early_needs_map:
        first_early = min(
            early_needs_map.values(),
            key=lambda item: safe_api_float(item.get("need_by_sec"), 0.0),
        )
        reserved_cost = Resources(
            pig_iron=max(0, safe_api_int(first_early.get("pig_iron"), 0)),
            metal=max(0, safe_api_int(first_early.get("metal"), 0)),
            kryptonite=max(0, safe_api_int(first_early.get("kryptonite"), 0)),
            spice=max(0, safe_api_int(first_early.get("spice"), 0)),
        )
    return reserved_cost, merged_needs


def _extract_spare_pct(active_plan_result: Mapping[str, object], cfg: StrategyConfig) -> Mapping[str, float] | float:
    meta_p = active_plan_result.get("meta_parameters")
    raw_val = meta_p.get("resource_spare_percent") if isinstance(meta_p, Mapping) else None
    if isinstance(raw_val, Mapping):
        return {str(k): safe_api_float(v, float(cfg.ai_resource_spare_pct)) for k, v in raw_val.items()}
    if raw_val is not None:
        return safe_api_float(raw_val, float(cfg.ai_resource_spare_pct))
    return float(cfg.ai_resource_spare_pct)


def _build_adaptive_inputs(
    ledgers: LedgerTelemetryService,
    enriched_state: GameState,
    active_plan_result: Mapping[str, object],
    adaptive_on: bool,
) -> tuple[
    dict[int, Resources] | None,
    dict[int, float] | None,
    float | None,
    dict[str, object] | None,
]:
    if not adaptive_on:
        return None, None, None, None
    inbound_cargo = dict(ai_mod.compute_inbound_friendly_cargo_by_planet(enriched_state))
    in_prog_rem = dict(ai_mod.compute_in_progress_build_rem_by_planet(enriched_state))
    raw_res_rem = ai_mod.compute_in_progress_research_rem_seconds(enriched_state)
    in_prog_res_rem: float | None = (
        max(0.0, float(raw_res_rem))
        if isinstance(raw_res_rem, (int, float)) and not isinstance(raw_res_rem, bool) and math.isfinite(raw_res_rem)
        else None
    )
    transport_demand: dict[str, object] | None = None
    raw_td = active_plan_result.get("transport_demand")
    if isinstance(raw_td, Mapping):
        transport_demand = dict(raw_td)
        td_rt = max(0.25, safe_api_float(transport_demand.get("round_trips_per_hour", 2.5), 2.5))
        transport_demand["realized_utilization"] = ledgers.recompute_transport_utilization_locked(rt_per_hour=td_rt)
        transport_demand["infra_cargo_ordered_last_hour"] = ledgers.get_infra_cargo_ordered_last_hour_locked()
    return inbound_cargo, in_prog_rem, in_prog_res_rem, transport_demand


def plan_hybrid_ai_actions(
    ctx: BridgeContext,
    ledgers: LedgerTelemetryService,
    enriched_state: GameState,
    active_plan_result: Mapping[str, object],
    cfg: StrategyConfig,
    *,
    record_conflicts: bool = True,
    on_upsert_field_conflict: Callable[[Mapping[str, object]], dict[str, object]] | None = None,
    on_compute_live_attack_telemetry: Callable[[GameState], dict[str, object]] | None = None,
    on_persist_runtime_state: Callable[[], None] | None = None,
) -> list[PlannedAction]:
    """Synthesize hybrid AI-Planner steps, fleet/attack actions, and Blast furnace throttles."""
    raw_ai_actions, field_conflicts = ai_mod.reconcile_live_ai_actions(enriched_state, active_plan_result, cfg)
    ai_actions = _filter_demolish_actions(enriched_state, raw_ai_actions, active_plan_result)
    if record_conflicts and field_conflicts and on_upsert_field_conflict is not None:
        for conflict in field_conflicts:
            if isinstance(conflict, Mapping):
                on_upsert_field_conflict(conflict)

    reserved_cost, prio_needs = extract_ai_resource_targets(ai_actions)
    reserved_cost, merged_priority_needs = _collect_early_transport_needs(
        ctx, enriched_state, active_plan_result, ai_actions, cfg, reserved_cost, prio_needs
    )
    spare_pct = _extract_spare_pct(active_plan_result, cfg)
    raw_atk_tel = active_plan_result.get("attack_telemetry")
    atk_tel: Mapping[str, object]
    if isinstance(raw_atk_tel, Mapping):
        atk_tel = raw_atk_tel
    elif on_compute_live_attack_telemetry is not None:
        atk_tel = on_compute_live_attack_telemetry(enriched_state)
    else:
        atk_tel = {}
    adaptive_on = bool(cfg.ai_adaptive_execution)
    if adaptive_on:
        atk_tel = ledgers.overlay_realized_loot_locked(atk_tel)
    inbound_cargo, in_prog_rem, in_prog_res_rem, transport_demand = _build_adaptive_inputs(
        ledgers, enriched_state, active_plan_result, adaptive_on
    )
    spare_budget = ledgers.compute_spare_ship_budget_locked(
        enriched_state,
        spare_pct,
        attack_telemetry=atk_tel,
        reserved_cost=reserved_cost,
        adaptive_execution=adaptive_on,
        config=cfg,
    )
    fleet_actions = [
        a
        for a in plan_fleet_and_attack_actions(
            enriched_state,
            config=cfg,
            scanned_systems=ledgers.get_scanned_systems(),
            transmitter_status=ledgers.get_transmitter_status_locked(),
            existing_plan=ai_actions,
            priority_resource_need=merged_priority_needs,
            spare_budget=spare_budget,
            transport_demand=transport_demand,
        )
        if a.kind != "DEMOLISH_BUILDING"
    ]
    cleared_overflow_pids: set[int] = set()
    throttle_actions = plan_ai_blast_furnace_throttles(
        enriched_state,
        ai_actions,
        fleet_actions,
        cfg,
        throttle_cooldowns=ledgers.get_throttle_cooldowns(),
        storage_warn_ratio=cfg.storage_warn_ratio,
        metal_overflow_paused=tuple(ctx.metal_overflow_paused_planets.keys()),
        cleared_overflow_pids=cleared_overflow_pids,
        inbound_cargo_by_planet=inbound_cargo,
        in_progress_rem_by_planet=in_prog_rem,
        in_progress_research_rem_seconds=in_prog_res_rem,
        enable_donor_smelting=bool(adaptive_on and transport_demand),
    )
    runtime_dirty = False
    if record_conflicts and cleared_overflow_pids:
        for cpid in cleared_overflow_pids:
            if ctx.metal_overflow_paused_planets.pop(cpid, None) is not None:
                runtime_dirty = True
    if record_conflicts:
        if ledgers.record_adaptive_idle_bucket_locked(
            enriched_state,
            active_plan_result,
            ai_actions=ai_actions,
            cfg=cfg,
            field_conflicts=field_conflicts,
            inbound_cargo_by_planet=inbound_cargo,
        ):
            runtime_dirty = True
    if runtime_dirty and on_persist_runtime_state is not None:
        on_persist_runtime_state()
    return dedupe_and_sort_hybrid_actions(enriched_state, [*ai_actions, *fleet_actions, *throttle_actions])


def apply_manual_queue_to_plan(
    ctx: BridgeContext,
    state: GameState,
    actions: list[PlannedAction],
    *,
    cfg: StrategyConfig,
    persist_auto_satisfied: bool = True,
    reconcile_metal_production: bool = True,
) -> list[PlannedAction]:
    """Merge pending manual queue items into `actions` and auto-complete satisfied entries."""
    if ctx.db is None:
        return actions
    pending_mq = ctx.db.get_manual_queue(status="pending", limit=200)
    if not pending_mq:
        return actions
    merged, auto_done_ids = merge_manual_queue_actions(
        state,
        actions,
        pending_mq,
        config=cfg,
        reconcile_metal_production=reconcile_metal_production,
    )
    if auto_done_ids and persist_auto_satisfied:
        for qid in auto_done_ids:
            ctx.db.complete_manual_queue_item(qid, message="Auto-satisfied by current empire state")
    return merged


def review_queue_metal_status(
    ctx: BridgeContext,
    ledgers: LedgerTelemetryService,
    planet_id: int,
    pending_mq: Sequence[Mapping[str, object]],
    *,
    evaluate_gate_fn: Callable[[GameState, StrategyConfig], Mapping[str, object]],
) -> dict[str, object]:
    """Inspect Blast furnace Metal production vs pinned queue position on `planet_id`."""
    if ctx.last_state is None or not pending_mq:
        return {"checked": False, "summary": ""}
    enriched = ledgers.attach_raid_stats(ctx.last_state)
    planet = (
        next((p for p in enriched.planets if p.planet_id == planet_id), None)
        if planet_id > 0
        else enriched.active_planet()
    )
    if planet is None:
        return {"checked": False, "summary": ""}
    b2_lvl = planet.building_level(2)
    cur_b2_pct = max(0, min(100, safe_api_int(planet.economy_rates.get("resource2", 100), 100)))
    gate = evaluate_gate_fn(enriched, ctx.strategy_config)
    in_ai_planner = gate.get("effective_planner_mode") == "ai_planner"
    merged, _ = merge_manual_queue_actions(
        enriched,
        [],
        pending_mq,
        config=ctx.strategy_config,
        reconcile_metal_production=not in_ai_planner,
    )
    p_acts = [a for a in merged if a.planet_id == planet.planet_id]
    bf_throttle = next(
        (a for a in p_acts if a.manual_queue_id is None and a.kind == "THROTTLE_BUILDING" and a.target_id == 2),
        None,
    )
    deferred_metal = next(
        (
            a
            for a in p_acts
            if a.manual_queue_id is not None and "[Metal production queued after" in str(a.reason or "")
        ),
        None,
    )
    pinned_needs_metal = any(a.manual_queue_id is not None and safe_api_int(a.missing.metal, 0) > 0 for a in p_acts)
    summary_txt = ""
    scheduled_pct: int | None = None
    if in_ai_planner and pinned_needs_metal:
        summary_txt = "Blast furnace controlled exclusively by AI-Planner (+ 100% Metal overflow guard)"
    elif bf_throttle is not None:
        scheduled_pct = safe_api_int(bf_throttle.target_level, cur_b2_pct)
        if scheduled_pct > 0:
            summary_txt = f"Metal production scheduled: Blast furnace {cur_b2_pct}% -> {scheduled_pct}%"
        else:
            summary_txt = "Blast furnace held at 0% until earlier Pig iron item completes"
    elif deferred_metal is not None:
        summary_txt = "Metal production queued after earlier Pig iron item completes"
    elif pinned_needs_metal:
        if b2_lvl <= 0:
            summary_txt = "Warning: pinned item needs Metal, and Colony has no Blast furnace (b2=0)"
        elif cur_b2_pct > 0:
            summary_txt = f"Metal production active (Blast furnace {cur_b2_pct}%)"
    return {
        "checked": True,
        "planet_id": planet.planet_id,
        "planet_coords": planet.coords,
        "blast_furnace_level": b2_lvl,
        "current_blast_furnace_pct": cur_b2_pct,
        "scheduled_blast_furnace_pct": scheduled_pct,
        "pinned_needs_metal": pinned_needs_metal,
        "summary": summary_txt,
    }


def record_manual_queue_metal_review(
    ctx: BridgeContext,
    ledgers: LedgerTelemetryService,
    queue_items: Sequence[Mapping[str, object]],
    *,
    evaluate_gate_fn: Callable[[GameState, StrategyConfig], Mapping[str, object]],
) -> list[dict[str, object]]:
    """Compute per-planet Blast furnace Metal production reviews across `queue_items`."""
    if ctx.last_state is None or not queue_items:
        return []
    seen_pids: list[int] = []
    for item in queue_items:
        pid = safe_api_int(item.get("planet_id"), 0)
        if pid not in seen_pids:
            seen_pids.append(pid)
    return [
        review_queue_metal_status(ctx, ledgers, pid, queue_items, evaluate_gate_fn=evaluate_gate_fn)
        for pid in seen_pids
    ]
