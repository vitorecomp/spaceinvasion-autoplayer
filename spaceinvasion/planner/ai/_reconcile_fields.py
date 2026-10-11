"""Max-field demolition safety, field-conflict ranking, and demolition floor resolution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_int, coerce_optional_int
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import PlannedAction, normalize_planet_type
from spaceinvasion.planner.ai._reconcile_helpers import energy_net_value, match_step_planet
from spaceinvasion.planner.ai._types import GameStateLike, PlanetLike


def _is_planet_demolish_energy_safe(state: GameStateLike, planet: PlanetLike, bid: int) -> bool:
    """Return True when tearing down 1 level of ``bid`` on ``planet`` keeps planet net energy >= 0."""
    cur_lvl = planet.building_level(bid)
    if cur_lvl < 1 or normalize_planet_type(planet) in ("asteroid", "raid"):
        return False
    raw_loss = max(0, game_data.building_energy_delta(bid, cur_lvl))
    if raw_loss <= 0:
        return True
    r2 = max(0, min(200, coerce_int((state.researches or {}).get("r2", 0), 0)))
    safe_loss = int(math.ceil(raw_loss * (1.0 + 0.01 * r2)))
    throttled_res = 0
    econ_map = planet.economy_rates if isinstance(planet.economy_rates, Mapping) else {}
    for mbid, rkey in ((1, "resource1"), (2, "resource2"), (3, "resource3"), (4, "resource4")):
        mlvl = planet.building_level(mbid)
        if mlvl <= 0:
            continue
        mpct = max(0, min(100, coerce_int(econ_map.get(rkey, 100), 100)))
        if mpct < 100:
            full_e = game_data.mine_energy_needed(mbid, mlvl)
            throttled_res += max(0, int(math.ceil(full_e * (100 - mpct) / 100.0)))
    net_e = energy_net_value(planet.energy)
    return (net_e - throttled_res - safe_loss) >= 0


def _field_conflict_candidates(
    planet: PlanetLike,
    blocked_bkey: str,
    *,
    planned_demolish_floors: Mapping[str, int] | None = None,
) -> list[dict[str, object]]:
    """List existing non-zero buildings on ``planet`` ordered from safest demolition candidate to riskiest."""
    dem_floors = planned_demolish_floors or {}

    def _sort_key(item: tuple[int, str, int]) -> tuple[int, int, int]:
        bid, bkey, lvl = item
        is_planned_dem = bkey in dem_floors and lvl > int(dem_floors[bkey])
        if is_planned_dem and bid != 5:
            group = -1
        elif bid == 5:
            group = 4
        elif bid == 6:
            group = 0
        elif bkey == blocked_bkey:
            group = 3
        elif bid in (1, 2, 3, 4):
            group = 2
        else:
            group = 1
        return (group, -lvl, bid)

    raw_items: list[tuple[int, str, int]] = []
    for bid in sorted(game_data.BUILDING_BY_ID):
        bkey = f"b{bid}"
        lvl = planet.building_level(bid)
        if lvl > 0:
            raw_items.append((bid, bkey, lvl))
    raw_items.sort(key=_sort_key)

    candidates: list[dict[str, object]] = []
    for bid, bkey, lvl in raw_items:
        info = game_data.BUILDING_BY_ID[bid]
        is_planned_dem = bkey in dem_floors and lvl > int(dem_floors[bkey])
        candidates.append(
            {
                "building_key": bkey,
                "building_id": bid,
                "building_name": str(info["name"]),
                "current_level": int(lvl),
                "is_power_plant": bid in (5, 6),
                "planned_for_demolition": bool(is_planned_dem),
            }
        )
    return candidates


def _resolve_plan_demolish_floors_by_planet(
    plan_result: Mapping[str, object],
    planets_by_id: Mapping[int, PlanetLike],
    planets_by_coords: Mapping[str, PlanetLike],
) -> tuple[bool, dict[int, dict[str, int]]]:
    """Extract ``(allow_demolition, {planet_id: {bkey: target_floor}})`` from ``plan_result``."""
    raw_summary = plan_result.get("summary")
    raw_meta = plan_result.get("meta_parameters")
    summary_m: Mapping[str, object] = raw_summary if isinstance(raw_summary, Mapping) else {}
    meta_m: Mapping[str, object] = raw_meta if isinstance(raw_meta, Mapping) else {}
    allow_dem = bool(
        plan_result.get("allow_demolition", False)
        or summary_m.get("allow_demolition", False)
        or meta_m.get("allow_demolition", False)
    )
    floors_by_pid: dict[int, dict[str, int]] = {}
    raw_dt = plan_result.get("demolish_targets")
    if isinstance(raw_dt, Mapping):
        for pk, bmap in raw_dt.items():
            if not isinstance(bmap, Mapping):
                continue
            pid_opt = coerce_optional_int(pk, None)
            p_obj = planets_by_id.get(pid_opt) if pid_opt is not None else None
            if p_obj is None:
                pk_s = str(pk).strip()
                p_obj = planets_by_coords.get(pk_s) or (planets_by_coords.get(normalize_coords(pk_s)) if pk_s else None)
            if p_obj is None:
                continue
            dst_m = floors_by_pid.setdefault(int(p_obj.planet_id), {})
            for bk, lvl in bmap.items():
                bk_s = str(bk).strip()
                if bk_s:
                    val = max(0, coerce_int(lvl, 0))
                    dst_m[bk_s] = min(dst_m[bk_s], val) if bk_s in dst_m else val

    steps_seq = plan_result.get("steps")
    if isinstance(steps_seq, Sequence) and not isinstance(steps_seq, (str, bytes)):
        for st in steps_seq:
            if not isinstance(st, Mapping):
                continue
            if str(st.get("kind") or "").strip().upper() not in ("DEMOLISH", "DEMOLISH_BUILDING"):
                continue
            allow_dem = True
            p_obj = match_step_planet(st, planets_by_id, planets_by_coords)
            if p_obj is None:
                continue
            ek = str(st.get("entity_key") or "").strip()
            tlvl = max(0, coerce_int(st.get("target_level"), 0))
            if ek:
                dst_m = floors_by_pid.setdefault(int(p_obj.planet_id), {})
                dst_m[ek] = min(dst_m[ek], tlvl) if ek in dst_m else tlvl
    return allow_dem, floors_by_pid


def handle_full_planet_fields(
    state: GameStateLike,
    planet: PlanetLike,
    *,
    bid: int,
    entity_key: str,
    b_name: str,
    target_level: int,
    seq: int,
    allow_live_demolition: bool,
    demolish_floors_by_pid: Mapping[int, Mapping[str, int]],
    planet_queue_counts: Mapping[int, int],
) -> tuple[PlannedAction | None, dict[str, object] | None]:
    """Handle a BUILD step on a planet with ``fields_used >= fields_max > 0``."""
    pid = int(planet.planet_id)
    planet_dem_floors = demolish_floors_by_pid.get(pid) or {}
    candidates = _field_conflict_candidates(planet, entity_key, planned_demolish_floors=planet_dem_floors)
    if allow_live_demolition and planet_dem_floors:
        safe_dem_cand = next(
            (
                cand
                for cand in candidates
                if bool(cand.get("planned_for_demolition"))
                and _is_planet_demolish_energy_safe(state, planet, coerce_int(cand.get("building_id"), 0))
            ),
            None,
        )
        if safe_dem_cand is not None:
            if planet_queue_counts.get(pid, 0) >= 1:
                return (
                    PlannedAction(
                        priority="P1",
                        kind="WAIT_RESOURCES",
                        planet_id=pid,
                        planet_coords=str(planet.coords),
                        target_id=bid,
                        target_name=b_name,
                        target_level=target_level,
                        reason=(
                            f"AI-Planner #{seq}: waiting for active building queue on full planet "
                            f"({planet.fields_used}/{planet.fields_max} fields) before {b_name} L{target_level}"
                        ),
                        executable_now=False,
                    ),
                    None,
                )
            dem_bid = coerce_int(safe_dem_cand.get("building_id"), 0)
            dem_name = str(safe_dem_cand.get("building_name") or "")
            dem_cur = coerce_int(safe_dem_cand.get("current_level"), 0)
            return (
                PlannedAction(
                    priority="P1",
                    kind="DEMOLISH_BUILDING",
                    planet_id=pid,
                    planet_coords=str(planet.coords),
                    target_id=dem_bid,
                    target_name=dem_name,
                    target_level=max(0, dem_cur - 1),
                    reason=(
                        f"AI-Planner: demolish {dem_name} L{dem_cur} -> L{max(0, dem_cur - 1)} "
                        f"({planet.fields_used}/{planet.fields_max} fields full, needed for {b_name} L{target_level})"
                    ),
                    executable_now=True,
                ),
                None,
            )

    top_cand = candidates[0] if candidates else None
    conflict: dict[str, object] = {
        "conflict_key": f"p{pid}:{entity_key}:L{target_level}",
        "planet_id": pid,
        "planet_coords": str(planet.coords),
        "planet_name": str(planet.name or planet.coords),
        "fields_used": int(planet.fields_used),
        "fields_max": int(planet.fields_max),
        "blocked_building_key": entity_key,
        "blocked_building_name": b_name,
        "blocked_target_level": int(target_level),
        "candidate_building_key": str(top_cand["building_key"]) if top_cand else "",
        "candidate_building_name": str(top_cand["building_name"]) if top_cand else "",
        "candidate_level": coerce_int(top_cand["current_level"], 0) if top_cand else 0,
        "candidates": candidates,
    }
    return None, conflict
