"""Structured building resource requests builder for the Dashboard Building Queue."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re

from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import (
    BUILDING_ID_BY_NAME,
    GameState,
    PlanetState,
    PlannedAction,
    Resources,
    is_in_flight_fleet_job,
)
from spaceinvasion.runtime.bridge._helpers import safe_api_float, safe_api_int
from spaceinvasion.runtime.bridge._helpers_queue import extract_coords_from_text, parse_building_target_hint

_STATUS_ORDER: dict[str, int] = {"in_flight": 0, "planned": 1, "requested": 2}
_BuildingHint = tuple[str, int | None, int | None, PlannedAction]


def _make_request_item(
    *,
    item_id: str,
    dest: PlanetState,
    target_building: str,
    target_level: int | None,
    building_id: int | None,
    label: str,
    status: str,
    pig: int,
    met: int,
    kry: int,
    spi: int,
    src: PlanetState | None = None,
    source_coords: str = "",
    eta_seconds: float | None = None,
    finish_ts: float | None = None,
    reason: str = "",
) -> dict[str, object]:
    src_pid = int(src.planet_id) if src is not None else None
    src_coords = normalize_coords(source_coords) or (str(src.coords) if src is not None else "")
    src_name = str(src.name) if src is not None else ""
    eta_clean = (
        round(float(eta_seconds), 1) if (eta_seconds is not None and math.isfinite(float(eta_seconds))) else None
    )
    fin_clean = (
        round(float(finish_ts), 3)
        if (finish_ts is not None and math.isfinite(float(finish_ts)) and float(finish_ts) > 0.0)
        else None
    )
    return {
        "id": item_id,
        "planet_id": int(dest.planet_id),
        "planet_coords": str(dest.coords),
        "planet_name": str(dest.name or dest.coords),
        "target_building": target_building,
        "target_level": int(target_level) if target_level is not None else None,
        "building_id": int(building_id) if building_id is not None else None,
        "label": label,
        "status": status,
        "resources": {"pig_iron": pig, "metal": met, "kryptonite": kry, "spice": spi},
        "pig_iron": pig,
        "metal": met,
        "kryptonite": kry,
        "spice": spi,
        "source_planet_id": src_pid,
        "source_coords": src_coords,
        "source_name": src_name,
        "eta_seconds": eta_clean,
        "finish_ts": fin_clean,
        "reason": str(reason or ""),
    }


def _collect_in_flight_requests(
    display_state: GameState,
    planet_by_coords: Mapping[str, PlanetState],
    early_map: Mapping[int, Mapping[str, object]],
    next_bldg_by_pid: Mapping[int, _BuildingHint],
) -> tuple[list[dict[str, object]], dict[int, list[int]]]:
    items: list[dict[str, object]] = []
    in_flight_by_pid: dict[int, list[int]] = {}
    for idx, q in enumerate(display_state.active_queues):
        if not is_in_flight_fleet_job(q):
            continue
        name_s = str(q.name or "")
        det_s = str(q.detail or "")
        name_l = name_s.lower()
        det_l = det_s.lower()
        if ("returning" in det_l) or ("↩" in name_s) or ("rück" in det_l) or ("rueck" in det_l):
            continue
        if not (name_l.startswith("transport") or "transport →" in name_l or "transport ->" in name_l):
            continue
        dest_coords = extract_coords_from_text(name_s)
        dest = planet_by_coords.get(dest_coords) if dest_coords else None
        if dest is None:
            continue
        res = q.expected_cargo or q.cargo
        if not isinstance(res, Resources) or res.is_zero():
            continue
        orig_coords = normalize_coords(str(q.origin_coords or ""))
        src = planet_by_coords.get(orig_coords) if orig_coords else None
        pid = int(dest.planet_id)
        buf = in_flight_by_pid.setdefault(pid, [0, 0, 0, 0])
        buf[0] += res.pig_iron
        buf[1] += res.metal
        buf[2] += res.kryptonite
        buf[3] += res.spice

        tb, tl, bid = "", None, None
        lbl = "Incoming transport"
        early_ent = early_map.get(pid)
        if isinstance(early_ent, Mapping) and early_ent.get("label"):
            tb, tl, bid = parse_building_target_hint(str(early_ent.get("label")))
            lbl = f"{tb} L{tl}" if tb and tl else (tb or str(early_ent.get("label"))[:64])
        elif pid in next_bldg_by_pid:
            tb, tl, bid, _ = next_bldg_by_pid[pid]
            lbl = f"{tb} L{tl}" if tb and tl else (tb or lbl)

        items.append(
            _make_request_item(
                item_id=f"inflight:{orig_coords or 'src'}->{dest_coords}:{int(q.finish_ts or 0)}:{idx}",
                dest=dest,
                target_building=tb,
                target_level=tl,
                building_id=bid,
                label=lbl,
                status="in_flight",
                pig=res.pig_iron,
                met=res.metal,
                kry=res.kryptonite,
                spi=res.spice,
                src=src,
                source_coords=orig_coords,
                eta_seconds=q.remaining_seconds,
                finish_ts=q.finish_ts,
                reason=det_s or "Transport in flight",
            )
        )
    return items, in_flight_by_pid


def _collect_planned_requests(
    actions: Sequence[PlannedAction],
    planet_by_id: Mapping[int, PlanetState],
    planet_by_coords: Mapping[str, PlanetState],
    early_map: Mapping[int, Mapping[str, object]],
    next_bldg_by_pid: Mapping[int, _BuildingHint],
) -> tuple[list[dict[str, object]], dict[int, list[int]]]:
    items: list[dict[str, object]] = []
    planned_by_pid: dict[int, list[int]] = {}
    for idx, act in enumerate(actions):
        if act.kind != "TRANSPORT" or act.cost.is_zero():
            continue
        dest = planet_by_id.get(safe_api_int(act.target_id, 0))
        if dest is None:
            dest_coords = extract_coords_from_text(act.target_name)
            dest = planet_by_coords.get(dest_coords) if dest_coords else None
        if dest is None:
            continue
        src = planet_by_id.get(safe_api_int(act.planet_id, 0))
        pid = int(dest.planet_id)
        res = act.cost
        buf = planned_by_pid.setdefault(pid, [0, 0, 0, 0])
        buf[0] += res.pig_iron
        buf[1] += res.metal
        buf[2] += res.kryptonite
        buf[3] += res.spice

        tb, tl, bid = "", None, None
        rsn = str(act.reason or "")
        m_for = re.search(r"\bfor\s+(.+?)\s*$", rsn)
        hint_str = m_for.group(1).strip() if m_for else ""
        if hint_str:
            tb, tl, bid = parse_building_target_hint(hint_str)
        if not tb and pid in early_map and early_map[pid].get("label"):
            tb, tl, bid = parse_building_target_hint(str(early_map[pid].get("label")))
        if not tb and pid in next_bldg_by_pid:
            tb, tl, bid, _ = next_bldg_by_pid[pid]
        lbl = f"{tb} L{tl}" if tb and tl else (tb or hint_str[:64] or "Scheduled transport")
        eta_s = (
            act.duration_seconds
            if (act.duration_seconds is not None and act.duration_seconds > 0)
            else act.wait_seconds
        )
        items.append(
            _make_request_item(
                item_id=f"planned:{act.planet_id}->{pid}:{idx}",
                dest=dest,
                target_building=tb,
                target_level=tl,
                building_id=bid,
                label=lbl,
                status="planned",
                pig=res.pig_iron,
                met=res.metal,
                kry=res.kryptonite,
                spi=res.spice,
                src=src,
                source_coords=str(act.planet_coords or ""),
                eta_seconds=eta_s,
                reason=rsn or "Planned inter-planet transport",
            )
        )
    return items, planned_by_pid


def _collect_pending_requests(
    display_state: GameState,
    planet_by_id: Mapping[int, PlanetState],
    early_map: Mapping[int, Mapping[str, object]],
    next_bldg_by_pid: Mapping[int, _BuildingHint],
    in_flight_by_pid: Mapping[int, list[int]],
    planned_by_pid: Mapping[int, list[int]],
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    requested_pids: set[int] = set()
    for raw_pid, entry in early_map.items():
        if not isinstance(entry, Mapping):
            continue
        pid = safe_api_int(raw_pid, 0)
        dest = planet_by_id.get(pid)
        if dest is None:
            continue
        planned_buf = planned_by_pid.get(pid, [0, 0, 0, 0])
        pig = max(0, safe_api_int(entry.get("pig_iron", 0), 0) - planned_buf[0])
        met = max(0, safe_api_int(entry.get("metal", 0), 0) - planned_buf[1])
        kry = max(0, safe_api_int(entry.get("kryptonite", 0), 0) - planned_buf[2])
        spi = max(0, safe_api_int(entry.get("spice", 0), 0) - planned_buf[3])
        requested_pids.add(pid)
        if pig + met + kry + spi <= 0:
            continue
        raw_lbl = str(entry.get("label") or "").strip()
        tb, tl, bid = parse_building_target_hint(raw_lbl)
        if not tb and pid in next_bldg_by_pid:
            tb, tl, bid, _ = next_bldg_by_pid[pid]
        lbl = f"{tb} L{tl}" if tb and tl else (raw_lbl[:64] or "Resource request")
        need_by = safe_api_float(entry.get("need_by_sec"), 0.0)
        items.append(
            _make_request_item(
                item_id=f"req:{pid}:{bid or 'b'}:{tl or 0}",
                dest=dest,
                target_building=tb,
                target_level=tl,
                building_id=bid,
                label=lbl,
                status="requested",
                pig=pig,
                met=met,
                kry=kry,
                spi=spi,
                eta_seconds=need_by if need_by > 0.0 else None,
                reason=raw_lbl or "AI-Planner early resource request",
            )
        )

    if len(display_state.planets) >= 2:
        for pid, (tb, tl, bid, act) in next_bldg_by_pid.items():
            if pid in requested_pids or act.executable_now or act.missing.is_zero():
                continue
            dest = planet_by_id.get(pid)
            if dest is None:
                continue
            inb = in_flight_by_pid.get(pid, [0, 0, 0, 0])
            pln = planned_by_pid.get(pid, [0, 0, 0, 0])
            pig = max(0, act.missing.pig_iron - inb[0] - pln[0])
            met = max(0, act.missing.metal - inb[1] - pln[1])
            kry = max(0, act.missing.kryptonite - inb[2] - pln[2])
            spi = max(0, act.missing.spice - inb[3] - pln[3])
            if pig + met + kry + spi <= 0:
                continue
            items.append(
                _make_request_item(
                    item_id=f"req:{pid}:{bid or 'b'}:{tl or 0}",
                    dest=dest,
                    target_building=tb,
                    target_level=tl,
                    building_id=bid,
                    label=f"{tb} L{tl}" if tb and tl else (tb or "Building resource request"),
                    status="requested",
                    pig=pig,
                    met=met,
                    kry=kry,
                    spi=spi,
                    eta_seconds=act.wait_seconds,
                    reason=str(act.reason or "Waiting for building resources"),
                )
            )
    return items


def build_building_resource_requests(
    display_state: GameState | None,
    actions: Sequence[PlannedAction],
    early_needs_map: Mapping[int, Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Build structured resource requests (`in_flight`, `planned`, `requested`) for the Dashboard Building Queue."""
    if display_state is None or not display_state.planets:
        return []
    planet_by_id = {int(p.planet_id): p for p in display_state.planets}
    planet_by_coords = {nc: p for p in display_state.planets if (nc := normalize_coords(p.coords))}
    next_bldg_by_pid: dict[int, _BuildingHint] = {}
    for act in actions:
        if act.kind not in ("BUILD", "WAIT_RESOURCES"):
            continue
        pid = safe_api_int(act.planet_id, 0)
        if pid <= 0:
            continue
        is_stalled = (not act.executable_now) and (not act.missing.is_zero())
        prev = next_bldg_by_pid.get(pid)
        if prev is None or (is_stalled and prev[3].executable_now):
            tname = str(act.target_name or "")
            bid = act.target_id if act.target_id is not None else BUILDING_ID_BY_NAME.get(tname.lower())
            next_bldg_by_pid[pid] = (tname, act.target_level, bid, act)

    early_map: Mapping[int, Mapping[str, object]] = early_needs_map if isinstance(early_needs_map, Mapping) else {}
    in_flight_items, in_flight_by_pid = _collect_in_flight_requests(
        display_state, planet_by_coords, early_map, next_bldg_by_pid
    )
    planned_items, planned_by_pid = _collect_planned_requests(
        actions, planet_by_id, planet_by_coords, early_map, next_bldg_by_pid
    )
    pending_items = _collect_pending_requests(
        display_state, planet_by_id, early_map, next_bldg_by_pid, in_flight_by_pid, planned_by_pid
    )
    items = [*in_flight_items, *planned_items, *pending_items]

    def _sort_key(it: dict[str, object]) -> tuple[int, float, str, str]:
        eta_v = it.get("eta_seconds")
        eta_num = safe_api_float(eta_v, float("inf")) if eta_v is not None else float("inf")
        return (
            _STATUS_ORDER.get(str(it.get("status") or ""), 9),
            eta_num,
            str(it.get("planet_coords") or ""),
            str(it.get("label") or ""),
        )

    items.sort(key=_sort_key)
    return items
