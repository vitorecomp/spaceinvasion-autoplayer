"""Tab 1 Heuristic Action Graph DAG builder (decomposed, zero legacy GA branches)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
import math

from backend.core.catalog import (
    BUILDING_BY_ID,
    RESEARCH_BY_ID,
    SHIPS,
    cap_json_int,
    safe_int,
    sanitize_json_tree,
)
from backend.core.config import StrategyConfig, strategy_config_from_dict
from backend.core.model import GameState, PlanetState, PlannedAction
from backend.core.settings import normalize_planner_mode
from backend.planner.eta import attach_action_etas
from backend.planner.graph._heuristics_economy import NodeMeta
from backend.planner.graph._heuristics_edges import build_heuristic_edges
from backend.planner.graph.common import (
    RESOURCE_KEYS,
    assemble_lanes,
    classify_node_category,
    classify_node_status,
    clean_float,
    clean_int,
    clean_optional_float,
    clean_optional_int,
    clean_str,
    diff_strategy_configs,
    extract_system_key,
    format_eta_short,
    kahn_longest_layers,
    res_to_dict,
    resolve_building_id,
    resolve_research_key,
    resolve_ship_id_and_slug,
    serialize_config,
    slot_key_for_action,
)


def _build_single_heuristic_node(
    idx: int,
    act: object,
    exec_rank: int | None,
    state: GameState | None,
    planets_by_id: Mapping[int, PlanetState],
) -> NodeMeta:
    kind = clean_str(getattr(act, "kind", "")).strip().upper() or "BUILD"
    prio = clean_str(getattr(act, "priority", "P2")).strip().upper()
    prio = prio if prio in ("P0", "P1", "P2", "P3") else "P2"
    pid = clean_int(getattr(act, "planet_id", 0), 0)
    p_obj = planets_by_id.get(pid)
    if p_obj is None and state is not None and pid > 0:
        p_obj = next((sp for sp in state.planets if clean_int(sp.planet_id, 0) == pid), None)
    p_coords = clean_str(getattr(act, "planet_coords", "")) or (clean_str(p_obj.coords) if p_obj is not None else "")
    p_name = clean_str(p_obj.name) if p_obj is not None else (f"Colony {pid}" if pid > 0 else "Empire")
    raw_tid = getattr(act, "target_id", None)
    t_name = clean_str(getattr(act, "target_name", ""))
    reason = clean_str(getattr(act, "reason", ""))
    exec_now = bool(getattr(act, "executable_now", False))

    raw_cost_obj = getattr(act, "cost", None)
    if raw_cost_obj is None:
        raw_cost_d = {rk: 0 for rk in RESOURCE_KEYS}
    elif isinstance(raw_cost_obj, Mapping):
        raw_cost_d = {rk: max(0, safe_int(raw_cost_obj.get(rk, 0), 0)) for rk in RESOURCE_KEYS}
    else:
        raw_cost_d = {rk: max(0, safe_int(getattr(raw_cost_obj, rk, 0), 0)) for rk in RESOURCE_KEYS}
    cost_d = {rk: cap_json_int(val) for rk, val in raw_cost_d.items()}
    missing_d = res_to_dict(getattr(act, "missing", None))

    bid = (
        resolve_building_id(raw_tid, t_name)
        if kind in ("BUILD", "WAIT_RESOURCES", "THROTTLE_BUILDING", "DEMOLISH_BUILDING")
        else None
    )
    rkey = resolve_research_key(raw_tid, t_name) if kind == "RESEARCH" else None
    sid, ship_slug = (
        resolve_ship_id_and_slug(raw_tid, t_name)
        if kind in ("BUILD_SHIP", "SPY", "RECYCLE", "COLONIZE")
        else (None, None)
    )
    lane_id = (
        "lane:global"
        if (kind in ("RESEARCH", "CLAIM_TUTORIAL", "ACTIVATE_TRANSMITTER") or pid <= 0)
        else f"lane:planet:{pid}"
    )

    unit_reqs: list[object] = []
    if kind in ("BUILD", "WAIT_RESOURCES") and bid is not None and bid in BUILDING_BY_ID:
        unit_reqs = list(BUILDING_BY_ID[bid].get("requirements", ()))
    elif kind == "RESEARCH" and rkey is not None and rkey in RESEARCH_BY_ID:
        unit_reqs = list(RESEARCH_BY_ID[rkey].get("requirements", ()))
    elif kind == "BUILD_SHIP" and ship_slug is not None and ship_slug in SHIPS:
        unit_reqs = list(SHIPS[ship_slug].get("requirements", ()))

    bottleneck_raw = getattr(act, "eta_bottleneck", None)
    return {
        "idx": idx,
        "id": f"act_{idx}",
        "order_index": idx,
        "exec_rank": exec_rank,
        "lane_id": lane_id,
        "priority": prio,
        "kind": kind,
        "category": classify_node_category(kind, bid, sid),
        "status": classify_node_status(kind, exec_now, missing_d, reason),
        "planet_id": pid,
        "planet_obj": p_obj,
        "planet_coords": p_coords,
        "planet_name": p_name,
        "target_id": clean_optional_int(raw_tid),
        "target_name": t_name,
        "target_level": clean_optional_int(getattr(act, "target_level", None)),
        "reason": reason,
        "executable_now": exec_now,
        "cost": cost_d,
        "raw_cost": raw_cost_d,
        "missing": missing_d,
        "wait_seconds": clean_optional_float(getattr(act, "wait_seconds", 0.0)),
        "wait_eta": clean_str(getattr(act, "wait_eta", "Ready"), "Ready"),
        "duration_seconds": clean_float(getattr(act, "duration_seconds", 0.0), 0.0),
        "duration_text": clean_str(getattr(act, "duration_text", "Instant"), "Instant"),
        "total_seconds": clean_optional_float(getattr(act, "total_seconds", 0.0)),
        "total_eta": clean_str(getattr(act, "total_eta", "Ready"), "Ready"),
        "eta_bottleneck": clean_str(bottleneck_raw) if bottleneck_raw else None,
        "bid": bid,
        "rkey": rkey,
        "sid": sid,
        "ship_slug": ship_slug,
        "unit_reqs": unit_reqs,
        "slot_key": slot_key_for_action(kind, pid),
        "sys_key": extract_system_key(t_name) or extract_system_key(reason),
    }


def _build_heuristic_nodes_meta(
    state: GameState | None,
    raw_list: Sequence[object],
    planets_by_id: Mapping[int, PlanetState],
    ordered_planets: Sequence[PlanetState],
) -> tuple[list[NodeMeta], dict[str, int]]:
    nodes_meta: list[NodeMeta] = []
    exec_counter = 0
    lane_counts: dict[str, int] = {
        "lane:global": 0,
        **{f"lane:planet:{clean_int(p.planet_id, 0)}": 0 for p in ordered_planets},
    }
    for idx, act in enumerate(raw_list):
        exec_rank: int | None = None
        if bool(getattr(act, "executable_now", False)):
            exec_rank = exec_counter
            exec_counter += 1
        meta = _build_single_heuristic_node(idx, act, exec_rank, state, planets_by_id)
        lid = str(meta["lane_id"])
        lane_counts[lid] = lane_counts.get(lid, 0) + 1
        nodes_meta.append(meta)
    return nodes_meta, lane_counts


def _format_heuristic_node(
    m: NodeMeta,
    layer: int,
    in_deg: int,
    out_deg: int,
) -> dict[str, object]:
    return {
        "id": m["id"],
        "order_index": m["order_index"],
        "exec_rank": m["exec_rank"],
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
        "ga_step": None,
        "in_degree": in_deg,
        "out_degree": out_deg,
    }


def _assemble_heuristic_nodes_and_summary(
    nodes_meta: Sequence[NodeMeta],
    layers: Sequence[int],
    incoming: Mapping[int, Sequence[int]],
    outgoing: Mapping[int, Sequence[int]],
    edges_out: Sequence[Mapping[str, object]],
    lanes_out: Sequence[Mapping[str, object]],
    by_edge_type: Mapping[str, int],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    nodes_out: list[dict[str, object]] = []
    by_priority: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    ready_count = waiting_count = blocked_count = queued_count = 0
    bottleneck_counts: dict[str, int] = {}
    max_total_s = 0.0
    has_unbounded_eta = False

    for m in nodes_meta:
        idx = clean_int(m["idx"], 0)
        st = str(m["status"])
        if st == "ready":
            ready_count += 1
        elif st == "waiting_resources":
            waiting_count += 1
        elif st == "blocked_gate":
            blocked_count += 1
        else:
            queued_count += 1
        prio = str(m["priority"])
        by_priority[prio] = by_priority.get(prio, 0) + 1
        bneck = m["eta_bottleneck"]
        if isinstance(bneck, str) and bneck:
            bottleneck_counts[bneck] = bottleneck_counts.get(bneck, 0) + 1
        tot_s = m["total_seconds"]
        if tot_s is None and st == "waiting_resources":
            has_unbounded_eta = True
        elif isinstance(tot_s, (int, float)) and math.isfinite(tot_s) and tot_s > max_total_s:
            max_total_s = float(tot_s)
        nodes_out.append(_format_heuristic_node(m, layers[idx], len(incoming[idx]), len(outgoing[idx])))

    primary_bottleneck = (
        max(sorted(bottleneck_counts.keys()), key=lambda k: bottleneck_counts[k]) if bottleneck_counts else None
    )
    crit_eta = (
        "Blocked (0/h)"
        if (has_unbounded_eta and max_total_s <= 0.0 and waiting_count > 0)
        else format_eta_short(max_total_s)
    )
    summary: dict[str, object] = {
        "total_nodes": len(nodes_meta),
        "total_edges": len(edges_out),
        "ready_count": ready_count,
        "waiting_count": waiting_count,
        "blocked_count": blocked_count,
        "queued_count": queued_count,
        "lane_count": len(lanes_out),
        "active_lane_count": sum(1 for ln in lanes_out if clean_int(ln.get("node_count", 0), 0) > 0),
        "max_layer": max(layers, default=0),
        "critical_path_seconds": clean_float(max_total_s, 0.0),
        "critical_path_eta": crit_eta,
        "primary_bottleneck": primary_bottleneck,
        "by_priority": by_priority,
        "by_edge_type": dict(by_edge_type),
    }
    return nodes_out, summary


def _serialize_config_pair(
    act_cfg: StrategyConfig,
    eff_cfg: StrategyConfig,
) -> tuple[bool, list[str], dict[str, object], dict[str, object]]:
    if act_cfg is eff_cfg or act_cfg == eff_cfg:
        act_dict = serialize_config(act_cfg, sanitize=False, already_normalized=False)
        st_map = act_dict["shipyard_targets"]
        fr_map = act_dict.get("fleet_research_targets")
        eff_dict: dict[str, object] = {
            **act_dict,
            "shipyard_targets": dict(st_map) if isinstance(st_map, Mapping) else {},
            "fleet_research_targets": dict(fr_map) if isinstance(fr_map, Mapping) else {},
        }
        return False, [], act_dict, eff_dict
    norm_act = strategy_config_from_dict(asdict(act_cfg))
    norm_eff = strategy_config_from_dict(asdict(eff_cfg))
    changed = diff_strategy_configs(norm_act, norm_eff)
    act_dict = serialize_config(norm_act, sanitize=False, already_normalized=True)
    eff_dict = serialize_config(norm_eff, sanitize=False, already_normalized=True)
    return bool(changed), changed, act_dict, eff_dict


def build_action_graph(
    state: GameState | None,
    actions: Sequence[PlannedAction],
    *,
    effective_config: StrategyConfig,
    active_config: StrategyConfig | None = None,
    simulated: bool = False,
    optimizer_summary: Mapping[str, object] | None = None,
    planet_filter: int | None = None,
    sanitize: bool = True,
) -> dict[str, object]:
    """Construct a deterministic, guaranteed-acyclic multi-lane Action Graph DAG."""
    _ = optimizer_summary
    eff_cfg = effective_config if isinstance(effective_config, StrategyConfig) else StrategyConfig()
    act_cfg = active_config if isinstance(active_config, StrategyConfig) else eff_cfg
    mode = normalize_planner_mode(eff_cfg.planner_mode, strict=False)
    is_dirty, changed_keys, act_cfg_dict, eff_cfg_dict = _serialize_config_pair(act_cfg, eff_cfg)

    clean_pfilter = clean_optional_int(planet_filter)
    if clean_pfilter is not None and clean_pfilter <= 0:
        clean_pfilter = None

    raw_list: list[object] = list(actions or ())
    if clean_pfilter is not None:
        raw_list = [
            a
            for a in raw_list
            if clean_int(getattr(a, "planet_id", 0), 0) in (0, clean_pfilter)
            or clean_str(getattr(a, "kind", "")).upper() in ("RESEARCH", "CLAIM_TUTORIAL", "ACTIVATE_TRANSMITTER")
        ]
    if state is not None and raw_list:
        needs_eta = any(
            any(v > 0 for v in res_to_dict(getattr(a, "missing", None)).values())
            and getattr(a, "wait_seconds", 0.0) == 0.0
            and getattr(a, "wait_eta", "Ready") == "Ready"
            for a in raw_list
        )
        if needs_eta:
            raw_list = attach_action_etas(state, raw_list)

    ordered_planets = [
        p
        for p in (state.planets if state is not None else ())
        if clean_pfilter is None or clean_int(p.planet_id, 0) == clean_pfilter
    ]
    planets_by_id = {clean_int(p.planet_id, 0): p for p in ordered_planets}
    researches_map = {
        clean_str(rk): max(0, clean_int(rv, 0)) for rk, rv in (state.researches.items() if state is not None else ())
    }
    warn_ratio = max(0.1, min(0.99, clean_float(eff_cfg.storage_warn_ratio, 0.85)))

    nodes_meta, lane_counts = _build_heuristic_nodes_meta(state, raw_list, planets_by_id, ordered_planets)
    edge_acc = build_heuristic_edges(nodes_meta, researches_map, warn_ratio=warn_ratio)
    layers, edges_out, incoming, outgoing, by_edge_type = kahn_longest_layers(
        len(nodes_meta), edge_acc, [str(m["id"]) for m in nodes_meta]
    )
    lanes_out = assemble_lanes(ordered_planets, nodes_meta, lane_counts)
    nodes_out, summary = _assemble_heuristic_nodes_and_summary(
        nodes_meta, layers, incoming, outgoing, edges_out, lanes_out, by_edge_type
    )

    payload: dict[str, object] = {
        "mode": mode,
        "simulated": bool(simulated),
        "state_available": state is not None,
        "is_dirty": is_dirty,
        "changed_keys": changed_keys,
        "planet_filter": clean_pfilter,
        "active_config": act_cfg_dict,
        "effective_config": eff_cfg_dict,
        "lanes": lanes_out,
        "nodes": nodes_out,
        "edges": edges_out,
        "summary": summary,
        "ga_summary": None,
    }
    if sanitize:
        sanitized: dict[str, object] = sanitize_json_tree(payload, nonfinite_float=0.0)
        return sanitized
    return payload
