"""Dependency-edge helpers for the Tab 3 AI-Planner DAG."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from backend.core.catalog import BUILDINGS, RESEARCHES, building_energy_delta
from backend.core.model import PlanetState
from backend.planner.graph.common import (
    MINE_RESOURCE_BY_BID,
    RESOURCE_KEYS,
    RESOURCE_LABELS,
    clean_int,
    clean_optional_int,
    clean_str,
    has_path,
)

StepDict = dict[str, object]


def parse_week_filter(raw: object) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 1 else None
    s = clean_str(raw).strip().lower()
    if not s or s in ("all", "next", "window", "*"):
        return None
    if s.startswith("w") and s[1:].isascii() and s[1:].isdigit():
        val = int(s[1:])
        return val if val >= 1 else None
    parsed = clean_optional_int(s)
    return parsed if (parsed is not None and parsed >= 1) else None


def dominant_cost_resource(cost_d: Mapping[str, int], missing_d: Mapping[str, int]) -> str | None:
    best_rk: str | None = None
    best_val = 0
    for rk in RESOURCE_KEYS:
        mv = max(0, clean_int(missing_d.get(rk, 0), 0))
        if mv > best_val:
            best_val, best_rk = mv, rk
    if best_rk is not None:
        return best_rk
    for rk in RESOURCE_KEYS:
        cv = max(0, clean_int(cost_d.get(rk, 0), 0))
        if cv > best_val:
            best_val, best_rk = cv, rk
    return best_rk


def _add_ai_node_edges(
    nodes_meta: Sequence[StepDict],
    v_pos: int,
    researches_map: Mapping[str, int],
    add_edge: object,
) -> None:
    assert callable(add_edge)
    v = nodes_meta[v_pos]
    v_kind, v_lane = str(v["kind"]), str(v["lane_id"])
    v_bid = v["bid"] if isinstance(v["bid"], int) else None
    v_rkey = v["rkey"] if isinstance(v["rkey"], str) else None
    v_tlvl = v["target_level"] if isinstance(v["target_level"], int) else None
    v_pobj = v["planet_obj"] if isinstance(v["planet_obj"], PlanetState) else None
    v_name = str(v["target_name"])

    if v_kind in ("BUILD", "DEMOLISH") and v_bid is not None:
        for u_pos in range(v_pos - 1, -1, -1):
            u = nodes_meta[u_pos]
            if u["kind"] == v_kind and u["lane_id"] == v_lane and u["bid"] == v_bid:
                u_tlvl = u["target_level"] if isinstance(u["target_level"], int) else None
                ok = (u_tlvl is None or v_tlvl is None) or (v_tlvl > u_tlvl if v_kind == "BUILD" else v_tlvl < u_tlvl)
                if ok:
                    prefix = "Demolish " if v_kind == "DEMOLISH" else ""
                    fallback = (
                        f"Sequential {v_name} demolition" if v_kind == "DEMOLISH" else f"Sequential {v_name} upgrade"
                    )
                    lbl = (
                        f"{prefix}{v_name} L{u_tlvl} -> L{v_tlvl}"
                        if (u_tlvl is not None and v_tlvl is not None)
                        else fallback
                    )
                    add_edge(u_pos, v_pos, "level_chain", lbl)
                break
    elif v_kind == "RESEARCH" and v_rkey is not None:
        for u_pos in range(v_pos - 1, -1, -1):
            u = nodes_meta[u_pos]
            if u["kind"] == "RESEARCH" and u["rkey"] == v_rkey:
                u_tlvl = u["target_level"] if isinstance(u["target_level"], int) else None
                if u_tlvl is None or v_tlvl is None or v_tlvl > u_tlvl:
                    lbl = (
                        f"{v_name} L{u_tlvl} -> L{v_tlvl}"
                        if (u_tlvl is not None and v_tlvl is not None)
                        else f"Sequential {v_name} research"
                    )
                    add_edge(u_pos, v_pos, "level_chain", lbl)
                break

    if v_kind == "BUILD" and v_lane.startswith("lane:planet:"):
        for u_pos in range(v_pos - 1, -1, -1):
            u = nodes_meta[u_pos]
            if u["lane_id"] != v_lane:
                continue
            if u["kind"] == "DEMOLISH":
                add_edge(u_pos, v_pos, "prerequisite", f"Demolish {u['target_name']} frees 1 field for {v_name}")
                break
            if u["kind"] == "BUILD":
                break

    reqs = v["unit_reqs"] if isinstance(v["unit_reqs"], Sequence) else ()
    for req in reqs:
        if not isinstance(req, Mapping):
            continue
        req_cat, req_slug, req_lvl = (
            str(req.get("category", "")),
            str(req.get("slug", "")),
            clean_int(req.get("level", 1), 1),
        )
        if req_cat == "buildings":
            req_info = BUILDINGS.get(req_slug)
            req_bid = int(req_info["id"]) if req_info and isinstance(req_info.get("id"), int) else None
            if req_bid is None:
                continue
            cur_b_lvl = v_pobj.building_level(req_bid) if v_pobj is not None else 0
            for u_pos in range(v_pos - 1, -1, -1):
                u = nodes_meta[u_pos]
                if u["kind"] == "BUILD" and u["bid"] == req_bid and (v_kind == "RESEARCH" or u["lane_id"] == v_lane):
                    u_lvl = u["target_level"] if isinstance(u["target_level"], int) else None
                    if cur_b_lvl < req_lvl or (u_lvl is not None and u_lvl <= req_lvl):
                        add_edge(
                            u_pos, v_pos, "prerequisite", f"{u['target_name']} L{u_lvl or req_lvl} unlocks {v_name}"
                        )
                        break
        elif req_cat == "research":
            req_info = RESEARCHES.get(req_slug)
            req_rkey = str(req_info["id"]) if req_info and req_info.get("id") else None
            if req_rkey is None:
                continue
            cur_r_lvl = researches_map.get(req_rkey, 0)
            for u_pos in range(v_pos - 1, -1, -1):
                u = nodes_meta[u_pos]
                if u["kind"] == "RESEARCH" and u["rkey"] == req_rkey:
                    u_lvl = u["target_level"] if isinstance(u["target_level"], int) else None
                    if cur_r_lvl < req_lvl or (u_lvl is not None and u_lvl <= req_lvl):
                        add_edge(
                            u_pos, v_pos, "prerequisite", f"{u['target_name']} L{u_lvl or req_lvl} unlocks {v_name}"
                        )
                        break


def _add_ai_support_and_resource_edges(nodes_meta: Sequence[StepDict], v_pos: int, add_edge: object) -> None:
    assert callable(add_edge)
    v = nodes_meta[v_pos]
    v_kind, v_lane, v_name = str(v["kind"]), str(v["lane_id"]), str(v["target_name"])
    v_bid = v["bid"] if isinstance(v["bid"], int) else None
    v_tlvl = v["target_level"] if isinstance(v["target_level"], int) else None

    if v_kind == "BUILD" and v_bid is not None:
        if building_energy_delta(v_bid, v_tlvl if (v_tlvl is not None and v_tlvl > 0) else 1) < 0:
            for u_pos in range(v_pos - 1, -1, -1):
                u = nodes_meta[u_pos]
                if (u["kind"] == "BUILD" and u["lane_id"] == v_lane and u["bid"] in (5, 6)) or (
                    u["kind"] == "RESEARCH" and u["rkey"] == "r2"
                ):
                    lvl_s = f" L{v_tlvl}" if v_tlvl is not None else ""
                    add_edge(u_pos, v_pos, "energy_support", f"Supplies energy for {v_name}{lvl_s}")
                    break

    if v_kind == "BUILD" and v_bid == 2:
        for u_pos in range(v_pos - 1, -1, -1):
            u = nodes_meta[u_pos]
            if u["kind"] == "BUILD" and u["lane_id"] == v_lane and u["bid"] == 1:
                add_edge(u_pos, v_pos, "resource_feed", "Supplies 2:1 Pig iron feedstock for Blast furnace")
                break

    cost_m = v["cost"] if isinstance(v["cost"], Mapping) else {}
    miss_m = v["missing"] if isinstance(v["missing"], Mapping) else {}
    dom_res = dominant_cost_resource(cost_m, miss_m)
    if dom_res is not None:
        for u_pos in range(v_pos - 1, -1, -1):
            u = nodes_meta[u_pos]
            u_bid = u["bid"] if isinstance(u["bid"], int) else None
            if u["kind"] == "BUILD" and u["lane_id"] == v_lane and u_bid in MINE_RESOURCE_BY_BID:
                if MINE_RESOURCE_BY_BID[u_bid] == dom_res and u_bid != v_bid:
                    add_edge(u_pos, v_pos, "resource_feed", f"Boosts {RESOURCE_LABELS[dom_res]} output for {v_name}")
                    break
            elif u["kind"] == "RESEARCH" and u["rkey"] == "r1" and dom_res in ("pig_iron", "kryptonite", "spice"):
                add_edge(
                    u_pos,
                    v_pos,
                    "resource_feed",
                    f"Mining technology (+2%/lvl) boosts {RESOURCE_LABELS[dom_res]} for {v_name}",
                )
                break


def build_ai_edges(
    nodes_meta: Sequence[StepDict], researches_map: Mapping[str, int]
) -> dict[tuple[int, int], dict[str, str]]:
    """Build guaranteed-acyclic forward edges for windowed AI-Planner steps."""
    n_count = len(nodes_meta)
    edge_acc: dict[tuple[int, int], dict[str, str]] = {}
    adj: dict[int, list[int]] = {i: [] for i in range(n_count)}

    def _add_edge(u_idx: int, v_idx: int, etype: str, label: str) -> None:
        if u_idx == v_idx or not (0 <= u_idx < n_count) or not (0 <= v_idx < n_count) or u_idx > v_idx:
            return
        pair = (u_idx, v_idx)
        if pair not in edge_acc:
            if has_path(adj, v_idx, u_idx):
                return
            edge_acc[pair] = {}
            adj[u_idx].append(v_idx)
        if etype not in edge_acc[pair]:
            edge_acc[pair][etype] = label

    for v_pos in range(1, n_count):
        _add_ai_node_edges(nodes_meta, v_pos, researches_map, _add_edge)
        _add_ai_support_and_resource_edges(nodes_meta, v_pos, _add_edge)

    last_by_slot: dict[str, int] = {}
    for m in nodes_meta:
        skey = str(m["slot_key"])
        cur_idx = clean_int(m["idx"], 0)
        prev_idx = last_by_slot.get(skey)
        if prev_idx is not None and prev_idx < cur_idx:
            q_lbl = (
                "Single global research lab slot"
                if skey == "global:research"
                else f"Colony build queue ({m['planet_coords'] or m['planet_name']})"
            )
            _add_edge(prev_idx, cur_idx, "queue_order", q_lbl)
        last_by_slot[skey] = cur_idx
    return edge_acc
