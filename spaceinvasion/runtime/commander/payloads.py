"""Action deserialization, `/command` payload builders, and busy-slot holding."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import re
from typing import TypeVar

from spaceinvasion.core.model import PlannedAction, Resources
from spaceinvasion.runtime.commander._types import FLEET_ACTION_KINDS, PlannedActionCmdLike
from spaceinvasion.runtime.commander.errors import (
    safe_cmd_int,
    safe_cmd_opt_int,
    safe_cmd_str,
    safe_finite_float,
)

_TAction = TypeVar("_TAction", bound=PlannedActionCmdLike)

_COORD_TRIPLE_RE = re.compile(r"(\d{1,2}:\d{1,3}:\d{1,2})", re.ASCII)
_GAL_SYS_RE = re.compile(r"(\d{1,2}):(\d{1,3})", re.ASCII)
_STATION_SHIP_TOKEN_RE = re.compile(r"\b(\d{1,9})\s*x\s*(s\d{1,3})\b", re.IGNORECASE | re.ASCII)
_INT_KEY_RE = re.compile(r"-?\d{1,18}", re.ASCII)
_VALID_STATION_SHIP_KEYS: frozenset[str] = frozenset({f"s{i}" for i in range(1, 17)} - {"s9"})
_PRIO_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def parse_station_ships_spec(text: str) -> dict[str, int]:
    """Extract ``{ship_key: qty}`` pairs from a formatted ``STATION`` label (e.g. ``[15x s11, 6x s4]``)."""
    out: dict[str, int] = {}
    for match in _STATION_SHIP_TOKEN_RE.finditer(str(text or "")):
        qty = max(0, safe_cmd_int(match.group(1), 0))
        skey = match.group(2).lower()
        if qty > 0 and skey in _VALID_STATION_SHIP_KEYS:
            out[skey] = out.get(skey, 0) + qty
    return out


def _coerce_resources(val: object) -> Resources:
    if isinstance(val, Resources):
        return val
    if not isinstance(val, Mapping):
        return Resources()
    return Resources(
        pig_iron=max(0, safe_cmd_int(val.get("pig_iron", 0), 0)),
        metal=max(0, safe_cmd_int(val.get("metal", 0), 0)),
        kryptonite=max(0, safe_cmd_int(val.get("kryptonite", 0), 0)),
        spice=max(0, safe_cmd_int(val.get("spice", 0), 0)),
    )


def planned_action_from_dict(raw: object) -> PlannedAction | None:
    """Deserialize an API action dictionary into a canonical ``PlannedAction``."""
    if not isinstance(raw, Mapping):
        return None
    kind = safe_cmd_str(raw.get("kind"), "").strip().upper()
    if not kind:
        return None
    prio = safe_cmd_str(raw.get("priority"), "P2").strip().upper()
    if prio not in _PRIO_RANK:
        prio = "P2"
    raw_href = raw.get("action_href")
    raw_bn = raw.get("eta_bottleneck")
    return PlannedAction(
        priority=prio,
        kind=kind,
        planet_id=safe_cmd_int(raw.get("planet_id", 0), 0),
        planet_coords=safe_cmd_str(raw.get("planet_coords"), ""),
        target_name=safe_cmd_str(raw.get("target_name"), kind),
        reason=safe_cmd_str(raw.get("reason"), ""),
        target_id=safe_cmd_opt_int(raw.get("target_id")),
        target_level=safe_cmd_opt_int(raw.get("target_level")),
        cost=_coerce_resources(raw.get("cost")),
        missing=_coerce_resources(raw.get("missing")),
        executable_now=bool(raw.get("executable_now", False)),
        action_href=safe_cmd_str(raw_href) if raw_href is not None else None,
        wait_hours=safe_finite_float(raw.get("wait_hours")),
        wait_seconds=safe_finite_float(raw.get("wait_seconds")),
        wait_eta=safe_cmd_str(raw.get("wait_eta"), "Ready"),
        duration_seconds=safe_finite_float(raw.get("duration_seconds")) or 0.0,
        duration_text=safe_cmd_str(raw.get("duration_text"), "Instant"),
        total_hours=safe_finite_float(raw.get("total_hours")),
        total_seconds=safe_finite_float(raw.get("total_seconds")),
        total_eta=safe_cmd_str(raw.get("total_eta"), "Ready"),
        eta_bottleneck=safe_cmd_str(raw_bn) if raw_bn is not None else None,
        manual_queue_id=safe_cmd_opt_int(raw.get("manual_queue_id")),
        manual_sort_rank=safe_cmd_opt_int(raw.get("manual_sort_rank")),
    )


def _core_action_payload(action: PlannedActionCmdLike, pid: int, *, dry_run: bool) -> dict[str, object] | None:
    if action.kind == "CLAIM_TUTORIAL":
        return {"command": "claim_tutorial", "dry_run": dry_run}
    if action.kind == "BUILD" and action.target_id is not None:
        return {
            "command": "build",
            "building_id": safe_cmd_opt_int(action.target_id),
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "DEMOLISH_BUILDING" and action.target_id is not None:
        return {
            "command": "demolish_building",
            "building_id": safe_cmd_opt_int(action.target_id) or 6,
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "RESEARCH" and action.target_id is not None:
        return {
            "command": "research",
            "research_id": safe_cmd_opt_int(action.target_id),
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "BUILD_SHIP" and action.target_id is not None:
        return {
            "command": "build_ship",
            "ship_id": safe_cmd_opt_int(action.target_id),
            "quantity": max(1, safe_cmd_int(action.target_level, 1)),
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "THROTTLE_BUILDING" and action.target_id is not None:
        th_payload: dict[str, object] = {
            "command": "throttle_building",
            "building_id": safe_cmd_opt_int(action.target_id),
            "percentage": 0 if action.target_level is None else safe_cmd_int(action.target_level, 0),
            "planet_id": pid,
            "dry_run": dry_run,
        }
        th_reason = safe_cmd_str(action.reason).strip()
        if th_reason:
            th_payload["reason"] = th_reason
        return th_payload
    if action.kind == "ABANDON_COLONY":
        return {
            "command": "abandon_colony",
            "planet_id": pid,
            "min_colony_fields": max(50, safe_cmd_int(action.target_level, 300)),
            "dry_run": dry_run,
        }
    if action.kind == "ACTIVATE_TRANSMITTER":
        return {"command": "activate_transmitter", "planet_id": pid, "dry_run": dry_run}
    return None


def _fleet_action_payload(action: PlannedActionCmdLike, pid: int, *, dry_run: bool) -> dict[str, object] | None:
    t_name = safe_cmd_str(action.target_name)
    coord_m = _COORD_TRIPLE_RE.search(t_name)
    coords_or_name = coord_m.group(1) if coord_m else t_name

    if action.kind == "ESCAPE_FLIGHT":
        return {
            "command": "escape_flight",
            "planet_id": pid,
            "target_coords": coord_m.group(1) if coord_m else None,
            "reason": safe_cmd_str(action.reason),
            "dry_run": dry_run,
        }
    if action.kind == "SCAN_GALAXY":
        p_coords = safe_cmd_str(action.planet_coords)
        gs_m = _GAL_SYS_RE.search(t_name) or _GAL_SYS_RE.search(p_coords)
        gal, sys_num = (int(gs_m.group(1)), int(gs_m.group(2))) if gs_m else (1, 1)
        return {"command": "scan_galaxy", "galaxy": gal, "system": sys_num, "planet_id": pid, "dry_run": dry_run}
    if action.kind in ("SPY", "RECYCLE"):
        return {
            "command": action.kind.lower(),
            "target_coords": coords_or_name,
            "quantity": max(1, safe_cmd_int(action.target_level, 1)),
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "TRANSPORT":
        cost_obj = action.cost
        payload_tr: dict[str, object] = {
            "command": "transport",
            "target_coords": coords_or_name,
            "planet_id": pid,
            "resources": {
                "pig_iron": max(0, safe_cmd_int(cost_obj.pig_iron, 0)),
                "metal": max(0, safe_cmd_int(cost_obj.metal, 0)),
                "kryptonite": max(0, safe_cmd_int(cost_obj.kryptonite, 0)),
                "spice": max(0, safe_cmd_int(cost_obj.spice, 0)),
            },
            "dry_run": dry_run,
        }
        if action.target_id is not None and safe_cmd_int(action.target_id, 0) > 0:
            payload_tr["dest_planet_id"] = safe_cmd_int(action.target_id, 0)
        return payload_tr
    if action.kind == "STATION":
        ships_map = parse_station_ships_spec(f"{t_name} {safe_cmd_str(action.reason)}")
        payload_st: dict[str, object] = {
            "command": "station",
            "target_coords": coords_or_name,
            "planet_id": pid,
            "ships": ships_map,
            "dry_run": dry_run,
        }
        if action.target_id is not None and safe_cmd_int(action.target_id, 0) > 0:
            payload_st["dest_planet_id"] = safe_cmd_int(action.target_id, 0)
        return payload_st
    if action.kind in ("COLONIZE", "ATTACK"):
        return {"command": action.kind.lower(), "target_coords": coords_or_name, "planet_id": pid, "dry_run": dry_run}
    return None


def action_to_command_payload(action: PlannedActionCmdLike, *, dry_run: bool) -> dict[str, object]:
    """Convert a ``PlannedAction`` into a ``POST /command`` JSON payload."""
    pid = safe_cmd_int(action.planet_id, 0)
    core = _core_action_payload(action, pid, dry_run=dry_run)
    if core is not None:
        return core
    fleet = _fleet_action_payload(action, pid, dry_run=dry_run)
    if fleet is not None:
        return fleet
    return {"command": "plan", "dry_run": True}


def _cmd_fleet_subrank(act: PlannedActionCmdLike) -> int:
    if act.priority == "P1":
        if act.kind == "ABANDON_COLONY":
            return 0
        if act.kind == "SPY":
            return 1 if "(free slot)" in safe_cmd_str(act.target_name).lower() else 2
        if act.kind == "TRANSPORT":
            return 3
        if act.kind == "COLONIZE":
            return 4
        return 5
    if act.priority == "P2":
        if act.kind == "CLAIM_TUTORIAL":
            return 0
        if act.kind == "SPY":
            return 1
        if act.kind == "TRANSPORT":
            return 2
        if act.kind == "COLONIZE":
            return 3
        if act.kind == "STATION":
            return 4
        return 5
    return 0


def hold_busy_fleet_slots(
    actions: Sequence[PlannedAction],
    fleet_slot_cooldowns: Mapping[int, float] | None,
    *,
    re_sort_priority: bool = False,
) -> list[PlannedAction]:
    """Mark fleet dispatches on cooldown-busy planets non-executable and optionally re-sort."""
    if not fleet_slot_cooldowns or not actions:
        return list(actions)
    busy_pids: set[int] = set()
    for key, val in fleet_slot_cooldowns.items():
        fv = safe_finite_float(val)
        if fv is None or fv <= 0.0:
            continue
        if isinstance(key, int) and not isinstance(key, bool):
            busy_pids.add(key)
        else:
            ks = safe_cmd_str(key).strip()
            if _INT_KEY_RE.fullmatch(ks):
                busy_pids.add(int(ks))
    if not busy_pids:
        return list(actions)

    hold_suffix = " [Hold: fleet slots busy / returning]"
    changed = False
    out: list[PlannedAction] = []
    for act in actions:
        if act.kind in FLEET_ACTION_KINDS and act.planet_id in busy_pids:
            cur_reason = safe_cmd_str(act.reason)
            new_reason = (
                cur_reason if "[Hold: fleet slots busy / returning]" in cur_reason else f"{cur_reason}{hold_suffix}"
            )
            if act.executable_now or new_reason != cur_reason:
                changed = True
                out.append(replace(act, executable_now=False, reason=new_reason))
                continue
        out.append(act)

    if changed and re_sort_priority:
        out.sort(
            key=lambda a: (
                _PRIO_RANK.get(a.priority, 9),
                -1 if a.kind == "ALERT_HOSTILE" else (0 if a.executable_now else 1),
                _cmd_fleet_subrank(a),
            )
        )
    return out
