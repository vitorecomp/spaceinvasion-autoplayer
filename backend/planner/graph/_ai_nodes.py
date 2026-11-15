"""Step normalization and node metadata builders for the Tab 3 AI-Planner DAG."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from backend.combat.flight import normalize_coords
from backend.core.catalog import BUILDING_BY_ID, RESEARCH_BY_ID
from backend.core.model import GameState, PlanetState
from backend.planner.graph._ai_edges import StepDict, dominant_cost_resource
from backend.planner.graph.common import (
    RESOURCE_KEYS,
    classify_node_category,
    clean_float,
    clean_int,
    clean_optional_float,
    clean_optional_int,
    clean_str,
    format_eta_short,
    res_to_dict,
    resolve_building_id,
    resolve_research_key,
)


def _step_seconds(step: Mapping[str, object], sec_key: str, hr_key: str, default: float = 0.0) -> float:
    raw = step.get(sec_key)
    if raw is None and step.get(hr_key) is not None:
        return max(0.0, clean_float(step.get(hr_key), 0.0) * 3600.0)
    return max(0.0, clean_float(raw, default))


def _resolve_ai_entity(
    kind: str,
    step: Mapping[str, object],
    p_obj: PlanetState | None,
    researches_map: Mapping[str, int],
    planet_label: str,
) -> tuple[str, str, int | None, int | None, str | None, bool]:
    entity_key = clean_str(step.get("entity_key") or step.get("target_id") or "").strip().lower()
    entity_name = clean_str(step.get("entity_name") or step.get("target_name") or "").strip()
    target_level = clean_optional_int(step.get("target_level"))
    bid: int | None = None
    rkey: str | None = None

    if kind in ("BUILD", "DEMOLISH"):
        bid = resolve_building_id(entity_key, entity_name)
        if bid is not None:
            entity_key = entity_key or f"b{bid}"
            if not entity_name:
                b_info = BUILDING_BY_ID.get(bid)
                entity_name = clean_str(b_info.get("name") if b_info else f"Building {bid}")
    elif kind == "RESEARCH":
        rkey = resolve_research_key(entity_key, entity_name)
        if rkey is not None:
            entity_key = entity_key or rkey
            if not entity_name:
                r_info = RESEARCH_BY_ID.get(rkey)
                entity_name = clean_str(r_info.get("name") if r_info else rkey.upper())
    elif not entity_name:
        entity_name = f"Colony Preview ({planet_label})"

    is_completed = False
    if kind == "BUILD" and p_obj is not None and bid is not None and target_level is not None and target_level > 0:
        is_completed = p_obj.building_level(bid) >= target_level
    elif (
        kind == "DEMOLISH" and p_obj is not None and bid is not None and target_level is not None and target_level >= 0
    ):
        is_completed = p_obj.building_level(bid) <= target_level
    elif kind == "RESEARCH" and rkey is not None and target_level is not None and target_level > 0:
        is_completed = researches_map.get(rkey, 0) >= target_level
    return entity_key, entity_name, target_level, bid, rkey, is_completed


def _resolve_ai_step_planet(
    step: Mapping[str, object],
    planets_by_id: Mapping[int, PlanetState],
    planets_by_coords: Mapping[str, PlanetState],
    planets_by_name: Mapping[str, PlanetState],
) -> tuple[int, str, str, int | None, PlanetState | None]:
    s_pid = clean_int(step.get("planet_id"), 0)
    s_coords_raw = clean_str(step.get("planet_coords") or step.get("coords"))
    s_coords = normalize_coords(s_coords_raw) if s_coords_raw else ""
    s_pname = clean_str(step.get("planet_name"))
    s_slot = clean_optional_int(step.get("planet_slot", step.get("slot")))

    p_obj = planets_by_id.get(s_pid) if s_pid > 0 else None
    if p_obj is None and s_coords:
        p_obj = planets_by_coords.get(s_coords)
    if p_obj is None and s_pname:
        p_obj = planets_by_name.get(s_pname.lower())
    if p_obj is not None:
        s_pid = s_pid if s_pid > 0 else clean_int(p_obj.planet_id, 0)
        s_coords = s_coords or normalize_coords(clean_str(p_obj.coords))
        s_pname = s_pname or clean_str(p_obj.name)
    return s_pid, s_coords, s_pname, s_slot, p_obj


def normalize_ai_step(
    raw_pos: int,
    step: Mapping[str, object],
    *,
    state: GameState | None,
    planets_by_id: Mapping[int, PlanetState],
    planets_by_coords: Mapping[str, PlanetState],
    planets_by_name: Mapping[str, PlanetState],
    ordered_planets: Sequence[PlanetState],
    researches_map: Mapping[str, int],
) -> StepDict:
    """Normalize one raw AI-Planner step and classify live completion status."""
    seq_num = clean_int(step.get("seq"), raw_pos + 1)
    kind = clean_str(step.get("kind"), "BUILD").strip().upper() or "BUILD"
    kind = (
        "DEMOLISH"
        if kind == "DEMOLISH_BUILDING"
        else (kind if kind in ("BUILD", "DEMOLISH", "RESEARCH", "COLONIZE_PREVIEW") else "BUILD")
    )

    start_sec = _step_seconds(step, "start_sec", "start_hours", 0.0)
    dur_sec = _step_seconds(step, "duration_sec", "duration_hours", 0.0)
    finish_sec = max(start_sec + dur_sec, _step_seconds(step, "finish_sec", "finish_hours", start_sec + dur_sec))
    week_raw = clean_optional_int(step.get("week"))
    week_num = week_raw if (week_raw is not None and week_raw >= 1) else (int(start_sec // (7.0 * 86400.0)) + 1)
    s_pid, s_coords, s_pname, s_slot, p_obj = _resolve_ai_step_planet(
        step, planets_by_id, planets_by_coords, planets_by_name
    )

    entity_key, entity_name, target_level, bid, rkey, is_completed = _resolve_ai_entity(
        kind, step, p_obj, researches_map, s_pname or s_coords or f"Slot {s_slot or 0}"
    )

    cost_d = res_to_dict(step.get("cost"))
    missing_d = {rk: 0 for rk in RESOURCE_KEYS}
    if not is_completed and p_obj is not None:
        p_res = res_to_dict(p_obj.resources)
        missing_d = {rk: max(0, cost_d[rk] - p_res[rk]) for rk in RESOURCE_KEYS}
    elif not is_completed and kind == "RESEARCH" and state is not None and ordered_planets:
        lab_p = planets_by_id.get(clean_int(state.active_planet_id, 0)) or ordered_planets[0]
        p_res = res_to_dict(lab_p.resources)
        missing_d = {rk: max(0, cost_d[rk] - p_res[rk]) for rk in RESOURCE_KEYS}

    if kind == "RESEARCH":
        lane_id = "lane:global"
    elif s_pid > 0:
        lane_id = f"lane:planet:{s_pid}"
    elif s_coords:
        lane_id = f"lane:coords:{s_coords}"
    elif s_slot is not None and s_slot > 0:
        lane_id = f"lane:slot:{s_slot}"
    else:
        lane_id = "lane:global"

    raw_tr = step.get("transport_sources")
    tr_sources = (
        [dict(src) for src in raw_tr if isinstance(src, Mapping)]
        if isinstance(raw_tr, Sequence) and not isinstance(raw_tr, (str, bytes))
        else []
    )
    return {
        "seq": seq_num,
        "kind": kind,
        "week": week_num,
        "start_sec": start_sec,
        "duration_sec": dur_sec,
        "finish_sec": finish_sec,
        "planet_id": s_pid,
        "planet_coords": s_coords,
        "planet_name": s_pname
        or (f"Slot {s_slot}" if s_slot else ("Empire" if kind == "RESEARCH" else f"Colony {s_pid}")),
        "planet_slot": s_slot,
        "planet_obj": p_obj,
        "entity_key": entity_key,
        "entity_name": entity_name or kind,
        "target_level": target_level,
        "bid": bid,
        "rkey": rkey,
        "cost": cost_d,
        "missing": missing_d,
        "completed": is_completed,
        "lane_id": lane_id,
        "needs_transport": bool(step.get("needs_transport", False)),
        "transport_request_at_sec": clean_optional_float(step.get("transport_request_at_sec")),
        "transport_request_text": clean_str(step.get("transport_request_text")),
        "transport_lead_sec": clean_float(step.get("transport_lead_sec"), 0.0),
        "transport_travel_sec": clean_float(step.get("transport_travel_sec"), 0.0),
        "transport_dispatch_delay_sec": clean_float(step.get("transport_dispatch_delay_sec"), 0.0),
        "transport_sources": tr_sources,
    }


def _build_single_ai_node(idx: int, cand_id: str, st: StepDict, is_first: bool, slot_key: str) -> StepDict:
    kind = str(st["kind"])
    bid = st["bid"] if isinstance(st["bid"], int) else None
    rkey = st["rkey"] if isinstance(st["rkey"], str) else None
    completed = bool(st["completed"])
    missing_m = st["missing"] if isinstance(st["missing"], Mapping) else {}
    cost_m = st["cost"] if isinstance(st["cost"], Mapping) else {}
    has_missing = any(clean_int(v, 0) > 0 for v in missing_m.values())
    p_obj = st["planet_obj"] if isinstance(st["planet_obj"], PlanetState) else None
    p_full_now = bool(p_obj is not None and p_obj.fields_max > 0 and p_obj.fields_used >= p_obj.fields_max)

    if completed:
        status, exec_now, prio = "completed", False, "P3"
    elif kind == "COLONIZE_PREVIEW":
        status, exec_now, prio = "blocked_gate", False, "P2"
    elif kind == "DEMOLISH":
        status, exec_now, prio = ("ready", True, "P1") if (is_first and p_full_now) else ("queued", False, "P2")
    elif is_first and not has_missing:
        status, exec_now, prio = "ready", True, "P1"
    elif is_first and has_missing:
        status, exec_now, prio = "waiting_resources", False, "P1"
    else:
        status, exec_now, prio = "queued", False, "P2"

    unit_reqs: list[object] = []
    if kind == "BUILD" and bid is not None and bid in BUILDING_BY_ID:
        unit_reqs = list(BUILDING_BY_ID[bid].get("requirements", ()))
    elif kind == "RESEARCH" and rkey is not None and rkey in RESEARCH_BY_ID:
        unit_reqs = list(RESEARCH_BY_ID[rkey].get("requirements", ()))

    start_s = st["start_sec"] if isinstance(st["start_sec"], float) else clean_float(st["start_sec"], 0.0)
    dur_s = st["duration_sec"] if isinstance(st["duration_sec"], float) else clean_float(st["duration_sec"], 0.0)
    fin_s = st["finish_sec"] if isinstance(st["finish_sec"], float) else clean_float(st["finish_sec"], 0.0)
    wait_eta = "Done" if completed else format_eta_short(start_s)
    dur_txt = format_eta_short(dur_s) if dur_s > 0.5 else "Instant"
    tot_eta = "Done" if completed else format_eta_short(fin_s)
    bneck = dominant_cost_resource(cost_m, missing_m) if has_missing else None

    ship_suffix = ""
    if not completed and st["needs_transport"]:
        tr_at = st["transport_request_at_sec"]
        req_txt = str(st["transport_request_text"]) or (
            format_eta_short(tr_at) if isinstance(tr_at, (int, float)) else ""
        )
        ship_suffix = f" · Ship at {req_txt}" if req_txt else " · Transport needed"
    verb = "Demolish" if kind == "DEMOLISH" else "Build"
    reason_txt = (
        f"Week {st['week']} · Step #{st['seq']} · Completed in live state"
        if completed
        else f"Week {st['week']} · Step #{st['seq']} · Start {wait_eta} · {verb} {dur_txt}{ship_suffix}"
    )

    cat_kind = "DEMOLISH_BUILDING" if kind == "DEMOLISH" else ("COLONIZE" if kind == "COLONIZE_PREVIEW" else kind)
    return {
        "idx": idx,
        "id": cand_id,
        "step_seq": st["seq"],
        "week": st["week"],
        "order_index": idx,
        "lane_id": st["lane_id"],
        "slot_key": slot_key,
        "priority": prio,
        "kind": kind,
        "category": classify_node_category(cat_kind, bid, None),
        "status": status,
        "planet_id": st["planet_id"],
        "planet_coords": st["planet_coords"],
        "planet_name": st["planet_name"],
        "planet_obj": p_obj,
        "target_id": bid if bid is not None else (int(rkey[1:]) if (rkey and rkey[1:].isdigit()) else None),
        "target_name": st["entity_name"],
        "target_level": st["target_level"],
        "entity_key": st["entity_key"],
        "reason": reason_txt,
        "executable_now": exec_now,
        "completed": completed,
        "cost": cost_m,
        "missing": missing_m,
        "wait_seconds": start_s,
        "wait_eta": wait_eta,
        "duration_seconds": dur_s,
        "duration_text": dur_txt,
        "total_seconds": fin_s,
        "total_eta": tot_eta,
        "eta_bottleneck": bneck,
        "needs_transport": st["needs_transport"],
        "transport_request_at_sec": st["transport_request_at_sec"],
        "transport_request_text": st["transport_request_text"],
        "transport_lead_sec": st["transport_lead_sec"],
        "transport_travel_sec": st["transport_travel_sec"],
        "transport_dispatch_delay_sec": st["transport_dispatch_delay_sec"],
        "transport_sources": st["transport_sources"],
        "bid": bid,
        "rkey": rkey,
        "unit_reqs": unit_reqs,
    }


def build_ai_nodes_meta(
    windowed_steps: Sequence[StepDict],
    ordered_planets: Sequence[PlanetState],
    *,
    raw_pfilter: str,
    pfilter_global_only: bool,
    pfilter_pid: int | None,
    pfilter_coords: str,
    pfilter_name_low: str,
) -> tuple[list[StepDict], dict[str, int]]:
    lane_counts: dict[str, int] = {"lane:global": 0}
    for p in ordered_planets:
        pid = clean_int(p.planet_id, 0)
        if not raw_pfilter or (
            not pfilter_global_only
            and (
                (pfilter_pid is not None and pid == pfilter_pid)
                or (pfilter_coords and normalize_coords(clean_str(p.coords)) == pfilter_coords)
                or (pfilter_name_low and clean_str(p.name).lower() == pfilter_name_low)
            )
        ):
            lane_counts[f"lane:planet:{pid}"] = 0

    seen_slot: set[str] = set()
    used_ids: set[str] = set()
    nodes_meta: list[StepDict] = []
    for idx, st in enumerate(windowed_steps):
        cand_id = f"ai_{st['seq']}"
        if cand_id in used_ids:
            cand_id = f"ai_{st['seq']}_{idx}"
        used_ids.add(cand_id)
        kind = str(st["kind"])
        lane_id = str(st["lane_id"])
        lane_counts[lane_id] = lane_counts.get(lane_id, 0) + 1
        slot_key = (
            "global:research"
            if kind == "RESEARCH"
            else (f"{lane_id}:build" if kind in ("BUILD", "DEMOLISH") else f"{lane_id}:colonize")
        )
        is_first = False
        if not bool(st["completed"]) and slot_key not in seen_slot:
            is_first = True
            seen_slot.add(slot_key)
        nodes_meta.append(_build_single_ai_node(idx, cand_id, st, is_first, slot_key))
    return nodes_meta, lane_counts
