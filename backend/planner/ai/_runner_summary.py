"""Smelting report, telemetry enrichment, and summary assembly for ``run_ai_planner``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import TYPE_CHECKING

from backend.core import catalog as game_data
from backend.core.coercion import coerce_finite_float, coerce_int
from backend.planner.ai._runner_report import seq_float, seq_int
from backend.planner.ai._types import PATH_HOURS_PER_WEEK, REALIZED_INCOME_BASIS
from backend.planner.ai.telemetry import fmt_path_time
from backend.planner.config_optimizer import RESOURCE_KEYS

if TYPE_CHECKING:
    from backend.planner.ai.simulator import EmpirePathSimulator


def build_smelting_report(simulator: EmpirePathSimulator, best_run: Mapping[str, object]) -> dict[str, object]:
    """Build the ``smelting`` telemetry and toggle-event section of ``run_ai_planner``."""
    raw_spans = best_run.get("smelting_spans")
    spans = (
        [s for s in raw_spans if isinstance(s, (list, tuple)) and len(s) >= 2]
        if isinstance(raw_spans, Sequence)
        else []
    )
    smelt_hours = sum(max(0.0, float(s[1]) - float(s[0])) for s in spans)
    spans_out = [
        {
            "start_hours": round(float(s[0]), 4),
            "end_hours": round(float(s[1]), 4),
            "start_sec": round(float(s[0]) * 3600.0, 2),
            "end_sec": round(float(s[1]) * 3600.0, 2),
            "duration_sec": round(max(0.0, float(s[1]) - float(s[0])) * 3600.0, 2),
            "start_text": "now" if float(s[0]) <= 1e-6 else fmt_path_time(float(s[0])),
            "end_text": fmt_path_time(float(s[1])),
            "duration_text": fmt_path_time(max(0.0, float(s[1]) - float(s[0]))),
        }
        for s in spans
    ]
    raw_evs = best_run.get("smelting_events")
    evs_in = [e for e in raw_evs if isinstance(e, Mapping)] if isinstance(raw_evs, Sequence) else []
    events_out: list[dict[str, object]] = []
    for ev_seq, ev in enumerate(evs_in, start=1):
        ev_hr = max(0.0, coerce_finite_float(ev.get("time_hours"), 0.0))
        ev_wk = min(simulator.reward_weeks, max(1, int(ev_hr // PATH_HOURS_PER_WEEK) + 1))
        step_id = ev.get("reserved_step_id", -1)
        step_label, step_coords = None, None
        if isinstance(step_id, int) and 0 <= step_id < len(simulator.steps):
            st_det = simulator.steps[step_id]
            step_label = f"{st_det['name']} L{coerce_int(st_det['level_to'], 0)}"
            sp_idx = coerce_int(st_det["planet"], -1)
            step_coords = str(simulator.planets[sp_idx]["coords"]) if sp_idx >= 0 else "account"
        aff = ev.get("affected_planets")
        events_out.append(
            {
                "seq": ev_seq,
                "time_hours": round(ev_hr, 4),
                "time_sec": round(ev_hr * 3600.0, 2),
                "time_text": "now" if ev_hr <= 1e-6 else fmt_path_time(ev_hr),
                "week": ev_wk,
                "action": str(ev.get("action") or ("enable" if ev.get("enabled") else "disable")),
                "enabled": bool(ev.get("enabled", False)),
                "target_pct": 100 if ev.get("enabled") else 0,
                "from_mode": str(ev.get("from_mode") or "off"),
                "from_enabled": bool(ev.get("from_enabled", False)),
                "is_initial": bool(ev.get("is_initial", False)),
                "affected_planets": list(aff) if isinstance(aff, Sequence) and not isinstance(aff, str) else [],
                "step_label": step_label,
                "step_planet_coords": step_coords,
                "reason": str(ev.get("reason") or ""),
            }
        )
    raw_bf = best_run.get("initial_blast_furnace")
    bf: Mapping[str, object] = raw_bf if isinstance(raw_bf, Mapping) else simulator.initial_blast_furnace
    init_toggles, bf_planets = best_run.get("initial_planets_needing_toggle"), bf.get("planets")
    return {
        "initial_live_mode": str(bf.get("mode") or "none"),
        "initial_live_any_enabled": bool(bf.get("any_enabled", False)),
        "initial_live_all_enabled": bool(bf.get("all_enabled", False)),
        "initial_live_enabled_count": coerce_int(bf.get("enabled_count"), 0),
        "initial_live_disabled_count": coerce_int(bf.get("disabled_count"), 0),
        "total_b2_planets": coerce_int(bf.get("total_b2_planets"), 0),
        "initial_target_enabled": bool(best_run.get("initial_smelting_target", False)),
        "initial_target_pct": 100 if best_run.get("initial_smelting_target", False) else 0,
        "initial_toggle_needed": bool(best_run.get("initial_toggle_needed", False)),
        "initial_planets_needing_toggle": list(init_toggles) if isinstance(init_toggles, Sequence) else [],
        "metal_smelted": int(round(coerce_finite_float(best_run.get("metal_smelted"), 0.0))),
        "smelting_hours_total": round(smelt_hours, 4),
        "smelting_time_text": fmt_path_time(smelt_hours),
        "smelting_toggles_count": len(events_out),
        "smelting_spans_count": len(spans_out),
        "smelting_spans": spans_out,
        "smelting_events": events_out,
        "planets": list(bf_planets) if isinstance(bf_planets, Sequence) else [],
    }


def enrich_attack_telemetry(
    telemetry: Mapping[str, object],
    cfg: Mapping[str, object],
    *,
    median_loot: Mapping[str, float],
    median_rt: float,
    sample_count: int,
    has_active_loot: bool,
    per_slot_hour: float,
    use_realized: bool,
    pace_set_by: str,
    fixed_inc: Mapping[str, float],
    slot_inc: Mapping[str, float],
    start_slots: int,
) -> tuple[dict[str, object], float]:
    """Attach computed pace, per-slot income, and hourly loot totals onto attack telemetry."""
    apd = cfg.get("attacks_per_day")
    if apd is not None:
        tel_per_slot, tel_aph = per_slot_hour, coerce_finite_float(apd, 0.0) / 24.0
    elif use_realized:
        tel_per_slot = max(0.0, coerce_finite_float(telemetry.get("attacks_per_slot_per_hour"), 0.0))
        tel_aph = max(0.0, coerce_finite_float(telemetry.get("estimated_attacks_per_hour"), 0.0))
    else:
        tel_per_slot, tel_aph = per_slot_hour, start_slots * per_slot_hour
    income_now = {r: fixed_inc[r] + start_slots * slot_inc[r] for r in RESOURCE_KEYS}
    raw_ml = telemetry.get("median_loot")
    ml_map: Mapping[str, object] = raw_ml if isinstance(raw_ml, Mapping) else {}
    enriched: dict[str, object] = {
        **telemetry,
        "median_loot": {
            **{r: round(median_loot[r], 2) for r in RESOURCE_KEYS},
            "spice_eq": round(
                coerce_finite_float(ml_map.get("spice_eq"), float(game_data.spice_equivalent(median_loot))), 2
            ),
        },
        "median_round_trip_seconds": round(median_rt, 2),
        "sample_count": max(0, sample_count) if sample_count >= 0 else (1 if has_active_loot else 0),
        "current_fleet_slots": start_slots,
        "attacks_per_slot_per_hour": round(tel_per_slot, 4),
        "estimated_attacks_per_hour": round(tel_aph, 4),
        "pace_set_by": pace_set_by,
        "income_per_slot_per_hour": {r: round(slot_inc[r], 2) for r in RESOURCE_KEYS},
        "hourly_loot": {
            **{r: round(income_now[r], 2) for r in RESOURCE_KEYS},
            "spice_eq": round(float(game_data.spice_equivalent(income_now)), 2),
        },
    }
    if apd is not None and "income_basis" in telemetry:
        enriched["income_basis"] = "attacks_per_day"
    return enriched, tel_per_slot


def _compute_summary_totals(
    simulator: EmpirePathSimulator,
    best_run: Mapping[str, object],
    factor_levels: Sequence[Mapping[str, object]],
) -> tuple[
    list[int],
    list[float],
    list[float],
    list[float],
    float,
    float,
    list[float],
    int,
    dict[str, str],
    dict[str, str],
]:
    started = seq_int(best_run.get("order"))
    cost_spent = [sum(simulator.cost[s][pos] for s in started) for pos in range(4)]
    loot_collected = seq_float(best_run.get("loot_collected")) or [0.0, 0.0, 0.0, 0.0]
    spare_diverted = seq_float(best_run.get("spare_diverted")) or [0.0, 0.0, 0.0, 0.0]
    cost_val = float(game_data.spice_equivalent(dict(zip(RESOURCE_KEYS, cost_spent, strict=False))))
    loot_val = float(game_data.spice_equivalent(dict(zip(RESOURCE_KEYS, loot_collected, strict=False))))
    weekly_sp = seq_float(best_run.get("weekly_spice_eq"))
    raw_ff = best_run.get("final_founded")
    colonies_founded = sum(1 for f in raw_ff if f) if isinstance(raw_ff, Sequence) else 0
    bld_reached = {
        str(r["name"]): f"{coerce_int(r['reached_max_level'], 0)}/{coerce_int(r['target_max_level'], 0)}"
        for r in factor_levels
        if r.get("factor_kind") == "building"
    }
    res_reached = {
        str(r["name"]): f"{coerce_int(r['reached_max_level'], 0)}/{coerce_int(r['target_max_level'], 0)}"
        for r in factor_levels
        if r.get("factor_kind") == "research"
    }
    return (
        started,
        cost_spent,
        loot_collected,
        spare_diverted,
        cost_val,
        loot_val,
        weekly_sp,
        colonies_founded,
        bld_reached,
        res_reached,
    )


def _summary_tail(
    simulator: EmpirePathSimulator,
    problem: Mapping[str, object],
    best_run: Mapping[str, object],
    smelting_out: Mapping[str, object],
    transport_demand_out: Mapping[str, object],
    loot_collected: Sequence[float],
    loot_val: float,
    cost_val: float,
    in_prog_counts: tuple[int, int, int],
    elapsed_ms: float,
) -> dict[str, object]:
    in_b_cnt, in_r_cnt, in_f_cnt = in_prog_counts
    return {
        "metal_smelted": int(round(coerce_finite_float(best_run.get("metal_smelted"), 0.0))),
        "initial_smelting_live_mode": smelting_out["initial_live_mode"],
        "initial_smelting_live_enabled": smelting_out["initial_live_any_enabled"],
        "initial_smelting_target_enabled": smelting_out["initial_target_enabled"],
        "initial_smelting_toggle_needed": smelting_out["initial_toggle_needed"],
        "smelting_hours_total": smelting_out["smelting_hours_total"],
        "smelting_toggles_count": smelting_out["smelting_toggles_count"],
        "loot_counted": {r: int(round(loot_collected[pos])) for pos, r in enumerate(RESOURCE_KEYS)},
        "loot_share_of_cost_spent": round(loot_val / cost_val, 4) if cost_val > 0.0 else 0.0,
        "transport_dispatch_delay_minutes": round(simulator.transport_dispatch_delay_minutes, 2),
        "transports_dispatched": coerce_int(best_run.get("transports_dispatched"), 0),
        "early_transports_dispatched": coerce_int(best_run.get("early_transports_dispatched"), 0),
        "avg_transport_travel_seconds": round(coerce_finite_float(problem.get("avg_transport_travel_seconds"), 0.0), 2),
        "max_transport_travel_seconds": round(coerce_finite_float(problem.get("max_transport_travel_seconds"), 0.0), 2),
        "planned_cargo_per_hour_24h": transport_demand_out["cargo_per_hour_24h"],
        "recommended_cargo_capacity": transport_demand_out["recommended_cargo_capacity"],
        "recommended_large_transports": transport_demand_out["recommended_large_transports"],
        "in_progress_buildings_count": in_b_cnt,
        "in_progress_research_count": in_r_cnt,
        "in_flight_arrivals_count": in_f_cnt,
        "started_from_live_state": True,
        "live_snapshot_ts": coerce_finite_float(problem.get("live_snapshot_ts"), 0.0),
        "elapsed_ms": elapsed_ms,
    }


def build_summary_payload(
    simulator: EmpirePathSimulator,
    problem: Mapping[str, object],
    best_strategy_name: str,
    best_run: Mapping[str, object],
    *,
    factor_levels: Sequence[Mapping[str, object]],
    steps_out: Sequence[Mapping[str, object]],
    smelting_out: Mapping[str, object],
    transport_demand_out: Mapping[str, object],
    busiest_queue: str,
    busiest_hours: float,
    tel_per_slot: float,
    use_realized_income: bool,
    in_prog_counts: tuple[int, int, int],
    elapsed_ms: float,
) -> dict[str, object]:
    """Assemble the top-level ``summary`` dictionary returned by ``run_ai_planner``."""
    lower_bounds = sorted(simulator.lower_bounds(), key=lambda item: item[1], reverse=True)
    top_lb_label, top_lb_hours = lower_bounds[0] if lower_bounds else ("none", 0.0)
    tot = _compute_summary_totals(simulator, best_run, factor_levels)
    started, cost_spent, loot_collected, spare_diverted, cost_val, loot_val = tot[:6]
    weekly_sp, colonies_founded, buildings_reached, research_reached = tot[6:]
    waiting_step = coerce_int(best_run.get("waiting_for_step"), -1)
    run_hours = coerce_finite_float(best_run.get("hours"), float("inf"))
    disc_reward = coerce_finite_float(best_run.get("discounted_reward"), 0.0)
    total_net_sp = coerce_finite_float(best_run.get("total_spice_eq_produced"), 0.0)
    reward_ub = float(simulator.reward_upper_bound)
    tail = _summary_tail(
        simulator,
        problem,
        best_run,
        smelting_out,
        transport_demand_out,
        loot_collected,
        loot_val,
        cost_val,
        in_prog_counts,
        elapsed_ms,
    )
    return {
        "strategy": best_strategy_name,
        "status": str(best_run.get("status") or ""),
        "reward_weeks": int(simulator.reward_weeks),
        "weekly_discount": float(simulator.weekly_discount),
        "horizon_hours": float(simulator.horizon_hours),
        "horizon": fmt_path_time(simulator.horizon_hours),
        "discounted_reward": round(disc_reward, 2),
        "discounted_reward_spice_eq": round(disc_reward, 2),
        "reward_upper_bound": round(reward_ub, 2),
        "share_of_reward_ceiling_percent": round(100.0 * disc_reward / reward_ub, 2) if reward_ub > 0.0 else 0.0,
        "total_spice_eq_produced": round(total_net_sp, 2),
        "total_net_spice_eq": round(total_net_sp, 2),
        "week_1_spice_eq": round(weekly_sp[0] if weekly_sp else 0.0, 2),
        "last_week_spice_eq": round(weekly_sp[-1] if weekly_sp else 0.0, 2),
        "hours": round(run_hours, 4) if math.isfinite(run_hours) else None,
        "time": fmt_path_time(run_hours),
        "lower_bound_hours": round(top_lb_hours, 4) if math.isfinite(top_lb_hours) else None,
        "lower_bound": fmt_path_time(top_lb_hours),
        "lower_bound_set_by": str(top_lb_label),
        "steps": (
            int(simulator._target_step_count) + sum(1 for s in started if simulator.kind[s] == "demolish")
            if bool(problem.get("allow_demolition", False))
            else int(len(simulator.steps))
        ),
        "steps_started": coerce_int(best_run.get("steps_started"), 0),
        "steps_completed": coerce_int(best_run.get("steps_completed"), 0),
        "steps_running": coerce_int(best_run.get("steps_running"), 0),
        "actions_count": len(steps_out),
        "allow_demolition": bool(problem.get("allow_demolition", False)),
        "demolitions_planned": sum(1 for s in steps_out if s.get("kind") == "DEMOLISH"),
        "stopped_at_hours": round(coerce_finite_float(best_run.get("stopped_at_hours"), 0.0), 4),
        "waiting_for": (
            f"{simulator.steps[waiting_step]['name']} {simulator.steps[waiting_step]['level_to']}"
            if waiting_step >= 0
            else None
        ),
        "colonies_founded": colonies_founded,
        "colonies_target": int(simulator.planet_count),
        "start_fleet_slots": coerce_int(best_run.get("start_fleet_slots"), 0),
        "final_fleet_slots": coerce_int(best_run.get("final_fleet_slots"), 0),
        "attacks_per_slot_per_hour": round(tel_per_slot, 4),
        **({"attack_income_basis": REALIZED_INCOME_BASIS} if use_realized_income else {}),
        "spare_percent": dict(zip(RESOURCE_KEYS, simulator.spare_percent, strict=False)),
        "spare_diverted": {r: int(round(spare_diverted[pos])) for pos, r in enumerate(RESOURCE_KEYS)},
        "max_building_levels_reached": buildings_reached,
        "research_levels_reached": research_reached,
        "busiest_queue": busiest_queue,
        "busy_hours_of_that_queue": round(max(0.0, busiest_hours), 2),
        "cost_spent": {r: int(round(cost_spent[pos])) for pos, r in enumerate(RESOURCE_KEYS)},
        **tail,
    }
