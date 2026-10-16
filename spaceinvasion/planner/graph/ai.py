"""Tab 3 Windowed AI-Planner Kahn DAG builder (decomposed, `<=100` LOC functions)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.combat.flight import normalize_coords
from spaceinvasion.core.catalog import sanitize_json_tree
from spaceinvasion.core.model import GameState
from spaceinvasion.planner.graph._ai_edges import StepDict, build_ai_edges, parse_week_filter
from spaceinvasion.planner.graph._ai_nodes import build_ai_nodes_meta, normalize_ai_step
from spaceinvasion.planner.graph.common import (
    assemble_lanes,
    clean_float,
    clean_int,
    clean_optional_int,
    clean_str,
    format_eta_short,
    kahn_longest_layers,
)


def _format_ai_node(m: StepDict, exec_rank: int | None, layer: int, in_deg: int, out_deg: int) -> dict[str, object]:
    return {
        "id": m["id"],
        "step_seq": m["step_seq"],
        "week": m["week"],
        "order_index": m["order_index"],
        "exec_rank": exec_rank,
        "topological_layer": layer,
        "lane_id": m["lane_id"],
        "priority": m["priority"],
        "kind": m["kind"],
        "category": m["category"],
        "status": m["status"],
        "planet_id": m["planet_id"],
        "planet_coords": m["planet_coords"],
        "planet_name": m["planet_name"],
        "target_id": m["target_id"],
        "target_name": m["target_name"],
        "target_level": m["target_level"],
        "entity_key": m["entity_key"],
        "reason": m["reason"],
        "executable_now": m["executable_now"],
        "completed": m["completed"],
        "cost": m["cost"],
        "missing": m["missing"],
        "wait_seconds": m["wait_seconds"],
        "wait_eta": m["wait_eta"],
        "duration_seconds": m["duration_seconds"],
        "duration_text": m["duration_text"],
        "total_seconds": m["total_seconds"],
        "total_eta": m["total_eta"],
        "eta_bottleneck": m["eta_bottleneck"],
        "needs_transport": m["needs_transport"],
        "transport_request_at_sec": m["transport_request_at_sec"],
        "transport_request_text": m["transport_request_text"],
        "transport_lead_sec": m["transport_lead_sec"],
        "transport_travel_sec": m["transport_travel_sec"],
        "transport_dispatch_delay_sec": m["transport_dispatch_delay_sec"],
        "transport_sources": m["transport_sources"],
        "in_degree": in_deg,
        "out_degree": out_deg,
    }


def _assemble_ai_nodes_and_summary(
    nodes_meta: Sequence[StepDict],
    layers: Sequence[int],
    incoming: Mapping[int, Sequence[int]],
    outgoing: Mapping[int, Sequence[int]],
    edges_out: Sequence[Mapping[str, object]],
    lanes_out: Sequence[Mapping[str, object]],
    by_edge_type: Mapping[str, int],
    *,
    total_plan_steps: int,
    completed_total: int,
    filtered_count: int,
    eff_window: int,
    clean_week: int | None,
    raw_pfilter: str,
    available_weeks: Sequence[int],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    nodes_out: list[dict[str, object]] = []
    by_priority: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    ready_count = waiting_count = blocked_count = queued_count = completed_in_window = exec_counter = 0
    max_finish_s = 0.0
    min_start_s: float | None = None

    for m in nodes_meta:
        idx = clean_int(m["idx"], 0)
        st = str(m["status"])
        if st == "ready":
            ready_count += 1
        elif st == "waiting_resources":
            waiting_count += 1
        elif st == "blocked_gate":
            blocked_count += 1
        elif st == "completed":
            completed_in_window += 1
        else:
            queued_count += 1

        prio = str(m["priority"])
        by_priority[prio] = by_priority.get(prio, 0) + 1
        tot_s = clean_float(m["total_seconds"], 0.0)
        wait_s = clean_float(m["wait_seconds"], 0.0)
        if tot_s > max_finish_s:
            max_finish_s = tot_s
        if min_start_s is None or wait_s < min_start_s:
            min_start_s = wait_s

        exec_rank: int | None = None
        if m["executable_now"]:
            exec_rank = exec_counter
            exec_counter += 1
        nodes_out.append(_format_ai_node(m, exec_rank, layers[idx], len(incoming[idx]), len(outgoing[idx])))

    window_span_s = max(0.0, max_finish_s - (min_start_s or 0.0)) if nodes_meta else 0.0
    summary: dict[str, object] = {
        "total_nodes": len(nodes_meta),
        "total_edges": len(edges_out),
        "total_plan_steps": total_plan_steps,
        "completed_steps": completed_total,
        "remaining_steps": total_plan_steps - completed_total,
        "filtered_steps": filtered_count,
        "window_size": eff_window,
        "week_filter": clean_week,
        "planet_filter": raw_pfilter or None,
        "available_weeks": list(available_weeks),
        "ready_count": ready_count,
        "waiting_count": waiting_count,
        "blocked_count": blocked_count,
        "queued_count": queued_count,
        "completed_in_window": completed_in_window,
        "lane_count": len(lanes_out),
        "active_lane_count": sum(1 for ln in lanes_out if clean_int(ln.get("node_count", 0), 0) > 0),
        "max_layer": max(layers, default=0),
        "critical_path_seconds": clean_float(max_finish_s, 0.0),
        "critical_path_eta": format_eta_short(max_finish_s),
        "window_span_seconds": clean_float(window_span_s, 0.0),
        "window_span_eta": format_eta_short(window_span_s),
        "by_priority": by_priority,
        "by_edge_type": dict(by_edge_type),
    }
    return nodes_out, summary


def _filter_ai_steps(
    all_steps: Sequence[StepDict],
    *,
    clean_week: int | None,
    raw_pfilter: str,
    pfilter_global_only: bool,
    pfilter_pid: int | None,
    pfilter_coords: str,
    pfilter_name_low: str,
) -> list[StepDict]:
    filtered: list[StepDict] = []
    for st in all_steps:
        if clean_week is not None:
            if st["week"] != clean_week:
                continue
        elif st["completed"]:
            continue
        if raw_pfilter:
            if pfilter_global_only:
                if st["lane_id"] != "lane:global":
                    continue
            elif st["kind"] != "RESEARCH" and not (
                (pfilter_pid is not None and st["planet_id"] == pfilter_pid)
                or (pfilter_coords and st["planet_coords"] == pfilter_coords)
                or (pfilter_name_low and str(st["planet_name"]).lower() == pfilter_name_low)
            ):
                continue
        filtered.append(st)
    return filtered


def _parse_ai_planet_filter(planet_filter: int | str | None) -> tuple[str, bool, int | None, str, str]:
    raw_pfilter = clean_str(planet_filter).strip() if planet_filter is not None else ""
    pfilter_global_only = raw_pfilter.lower() in ("global", "research", "0")
    pfilter_pid = clean_optional_int(planet_filter) if not pfilter_global_only else None
    pfilter_pid = None if (pfilter_pid is not None and pfilter_pid <= 0) else pfilter_pid
    pfilter_coords = normalize_coords(raw_pfilter) if (raw_pfilter and ":" in raw_pfilter) else ""
    pfilter_name_low = (
        raw_pfilter.lower()
        if (raw_pfilter and not pfilter_global_only and pfilter_pid is None and not pfilter_coords)
        else ""
    )
    return raw_pfilter, pfilter_global_only, pfilter_pid, pfilter_coords, pfilter_name_low


def build_ai_planner_graph(
    state: GameState | None,
    plan_result: Mapping[str, object] | None,
    *,
    window_size: int = 40,
    week_filter: int | None = None,
    planet_filter: int | str | None = None,
) -> dict[str, object]:
    """Construct a windowed, guaranteed-acyclic multi-lane Kahn DAG for Tab 3 (AI-Planner)."""
    eff_window = max(1, min(250, clean_int(window_size, 40)))
    clean_week = parse_week_filter(week_filter)
    raw_pfilter, pfilter_global_only, pfilter_pid, pfilter_coords, pfilter_name_low = _parse_ai_planet_filter(
        planet_filter
    )

    ordered_planets = list(state.planets) if state is not None else []
    planets_by_id = {clean_int(p.planet_id, 0): p for p in ordered_planets}
    planets_by_coords = {c: p for p in ordered_planets if (c := normalize_coords(clean_str(p.coords)))}
    planets_by_name = {n.lower(): p for p in ordered_planets if (n := clean_str(p.name))}
    researches_map = {
        clean_str(rk): max(0, clean_int(rv, 0)) for rk, rv in (state.researches.items() if state is not None else ())
    }

    raw_steps_seq: list[Mapping[str, object]] = []
    if isinstance(plan_result, Mapping):
        cand = plan_result.get("steps")
        if isinstance(cand, Sequence) and not isinstance(cand, (str, bytes)):
            raw_steps_seq = [s for s in cand if isinstance(s, Mapping)]

    all_steps = [
        normalize_ai_step(
            raw_pos,
            step,
            state=state,
            planets_by_id=planets_by_id,
            planets_by_coords=planets_by_coords,
            planets_by_name=planets_by_name,
            ordered_planets=ordered_planets,
            researches_map=researches_map,
        )
        for raw_pos, step in enumerate(raw_steps_seq)
    ]
    available_weeks = sorted({clean_int(st["week"], 1) for st in all_steps})
    completed_total = sum(1 for st in all_steps if st["completed"])
    filtered_steps = _filter_ai_steps(
        all_steps,
        clean_week=clean_week,
        raw_pfilter=raw_pfilter,
        pfilter_global_only=pfilter_global_only,
        pfilter_pid=pfilter_pid,
        pfilter_coords=pfilter_coords,
        pfilter_name_low=pfilter_name_low,
    )

    nodes_meta, lane_counts = build_ai_nodes_meta(
        filtered_steps[:eff_window],
        ordered_planets,
        raw_pfilter=raw_pfilter,
        pfilter_global_only=pfilter_global_only,
        pfilter_pid=pfilter_pid,
        pfilter_coords=pfilter_coords,
        pfilter_name_low=pfilter_name_low,
    )
    edge_acc = build_ai_edges(nodes_meta, researches_map)
    layers, edges_out, incoming, outgoing, by_edge_type = kahn_longest_layers(
        len(nodes_meta), edge_acc, [str(m["id"]) for m in nodes_meta]
    )
    lanes_out = assemble_lanes(
        ordered_planets,
        nodes_meta,
        lane_counts,
        require_in_lane_counts=True,
        fallback_type_fn=lambda m: "preview" if m["kind"] == "COLONIZE_PREVIEW" else "normal",
    )
    nodes_out, summary = _assemble_ai_nodes_and_summary(
        nodes_meta,
        layers,
        incoming,
        outgoing,
        edges_out,
        lanes_out,
        by_edge_type,
        total_plan_steps=len(all_steps),
        completed_total=completed_total,
        filtered_count=len(filtered_steps),
        eff_window=eff_window,
        clean_week=clean_week,
        raw_pfilter=raw_pfilter,
        available_weeks=available_weeks,
    )
    payload: dict[str, object] = {
        "planner_mode": "ai_planner",
        "lanes": lanes_out,
        "nodes": nodes_out,
        "edges": edges_out,
        "summary": summary,
    }
    sanitized: dict[str, object] = sanitize_json_tree(payload, nonfinite_float=0.0)
    return sanitized
