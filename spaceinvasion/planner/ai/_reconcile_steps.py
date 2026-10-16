"""Per-step live BUILD and RESEARCH reconciliation helpers."""

from __future__ import annotations

from collections.abc import Mapping

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coercion import coerce_int, coerce_optional_int
from spaceinvasion.core.model import PlannedAction, Resources, is_building_allowed_on_planet_type, normalize_planet_type
from spaceinvasion.planner.ai._reconcile_fields import handle_full_planet_fields
from spaceinvasion.planner.ai._reconcile_helpers import (
    ReadConfigLike,
    _is_stale_remote_resource_wait,
    config_allow_research,
    match_step_planet,
    offers_by_building_id,
    resources_can_afford,
    resources_is_zero,
    resources_missing,
    storage_exceeds_cost,
)
from spaceinvasion.planner.ai._types import (
    BuildingOfferLike,
    GameStateLike,
    PlanetLike,
    ResearchOfferLike,
    coerce_resources_obj,
)
from spaceinvasion.planner.config_optimizer import full_cost


def _resolve_build_execution(
    planet: PlanetLike,
    *,
    offers_by_id: Mapping[int, BuildingOfferLike],
    bid: int,
    target_level: int,
    wiki_cost: Resources,
    is_remote: bool,
    allow_planet: bool,
    queue_full: bool,
    reason: str,
) -> tuple[Resources, Resources, bool, str | None, str]:
    offer = offers_by_id.get(bid)
    if offer is not None and coerce_int(offer.target_level, target_level) == target_level:
        cost = coerce_resources_obj(offer.cost)
        missing = resources_missing(planet.resources, cost)
        stale_resource_ok = _is_stale_remote_resource_wait(offer) and not resources_is_zero(cost)
        can_exec = (
            allow_planet
            and (not queue_full)
            and (bool(offer.can_build) or stale_resource_ok)
            and resources_can_afford(planet.resources, cost)
        )
        href = (str(offer.build_href) or None) if (can_exec and offer.can_build and offer.build_href) else None
        if not allow_planet:
            out_reason = f"{reason} (multi-planet disabled)"
        elif queue_full:
            out_reason = f"{reason} (build queue full)"
        else:
            out_reason = reason
        return cost, missing, can_exec, href, out_reason

    cost = wiki_cost
    missing = resources_missing(planet.resources, cost)
    can_live_verify = (
        is_remote or (not offers_by_id) or (offer is not None and coerce_int(offer.target_level, 0) < target_level)
    )
    can_exec = (
        allow_planet
        and (not queue_full)
        and can_live_verify
        and (not resources_is_zero(cost))
        and resources_can_afford(planet.resources, cost)
    )
    if not allow_planet:
        out_reason = f"{reason} (multi-planet disabled)"
    elif queue_full:
        out_reason = f"{reason} (build queue full)"
    elif not can_live_verify and not can_exec:
        out_reason = f"{reason} (building card not currently visible)"
    else:
        out_reason = reason
    return cost, missing, can_exec, None, out_reason


def reconcile_build_step(
    state: GameStateLike,
    step: Mapping[str, object],
    *,
    entity_key: str,
    target_level: int,
    seq: int,
    planets_by_id: Mapping[int, PlanetLike],
    planets_by_coords: Mapping[str, PlanetLike],
    handled_planets: set[int],
    in_flight_buildings: Mapping[int, Mapping[str, int]],
    planet_queue_counts: Mapping[int, int],
    max_build_slots: int,
    allow_multi_planet: bool,
    allow_live_demolition: bool,
    demolish_floors_by_pid: Mapping[int, Mapping[str, int]],
) -> tuple[PlannedAction | None, dict[str, object] | None]:
    """Reconcile a single ``BUILD`` step into a live ``PlannedAction`` or field conflict."""
    planet = match_step_planet(step, planets_by_id, planets_by_coords)
    if planet is None or normalize_planet_type(planet) != "normal":
        return None, None
    pid = int(planet.planet_id)
    if pid in handled_planets or not (entity_key.startswith("b") and entity_key[1:].isdigit()):
        return None, None
    bid = int(entity_key[1:])
    b_info = game_data.BUILDING_BY_ID.get(bid)
    if not b_info:
        return None, None
    b_name = str(step.get("entity_name") or b_info["name"])
    live_level = planet.building_level(bid)
    if max(live_level, in_flight_buildings.get(pid, {}).get(entity_key, 0)) >= target_level:
        return None, None
    if target_level != live_level + 1:
        return None, None

    offers_by_id = offers_by_building_id(planet)
    if not is_building_allowed_on_planet_type(planet, bid, has_b8_offer=(8 in offers_by_id)):
        return None, None
    if game_data.missing_prerequisites("buildings", bid, buildings=planet.buildings, researches=state.researches):
        return None, None

    if planet.fields_max > 0 and planet.fields_used >= planet.fields_max:
        handled_planets.add(pid)
        return handle_full_planet_fields(
            state,
            planet,
            bid=bid,
            entity_key=entity_key,
            b_name=b_name,
            target_level=target_level,
            seq=seq,
            allow_live_demolition=allow_live_demolition,
            demolish_floors_by_pid=demolish_floors_by_pid,
            planet_queue_counts=planet_queue_counts,
        )

    wiki_cost = Resources(**full_cost(game_data.building_cost(bid, target_level)))
    if storage_exceeds_cost(planet, bid, wiki_cost):
        return None, None

    queue_full = planet_queue_counts.get(pid, 0) >= max_build_slots
    is_remote = state.active_planet_id is not None and pid != int(state.active_planet_id)
    allow_planet = (not is_remote) or allow_multi_planet
    reason = f"AI-Planner #{seq}: build {b_name} L{target_level}"
    cost, missing, can_exec, href, out_reason = _resolve_build_execution(
        planet,
        offers_by_id=offers_by_id,
        bid=bid,
        target_level=target_level,
        wiki_cost=wiki_cost,
        is_remote=is_remote,
        allow_planet=allow_planet,
        queue_full=queue_full,
        reason=reason,
    )
    handled_planets.add(pid)
    return (
        PlannedAction(
            priority="P1",
            kind="BUILD" if can_exec else "WAIT_RESOURCES",
            planet_id=pid,
            planet_coords=str(planet.coords),
            target_id=bid,
            target_name=b_name,
            target_level=target_level,
            cost=cost,
            missing=missing,
            reason=out_reason,
            executable_now=can_exec,
            action_href=href,
        ),
        None,
    )


