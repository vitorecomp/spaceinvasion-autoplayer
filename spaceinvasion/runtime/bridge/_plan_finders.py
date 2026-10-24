"""Direct building upgrade and demolition action finder helpers."""

from __future__ import annotations

from collections.abc import Mapping

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import (
    BuildingOffer,
    GameState,
    PlanetState,
    PlannedAction,
    is_building_allowed_on_planet_type,
    normalize_planet_type,
)
from spaceinvasion.planner.heuristics import _is_stale_remote_resource_wait, _resources_from_dict
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import safe_api_int
from spaceinvasion.runtime.bridge._plan_helpers import check_demolish_energy_safety


def _build_without_offer(
    state: GameState,
    planet: PlanetState,
    building_id: int,
    nxt_lvl: int,
    *,
    allow_live_verify: bool,
    planet_type_allowed: bool,
    ptype_norm: str,
    prereqs_met: bool,
) -> PlannedAction:
    wiki_cost = _resources_from_dict(game_data.building_cost(building_id, nxt_lvl))
    can_fallback = planet.planet_id != state.active_planet_id or (allow_live_verify and not planet.building_offers)
    fallback_ok = (
        can_fallback
        and planet_type_allowed
        and prereqs_met
        and not wiki_cost.is_zero()
        and planet.resources.can_afford(wiki_cost)
    )
    if not planet_type_allowed:
        reason = f"API build command (building b{building_id} not allowed on planet_type={ptype_norm})"
    elif planet.planet_id != state.active_planet_id and fallback_ok:
        reason = "API build command (cross-planet check)"
    elif fallback_ok:
        reason = "API build command (live verify)"
    else:
        reason = "API build command (offer unavailable/not unlocked)"
    return PlannedAction(
        priority="P2",
        kind="BUILD",
        planet_id=planet.planet_id,
        planet_coords=planet.coords,
        target_id=building_id,
        target_name=planet.building_names.get(f"b{building_id}", f"Building {building_id}"),
        target_level=nxt_lvl,
        cost=wiki_cost,
        missing=planet.resources.missing(wiki_cost),
        reason=reason,
        executable_now=fallback_ok,
        action_href=None,
    )


def _build_mismatched_offer(
    planet: PlanetState,
    offer: BuildingOffer,
    building_id: int,
    nxt_lvl: int,
    *,
    allow_live_verify: bool,
    planet_type_allowed: bool,
    ptype_norm: str,
    prereqs_met: bool,
) -> PlannedAction:
    wiki_cost = _resources_from_dict(game_data.building_cost(building_id, nxt_lvl))
    can_exec = (
        allow_live_verify
        and planet_type_allowed
        and prereqs_met
        and not wiki_cost.is_zero()
        and planet.resources.can_afford(wiki_cost)
    )
    if not planet_type_allowed:
        reason = f"API build command (building b{building_id} not allowed on planet_type={ptype_norm})"
    elif not prereqs_met:
        reason = "API build command (prerequisites unmet)"
    elif allow_live_verify:
        reason = "API build command (live verify)"
    else:
        reason = "API build command (offer unavailable/not unlocked)"
    return PlannedAction(
        priority="P2",
        kind="BUILD",
        planet_id=planet.planet_id,
        planet_coords=planet.coords,
        target_id=offer.building_id,
        target_name=offer.name,
        target_level=nxt_lvl,
        cost=wiki_cost,
        missing=planet.resources.missing(wiki_cost),
        reason=reason,
        executable_now=can_exec,
        action_href=None,
    )


