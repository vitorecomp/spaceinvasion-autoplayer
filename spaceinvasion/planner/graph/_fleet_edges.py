"""Tactical fleet/shipyard action classification, slot budgets, and edge builders for Tab 4."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.combat.flight import normalize_coords
from spaceinvasion.core.catalog import RESEARCH_BY_ID, SHIPS, resolve_unit
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import (
    GameState,
    PlanetState,
    count_in_flight_spy_fleets_for_planet,
    normalize_planet_type,
)
from spaceinvasion.planner.graph.common import (
    clean_int,
    clean_str,
    has_path,
    resolve_research_key,
)

FLEET_PLANNER_KINDS: frozenset[str] = frozenset(
    {
        "ALERT_HOSTILE",
        "ESCAPE_FLIGHT",
        "ATTACK",
        "RECYCLE",
        "SPY",
        "SCAN_GALAXY",
        "TRANSPORT",
        "STATION",
        "COLONIZE",
        "ABANDON_COLONY",
        "BUILD_SHIP",
        "ACTIVATE_TRANSMITTER",
    }
)

FLEET_DISPATCH_KINDS: frozenset[str] = frozenset(
    {"ESCAPE_FLIGHT", "ATTACK", "RECYCLE", "SPY", "TRANSPORT", "STATION", "COLONIZE"}
)

FleetNode = dict[str, object]


def is_fleet_planner_action(act: object) -> bool:
    """Include tactical/shipyard actions plus fleet/ship-unlock `RESEARCH` actions (excluding pure economy `r1`/`r2`)."""
    kind = clean_str(getattr(act, "kind", "")).strip().upper()
    if kind in FLEET_PLANNER_KINDS:
        return True
    if kind == "RESEARCH":
        rkey = resolve_research_key(getattr(act, "target_id", None), clean_str(getattr(act, "target_name", "")))
        if "fleet research" in clean_str(getattr(act, "reason", "")).lower():
            return True
        if rkey is not None and rkey not in ("r1", "r2"):
            return True
    return False


def research_direct_deps(rkey: str | None) -> set[str]:
    if not rkey or rkey not in RESEARCH_BY_ID:
        return set()
    deps: set[str] = set()
    for req in RESEARCH_BY_ID[rkey].get("requirements") or ():
        if not isinstance(req, Mapping) or str(req.get("category") or "").strip().lower() != "research":
            continue
        r_info = resolve_unit("research", str(req.get("slug") or req.get("name") or ""))
        dep_id = str(r_info.get("id") or "") if isinstance(r_info, Mapping) else ""
        if dep_id in RESEARCH_BY_ID:
            deps.add(dep_id)
    return deps


def ship_all_research_deps(ship_slug: str | None) -> set[str]:
    if not ship_slug:
        return set()
    ship_info = SHIPS.get(ship_slug) or resolve_unit("ships", ship_slug)
    if not isinstance(ship_info, Mapping):
        return set()
    deps: set[str] = set()
    stack: list[str] = []
    for req in ship_info.get("requirements") or ():
        if not isinstance(req, Mapping) or str(req.get("category") or "").strip().lower() != "research":
            continue
        r_info = resolve_unit("research", str(req.get("slug") or req.get("name") or ""))
        r_id = str(r_info.get("id") or "") if isinstance(r_info, Mapping) else ""
        if r_id in RESEARCH_BY_ID and r_id not in deps:
            deps.add(r_id)
            stack.append(r_id)
    while stack:
        for dep_id in research_direct_deps(stack.pop()):
            if dep_id not in deps:
                deps.add(dep_id)
                stack.append(dep_id)
    return deps


def classify_inflight_mission(name: str, detail: str) -> tuple[str, str]:
    txt = f"{name} {detail}".lower()
    if any(tok in txt for tok in ("escape", "flucht", "save")):
        return "ESCAPE_FLIGHT", "survival"
    if any(tok in txt for tok in ("attack", "angriff", "raid")):
        return "ATTACK", "combat"
    if any(tok in txt for tok in ("recycl", "trümmer", "truemmer")):
        return "RECYCLE", "recon"
    if any(tok in txt for tok in ("spy", "spio", "probe")):
        return "SPY", "recon"
    if any(tok in txt for tok in ("coloniz", "koloni", "siedel")):
        return "COLONIZE", "recon"
    if any(tok in txt for tok in ("station", "deploy")):
        return "STATION", "recon"
    if any(tok in txt for tok in ("transport", "liefer")):
        return "TRANSPORT", "recon"
    return "IN_FLIGHT", "recon"


def build_slot_budgets(
    state: GameState | None,
    ordered_planets: Sequence[PlanetState],
    inflight_by_pid: Mapping[int, Sequence[object]],
    fleet_actions: Sequence[object],
    config: StrategyConfig,
) -> tuple[list[dict[str, object]], dict[int, dict[str, object]], int, int]:
    """Compute per-planet `1 + b10` Fleet base slot usage and docked ship inventory."""
    planned_fleet_by_pid: dict[int, int] = {}
    planned_shipyard_by_pid: dict[int, int] = {}
    for act in fleet_actions:
        k = clean_str(getattr(act, "kind", "")).strip().upper()
        pid = clean_int(getattr(act, "planet_id", 0), 0)
        if k in FLEET_DISPATCH_KINDS:
            planned_fleet_by_pid[pid] = planned_fleet_by_pid.get(pid, 0) + 1
        elif k == "BUILD_SHIP":
            planned_shipyard_by_pid[pid] = planned_shipyard_by_pid.get(pid, 0) + 1

    slot_budgets_out: list[dict[str, object]] = []
    slot_budget_by_pid: dict[int, dict[str, object]] = {}
    total_used = total_max = 0
    res_spy_cfg = max(0, clean_int(config.reserved_spy_slots, 1))
    spy_res_only = bool(config.spy_reserved_slots_only)

    for p in ordered_planets:
        pid = clean_int(p.planet_id, 0)
        b10_lvl = max(0, p.building_level(10))
        max_slots = 1 + b10_lvl
        used_slots = len(inflight_by_pid.get(pid, ()))
        spy_used = count_in_flight_spy_fleets_for_planet(state, p) if state is not None else 0
        total_used += used_slots
        total_max += max_slots
        ships_clean = {
            clean_str(sk): max(0, clean_int(sv, 0)) for sk, sv in (p.ships or {}).items() if clean_int(sv, 0) > 0
        }
        entry: dict[str, object] = {
            "planet_id": pid,
            "planet_name": clean_str(p.name) or f"Colony {pid}",
            "coords": normalize_coords(clean_str(p.coords)) or clean_str(p.coords),
            "planet_type": normalize_planet_type(p),
            "b10_level": b10_lvl,
            "used_slots": used_slots,
            "spy_used_slots": spy_used,
            "reserved_spy_slots": min(max_slots, res_spy_cfg),
            "spy_reserved_slots_only": spy_res_only,
            "max_slots": max_slots,
            "available_slots": max(0, max_slots - used_slots),
            "in_flight_count": used_slots,
            "planned_fleet_count": planned_fleet_by_pid.get(pid, 0),
            "planned_shipyard_count": planned_shipyard_by_pid.get(pid, 0),
            "docked_ships": ships_clean,
            "total_docked_ships": sum(v for k, v in ships_clean.items() if k != "s9"),
            "solar_cells": ships_clean.get("s9", 0),
        }
        slot_budgets_out.append(entry)
        slot_budget_by_pid[pid] = entry
    return slot_budgets_out, slot_budget_by_pid, total_used, total_max


def _add_fleet_pairwise_edge(u: FleetNode, v: FleetNode, u_pos: int, v_pos: int, add_edge: object) -> None:
    assert callable(add_edge)
    u_kind, v_kind = str(u["kind"]), str(v["kind"])
    u_pid, v_pid = clean_int(u["planet_id"], 0), clean_int(v["planet_id"], 0)
    u_sid = u["sid"] if isinstance(u["sid"], int) else None
    raw_u_rkey = u.get("rkey")
    raw_v_rkey = v.get("rkey")
    raw_v_slug = v.get("ship_slug")
    u_rkey = raw_u_rkey if isinstance(raw_u_rkey, str) else None
    v_rkey = raw_v_rkey if isinstance(raw_v_rkey, str) else None
    v_slug = raw_v_slug if isinstance(raw_v_slug, str) else None
    v_name = str(v["target_name"])

    if u_kind == "RESEARCH" and u_rkey:
        if v_kind == "RESEARCH" and v_rkey and u_rkey in research_direct_deps(v_rkey):
            add_edge(u_pos, v_pos, "prerequisite", f"Prerequisite {u['target_name']} for {v_name}")
        elif v_kind == "BUILD_SHIP" and v_slug and u_rkey in ship_all_research_deps(v_slug):
            add_edge(u_pos, v_pos, "prerequisite", f"Unlocks {v_name} ({u['target_name']})")

    if u_kind == "BUILD_SHIP" and u_pid in (0, v_pid):
        if u_sid == 10 and v_kind == "SPY":
            add_edge(u_pos, v_pos, "prerequisite", "Builds Spy probe (s10) for espionage")
        elif u_sid == 11 and v_kind == "RECYCLE":
            add_edge(u_pos, v_pos, "prerequisite", "Builds Recycler (s11) for debris harvesting")
        elif u_sid == 8 and v_kind == "COLONIZE":
            add_edge(u_pos, v_pos, "prerequisite", "Builds Colony ship (s8) for colonization")
        elif u_sid in (1, 2) and v_kind in ("TRANSPORT", "STATION"):
            add_edge(u_pos, v_pos, "prerequisite", f"Builds {u['target_name']} for logistics")
        elif u_sid not in (None, 8, 9, 10, 11) and v_kind in ("ATTACK", "ESCAPE_FLIGHT") and u_pid == v_pid:
            add_edge(u_pos, v_pos, "prerequisite", f"Supplies {u['target_name']} for {v_kind.lower()}")

    if u_kind == "SCAN_GALAXY" and v_kind in ("SPY", "ATTACK", "RECYCLE", "COLONIZE"):
        if (u["sys_key"] and v["sys_key"] and u["sys_key"] == v["sys_key"]) or (
            (not u["sys_key"] or not v["sys_key"]) and u_pid == v_pid
        ):
            add_edge(u_pos, v_pos, "prerequisite", f"Galaxy scan scouts system for {v_name or v_kind}")

    if u_kind == "SPY" and v_kind in ("ATTACK", "COLONIZE"):
        same_target = bool(u["target_coords"] and v["target_coords"] and u["target_coords"] == v["target_coords"])
        same_sys = bool(u["sys_key"] and v["sys_key"] and u["sys_key"] == v["sys_key"] and v_kind == "COLONIZE")
        if same_target or same_sys:
            add_edge(u_pos, v_pos, "prerequisite", f"Spy intel precedes {v_name or v_kind}")

    if u_kind == "STATION" and v_kind in ("ATTACK", "RECYCLE"):
        dest_c = str(u["target_coords"])
        v_c = normalize_coords(str(v["planet_coords"])) if v["planet_coords"] else ""
        if dest_c and v_c and dest_c == v_c:
            add_edge(u_pos, v_pos, "prerequisite", f"Stations ships at {dest_c} for {v_kind.lower()}")


def _add_fleet_queue_and_exec_edges(nodes_meta: Sequence[FleetNode], add_edge: object) -> None:
    assert callable(add_edge)
    last_by_slot: dict[str, int] = {}
    last_ready_by_lane: dict[str, tuple[int, int]] = {}
    for m in nodes_meta:
        if m["in_flight"]:
            continue
        cur_idx = clean_int(m["idx"], 0)
        skey = m["slot_key"]
        if isinstance(skey, str) and skey:
            prev_idx = last_by_slot.get(skey)
            if prev_idx is not None and prev_idx < cur_idx:
                p_lbl = str(m["planet_coords"]) or str(m["planet_name"])
                q_lbl = (
                    "Global Research lab queue"
                    if skey == "global:research"
                    else (
                        f"Weapons factory queue ({p_lbl})"
                        if skey.endswith(":shipyard")
                        else f"Colony fleet dispatch order ({p_lbl})"
                    )
                )
                add_edge(prev_idx, cur_idx, "queue_order", q_lbl)
            last_by_slot[skey] = cur_idx
        if m["executable_now"] and isinstance(m["exec_rank"], int):
            lid = str(m["lane_id"])
            cur_rank = m["exec_rank"]
            prev_ready = last_ready_by_lane.get(lid)
            if prev_ready is not None and prev_ready[0] < cur_idx:
                add_edge(
                    prev_ready[0],
                    cur_idx,
                    "execution_order",
                    f"Dispatch sequence #{prev_ready[1] + 1} -> #{cur_rank + 1}",
                )
            last_ready_by_lane[lid] = (cur_idx, cur_rank)


def build_fleet_edges(
    nodes_meta: Sequence[FleetNode],
    slot_budget_by_pid: Mapping[int, Mapping[str, object]],
    inflight_indices_by_pid: Mapping[int, Sequence[int]],
) -> dict[tuple[int, int], dict[str, str]]:
    """Build guaranteed-acyclic Fleet & Attack Planner DAG edges."""
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

    p0_escape: dict[int, int] = {}
    for u in nodes_meta:
        if u["kind"] == "ALERT_HOSTILE":
            u_pid = clean_int(u["planet_id"], 0)
            for v in nodes_meta:
                if v["kind"] == "ESCAPE_FLIGHT" and not v["in_flight"] and u_pid in (0, clean_int(v["planet_id"], 0)):
                    _add_edge(
                        clean_int(u["idx"], 0),
                        clean_int(v["idx"], 0),
                        "priority_gate",
                        f"Hostile alert triggers escape flight on {v['planet_coords'] or v['planet_name']}",
                        allow_reverse_index=True,
                    )
        if u["priority"] == "P0" and u["kind"] == "ESCAPE_FLIGHT" and not u["in_flight"]:
            p0_escape.setdefault(clean_int(u["planet_id"], 0), clean_int(u["idx"], 0))

    gated_p0: set[int] = set()
    for v in nodes_meta:
        if v["in_flight"] or v["priority"] == "P0":
            continue
        v_pid = clean_int(v["planet_id"], 0)
        if v_pid in gated_p0:
            continue
        gate_u = p0_escape.get(v_pid)
        if gate_u is not None and gate_u < clean_int(v["idx"], 0):
            _add_edge(
                gate_u,
                clean_int(v["idx"], 0),
                "priority_gate",
                f"P0 escape flight precedes {v['target_name'] or v['kind']}",
            )
            gated_p0.add(v_pid)

    for pid, sb in slot_budget_by_pid.items():
        if clean_int(sb.get("used_slots", 0), 0) >= clean_int(
            sb.get("max_slots", 1), 1
        ) and inflight_indices_by_pid.get(pid):
            earliest_inf = inflight_indices_by_pid[pid][0]
            first_disp = next(
                (
                    v
                    for v in nodes_meta
                    if not v["in_flight"]
                    and clean_int(v["planet_id"], 0) == pid
                    and v["kind"] in FLEET_DISPATCH_KINDS
                    and v["kind"] != "ESCAPE_FLIGHT"
                ),
                None,
            )
            if first_disp is not None:
                _add_edge(
                    earliest_inf,
                    clean_int(first_disp["idx"], 0),
                    "queue_order",
                    f"Awaits free Fleet base (b10 L{sb['b10_level']}) slot on {sb['coords'] or sb['planet_name']}",
                )

    for v_pos in range(n_count):
        v = nodes_meta[v_pos]
        if v["in_flight"]:
            continue
        for u_pos in range(v_pos):
            if not nodes_meta[u_pos]["in_flight"]:
                _add_fleet_pairwise_edge(nodes_meta[u_pos], v, u_pos, v_pos, _add_edge)

    _add_fleet_queue_and_exec_edges(nodes_meta, _add_edge)
    return edge_acc
