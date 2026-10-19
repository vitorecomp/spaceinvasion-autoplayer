"""Factor levels, weekly breakdown, step output, and transport reports for ``run_ai_planner``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import TYPE_CHECKING

from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_finite_float, coerce_int
from spaceinvasion.planner.ai._types import PATH_HOURS_PER_WEEK
from spaceinvasion.planner.ai.telemetry import fmt_path_time
from spaceinvasion.planner.config_optimizer import RESOURCE_KEYS

if TYPE_CHECKING:
    from spaceinvasion.planner.ai.simulator import EmpirePathSimulator


def seq_int(val: object) -> list[int]:
    """Coerce an untyped sequence into ``list[int]``."""
    return [coerce_int(x, 0) for x in val] if isinstance(val, Sequence) and not isinstance(val, (str, bytes)) else []


def seq_float(val: object) -> list[float]:
    """Coerce an untyped sequence into ``list[float]``."""
    return (
        [coerce_finite_float(x, 0.0) for x in val]
        if isinstance(val, Sequence) and not isinstance(val, (str, bytes))
        else []
    )


def seq_opt_float(val: object) -> list[float | None]:
    """Coerce an untyped sequence into ``list[float | None]``."""
    if not isinstance(val, Sequence) or isinstance(val, (str, bytes)):
        return []
    return [None if x is None else coerce_finite_float(x, 0.0) for x in val]


def _build_factor_levels(simulator: EmpirePathSimulator, run: Mapping[str, object]) -> list[dict[str, object]]:
    """Summarize start/reached/started/target max levels across colonies, buildings, and researches."""
    limit = coerce_finite_float(run.get("stopped_at_hours"), 0.0)
    order, starts = seq_int(run.get("order")), seq_opt_float(run.get("start"))
    raw_founded = run.get("final_founded")
    final_founded = [bool(f) for f in raw_founded] if isinstance(raw_founded, Sequence) else []
    raw_fl = run.get("final_levels")
    final_levels: list[Mapping[str, object]] = (
        [m for m in raw_fl if isinstance(m, Mapping)] if isinstance(raw_fl, Sequence) else []
    )
    raw_fr = run.get("final_research")
    final_research: Mapping[str, object] = raw_fr if isinstance(raw_fr, Mapping) else {}

    start_colonies = sum(1 for p in simulator.planets if bool(p["founded"]))
    reached_colonies = sum(1 for flag in final_founded if flag)
    started_colonies = start_colonies + sum(
        1 for step in order if simulator.kind[step] == "colonize" and (starts[step] or 0.0) <= limit
    )
    rows: list[dict[str, object]] = [
        {
            "factor_kind": "colonies",
            "key": "colonies",
            "name": "Colonies founded",
            "start_max_level": start_colonies,
            "reached_max_level": reached_colonies,
            "started_max_level": started_colonies,
            "target_max_level": simulator.planet_count,
            "planets_at_reached_max": reached_colonies,
        }
    ]
    _append_building_factor_rows(rows, simulator, order, final_levels)
    _append_research_factor_rows(rows, simulator, order, final_research)
    return rows


def _append_building_factor_rows(
    rows: list[dict[str, object]],
    simulator: EmpirePathSimulator,
    order: Sequence[int],
    final_levels: Sequence[Mapping[str, object]],
) -> None:
    bkeys: set[str] = set()
    for p in simulator.planets:
        for field_name in ("levels", "target_levels"):
            if isinstance((raw_m := p.get(field_name)), Mapping):
                for k, v in raw_m.items():
                    if coerce_int(v, 0) > 0:
                        bkeys.add(str(k))
    building_keys = sorted(bkeys, key=lambda item: int(item[1:]))
    started_max = {
        key: max(
            (
                coerce_int(p["levels"].get(key, 0) if isinstance(p["levels"], Mapping) else 0, 0)
                for p in simulator.planets
            ),
            default=0,
        )
        for key in building_keys
    }
    for job in simulator.initial_jobs:
        if str(job.get("kind") or "") == "build" and (jk := str(job.get("key") or "")) in started_max:
            started_max[jk] = max(started_max[jk], coerce_int(job.get("level_to"), 0))
    for step in order:
        if simulator.kind[step] == "build" and (sk := simulator.key[step]) in started_max:
            started_max[sk] = max(started_max[sk], int(simulator.level_to[step]))
    for key in building_keys:
        bid = int(key[1:])
        reached = max((coerce_int(lvls.get(key, 0), 0) for lvls in final_levels), default=0)
        start_m = max(
            (
                coerce_int(p["levels"].get(key, 0) if isinstance(p["levels"], Mapping) else 0, 0)
                for p in simulator.planets
            ),
            default=0,
        )
        tgt_m = max(
            (
                coerce_int(p["target_levels"].get(key, 0) if isinstance(p["target_levels"], Mapping) else 0, 0)
                for p in simulator.planets
            ),
            default=0,
        )
        rows.append(
            {
                "factor_kind": "building",
                "key": key,
                "name": str(game_data.BUILDING_BY_ID[bid]["name"]),
                "start_max_level": start_m,
                "reached_max_level": reached,
                "started_max_level": max(reached, started_max[key]),
                "target_max_level": tgt_m,
                "planets_at_reached_max": sum(1 for lvls in final_levels if coerce_int(lvls.get(key, 0), 0) == reached)
                if reached > 0
                else 0,
            }
        )


def _append_research_factor_rows(
    rows: list[dict[str, object]],
    simulator: EmpirePathSimulator,
    order: Sequence[int],
    final_research: Mapping[str, object],
) -> None:
    raw_rl, raw_rt = simulator.problem.get("research_levels"), simulator.problem.get("research_target")
    res_lvls: Mapping[str, object] = raw_rl if isinstance(raw_rl, Mapping) else {}
    res_tgts: Mapping[str, object] = raw_rt if isinstance(raw_rt, Mapping) else {}
    rkeys = sorted(
        {str(k) for k, v in (*res_lvls.items(), *res_tgts.items()) if coerce_int(v, 0) > 0},
        key=lambda item: int(item[1:]),
    )
    started_res = {key: coerce_int(res_lvls.get(key, 0), 0) for key in rkeys}
    for job in simulator.initial_jobs:
        if str(job.get("kind") or "") == "research" and (jk := str(job.get("key") or "")) in started_res:
            started_res[jk] = max(started_res[jk], coerce_int(job.get("level_to"), 0))
    for step in order:
        if simulator.kind[step] == "research" and (sk := simulator.key[step]) in started_res:
            started_res[sk] = max(started_res[sk], int(simulator.level_to[step]))
    for key in rkeys:
        reached = coerce_int(final_research.get(key, 0), 0)
        rows.append(
            {
                "factor_kind": "research",
                "key": key,
                "name": str(game_data.RESEARCH_BY_ID[key]["name"]),
                "start_max_level": coerce_int(res_lvls.get(key, 0), 0),
                "reached_max_level": reached,
                "started_max_level": max(reached, started_res[key]),
                "target_max_level": coerce_int(res_tgts.get(key, 0), 0),
                "planets_at_reached_max": 1 if reached > 0 else 0,
            }
        )


def build_queue_and_weekly_breakdown(
    simulator: EmpirePathSimulator, best_run: Mapping[str, object]
) -> tuple[str, float, list[dict[str, object]]]:
    """Compute busiest queue stats and weekly discounted production breakdown."""
    limit_hr = coerce_finite_float(best_run.get("stopped_at_hours"), 0.0)
    starts, ends = seq_opt_float(best_run.get("start")), seq_opt_float(best_run.get("end"))
    busiest_queue, busiest_hours = "none", -1.0
    for q in range(simulator.planet_count + 1):
        q_name = (
            f"building queue of {simulator.planets[q]['coords']}" if q < simulator.planet_count else "research queue"
        )
        busy = sum(
            max(0.0, min(limit_hr, float(ends[s] or 0.0)) - float(starts[s] or 0.0))
            for s in range(len(simulator.steps))
            if simulator.queue[s] == q and starts[s] is not None
        )
        if busy > busiest_hours:
            busiest_hours, busiest_queue = busy, q_name

    weekly_breakdown: list[dict[str, object]] = []
    cum_disc = 0.0
    raw_wr = best_run.get("weekly_resources")
    w_res = raw_wr if isinstance(raw_wr, Sequence) else ()
    w_sp = seq_float(best_run.get("weekly_spice_eq"))
    for w_idx in range(simulator.reward_weeks):
        weight = float(simulator.week_weights[w_idx])
        amounts = seq_float(w_res[w_idx]) if w_idx < len(w_res) else [0.0, 0.0, 0.0, 0.0]
        sp_eq = w_sp[w_idx] if w_idx < len(w_sp) else 0.0
        disc = weight * sp_eq
        cum_disc += disc
        weekly_breakdown.append(
            {
                "week": w_idx + 1,
                "discount_weight": round(weight, 6),
                **{r: round(float(amounts[pos]), 2) for pos, r in enumerate(RESOURCE_KEYS)},
                "spice_eq": round(sp_eq, 2),
                "discounted_spice_eq": round(disc, 2),
                "cumulative_discounted_spice_eq": round(cum_disc, 2),
            }
        )
    return busiest_queue, busiest_hours, weekly_breakdown


def build_steps_output(
    simulator: EmpirePathSimulator, best_run: Mapping[str, object], dispatch_delay_sec: float
) -> list[dict[str, object]]:
    """Format sequential planned steps with start/finish times and transport lead metadata."""
    raw_tr = best_run.get("step_transport_info")
    step_tr_map: Mapping[object, object] = raw_tr if isinstance(raw_tr, Mapping) else {}
    order = seq_int(best_run.get("order"))
    starts, ends = seq_opt_float(best_run.get("start")), seq_opt_float(best_run.get("end"))
    kind_map = {"build": "BUILD", "demolish": "DEMOLISH", "research": "RESEARCH", "colonize": "COLONIZE_PREVIEW"}
    steps_out: list[dict[str, object]] = []
    for seq, step_id in enumerate(order, start=1):
        details = simulator.steps[step_id]
        out_kind = kind_map.get(str(details["kind"]))
        if out_kind is None:
            continue
        start_hr, end_hr = float(starts[step_id] or 0.0), float(ends[step_id] or 0.0)
        dur_hr = max(0.0, end_hr - start_hr)
        week_num = min(simulator.reward_weeks, max(1, int(start_hr // PATH_HOURS_PER_WEEK) + 1))
        p_idx = coerce_int(details["planet"], -1)
        if p_idx >= 0:
            p_info = simulator.planets[p_idx]
            p_id, p_coords, p_name = coerce_int(p_info["planet_id"], 0), str(p_info["coords"]), str(p_info["name"])
        else:
            p_id, p_coords, p_name = 0, "account", "Account"
        cost_vals = seq_float(details.get("cost"))
        tr_info = step_tr_map.get(step_id)
        if isinstance(tr_info, Mapping):
            needs_tr = True
            req_hr = max(0.0, coerce_finite_float(tr_info.get("earliest_request_hr"), start_hr))
            tr_req_sec: float | None = round(req_hr * 3600.0, 2)
            tr_req_txt: str | None = "now" if req_hr <= 1e-6 else fmt_path_time(req_hr)
            tr_travel_sec = round(max(0.0, coerce_finite_float(tr_info.get("max_travel_hr"), 0.0)) * 3600.0, 2)
            tr_lead_sec = round(max(0.0, coerce_finite_float(tr_info.get("max_lead_hr"), 0.0)) * 3600.0, 2)
            raw_src = tr_info.get("sources")
            tr_sources = (
                [str(x) for x in raw_src] if isinstance(raw_src, Sequence) and not isinstance(raw_src, str) else []
            )
        else:
            needs_tr = False
            tr_req_sec, tr_req_txt, tr_travel_sec, tr_lead_sec, tr_sources = None, None, 0.0, 0.0, []
        steps_out.append(
            {
                "seq": seq,
                "start_sec": round(start_hr * 3600.0, 2),
                "duration_sec": round(dur_hr * 3600.0, 2),
                "finish_sec": round(end_hr * 3600.0, 2),
                "start_text": fmt_path_time(start_hr),
                "duration_text": fmt_path_time(dur_hr),
                "finish_text": fmt_path_time(end_hr),
                "week": week_num,
                "kind": out_kind,
                "planet_id": p_id,
                "planet_coords": p_coords,
                "planet_name": p_name,
                "entity_key": str(details["key"]),
                "entity_name": str(details["name"]),
                "from_level": coerce_int(details["level_from"], 0),
                "target_level": coerce_int(details["level_to"], 0),
                "cost": {r: int(round(cost_vals[pos])) for pos, r in enumerate(RESOURCE_KEYS)},
                "cost_spice_eq": round(coerce_finite_float(details.get("cost_spice_eq"), 0.0), 2),
                "needs_transport": needs_tr,
                "transport_request_at_sec": tr_req_sec,
                "transport_request_text": tr_req_txt,
                "transport_travel_sec": tr_travel_sec,
                "transport_dispatch_delay_sec": dispatch_delay_sec if needs_tr else 0.0,
                "transport_lead_sec": tr_lead_sec,
                "transport_sources": tr_sources,
            }
        )
    return steps_out


def build_transport_report(
    simulator: EmpirePathSimulator,
    problem: Mapping[str, object],
    best_run: Mapping[str, object],
    dispatch_delay_sec: float,
) -> tuple[dict[str, object], dict[str, object]]:
    """Build ``transport_matrix`` and ``transport_demand`` sections of ``run_ai_planner``."""
    raw_tsec, raw_dist, raw_hulls = (
        problem.get("travel_seconds"),
        problem.get("distance_units"),
        problem.get("transport_hull_by_planet"),
    )
    tsec_mat = raw_tsec if isinstance(raw_tsec, Sequence) else ()
    dist_mat = raw_dist if isinstance(raw_dist, Sequence) else ()
    hulls = raw_hulls if isinstance(raw_hulls, Sequence) else ()
    pairs: list[dict[str, object]] = []
    for i in range(simulator.planet_count):
        if not bool(simulator.planets[i]["founded"]):
            continue
        for j in range(simulator.planet_count):
            if i == j or not bool(simulator.planets[j]["founded"]):
                continue
            row_t = tsec_mat[i] if i < len(tsec_mat) and isinstance(tsec_mat[i], Sequence) else ()
            row_d = dist_mat[i] if i < len(dist_mat) and isinstance(dist_mat[i], Sequence) else ()
            ow_s = coerce_finite_float(row_t[j], 0.0) if j < len(row_t) else 0.0
            du = coerce_finite_float(row_d[j], 0.0) if j < len(row_d) else 0.0
            pairs.append(
                {
                    "origin_coords": str(simulator.planets[i]["coords"]),
                    "target_coords": str(simulator.planets[j]["coords"]),
                    "distance_units": round(du, 2),
                    "one_way_seconds": round(ow_s, 2),
                    "lead_seconds": round(ow_s + dispatch_delay_sec, 2),
                    "hull": str(hulls[i]) if i < len(hulls) else "s2",
                }
            )
    avg_ow = round(max(0.0, coerce_finite_float(problem.get("avg_transport_travel_seconds"), 0.0)), 2)
    matrix_out: dict[str, object] = {
        "dispatch_delay_minutes": round(simulator.transport_dispatch_delay_minutes, 2),
        "dispatch_delay_seconds": dispatch_delay_sec,
        "avg_one_way_seconds": avg_ow,
        "max_one_way_seconds": round(coerce_finite_float(problem.get("max_transport_travel_seconds"), 0.0), 2),
        "pairs": pairs,
    }
    raw_tr = best_run.get("step_transport_info")
    step_tr_map: Mapping[object, object] = raw_tr if isinstance(raw_tr, Mapping) else {}
    cargo_4h, cargo_24h, max_ship, steps_4h, steps_24h = 0.0, 0.0, 0.0, 0, 0
    for tr_info in step_tr_map.values():
        if not isinstance(tr_info, Mapping):
            continue
        step_cargo = sum(max(0.0, v) for v in seq_float(tr_info.get("total_shipped")))
        if step_cargo <= 0.0:
            continue
        req_hr = max(0.0, coerce_finite_float(tr_info.get("earliest_request_hr"), 0.0))
        if req_hr <= 24.0 + 1e-9:
            cargo_24h += step_cargo
            steps_24h += 1
            max_ship = max(max_ship, step_cargo)
            if req_hr <= 4.0 + 1e-9:
                cargo_4h += step_cargo
                steps_4h += 1
    cph_4h, cph_24h = round(cargo_4h / 4.0, 2), round(cargo_24h / 24.0, 2)
    rt_per_hr = round(3600.0 / (2.0 * avg_ow), 4) if avg_ow > 0.0 else 2.5
    rec_cap = int(math.ceil(cph_24h / max(0.25, rt_per_hr) / 0.5)) if cph_24h > 0.0 else 0
    demand_out: dict[str, object] = {
        "cargo_4h": int(round(cargo_4h)),
        "cargo_per_hour_4h": cph_4h,
        "cargo_24h": int(round(cargo_24h)),
        "cargo_per_hour_24h": cph_24h,
        "max_single_shipment_cargo": int(round(max_ship)),
        "shipment_steps_4h": steps_4h,
        "shipment_steps_24h": steps_24h,
        "avg_one_way_seconds": avg_ow,
        "round_trips_per_hour": rt_per_hr,
        "utilization_assumed": 0.5,
        "recommended_cargo_capacity": rec_cap,
        "recommended_large_transports": int(math.ceil(rec_cap / 30_000.0)) if rec_cap > 0 else 0,
        "recommended_small_transports": int(math.ceil(rec_cap / 6_000.0)) if rec_cap > 0 else 0,
    }
    return matrix_out, demand_out