def find_build_action(
    state: GameState,
    building_id: int,
    planet_id: int | None,
    *,
    allow_live_verify: bool = False,
) -> PlannedAction:
    """Resolve an explicit `BUILD` `PlannedAction` for `building_id` on `planet_id`."""
    planet = BridgeContext.resolve_planet(state, planet_id)
    bid_int = int(building_id)
    nxt_lvl = planet.building_level(bid_int) + 1
    offer = next((o for o in planet.building_offers if o.building_id == bid_int), None)
    ptype_norm = normalize_planet_type(planet)
    planet_type_allowed = is_building_allowed_on_planet_type(
        planet, bid_int, has_b8_offer=(offer is not None and bid_int == 8)
    )
    prereqs_met = not game_data.missing_prerequisites(
        "buildings", bid_int, buildings=planet.buildings, researches=state.researches
    )
    if offer is None:
        return _build_without_offer(
            state,
            planet,
            bid_int,
            nxt_lvl,
            allow_live_verify=allow_live_verify,
            planet_type_allowed=planet_type_allowed,
            ptype_norm=ptype_norm,
            prereqs_met=prereqs_met,
        )
    if offer.target_level != nxt_lvl:
        return _build_mismatched_offer(
            planet,
            offer,
            bid_int,
            nxt_lvl,
            allow_live_verify=allow_live_verify,
            planet_type_allowed=planet_type_allowed,
            ptype_norm=ptype_norm,
            prereqs_met=prereqs_met,
        )
    is_remote = planet.planet_id != state.active_planet_id
    can_exec = planet_type_allowed and offer.can_build and planet.resources.can_afford(offer.cost)
    if (
        planet_type_allowed
        and not can_exec
        and prereqs_met
        and (allow_live_verify or is_remote)
        and not offer.cost.is_zero()
        and _is_stale_remote_resource_wait(offer)
        and planet.resources.can_afford(offer.cost)
    ):
        can_exec = True
    reason = (
        "API build command"
        if planet_type_allowed
        else f"API build command (building b{bid_int} not allowed on planet_type={ptype_norm})"
    )
    return PlannedAction(
        priority="P2",
        kind="BUILD",
        planet_id=planet.planet_id,
        planet_coords=planet.coords,
        target_id=offer.building_id,
        target_name=offer.name,
        target_level=offer.target_level,
        cost=offer.cost,
        missing=planet.resources.missing(offer.cost),
        reason=reason,
        executable_now=can_exec,
        action_href=offer.build_href if (can_exec and offer.can_build) else None,
    )


def find_demolish_action(state: GameState, building_id: int, planet_id: int | None) -> PlannedAction:
    """Resolve an explicit `DEMOLISH_BUILDING` `PlannedAction` with queue and energy-safety checks."""
    planet = BridgeContext.resolve_planet(state, planet_id)
    bid = int(building_id)
    binfo = game_data.BUILDING_BY_ID.get(bid)
    bname = str(binfo.get("name") or f"Building {bid}") if isinstance(binfo, Mapping) else f"Building {bid}"
    cur_lvl = planet.building_level(bid)
    ptype_norm = normalize_planet_type(planet)
    active_p = state.active_planet()
    is_active_p = active_p is not None and planet.planet_id == active_p.planet_id
    default_slots = (
        1
        if ptype_norm == "asteroid"
        else (6 if safe_api_int(state.specs.get("construction_commander", 0), 0) > 0 else 5)
    )
    max_slots = max(1, safe_api_int(state.specs.get("max_build_slots", default_slots), default_slots))
    planet_nc = normalize_coords(planet.coords)
    planet_b_jobs = [
        q
        for q in state.active_queues
        if q.category == "building"
        and ((normalize_coords(q.origin_coords) == planet_nc) if q.origin_coords else is_active_p)
    ]
    q_busy = len(planet_b_jobs) >= max_slots
    b_in_queue = any(
        q.building_id == bid
        or bname.lower() in str(q.name or "").lower()
        or (bid == 6 and "thermal" in str(q.name or "").lower())
        for q in planet_b_jobs
    )
    if cur_lvl < 1:
        can_dem, reason = False, f"{bname} is already at Level 0"
    elif ptype_norm in ("asteroid", "raid"):
        can_dem, reason = False, f"Building demolition unavailable on {ptype_norm}"
    elif q_busy or b_in_queue:
        can_dem, reason = False, "Building queue busy"
    else:
        can_dem, reason, _ = check_demolish_energy_safety(state, planet, bid)
    return PlannedAction(
        priority="P1",
        kind="DEMOLISH_BUILDING",
        planet_id=planet.planet_id,
        planet_coords=planet.coords,
        target_id=bid,
        target_name=bname,
        target_level=max(0, cur_lvl - 1),
        reason=reason,
        executable_now=can_dem,
    )
