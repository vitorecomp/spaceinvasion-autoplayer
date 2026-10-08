"""Decomposed edge-construction helpers for the Tab 1 Heuristic Action Graph DAG."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.core.catalog import (
    BUILDINGS,
    RESEARCHES,
    building_energy_delta,
)
from spaceinvasion.core.state import PlanetState
from spaceinvasion.planner.graph._heuristics_economy import AddEdgeFn, NodeMeta, add_economy_edges
from spaceinvasion.planner.graph.common import (
    clean_int,
    has_path,
)


def _int(val: object, default: int = 0) -> int:
    return val if isinstance(val, int) and not isinstance(val, bool) else clean_int(val, default)


def _opt_int(val: object) -> int | None:
    return val if isinstance(val, int) and not isinstance(val, bool) else None


def _str(val: object) -> str:
    return val if isinstance(val, str) else ""


def _add_priority_gates(nodes_meta: Sequence[NodeMeta], add_edge: AddEdgeFn) -> None:
    p0_escape: dict[int, int] = {}
    p0_alert: dict[int, int] = {}
    for m in nodes_meta:
        if m["priority"] == "P0":
            pid = _int(m["planet_id"])
            if m["kind"] == "ESCAPE_FLIGHT" and pid not in p0_escape:
                p0_escape[pid] = _int(m["idx"])
            elif m["kind"] == "ALERT_HOSTILE" and pid not in p0_alert:
                p0_alert[pid] = _int(m["idx"])

    for u in nodes_meta:
        if u["kind"] == "ALERT_HOSTILE":
            u_pid = _int(u["planet_id"])
            for v in nodes_meta:
                if v["kind"] == "ESCAPE_FLIGHT" and u_pid in (0, _int(v["planet_id"])):
                    add_edge(
                        _int(u["idx"]),
                        _int(v["idx"]),
                        "priority_gate",
                        f"Hostile alert triggers escape flight on {_str(v['planet_coords']) or _str(v['planet_name'])}",
                        allow_reverse_index=True,
                    )

    gated_p0: set[int] = set()
    for v in nodes_meta:
        if v["priority"] == "P0":
            continue
        v_pid = _int(v["planet_id"])
        if v_pid in gated_p0:
            continue
        gate_u = p0_escape.get(v_pid)
        if gate_u is None:
            gate_u = p0_alert.get(v_pid, p0_alert.get(0))
        if gate_u is not None and gate_u < _int(v["idx"]):
            add_edge(gate_u, _int(v["idx"]), "priority_gate", f"P0 survival gate precedes {_str(v['target_name'])}")
            gated_p0.add(v_pid)

    p1_by_planet: dict[int, list[int]] = {}
    for u in nodes_meta:
        if u["priority"] == "P1" and _int(u["planet_id"]) > 0:
            p1_by_planet.setdefault(_int(u["planet_id"]), []).append(_int(u["idx"]))
    for pid, p1_indices in p1_by_planet.items():
        first_p2 = next(
            (
                v
                for v in nodes_meta
                if _int(v["planet_id"]) == pid and v["priority"] == "P2" and _int(v["idx"]) > p1_indices[-1]
            ),
            None,
        )
        if first_p2 is not None:
            for u_idx in p1_indices:
                add_edge(
                    u_idx,
                    _int(first_p2["idx"]),
                    "priority_gate",
                    f"P1 safety recovery precedes {_str(first_p2['target_name'])}",
                )


def _add_level_chain_edge(nodes_meta: Sequence[NodeMeta], v_pos: int, add_edge: AddEdgeFn) -> None:
    v = nodes_meta[v_pos]
    v_idx, v_kind, v_pid = _int(v["idx"]), _str(v["kind"]), _int(v["planet_id"])
    v_bid, v_rkey, v_tlvl = _opt_int(v["bid"]), v["rkey"], _opt_int(v["target_level"])
    v_name = _str(v["target_name"])

    if v_kind in ("BUILD", "WAIT_RESOURCES") and v_bid is not None and v_pid > 0:
        for u_pos in range(v_pos - 1, -1, -1):
            u = nodes_meta[u_pos]
            if u["kind"] in ("BUILD", "WAIT_RESOURCES") and _int(u["planet_id"]) == v_pid and u["bid"] == v_bid:
                u_tlvl = _opt_int(u["target_level"])
                if u_tlvl is None or v_tlvl is None or v_tlvl > u_tlvl:
                    lbl = (
                        f"{v_name} L{u_tlvl} -> L{v_tlvl}"
                        if (u_tlvl is not None and v_tlvl is not None)
                        else f"Sequential {v_name} upgrade"
                    )
                    add_edge(_int(u["idx"]), v_idx, "level_chain", lbl)
                return
    elif v_kind == "DEMOLISH_BUILDING" and v_bid is not None and v_pid > 0:
        for u_pos in range(v_pos - 1, -1, -1):
            u = nodes_meta[u_pos]
            if u["kind"] == "DEMOLISH_BUILDING" and _int(u["planet_id"]) == v_pid and u["bid"] == v_bid:
                u_tlvl = _opt_int(u["target_level"])
                if u_tlvl is None or v_tlvl is None or v_tlvl < u_tlvl:
                    lbl = (
                        f"{v_name} L{u_tlvl} -> L{v_tlvl}"
                        if (u_tlvl is not None and v_tlvl is not None)
                        else f"Sequential {v_name} teardown"
                    )
                    add_edge(_int(u["idx"]), v_idx, "level_chain", lbl)
                return
    elif v_kind == "RESEARCH" and isinstance(v_rkey, str):
        for u_pos in range(v_pos - 1, -1, -1):
            u = nodes_meta[u_pos]
            if u["kind"] == "RESEARCH" and u["rkey"] == v_rkey:
                u_tlvl = _opt_int(u["target_level"])
                if u_tlvl is None or v_tlvl is None or v_tlvl > u_tlvl:
                    lbl = (
                        f"{v_name} L{u_tlvl} -> L{v_tlvl}"
                        if (u_tlvl is not None and v_tlvl is not None)
                        else f"Sequential {v_name} research"
                    )
                    add_edge(_int(u["idx"]), v_idx, "level_chain", lbl)
                return


def _add_tech_prereq_edges(
    nodes_meta: Sequence[NodeMeta],
    v_pos: int,
    researches_map: Mapping[str, int],
    add_edge: AddEdgeFn,
) -> None:
    v = nodes_meta[v_pos]
    v_idx, v_pid, v_name = _int(v["idx"]), _int(v["planet_id"]), _str(v["target_name"])
    v_pobj = v["planet_obj"] if isinstance(v["planet_obj"], PlanetState) else None
    reqs = v["unit_reqs"] if isinstance(v["unit_reqs"], Sequence) else ()
    for req in reqs:
        if not isinstance(req, Mapping):
            continue
        req_cat, req_slug = str(req.get("category", "")), str(req.get("slug", ""))
        req_lvl = clean_int(req.get("level", 1), 1)
        if req_cat == "buildings":
            req_info = BUILDINGS.get(req_slug)
            req_bid = int(req_info["id"]) if req_info and isinstance(req_info.get("id"), int) else None
            if req_bid is None:
                continue
            cur_b_lvl = v_pobj.building_level(req_bid) if v_pobj is not None else 0
            for u_pos in range(v_pos - 1, -1, -1):
                u = nodes_meta[u_pos]
                if u["kind"] in ("BUILD", "WAIT_RESOURCES") and _int(u["planet_id"]) == v_pid and u["bid"] == req_bid:
                    u_lvl = _opt_int(u["target_level"])
                    if cur_b_lvl < req_lvl or (u_lvl is not None and u_lvl <= req_lvl):
                        add_edge(
                            _int(u["idx"]),
                            v_idx,
                            "prerequisite",
                            f"{_str(u['target_name'])} L{u_lvl or req_lvl} unlocks {v_name}",
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
                    u_lvl = _opt_int(u["target_level"])
                    if cur_r_lvl < req_lvl or (u_lvl is not None and u_lvl <= req_lvl):
                        add_edge(
                            _int(u["idx"]),
                            v_idx,
                            "prerequisite",
                            f"{_str(u['target_name'])} L{u_lvl or req_lvl} unlocks {v_name}",
                        )
                        break


def _add_operational_edges(u: NodeMeta, v: NodeMeta, add_edge: AddEdgeFn) -> None:
    u_idx, v_idx = _int(u["idx"]), _int(v["idx"])
    u_kind, v_kind = _str(u["kind"]), _str(v["kind"])
    u_pid, v_pid = _int(u["planet_id"]), _int(v["planet_id"])
    u_bid, v_bid, u_sid = _opt_int(u["bid"]), _opt_int(v["bid"]), _opt_int(u["sid"])
    v_name, u_reason_low = _str(v["target_name"]), _str(u["reason"]).lower()
    v_pobj = v["planet_obj"] if isinstance(v["planet_obj"], PlanetState) else None

    if v_kind == "CLAIM_TUTORIAL" and "tutorial" in u_reason_low and u_kind != "CLAIM_TUTORIAL":
        add_edge(u_idx, v_idx, "prerequisite", f"Completes tutorial step before {v_name}")
    if u_kind == "BUILD_SHIP" and u_sid == 10 and v_kind == "SPY" and u_pid in (0, v_pid):
        add_edge(u_idx, v_idx, "prerequisite", "Builds Spy probe (s10) for espionage")
    if u_kind == "BUILD_SHIP" and u_sid == 11 and v_kind == "RECYCLE" and u_pid in (0, v_pid):
        add_edge(u_idx, v_idx, "prerequisite", "Builds Recycler (s11) for debris harvesting")
    if u_kind == "BUILD_SHIP" and u_sid == 8 and v_kind == "COLONIZE" and u_pid in (0, v_pid):
        add_edge(u_idx, v_idx, "prerequisite", "Builds Colony ship (s8) for colonization")
    if u_kind == "SCAN_GALAXY" and v_kind in ("SPY", "ATTACK", "RECYCLE", "COLONIZE"):
        if (u["sys_key"] and v["sys_key"] and u["sys_key"] == v["sys_key"]) or (
            (not u["sys_key"] or not v["sys_key"]) and u_pid == v_pid
        ):
            add_edge(u_idx, v_idx, "prerequisite", f"Galaxy scan scouts system for {v_name}")
    if u_kind == "SPY" and v_kind == "COLONIZE" and u["sys_key"] and v["sys_key"] and u["sys_key"] == v["sys_key"]:
        add_edge(u_idx, v_idx, "prerequisite", f"Probes free slot fields before {v_name}")
    if (
        u_kind in ("BUILD", "WAIT_RESOURCES")
        and u_bid == 5
        and v_kind == "DEMOLISH_BUILDING"
        and v_bid == 6
        and u_pid == v_pid
    ):
        add_edge(u_idx, v_idx, "energy_support", "Supplies Fusion energy buffer before Thermosolar teardown")
    if (
        u_kind == "DEMOLISH_BUILDING"
        and v_kind in ("BUILD", "WAIT_RESOURCES")
        and u_pid == v_pid
        and (
            (v_pobj is not None and clean_int(v_pobj.fields_remaining, 1) <= 0)
            or "reclaim" in u_reason_low
            or "fields" in u_reason_low
        )
    ):
        add_edge(u_idx, v_idx, "prerequisite", f"Reclaims planetary building field before {v_name}")
    if (
        u_kind == "BUILD_SHIP"
        and u_sid not in (None, 9, 10)
        and v_kind in ("ESCAPE_FLIGHT", "ATTACK")
        and u_pid == v_pid
    ):
        add_edge(
            u_idx, v_idx, "prerequisite", f"Supplies {_str(u['target_name'])} for {v_kind.replace('_', ' ').lower()}"
        )


def build_heuristic_edges(
    nodes_meta: Sequence[NodeMeta],
    researches_map: Mapping[str, int],
    *,
    warn_ratio: float,
) -> dict[tuple[int, int], dict[str, str]]:
    """Build guaranteed-acyclic edge accumulator for the heuristic Action Graph."""
    n_count = len(nodes_meta)
    edge_acc: dict[tuple[int, int], dict[str, str]] = {}
    adj: dict[int, list[int]] = {i: [] for i in range(n_count)}

    def _add_edge(u_idx: int, v_idx: int, etype: str, label: str, *, allow_reverse_index: bool = False) -> None:
        if u_idx == v_idx or not (0 <= u_idx < n_count) or not (0 <= v_idx < n_count):
            return
        if not allow_reverse_index and u_idx > v_idx:
            return
        pair = (u_idx, v_idx)
        if pair not in edge_acc:
            if has_path(adj, v_idx, u_idx):
                return
            edge_acc[pair] = {}
            adj[u_idx].append(v_idx)
        if etype not in edge_acc[pair]:
            edge_acc[pair][etype] = label

    _add_priority_gates(nodes_meta, _add_edge)

    for v_pos in range(1, n_count):
        _add_level_chain_edge(nodes_meta, v_pos, _add_edge)
        _add_tech_prereq_edges(nodes_meta, v_pos, researches_map, _add_edge)
        v = nodes_meta[v_pos]
        v_bid = _opt_int(v["bid"])
        v_tlvl = _opt_int(v["target_level"])
        v_pobj = v["planet_obj"] if isinstance(v["planet_obj"], PlanetState) else None
        v_energy_delta = 0
        if v["kind"] in ("BUILD", "WAIT_RESOURCES") and v_bid is not None:
            eff_lvl = (
                v_tlvl
                if (v_tlvl is not None and v_tlvl > 0)
                else ((v_pobj.building_level(v_bid) + 1) if v_pobj is not None else 1)
            )
            v_energy_delta = building_energy_delta(v_bid, eff_lvl)
        for u_pos in range(v_pos):
            _add_operational_edges(nodes_meta[u_pos], v, _add_edge)
            add_economy_edges(
                nodes_meta[u_pos], v, v_energy_delta=v_energy_delta, warn_ratio=warn_ratio, add_edge=_add_edge
            )

    last_by_slot: dict[str, int] = {}
    last_ready_by_lane: dict[str, tuple[int, int]] = {}
    for m in nodes_meta:
        cur_idx = _int(m["idx"])
        skey = m["slot_key"]
        if isinstance(skey, str) and skey:
            prev_idx = last_by_slot.get(skey)
            if prev_idx is not None and prev_idx < cur_idx:
                p_lbl = _str(m["planet_coords"]) or _str(m["planet_name"])
                if skey == "global:research":
                    q_label = "Single global research lab slot"
                elif skey.endswith(":build"):
                    q_label = f"Colony build queue ({p_lbl})"
                elif skey.endswith(":shipyard"):
                    q_label = f"Weapons factory queue ({p_lbl})"
                else:
                    q_label = f"Colony fleet dispatch order ({p_lbl})"
                _add_edge(prev_idx, cur_idx, "queue_order", q_label)
            last_by_slot[skey] = cur_idx
        if m["executable_now"] and isinstance(m["exec_rank"], int):
            lid = _str(m["lane_id"])
            cur_rank = m["exec_rank"]
            prev_ready = last_ready_by_lane.get(lid)
            if prev_ready is not None and prev_ready[0] < cur_idx:
                _add_edge(
                    prev_ready[0],
                    cur_idx,
                    "execution_order",
                    f"Dispatch sequence #{prev_ready[1] + 1} -> #{cur_rank + 1}",
                )
            last_ready_by_lane[lid] = (cur_idx, cur_rank)

    return edge_acc
