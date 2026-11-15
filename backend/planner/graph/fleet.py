"""Tab 4 Multi-lane Fleet & Attack Planner DAG builder (decomposed, `<=100` LOC functions)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

from backend.combat.flight import normalize_coords
from backend.core.catalog import sanitize_json_tree
from backend.core.config import StrategyConfig
from backend.core.model import GameState, PlanetState, PlannedAction, is_in_flight_fleet_job
from backend.planner.eta import attach_action_etas
from backend.planner.graph._fleet_edges import (
    FleetNode,
    build_fleet_edges,
    build_slot_budgets,
    is_fleet_planner_action,
)
from backend.planner.graph._fleet_nodes import append_planned_fleet_nodes, build_inflight_nodes
from backend.planner.graph.common import (
    assemble_lanes,
    clean_float,
    clean_int,
    clean_optional_float,
    clean_str,
    format_eta_short,
    kahn_longest_layers,
    res_to_dict,
)


def _format_fleet_node(m: FleetNode, layer: int, in_deg: int, out_deg: int) -> dict[str, object]:
    return {
        "id": m["id"],
        "order_index": m["order_index"],
        "exec_rank": m["exec_rank"],
        "topological_layer": layer,
        "lane_id": m["lane_id"],
        "priority": m["priority"],
        "kind": m["kind"],
        "mission_kind": m["mission_kind"],
        "category": m["category"],
        "status": m["status"],
        "in_flight": m["in_flight"],
        "planet_id": m["planet_id"],
        "planet_coords": m["planet_coords"],
        "planet_name": m["planet_name"],
        "target_id": m["target_id"],
        "target_name": m["target_name"],
        "target_level": m["target_level"],
        "reason": m["reason"],
        "executable_now": m["executable_now"],
        "cost": m["cost"],
        "missing": m["missing"],
        "wait_seconds": m["wait_seconds"],
        "wait_eta": m["wait_eta"],
        "duration_seconds": m["duration_seconds"],
        "duration_text": m["duration_text"],
        "total_seconds": m["total_seconds"],
        "total_eta": m["total_eta"],
        "eta_bottleneck": m["eta_bottleneck"],
        "in_degree": in_deg,
        "out_degree": out_deg,
    }


def _assemble_fleet_nodes_and_summary(
    nodes_meta: Sequence[FleetNode],
    fleet_actions: Sequence[object],
    layers: Sequence[int],
    incoming: Mapping[int, Sequence[int]],
    outgoing: Mapping[int, Sequence[int]],
    edges_out: Sequence[Mapping[str, object]],
    lanes_out: Sequence[Mapping[str, object]],
    by_edge_type: Mapping[str, int],
    *,
    total_slots_used: int,
    total_slots_max: int,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    nodes_out: list[dict[str, object]] = []
    by_priority: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    by_kind: dict[str, int] = {}
    ready_count = waiting_count = blocked_count = queued_count = in_flight_count = 0
    max_total_s = 0.0

    for m in nodes_meta:
        idx = clean_int(m["idx"], 0)
        st = str(m["status"])
        if st == "in_flight":
            in_flight_count += 1
        elif st == "ready":
            ready_count += 1
        elif st == "waiting_resources":
            waiting_count += 1
        elif st == "blocked_gate":
            blocked_count += 1
        else:
            queued_count += 1
        prio, kind = str(m["priority"]), str(m["kind"])
        by_priority[prio] = by_priority.get(prio, 0) + 1
        by_kind[kind] = by_kind.get(kind, 0) + 1
        tot_s = m["total_seconds"]
        if isinstance(tot_s, (int, float)) and math.isfinite(tot_s) and tot_s > max_total_s:
            max_total_s = float(tot_s)
        nodes_out.append(_format_fleet_node(m, layers[idx], len(incoming[idx]), len(outgoing[idx])))

    summary: dict[str, object] = {
        "total_nodes": len(nodes_meta),
        "total_edges": len(edges_out),
        "in_flight_count": in_flight_count,
        "ready_count": ready_count,
        "waiting_count": waiting_count,
        "blocked_count": blocked_count,
        "queued_count": queued_count,
        "lane_count": len(lanes_out),
        "active_lane_count": sum(1 for ln in lanes_out if clean_int(ln.get("node_count", 0), 0) > 0),
        "max_layer": max(layers, default=0),
        "critical_path_seconds": clean_float(max_total_s, 0.0),
        "critical_path_eta": format_eta_short(max_total_s),
        "total_slots_used": total_slots_used,
        "total_slots_max": total_slots_max,
        "total_slots_available": max(0, total_slots_max - total_slots_used),
        "planned_fleet_actions": sum(
            1 for a in fleet_actions if clean_str(getattr(a, "kind", "")).upper() not in ("BUILD_SHIP", "RESEARCH")
        ),
        "planned_shipyard_actions": sum(
            1 for a in fleet_actions if clean_str(getattr(a, "kind", "")).upper() == "BUILD_SHIP"
        ),
        "planned_research_actions": sum(
            1 for a in fleet_actions if clean_str(getattr(a, "kind", "")).upper() == "RESEARCH"
        ),
        "by_priority": by_priority,
        "by_kind": by_kind,
        "by_edge_type": dict(by_edge_type),
    }
    return nodes_out, summary


def _partition_inflight_jobs(
    state: GameState | None,
    ordered_planets: Sequence[PlanetState],
    planets_by_id: Mapping[int, PlanetState],
    planets_by_coords: Mapping[str, PlanetState],
) -> tuple[dict[int, list[object]], list[object]]:
    fallback_pid = 0
    if state is not None:
        act_pid = clean_int(state.active_planet_id, 0)
        fallback_pid = (
            act_pid
            if act_pid in planets_by_id
            else (clean_int(ordered_planets[0].planet_id, 0) if ordered_planets else 0)
        )
    inflight_by_pid: dict[int, list[object]] = {pid: [] for pid in planets_by_id}
    inflight_unassigned: list[object] = []
    if state is not None:
        for q in state.active_queues:
            if not is_in_flight_fleet_job(q):
                continue
            q_orig = normalize_coords(clean_str(getattr(q, "origin_coords", "")))
            matched_p = planets_by_coords.get(q_orig) if q_orig else None
            if matched_p is not None:
                inflight_by_pid[clean_int(matched_p.planet_id, 0)].append(q)
            elif fallback_pid > 0:
                inflight_by_pid.setdefault(fallback_pid, []).append(q)
            else:
                inflight_unassigned.append(q)

    def _rem_key(job: object) -> float:
        rs = clean_optional_float(getattr(job, "remaining_seconds", None))
        return rs if rs is not None else 1e12

    for jobs in inflight_by_pid.values():
        jobs.sort(key=_rem_key)
    inflight_unassigned.sort(key=_rem_key)
    return inflight_by_pid, inflight_unassigned


def build_fleet_planner_graph(
    state: GameState | None,
    planned_actions: Sequence[PlannedAction],
    config: StrategyConfig,
    *,
    attack_telemetry: Mapping[str, object] | None = None,
    spare_budget: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Construct a guaranteed-acyclic multi-lane Fleet & Attack Planner DAG with `1 + b10` slot budgets."""
    eff_cfg = config if isinstance(config, StrategyConfig) else StrategyConfig()
    ordered_planets = list(state.planets) if state is not None else []
    planets_by_id = {clean_int(p.planet_id, 0): p for p in ordered_planets}
    planets_by_coords = {c: p for p in ordered_planets if (c := normalize_coords(clean_str(p.coords)))}
    inflight_by_pid, inflight_unassigned = _partition_inflight_jobs(
        state, ordered_planets, planets_by_id, planets_by_coords
    )

    fleet_actions: list[object] = [a for a in (planned_actions or ()) if is_fleet_planner_action(a)]
    if state is not None and fleet_actions:
        needs_eta = any(
            any(v > 0 for v in res_to_dict(getattr(a, "missing", None)).values())
            and getattr(a, "wait_seconds", 0.0) == 0.0
            and getattr(a, "wait_eta", "Ready") == "Ready"
            for a in fleet_actions
        )
        if needs_eta:
            fleet_actions = attach_action_etas(state, fleet_actions)

    slot_budgets_out, slot_budget_by_pid, total_used, total_max = build_slot_budgets(
        state, ordered_planets, inflight_by_pid, fleet_actions, eff_cfg
    )
    lane_counts: dict[str, int] = {
        "lane:global": 0,
        **{f"lane:planet:{clean_int(p.planet_id, 0)}": 0 for p in ordered_planets},
    }
    nodes_meta, inflight_indices_by_pid = build_inflight_nodes(
        ordered_planets, planets_by_id, inflight_by_pid, inflight_unassigned, lane_counts
    )
    append_planned_fleet_nodes(nodes_meta, fleet_actions, planets_by_id, lane_counts)

    edge_acc = build_fleet_edges(nodes_meta, slot_budget_by_pid, inflight_indices_by_pid)
    layers, edges_out, incoming, outgoing, by_edge_type = kahn_longest_layers(
        len(nodes_meta), edge_acc, [str(m["id"]) for m in nodes_meta]
    )
    lanes_out = assemble_lanes(
        ordered_planets,
        nodes_meta,
        lane_counts,
        global_label="Empire Tactical, Research & Transmitter",
        global_extra={"b10_level": 0, "fleet_slots_used": total_used, "fleet_slots_max": total_max},
        planet_extra_fn=lambda pid: {
            "b10_level": slot_budget_by_pid.get(pid, {}).get("b10_level", 0),
            "fleet_slots_used": slot_budget_by_pid.get(pid, {}).get("used_slots", 0),
            "fleet_slots_max": slot_budget_by_pid.get(pid, {}).get("max_slots", 1),
        },
        fallback_extra={"b10_level": 0, "fleet_slots_used": 0, "fleet_slots_max": 1},
    )
    nodes_out, summary = _assemble_fleet_nodes_and_summary(
        nodes_meta,
        fleet_actions,
        layers,
        incoming,
        outgoing,
        edges_out,
        lanes_out,
        by_edge_type,
        total_slots_used=total_used,
        total_slots_max=total_max,
    )
    payload: dict[str, object] = {
        "planner_mode": "fleet_planner",
        "lanes": lanes_out,
        "nodes": nodes_out,
        "edges": edges_out,
        "summary": summary,
        "slot_budgets": slot_budgets_out,
        "attack_telemetry": dict(attack_telemetry) if isinstance(attack_telemetry, Mapping) else {},
        "spare_budget": dict(spare_budget) if isinstance(spare_budget, Mapping) else {},
    }
    sanitized: dict[str, object] = sanitize_json_tree(payload, nonfinite_float=0.0)
    return sanitized
