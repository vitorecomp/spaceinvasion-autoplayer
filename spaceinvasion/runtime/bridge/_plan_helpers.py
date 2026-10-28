"""Shared helpers for plan serialization, demolition safety, hybrid sorting, and P0 escape checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
import logging
import math

from spaceinvasion.combat.cargo import plan_escape_flight
from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import GameState, PlanetState, PlannedAction, Resources, normalize_planet_type
from spaceinvasion.planner.heuristics import AI_INFRA_CARGO_TAG, attach_action_etas
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    safe_api_int,
    sanitize_json_dict,
)

_LOGGER = logging.getLogger(__name__)
_PRIO_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def extract_ai_resource_targets(
    ai_actions: Sequence[PlannedAction],
) -> tuple[Resources, dict[int, Resources]]:
    """Derive `(reserved_cost, priority_resource_need_by_planet)` from live AI-Planner steps."""
    reserved = Resources()
    first_waiting = next(
        (
            a
            for a in ai_actions
            if a.kind in ("BUILD", "WAIT_RESOURCES", "RESEARCH") and not a.executable_now and not a.cost.is_zero()
        ),
        None,
    )
    if first_waiting is None:
        first_waiting = next(
            (a for a in ai_actions if a.kind in ("BUILD", "WAIT_RESOURCES", "RESEARCH") and not a.cost.is_zero()),
            None,
        )
    if first_waiting is not None:
        reserved = first_waiting.cost

    needs_by_pid: dict[int, list[int]] = {}
    for act in ai_actions:
        if act.kind not in ("BUILD", "WAIT_RESOURCES", "RESEARCH"):
            continue
        if act.planet_id <= 0 or act.missing.is_zero():
            continue
        buf = needs_by_pid.setdefault(act.planet_id, [0, 0, 0, 0])
        buf[0] = max(buf[0], max(0, safe_api_int(act.missing.pig_iron, 0)))
        buf[1] = max(buf[1], max(0, safe_api_int(act.missing.metal, 0)))
        buf[2] = max(buf[2], max(0, safe_api_int(act.missing.kryptonite, 0)))
        buf[3] = max(buf[3], max(0, safe_api_int(act.missing.spice, 0)))
    prio_needs = {
        pid: Resources(vals[0], vals[1], vals[2], vals[3]) for pid, vals in needs_by_pid.items() if sum(vals) > 0
    }
    return reserved, prio_needs


def check_demolish_energy_safety(
    state: GameState | None,
    planet: PlanetState | None,
    bid: int,
) -> tuple[bool, str, int]:
    """Validate whether demolishing 1 level of `bid` on `planet` preserves non-negative net energy."""
    binfo = game_data.BUILDING_BY_ID.get(bid)
    bname = str(binfo.get("name") or f"Building b{bid}") if isinstance(binfo, Mapping) else f"Building b{bid}"
    if planet is None:
        return True, f"Demolish {bname} (no live planet snapshot for energy check)", 1
    cur_lvl = planet.building_level(bid)
    if cur_lvl < 1:
        return False, f"{bname} is already at Level 0 on {planet.coords}", 0
    ptype_norm = normalize_planet_type(planet)
    if ptype_norm in ("asteroid", "raid"):
        return False, f"Building demolition unavailable on {ptype_norm}", cur_lvl
    raw_loss = max(0, game_data.building_energy_delta(bid, cur_lvl))
    if raw_loss > 0:
        res_map: Mapping[str, int] = state.researches if state is not None else {}
        r2 = max(0, min(200, safe_api_int(res_map.get("r2", 0), 0)))
        safe_loss = int(math.ceil(raw_loss * (1.0 + 0.01 * r2)))
        throttled_res = 0
        econ_map = planet.economy_rates
        for mbid, rkey in ((1, "resource1"), (2, "resource2"), (3, "resource3"), (4, "resource4")):
            mlvl = planet.building_level(mbid)
            if mlvl <= 0:
                continue
            mpct = max(0, min(100, safe_api_int(econ_map.get(rkey, 100), 100)))
            if mpct < 100:
                full_e = game_data.mine_energy_needed(mbid, mlvl)
                throttled_res += max(0, int(math.ceil(full_e * (100 - mpct) / 100.0)))
        if safe_api_int(planet.energy.net, 0) - throttled_res - safe_loss < 0:
            return (
                False,
                f"Insufficient spare energy to demolish {bname} safely (upgrade Fusion plant first)",
                cur_lvl,
            )
    return True, f"Demolish {bname} L{cur_lvl} -> L{cur_lvl - 1} to reclaim 1 field", cur_lvl


def serialize_planned_action(
    ctx: BridgeContext,
    action: PlannedAction,
    state: GameState | None = None,
) -> dict[str, object]:
    """Attach ETA fields, resolve display `planet_name`, and sanitize a `PlannedAction` for JSON."""
    enriched = attach_action_etas(state, [action])[0] if state is not None else action
    raw: dict[str, object] = asdict(enriched)
    for key in ("wait_hours", "wait_seconds", "duration_seconds", "total_hours", "total_seconds"):
        val = raw.get(key)
        if isinstance(val, float) and not math.isfinite(val):
            raw[key] = None if key.startswith(("wait_", "total_")) else 0.0
    pid = safe_api_int(raw.get("planet_id"), 0)
    p_name = ctx.custom_planet_names.get(str(pid), "") if pid > 0 else ""
    if not p_name:
        ref_st = state if state is not None else ctx.last_state
        if ref_st is not None:
            nc_act = normalize_coords(str(raw.get("planet_coords") or ""))
            for p_it in ref_st.planets:
                if (pid > 0 and int(p_it.planet_id) == pid) or (nc_act and normalize_coords(p_it.coords) == nc_act):
                    p_name = ctx.custom_planet_names.get(str(p_it.planet_id)) or str(p_it.name or "")
                    break
    if p_name:
        raw["planet_name"] = p_name
    return sanitize_json_dict(raw)


def hybrid_action_subrank(act: PlannedAction) -> tuple[int, int]:
    """Return intra-priority ordering tuple for hybrid AI-Planner + fleet/throttle plans."""
    if act.priority == "P0":
        return (0 if act.kind == "ALERT_HOSTILE" else 1, 0)
    if act.priority == "P1":
        if act.kind == "THROTTLE_BUILDING":
            rsn_l = str(act.reason or "").lower()
            if "negative energy" in rsn_l or "metal storage full" in rsn_l or "metal overflow cleared" in rsn_l:
                return (0, 0)
            return (1, 0)
        if act.kind in ("BUILD", "DEMOLISH_BUILDING", "RESEARCH"):
            return (0, 1)
        if act.kind == "TRANSPORT":
            return (0, 2)
        if act.kind == "SPY":
            return (0, 3)
        if act.kind == "WAIT_RESOURCES":
            return (1, 1)
        return (2, 0)
    if act.priority == "P2":
        if act.kind in ("BUILD", "RESEARCH", "WAIT_RESOURCES"):
            return (0, 0)
        if act.kind == "THROTTLE_BUILDING":
            return (0, 5)
        if act.kind == "BUILD_SHIP" and AI_INFRA_CARGO_TAG in str(act.reason or ""):
            return (0, 6)
        if act.kind == "SPY":
            return (1, 0)
        if act.kind == "TRANSPORT":
            return (2, 0)
        if act.kind == "STATION":
            return (3, 0)
        if act.kind in ("ATTACK", "RECYCLE"):
            return (4, 0)
        return (5, 0)
    return (0, 0)


def dedupe_and_sort_hybrid_actions(state: GameState, combined: list[PlannedAction]) -> list[PlannedAction]:
    """Sort combined hybrid AI/fleet/throttle actions, deduplicate, and attach ETAs."""
    combined.sort(
        key=lambda a: (
            _PRIO_RANK.get(a.priority, 9),
            -1 if a.kind == "ALERT_HOSTILE" else (0 if a.executable_now else 1),
            hybrid_action_subrank(a),
        )
    )
    deduped: list[PlannedAction] = []
    seen: set[tuple[str, int, int | None, str]] = set()
    for act in combined:
        key = (act.kind, act.planet_id, act.target_id, act.target_name)
        if key not in seen:
            seen.add(key)
            deduped.append(act)
    return list(attach_action_etas(state, deduped))


def has_urgent_p0_escape_flight(ctx: BridgeContext) -> tuple[bool, str]:
    """Return `(True, reason)` if an urgent P0 escape flight should preempt background galaxy sweeps."""
    if not ctx.strategy_config.allow_fleetsave:
        return False, ""
    st = ctx.last_state
    if st is not None and any(bool(fa.hostile) for fa in st.fleet_alerts):
        active = st.active_planet()
        if active is not None:
            other_coords = next((p.coords for p in st.planets if p.planet_id != active.planet_id), active.coords)
            escape = plan_escape_flight(
                active,
                target_coords=other_coords,
                fuel_reserve_spice=ctx.strategy_config.fleet_save_fuel_reserve_spice,
            )
            if escape.feasible:
                return True, "Preempted galaxy scan for P0 hostile Escape Flight."
    if ctx.db is not None:
        try:
            pending = ctx.db.get_manual_queue(status="pending", limit=50)
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.warning("Failed to inspect manual queue for P0 escape flight", exc_info=True)
            pending = []
        for item in pending:
            if (
                isinstance(item, Mapping)
                and str(item.get("priority") or "").strip().upper() == "P0"
                and str(item.get("kind") or "").strip().upper() == "ESCAPE_FLIGHT"
            ):
                return True, "Preempted galaxy scan for pinned P0 Escape Flight."
    return False, ""
