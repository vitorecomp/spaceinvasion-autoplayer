"""Action constructors, power selection, transmitter, payback, and prerequisite chain helpers."""

from __future__ import annotations

from collections.abc import Mapping

from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import (
    BuildingOffer,
    GameState,
    PlanetState,
    ResearchOffer,
    Resources,
    homeworld_planet_id,
    normalize_planet_type,
)
from spaceinvasion.planner.heuristics._common import (
    _is_building_allowed_on_planet,
    _is_stale_remote_resource_wait,
    _norm_planet_type,
    _planet_has_free_building_fields,
    _resources_from_dict,
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._queues import _eff_level
from spaceinvasion.planner.heuristics._types import (
    FUSION_PLANT,
    RESEARCH_LAB,
    SPICE_MINE,
    THERMOSOLAR_PLANT,
    PlannedAction,
)


def _format_build_block_reason(
    planet: PlanetState,
    reason: str,
    *,
    allowed_on_planet: bool,
    has_free_fields: bool,
    unmet_prereqs: list[dict[str, object]],
) -> str | None:
    if not allowed_on_planet:
        return f"{reason} (building not permitted on {normalize_planet_type(planet)})"
    if not has_free_fields:
        f_used_s = catalog.fmt_compact_int(max(0, _safe_strategy_int(planet.fields_used, 0)))
        f_max_s = catalog.fmt_compact_int(_safe_strategy_int(planet.fields_max, 0))
        return f"{reason} (no free building fields: {f_used_s}/{f_max_s})"
    if unmet_prereqs:
        first_req = unmet_prereqs[0]
        cur_s = catalog.fmt_compact_int(_safe_strategy_int(first_req.get("current", 0), 0))
        req_s = catalog.fmt_compact_int(_safe_strategy_int(first_req.get("required", 0), 0))
        return f"{reason} (requires {first_req.get('name')} {cur_s}/{req_s})"
    return None


def _make_build_action(
    priority: str,
    planet: PlanetState,
    building_id: int,
    fallback_name: str,
    reason: str,
    offers: Mapping[int, BuildingOffer],
    *,
    effective_level: int | None = None,
    researches: Mapping[str, int] | None = None,
    allow_remote_colony_build: bool = False,
) -> PlannedAction:
    offer = offers.get(building_id)
    allowed = _is_building_allowed_on_planet(planet, building_id, offers)
    has_fields = _planet_has_free_building_fields(planet)
    live_lvl = int(planet.building_level(building_id))
    cur_lvl = max(0, _safe_strategy_int(effective_level, 0)) if effective_level is not None else live_lvl
    nxt_lvl = cur_lvl + 1
    wiki_cost = _resources_from_dict(catalog.building_cost(building_id, nxt_lvl))
    unmet_prereqs = catalog.missing_prerequisites(
        "buildings", building_id, buildings=planet.buildings, researches=researches or {}
    )
    prereq_ok = not unmet_prereqs
    block_reason = _format_build_block_reason(
        planet, reason, allowed_on_planet=allowed, has_free_fields=has_fields, unmet_prereqs=unmet_prereqs
    )
    offer_tgt = catalog.safe_optional_int(offer.target_level, None) if offer is not None else None
    offer_matches = offer is not None and offer_tgt is not None and offer_tgt == nxt_lvl

    if offer is None or not offer_matches:
        missing = planet.resources.missing(wiki_cost) if not wiki_cost.is_zero() else Resources()
        offer_behind = offer is not None and offer_tgt is not None and offer_tgt <= live_lvl
        remote_ready = (
            (offer is None or offer_behind)
            and allowed
            and has_fields
            and allow_remote_colony_build
            and not wiki_cost.is_zero()
            and bool(planet.resources.can_afford(wiki_cost))
            and prereq_ok
        )
        if block_reason is not None:
            out_reason = block_reason
        elif remote_ready:
            out_reason = reason
        elif offer is not None:
            out_reason = f"{reason} (future/queued upgrade)"
        else:
            out_reason = f"{reason} (building card not currently visible/unlocked)"
        return PlannedAction(
            priority=priority,
            kind="BUILD" if remote_ready else "WAIT_RESOURCES",
            planet_id=planet.planet_id,
            planet_coords=_safe_strategy_str(planet.coords),
            target_id=building_id,
            target_name=_safe_strategy_str(fallback_name),
            target_level=nxt_lvl,
            cost=wiki_cost,
            missing=missing,
            reason=out_reason,
            executable_now=remote_ready,
            action_href=None,
        )

    missing = planet.resources.missing(offer.cost)
    stale_unlock = allow_remote_colony_build and _is_stale_remote_resource_wait(offer) and not offer.cost.is_zero()
    can_exec = (
        allowed
        and has_fields
        and prereq_ok
        and (offer.can_build or stale_unlock)
        and bool(planet.resources.can_afford(offer.cost))
    )
    return PlannedAction(
        priority=priority,
        kind="BUILD" if can_exec else "WAIT_RESOURCES",
        planet_id=planet.planet_id,
        planet_coords=_safe_strategy_str(planet.coords),
        target_id=catalog.safe_optional_int(offer.building_id, building_id),
        target_name=_safe_strategy_str(offer.name) or _safe_strategy_str(fallback_name),
        target_level=offer_tgt,
        cost=offer.cost,
        missing=missing,
        reason=block_reason if block_reason is not None else reason,
        executable_now=can_exec,
        action_href=(_safe_strategy_str(offer.build_href) or None) if (can_exec and offer.can_build) else None,
    )


def _pick_best_power_building(
    planet: PlanetState,
    eff_buildings: Mapping[str, int],
    researches: Mapping[str, int],
    *,
    forbid_planetary_solar: bool = False,
) -> tuple[int, str, str]:
    """Select Fusion plant (`b5`) by default, allowing Thermosolar (`b6`) on deep-cold colonies when allowed."""
    if forbid_planetary_solar and _norm_planet_type(planet) != "asteroid":
        return FUSION_PLANT, "Fusion plant", "No-fuel planetary energy generation"
    temp_c = _safe_strategy_int(planet.temperature, 0)
    spice_lvl = _eff_level(planet, eff_buildings, SPICE_MINE)
    thermo_lvl = _eff_level(planet, eff_buildings, THERMOSOLAR_PLANT)
    spacious = _safe_strategy_int(planet.fields_max, 0) <= 0 or _safe_strategy_int(planet.fields_remaining, 0) > 40
    if temp_c <= -100 and spice_lvl >= 12 and spacious and thermo_lvl < 6:
        unmet = catalog.missing_prerequisites(
            "buildings", THERMOSOLAR_PLANT, buildings=eff_buildings, researches=researches
        )
        if not unmet:
            fusion_lvl = _eff_level(planet, eff_buildings, FUSION_PLANT)
            fusion_delta = max(1, catalog.building_energy_delta(FUSION_PLANT, fusion_lvl + 1))
            fusion_cpe = catalog.spice_equivalent(catalog.building_cost(FUSION_PLANT, fusion_lvl + 1)) / fusion_delta
            thermo_row = catalog.building_level_stats(THERMOSOLAR_PLANT, thermo_lvl + 1)
            thermo_delta = max(0, catalog.building_energy_delta(THERMOSOLAR_PLANT, thermo_lvl + 1))
            spice_burn = _safe_strategy_float(thermo_row.get("spice_used", 0), 0.0)
            spice_prod = catalog.mine_production_per_hour(
                SPICE_MINE,
                spice_lvl,
                temperature=temp_c,
                mining_tech_level=max(0, _safe_strategy_int((researches or {}).get("r1", 0), 0)),
            )
            if thermo_delta > 0 and spice_burn <= spice_prod * 0.5:
                thermo_cost = catalog.spice_equivalent(catalog.building_cost(THERMOSOLAR_PLANT, thermo_lvl + 1))
                thermo_cpe = (thermo_cost + spice_burn * 12.0) / thermo_delta
                if thermo_cpe < fusion_cpe * 0.70:
                    return (
                        THERMOSOLAR_PLANT,
                        "Thermosolar plant",
                        f"Cheaper deep-cold power than Fusion ({thermo_cpe:.1f} vs {fusion_cpe:.1f} Spice/energy)",
                    )
    return FUSION_PLANT, "Fusion plant", "No-fuel planetary energy generation"


def _make_research_action(
    priority: str,
    planet: PlanetState,
    state: GameState,
    res_key: object,
    reason: object,
    research_offers: Mapping[str, ResearchOffer],
    research_queue_busy: bool,
    *,
    allow_live: bool,
) -> PlannedAction:
    rkey_s = _safe_strategy_str(res_key)
    offer = research_offers.get(rkey_s)
    info = catalog.RESEARCH_BY_ID.get(rkey_s)
    cur_lvl = max(0, _safe_strategy_int((state.researches or {}).get(rkey_s, 0), 0))
    nxt_lvl = cur_lvl + 1
    name = (_safe_strategy_str(offer.name) if offer and offer.name else "") or (
        str(info["name"]) if info else _safe_strategy_str(state.research_names.get(rkey_s, rkey_s))
    )
    rid_num = (
        int(rkey_s[1:])
        if rkey_s.startswith("r") and len(rkey_s) <= 8 and rkey_s[1:].isascii() and rkey_s[1:].isdigit()
        else None
    )
    ptype = _norm_planet_type(planet)
    has_lab = planet.building_level(RESEARCH_LAB) >= 1
    allowed = ptype != "raid" and has_lab
    unmet = (
        catalog.missing_prerequisites("research", rkey_s, buildings=planet.buildings, researches=state.researches)
        if rkey_s and rkey_s in catalog.RESEARCH_BY_ID
        else []
    )
    prereqs_ok = not unmet
    if ptype == "raid":
        block_note = " (research not permitted on raid)"
    elif not has_lab:
        block_note = " (Research lab b8 required on asteroid)" if ptype == "asteroid" else " (Research lab b8 required)"
    elif not prereqs_ok:
        first_req = unmet[0]
        cur_s = catalog.fmt_compact_int(_safe_strategy_int(first_req.get("current", 0), 0))
        req_s = catalog.fmt_compact_int(_safe_strategy_int(first_req.get("required", 0), 0))
        block_note = f" (requires {first_req.get('name')} {cur_s}/{req_s})"
    elif research_queue_busy:
        block_note = " (research slot busy)"
    else:
        block_note = ""

    p_coords_s = _safe_strategy_str(planet.coords)
    reason_s = _safe_strategy_str(reason)
    if offer is not None:
        offer_tgt = catalog.safe_optional_int(offer.target_level, None)
        can_exec = (
            allowed
            and prereqs_ok
            and allow_live
            and offer_tgt == nxt_lvl
            and bool(offer.can_research)
            and not research_queue_busy
            and bool(planet.resources.can_afford(offer.cost))
        )
        return PlannedAction(
            priority=priority,
            kind="RESEARCH",
            planet_id=planet.planet_id,
            planet_coords=p_coords_s,
            target_id=catalog.safe_optional_int(offer.research_id, rid_num),
            target_name=name,
            target_level=offer_tgt if offer_tgt is not None else nxt_lvl,
            cost=offer.cost,
            missing=planet.resources.missing(offer.cost),
            reason=f"{reason_s}{block_note}",
            executable_now=can_exec,
            action_href=(_safe_strategy_str(offer.research_href) or None) if can_exec else None,
        )

    levels_raw = (info or {}).get("levels")
    lvl_entry = levels_raw.get(nxt_lvl) if isinstance(levels_raw, Mapping) else None
    wiki_cost = _resources_from_dict(lvl_entry.get("cost", {}) if isinstance(lvl_entry, Mapping) else {})
    return PlannedAction(
        priority=priority,
        kind="RESEARCH",
        planet_id=planet.planet_id,
        planet_coords=p_coords_s,
        target_id=rid_num,
        target_name=name,
        target_level=nxt_lvl,
        cost=wiki_cost,
        missing=planet.resources.missing(wiki_cost),
        reason=f"{reason_s}{block_note}" if (not allowed or not prereqs_ok) else reason_s,
        executable_now=False,
    )


def _plan_transmitter_action(
    state: GameState,
    cfg: StrategyConfig,
    transmitter_status: Mapping[str, object] | None = None,
) -> PlannedAction | None:
    """Emit once-a-day free Interdimensional Transmitter (`ACTIVATE_TRANSMITTER`) action when due."""
    if state.tutorial is not None and state.tutorial.can_complete:
        return None
    eff_tm: Mapping[str, object] | None = transmitter_status if isinstance(transmitter_status, Mapping) else None
    if eff_tm is None:
        state_rs = getattr(state, "raid_stats", None)
        if isinstance(state_rs, Mapping):
            cand_tm = state_rs.get("transmitter_status")
            if isinstance(cand_tm, Mapping):
                eff_tm = cand_tm
    if eff_tm is None or not bool(eff_tm.get("due", False)):
        return None
    hw_pid = homeworld_planet_id(state)
    target_planet = (
        next((p for p in state.planets if hw_pid is not None and p.planet_id == hw_pid), None)
        or state.active_planet()
        or (state.planets[0] if state.planets else None)
    )
    if target_planet is None or not bool(getattr(cfg, "allow_transmitter", False)):
        return None
    free_rem = eff_tm.get("free_spins_remaining")
    charges_hint = (
        f" ({_safe_strategy_int(free_rem, 1)} free charge(s) ready)"
        if isinstance(free_rem, int) and not isinstance(free_rem, bool) and free_rem > 0
        else ""
    )
    hw_coords = _safe_strategy_str(target_planet.coords)
    return PlannedAction(
        priority="P2",
        kind="ACTIVATE_TRANSMITTER",
        planet_id=target_planet.planet_id,
        planet_coords=hw_coords,
        target_name="Interdimensional Transmitter",
        reason=(
            f"Collect Command Central daily bonus & activate free Interdimensional Transmitter spin "
            f"on Homeworld [{hw_coords}]{charges_hint} (0 Urplasma)"
        ),
        executable_now=True,
    )
