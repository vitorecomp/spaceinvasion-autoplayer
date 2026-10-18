"""Node metadata builders for the Tab 4 Fleet & Attack Planner DAG."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.combat.flight import normalize_coords
from spaceinvasion.core.state import PlanetState
from spaceinvasion.planner.graph._fleet_edges import FleetNode, classify_inflight_mission
from spaceinvasion.planner.graph.common import (
    RESOURCE_KEYS,
    classify_node_category,
    classify_node_status,
    clean_float,
    clean_int,
    clean_optional_float,
    clean_optional_int,
    clean_str,
    extract_first_coords,
    extract_system_key,
    format_eta_short,
    res_to_dict,
    resolve_research_key,
    resolve_ship_id_and_slug,
    slot_key_for_action,
)


def build_inflight_nodes(
    ordered_planets: Sequence[PlanetState],
    planets_by_id: Mapping[int, PlanetState],
    inflight_by_pid: Mapping[int, Sequence[object]],
    inflight_unassigned: Sequence[object],
    lane_counts: dict[str, int],
) -> tuple[list[FleetNode], dict[int, list[int]]]:
    nodes_meta: list[FleetNode] = []
    inflight_indices_by_pid: dict[int, list[int]] = {}
    inflight_seq = 0
    for pid in [clean_int(p.planet_id, 0) for p in ordered_planets] + [0]:
        jobs = inflight_by_pid.get(pid, ()) if pid > 0 else inflight_unassigned
        p_obj = planets_by_id.get(pid)
        p_coords = normalize_coords(clean_str(p_obj.coords)) if p_obj is not None else ""
        p_name = clean_str(p_obj.name) if p_obj is not None else "Empire"
        lid = f"lane:planet:{pid}" if pid > 0 else "lane:global"
        for q in jobs:
            idx = len(nodes_meta)
            q_name = clean_str(getattr(q, "name", "")) or "Active Fleet"
            q_detail = clean_str(getattr(q, "detail", ""))
            mission_kind, cat = classify_inflight_mission(q_name, q_detail)
            rem_s = max(0.0, clean_float(getattr(q, "remaining_seconds", 0.0), 0.0))
            rem_txt = clean_str(getattr(q, "remaining_text", "")) or format_eta_short(rem_s)
            lane_counts[lid] = lane_counts.get(lid, 0) + 1
            inflight_indices_by_pid.setdefault(pid, []).append(idx)
            nodes_meta.append(
                {
                    "idx": idx,
                    "id": f"inflight_{inflight_seq}",
                    "order_index": idx,
                    "exec_rank": None,
                    "lane_id": lid,
                    "priority": "P0" if mission_kind == "ESCAPE_FLIGHT" else "P1",
                    "kind": "IN_FLIGHT",
                    "mission_kind": mission_kind,
                    "category": cat,
                    "status": "in_flight",
                    "in_flight": True,
                    "planet_id": pid,
                    "planet_coords": p_coords,
                    "planet_name": p_name,
                    "target_id": None,
                    "target_name": q_name,
                    "target_level": None,
                    "reason": q_detail
                    or f"In-flight {mission_kind.lower().replace('_', ' ')} occupying Fleet base slot",
                    "executable_now": False,
                    "cost": {rk: 0 for rk in RESOURCE_KEYS},
                    "missing": {rk: 0 for rk in RESOURCE_KEYS},
                    "wait_seconds": 0.0,
                    "wait_eta": "In Flight",
                    "duration_seconds": rem_s,
                    "duration_text": rem_txt,
                    "total_seconds": rem_s,
                    "total_eta": rem_txt,
                    "eta_bottleneck": None,
                    "sid": None,
                    "slot_key": None,
                    "sys_key": extract_system_key(q_name) or extract_system_key(q_detail),
                    "target_coords": extract_first_coords(q_name) or extract_first_coords(q_detail),
                }
            )
            inflight_seq += 1
    return nodes_meta, inflight_indices_by_pid


def append_planned_fleet_nodes(
    nodes_meta: list[FleetNode],
    fleet_actions: Sequence[object],
    planets_by_id: Mapping[int, PlanetState],
    lane_counts: dict[str, int],
) -> None:
    exec_counter = 0
    for f_pos, act in enumerate(fleet_actions):
        idx = len(nodes_meta)
        kind = clean_str(getattr(act, "kind", "")).strip().upper() or "ATTACK"
        prio = clean_str(getattr(act, "priority", "P2")).strip().upper()
        prio = prio if prio in ("P0", "P1", "P2", "P3") else "P2"
        pid = clean_int(getattr(act, "planet_id", 0), 0)
        p_obj = planets_by_id.get(pid)
        p_coords = clean_str(getattr(act, "planet_coords", "")) or (
            clean_str(p_obj.coords) if p_obj is not None else ""
        )
        p_name = clean_str(p_obj.name) if p_obj is not None else (f"Colony {pid}" if pid > 0 else "Empire")
        raw_tid = getattr(act, "target_id", None)
        t_name = clean_str(getattr(act, "target_name", ""))
        reason = clean_str(getattr(act, "reason", ""))
        exec_now = bool(getattr(act, "executable_now", False))
        exec_rank: int | None = None
        if exec_now:
            exec_rank = exec_counter
            exec_counter += 1

        cost_d = res_to_dict(getattr(act, "cost", None))
        missing_d = res_to_dict(getattr(act, "missing", None))
        sid, ship_slug = (
            resolve_ship_id_and_slug(raw_tid, t_name)
            if kind in ("BUILD_SHIP", "SPY", "RECYCLE", "COLONIZE")
            else (None, None)
        )
        rkey = resolve_research_key(raw_tid, t_name) if kind == "RESEARCH" else None
        lane_id = "lane:global" if (kind in ("ACTIVATE_TRANSMITTER", "RESEARCH") or pid <= 0) else f"lane:planet:{pid}"
        lane_counts[lane_id] = lane_counts.get(lane_id, 0) + 1

        category = "recon" if kind == "ACTIVATE_TRANSMITTER" else classify_node_category(kind, None, sid)
        bottleneck_raw = getattr(act, "eta_bottleneck", None)
        nodes_meta.append(
            {
                "idx": idx,
                "id": f"fleet_{f_pos}",
                "order_index": idx,
                "exec_rank": exec_rank,
                "lane_id": lane_id,
                "priority": prio,
                "kind": kind,
                "mission_kind": kind,
                "category": category,
                "status": classify_node_status(kind, exec_now, missing_d, reason),
                "in_flight": False,
                "planet_id": pid,
                "planet_coords": p_coords,
                "planet_name": p_name,
                "target_id": clean_optional_int(raw_tid),
                "target_name": t_name,
                "target_level": clean_optional_int(getattr(act, "target_level", None)),
                "reason": reason,
                "executable_now": exec_now,
                "cost": cost_d,
                "missing": missing_d,
                "wait_seconds": clean_optional_float(getattr(act, "wait_seconds", 0.0)),
                "wait_eta": clean_str(getattr(act, "wait_eta", "Ready"), "Ready"),
                "duration_seconds": clean_float(getattr(act, "duration_seconds", 0.0), 0.0),
                "duration_text": clean_str(getattr(act, "duration_text", "Instant"), "Instant"),
                "total_seconds": clean_optional_float(getattr(act, "total_seconds", 0.0)),
                "total_eta": clean_str(getattr(act, "total_eta", "Ready"), "Ready"),
                "eta_bottleneck": clean_str(bottleneck_raw) if bottleneck_raw else None,
                "sid": sid,
                "ship_slug": ship_slug,
                "rkey": rkey,
                "slot_key": slot_key_for_action(kind, pid),
                "sys_key": extract_system_key(t_name) or extract_system_key(reason),
                "target_coords": extract_first_coords(t_name) or extract_first_coords(reason),
            }
        )