def _research_wiki_cost(r_info: Mapping[str, object], target_level: int) -> Resources:
    levels_raw = r_info.get("levels")
    levels_map: Mapping[object, object] = levels_raw if isinstance(levels_raw, Mapping) else {}
    lvl_row = levels_map.get(target_level)
    raw_cost = lvl_row.get("cost") if isinstance(lvl_row, Mapping) else None
    return Resources(**full_cost(raw_cost or {}))


def _resolve_research_execution(
    state: GameStateLike,
    lab_planet: PlanetLike,
    config: ReadConfigLike,
    *,
    r_info: Mapping[str, object],
    rid_num: int,
    target_level: int,
    offer: ResearchOfferLike | None,
    research_queue_busy: bool,
    allow_multi_planet: bool,
    reason: str,
) -> tuple[Resources, bool, str | None, str, int | None]:
    if not bool(config.ai_adaptive_execution):
        if offer is not None and coerce_int(offer.target_level, target_level) == target_level:
            cost = coerce_resources_obj(offer.cost)
            can_exec = (
                (not research_queue_busy)
                and bool(offer.can_research)
                and resources_can_afford(lab_planet.resources, cost)
            )
            href = (str(offer.research_href) or None) if (can_exec and offer.research_href) else None
            tid = coerce_optional_int(offer.research_id, rid_num)
        else:
            cost = _research_wiki_cost(r_info, target_level)
            can_exec, href, tid = False, None, rid_num
        out_reason = f"{reason} (research slot busy)" if research_queue_busy else reason
        return cost, can_exec, href, out_reason, tid

    allow_research = config_allow_research(config)
    is_lab_active = state.active_planet_id is not None and int(lab_planet.planet_id) == int(state.active_planet_id)
    is_remote_lab = state.active_planet_id is not None and int(lab_planet.planet_id) != int(state.active_planet_id)
    if is_lab_active and offer is not None and coerce_int(offer.target_level, target_level) == target_level:
        cost = coerce_resources_obj(offer.cost)
        can_exec = bool(
            allow_research
            and (not research_queue_busy)
            and bool(offer.can_research)
            and resources_can_afford(lab_planet.resources, cost)
        )
        href = (str(offer.research_href) or None) if (can_exec and offer.research_href) else None
        tid = coerce_optional_int(offer.research_id, rid_num)
    else:
        cost = _research_wiki_cost(r_info, target_level)
        can_exec = bool(
            allow_research
            and (not is_remote_lab or allow_multi_planet)
            and (not research_queue_busy)
            and (not resources_is_zero(cost))
            and resources_can_afford(lab_planet.resources, cost)
        )
        href, tid = None, rid_num

    if not allow_research:
        out_reason = f"{reason} (research disabled)"
    elif is_remote_lab and not allow_multi_planet:
        out_reason = f"{reason} (multi-planet disabled)"
    elif research_queue_busy:
        out_reason = f"{reason} (research slot busy)"
    else:
        out_reason = reason
    return cost, can_exec, href, out_reason, tid


def reconcile_research_step(
    state: GameStateLike,
    step: Mapping[str, object],
    config: ReadConfigLike,
    *,
    entity_key: str,
    target_level: int,
    seq: int,
    lab_planet: PlanetLike | None,
    in_flight_research: Mapping[str, int],
    research_queue_busy: bool,
    research_offers_by_key: Mapping[str, ResearchOfferLike],
    allow_multi_planet: bool,
) -> PlannedAction | None:
    """Reconcile a single ``RESEARCH`` step into a live ``PlannedAction``."""
    r_info = game_data.RESEARCH_BY_ID.get(entity_key)
    if not r_info or lab_planet is None:
        return None
    r_name = str(step.get("entity_name") or r_info["name"])
    rid_num = int(entity_key[1:])
    live_level = max(0, int((state.researches or {}).get(entity_key, 0) or 0))
    if max(live_level, in_flight_research.get(entity_key, 0)) >= target_level or target_level != live_level + 1:
        return None
    if game_data.missing_prerequisites(
        "research", entity_key, buildings=lab_planet.buildings, researches=state.researches
    ):
        return None

    cost, can_exec, href, out_reason, tid = _resolve_research_execution(
        state,
        lab_planet,
        config,
        r_info=r_info,
        rid_num=rid_num,
        target_level=target_level,
        offer=research_offers_by_key.get(entity_key),
        research_queue_busy=research_queue_busy,
        allow_multi_planet=allow_multi_planet,
        reason=f"AI-Planner #{seq}: research {r_name} L{target_level}",
    )
    return PlannedAction(
        priority="P1",
        kind="RESEARCH",
        planet_id=int(lab_planet.planet_id),
        planet_coords=str(lab_planet.coords),
        target_id=tid,
        target_name=r_name,
        target_level=target_level,
        cost=cost,
        missing=resources_missing(lab_planet.resources, cost),
        reason=out_reason,
        executable_now=can_exec,
        action_href=href,
    )
