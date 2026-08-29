"""Priority-ordered decision planner for SpaceInvasion Classic."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import os
import re
import time
from typing import Any, Mapping, Optional, Sequence

from spaceinvasion.core import game_data
from spaceinvasion.core.config import StrategyConfig, normalize_planner_mode
from spaceinvasion.core.state import (
    RAID_COLONY_FORBIDDEN_BUILDINGS,
    BuildingOffer,
    EnergyBalance,
    GameState,
    PlanetState,
    ResearchOffer,
    Resources,
    UnitOffer,
    is_building_allowed_on_planet_type,
    is_in_flight_fleet_job,
    normalize_planet_type,
)

SHIP_SLUG_BY_KEY: dict[str, str] = {
    "s1": "small-transport",
    "s2": "large-transport",
    "s3": "transmitter",
    "s4": "small-fighter",
    "s5": "warship",
    "s6": "starcruiser",
    "s7": "phoenix",
    "s8": "colony-ship",
    "s9": "solar-cells",
    "s10": "spy-probe",
    "s11": "recycler",
    "s13": "battleship",
    "s14": "stealth-bomber",
    "s15": "destroyer",
    "s16": "imperial-starbase",
}

# SpaceInvasion building IDs matching `internalBuildings`
FUSION_PLANT = 5
THERMOSOLAR_PLANT = 6
PIG_IRON_MINE = 1
BLAST_FURNACE = 2
KRYPTONITE_MINE = 3
SPICE_MINE = 4
DEVELOPMENT_CENTER = 7
RESEARCH_LAB = 8
WEAPONS_FACTORY = 9
FLEET_BASE = 10
PIG_IRON_WAREHOUSE = 11
METAL_WAREHOUSE = 12
KRYPTONITE_WAREHOUSE = 13
SPICE_WAREHOUSE = 14

SOLAR_CELLS_ID = 9

_MINE_IDS = (PIG_IRON_MINE, BLAST_FURNACE, KRYPTONITE_MINE, SPICE_MINE)
_MINE_RES_KEY = {
    PIG_IRON_MINE: "pig_iron",
    BLAST_FURNACE: "metal",
    KRYPTONITE_MINE: "kryptonite",
    SPICE_MINE: "spice",
}
_MINE_LABELS = {
    PIG_IRON_MINE: "Pig iron mine",
    BLAST_FURNACE: "Blast furnace",
    KRYPTONITE_MINE: "Kryptonite mine",
    SPICE_MINE: "Spice mine",
}
_WAREHOUSE_BY_RESOURCE = {
    "pig_iron": (PIG_IRON_WAREHOUSE, "Pig iron warehouse"),
    "metal": (METAL_WAREHOUSE, "Metal warehouse"),
    "kryptonite": (KRYPTONITE_WAREHOUSE, "Kryptonite warehouse"),
    "spice": (SPICE_WAREHOUSE, "Spice warehouse"),
}


@dataclass(frozen=True)
class PlannedAction:
    """Single recommended or blocked empire action produced by the planner."""

    priority: str  # P0 (survival), P1 (safety), P2 (tutorial/economy/fleet/combat), P3 (expansion)
    kind: str      # ALERT_HOSTILE, ESCAPE_FLIGHT, BUILD, WAIT_RESOURCES, RESEARCH, BUILD_SHIP, SCAN_GALAXY, ATTACK, THROTTLE_BUILDING
    planet_id: int
    planet_coords: str
    target_name: str
    reason: str
    target_id: Optional[int] = None
    target_level: Optional[int] = None  # Or unit quantity for BUILD_SHIP
    cost: Resources = Resources()
    missing: Resources = Resources()
    executable_now: bool = False
    action_href: Optional[str] = None
    wait_hours: Optional[float] = 0.0
    wait_seconds: Optional[float] = 0.0
    wait_eta: str = "Ready"
    duration_seconds: float = 0.0
    duration_text: str = "Instant"
    total_hours: Optional[float] = 0.0
    total_seconds: Optional[float] = 0.0
    total_eta: str = "Ready"
    eta_bottleneck: Optional[str] = None
    manual_queue_id: Optional[int] = None
    manual_sort_rank: Optional[int] = None


_fleet_mod: Any = None


def _get_fleet() -> Any:
    global _fleet_mod
    if _fleet_mod is None:
        from spaceinvasion.combat import fleet as _loaded_fleet

        _fleet_mod = _loaded_fleet
    return _fleet_mod


def attach_action_etas(state: GameState, actions: list[PlannedAction]) -> list[PlannedAction]:
    from spaceinvasion.combat.ranking import attach_action_etas as _attach_etas

    return _attach_etas(state, actions)


_FLEET_REEXPORTS: frozenset[str] = frozenset(
    {
        "BashTracker",
        "GalaxySlot",
        "SpyReport",
        "normalize_coords",
        "plan_escape_flight",
        "score_raid_target",
    }
)


def __getattr__(name: str) -> Any:
    if name in _FLEET_REEXPORTS:
        return getattr(_get_fleet(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _resources_from_dict(raw: dict[str, int]) -> Resources:
    if not isinstance(raw, Mapping):
        return Resources()
    return Resources(
        pig_iron=max(0, _safe_strategy_int(raw.get("pig_iron", 0), 0)),
        metal=max(0, _safe_strategy_int(raw.get("metal", 0), 0)),
        kryptonite=max(0, _safe_strategy_int(raw.get("kryptonite", 0), 0)),
        spice=max(0, _safe_strategy_int(raw.get("spice", 0), 0)),
    )


def _offer_map(planet: PlanetState) -> dict[int, BuildingOffer]:
    out: dict[int, BuildingOffer] = {}
    for offer in getattr(planet, "building_offers", ()) or ():
        bid = game_data.safe_optional_int(getattr(offer, "building_id", None), None)
        if bid is not None:
            out[bid] = offer
    return out


def _research_offer_map(state: GameState) -> dict[str, ResearchOffer]:
    out: dict[str, ResearchOffer] = {}
    for offer in getattr(state, "research_offers", ()) or ():
        rkey = game_data.safe_key(getattr(offer, "research_key", "") or "")
        if rkey:
            out[rkey] = offer
    return out


def _ship_offer_map(planet: PlanetState) -> dict[int, UnitOffer]:
    out: dict[int, UnitOffer] = {}
    for offer in getattr(planet, "ship_offers", ()) or ():
        uid = game_data.safe_optional_int(getattr(offer, "unit_id", None), None)
        if uid is not None:
            out[uid] = offer
    return out


def _apply_queue_cap_if_full(action: PlannedAction, queue_full: bool) -> PlannedAction:
    if not (queue_full and action.executable_now):
        return action
    return PlannedAction(
        priority=action.priority,
        kind="WAIT_RESOURCES",
        planet_id=action.planet_id,
        planet_coords=action.planet_coords,
        target_id=action.target_id,
        target_name=action.target_name,
        target_level=action.target_level,
        cost=action.cost,
        missing=action.missing,
        reason=f"{_safe_strategy_str(action.reason)} (build queue full)",
        executable_now=False,
    )


_RAID_COLONY_FORBIDDEN_BUILDINGS: frozenset[int] = RAID_COLONY_FORBIDDEN_BUILDINGS
_SLUG_TO_SHIP_KEY: dict[str, str] = {slug: skey for skey, slug in SHIP_SLUG_BY_KEY.items()}


def _resolve_shipyard_targets(raw_targets: Any) -> dict[str, int]:
    if not isinstance(raw_targets, Mapping):
        return {}
    resolved: dict[str, int] = {}
    for raw_k, raw_v in raw_targets.items():
        key_s = _safe_strategy_str(raw_k).strip()
        if not key_s or (key_s.isascii() and key_s.isdigit()):
            continue
        cnt = _safe_strategy_int(raw_v, 0)
        low_s = key_s.lower()
        canon_slug: Optional[str] = None
        if low_s in SHIP_SLUG_BY_KEY:
            canon_slug = SHIP_SLUG_BY_KEY[low_s]
        else:
            unit_info = game_data.resolve_unit("ships", key_s)
            if unit_info:
                u_slug = unit_info.get("slug")
                if isinstance(u_slug, str) and u_slug in _SLUG_TO_SHIP_KEY:
                    canon_slug = u_slug
        if canon_slug is None:
            continue
        resolved[canon_slug] = cnt
    return resolved


def _norm_planet_type(planet: Optional[PlanetState]) -> str:
    return normalize_planet_type(planet)


def _is_building_allowed_on_planet(
    planet: PlanetState,
    building_id: int,
    offers: Optional[Mapping[int, BuildingOffer] | Sequence[BuildingOffer]] = None,
) -> bool:
    """Enforce REQ-BLD-003 building eligibility for asteroids, raid colonies, and normal planets."""
    if isinstance(offers, Mapping):
        has_b8 = RESEARCH_LAB in offers
    elif offers:
        has_b8 = any(
            game_data.safe_optional_int(getattr(o, "building_id", None), None) == RESEARCH_LAB
            for o in offers
        )
    else:
        has_b8 = False
    return is_building_allowed_on_planet_type(
        planet,
        building_id,
        has_b8_offer=has_b8,
    )


def _planet_has_free_building_fields(planet: PlanetState) -> bool:
    """Return False when `fields_max > 0` and `fields_used >= fields_max`; preserve `fields_max <= 0` test fixtures."""
    f_max = _safe_strategy_int(getattr(planet, "fields_max", 0), 0)
    if f_max <= 0:
        return True
    f_used = max(0, _safe_strategy_int(getattr(planet, "fields_used", 0), 0))
    return f_used < f_max


def _make_build_action(
    priority: str,
    planet: PlanetState,
    building_id: int,
    fallback_name: str,
    reason: str,
    offers: dict[int, BuildingOffer],
    *,
    effective_level: Optional[int] = None,
    researches: Optional[dict[str, int]] = None,
    allow_remote_colony_build: bool = False,
) -> PlannedAction:
    offer = offers.get(building_id)
    allowed_on_planet = _is_building_allowed_on_planet(planet, building_id, offers)
    has_free_fields = _planet_has_free_building_fields(planet)
    cur_lvl = (
        max(0, _safe_strategy_int(effective_level, 0))
        if effective_level is not None
        else planet.building_level(building_id)
    )
    nxt_lvl = cur_lvl + 1
    wiki_cost = _resources_from_dict(game_data.building_cost(building_id, nxt_lvl))
    unmet_prereqs = game_data.missing_prerequisites(
        "buildings",
        building_id,
        buildings=planet.buildings,
        researches=researches or {},
    )
    prereq_ok = not unmet_prereqs
    offer_tgt = game_data.safe_optional_int(offer.target_level, None) if offer is not None else None
    offer_matches = offer is not None and offer_tgt is not None and offer_tgt == nxt_lvl

    if offer is None or not offer_matches:
        missing = planet.resources.missing(wiki_cost) if not wiki_cost.is_zero() else Resources()
        remote_ready = (
            offer is None
            and allowed_on_planet
            and has_free_fields
            and allow_remote_colony_build
            and not wiki_cost.is_zero()
            and planet.resources.can_afford(wiki_cost)
            and prereq_ok
        )
        if not allowed_on_planet:
            out_reason = f"{reason} (building not permitted on {normalize_planet_type(planet)})"
        elif not has_free_fields:
            f_used_s = game_data.fmt_compact_int(max(0, _safe_strategy_int(planet.fields_used, 0)))
            f_max_s = game_data.fmt_compact_int(_safe_strategy_int(planet.fields_max, 0))
            out_reason = f"{reason} (no free building fields: {f_used_s}/{f_max_s})"
        elif not prereq_ok:
            first_req = unmet_prereqs[0]
            out_reason = (
                f"{reason} (requires {first_req['name']} "
                f"{game_data.fmt_compact_int(first_req['current'])}/{game_data.fmt_compact_int(first_req['required'])})"
            )
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
    can_exec = (
        allowed_on_planet
        and has_free_fields
        and prereq_ok
        and offer.can_build
        and planet.resources.can_afford(offer.cost)
    )
    offer_bid = game_data.safe_optional_int(offer.building_id, building_id)
    if not allowed_on_planet:
        out_reason = f"{reason} (building not permitted on {normalize_planet_type(planet)})"
    elif not has_free_fields:
        f_used_s = game_data.fmt_compact_int(max(0, _safe_strategy_int(planet.fields_used, 0)))
        f_max_s = game_data.fmt_compact_int(_safe_strategy_int(planet.fields_max, 0))
        out_reason = f"{reason} (no free building fields: {f_used_s}/{f_max_s})"
    elif not prereq_ok:
        first_req = unmet_prereqs[0]
        out_reason = (
            f"{reason} (requires {first_req['name']} "
            f"{game_data.fmt_compact_int(first_req['current'])}/{game_data.fmt_compact_int(first_req['required'])})"
        )
    else:
        out_reason = reason
    return PlannedAction(
        priority=priority,
        kind="BUILD" if can_exec else "WAIT_RESOURCES",
        planet_id=planet.planet_id,
        planet_coords=_safe_strategy_str(planet.coords),
        target_id=offer_bid,
        target_name=_safe_strategy_str(offer.name) or _safe_strategy_str(fallback_name),
        target_level=offer_tgt,
        cost=offer.cost,
        missing=missing,
        reason=out_reason,
        executable_now=can_exec,
        action_href=(_safe_strategy_str(offer.build_href) or None) if can_exec else None,
    )


def _queued_building_counts(state: GameState) -> dict[int, int]:
    """Count active/queued construction jobs per building ID."""
    counts: dict[int, int] = {}
    for job in state.active_queues:
        if job.category == "building" and job.building_id is not None:
            bid = game_data.safe_optional_int(job.building_id, None)
            if bid is not None:
                counts[bid] = counts.get(bid, 0) + 1
    return counts


def _safe_strategy_int(val: Any, default: int = 0) -> int:
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")
    if isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii() or txt.lower() in ("null", "none", "~"):
            return default
    return game_data.safe_int(val, default=default)


def _safe_strategy_float(val: Any, default: float = 0.0) -> float:
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")
    if isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii() or txt.lower() in ("null", "none", "~"):
            return default
    return game_data.safe_finite_float(val, default=default)


def _safe_strategy_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except Exception:
            return game_data.safe_key(val)
        if val is None:
            return ""
    if isinstance(val, str):
        return val
    if isinstance(val, int) and not isinstance(val, bool):
        return game_data.fmt_compact_int(val) if abs(val).bit_length() >= 12_000 else str(val)
    if isinstance(val, (bytes, bytearray)):
        return bytes(val).decode("utf-8", errors="replace")
    return game_data.safe_key(val)


def _resolve_storage_warn_ratio(cfg: StrategyConfig, override: Optional[float]) -> float:
    cfg_val = _safe_strategy_float(getattr(cfg, "storage_warn_ratio", 0.85), 0.85)
    if cfg_val <= 0.0:
        cfg_val = 0.85
    cfg_val = max(0.05, min(1.0, cfg_val))
    if override is None:
        return cfg_val
    val = _safe_strategy_float(override, cfg_val)
    if val <= 0.0:
        val = cfg_val
    return max(0.05, min(1.0, val))


def _max_build_slots(planet: PlanetState, state: GameState) -> int:
    if _norm_planet_type(planet) == "asteroid":
        return 1
    specs = getattr(state, "specs", None)
    if isinstance(specs, Mapping) and _safe_strategy_int(specs.get("construction_commander", 0), 0) > 0:
        return 6
    return 5


def _effective_building_levels_and_energy(
    state: GameState,
    planet: PlanetState,
    *,
    is_active_planet: bool,
) -> tuple[dict[str, int], int]:
    """Project building levels and energy net after active queues complete."""
    sim_levels: dict[int, int] = {}
    eff_map: dict[str, int] = {
        game_data.safe_key(k): max(0, _safe_strategy_int(v, 0))
        for k, v in (planet.buildings or {}).items()
    }
    projected_net = _safe_strategy_int(getattr(planet.energy, "net", 0), 0)

    if not is_active_planet:
        return eff_map, projected_net

    for job in state.active_queues:
        if job.category != "building" or job.building_id is None:
            continue
        bid = game_data.safe_optional_int(job.building_id, None)
        if bid is None:
            continue
        cur_lvl = sim_levels.get(bid, planet.building_level(bid))
        j_tgt = game_data.safe_optional_int(job.target_level, None)
        if j_tgt is not None and 0 <= j_tgt < cur_lvl:
            tgt = j_tgt
            max_tbl = max(
                _safe_strategy_int((game_data.BUILDING_BY_ID.get(bid) or {}).get("max_table_level", 100), 100),
                100,
            )
            step_hi = min(cur_lvl, max_tbl + 1)
            for step_lvl in range(tgt + 1, step_hi + 1):
                projected_net -= game_data.building_energy_delta(bid, step_lvl)
        else:
            tgt = j_tgt if (j_tgt is not None and j_tgt > cur_lvl) else cur_lvl + 1
            max_tbl = max(
                _safe_strategy_int((game_data.BUILDING_BY_ID.get(bid) or {}).get("max_table_level", 100), 100),
                100,
            )
            step_hi = min(tgt, max_tbl + 1)
            for step_lvl in range(cur_lvl + 1, step_hi + 1):
                projected_net += game_data.building_energy_delta(bid, step_lvl)
        sim_levels[bid] = tgt
        eff_map[f"b{bid}"] = tgt

    return eff_map, projected_net


def _eff_level(planet: PlanetState, eff_buildings: dict[str, int], building_id: int) -> int:
    key = f"b{building_id}"
    return max(0, _safe_strategy_int(eff_buildings[key], 0)) if key in eff_buildings else planet.building_level(building_id)


def _energy_starvation_delta(building_id: int, next_level: int, projected_energy_net: int) -> int:
    delta = game_data.building_energy_delta(building_id, next_level)
    if delta < 0 and (projected_energy_net + delta) < 0:
        return delta
    return 0


def _pick_best_power_building(
    planet: PlanetState,
    eff_buildings: dict[str, int],
    researches: dict[str, int],
    *,
    forbid_planetary_solar: bool = False,
) -> tuple[int, str, str]:
    """Select Fusion plant (`b5`) by default, allowing Thermosolar (`b6`) only when `forbid_planetary_solar=False` on deep-cold colonies."""
    if forbid_planetary_solar and _norm_planet_type(planet) != "asteroid":
        return FUSION_PLANT, "Fusion plant", "No-fuel planetary energy generation"
    temp_c = _safe_strategy_int(planet.temperature, 0)
    spice_lvl = _eff_level(planet, eff_buildings, SPICE_MINE)
    thermo_lvl = _eff_level(planet, eff_buildings, THERMOSOLAR_PLANT)
    spacious_for_b6 = (
        _safe_strategy_int(planet.fields_max, 0) <= 0
        or _safe_strategy_int(planet.fields_remaining, 0) > 40
    )
    if temp_c <= -100 and spice_lvl >= 12 and spacious_for_b6 and thermo_lvl < 6:
        thermo_unmet = game_data.missing_prerequisites(
            "buildings",
            THERMOSOLAR_PLANT,
            buildings=eff_buildings,
            researches=researches,
        )
        if not thermo_unmet:
            fusion_lvl = _eff_level(planet, eff_buildings, FUSION_PLANT)
            fusion_delta = max(1, game_data.building_energy_delta(FUSION_PLANT, fusion_lvl + 1))
            fusion_cost_sp = game_data.spice_equivalent(game_data.building_cost(FUSION_PLANT, fusion_lvl + 1))
            fusion_cpe = fusion_cost_sp / fusion_delta
            thermo_row = game_data.building_level_stats(THERMOSOLAR_PLANT, thermo_lvl + 1)
            thermo_delta = max(0, game_data.building_energy_delta(THERMOSOLAR_PLANT, thermo_lvl + 1))
            spice_burn = _safe_strategy_float(thermo_row.get("spice_used", 0), 0.0)
            spice_prod = game_data.mine_production_per_hour(
                SPICE_MINE,
                spice_lvl,
                temperature=temp_c,
                mining_tech_level=max(0, _safe_strategy_int((researches or {}).get("r1", 0), 0)),
            )
            if thermo_delta > 0 and spice_burn <= spice_prod * 0.5:
                thermo_cost_sp = game_data.spice_equivalent(
                    game_data.building_cost(THERMOSOLAR_PLANT, thermo_lvl + 1)
                ) + spice_burn * 12.0
                thermo_cpe = thermo_cost_sp / thermo_delta
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
    res_key: Any,
    reason: Any,
    research_offers: dict[str, ResearchOffer],
    research_queue_busy: bool,
    *,
    allow_live: bool,
) -> PlannedAction:
    rkey_s = _safe_strategy_str(res_key)
    offer = research_offers.get(rkey_s)
    info = game_data.RESEARCH_BY_ID.get(rkey_s)
    cur_lvl = max(0, _safe_strategy_int((state.researches or {}).get(rkey_s, 0), 0))
    nxt_lvl = cur_lvl + 1
    name = (
        (_safe_strategy_str(offer.name) if offer and offer.name else "")
        or (str(info["name"]) if info else _safe_strategy_str(state.research_names.get(rkey_s, rkey_s)))
    )
    rid_num = (
        int(rkey_s[1:])
        if rkey_s.startswith("r") and len(rkey_s) <= 8 and rkey_s[1:].isascii() and rkey_s[1:].isdigit()
        else None
    )
    ptype = _norm_planet_type(planet)
    has_lab = planet.building_level(RESEARCH_LAB) >= 1
    allowed_on_planet = ptype != "raid" and has_lab
    unmet_prereqs = (
        game_data.missing_prerequisites(
            "research",
            rkey_s,
            buildings=planet.buildings,
            researches=state.researches,
        )
        if rkey_s and rkey_s in game_data.RESEARCH_BY_ID
        else []
    )
    prereqs_ok = not unmet_prereqs
    if ptype == "raid":
        block_note = " (research not permitted on raid)"
    elif not has_lab:
        block_note = (
            " (Research lab b8 required on asteroid)"
            if ptype == "asteroid"
            else " (Research lab b8 required)"
        )
    elif not prereqs_ok:
        first_req = unmet_prereqs[0]
        block_note = (
            f" (requires {first_req['name']} "
            f"{game_data.fmt_compact_int(first_req['current'])}/{game_data.fmt_compact_int(first_req['required'])})"
        )
    elif research_queue_busy:
        block_note = " (research slot busy)"
    else:
        block_note = ""
    p_coords_s = _safe_strategy_str(planet.coords)
    reason_s = _safe_strategy_str(reason)

    if offer is not None:
        offer_tgt = game_data.safe_optional_int(offer.target_level, None)
        can_exec = (
            allowed_on_planet
            and prereqs_ok
            and allow_live
            and offer_tgt == nxt_lvl
            and offer.can_research
            and not research_queue_busy
            and planet.resources.can_afford(offer.cost)
        )
        return PlannedAction(
            priority=priority,
            kind="RESEARCH",
            planet_id=planet.planet_id,
            planet_coords=p_coords_s,
            target_id=game_data.safe_optional_int(offer.research_id, rid_num),
            target_name=name,
            target_level=offer_tgt if offer_tgt is not None else nxt_lvl,
            cost=offer.cost,
            missing=planet.resources.missing(offer.cost),
            reason=f"{reason_s}{block_note}",
            executable_now=can_exec,
            action_href=(_safe_strategy_str(offer.research_href) or None) if can_exec else None,
        )

    wiki_cost = _resources_from_dict((info or {}).get("levels", {}).get(nxt_lvl, {}).get("cost", {}))
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
        reason=f"{reason_s}{block_note}" if (not allowed_on_planet or not prereqs_ok) else reason_s,
        executable_now=False,
    )


def _active_research_increments(state: GameState) -> dict[str, int]:
    """Return in-flight research increments (+1) keyed by canonical research ID (`r1`..`r16`)."""
    inc: dict[str, int] = {}
    for r_off in state.research_offers or ():
        if getattr(r_off, "active", False):
            rk = _safe_strategy_str(getattr(r_off, "research_key", "")).strip()
            if rk:
                inc[rk] = max(inc.get(rk, 0), 1)
    for q in state.active_queues or ():
        if getattr(q, "category", "") != "research":
            continue
        raw_name = _safe_strategy_str(getattr(q, "name", ""))
        clean_name = re.sub(r"\(.*?\)", "", raw_name)
        clean_name = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", "", clean_name)
        clean_name = re.sub(r"\b(?:level|lvl|l)\s*\d+\b", "", clean_name, flags=re.IGNORECASE).strip()
        if not clean_name:
            continue
        r_unit = game_data.resolve_unit("research", clean_name)
        if r_unit and r_unit.get("id"):
            rk = str(r_unit["id"])
            inc[rk] = max(inc.get(rk, 0), 1)
            continue
        low_clean = clean_name.lower()
        for r_off in state.research_offers or ():
            if _safe_strategy_str(getattr(r_off, "name", "")).strip().lower() == low_clean:
                rk = _safe_strategy_str(getattr(r_off, "research_key", "")).strip()
                if rk:
                    inc[rk] = max(inc.get(rk, 0), 1)
                break
    return inc


def _emit_tutorial_prereq_actions(
    *,
    tut_step_s: str,
    tut_total_s: str,
    category: str,
    slug_or_id: int | str,
    goal_label: str,
    state: GameState,
    build_planet: PlanetState,
    build_offers: dict[int, BuildingOffer],
    eff_buildings: dict[str, int],
    queue_full: bool,
    allow_remote_colony_build: bool,
    research_planet: Optional[PlanetState],
    research_offers: dict[str, ResearchOffer],
    research_queue_busy: bool,
    allow_live_research: bool,
    config: StrategyConfig,
    emitted_build_ids: set[tuple[int, int]],
    emitted_res_keys: set[str],
    eff_researches: Optional[dict[str, int]] = None,
) -> list[PlannedAction]:
    """Resolve leaf building/research prerequisites for a tutorial target and emit actionable P2 unlocks."""
    res_map = eff_researches if eff_researches is not None else dict(state.researches or {})
    steps = _resolve_prereq_chain(
        category,
        slug_or_id,
        goal_label,
        eff_buildings,
        res_map,
    )
    out: list[PlannedAction] = []
    for req in steps:
        req_cat = str(req.get("category") or "")
        if req_cat == "buildings":
            req_info = game_data.resolve_unit("buildings", req["slug"])
            if not req_info or req_info.get("id") is None:
                continue
            req_bid = int(req_info["id"])
            if (build_planet.planet_id, req_bid) in emitted_build_ids:
                continue
            if not _is_building_allowed_on_planet(build_planet, req_bid, build_offers):
                continue
            if not _planet_has_free_building_fields(build_planet):
                continue
            emitted_build_ids.add((build_planet.planet_id, req_bid))
            pr_reason = (
                f"Tutorial {tut_step_s}/{tut_total_s}: unlock {req['name']} "
                f"({game_data.fmt_compact_int(req['current'])}/{game_data.fmt_compact_int(req['required'])}) for {goal_label}"
            )
            act = _make_build_action(
                "P2",
                build_planet,
                req_bid,
                str(req["name"]),
                pr_reason,
                build_offers,
                effective_level=_eff_level(build_planet, eff_buildings, req_bid),
                researches=state.researches,
                allow_remote_colony_build=allow_remote_colony_build,
            )
            out.append(_apply_queue_cap_if_full(act, queue_full))
        elif req_cat == "research" and research_planet is not None:
            res_info = game_data.resolve_unit("research", req["slug"])
            rkey = str((res_info or {}).get("id") or "")
            if not rkey or rkey in emitted_res_keys:
                continue
            emitted_res_keys.add(rkey)
            pr_reason = (
                f"Tutorial {tut_step_s}/{tut_total_s}: unlock {req['name']} "
                f"({game_data.fmt_compact_int(req['current'])}/{game_data.fmt_compact_int(req['required'])}) for {goal_label}"
            )
            out.append(
                _make_research_action(
                    "P2",
                    research_planet,
                    state,
                    rkey,
                    pr_reason,
                    research_offers,
                    research_queue_busy,
                    allow_live=config.allow_research and allow_live_research,
                )
            )
    return out


def _plan_tutorial_actions(
    state: GameState,
    planet: PlanetState,
    offers: dict[int, BuildingOffer],
    research_offers: dict[str, ResearchOffer],
    queued_counts: dict[int, int],
    eff_buildings: dict[str, int],
    projected_net: int,
    queue_full: bool,
    research_queue_busy: bool,
    config: StrategyConfig,
    *,
    allow_remote_colony_build: bool = False,
    allow_live_research: bool = True,
) -> list[PlannedAction]:
    """Order incomplete tutorial building & research tasks with proactive power & prerequisite lookahead."""
    if not state.tutorial:
        return []

    ptype = _norm_planet_type(planet)
    lab_eff = _eff_level(planet, eff_buildings, RESEARCH_LAB)
    can_research_planet = ptype != "raid" and (
        ptype != "asteroid"
        or lab_eff >= 1
        or _is_building_allowed_on_planet(planet, RESEARCH_LAB, offers)
    )
    active_r_inc = _active_research_increments(state)
    eff_researches: dict[str, int] = {
        game_data.safe_key(k): max(0, _safe_strategy_int(v, 0))
        for k, v in (state.researches or {}).items()
    }
    for rk_i, delta_i in active_r_inc.items():
        eff_researches[rk_i] = eff_researches.get(rk_i, 0) + delta_i

    building_tasks = []
    research_tasks: list[tuple[Any, int]] = []
    for t in state.tutorial.tasks:
        if t.completed:
            continue
        t_cur = _safe_strategy_int(t.current, 0)
        t_tgt = _safe_strategy_int(t.target, 0)
        if t.category == "research" and t.research_id:
            rk_s = _safe_strategy_str(t.research_id).strip()
            eff_r_progress = t_cur + active_r_inc.get(rk_s, 0)
            if can_research_planet and eff_r_progress < t_tgt:
                research_tasks.append((t, eff_r_progress))
        elif t.category == "building" and t.building_id is not None:
            t_bid = game_data.safe_optional_int(t.building_id, None)
            if (
                t_bid is not None
                and t_bid > 0
                and _is_building_allowed_on_planet(planet, t_bid, offers)
            ):
                eff_progress = t_cur + queued_counts.get(t_bid, 0)
                if eff_progress < t_tgt:
                    building_tasks.append((t, eff_progress))

    if not building_tasks and not research_tasks:
        return []

    starving_task_note = ""
    has_fusion_task = any(
        game_data.safe_optional_int(t.building_id, None) == FUSION_PLANT for t, _ in building_tasks
    )
    for t, _eff in building_tasks:
        bid = game_data.safe_optional_int(t.building_id, 0) or 0
        if bid in (FUSION_PLANT, THERMOSOLAR_PLANT):
            continue
        nxt_lvl = _eff_level(planet, eff_buildings, bid) + 1
        starve_delta = _energy_starvation_delta(bid, nxt_lvl, projected_net)
        if starve_delta < 0:
            starving_task_note = (
                f" (proactive power: {_safe_strategy_str(t.building_name) or f'Building {bid}'} L{game_data.fmt_compact_int(nxt_lvl)} "
                f"needs {game_data.fmt_compact_int(-starve_delta)} energy, projected net {game_data.fmt_compact_int(projected_net, signed=True)})"
            )
            break

    need_power_first = projected_net <= 0 or bool(starving_task_note)

    def _sort_key(item) -> tuple[int, int, int]:
        task, eff = item
        bid = game_data.safe_optional_int(task.building_id, 99) or 99
        if bid == FUSION_PLANT and need_power_first:
            bucket = 0
        elif bid == DEVELOPMENT_CENTER:
            bucket = 1
        elif bid == FUSION_PLANT:
            # Defer non-urgent Fusion upgrades behind mines when ample spare energy exists
            bucket = 4
        else:
            bucket = 3
        return (bucket, eff, bid)

    ordered = sorted(building_tasks, key=_sort_key)
    actions: list[PlannedAction] = []
    tut_step_s = game_data.fmt_compact_int(state.tutorial.step)
    tut_total_s = game_data.fmt_compact_int(state.tutorial.total_steps)
    emitted_build_ids: set[tuple[int, int]] = set()
    emitted_res_keys: set[str] = set()

    if (
        need_power_first
        and not has_fusion_task
        and building_tasks
        and _safe_strategy_int(planet.energy.net, 0) >= 0
        and _planet_has_free_building_fields(planet)
        and _is_building_allowed_on_planet(planet, FUSION_PLANT, offers)
    ):
        fusion_act = _make_build_action(
            "P2",
            planet,
            FUSION_PLANT,
            "Fusion plant",
            f"Proactive power before tutorial mines{starving_task_note}",
            offers,
            effective_level=_eff_level(planet, eff_buildings, FUSION_PLANT),
            researches=state.researches,
            allow_remote_colony_build=allow_remote_colony_build,
        )
        actions.append(_apply_queue_cap_if_full(fusion_act, queue_full))
        emitted_build_ids.add((planet.planet_id, FUSION_PLANT))

    for task, eff in ordered:
        bid = game_data.safe_optional_int(task.building_id, None)
        if bid is None:
            continue
        b_label = _safe_strategy_str(task.building_name) or f"Building {bid}"
        prereq_acts = _emit_tutorial_prereq_actions(
            tut_step_s=tut_step_s,
            tut_total_s=tut_total_s,
            category="buildings",
            slug_or_id=bid,
            goal_label=b_label,
            state=state,
            build_planet=planet,
            build_offers=offers,
            eff_buildings=eff_buildings,
            queue_full=queue_full,
            allow_remote_colony_build=allow_remote_colony_build,
            research_planet=planet if can_research_planet else None,
            research_offers=research_offers,
            research_queue_busy=research_queue_busy,
            allow_live_research=allow_live_research,
            config=config,
            emitted_build_ids=emitted_build_ids,
            emitted_res_keys=emitted_res_keys,
            eff_researches=eff_researches,
        )
        actions.extend(prereq_acts)
        if prereq_acts and game_data.missing_prerequisites(
            "buildings", bid, buildings=planet.buildings, researches=state.researches
        ):
            continue
        if (planet.planet_id, bid) in emitted_build_ids:
            continue
        emitted_build_ids.add((planet.planet_id, bid))
        reason = (
            f"Tutorial {tut_step_s}/{tut_total_s}: "
            f"{_safe_strategy_str(task.description)} (progress {game_data.fmt_compact_int(eff)}/{game_data.fmt_compact_int(task.target)})"
        )
        if bid == FUSION_PLANT and need_power_first:
            reason += starving_task_note or " (energy first for 100% mine output)"
        act = _make_build_action(
            "P2",
            planet,
            bid,
            b_label,
            reason,
            offers,
            effective_level=_eff_level(planet, eff_buildings, bid),
            researches=state.researches,
            allow_remote_colony_build=allow_remote_colony_build,
        )
        actions.append(_apply_queue_cap_if_full(act, queue_full))

    if can_research_planet:
        for rtask, eff_r in research_tasks:
            rid = _safe_strategy_str(rtask.research_id).strip()
            if not rid:
                continue
            r_label = _safe_strategy_str(rtask.building_name) or rid
            prereq_acts = _emit_tutorial_prereq_actions(
                tut_step_s=tut_step_s,
                tut_total_s=tut_total_s,
                category="research",
                slug_or_id=rid,
                goal_label=r_label,
                state=state,
                build_planet=planet,
                build_offers=offers,
                eff_buildings=eff_buildings,
                queue_full=queue_full,
                allow_remote_colony_build=allow_remote_colony_build,
                research_planet=planet,
                research_offers=research_offers,
                research_queue_busy=research_queue_busy,
                allow_live_research=allow_live_research,
                config=config,
                emitted_build_ids=emitted_build_ids,
                emitted_res_keys=emitted_res_keys,
                eff_researches=eff_researches,
            )
            actions.extend(prereq_acts)
            if rid in emitted_res_keys:
                continue
            emitted_res_keys.add(rid)
            desc_s = _safe_strategy_str(rtask.description)
            reason = (
                f"Tutorial {tut_step_s}/{tut_total_s}: "
                f"{desc_s} (progress {game_data.fmt_compact_int(eff_r)}/{game_data.fmt_compact_int(rtask.target)})"
            )
            actions.append(
                _make_research_action(
                    "P2",
                    planet,
                    state,
                    rid,
                    reason,
                    research_offers,
                    research_queue_busy,
                    allow_live=config.allow_research and allow_live_research,
                )
            )

    return actions


def _tutorial_fight_task(state: GameState) -> Optional[object]:
    """Return active incomplete tutorial combat milestone task, if any."""
    if state.tutorial is None or state.tutorial.can_complete:
        return None
    for task in state.tutorial.tasks:
        if task.completed or task.category != "milestone":
            continue
        desc = _safe_strategy_str(task.description).lower()
        if any(word in desc for word in ("fight", "attack", "combat", "battle")):
            return task
    return None


def _tutorial_spy_task(state: GameState) -> Optional[object]:
    """Return active incomplete tutorial espionage milestone task, if any."""
    if state.tutorial is None or state.tutorial.can_complete:
        return None
    for task in state.tutorial.tasks:
        if task.completed or task.category != "milestone":
            continue
        desc = _safe_strategy_str(task.description).lower()
        if any(word in desc for word in ("spy", "espionage", "spionage", "probe")):
            return task
    return None


def _tutorial_escape_task(state: GameState) -> Optional[object]:
    """Return active incomplete tutorial escape-flight milestone task, if any."""
    if state.tutorial is None or state.tutorial.can_complete:
        return None
    for task in state.tutorial.tasks:
        if task.completed or task.category != "milestone":
            continue
        desc = _safe_strategy_str(task.description).lower()
        if any(word in desc for word in ("escape flight", "escape", "flucht", "fleetsave")):
            return task
    return None


def _tutorial_recycle_task(state: GameState) -> Optional[object]:
    """Return active incomplete tutorial space-rubble recycling milestone task, if any."""
    if state.tutorial is None or state.tutorial.can_complete:
        return None
    for task in state.tutorial.tasks:
        if task.completed or task.category != "milestone":
            continue
        desc = _safe_strategy_str(task.description).lower()
        if any(
            word in desc
            for word in ("recycle", "recycling", "recycler", "debris", "rubble", "trümmer", "truemmer", "abbau", "harvest")
        ):
            return task
    return None


def _tutorial_colonize_task(state: GameState) -> Optional[object]:
    """Return active incomplete tutorial colonization milestone task, if any."""
    if state.tutorial is None or state.tutorial.can_complete:
        return None
    for task in state.tutorial.tasks:
        if task.completed or task.category != "milestone":
            continue
        desc = _safe_strategy_str(task.description).lower()
        if any(word in desc for word in ("colonize", "colonisation", "colonization", "kolonisier")):
            return task
    return None


def _milestone_helper_ship_id(desc_lower: str) -> Optional[int]:
    if any(word in desc_lower for word in ("spy", "espionage", "spionage", "probe")):
        return 10
    if any(
        word in desc_lower
        for word in ("recycle", "recycling", "recycler", "debris", "rubble", "trümmer", "truemmer", "abbau", "harvest")
    ):
        return 11
    if any(word in desc_lower for word in ("colonize", "colony", "colonisation", "colonization")):
        return 8
    return None


def _plan_tutorial_fleet_milestones(
    state: GameState,
    active: PlanetState,
    config: StrategyConfig,
    *,
    planets_to_check: Optional[list[PlanetState]] = None,
    emitted_build_ids: Optional[set[tuple[int, int]]] = None,
    emitted_res_keys: Optional[set[str]] = None,
) -> list[PlannedAction]:
    """Emit ship build, prerequisite unlock, and non-combat milestone actions for Tutorial Steps 5–11."""
    if state.tutorial is None:
        return []
    tut = state.tutorial
    tut_step_s = game_data.fmt_compact_int(tut.step)
    tut_total_s = game_data.fmt_compact_int(tut.total_steps)
    out: list[PlannedAction] = []
    if planets_to_check is not None:
        candidates_all = list(planets_to_check)
    elif config.allow_multi_planet:
        candidates_all = [active] + [p for p in state.planets if p.planet_id != active.planet_id]
    else:
        candidates_all = [active]
    non_raid_candidates = [p for p in candidates_all if _norm_planet_type(p) != "raid"]
    active_coords_s = _safe_strategy_str(active.coords)
    queued_b_cnt = sum(1 for q in state.active_queues if q.category == "building")
    active_queue_full = queued_b_cnt >= _max_build_slots(active, state)
    research_queue_busy = any(q.category == "research" for q in state.active_queues) or any(
        o.active for o in state.research_offers
    )
    active_r_offers = {o.research_key: o for o in state.research_offers if o.research_key}
    active_r_inc = _active_research_increments(state)
    eff_researches: dict[str, int] = {
        game_data.safe_key(k): max(0, _safe_strategy_int(v, 0))
        for k, v in (state.researches or {}).items()
    }
    for rk_i, delta_i in active_r_inc.items():
        eff_researches[rk_i] = eff_researches.get(rk_i, 0) + delta_i
    seen_build_ids = emitted_build_ids if emitted_build_ids is not None else set()
    seen_res_keys = emitted_res_keys if emitted_res_keys is not None else set()

    def _emit_ship_and_prereqs(
        sid_num: int,
        needed_qty: int,
        base_reason: str,
        fallback_ship_name: str,
        *,
        hold_in_flight: bool = False,
    ) -> None:
        if not non_raid_candidates:
            return
        slug = SHIP_SLUG_BY_KEY.get(f"s{sid_num}")
        info = game_data.SHIPS.get(slug or "") if slug else None
        best_rank: Optional[tuple[bool, bool, bool, bool, bool, bool, bool, bool]] = None
        best_tuple: Optional[tuple[PlanetState, Optional[Any], list[dict[str, Any]], bool, str, Resources, bool]] = None
        for cand in non_raid_candidates:
            is_active = cand.planet_id == active.planet_id
            c_ship_offers = _ship_offer_map(cand)
            c_offer = c_ship_offers.get(sid_num)
            c_missing_all = (
                game_data.missing_prerequisites(
                    "ships",
                    slug,
                    buildings=cand.buildings,
                    researches=state.researches,
                )
                if slug
                else []
            )
            c_missing_b = [m for m in c_missing_all if m.get("category") == "buildings"]
            c_wf_lvl = cand.building_level(WEAPONS_FACTORY)
            c_has_wf = c_wf_lvl >= 1 and not c_missing_b
            c_prereqs_ok = not c_missing_all
            if c_offer is not None:
                c_ship_name = _safe_strategy_str(c_offer.name) or f"Ship s{sid_num}"
                c_cost = c_offer.cost
                c_can_exec = bool(
                    c_has_wf
                    and c_prereqs_ok
                    and not hold_in_flight
                    and config.allow_shipyard
                    and c_offer.can_build
                    and cand.resources.can_afford(c_cost)
                )
            else:
                c_ship_name = fallback_ship_name or str((info or {}).get("name") or f"Ship s{sid_num}")
                c_cost = _resources_from_dict((info or {}).get("base_cost", {}))
                c_can_exec = False
            rank = (
                c_can_exec,
                c_prereqs_ok and c_offer is not None,
                c_has_wf and c_offer is not None,
                c_prereqs_ok,
                c_has_wf,
                c_offer is not None,
                c_wf_lvl >= 1,
                is_active,
            )
            if best_rank is None or rank > best_rank:
                best_rank = rank
                best_tuple = (
                    cand,
                    c_offer,
                    c_missing_b,
                    c_has_wf,
                    c_ship_name,
                    c_cost,
                    c_can_exec,
                )
        assert best_tuple is not None
        cand, offer, missing_b, _, ship_name, cost, can_exec = best_tuple
        if slug:
            is_cand_active = cand.planet_id == active.planet_id
            cand_offers = _offer_map(cand)
            cand_eff, _ = _effective_building_levels_and_energy(
                state, cand, is_active_planet=is_cand_active
            )
            res_planet: Optional[PlanetState] = None
            cand_ptype = _norm_planet_type(cand)
            if cand_ptype != "raid" and (
                cand_ptype != "asteroid"
                or _eff_level(cand, cand_eff, RESEARCH_LAB) >= 1
                or _is_building_allowed_on_planet(cand, RESEARCH_LAB, cand_offers)
            ):
                res_planet = cand
            elif _norm_planet_type(active) not in ("raid", "asteroid"):
                res_planet = active
            out.extend(
                _emit_tutorial_prereq_actions(
                    tut_step_s=tut_step_s,
                    tut_total_s=tut_total_s,
                    category="ships",
                    slug_or_id=slug,
                    goal_label=ship_name,
                    state=state,
                    build_planet=cand,
                    build_offers=cand_offers,
                    eff_buildings=cand_eff,
                    queue_full=active_queue_full if is_cand_active else False,
                    allow_remote_colony_build=config.allow_multi_planet and not is_cand_active,
                    research_planet=res_planet,
                    research_offers=active_r_offers if (res_planet and res_planet.planet_id == active.planet_id) else {},
                    research_queue_busy=research_queue_busy,
                    allow_live_research=bool(res_planet and res_planet.planet_id == active.planet_id),
                    config=config,
                    emitted_build_ids=seen_build_ids,
                    emitted_res_keys=seen_res_keys,
                    eff_researches=eff_researches,
                )
            )
        reason = base_reason
        if hold_in_flight:
            reason += " (hold while own fleet is in transit)"
        elif cand.building_level(WEAPONS_FACTORY) < 1:
            reason += " (Weapons factory b9 required)"
        elif missing_b:
            first_req = missing_b[0]
            reason += (
                f" (requires {first_req['name']} "
                f"{game_data.fmt_compact_int(first_req['current'])}/{game_data.fmt_compact_int(first_req['required'])})"
            )
        out.append(
            PlannedAction(
                priority="P2",
                kind="BUILD_SHIP",
                planet_id=cand.planet_id,
                planet_coords=_safe_strategy_str(cand.coords),
                target_id=sid_num,
                target_name=ship_name,
                target_level=max(1, needed_qty) if offer is None else 1,
                cost=cost,
                missing=cand.resources.missing(cost),
                reason=reason,
                executable_now=can_exec,
            )
        )

    own_fleet_in_flight = any(is_in_flight_fleet_job(q) for q in state.active_queues)
    for task in tut.tasks:
        if task.completed:
            continue
        sid = game_data.safe_optional_int(task.building_id, None)
        if task.category == "ship" and sid is not None and sid > 0:
            t_cur = _safe_strategy_int(task.current, 0)
            t_tgt = _safe_strategy_int(task.target, 0)
            _emit_ship_and_prereqs(
                sid,
                max(1, t_tgt - t_cur),
                f"Tutorial {tut_step_s}/{tut_total_s}: {_safe_strategy_str(task.description)}",
                _safe_strategy_str(task.building_name),
            )
        elif task.category == "milestone":
            desc_s = _safe_strategy_str(task.description)
            desc_l = desc_s.lower()
            if any(word in desc_l for word in ("fight", "attack", "combat", "battle")):
                # Handled at P2 as SCAN_GALAXY / ATTACK by `_plan_combat_and_raid_actions`
                continue
            if any(word in desc_l for word in ("escape flight", "escape", "flucht", "fleetsave")):
                esc_planets = [
                    p
                    for p in non_raid_candidates
                    if any(k != "s9" and _safe_strategy_int(v, 0) > 0 for k, v in (p.ships or {}).items())
                ]
                if not esc_planets:
                    if non_raid_candidates:
                        _emit_ship_and_prereqs(
                            1,
                            1,
                            f"Tutorial {tut_step_s}/{tut_total_s}: build Small transport for {desc_s}",
                            "Small transport",
                            hold_in_flight=own_fleet_in_flight,
                        )
                        continue
                else:
                    esc_planet = next(
                        (p for p in esc_planets if _safe_strategy_int(p.ships.get("s1", 0), 0) > 0),
                        esc_planets[0],
                    )
                    e_gal, e_sys, e_pos = _parse_gal_sys_pos(esc_planet.coords)
                    target_coords = f"{e_gal:02d}:{e_sys:03d}:{(e_pos % 16) + 1:02d}"
                    esc_reason = f"Tutorial {tut_step_s}/{tut_total_s}: {desc_s}"
                    if own_fleet_in_flight:
                        esc_reason += " [Hold: Own fleet in transit]"
                    out.append(
                        PlannedAction(
                            priority="P2",
                            kind="ESCAPE_FLIGHT",
                            planet_id=esc_planet.planet_id,
                            planet_coords=_safe_strategy_str(esc_planet.coords),
                            target_name=f"Save -> {target_coords}",
                            reason=esc_reason,
                            executable_now=bool(config.allow_fleetsave and not own_fleet_in_flight),
                        )
                    )
                    continue
            helper_sid = _milestone_helper_ship_id(desc_l)
            if helper_sid == 10:
                has_any_probe = any(
                    _safe_strategy_int(p.ships.get("s10", 0), 0) > 0
                    for p in non_raid_candidates
                )
                if has_any_probe:
                    # Handled at P2 as SCAN_GALAXY / SPY by `_plan_combat_and_raid_actions`
                    continue
                if non_raid_candidates:
                    _emit_ship_and_prereqs(
                        10,
                        1,
                        f"Tutorial {tut_step_s}/{tut_total_s}: build Spy probe for {desc_s}",
                        "Spy probe",
                    )
                    continue
            elif helper_sid == 11:
                has_any_recycler = any(
                    _safe_strategy_int(p.ships.get("s11", 0), 0) > 0
                    for p in non_raid_candidates
                )
                if has_any_recycler:
                    # Handled at P2 as SCAN_GALAXY / RECYCLE by `_plan_combat_and_raid_actions`
                    continue
                if non_raid_candidates:
                    _emit_ship_and_prereqs(
                        11,
                        1,
                        f"Tutorial {tut_step_s}/{tut_total_s}: build Recycler for {desc_s}",
                        "Recycler",
                    )
            elif helper_sid == 8:
                has_any_helper = any(
                    _safe_strategy_int(p.ships.get("s8", 0), 0) > 0
                    for p in non_raid_candidates
                )
                if has_any_helper:
                    # Handled at P2 as SCAN_GALAXY / SPY / COLONIZE by `_plan_combat_and_raid_actions`
                    continue
                if non_raid_candidates:
                    _emit_ship_and_prereqs(
                        8,
                        1,
                        f"Tutorial {tut_step_s}/{tut_total_s}: build Colony ship for {desc_s}",
                        "Colony ship",
                    )
            out.append(
                PlannedAction(
                    priority="P3",
                    kind="TUTORIAL_TASK",
                    planet_id=active.planet_id,
                    planet_coords=active_coords_s,
                    target_name=desc_s,
                    reason=f"Tutorial {tut_step_s}/{tut_total_s}: {desc_s}",
                    executable_now=False,
                )
            )
    return out


_COMBAT_AND_RAID_SHIP_KEYS = ("s4", "s5", "s6", "s7", "s13", "s14", "s15", "s16", "s1", "s2", "s3")


def _parse_gal_sys_pos(coords: Any) -> tuple[int, int, int]:
    coords_s = game_data.safe_key(coords) if coords is not None else ""
    parts = [p.strip() for p in coords_s.split(":")]
    if len(parts) == 3 and all(p.isascii() and p.isdigit() for p in parts):
        try:
            return int(parts[0][:9]), int(parts[1][:9]), int(parts[2][:9])
        except ValueError:
            return 1, 1, 1
    return 1, 1, 1


def _plan_combat_and_raid_actions(
    state: GameState,
    active: PlanetState,
    cfg: StrategyConfig,
    *,
    scanned_systems: Optional[Mapping[str, float]] = None,
    now_ts: Optional[float] = None,
    raid_stats: Optional[Mapping[str, Any]] = None,
) -> list[PlannedAction]:
    """Plan Galaxy exploration sweep, Spy probe scouting, Space Rubble recycling, and scored Attack targets."""
    if state.tutorial is not None and state.tutorial.can_complete:
        return []

    ref_now = _safe_strategy_float(now_ts, time.time()) if now_ts is not None else time.time()
    tut_fight = _tutorial_fight_task(state)
    tut_spy = _tutorial_spy_task(state)
    tut_escape = _tutorial_escape_task(state)
    tut_recycle = _tutorial_recycle_task(state)
    tut_colonize = _tutorial_colonize_task(state)
    tutorial_fight_pending = tut_fight is not None
    tutorial_spy_pending = tut_spy is not None
    tutorial_escape_pending = tut_escape is not None
    tutorial_recycle_pending = tut_recycle is not None
    tutorial_colonize_pending = tut_colonize is not None
    max_planets_cap = max(1, _safe_strategy_int(getattr(cfg, "max_planets", 11), 11))
    allocated_normal_planets = sum(
        1 for p in state.planets if _norm_planet_type(p) == "normal"
    )
    planets_fully_allocated = allocated_normal_planets >= max_planets_cap
    needed_colonies = max(0, max_planets_cap - allocated_normal_planets)
    min_colony_fields = max(50, _safe_strategy_int(getattr(cfg, "min_colony_fields", 300), 300))

    def _planet_attack_ships(planet: PlanetState) -> dict[str, int]:
        return {
            k: max(0, _safe_strategy_int(planet.ships.get(k, 0), 0))
            for k in _COMBAT_AND_RAID_SHIP_KEYS
            if _safe_strategy_int(planet.ships.get(k, 0), 0) > 0
        }

    origin = active
    attack_ships = _planet_attack_ships(active)
    if not attack_ships:
        for candidate in state.planets:
            if _norm_planet_type(candidate) == "raid":
                continue
            cand_ships = _planet_attack_ships(candidate)
            if cand_ships:
                origin = candidate
                attack_ships = cand_ships
                break

    has_attack_fleet = bool(attack_ships)
    req_spy_pid = max(0, _safe_strategy_int(getattr(cfg, "spy_dispatch_planet_id", 0), 0))
    designated_spy_planet = (
        next(
            (
                p
                for p in state.planets
                if p.planet_id == req_spy_pid and _norm_planet_type(p) != "raid"
            ),
            None,
        )
        if req_spy_pid > 0
        else None
    )
    if designated_spy_planet is not None:
        probe_planet: Optional[PlanetState] = designated_spy_planet
        has_probes = _safe_strategy_int(designated_spy_planet.ships.get("s10", 0), 0) > 0
        designated_missing_probes = not has_probes
    else:
        probe_planet = (
            active
            if _norm_planet_type(active) != "raid" and _safe_strategy_int(active.ships.get("s10", 0), 0) > 0
            else next(
                (
                    p
                    for p in state.planets
                    if _norm_planet_type(p) != "raid" and _safe_strategy_int(p.ships.get("s10", 0), 0) > 0
                ),
                None,
            )
        )
        has_probes = probe_planet is not None
        designated_missing_probes = False
    can_plan_spy = probe_planet is not None
    recycler_planet = (
        active
        if _norm_planet_type(active) != "raid" and _safe_strategy_int(active.ships.get("s11", 0), 0) > 0
        else next(
            (
                p
                for p in state.planets
                if _norm_planet_type(p) != "raid" and _safe_strategy_int(p.ships.get("s11", 0), 0) > 0
            ),
            None,
        )
    )
    has_recyclers = recycler_planet is not None
    colony_planet = (
        active
        if _norm_planet_type(active) != "raid" and _safe_strategy_int(active.ships.get("s8", 0), 0) > 0
        else next(
            (
                p
                for p in state.planets
                if _norm_planet_type(p) != "raid" and _safe_strategy_int(p.ships.get("s8", 0), 0) > 0
            ),
            None,
        )
    )
    has_colony_ship = colony_planet is not None
    raid_targets = getattr(state, "raid_targets", ()) or ()
    _fleet_pre = _get_fleet()
    own_coords_pre = {_fleet_pre.normalize_coords(p.coords) for p in state.planets}
    qualified_colony_coords: set[str] = set()
    for raw_pre in raid_targets:
        if not isinstance(raw_pre, Mapping) or bool(raw_pre.get("discarded", False)):
            continue
        c_pre = game_data.safe_key(raw_pre.get("coords") or raw_pre.get("target_coords") or "").strip()
        if not c_pre:
            continue
        nc_pre = _fleet_pre.normalize_coords(c_pre)
        if not nc_pre or nc_pre in own_coords_pre or bool(raw_pre.get("is_spacia", False)):
            continue
        sc_pre = game_data.safe_key(raw_pre.get("status_color") or "").strip().lower()
        ow_pre = game_data.safe_key(raw_pre.get("owner") or "").strip()
        raw_uf_pre = raw_pre.get("uninhabited_fields")
        uf_pre = game_data.safe_optional_int(raw_uf_pre, None) if raw_uf_pre is not None else None
        raw_cmax_pre = raw_pre.get("colonize_max_fields")
        cmax_pre = game_data.safe_optional_int(raw_cmax_pre, None) if raw_cmax_pre is not None else None
        eff_qual_f = uf_pre if (uf_pre is not None and uf_pre > 0) else (cmax_pre if (cmax_pre is not None and cmax_pre > 0) else None)
        if (sc_pre == "free" or (not ow_pre and eff_qual_f is not None)) and eff_qual_f is not None and eff_qual_f >= min_colony_fields:
            qualified_colony_coords.add(nc_pre)
    qualified_candidates_count = len(qualified_colony_coords)
    want_colony_probes = bool(
        (cfg.allow_galaxy_scan or cfg.allow_colonize or tutorial_colonize_pending)
        and not planets_fully_allocated
        and qualified_candidates_count < needed_colonies
    )
    want_colonize = bool(
        (cfg.allow_colonize or tutorial_colonize_pending)
        and not planets_fully_allocated
    )

    if (
        not raid_targets
        and not tutorial_fight_pending
        and not tutorial_spy_pending
        and not tutorial_recycle_pending
        and not want_colonize
        and not want_colony_probes
        and not cfg.allow_galaxy_scan
        and not cfg.allow_raids
        and not has_attack_fleet
        and not can_plan_spy
        and not has_recyclers
    ):
        return []

    scanned_map: dict[str, float] = {}
    if isinstance(scanned_systems, Mapping):
        for raw_sys, raw_ts in scanned_systems.items():
            m_sys = re.search(r"(\d{1,2})\s*:\s*(\d{1,3})", game_data.safe_key(raw_sys), re.ASCII)
            if m_sys:
                skey = f"{int(m_sys.group(1)):02d}:{int(m_sys.group(2)):03d}"
                scanned_map[skey] = max(
                    scanned_map.get(skey, 0.0),
                    _safe_strategy_float(raw_ts, 0.0),
                )

    _fleet = _get_fleet()
    bash_tracker = _fleet.BashTracker()
    latest_battle_by_sys: dict[str, float] = {}
    stats_sources: list[Mapping[str, Any]] = []
    state_rs = getattr(state, "raid_stats", None)
    if isinstance(state_rs, Mapping):
        stats_sources.append(state_rs)
    if isinstance(raid_stats, Mapping) and raid_stats is not state_rs:
        stats_sources.append(raid_stats)
    hist_entries: list[tuple[float, int, str, bool]] = []
    seen_hist_keys: set[tuple[str, float]] = set()
    skew_max = (ref_now + 60.0) if ref_now > 0.0 else float("inf")
    max_until_ts = (ref_now + 86400.0 * 2.0) if ref_now > 0.0 else float("inf")
    for rs_src in stats_sources:
        raw_hist = rs_src.get("history")
        if isinstance(raw_hist, Sequence) and not isinstance(raw_hist, (str, bytes)):
            for idx_h, hrow in enumerate(raw_hist):
                if not isinstance(hrow, Mapping):
                    continue
                raw_hc = game_data.safe_key(hrow.get("target_coords") or hrow.get("coords") or "").strip()
                if not raw_hc:
                    continue
                norm_hc = _fleet.normalize_coords(raw_hc)
                raw_hts = (
                    hrow.get("attacked_ts")
                    if hrow.get("attacked_ts") is not None
                    else (hrow.get("ts") if hrow.get("ts") is not None else hrow.get("timestamp"))
                )
                hts = _safe_strategy_float(raw_hts, 0.0)
                if hts <= 0.0 or hts > skew_max:
                    continue
                h_gal, h_sys, _ = _parse_gal_sys_pos(norm_hc)
                h_skey = f"{h_gal:02d}:{h_sys:03d}"
                latest_battle_by_sys[h_skey] = max(latest_battle_by_sys.get(h_skey, 0.0), hts)
                dedup_k = (norm_hc, round(hts, 3))
                if dedup_k in seen_hist_keys:
                    continue
                seen_hist_keys.add(dedup_k)
                if (ref_now - 86400.0) <= hts <= skew_max:
                    hist_entries.append((min(hts, ref_now), idx_h, norm_hc, bool(hrow.get("is_exempt", False))))
        raw_bt = rs_src.get("blocked_targets")
        if isinstance(raw_bt, Mapping):
            for bk, bv in raw_bt.items():
                bk_s = game_data.safe_key(bk).strip()
                if not bk_s:
                    continue
                if isinstance(bv, Mapping):
                    b_until = _safe_strategy_float(bv.get("blocked_until_ts"), 0.0)
                    b_last_ts = _safe_strategy_float(
                        bv.get("last_blocked_ts")
                        if bv.get("last_blocked_ts") is not None
                        else bv.get("blocked_ts"),
                        0.0,
                    )
                    b_reason = _safe_strategy_str(bv.get("reason") or bv.get("block_reason"))
                else:
                    b_until = _safe_strategy_float(bv, 0.0)
                    b_last_ts = 0.0
                    b_reason = ""
                if b_last_ts > 0.0 and b_last_ts > skew_max:
                    continue
                if b_last_ts <= 0.0 and b_until > max_until_ts:
                    continue
                if b_until > ref_now:
                    bash_tracker.record_block(_fleet.normalize_coords(bk_s), b_until, reason=b_reason)
        raw_bb = rs_src.get("bash_blocks")
        if isinstance(raw_bb, Sequence) and not isinstance(raw_bb, (str, bytes)):
            for brow in raw_bb:
                if not isinstance(brow, Mapping):
                    continue
                bc_s = game_data.safe_key(brow.get("target_coords") or brow.get("coords") or "").strip()
                b_until = _safe_strategy_float(brow.get("blocked_until_ts"), 0.0)
                b_ts = _safe_strategy_float(
                    brow.get("blocked_ts")
                    if brow.get("blocked_ts") is not None
                    else brow.get("last_blocked_ts"),
                    0.0,
                )
                if b_ts > 0.0 and b_ts > skew_max:
                    continue
                if b_ts <= 0.0 and b_until > max_until_ts:
                    continue
                if bc_s and b_until > ref_now:
                    bash_tracker.record_block(
                        _fleet.normalize_coords(bc_s),
                        b_until,
                        reason=_safe_strategy_str(brow.get("reason")),
                    )
    hist_entries.sort(key=lambda item: (item[0], item[1]))
    for hts, _, norm_hc, is_ex in hist_entries:
        if not is_ex:
            bash_tracker.record_attack(norm_hc, timestamp=hts)

    own_coords = {_fleet.normalize_coords(p.coords) for p in state.planets}
    used_fleet_slots = sum(1 for q in (state.active_queues or ()) if is_in_flight_fleet_job(q))
    own_fleet_in_flight = used_fleet_slots > 0
    scored_attacks: list[tuple[tuple[int, float, float, int], PlannedAction]] = []
    spy_actions: list[tuple[tuple[int, int, int, float], PlannedAction]] = []
    colony_spy_actions: list[tuple[tuple[int, int, int, int], PlannedAction]] = []
    recycle_actions: list[tuple[tuple[int, int], PlannedAction]] = []
    colonize_actions: list[tuple[tuple[int, int, int, int, int], PlannedAction]] = []
    seen_recycle_coords: set[str] = set()
    seen_colony_coords: set[str] = set()

    designated_probe_build_act: Optional[PlannedAction] = None
    if designated_missing_probes and designated_spy_planet is not None and cfg.allow_shipyard:
        if designated_spy_planet.building_level(WEAPONS_FACTORY) >= 1:
            unmet_s10 = game_data.missing_prerequisites(
                "ships",
                "spy-probe",
                buildings=designated_spy_planet.buildings,
                researches=state.researches or {},
            )
            if not unmet_s10:
                s10_offers = _ship_offer_map(designated_spy_planet)
                s10_off = s10_offers.get(10)
                s10_cost = (
                    s10_off.cost
                    if (s10_off is not None and not s10_off.cost.is_zero())
                    else _resources_from_dict((game_data.SHIPS.get("spy-probe") or {}).get("base_cost", {}))
                )
                s10_can = bool(
                    designated_spy_planet.resources.can_afford(s10_cost)
                    and (s10_off is None or s10_off.can_build)
                )
                designated_probe_build_act = PlannedAction(
                    priority="P2",
                    kind="BUILD_SHIP",
                    planet_id=designated_spy_planet.planet_id,
                    planet_coords=_safe_strategy_str(designated_spy_planet.coords),
                    target_id=10,
                    target_name="Spy probe",
                    target_level=1,
                    cost=s10_cost,
                    missing=designated_spy_planet.resources.missing(s10_cost),
                    reason=f"Build Spy probe (s10) on designated spy colony [{_safe_strategy_str(designated_spy_planet.coords)}]",
                    executable_now=s10_can,
                )

    for raw in raid_targets:
        if not isinstance(raw, Mapping):
            continue
        coords = game_data.safe_key(raw.get("coords") or raw.get("target_coords") or "").strip()
        if not coords:
            continue
        norm_coords = _fleet.normalize_coords(coords)
        t_gal, t_sys, pos = _parse_gal_sys_pos(norm_coords)
        skey = f"{t_gal:02d}:{t_sys:03d}"
        raw_scanned_ts = _safe_strategy_float(raw.get("last_scanned_ts"), 0.0)
        if raw_scanned_ts > 0.0:
            scanned_map[skey] = max(scanned_map.get(skey, 0.0), raw_scanned_ts)
        raw_last_atk = max(
            _safe_strategy_float(raw.get("last_attack_ts"), 0.0),
            _safe_strategy_float(raw.get("last_attacked_ts"), 0.0),
        )
        if raw_last_atk > 0.0:
            latest_battle_by_sys[skey] = max(latest_battle_by_sys.get(skey, 0.0), raw_last_atk)
        raw_blocked_until = _safe_strategy_float(raw.get("blocked_until_ts"), 0.0)
        if raw_blocked_until > ref_now:
            bash_tracker.record_block(
                norm_coords,
                raw_blocked_until,
                reason=_safe_strategy_str(raw.get("block_reason")),
            )

        owner = game_data.safe_key(raw.get("owner") or "")
        pname = game_data.safe_key(raw.get("planet_name") or "")
        if bool(raw.get("has_debris", False)) and has_recyclers and norm_coords not in seen_recycle_coords:
            assert recycler_planet is not None
            seen_recycle_coords.add(norm_coords)
            rec_qty = max(1, _safe_strategy_int(recycler_planet.ships.get("s11", 0), 1))
            hold_rec_inflight = bool(tutorial_recycle_pending and own_fleet_in_flight)
            can_exec_rec = bool((cfg.allow_raids or cfg.allow_galaxy_scan) and not hold_rec_inflight)
            rec_gal, rec_sys, rec_pos = _parse_gal_sys_pos(recycler_planet.coords)
            rec_dist = abs(rec_gal - t_gal) * 20000 + abs(rec_sys - t_sys) * 95 + abs(rec_pos - pos) * 5
            rec_lbl = f"Rubble -> {norm_coords}" + (f" ({owner or pname})" if (owner or pname) else "")
            rec_prefix = "Tutorial recycle: " if tutorial_recycle_pending else ""
            rec_reason = f"{rec_prefix}Harvest space rubble at {norm_coords} with {rec_qty} Recycler(s)"
            if hold_rec_inflight:
                rec_reason += " [Hold: Own fleet in transit]"
            elif not (cfg.allow_raids or cfg.allow_galaxy_scan):
                rec_reason += " [Hold: allow_raids/allow_galaxy_scan disabled in Config]"
            recycle_actions.append(
                (
                    (0 if can_exec_rec else 1, rec_dist),
                    PlannedAction(
                        priority="P2",
                        kind="RECYCLE",
                        planet_id=recycler_planet.planet_id,
                        planet_coords=_safe_strategy_str(recycler_planet.coords),
                        target_id=11,
                        target_level=rec_qty,
                        target_name=rec_lbl,
                        reason=rec_reason,
                        executable_now=can_exec_rec,
                    ),
                )
            )

        if norm_coords in own_coords:
            continue

        is_spacia = bool(raw.get("is_spacia", False))
        explicit_color = game_data.safe_key(raw.get("status_color") or "").strip().lower()
        if explicit_color:
            status_color = explicit_color
        elif is_spacia:
            status_color = "purple"
        elif raw.get("is_inactive") is False:
            status_color = "light_red"
        else:
            status_color = "yellow"

        if (
            want_colonize
            and not is_spacia
            and not bool(raw.get("discarded", False))
            and (
                status_color == "free"
                or (not owner and (raw.get("uninhabited_fields") is not None or raw.get("colonize_max_fields") is not None))
            )
            and norm_coords not in seen_colony_coords
        ):
            seen_colony_coords.add(norm_coords)
            raw_uf = raw.get("uninhabited_fields")
            uf = game_data.safe_optional_int(raw_uf, None) if raw_uf is not None else None
            raw_cmin = raw.get("colonize_min_fields")
            cmin_col = game_data.safe_optional_int(raw_cmin, None) if raw_cmin is not None else None
            raw_cmax = raw.get("colonize_max_fields")
            cmax_col = game_data.safe_optional_int(raw_cmax, None) if raw_cmax is not None else None
            raw_temp_c = raw.get("temperature")
            temp_col = game_data.safe_optional_int(raw_temp_c, None) if raw_temp_c is not None else None
            spied_ts_col = _safe_strategy_float(raw.get("last_spied_ts"), 0.0)
            col_ref_planet = colony_planet or probe_planet or origin
            grade_info = _fleet.grade_colony_candidate(
                coords=norm_coords,
                position=pos,
                uninhabited_fields=uf,
                colonize_min_fields=cmin_col,
                colonize_max_fields=cmax_col,
                temperature=temp_col,
                origin_coords=col_ref_planet.coords,
                min_fields_target=min_colony_fields,
            )
            if bool(grade_info["meets_target"]) and (cfg.allow_colonize or tutorial_colonize_pending):
                col_exec_planet = colony_planet if colony_planet is not None else origin
                can_exec_col = bool(cfg.allow_colonize and has_colony_ship and not own_fleet_in_flight)
                col_prefix = "Tutorial colonize: " if tutorial_colonize_pending else ""
                f_disp = str(grade_info.get("fields_display") or (f"{uf} fields" if uf is not None else f"{cmax_col} fields"))
                eff_f_sort = int(uf if uf is not None else (cmax_col or 0))
                col_reason = (
                    f"{col_prefix}Colonize {norm_coords} "
                    f"({f_disp} >= {min_colony_fields} target, Grade {grade_info['grade']} {grade_info['score']}/100)"
                )
                if not has_colony_ship:
                    col_reason += " [Hold: Build Colony ship (s8) first]"
                elif own_fleet_in_flight:
                    col_reason += " [Hold: Own fleet in transit]"
                elif not cfg.allow_colonize:
                    col_reason += " [Hold: allow_colonize=false in Config]"
                colonize_actions.append(
                    (
                        (0 if can_exec_col else 1, 0, -int(grade_info["score"]), -eff_f_sort, int(grade_info["distance"])),
                        PlannedAction(
                            priority="P2",
                            kind="COLONIZE",
                            planet_id=col_exec_planet.planet_id,
                            planet_coords=_safe_strategy_str(col_exec_planet.coords),
                            target_id=8,
                            target_level=1,
                            target_name=f"Colonize -> {norm_coords} ({f_disp})",
                            reason=col_reason,
                            executable_now=can_exec_col,
                        ),
                    )
                )
            elif (
                not bool(grade_info["probed"])
                and spied_ts_col <= 0.0
                and want_colony_probes
                and can_plan_spy
                and (
                    _safe_strategy_float(raw.get("blocked_until_ts"), 0.0) <= ref_now
                    or "can't be attacked" in _safe_strategy_str(raw.get("block_reason")).lower()
                    or "cannot be attacked" in _safe_strategy_str(raw.get("block_reason")).lower()
                )
                and (
                    bash_tracker.can_attack(norm_coords, is_inactive=True, timestamp=ref_now)
                    or "can't be attacked" in _safe_strategy_str(raw.get("block_reason")).lower()
                    or "cannot be attacked" in _safe_strategy_str(raw.get("block_reason")).lower()
                )
            ):
                assert probe_planet is not None
                max_spy_slots_col = 1 + max(0, probe_planet.building_level(FLEET_BASE))
                p_gal_c, p_sys_c, p_pos_c = _parse_gal_sys_pos(probe_planet.coords)
                phys_dist_col = abs(p_gal_c - t_gal) * 20000 + abs(p_sys_c - t_sys) * 95 + abs(p_pos_c - pos) * 5
                zone_prio = 0 if 7 <= pos <= 12 else (1 if 4 <= pos <= 6 else (2 if 13 <= pos <= 16 else 3))
                can_exec_pcol = bool(
                    (cfg.allow_galaxy_scan or cfg.allow_colonize)
                    and has_probes
                    and used_fleet_slots < max_spy_slots_col
                )
                pcol_reason = (
                    f"Probe free slot {norm_coords} (pos {pos}, {grade_info['orbital_zone']}) "
                    f"for {min_colony_fields}+ fields ({qualified_candidates_count}/{needed_colonies} qualified)"
                )
                if designated_missing_probes:
                    pcol_reason += " [Hold: No Spy probes (s10) stationed on designated spy colony]"
                elif used_fleet_slots >= max_spy_slots_col:
                    pcol_reason += f" [Hold: Fleet slots full ({used_fleet_slots}/{max_spy_slots_col} in flight)]"
                elif not (cfg.allow_galaxy_scan or cfg.allow_colonize):
                    pcol_reason += " [Hold: allow_galaxy_scan/allow_colonize disabled in Config]"
                colony_spy_actions.append(
                    (
                        (0 if can_exec_pcol else 1, zone_prio, phys_dist_col, pos),
                        PlannedAction(
                            priority="P2",
                            kind="SPY",
                            planet_id=probe_planet.planet_id,
                            planet_coords=_safe_strategy_str(probe_planet.coords),
                            target_id=10,
                            target_level=1,
                            target_name=f"{norm_coords} (Free slot)",
                            reason=pcol_reason,
                            executable_now=can_exec_pcol,
                        ),
                    )
                )
            continue

        if not is_spacia and status_color in ("free", "blue", "green", "white", "purple", "dark_red"):
            continue

        slot = _fleet.GalaxySlot(
            coords=norm_coords,
            position=pos,
            name=pname,
            owner=owner,
            status_color=status_color,
            is_spacia=is_spacia,
        )
        res_map = raw.get("resources") if isinstance(raw.get("resources"), Mapping) else raw
        res = Resources(
            pig_iron=max(0, _safe_strategy_int(res_map.get("pig_iron", 0), 0)),
            metal=max(0, _safe_strategy_int(res_map.get("metal", 0), 0)),
            kryptonite=max(0, _safe_strategy_int(res_map.get("kryptonite", 0), 0)),
            spice=max(0, _safe_strategy_int(res_map.get("spice", 0), 0)),
        )
        spied_ts = _safe_strategy_float(raw.get("last_spied_ts"), 0.0)
        raw_fleet = raw.get("fleet") if isinstance(raw.get("fleet"), Mapping) else {}
        raw_defense = raw.get("defense") if isinstance(raw.get("defense"), Mapping) else {}
        clean_fleet = {game_data.safe_key(k): cnt for k, v in raw_fleet.items() if (cnt := max(0, _safe_strategy_int(v, 0))) > 0}
        clean_defense = {game_data.safe_key(k): cnt for k, v in raw_defense.items() if (cnt := max(0, _safe_strategy_int(v, 0))) > 0}
        alt_name = game_data.safe_key(raw.get("name") or "")
        if not is_spacia and not owner and not pname and not alt_name:
            continue
        has_intel = (
            bool(raw.get("has_spy_intel"))
            or spied_ts > 0.0
            or (not res.is_zero())
            or bool(clean_fleet)
            or bool(clean_defense)
        )
        stale_intel = spied_ts > 0.0 and (ref_now - spied_ts) > (4 * 3600.0)
        eligible_target = (
            is_spacia
            or status_color == "yellow"
            or (cfg.allow_pvp_in_az and status_color == "light_red")
        )
        can_attack_now = (
            _safe_strategy_float(raw.get("blocked_until_ts"), 0.0) <= ref_now
            and bash_tracker.can_attack(
                norm_coords,
                is_inactive=(status_color == "yellow"),
                is_spacia=is_spacia,
                timestamp=ref_now,
            )
        )
        tag = "Spacia" if is_spacia else ("Inactive" if status_color == "yellow" else status_color.title())
        target_label = f"{coords} ({owner or pname or alt_name or tag})"

        if (
            can_plan_spy
            and can_attack_now
            and (tutorial_spy_pending or not tutorial_fight_pending)
            and eligible_target
            and (tutorial_spy_pending or not has_intel or stale_intel)
        ):
            assert probe_planet is not None
            max_spy_slots = 1 + max(0, probe_planet.building_level(FLEET_BASE))
            p_gal_s, p_sys_s, p_pos_s = _parse_gal_sys_pos(probe_planet.coords)
            phys_dist_spy = abs(p_gal_s - t_gal) * 20000 + abs(p_sys_s - t_sys) * 95 + abs(p_pos_s - pos) * 5
            can_exec_spy = bool(
                cfg.allow_galaxy_scan
                and has_probes
                and used_fleet_slots < max_spy_slots
            )
            spy_reason = (
                f"{'Tutorial spy: ' if tutorial_spy_pending else ''}Send Spy probe (s10) to scout {tag} {coords}"
                + (" (refresh stale intel)" if (stale_intel and not tutorial_spy_pending) else " before raiding")
            )
            if designated_missing_probes:
                spy_reason += " [Hold: No Spy probes (s10) stationed on designated spy colony]"
            elif used_fleet_slots >= max_spy_slots:
                spy_reason += f" [Hold: Fleet slots full ({used_fleet_slots}/{max_spy_slots} in flight)]"
            elif not cfg.allow_galaxy_scan:
                spy_reason += " [Hold: allow_galaxy_scan=false in Config]"
            spy_actions.append(
                (
                    (
                        0 if can_exec_spy else 1,
                        0 if (status_color == "yellow" or is_spacia) else 1,
                        phys_dist_spy,
                        -raw_scanned_ts,
                    ),
                    PlannedAction(
                        priority="P2",
                        kind="SPY",
                        planet_id=probe_planet.planet_id,
                        planet_coords=_safe_strategy_str(probe_planet.coords),
                        target_id=10,
                        target_level=1,
                        target_name=target_label,
                        reason=spy_reason,
                        executable_now=can_exec_spy,
                    ),
                )
            )
            if tutorial_spy_pending or not has_intel:
                continue

        report = _fleet.SpyReport(
            target_coords=norm_coords,
            owner=owner,
            resources=res,
            fleet=clean_fleet,
            defense=clean_defense,
            is_spacia=is_spacia,
        )
        sim_fleet = attack_ships if has_attack_fleet else {"s1": 1}
        min_loot_cfg = max(0.0, _safe_strategy_float(getattr(cfg, "raid_min_loot_spice_eq", 1500.0), 1500.0))
        min_loot = 0.0 if (res.is_zero() or tutorial_fight_pending) else min_loot_cfg
        safety_margin_val = _safe_strategy_float(getattr(cfg, "combat_safety_margin", 1.4), 1.4)
        if safety_margin_val <= 0.0:
            safety_margin_val = 1.4
        rec = _fleet.score_raid_target(
            origin.coords,
            slot,
            report,
            sim_fleet,
            attacker_researches=state.researches,
            bash_tracker=bash_tracker,
            safety_margin=safety_margin_val,
            min_loot_spice_eq=min_loot,
            allow_pvp_in_az=cfg.allow_pvp_in_az,
            timestamp=ref_now,
        )
        if rec is None:
            continue

        hold_tutorial_inflight = bool(tutorial_fight_pending and own_fleet_in_flight)
        hold_for_escape_tutorial = bool(tutorial_escape_pending and not tutorial_fight_pending)
        can_exec = bool(
            cfg.allow_raids
            and has_attack_fleet
            and rec.combat.safe_to_attack
            and not hold_tutorial_inflight
            and not hold_for_escape_tutorial
        )
        if rec.loot_spice_equivalent > 0:
            loot_disp = (
                game_data.fmt_compact_int(round(rec.loot_spice_equivalent), grouped=True)
                if math.isfinite(rec.loot_spice_equivalent)
                else "INF"
            )
            loot_note = f"~{loot_disp} SpEq plunder"
        else:
            loot_note = "scouted inactive target"
        gates: list[str] = []
        if hold_tutorial_inflight:
            gates.append("tutorial raid fleet already in transit")
        if hold_for_escape_tutorial:
            gates.append("tutorial escape flight pending")
        if not has_attack_fleet:
            gates.append("need stationed attack/transport ships")
        if not cfg.allow_raids:
            gates.append("allow_raids=false in Config")
        if not rec.combat.safe_to_attack:
            gates.append(rec.combat.reason)
        gate_suffix = f" [Hold: {'; '.join(gates)}]" if gates else " [Safe >=1.4x]"
        tut_prefix = "Tutorial fight: " if tutorial_fight_pending else ""

        o_gal, o_sys, o_pos = _parse_gal_sys_pos(origin.coords)
        phys_dist = abs(o_gal - t_gal) * 20000 + abs(o_sys - t_sys) * 95 + abs(o_pos - pos) * 5
        scored_attacks.append(
            (
                (0 if can_exec else 1, -rec.distance_score, -rec.loot_spice_equivalent, phys_dist),
                PlannedAction(
                    priority="P2",
                    kind="ATTACK",
                    planet_id=origin.planet_id,
                    planet_coords=_safe_strategy_str(origin.coords),
                    target_name=target_label,
                    reason=f"{tut_prefix}{tag} raid {loot_note} (ratio {rec.combat.power_ratio if rec.combat.power_ratio != float('inf') else 'INF'}x){gate_suffix}",
                    executable_now=can_exec,
                ),
            )
        )

    if tutorial_recycle_pending:
        if recycle_actions:
            recycle_actions.sort(key=lambda item: item[0])
            return [act for _, act in recycle_actions[:3]]
        if has_recyclers and not own_fleet_in_flight:
            assert recycler_planet is not None
            r_gal, r_sys, _ = _parse_gal_sys_pos(recycler_planet.coords)
            unscanned_battles: list[tuple[int, float, int, int]] = []
            for skey_b, b_ts in latest_battle_by_sys.items():
                if b_ts > scanned_map.get(skey_b, 0.0):
                    parts_b = skey_b.split(":")
                    if len(parts_b) == 2 and parts_b[0].isdigit() and parts_b[1].isdigit():
                        bg, bs = int(parts_b[0]), int(parts_b[1])
                        if 1 <= bs <= 400:
                            dist_b = abs(r_gal - bg) * 400 + abs(r_sys - bs)
                            unscanned_battles.append((dist_b, -b_ts, bg, bs))
            if unscanned_battles:
                unscanned_battles.sort()
                _, _, gal_b, sys_b = unscanned_battles[0]
                scan_note_r = "" if cfg.allow_galaxy_scan else " (enable allow_galaxy_scan in Config)"
                return [
                    PlannedAction(
                        priority="P2",
                        kind="SCAN_GALAXY",
                        planet_id=recycler_planet.planet_id,
                        planet_coords=_safe_strategy_str(recycler_planet.coords),
                        target_name=f"Galaxy [{gal_b:02d}:{sys_b:03d}]",
                        reason=f"Scan battle system {gal_b:02d}:{sys_b:03d} for space rubble ({_safe_strategy_str(tut_recycle.description)}){scan_note_r}",
                        executable_now=bool(cfg.allow_galaxy_scan),
                    )
                ]

    def _next_spiral_scan_action() -> Optional[PlannedAction]:
        scan_cooldown_s = 1800.0
        primary_scan_p = (
            recycler_planet
            if (tutorial_recycle_pending and recycler_planet is not None)
            else (colony_planet if (want_colonize and colony_planet is not None) else origin)
        )
        base_planets = [primary_scan_p]
        if cfg.allow_multi_planet:
            for p in state.planets:
                if p.planet_id != primary_scan_p.planet_id and _norm_planet_type(p) != "raid":
                    base_planets.append(p)
        colonies = [(p, *_parse_gal_sys_pos(p.coords)[:2]) for p in base_planets]
        max_radius = 25 if (tutorial_recycle_pending or want_colonize) else 15
        offsets = [0] + [off for d in range(1, max_radius + 1) for off in (d, -d)]

        chosen: Optional[tuple[PlanetState, int, int]] = None
        for off in offsets:
            for col_p, col_gal, col_sys in colonies:
                cand_sys = col_sys + off
                if not (1 <= cand_sys <= 400):
                    continue
                skey = f"{col_gal:02d}:{cand_sys:03d}"
                prev_ts = scanned_map.get(skey)
                if prev_ts is not None and (ref_now - prev_ts) < scan_cooldown_s:
                    continue
                chosen = (col_p, col_gal, cand_sys)
                break
            if chosen is not None:
                break

        if chosen is None:
            return None

        scan_planet, gal_num, sys_num = chosen
        if want_colonize:
            tut_hint = f" and {min_colony_fields}+ field colony slots"
        elif tut_recycle is not None:
            tut_hint = f" and space rubble for {_safe_strategy_str(tut_recycle.description)}"
        elif tut_fight is not None:
            tut_hint = f" for {_safe_strategy_str(tut_fight.description)}"
        else:
            tut_hint = ""
        scan_note = "" if cfg.allow_galaxy_scan else " (click Scan Galaxy in Combat or enable allow_galaxy_scan in Config)"
        return PlannedAction(
            priority="P2",
            kind="SCAN_GALAXY",
            planet_id=scan_planet.planet_id,
            planet_coords=_safe_strategy_str(scan_planet.coords),
            target_name=f"Galaxy [{gal_num:02d}:{sys_num:03d}]",
            reason=f"Scan system {gal_num:02d}:{sys_num:03d} for yellow inactive / Spacia raid targets{tut_hint}{scan_note}",
            executable_now=bool(cfg.allow_galaxy_scan),
        )

    # Enforce max_fleet_slots (`1 + b10`) and `reserved_spy_slots` on non-SPY missions
    res_spy_cfg = max(0, _safe_strategy_int(getattr(cfg, "reserved_spy_slots", 1), 1))
    all_spy_planet_ids = {
        sa.planet_id
        for _, sa in [*spy_actions, *colony_spy_actions]
        if sa.executable_now or (cfg.allow_galaxy_scan or cfg.allow_colonize)
    }

    def _gate_non_spy_slot(act: PlannedAction) -> PlannedAction:
        p_obj = next((p for p in state.planets if p.planet_id == act.planet_id), origin)
        max_p_slots = 1 + max(0, p_obj.building_level(FLEET_BASE))
        has_spy_demand_p = (
            res_spy_cfg > 0
            and _safe_strategy_int(p_obj.ships.get("s10", 0), 0) > 0
            and p_obj.planet_id in all_spy_planet_ids
        )
        eff_res = min(res_spy_cfg, max(0, max_p_slots - 1)) if has_spy_demand_p else 0
        if used_fleet_slots >= max_p_slots:
            rsn = act.reason
            if "in flight" not in rsn.lower() and "in transit" not in rsn.lower():
                rsn = f"{rsn} [Hold: Fleet slots full ({used_fleet_slots}/{max_p_slots} in flight)]"
            return replace(act, executable_now=False, reason=rsn) if act.executable_now or rsn != act.reason else act
        if eff_res > 0 and used_fleet_slots >= max_p_slots - eff_res:
            rsn = act.reason
            if "reserved for spy" not in rsn.lower():
                rsn = (
                    f"{rsn} [Hold: Fleet slot reserved for Spy probes "
                    f"({used_fleet_slots}/{max_p_slots} in flight, {eff_res} reserved)]"
                )
            return replace(act, executable_now=False, reason=rsn) if act.executable_now or rsn != act.reason else act
        return act

    colonize_actions = [
        ((0 if (g := _gate_non_spy_slot(act)).executable_now else 1, *sk[1:]), g)
        for sk, act in colonize_actions
    ]
    scored_attacks = [
        ((0 if (g := _gate_non_spy_slot(act)).executable_now else 1, *sk[1:]), g)
        for sk, act in scored_attacks
    ]
    recycle_actions = [
        ((0 if (g := _gate_non_spy_slot(act)).executable_now else 1, *sk[1:]), g)
        for sk, act in recycle_actions
    ]

    if tutorial_spy_pending and spy_actions:
        spy_actions.sort(key=lambda item: item[0])
        out_tut_spy = [act for _, act in spy_actions[:3]]
        if designated_probe_build_act is not None:
            out_tut_spy.insert(0, designated_probe_build_act)
        return out_tut_spy

    if colonize_actions or colony_spy_actions or scored_attacks or recycle_actions:
        out_actions: list[PlannedAction] = []
        if designated_probe_build_act is not None:
            out_actions.append(designated_probe_build_act)
        if colony_spy_actions:
            colony_spy_actions.sort(key=lambda item: item[0])
            out_actions.extend(act for _, act in colony_spy_actions[:2])
        if spy_actions:
            spy_actions.sort(key=lambda item: item[0])
            out_actions.extend(act for _, act in spy_actions[:2])
        if colonize_actions:
            colonize_actions.sort(key=lambda item: item[0])
            out_actions.extend(act for _, act in colonize_actions[:1])
        if scored_attacks:
            scored_attacks.sort(key=lambda item: item[0])
            out_actions.extend(act for _, act in scored_attacks[:3])
        if recycle_actions:
            recycle_actions.sort(key=lambda item: item[0])
            out_actions.extend(act for _, act in recycle_actions[:2])
        if (want_colonize or want_colony_probes) and not colonize_actions and not colony_spy_actions:
            nxt_scan = _next_spiral_scan_action()
            if nxt_scan is not None:
                out_actions.append(nxt_scan)
        return out_actions

    if spy_actions or colony_spy_actions or designated_probe_build_act is not None:
        out_spy_only: list[PlannedAction] = []
        if designated_probe_build_act is not None:
            out_spy_only.append(designated_probe_build_act)
        if colony_spy_actions:
            colony_spy_actions.sort(key=lambda item: item[0])
            out_spy_only.extend(act for _, act in colony_spy_actions[:3])
        if spy_actions:
            spy_actions.sort(key=lambda item: item[0])
            out_spy_only.extend(act for _, act in spy_actions[:3])
        return out_spy_only

    nxt_scan = _next_spiral_scan_action()
    return [nxt_scan] if nxt_scan is not None else []


def _mine_payback_hours(
    planet: PlanetState,
    eff_buildings: dict[str, int],
    building_id: int,
    mining_tech_level: int,
    offers: dict[int, BuildingOffer],
) -> tuple[float, float, int]:
    """Return `(payback_hours, delta_spice_per_hour, next_level)` at Merchant parity."""
    cur_lvl = _eff_level(planet, eff_buildings, building_id)
    nxt_lvl = cur_lvl + 1
    cur_prod = game_data.mine_production_per_hour(
        building_id,
        cur_lvl,
        temperature=planet.temperature,
        mining_tech_level=mining_tech_level,
    )
    nxt_prod = game_data.mine_production_per_hour(
        building_id,
        nxt_lvl,
        temperature=planet.temperature,
        mining_tech_level=mining_tech_level,
    )
    delta_prod = max(0.0, nxt_prod - cur_prod)
    res_key = _MINE_RES_KEY[building_id]
    delta_spice = delta_prod / game_data.MERCHANT_RATES_IN_SPICE[res_key]
    if delta_spice <= 0:
        return 999999.0, 0.0, nxt_lvl

    offer = offers.get(building_id)
    if offer is not None and game_data.safe_optional_int(offer.target_level, None) == nxt_lvl and not offer.cost.is_zero():
        cost_spice = game_data.spice_equivalent(offer.cost)
    else:
        cost_spice = game_data.spice_equivalent(game_data.building_cost(building_id, nxt_lvl))

    payback_h = cost_spice / delta_spice if cost_spice > 0 else 0.0
    return payback_h, delta_spice, nxt_lvl


def _empire_raw_spice_eq_per_hour(state: GameState) -> float:
    """Sum hourly Pig iron + Kryptonite + Spice output across all planets in Spice equivalents."""
    mining_lvl = max(0, _safe_strategy_int((state.researches or {}).get("r1", 0), 0))
    total = 0.0
    for planet in state.planets:
        for bid in (PIG_IRON_MINE, KRYPTONITE_MINE, SPICE_MINE):
            lvl = planet.building_level(bid)
            if lvl <= 0:
                continue
            prod = game_data.mine_production_per_hour(
                bid,
                lvl,
                temperature=planet.temperature,
                mining_tech_level=mining_lvl,
            )
            total += prod / game_data.MERCHANT_RATES_IN_SPICE[_MINE_RES_KEY[bid]]
    return total


def _resolve_prereq_chain(
    category: str,
    slug: str,
    goal_label: str,
    eff_buildings: dict[str, int],
    researches: dict[str, int],
    visited: Optional[set[tuple[str, str]]] = None,
) -> list[dict]:
    """Walk `missing_prerequisites` DFS to surface immediately actionable unlock steps."""
    if visited is None:
        visited = set()
    if (category, slug) in visited:
        return []
    visited.add((category, slug))

    unmet = game_data.missing_prerequisites(
        category,
        slug,
        buildings=eff_buildings,
        researches=researches,
    )
    steps: list[dict] = []
    for req in unmet:
        sub = _resolve_prereq_chain(
            req["category"],
            req["slug"],
            goal_label,
            eff_buildings,
            researches,
            visited,
        )
        if sub:
            steps.extend(sub)
        else:
            steps.append({**req, "goal": goal_label})
    return steps


def _plan_unlock_and_advisory_actions(
    state: GameState,
    planet: PlanetState,
    eff_buildings: dict[str, int],
    offers: dict[int, BuildingOffer],
    research_offers: dict[str, ResearchOffer],
    ship_offers: dict[int, UnitOffer],
    queue_full: bool,
    research_queue_busy: bool,
    allow_remote_build: bool,
    config: StrategyConfig,
) -> tuple[list[PlannedAction], list[PlannedAction]]:
    """Plan Research lab/Weapons factory unlocks, research ROI steps, and utility ship builds."""
    build_unlocks: list[PlannedAction] = []
    advisories: list[PlannedAction] = []

    pig = _eff_level(planet, eff_buildings, PIG_IRON_MINE)
    blast = _eff_level(planet, eff_buildings, BLAST_FURNACE)
    kryp = _eff_level(planet, eff_buildings, KRYPTONITE_MINE)
    dev = _eff_level(planet, eff_buildings, DEVELOPMENT_CENTER)
    lab = _eff_level(planet, eff_buildings, RESEARCH_LAB)
    wf = _eff_level(planet, eff_buildings, WEAPONS_FACTORY)

    base_ready = min(pig, blast, kryp) >= 3 or (config.merchant_mode and min(pig, kryp) >= 3)

    # Step toward Research lab (`b8`, requires Development center L2)
    if base_ready and lab == 0 and _is_building_allowed_on_planet(planet, RESEARCH_LAB, offers):
        unmet_lab = game_data.missing_prerequisites(
            "buildings",
            RESEARCH_LAB,
            buildings=eff_buildings,
            researches=state.researches,
        )
        if unmet_lab:
            for req in unmet_lab:
                if req["category"] == "buildings":
                    req_info = game_data.resolve_unit("buildings", req["slug"])
                    if req_info and req_info.get("id"):
                        bid = int(req_info["id"])
                        if not _is_building_allowed_on_planet(planet, bid, offers):
                            continue
                        act = _make_build_action(
                            "P2",
                            planet,
                            bid,
                            str(req["name"]),
                            f"Prerequisite for Research lab ({game_data.fmt_compact_int(req['current'])}/{game_data.fmt_compact_int(req['required'])})",
                            offers,
                            effective_level=_eff_level(planet, eff_buildings, bid),
                            researches=state.researches,
                            allow_remote_colony_build=allow_remote_build,
                        )
                        build_unlocks.append(_apply_queue_cap_if_full(act, queue_full))
        else:
            act = _make_build_action(
                "P2",
                planet,
                RESEARCH_LAB,
                "Research lab",
                "Unlock empire research and Mining/Energy/Engine technologies",
                offers,
                effective_level=lab,
                researches=state.researches,
                allow_remote_colony_build=allow_remote_build,
            )
            build_unlocks.append(_apply_queue_cap_if_full(act, queue_full))

    # Step toward Weapons factory (`b9`, requires Development center L3 + Energy tech L1)
    if min(pig, kryp) >= 4 and lab >= 1 and wf == 0 and _is_building_allowed_on_planet(planet, WEAPONS_FACTORY, offers):
        unmet_wf = game_data.missing_prerequisites(
            "buildings",
            WEAPONS_FACTORY,
            buildings=eff_buildings,
            researches=state.researches,
        )
        if unmet_wf:
            for req in unmet_wf:
                if req["category"] == "buildings":
                    req_info = game_data.resolve_unit("buildings", req["slug"])
                    if req_info and req_info.get("id"):
                        bid = int(req_info["id"])
                        if not _is_building_allowed_on_planet(planet, bid, offers):
                            continue
                        act = _make_build_action(
                            "P2",
                            planet,
                            bid,
                            str(req["name"]),
                            f"Prerequisite for Weapons factory ({game_data.fmt_compact_int(req['current'])}/{game_data.fmt_compact_int(req['required'])})",
                            offers,
                            effective_level=_eff_level(planet, eff_buildings, bid),
                            researches=state.researches,
                            allow_remote_colony_build=allow_remote_build,
                        )
                        build_unlocks.append(_apply_queue_cap_if_full(act, queue_full))
                elif req["category"] == "research":
                    res_info = game_data.resolve_unit("research", req["slug"])
                    rkey = str((res_info or {}).get("id") or "r2")
                    advisories.append(
                        _make_research_action(
                            "P3",
                            planet,
                            state,
                            rkey,
                            f"Unlock prerequisite for Weapons factory ({game_data.fmt_compact_int(req['current'])}/{game_data.fmt_compact_int(req['required'])})",
                            research_offers,
                            research_queue_busy,
                            allow_live=config.allow_research,
                        )
                    )
        else:
            act = _make_build_action(
                "P2",
                planet,
                WEAPONS_FACTORY,
                "Weapons factory",
                "Unlock fleet & solar cell construction",
                offers,
                effective_level=wf,
                researches=state.researches,
                allow_remote_colony_build=allow_remote_build,
            )
            build_unlocks.append(_apply_queue_cap_if_full(act, queue_full))

    if base_ready and lab >= 1 and dev < max(2, min(pig, kryp) - 2) and _is_building_allowed_on_planet(planet, DEVELOPMENT_CENTER, offers):
        act = _make_build_action(
            "P2",
            planet,
            DEVELOPMENT_CENTER,
            "Development center",
            "Cut local building times and scale infrastructure",
            offers,
            effective_level=dev,
            researches=state.researches,
            allow_remote_colony_build=allow_remote_build,
        )
        build_unlocks.append(_apply_queue_cap_if_full(act, queue_full))

    # Scale Fleet base (`b10`) to increase concurrent fleet flight slots (`1 + b10` max simultaneous flights)
    fb = _eff_level(planet, eff_buildings, FLEET_BASE)
    in_flight_fleets = sum(
        1 for q in (getattr(state, "active_queues", ()) or ()) if is_in_flight_fleet_job(q)
    )
    mission_ships = sum(
        max(0, _safe_strategy_int(cnt, 0))
        for skey, cnt in (getattr(planet, "ships", None) or {}).items()
        if skey != "s9"
    )
    if (
        wf >= 1
        and _planet_has_free_building_fields(planet)
        and _is_building_allowed_on_planet(planet, FLEET_BASE, offers)
    ):
        slots_saturated = in_flight_fleets >= (1 + fb)
        target_fb = 0
        if slots_saturated:
            target_fb = max(fb + 1, in_flight_fleets + 1)
        elif mission_ships >= 1 or in_flight_fleets > 0 or (config.playstyle == "fleeter" and min(pig, kryp) >= 8):
            target_fb = min(15, max(2, dev // 2 + (2 if config.playstyle == "fleeter" else 1)))
        if fb < target_fb:
            fb_prio = "P2" if (slots_saturated or fb < 2 or in_flight_fleets >= max(1, fb)) else "P3"
            fb_reason = (
                f"Expand Fleet base to L{fb + 1} (+1 concurrent flight slot: "
                f"{1 + fb} -> {2 + fb} flights; {in_flight_fleets}/{1 + fb} currently in transit)"
                if slots_saturated
                else f"Increase Fleet base to L{fb + 1} ({1 + fb} -> {2 + fb} concurrent fleet flights)"
            )
            fb_act = _make_build_action(
                fb_prio,
                planet,
                FLEET_BASE,
                "Fleet base",
                fb_reason,
                offers,
                effective_level=fb,
                researches=state.researches,
                allow_remote_colony_build=allow_remote_build,
            )
            capped_fb = _apply_queue_cap_if_full(fb_act, queue_full)
            if fb_prio == "P2":
                build_unlocks.append(capped_fb)
            else:
                advisories.append(capped_fb)

    # Cross-planet Mining tech (`r1`) ROI + Energy/Ship unlock steps once Research lab L1 exists
    is_raid = _norm_planet_type(planet) == "raid"
    if lab >= 1 and not is_raid:
        mining_lvl = max(0, _safe_strategy_int((state.researches or {}).get("r1", 0), 0))
        r1_info = game_data.RESEARCH_BY_ID.get("r1")
        if r1_info and not game_data.missing_prerequisites(
            "research", "r1", buildings=eff_buildings, researches=state.researches
        ):
            empire_raw = _empire_raw_spice_eq_per_hour(state)
            delta_r1 = 0.02 * empire_raw
            r1_cost_d = r1_info.get("levels", {}).get(mining_lvl + 1, {}).get("cost", {})
            r1_cost_sp = game_data.spice_equivalent(r1_cost_d)
            if delta_r1 > 0 and r1_cost_sp > 0:
                pb = r1_cost_sp / delta_r1
                reason = (
                    f"+2% Pig/Kryp/Spice across {len(state.planets)} planet(s): "
                    f"+{delta_r1:.1f} Spice-eq/h ({pb:.1f}h payback)"
                )
            else:
                reason = "+2% Pig iron, Kryptonite & Spice output across all planets"
            advisories.append(
                _make_research_action(
                    "P3",
                    planet,
                    state,
                    "r1",
                    reason,
                    research_offers,
                    research_queue_busy,
                    allow_live=config.allow_research,
                )
            )
        elif max(0, _safe_strategy_int((state.researches or {}).get("r2", 0), 0)) < 4 and 2 not in {
            a.target_id for a in advisories if a.kind == "RESEARCH"
        }:
            r2_cur = max(0, _safe_strategy_int((state.researches or {}).get("r2", 0), 0))
            advisories.append(
                _make_research_action(
                    "P3",
                    planet,
                    state,
                    "r2",
                    f"Global +1% energy & prerequisite L4 for Mining tech ({game_data.fmt_compact_int(r2_cur)}/4)",
                    research_offers,
                    research_queue_busy,
                    allow_live=config.allow_research,
                )
            )

        goal_ships = (
            ("small-transport", "spy-probe", "recycler", "colony-ship", "phoenix", "transmitter", "battleship", "stealth-bomber")
            if config.playstyle == "fleeter"
            else ("small-transport", "colony-ship")
        )
        planned_ship_names = {
            a.target_name for a in advisories if a.kind in ("BUILD_SHIP", "SHIP")
        }
        norm_shipyard_targets = _resolve_shipyard_targets(config.shipyard_targets)
        if wf >= 1 and config.allow_shipyard:
            for _, raw_t_cnt in norm_shipyard_targets.items():
                if raw_t_cnt > 0:
                    planned_ship_names.add("Small transport")
                    break
        for ship_slug in goal_ships:
            ship_info = game_data.SHIPS.get(ship_slug)
            if not ship_info or str(ship_info["name"]) in planned_ship_names:
                continue
            steps = _resolve_prereq_chain(
                "ships",
                ship_slug,
                str(ship_info["name"]),
                eff_buildings,
                state.researches,
            )
            if not steps:
                ship_cost = _resources_from_dict(ship_info.get("base_cost", {}))
                advisories.append(
                    PlannedAction(
                        priority="P3",
                        kind="SHIP",
                        planet_id=planet.planet_id,
                        planet_coords=_safe_strategy_str(planet.coords),
                        target_name=str(ship_info["name"]),
                        cost=ship_cost,
                        missing=planet.resources.missing(ship_cost),
                        reason=f"{ship_info['name']} unlocked (trade/expedition/colonization ready)",
                        executable_now=False,
                    )
                )
            else:
                first_res = next((s for s in steps if s["category"] == "research"), None)
                if first_res:
                    res_info = game_data.resolve_unit("research", first_res["slug"])
                    rkey = str((res_info or {}).get("id") or "")
                    if rkey and int(rkey[1:]) not in {a.target_id for a in advisories if a.kind == "RESEARCH"}:
                        advisories.append(
                            _make_research_action(
                                "P3",
                                planet,
                                state,
                                rkey,
                                f"Tech step ({game_data.fmt_compact_int(first_res['current'])}/{game_data.fmt_compact_int(first_res['required'])}) toward {first_res['goal']}",
                                research_offers,
                                research_queue_busy,
                                allow_live=config.allow_research,
                            )
                        )
                else:
                    first_bld = next((s for s in steps if s["category"] == "buildings"), None)
                    if first_bld and base_ready and _planet_has_free_building_fields(planet):
                        bld_info = game_data.resolve_unit("buildings", first_bld["slug"])
                        req_bid = int(bld_info["id"]) if (bld_info and bld_info.get("id") is not None) else 0
                        if (
                            req_bid > 0
                            and _is_building_allowed_on_planet(planet, req_bid, offers)
                            and req_bid not in {a.target_id for a in build_unlocks}
                        ):
                            act = _make_build_action(
                                "P3",
                                planet,
                                req_bid,
                                str(first_bld["name"]),
                                f"Building step ({game_data.fmt_compact_int(first_bld['current'])}/{game_data.fmt_compact_int(first_bld['required'])}) toward {first_bld['goal']}",
                                offers,
                                effective_level=_eff_level(planet, eff_buildings, req_bid),
                                researches=state.researches,
                                allow_remote_colony_build=allow_remote_build,
                            )
                            advisories.append(_apply_queue_cap_if_full(act, queue_full))

    # Utility & Colony Shipyard targets whenever Weapons factory is built (`wf >= 1`)
    if wf >= 1 and config.allow_shipyard and not is_raid:
        slug_to_skey = {slug: skey for skey, slug in SHIP_SLUG_BY_KEY.items()}
        tutorial_ship_ids: set[int] = set()
        if state.tutorial and not state.tutorial.can_complete:
            for t in state.tutorial.tasks:
                if t.completed:
                    continue
                t_bid = game_data.safe_optional_int(t.building_id, None)
                if t.category == "ship" and t_bid is not None:
                    tutorial_ship_ids.add(t_bid)
                elif t.category == "milestone":
                    h_sid = _milestone_helper_ship_id(_safe_strategy_str(t.description).lower())
                    if h_sid is not None:
                        tutorial_ship_ids.add(h_sid)

        waiting_ships: list[tuple[float, PlannedAction]] = []
        norm_shipyard_targets_2 = _resolve_shipyard_targets(config.shipyard_targets)
        for ship_slug, target_cnt in norm_shipyard_targets_2.items():
            if target_cnt <= 0:
                continue
            if (
                ship_slug == "solar-cells"
                and _norm_planet_type(planet) != "asteroid"
                and bool(getattr(config, "forbid_planetary_solar", False))
                and 9 not in tutorial_ship_ids
            ):
                continue
            skey = slug_to_skey.get(ship_slug)
            if not skey or len(skey) > 8 or not skey[1:].isascii() or not skey[1:].isdigit():
                continue
            sid_num = int(skey[1:])
            offer = ship_offers.get(sid_num)
            if offer is None:
                unmet_ship = game_data.missing_prerequisites(
                    "ships",
                    ship_slug,
                    buildings=planet.buildings,
                    researches=state.researches,
                )
                if unmet_ship:
                    continue

            owned = max(0, _safe_strategy_int(planet.ships.get(skey, offer.owned if offer is not None else 0), 0))
            need = target_cnt - owned
            if need <= 0:
                continue

            if offer is not None:
                ship_name = _safe_strategy_str(offer.name) or ship_slug
                cost = offer.cost
            else:
                info = game_data.resolve_unit("ships", ship_slug) or {}
                ship_name = str(info.get("name") or ship_slug)
                cost = _resources_from_dict(info.get("base_cost", {}))

            missing = planet.resources.missing(cost)
            missing_b = [
                m
                for m in game_data.missing_prerequisites(
                    "ships",
                    ship_slug,
                    buildings=planet.buildings,
                    researches=state.researches,
                )
                if m.get("category") == "buildings"
            ]
            own_fleet_in_flight = any(is_in_flight_fleet_job(q) for q in state.active_queues)
            hold_for_in_flight = (
                own_fleet_in_flight
                and sid_num not in tutorial_ship_ids
                and sid_num in (1, 2, 3, 4, 5, 6, 7, 13, 14, 15, 16)
            )
            has_wf_now = planet.building_level(WEAPONS_FACTORY) >= 1 and not missing_b
            can_exec = (
                not is_raid
                and not hold_for_in_flight
                and has_wf_now
                and offer is not None
                and offer.can_build
                and planet.resources.can_afford(cost)
            )
            ship_reason = f"Fleet build target ({game_data.fmt_compact_int(owned)}/{game_data.fmt_compact_int(target_cnt)} {ship_name})"
            if hold_for_in_flight:
                ship_reason += " (hold while own fleet is in transit)"
            elif planet.building_level(WEAPONS_FACTORY) < 1:
                ship_reason += " (Weapons factory b9 required)"
            elif missing_b:
                first_req = missing_b[0]
                ship_reason += (
                    f" (requires {first_req['name']} "
                    f"{game_data.fmt_compact_int(first_req['current'])}/{game_data.fmt_compact_int(first_req['required'])})"
                )
            ship_act = PlannedAction(
                priority="P2",
                kind="BUILD_SHIP",
                planet_id=planet.planet_id,
                planet_coords=_safe_strategy_str(planet.coords),
                target_id=sid_num,
                target_name=ship_name,
                target_level=1,
                cost=cost,
                missing=missing,
                reason=ship_reason,
                executable_now=can_exec,
            )
            if can_exec:
                advisories.append(ship_act)
            elif sid_num not in tutorial_ship_ids:
                waiting_ships.append((game_data.spice_equivalent(missing), ship_act))

        waiting_ships.sort(key=lambda pair: pair[0])
        for _, wait_act in waiting_ships[:3]:
            advisories.append(wait_act)

    return build_unlocks, advisories


def _plan_economy_for_planet(
    state: GameState,
    planet: PlanetState,
    offers: dict[int, BuildingOffer],
    research_offers: dict[str, ResearchOffer],
    ship_offers: dict[int, UnitOffer],
    eff_buildings: dict[str, int],
    projected_net: int,
    queue_full: bool,
    research_queue_busy: bool,
    allow_remote_build: bool,
    config: StrategyConfig,
) -> tuple[list[tuple[float, PlannedAction]], list[PlannedAction], list[PlannedAction]]:
    """Return `(scored_mines, power_or_unlock_actions, advisories)` for one planet."""
    pig_lvl = _eff_level(planet, eff_buildings, PIG_IRON_MINE)
    blast_lvl = _eff_level(planet, eff_buildings, BLAST_FURNACE)
    mining_tech = max(0, _safe_strategy_int((state.researches or {}).get("r1", 0), 0))

    temp_i = _safe_strategy_int(planet.temperature, 0)
    temp_s = game_data.fmt_compact_int(temp_i)
    fields_rem_i = _safe_strategy_int(planet.fields_remaining, 0)
    pig_prod = game_data.mine_production_per_hour(
        PIG_IRON_MINE,
        pig_lvl,
        temperature=temp_i,
        mining_tech_level=mining_tech,
    )
    blast_used_next = game_data.blast_furnace_pig_iron_used(blast_lvl + 1)
    blast_pig_capped = pig_prod <= 0 or (blast_used_next > pig_prod * config.blast_pig_iron_max_share)
    rates = getattr(planet, "economy_rates", None) or {}
    cur_b2_pct = max(0, min(100, _safe_strategy_int(rates.get("resource2", 100), 100)))
    tut_needs_b2 = bool(
        state.tutorial
        and not state.tutorial.can_complete
        and any(
            not t.completed and t.category == "building" and t.building_id == BLAST_FURNACE
            for t in state.tutorial.tasks
        )
    )
    res_pig_eco = _safe_strategy_int(getattr(planet.resources, "pig_iron", 0), 0)
    res_met_eco = _safe_strategy_int(getattr(planet.resources, "metal", 0), 0)
    sto_pig_eco = _safe_strategy_int(getattr(planet.storage, "pig_iron", 0), 0)
    sto_met_eco = _safe_strategy_int(getattr(planet.storage, "metal", 0), 0)
    metal_full_pig_space_eco = (
        sto_met_eco > 0
        and res_met_eco * 100 >= sto_met_eco * 98
        and (sto_pig_eco <= 0 or res_pig_eco * 100 < sto_pig_eco * 99)
    )
    b2_offer = offers.get(BLAST_FURNACE)
    blast_paused_hold = (
        (cur_b2_pct == 0 or metal_full_pig_space_eco)
        and blast_lvl >= 1
        and config.auto_throttle_blast_furnace
        and not config.merchant_mode
        and not tut_needs_b2
        and (b2_offer is None or b2_offer.cost.metal == 0)
    )

    scored_mines: list[tuple[float, int, int, str]] = []
    deferred_blast: Optional[tuple[float, int, int, str]] = None

    for bid in _MINE_IDS:
        if not _is_building_allowed_on_planet(planet, bid, offers):
            continue
        if bid == BLAST_FURNACE and config.merchant_mode:
            continue
        payback_h, delta_sp, nxt_lvl = _mine_payback_hours(
            planet,
            eff_buildings,
            bid,
            mining_tech,
            offers,
        )
        name = _MINE_LABELS[bid]
        nxt_lvl_s = game_data.fmt_compact_int(nxt_lvl)
        reason = f"Best mine ROI ({payback_h:.1f}h Spice payback, +{delta_sp:.1f} Spice-eq/h @ {temp_s}C)"
        if bid == BLAST_FURNACE:
            if blast_paused_hold:
                deferred_blast = (
                    payback_h,
                    bid,
                    nxt_lvl,
                    f"Blast furnace L{game_data.fmt_compact_int(blast_lvl)} is paused at 0%; resume Metal smelting before upgrading to L{nxt_lvl_s}",
                )
                continue
            if blast_pig_capped:
                deferred_blast = (
                    payback_h,
                    bid,
                    nxt_lvl,
                    f"Blast furnace L{nxt_lvl_s} needs {blast_used_next:.0f} Pig iron/h vs {pig_prod:.0f}/h produced; expand Pig iron first",
                )
                continue
            reason = (
                f"Blast furnace ROI ({payback_h:.1f}h payback; uses {blast_used_next:.0f}/{pig_prod:.0f} Pig iron/h)"
            )
        scored_mines.append((payback_h, bid, nxt_lvl, reason))

    scored_mines.sort(key=lambda row: (row[0], row[1]))

    priority_builds: list[PlannedAction] = []
    has_free_fields = _planet_has_free_building_fields(planet)
    if scored_mines and has_free_fields:
        _, top_bid, top_nxt, _ = scored_mines[0]
        starve_delta = _energy_starvation_delta(top_bid, top_nxt, projected_net)
        if starve_delta < 0 and _safe_strategy_int(planet.energy.net, 0) >= 0:
            forbid_solar = bool(getattr(config, "forbid_planetary_solar", False))
            power_bid, power_name, power_note = _pick_best_power_building(
                planet,
                eff_buildings,
                state.researches,
                forbid_planetary_solar=forbid_solar,
            )
            # Solar cells (`s9`) on Asteroids (and hot tight-field colonies only when `forbid_planetary_solar=False`)
            solar_offer = ship_offers.get(SOLAR_CELLS_ID)
            hot_tight_s9 = (
                not forbid_solar
                and _norm_planet_type(planet) == "normal"
                and _safe_strategy_int(planet.fields_max, 0) > 0
                and 0 < fields_rem_i <= _safe_strategy_int(config.min_free_fields_warning, 20)
                and temp_i >= _safe_strategy_int(config.hot_planet_solar_temp_threshold, 40)
            )
            if (
                (_norm_planet_type(planet) == "asteroid" or hot_tight_s9)
                and planet.building_level(WEAPONS_FACTORY) >= 1
                and solar_offer is not None
                and solar_offer.can_build
                and config.allow_shipyard
            ):
                s9_lbl = "asteroid" if _norm_planet_type(planet) == "asteroid" else "colony"
                priority_builds.append(
                    PlannedAction(
                        priority="P1",
                        kind="BUILD_SHIP",
                        planet_id=planet.planet_id,
                        planet_coords=_safe_strategy_str(planet.coords),
                        target_id=SOLAR_CELLS_ID,
                        target_name="Solar cells",
                        target_level=1,
                        cost=solar_offer.cost,
                        missing=planet.resources.missing(solar_offer.cost),
                        reason=(
                            f"0-field orbital power on {temp_s}C {s9_lbl} "
                            f"({game_data.fmt_compact_int(fields_rem_i)} fields left)"
                        ),
                        executable_now=planet.resources.can_afford(solar_offer.cost),
                    )
                )
            elif _is_building_allowed_on_planet(planet, power_bid, offers):
                power_act = _make_build_action(
                    "P1",
                    planet,
                    power_bid,
                    power_name,
                    (
                        f"Proactive power ({power_note}) for {_MINE_LABELS[top_bid]} L{game_data.fmt_compact_int(top_nxt)} "
                        f"(needs {game_data.fmt_compact_int(-starve_delta)} energy, net {game_data.fmt_compact_int(projected_net, signed=True)})"
                    ),
                    offers,
                    effective_level=_eff_level(planet, eff_buildings, power_bid),
                    researches=state.researches,
                    allow_remote_colony_build=allow_remote_build,
                )
                priority_builds.append(_apply_queue_cap_if_full(power_act, queue_full))

    unlocks, advisories = _plan_unlock_and_advisory_actions(
        state,
        planet,
        eff_buildings,
        offers,
        research_offers,
        ship_offers,
        queue_full,
        research_queue_busy,
        allow_remote_build,
        config,
    )
    priority_builds.extend(unlocks)

    mine_actions: list[tuple[float, PlannedAction]] = []
    for payback_h, bid, nxt_lvl, reason in scored_mines:
        act = _make_build_action(
            "P2",
            planet,
            bid,
            _MINE_LABELS[bid],
            reason,
            offers,
            effective_level=nxt_lvl - 1,
            researches=state.researches,
            allow_remote_colony_build=allow_remote_build,
        )
        mine_actions.append((payback_h, _apply_queue_cap_if_full(act, queue_full)))

    if deferred_blast is not None:
        pb, bid, nxt_lvl, reason = deferred_blast
        act = _make_build_action(
            "P3",
            planet,
            bid,
            _MINE_LABELS[bid],
            reason,
            offers,
            effective_level=nxt_lvl - 1,
            researches=state.researches,
            allow_remote_colony_build=allow_remote_build,
        )
        capped_act = _apply_queue_cap_if_full(act, queue_full)
        if blast_paused_hold and capped_act.executable_now:
            capped_act = replace(
                capped_act,
                kind="WAIT_RESOURCES",
                executable_now=False,
            )
        advisories.append(capped_act)

    # Reserve energy and proactively scale power when Blast furnace is paused (0%) and Metal is needed
    if (
        cur_b2_pct == 0
        and blast_lvl >= 1
        and config.auto_throttle_blast_furnace
        and not config.merchant_mode
        and has_free_fields
    ):
        b2_latent_energy = abs(
            _safe_strategy_int(
                game_data.building_level_stats(BLAST_FURNACE, blast_lvl).get("energy", 0) or 0,
                0,
            )
        )
        colony_needs_metal = any(
            a.missing.metal > 0
            for a in (*unlocks, *advisories, *(m for _, m in mine_actions))
        )
        if colony_needs_metal and b2_latent_energy > 0:
            power_bid, power_name, power_note = _pick_best_power_building(
                planet,
                eff_buildings,
                state.researches,
                forbid_planetary_solar=bool(getattr(config, "forbid_planetary_solar", False)),
            )
            already_has_power = any(
                a.target_id in (FUSION_PLANT, THERMOSOLAR_PLANT, SOLAR_CELLS_ID)
                for a in priority_builds
            )
            if (
                projected_net < b2_latent_energy
                and _safe_strategy_int(planet.energy.net, 0) >= 0
                and not already_has_power
                and _is_building_allowed_on_planet(planet, power_bid, offers)
            ):
                p1_bf_pw = _make_build_action(
                    "P1",
                    planet,
                    power_bid,
                    power_name,
                    (
                        f"Proactive power ({power_note}) to resume Blast furnace L{game_data.fmt_compact_int(blast_lvl)} "
                        f"(needs {game_data.fmt_compact_int(b2_latent_energy)} energy, net {game_data.fmt_compact_int(projected_net, signed=True)})"
                    ),
                    offers,
                    effective_level=_eff_level(planet, eff_buildings, power_bid),
                    researches=state.researches,
                    allow_remote_colony_build=allow_remote_build,
                )
                priority_builds.insert(0, _apply_queue_cap_if_full(p1_bf_pw, queue_full))
            elif projected_net >= b2_latent_energy:
                pw_offer = offers.get(power_bid)
                pw_already_buildable = bool(
                    not queue_full
                    and pw_offer is not None
                    and pw_offer.can_build
                    and planet.resources.can_afford(pw_offer.cost)
                )
                if not pw_already_buildable:
                    net_after_b2 = projected_net - b2_latent_energy
                    held_any_mine = False
                    guarded_mines: list[tuple[float, PlannedAction]] = []
                    for pb_h, m_act in mine_actions:
                        m_tid = game_data.safe_optional_int(m_act.target_id, None)
                        m_lvl = _safe_strategy_int(m_act.target_level, 0)
                        if (
                            m_act.executable_now
                            and m_tid is not None
                            and _energy_starvation_delta(m_tid, m_lvl, net_after_b2) < 0
                        ):
                            held_any_mine = True
                            guarded_mines.append(
                                (
                                    pb_h,
                                    replace(
                                        m_act,
                                        kind="WAIT_RESOURCES",
                                        executable_now=False,
                                        reason=(
                                            f"{m_act.reason} (hold until power covers paused Blast furnace "
                                            f"L{game_data.fmt_compact_int(blast_lvl)} +{game_data.fmt_compact_int(b2_latent_energy)} energy)"
                                        ),
                                    ),
                                )
                            )
                        else:
                            guarded_mines.append((pb_h, m_act))
                    mine_actions = guarded_mines
                    if (
                        held_any_mine
                        and not already_has_power
                        and _is_building_allowed_on_planet(planet, power_bid, offers)
                    ):
                        p2_bf_pw = _make_build_action(
                            "P2",
                            planet,
                            power_bid,
                            power_name,
                            (
                                f"Scale power ({power_note}) so Blast furnace L{game_data.fmt_compact_int(blast_lvl)} "
                                f"can resume 100% alongside next mine"
                            ),
                            offers,
                            effective_level=_eff_level(planet, eff_buildings, power_bid),
                            researches=state.researches,
                            allow_remote_colony_build=allow_remote_build,
                        )
                        priority_builds.append(_apply_queue_cap_if_full(p2_bf_pw, queue_full))

    return mine_actions, priority_builds, advisories


def _plan_blast_furnace_throttle(
    state: GameState,
    planet: PlanetState,
    existing_plan: list[PlannedAction],
    cfg: StrategyConfig,
    *,
    last_throttle_ts: Optional[float] = None,
    now_ts: Optional[float] = None,
    storage_warn_ratio: Optional[float] = None,
    _check_bounce: bool = True,
) -> Optional[PlannedAction]:
    """Pause Blast furnace (`0%`) to stop 2:1 Pig iron drain when Iron-starved, or resume (`100%`)."""
    if _norm_planet_type(planet) in ("asteroid", "raid"):
        return None
    b2_lvl = planet.building_level(BLAST_FURNACE)
    if b2_lvl <= 0:
        return None

    rates = getattr(planet, "economy_rates", None) or {}
    cur_b2_pct = max(0, min(100, _safe_strategy_int(rates.get("resource2", 100), 100)))
    cur_b1_pct = max(0, min(100, _safe_strategy_int(rates.get("resource1", 100), 100)))

    e_prod = _safe_strategy_int(getattr(planet.energy, "produced", 0), 0)
    e_used = _safe_strategy_int(getattr(planet.energy, "used", 0), 0)
    e_net = _safe_strategy_int(getattr(planet.energy, "net", 0), 0)
    res_pig = _safe_strategy_int(getattr(planet.resources, "pig_iron", 0), 0)
    res_met = _safe_strategy_int(getattr(planet.resources, "metal", 0), 0)
    sto_pig = _safe_strategy_int(getattr(planet.storage, "pig_iron", 0), 0)
    sto_met = _safe_strategy_int(getattr(planet.storage, "metal", 0), 0)
    pig_has_space = sto_pig <= 0 or res_pig * 100 < sto_pig * 99
    metal_is_full = sto_met > 0 and res_met * 100 >= sto_met * 98
    metal_full_with_pig_space = metal_is_full and pig_has_space

    ref_now = _safe_strategy_float(now_ts, time.time()) if now_ts is not None else time.time()
    last_t_val = _safe_strategy_float(last_throttle_ts, -1.0) if last_throttle_ts is not None else None
    if last_t_val is not None and last_t_val >= 0.0 and (ref_now - last_t_val) < 300.0:
        if not ((e_net < 0 or metal_full_with_pig_space) and cur_b2_pct > 0):
            return None

    mining_tech = max(0, _safe_strategy_int((state.researches or {}).get("r1", 0), 0))
    speedup = _safe_strategy_float(getattr(state, "speedup_factor", 1.0), 1.0)
    if speedup <= 0:
        speedup = 1.0
    prod_ratio = game_data.safe_big_ratio(e_prod, e_used, cap=1.0) if (e_used > 0 and e_prod < e_used) else 1.0
    factor = speedup * prod_ratio

    p_pig_gross = (
        game_data.mine_production_per_hour(
            PIG_IRON_MINE,
            planet.building_level(PIG_IRON_MINE),
            temperature=_safe_strategy_int(planet.temperature, 0),
            mining_tech_level=mining_tech,
        )
        * factor
        * (max(0, cur_b1_pct) / 100.0)
    )
    bf_pig_used_full = game_data.blast_furnace_pig_iron_used(b2_lvl) * factor
    net_pig_at_100 = p_pig_gross - bf_pig_used_full

    p_acts = [
        a
        for a in existing_plan
        if a.planet_id == planet.planet_id
        and a.kind in ("WAIT_RESOURCES", "BUILD", "RESEARCH", "BUILD_SHIP")
    ]
    prio_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    p_acts.sort(key=lambda a: prio_order.get(a.priority, 9))

    top_blocker = next((a for a in p_acts if a.kind == "WAIT_RESOURCES"), None)
    top_needs_pig_only = (
        top_blocker is not None
        and top_blocker.missing.pig_iron > 0
        and top_blocker.missing.metal == 0
    )
    top_needs_metal = (
        top_blocker is not None
        and top_blocker.missing.metal > 0
        and (top_blocker.missing.pig_iron == 0 or res_pig >= 2000)
    )
    # When Blast furnace is active (>0%), scan full planned queue so P1 power items
    # or 3 affordable builds never mask Metal demand; preserve p_acts[:3] when paused (0%)
    # so the Pig-iron resume deadband remains unchanged.
    metal_scope = p_acts if cur_b2_pct > 0 else p_acts[:3]
    any_needs_metal = top_needs_metal or any(a.missing.metal > 0 for a in metal_scope)
    warn_ratio = _resolve_storage_warn_ratio(cfg, storage_warn_ratio)
    metal_cap_pct = game_data.safe_big_ratio(res_met, sto_met, cap=1e200) if sto_met > 0 else 0.0

    b2_full_energy = abs(_safe_strategy_int(game_data.building_level_stats(BLAST_FURNACE, b2_lvl).get("energy", 0) or 0, 0))
    pause_pig_floor = math.ceil(max(500.0, bf_pig_used_full * 0.5)) if math.isfinite(bf_pig_used_full) else 500
    pause_pig_full = math.ceil(bf_pig_used_full) if math.isfinite(bf_pig_used_full) else 0
    top_pig_storage_capped = bool(
        top_blocker is not None and sto_pig > 0 and top_blocker.cost.pig_iron > sto_pig
    )
    metal_starved_with_positive_pig = bool(
        any_needs_metal and net_pig_at_100 >= 0 and res_pig >= pause_pig_floor
    )
    candidate: Optional[PlannedAction] = None

    if cur_b2_pct > 0:
        if metal_full_with_pig_space:
            candidate = PlannedAction(
                priority="P1",
                kind="THROTTLE_BUILDING",
                planet_id=planet.planet_id,
                planet_coords=_safe_strategy_str(planet.coords),
                target_id=BLAST_FURNACE,
                target_name="Blast furnace 0%",
                target_level=0,
                reason=(
                    f"Metal storage full ({metal_cap_pct * 100:.0f}%) while Pig iron has free capacity — "
                    f"disable Blast furnace (0%) to avoid throwing Metal away and save +{bf_pig_used_full:.0f} Pig iron/h"
                ),
                executable_now=True,
            )
        elif (
            top_needs_pig_only
            and not top_pig_storage_capped
            and not metal_starved_with_positive_pig
        ) or (
            not any_needs_metal
            and net_pig_at_100 < 0
            and res_pig < pause_pig_floor
        ):
            candidate = PlannedAction(
                priority="P1",
                kind="THROTTLE_BUILDING",
                planet_id=planet.planet_id,
                planet_coords=_safe_strategy_str(planet.coords),
                target_id=BLAST_FURNACE,
                target_name="Blast furnace 0%",
                target_level=0,
                reason=(
                    f"Pause Blast furnace (currently {cur_b2_pct}%) to save "
                    f"+{bf_pig_used_full:.0f} Pig iron/h while Pig iron is the bottleneck"
                ),
                executable_now=True,
            )
        elif not any_needs_metal and (
            metal_cap_pct >= warn_ratio
            or (net_pig_at_100 < 0 and res_pig < pause_pig_full)
        ):
            candidate = PlannedAction(
                priority="P2",
                kind="THROTTLE_BUILDING",
                planet_id=planet.planet_id,
                planet_coords=_safe_strategy_str(planet.coords),
                target_id=BLAST_FURNACE,
                target_name="Blast furnace 0%",
                target_level=0,
                reason=(
                    f"Blast furnace drains {bf_pig_used_full:.0f} Pig iron/h; "
                    "pause to 0% to prioritize Pig iron stockpiling"
                ),
                executable_now=True,
            )
        else:
            return None
    else:
        # Currently paused (0%): apply energy-coupling lookahead and symmetric deadband before resuming 100%
        if metal_full_with_pig_space or metal_cap_pct >= warn_ratio * 0.85:
            return None
        if any(
            a.priority == "P1"
            and a.missing.pig_iron > 0
            and not (sto_pig > 0 and a.cost.pig_iron > sto_pig)
            for a in p_acts
        ):
            return None

        resume_energy_cost = int(round(b2_full_energy * max(0, 100 - cur_b2_pct) / 100.0))
        net_if_resumed = e_net - resume_energy_cost
        if net_if_resumed < 0:
            return None

        active = state.active_planet()
        is_active = active is not None and planet.planet_id == active.planet_id
        eff_buildings, projected_net = _effective_building_levels_and_energy(
            state,
            planet,
            is_active_planet=is_active,
        )
        projected_net_if_resumed = projected_net - resume_energy_cost
        queued_counts = _queued_building_counts(state) if is_active else {}
        queue_full = (
            sum(queued_counts.values()) >= _max_build_slots(planet, state)
            if is_active
            else False
        )
        allow_remote = cfg.allow_multi_planet and not is_active

        has_p1_power = any(
            a.priority == "P1"
            and a.kind in ("BUILD", "WAIT_RESOURCES", "BUILD_SHIP")
            and a.target_id in (FUSION_PLANT, THERMOSOLAR_PLANT, SOLAR_CELLS_ID)
            for a in p_acts
        )
        would_trigger_p1_power = has_p1_power
        if not would_trigger_p1_power:
            for a in p_acts:
                tid = game_data.safe_optional_int(a.target_id, None)
                if tid is None or tid in (FUSION_PLANT, THERMOSOLAR_PLANT, SOLAR_CELLS_ID):
                    continue
                if a.kind not in ("BUILD", "WAIT_RESOURCES"):
                    continue
                nxt_l = _safe_strategy_int(a.target_level, 0) or (_eff_level(planet, eff_buildings, tid) + 1)
                if _energy_starvation_delta(tid, nxt_l, projected_net_if_resumed) < 0:
                    would_trigger_p1_power = True
                    break
        if would_trigger_p1_power:
            forbid_solar_bf = bool(getattr(cfg, "forbid_planetary_solar", False))
            solar_offer = _ship_offer_map(planet).get(SOLAR_CELLS_ID)
            f_rem_bf = _safe_strategy_int(planet.fields_remaining, 0)
            hot_tight_bf = (
                not forbid_solar_bf
                and _norm_planet_type(planet) == "normal"
                and _safe_strategy_int(planet.fields_max, 0) > 0
                and 0 < f_rem_bf <= _safe_strategy_int(cfg.min_free_fields_warning, 20)
                and _safe_strategy_int(planet.temperature, 0) >= _safe_strategy_int(cfg.hot_planet_solar_temp_threshold, 40)
            )
            use_solar_cells = (
                (
                    _norm_planet_type(planet) == "asteroid"
                    or (not forbid_solar_bf and (not _planet_has_free_building_fields(planet) or hot_tight_bf))
                )
                and solar_offer is not None
                and solar_offer.can_build
                and cfg.allow_shipyard
            )
            if use_solar_cells:
                assert solar_offer is not None
                miss_pw = planet.resources.missing(solar_offer.cost)
                can_exec_power = planet.resources.can_afford(solar_offer.cost)
            else:
                power_bid, _, _ = _pick_best_power_building(
                    planet,
                    eff_buildings,
                    state.researches,
                    forbid_planetary_solar=forbid_solar_bf,
                )
                power_lvl = _eff_level(planet, eff_buildings, power_bid) + 1
                power_cost = _resources_from_dict(game_data.building_cost(power_bid, power_lvl))
                power_offer = _offer_map(planet).get(power_bid)
                if power_offer is not None:
                    miss_pw = planet.resources.missing(power_offer.cost)
                    can_exec_power = (
                        (not queue_full)
                        and power_offer.can_build
                        and planet.resources.can_afford(power_offer.cost)
                    )
                else:
                    miss_pw = planet.resources.missing(power_cost)
                    prereq_ok = not game_data.missing_prerequisites(
                        "buildings",
                        power_bid,
                        buildings=planet.buildings,
                        researches=state.researches,
                    )
                    can_exec_power = (
                        (not queue_full)
                        and allow_remote
                        and (not power_cost.is_zero())
                        and planet.resources.can_afford(power_cost)
                        and prereq_ok
                    )
            if not metal_starved_with_positive_pig and (
                has_p1_power
                or miss_pw.pig_iron > 0
                or (not miss_pw.is_zero() and miss_pw.metal == 0)
                or (not can_exec_power and miss_pw.metal == 0)
            ):
                return None

        if (
            top_needs_metal
            or metal_starved_with_positive_pig
            or (not top_needs_pig_only and any_needs_metal and net_pig_at_100 >= 0)
        ):
            resume_pig_min_i = max(2000, math.ceil(bf_pig_used_full * 0.6)) if math.isfinite(bf_pig_used_full) else 2000
            if net_pig_at_100 >= 0 or res_pig >= resume_pig_min_i:
                candidate = PlannedAction(
                    priority="P1",
                    kind="THROTTLE_BUILDING",
                    planet_id=planet.planet_id,
                    planet_coords=_safe_strategy_str(planet.coords),
                    target_id=BLAST_FURNACE,
                    target_name="Blast furnace 100%",
                    target_level=100,
                    reason="Metal needed for next priority build/research: resume Blast furnace to 100%",
                    executable_now=True,
                )

        if candidate is None:
            min_pig_for_bf = max(5000, math.ceil(bf_pig_used_full * 1.5)) if math.isfinite(bf_pig_used_full) else 5000
            if (
                not top_needs_pig_only
                and net_pig_at_100 >= 0
                and res_pig >= min_pig_for_bf
                and res_pig >= (2 * res_met)
            ):
                candidate = PlannedAction(
                    priority="P2",
                    kind="THROTTLE_BUILDING",
                    planet_id=planet.planet_id,
                    planet_coords=_safe_strategy_str(planet.coords),
                    target_id=BLAST_FURNACE,
                    target_name="Blast furnace 100%",
                    target_level=100,
                    reason="Pig iron reserves healthy: resume Blast furnace smelting to 100%",
                    executable_now=True,
                )

    if candidate is not None and _check_bounce:
        new_pct = _safe_strategy_int(candidate.target_level, 0)
        delta_e = int(round(b2_full_energy * (new_pct - cur_b2_pct) / 100.0))
        new_rates = dict(rates)
        new_rates["resource2"] = new_pct
        post_planet = replace(
            planet,
            economy_rates=new_rates,
            energy=EnergyBalance(e_prod, max(0, e_used + delta_e)),
        )
        post_state = replace(
            state,
            planets=tuple(
                post_planet if p.planet_id == planet.planet_id else p for p in state.planets
            ),
        )
        post_plan, _ = _build_pre_throttle_plan(
            post_state,
            warn_ratio=warn_ratio,
            cfg=cfg,
            include_combat=False,
        )
        bounce = _plan_blast_furnace_throttle(
            post_state,
            post_planet,
            post_plan,
            cfg,
            last_throttle_ts=None,
            now_ts=now_ts,
            storage_warn_ratio=warn_ratio,
            _check_bounce=False,
        )
        if bounce is not None:
            return None

    return candidate


def _build_pre_throttle_plan(
    state: GameState,
    *,
    warn_ratio: float,
    cfg: StrategyConfig,
    scanned_systems: Optional[Mapping[str, float]] = None,
    now_ts: Optional[float] = None,
    include_combat: bool = True,
) -> tuple[list[PlannedAction], list[PlanetState]]:
    plan: list[PlannedAction] = []
    active = state.active_planet()
    if active is None:
        return plan, []

    # P0: Survival / incoming hostile attacks + optimal Escape Flight packing
    active_coords_s = _safe_strategy_str(active.coords)
    for alert in state.fleet_alerts:
        if alert.hostile:
            plan.append(
                PlannedAction(
                    priority="P0",
                    kind="ALERT_HOSTILE",
                    planet_id=active.planet_id,
                    planet_coords=active_coords_s,
                    target_name="Escape flight / Fleet Save",
                    reason=f"Hostile fleet detected: {_safe_strategy_str(alert.summary)}",
                    executable_now=False,
                )
            )
            other_coords = next(
                (p.coords for p in state.planets if p.planet_id != active.planet_id),
                active.coords,
            )
            escape = _get_fleet().plan_escape_flight(
                active,
                target_coords=other_coords,
                fuel_reserve_spice=cfg.fleet_save_fuel_reserve_spice,
            )
            if escape.feasible:
                plan.append(
                    PlannedAction(
                        priority="P0",
                        kind="ESCAPE_FLIGHT",
                        planet_id=active.planet_id,
                        planet_coords=active_coords_s,
                        target_name=f"Save -> {_safe_strategy_str(escape.target_coords)}",
                        cost=escape.cargo,
                        reason=_safe_strategy_str(escape.reason),
                        executable_now=cfg.allow_fleetsave,
                    )
                )

    queued_counts = _queued_building_counts(state)
    total_b_queued = sum(queued_counts.values())
    active_queue_full = total_b_queued >= _max_build_slots(active, state)
    research_offers = _research_offer_map(state)
    research_queue_busy = (
        any(q.category == "research" for q in state.active_queues)
        or any(o.active for o in state.research_offers)
    )

    planets_to_check = list(state.planets) if cfg.allow_multi_planet else [active]
    # Keep active planet first for tie-breaks
    planets_to_check.sort(key=lambda p: 0 if p.planet_id == active.planet_id else 1)

    for planet in planets_to_check:
        is_active = planet.planet_id == active.planet_id
        offers = _offer_map(planet)
        eff_buildings, projected_net = _effective_building_levels_and_energy(
            state,
            planet,
            is_active_planet=is_active,
        )
        queue_full = active_queue_full if is_active else False
        allow_remote = cfg.allow_multi_planet and not is_active

        # P1: Negative energy guard per planet (Solar cells on Asteroids ONLY; Fusion plant on normal planets)
        ptype_now = _norm_planet_type(planet)
        if ptype_now == "asteroid" and _safe_strategy_int(planet.energy.net, 0) < 0:
            solar_offer = _ship_offer_map(planet).get(SOLAR_CELLS_ID)
            if planet.building_level(WEAPONS_FACTORY) >= 1:
                solar_cost = (
                    solar_offer.cost
                    if (solar_offer is not None and not solar_offer.cost.is_zero())
                    else _resources_from_dict((game_data.SHIPS.get("solar-cells") or {}).get("base_cost", {}))
                )
                can_exec_s9 = bool(
                    cfg.allow_shipyard
                    and solar_offer is not None
                    and solar_offer.can_build
                    and planet.resources.can_afford(solar_cost)
                )
                plan.append(
                    PlannedAction(
                        priority="P1",
                        kind="BUILD_SHIP",
                        planet_id=planet.planet_id,
                        planet_coords=_safe_strategy_str(planet.coords),
                        target_id=SOLAR_CELLS_ID,
                        target_name=(_safe_strategy_str(solar_offer.name) if solar_offer else "") or "Solar cells",
                        target_level=1,
                        cost=solar_cost,
                        missing=planet.resources.missing(solar_cost),
                        reason=(
                            f"Negative energy on asteroid ({game_data.fmt_compact_int(planet.energy.produced)}/"
                            f"{game_data.fmt_compact_int(planet.energy.used)}); build Solar cells (0 fields)"
                        ),
                        executable_now=can_exec_s9,
                    )
                )
        elif ptype_now not in ("asteroid", "raid") and _safe_strategy_int(planet.energy.net, 0) < 0:
            forbid_solar_p1 = bool(getattr(cfg, "forbid_planetary_solar", False))
            solar_offer = _ship_offer_map(planet).get(SOLAR_CELLS_ID)
            f_rem_p1 = _safe_strategy_int(planet.fields_remaining, 0)
            hot_tight_p1 = (
                not forbid_solar_p1
                and _safe_strategy_int(planet.fields_max, 0) > 0
                and 0 < f_rem_p1 <= _safe_strategy_int(cfg.min_free_fields_warning, 20)
                and _safe_strategy_int(planet.temperature, 0) >= _safe_strategy_int(cfg.hot_planet_solar_temp_threshold, 40)
            )
            if (
                not forbid_solar_p1
                and (not _planet_has_free_building_fields(planet) or hot_tight_p1)
                and planet.building_level(WEAPONS_FACTORY) >= 1
                and solar_offer is not None
                and solar_offer.can_build
                and cfg.allow_shipyard
            ):
                solar_cost = (
                    solar_offer.cost
                    if not solar_offer.cost.is_zero()
                    else _resources_from_dict((game_data.SHIPS.get("solar-cells") or {}).get("base_cost", {}))
                )
                plan.append(
                    PlannedAction(
                        priority="P1",
                        kind="BUILD_SHIP",
                        planet_id=planet.planet_id,
                        planet_coords=_safe_strategy_str(planet.coords),
                        target_id=SOLAR_CELLS_ID,
                        target_name=_safe_strategy_str(solar_offer.name) or "Solar cells",
                        target_level=1,
                        cost=solar_cost,
                        missing=planet.resources.missing(solar_cost),
                        reason=(
                            f"Negative energy ({game_data.fmt_compact_int(planet.energy.produced)}/"
                            f"{game_data.fmt_compact_int(planet.energy.used)}) with 0 free building fields; build Solar cells (0 fields)"
                        ),
                        executable_now=planet.resources.can_afford(solar_cost),
                    )
                )
            else:
                power_bid, power_name, _ = _pick_best_power_building(
                    planet,
                    eff_buildings,
                    state.researches,
                    forbid_planetary_solar=forbid_solar_p1,
                )
                if _is_building_allowed_on_planet(planet, power_bid, offers):
                    plan.append(
                        _apply_queue_cap_if_full(
                            _make_build_action(
                                "P1",
                                planet,
                                power_bid,
                                power_name,
                                f"Negative energy ({game_data.fmt_compact_int(planet.energy.produced)}/{game_data.fmt_compact_int(planet.energy.used)}); mines throttled",
                                offers,
                                effective_level=_eff_level(planet, eff_buildings, power_bid),
                                researches=state.researches,
                                allow_remote_colony_build=allow_remote,
                            ),
                            queue_full,
                        )
                    )

        # P1: Demolish already-built Thermosolar plant (`b6`) on normal planets to reclaim building fields
        cold_spice_keep_b6 = (
            not bool(getattr(cfg, "forbid_planetary_solar", False))
            and _safe_strategy_int(planet.temperature, 0) <= -100
            and _eff_level(planet, eff_buildings, SPICE_MINE) >= 12
            and (_safe_strategy_int(planet.fields_max, 0) <= 0 or _safe_strategy_int(planet.fields_remaining, 0) > 40)
            and planet.building_level(THERMOSOLAR_PLANT) <= 6
        )
        if (
            ptype_now not in ("asteroid", "raid")
            and not (state.tutorial and state.tutorial.can_complete)
            and not cold_spice_keep_b6
        ):
            cur_b6 = planet.building_level(THERMOSOLAR_PLANT)
            tut_needs_b6 = bool(
                state.tutorial
                and not state.tutorial.can_complete
                and any(
                    not t.completed
                    and t.category == "building"
                    and game_data.safe_optional_int(t.building_id, None) == THERMOSOLAR_PLANT
                    for t in state.tutorial.tasks
                )
            )
            b6_in_queue = bool(
                is_active
                and any(
                    q.category == "building"
                    and (
                        game_data.safe_optional_int(q.building_id, None) == THERMOSOLAR_PLANT
                        or "thermosolar" in _safe_strategy_str(q.name).lower()
                        or "thermal" in _safe_strategy_str(q.name).lower()
                    )
                    for q in state.active_queues
                )
            )
            if cur_b6 > 0 and not tut_needs_b6 and not b6_in_queue:
                r2_lvl = max(0, _safe_strategy_int((state.researches or {}).get("r2", 0), 0))
                raw_b6_step = max(0, game_data.building_energy_delta(THERMOSOLAR_PLANT, cur_b6))
                safe_b6_loss = int(math.ceil(raw_b6_step * (1.0 + 0.01 * min(r2_lvl, 200))))
                throttled_reserve = 0
                e_rates = planet.economy_rates if isinstance(planet.economy_rates, Mapping) else {}
                for bid_m, rkey_m in (
                    (PIG_IRON_MINE, "resource1"),
                    (BLAST_FURNACE, "resource2"),
                    (KRYPTONITE_MINE, "resource3"),
                    (SPICE_MINE, "resource4"),
                ):
                    pct_m = max(0, min(100, _safe_strategy_int(e_rates.get(rkey_m, 100), 100)))
                    lvl_m = planet.building_level(bid_m)
                    if pct_m < 100 and lvl_m > 0:
                        full_m = abs(sum(game_data.building_energy_delta(bid_m, l) for l in range(1, min(lvl_m, 101) + 1)))
                        throttled_reserve += int(math.ceil(full_m * (100 - pct_m) / 100.0))
                avail_e_buffer = min(_safe_strategy_int(planet.energy.net, 0), projected_net) - throttled_reserve
                if avail_e_buffer - safe_b6_loss >= 0:
                    allow_bld_cfg = bool(getattr(cfg, "allow_build", True))
                    can_dem_now = bool(allow_bld_cfg and not queue_full and (is_active or allow_remote))
                    dem_reason = f"Tear down Thermosolar plant (L{cur_b6} -> L{cur_b6 - 1}) to reclaim planetary building fields"
                    if queue_full:
                        dem_reason += " (build queue full)"
                    elif not allow_bld_cfg:
                        dem_reason += " (allow_build=false in Config)"
                    elif not (is_active or allow_remote):
                        dem_reason += " (remote colony build disabled)"
                    plan.append(
                        PlannedAction(
                            priority="P1",
                            kind="DEMOLISH_BUILDING",
                            planet_id=planet.planet_id,
                            planet_coords=_safe_strategy_str(planet.coords),
                            target_id=THERMOSOLAR_PLANT,
                            target_name="Thermosolar plant",
                            target_level=cur_b6 - 1,
                            reason=dem_reason,
                            executable_now=can_dem_now,
                        )
                    )
                elif _is_building_allowed_on_planet(planet, FUSION_PLANT, offers) and not any(
                    a.planet_id == planet.planet_id
                    and a.target_id == FUSION_PLANT
                    and a.kind in ("BUILD", "WAIT_RESOURCES")
                    for a in plan
                ):
                    plan.append(
                        _apply_queue_cap_if_full(
                            _make_build_action(
                                "P1",
                                planet,
                                FUSION_PLANT,
                                "Fusion plant",
                                f"Upgrade Fusion plant before tearing down Thermosolar plant L{cur_b6} (needs +{safe_b6_loss} energy buffer)",
                                offers,
                                effective_level=_eff_level(planet, eff_buildings, FUSION_PLANT),
                                researches=state.researches,
                                allow_remote_colony_build=allow_remote,
                            ),
                            queue_full,
                        )
                    )

        # P1: Warehouse overflow guard per planet (asteroids & raid colonies forbid warehouses)
        if ptype_now not in ("asteroid", "raid"):
            for res_key, raw_ratio in planet.storage_fill_ratios().items():
                ratio = _safe_strategy_float(raw_ratio, 0.0)
                if ratio >= warn_ratio:
                    wh_id, wh_name = _WAREHOUSE_BY_RESOURCE[res_key]
                    if not _is_building_allowed_on_planet(planet, wh_id, offers):
                        continue
                    plan.append(
                        _apply_queue_cap_if_full(
                            _make_build_action(
                                "P1",
                                planet,
                                wh_id,
                                wh_name,
                                f"{res_key.replace('_', ' ').title()} storage at {ratio * 100:.0f}% (5% cap risk)",
                                offers,
                                effective_level=_eff_level(planet, eff_buildings, wh_id),
                                researches=state.researches,
                                allow_remote_colony_build=allow_remote,
                            ),
                            queue_full,
                        )
                    )

    # P2: Active planet Tutorial Claim + Tutorial Tasks
    active_offers = _offer_map(active)
    active_eff, active_proj_net = _effective_building_levels_and_energy(
        state,
        active,
        is_active_planet=True,
    )
    if state.tutorial and state.tutorial.can_complete:
        plan.append(
            PlannedAction(
                priority="P2",
                kind="CLAIM_TUTORIAL",
                planet_id=active.planet_id,
                planet_coords=active_coords_s,
                target_name=f"Tutorial {game_data.fmt_compact_int(state.tutorial.step)}/{game_data.fmt_compact_int(state.tutorial.total_steps)} Reward",
                reason=f"All tasks complete; claim {_safe_strategy_str(state.tutorial.reward_text) or 'tutorial reward'}",
                executable_now=True,
            )
        )

    tutorial_actions: list[PlannedAction] = []
    if state.tutorial and not state.tutorial.can_complete:
        for tut_planet in planets_to_check:
            is_tut_active = tut_planet.planet_id == active.planet_id
            if is_tut_active:
                t_offers, t_eff, t_proj_net = active_offers, active_eff, active_proj_net
                t_q_counts = queued_counts
                t_q_full = active_queue_full
                t_r_offers = research_offers
            else:
                if _norm_planet_type(tut_planet) == "raid":
                    continue
                t_offers = _offer_map(tut_planet)
                t_eff, t_proj_net = _effective_building_levels_and_energy(
                    state,
                    tut_planet,
                    is_active_planet=False,
                )
                t_q_counts = {}
                t_q_full = False
                t_r_offers = {}
            tutorial_actions = _plan_tutorial_actions(
                state,
                tut_planet,
                t_offers,
                t_r_offers,
                t_q_counts,
                t_eff,
                t_proj_net,
                t_q_full,
                research_queue_busy,
                cfg,
                allow_remote_colony_build=(cfg.allow_multi_planet and not is_tut_active),
                allow_live_research=is_tut_active,
            )
            if tutorial_actions:
                break
    if tutorial_actions:
        plan.extend(tutorial_actions)
    elif not (state.tutorial and state.tutorial.can_complete):
        all_mines: list[tuple[float, PlannedAction]] = []
        all_advisories: list[PlannedAction] = []
        for planet in planets_to_check:
            if _norm_planet_type(planet) == "raid":
                continue
            is_active = planet.planet_id == active.planet_id
            eff_b, proj_net = _effective_building_levels_and_energy(
                state,
                planet,
                is_active_planet=is_active,
            )
            mines, extra_builds, adv = _plan_economy_for_planet(
                state,
                planet,
                _offer_map(planet),
                research_offers if is_active else {},
                _ship_offer_map(planet),
                eff_b,
                proj_net,
                active_queue_full if is_active else False,
                research_queue_busy,
                cfg.allow_multi_planet and not is_active,
                cfg,
            )
            all_mines.extend(mines)
            plan.extend(extra_builds)
            all_advisories.extend(adv)

        # Sort cross-planet mines by global best payback hours!
        all_mines.sort(key=lambda pair: pair[0])
        plan.extend(act for _, act in all_mines)
        plan.extend(all_advisories)

    if not (state.tutorial and state.tutorial.can_complete):
        if state.tutorial is not None:
            tut_emitted_b = {
                (a.planet_id, int(a.target_id))
                for a in tutorial_actions
                if a.kind in ("BUILD", "WAIT_RESOURCES") and a.target_id is not None
            }
            tut_emitted_r = {
                f"r{int(a.target_id)}"
                for a in tutorial_actions
                if a.kind == "RESEARCH" and a.target_id is not None
            }
            plan.extend(
                _plan_tutorial_fleet_milestones(
                    state,
                    active,
                    cfg,
                    planets_to_check=planets_to_check,
                    emitted_build_ids=tut_emitted_b,
                    emitted_res_keys=tut_emitted_r,
                )
            )

        # Expand warehouse when a P1/P2 planned upgrade exceeds current warehouse capacity
        for planet in planets_to_check:
            if _norm_planet_type(planet) in ("asteroid", "raid") or not _planet_has_free_building_fields(planet):
                continue
            is_p_active = planet.planet_id == active.planet_id
            p_offers = _offer_map(planet)
            if not p_offers:
                continue
            eff_b_wh, _ = _effective_building_levels_and_energy(state, planet, is_active_planet=is_p_active)
            p_queue_full = active_queue_full if is_p_active else False
            p_allow_remote = cfg.allow_multi_planet and not is_p_active
            p_wait_acts = [
                a for a in plan
                if a.planet_id == planet.planet_id and a.priority in ("P1", "P2") and a.kind == "WAIT_RESOURCES"
            ]
            for w_act in p_wait_acts:
                for rk in ("pig_iron", "metal", "kryptonite", "spice"):
                    cost_v = _safe_strategy_int(getattr(w_act.cost, rk, 0), 0)
                    sto_v = _safe_strategy_int(getattr(planet.storage, rk, 0), 0)
                    if sto_v <= 0 or cost_v <= sto_v:
                        continue
                    wh_id, wh_name = _WAREHOUSE_BY_RESOURCE[rk]
                    if wh_id not in p_offers or not _is_building_allowed_on_planet(planet, wh_id, p_offers):
                        continue
                    if is_p_active and queued_counts.get(wh_id, 0) > 0:
                        continue
                    if any(
                        a.planet_id == planet.planet_id and a.target_id == wh_id and a.kind in ("BUILD", "WAIT_RESOURCES")
                        for a in plan
                    ):
                        continue
                    plan.append(
                        _apply_queue_cap_if_full(
                            _make_build_action(
                                w_act.priority,
                                planet,
                                wh_id,
                                wh_name,
                                (
                                    f"Expand {rk.replace('_', ' ')} cap ({game_data.fmt_compact_int(sto_v)}) "
                                    f"for {w_act.target_name} ({game_data.fmt_compact_int(cost_v)} needed)"
                                ),
                                p_offers,
                                effective_level=_eff_level(planet, eff_b_wh, wh_id),
                                researches=state.researches,
                                allow_remote_colony_build=p_allow_remote,
                            ),
                            p_queue_full,
                        )
                    )

        if include_combat and (
            not tutorial_actions
            or _tutorial_fight_task(state) is not None
            or _tutorial_spy_task(state) is not None
            or _tutorial_recycle_task(state) is not None
            or _tutorial_colonize_task(state) is not None
            or cfg.allow_colonize
            or cfg.allow_galaxy_scan
        ):
            plan.extend(
                _plan_combat_and_raid_actions(
                    state,
                    active,
                    cfg,
                    scanned_systems=scanned_systems,
                    now_ts=now_ts,
                )
            )

    return plan, planets_to_check


def _plan_transmitter_action(
    state: GameState,
    cfg: StrategyConfig,
    transmitter_status: Optional[Mapping[str, Any]] = None,
) -> Optional[PlannedAction]:
    """Emit once-a-day free Interdimensional Transmitter (`ACTIVATE_TRANSMITTER`) action when due."""
    if state.tutorial is not None and state.tutorial.can_complete:
        return None
    eff_tm: Optional[Mapping[str, Any]] = (
        transmitter_status if isinstance(transmitter_status, Mapping) else None
    )
    if eff_tm is None:
        state_rs = getattr(state, "raid_stats", None)
        if isinstance(state_rs, Mapping):
            cand_tm = state_rs.get("transmitter_status")
            if isinstance(cand_tm, Mapping):
                eff_tm = cand_tm
    if eff_tm is None or not bool(eff_tm.get("due", False)):
        return None
    active = state.active_planet() or (state.planets[0] if state.planets else None)
    if active is None:
        return None
    can_exec = bool(getattr(cfg, "allow_transmitter", False))
    if not can_exec:
        return None
    free_rem = eff_tm.get("free_spins_remaining")
    charges_hint = (
        f" ({_safe_strategy_int(free_rem, 1)} free charge(s) ready)"
        if isinstance(free_rem, int) and not isinstance(free_rem, bool) and free_rem > 0
        else ""
    )
    hold_note = "" if can_exec else " [Hold: allow_transmitter=false in Config]"
    return PlannedAction(
        priority="P2",
        kind="ACTIVATE_TRANSMITTER",
        planet_id=active.planet_id,
        planet_coords=_safe_strategy_str(active.coords),
        target_name="Interdimensional Transmitter",
        reason=f"Activate daily free Interdimensional Transmitter spin{charges_hint} (0 Urplasma){hold_note}",
        executable_now=can_exec,
    )


def plan_actions(
    state: GameState,
    *,
    storage_warn_ratio: Optional[float] = None,
    config: Optional[StrategyConfig] = None,
    scanned_systems: Optional[Mapping[str, float]] = None,
    throttle_cooldowns: Optional[Mapping[int, float]] = None,
    now_ts: Optional[float] = None,
    raid_stats: Optional[Mapping[str, Any]] = None,
    transmitter_status: Optional[Mapping[str, Any]] = None,
) -> list[PlannedAction]:
    """Generate an ETA-annotated action plan via the configured planner (`priority` or `ga_nn`)."""
    cfg = config or StrategyConfig()
    warn_ratio = _resolve_storage_warn_ratio(cfg, storage_warn_ratio)
    state_for_plan = (
        replace(state, raid_stats=dict(raid_stats))
        if isinstance(raid_stats, Mapping)
        else state
    )
    tm_act = _plan_transmitter_action(state_for_plan, cfg, transmitter_status=transmitter_status)

    if normalize_planner_mode(getattr(cfg, "planner_mode", "priority")) == "ga_nn":
        from spaceinvasion.planner.optimizer import optimize_plan_actions

        opt_kwargs: dict[str, Any] = {
            "storage_warn_ratio": warn_ratio,
            "config": cfg,
        }
        if raid_stats is not None:
            opt_kwargs["raid_stats"] = raid_stats
        else:
            state_raid_stats = getattr(state, "raid_stats", None)
            if isinstance(state_raid_stats, Mapping) and state_raid_stats:
                opt_kwargs["raid_stats"] = state_raid_stats
        if now_ts is not None:
            opt_kwargs["now_ts"] = now_ts
        if scanned_systems is not None:
            opt_kwargs["scanned_systems"] = scanned_systems
        if throttle_cooldowns is not None:
            opt_kwargs["throttle_cooldowns"] = throttle_cooldowns
        ga_actions = list(optimize_plan_actions(state, **opt_kwargs))
        if tm_act is not None and not any(a.kind == "ACTIVATE_TRANSMITTER" for a in ga_actions):
            insert_idx = 0
            while insert_idx < len(ga_actions) and ga_actions[insert_idx].priority in ("P0", "P1"):
                insert_idx += 1
            ga_actions.insert(insert_idx, tm_act)
        return attach_action_etas(state, ga_actions)

    plan, planets_to_check = _build_pre_throttle_plan(
        state_for_plan,
        warn_ratio=warn_ratio,
        cfg=cfg,
        scanned_systems=scanned_systems,
        now_ts=now_ts,
        include_combat=True,
    )
    if tm_act is not None:
        plan.append(tm_act)
    if not planets_to_check:
        return attach_action_etas(state, plan)

    # Bucket: Blast furnace (b2) 0% / 100% auto-throttling to protect Pig iron flow
    if cfg.auto_throttle_blast_furnace and not cfg.merchant_mode:
        cooldown_map = throttle_cooldowns or {}
        for planet in planets_to_check:
            throttle_act = _plan_blast_furnace_throttle(
                state,
                planet,
                plan,
                cfg,
                last_throttle_ts=cooldown_map.get(planet.planet_id),
                now_ts=now_ts,
                storage_warn_ratio=warn_ratio,
            )
            if throttle_act is not None:
                plan.append(throttle_act)

    # Sort strictly by priority band, executable actions first within each band, colony SPY -> raid SPY ahead of other P2 work
    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

    def _p2_subrank(act: PlannedAction) -> int:
        if act.priority != "P2":
            return 0
        if act.kind == "CLAIM_TUTORIAL":
            return 0
        if act.kind == "SPY":
            return 1 if "(free slot)" in _safe_strategy_str(act.target_name).lower() else 2
        return 3

    plan.sort(
        key=lambda a: (
            priority_rank.get(a.priority, 9),
            0 if a.executable_now else 1,
            _p2_subrank(a),
        )
    )
    deduped: list[PlannedAction] = []
    seen_keys: set[tuple[str, int, Optional[int], str]] = set()
    for act in plan:
        key = (act.kind, act.planet_id, act.target_id, act.target_name)
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(act)
    return attach_action_etas(state, deduped)


def _normalize_manual_coords(raw: Any) -> str:
    txt = _safe_strategy_str(raw).strip()
    if not txt:
        return ""
    m3 = re.search(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", txt, re.ASCII)
    if m3:
        g, s, p = int(m3.group(1)), int(m3.group(2)), int(m3.group(3))
        if 1 <= g <= 14 and 1 <= s <= 400 and 1 <= p <= 16:
            return f"{g:02d}:{s:03d}:{p:02d}"
    m2 = re.search(r"(\d{1,2}):(\d{1,3})", txt, re.ASCII)
    if m2:
        g, s = int(m2.group(1)), int(m2.group(2))
        if 1 <= g <= 14 and 1 <= s <= 400:
            return f"{g:02d}:{s:03d}"
    return txt[:32]


def merge_manual_queue_actions(
    state: GameState,
    planned_actions: Sequence[PlannedAction],
    manual_queue: Optional[Sequence[Mapping[str, Any]]],
    *,
    config: Optional[StrategyConfig] = None,
) -> tuple[list[PlannedAction], list[int]]:
    """Merge operator-enqueued manual commands into `planned_actions` with priority & resource reservation.

    Returns `(merged_actions, auto_satisfied_queue_ids)`.
    """
    if not manual_queue or not state.planets:
        return list(planned_actions), []

    from spaceinvasion.combat.ranking import attach_action_etas

    cfg = config or StrategyConfig()
    active_p = state.active_planet() or state.planets[0]
    planets_by_id = {p.planet_id: p for p in state.planets}
    planets_by_coords = {_normalize_manual_coords(p.coords): p for p in state.planets if p.coords}

    b_queued_active = sum(
        1 for q in (getattr(state, "active_queues", ()) or ()) if getattr(q, "category", "") == "building"
    )
    r_queued_total = sum(
        1 for q in (getattr(state, "active_queues", ()) or ()) if getattr(q, "category", "") == "research"
    )
    has_cmd = game_data.safe_int((getattr(state, "specs", None) or {}).get("construction_commander", 0), 0) > 0

    manual_actions: list[PlannedAction] = []
    auto_satisfied_ids: list[int] = []
    reserved_by_pid: dict[int, tuple[int, Resources]] = {}
    prio_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

    sorted_items = sorted(
        (m for m in manual_queue if isinstance(m, Mapping) and str(m.get("status", "pending")).lower() == "pending"),
        key=lambda m: (
            prio_rank.get((_safe_strategy_str(m.get("priority")) or "P1").strip().upper(), 1),
            _safe_strategy_int(m.get("sort_rank"), 1000),
            _safe_strategy_int(m.get("id"), 0),
        ),
    )

    for idx_m, item in enumerate(sorted_items):
        qid = _safe_strategy_int(item.get("id"), 0)
        if qid <= 0:
            continue
        raw_prio = (_safe_strategy_str(item.get("priority")) or "P1").strip().upper()
        prio = raw_prio if raw_prio in ("P0", "P1", "P2", "P3") else "P1"
        srank = _safe_strategy_int(item.get("sort_rank"), (idx_m + 1) * 10)
        kind = _safe_strategy_str(item.get("kind")).strip().upper()
        pid_req = _safe_strategy_int(item.get("planet_id"), 0)
        pcoords_req = _normalize_manual_coords(item.get("planet_coords"))
        planet = (
            planets_by_id.get(pid_req)
            or (planets_by_coords.get(pcoords_req) if pcoords_req else None)
            or active_p
        )
        is_active_planet = planet.planet_id == active_p.planet_id
        is_astro = _norm_planet_type(planet) == "asteroid"
        b_cap = 1 if is_astro else (6 if has_cmd else 5)
        b_queue_full = is_active_planet and b_queued_active >= b_cap
        r_queue_full = r_queued_total >= 1

        note_s = _safe_strategy_str(item.get("note")).strip()
        note_suffix = f" · {note_s}" if note_s else ""
        base_tag = f"User Queue #{qid}"

        built_act: Optional[PlannedAction] = None
        prereq_met_for_reserve = False
        reserve_bundle = Resources()

        if kind == "BUILD":
            bid = _safe_strategy_int(item.get("target_id"), 0)
            b_info = game_data.resolve_unit("buildings", bid) if bid > 0 else None
            if bid <= 0 or not b_info:
                continue
            b_name = _safe_strategy_str(b_info.get("name") or item.get("target_name") or f"Building b{bid}")
            cur_lvl = planet.building_level(bid)
            goal_lvl = game_data.safe_optional_int(item.get("target_level"), None)
            if goal_lvl is not None and goal_lvl > 0 and cur_lvl >= goal_lvl:
                auto_satisfied_ids.append(qid)
                continue
            b_offers = _offer_map(planet)
            offer = b_offers.get(bid)
            offer_tgt = game_data.safe_optional_int(offer.target_level, None) if offer is not None else None
            if goal_lvl is not None and offer_tgt is not None and offer_tgt > goal_lvl:
                # Target level is already queued inside the in-game construction queue
                built_act = PlannedAction(
                    priority=prio,
                    kind="BUILD",
                    planet_id=planet.planet_id,
                    planet_coords=planet.coords,
                    target_id=bid,
                    target_name=b_name,
                    target_level=goal_lvl,
                    cost=Resources(),
                    missing=Resources(),
                    reason=f"{base_tag}: In-game build queue already covers L{goal_lvl}{note_suffix}",
                    executable_now=False,
                    manual_queue_id=qid,
                    manual_sort_rank=srank,
                )
            else:
                eff_cur = max(cur_lvl, (offer_tgt - 1) if (offer_tgt is not None and offer_tgt > cur_lvl) else cur_lvl)
                goal_hint = f" (goal L{goal_lvl})" if (goal_lvl is not None and goal_lvl > eff_cur + 1) else ""
                raw_b = _make_build_action(
                    prio,
                    planet,
                    bid,
                    b_name,
                    f"{base_tag}: Queued upgrade{goal_hint}{note_suffix}",
                    b_offers,
                    effective_level=eff_cur,
                    researches=state.researches,
                    allow_remote_colony_build=bool(cfg.allow_multi_planet),
                )
                exec_now = raw_b.executable_now and not b_queue_full
                rsn = f"{raw_b.reason} (build queue full)" if (raw_b.executable_now and b_queue_full) else raw_b.reason
                built_act = replace(
                    raw_b,
                    priority=prio,
                    kind="BUILD",
                    executable_now=exec_now,
                    reason=rsn,
                    manual_queue_id=qid,
                    manual_sort_rank=srank,
                )
                unmet = game_data.missing_prerequisites(
                    "buildings",
                    bid,
                    buildings=planet.buildings,
                    researches=state.researches or {},
                )
                prereq_met_for_reserve = not unmet and _is_building_allowed_on_planet(planet, bid, b_offers)
                reserve_bundle = built_act.cost

        elif kind == "DEMOLISH_BUILDING":
            bid = _safe_strategy_int(item.get("target_id"), 6) or 6
            b_info = game_data.resolve_unit("buildings", bid)
            b_name = _safe_strategy_str((b_info or {}).get("name") or item.get("target_name") or f"Building b{bid}")
            cur_lvl = planet.building_level(bid)
            raw_goal = game_data.safe_optional_int(item.get("target_level"), None)
            if cur_lvl <= 0 or (raw_goal is not None and cur_lvl <= max(0, raw_goal)):
                auto_satisfied_ids.append(qid)
                continue
            step_lvl = max(0, cur_lvl - 1)
            energy_loss = max(0, game_data.building_energy_delta(bid, cur_lvl))
            energy_ok = True
            if energy_loss > 0:
                spare_e = getattr(planet.energy, "net", 0)
                if spare_e - energy_loss < 0:
                    energy_ok = False
            exec_now = (not b_queue_full) and energy_ok
            goal_hint = f" (goal L{max(0, raw_goal)})" if (raw_goal is not None and raw_goal < step_lvl) else ""
            rsn = f"{base_tag}: Demolish L{cur_lvl} -> L{step_lvl}{goal_hint}{note_suffix}"
            if b_queue_full:
                rsn += " (build queue full)"
            elif not energy_ok:
                rsn += " (insufficient spare energy — upgrade Fusion plant first)"
            built_act = PlannedAction(
                priority=prio,
                kind="DEMOLISH_BUILDING",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=bid,
                target_name=b_name,
                target_level=step_lvl,
                cost=Resources(),
                missing=Resources(),
                reason=rsn,
                executable_now=exec_now,
                manual_queue_id=qid,
                manual_sort_rank=srank,
            )

        elif kind == "RESEARCH":
            rid = _safe_strategy_int(item.get("target_id"), 0)
            rkey = f"r{rid}"
            r_info = game_data.resolve_unit("research", rkey) if rid > 0 else None
            if rid <= 0 or not r_info:
                continue
            r_name = _safe_strategy_str(r_info.get("name") or item.get("target_name") or rkey)
            cur_lvl = max(0, _safe_strategy_int((state.researches or {}).get(rkey, 0), 0))
            goal_lvl = game_data.safe_optional_int(item.get("target_level"), None)
            if goal_lvl is not None and goal_lvl > 0 and cur_lvl >= goal_lvl:
                auto_satisfied_ids.append(qid)
                continue
            nxt_lvl = cur_lvl + 1
            r_offers = _research_offer_map(state)
            r_off = r_offers.get(rkey)
            if r_off is not None and game_data.safe_optional_int(r_off.target_level, None) == nxt_lvl:
                cost = r_off.cost
            else:
                lvl_row = (r_info.get("levels") or {}).get(nxt_lvl) or {}
                cost = _resources_from_dict(lvl_row.get("cost"))
            missing = planet.resources.missing(cost) if not cost.is_zero() else Resources()
            unmet = game_data.missing_prerequisites(
                "research",
                rkey,
                buildings=planet.buildings,
                researches=state.researches or {},
            )
            has_lab = planet.building_level(RESEARCH_LAB) >= 1
            can_afford = not cost.is_zero() and planet.resources.can_afford(cost)
            exec_now = (
                has_lab
                and not unmet
                and not r_queue_full
                and (bool(r_off.can_research) if (r_off is not None and is_active_planet) else can_afford)
            )
            goal_hint = f" (goal L{goal_lvl})" if (goal_lvl is not None and goal_lvl > nxt_lvl) else ""
            rsn = f"{base_tag}: Queued research{goal_hint}{note_suffix}"
            if not has_lab:
                rsn += " (requires Research lab)"
            elif unmet:
                rsn += f" (needs {unmet[0]['name']} L{unmet[0]['required']})"
            elif r_queue_full:
                rsn += " (research queue busy)"
            built_act = PlannedAction(
                priority=prio,
                kind="RESEARCH",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=rid,
                target_name=r_name,
                target_level=nxt_lvl,
                cost=cost,
                missing=missing,
                reason=rsn,
                executable_now=exec_now,
                manual_queue_id=qid,
                manual_sort_rank=srank,
            )
            prereq_met_for_reserve = has_lab and not unmet
            reserve_bundle = cost

        elif kind == "BUILD_SHIP":
            sid = _safe_strategy_int(item.get("target_id"), 0)
            s_slug = SHIP_SLUG_BY_KEY.get(f"s{sid}", f"s{sid}")
            s_info = (
                game_data.resolve_unit("ships", s_slug)
                or game_data.resolve_unit("ships", f"s{sid}")
                or game_data.resolve_unit("ships", sid)
            ) if sid > 0 else None
            if sid <= 0 or not s_info:
                continue
            s_name = _safe_strategy_str(s_info.get("name") or item.get("target_name") or f"Ship s{sid}")
            qty = max(1, _safe_strategy_int(item.get("target_level"), 1))
            s_offers = _ship_offer_map(planet)
            s_off = s_offers.get(sid)
            unit_cost = s_off.cost if s_off is not None else _resources_from_dict(s_info.get("base_cost"))
            total_cost = Resources(
                pig_iron=unit_cost.pig_iron * qty,
                metal=unit_cost.metal * qty,
                kryptonite=unit_cost.kryptonite * qty,
                spice=unit_cost.spice * qty,
            )
            missing = planet.resources.missing(total_cost) if not total_cost.is_zero() else Resources()
            unmet = game_data.missing_prerequisites(
                "ships",
                s_info.get("slug", f"s{sid}"),
                buildings=planet.buildings,
                researches=state.researches or {},
            )
            has_wf = planet.building_level(WEAPONS_FACTORY) >= 1
            solar_blocked = (
                sid == SOLAR_CELLS_ID
                and bool(getattr(cfg, "forbid_planetary_solar", False))
                and _norm_planet_type(planet) != "asteroid"
            )
            exec_now = (
                has_wf
                and not unmet
                and not solar_blocked
                and not total_cost.is_zero()
                and planet.resources.can_afford(total_cost)
            )
            rsn = f"{base_tag}: Build {qty}x {s_name}{note_suffix}"
            if solar_blocked:
                rsn += " (blocked on normal planets by forbid_planetary_solar)"
            elif not has_wf:
                rsn += " (requires Weapons factory)"
            elif unmet:
                rsn += f" (needs {unmet[0]['name']} L{unmet[0]['required']})"
            built_act = PlannedAction(
                priority=prio,
                kind="BUILD_SHIP",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=sid,
                target_name=s_name,
                target_level=qty,
                cost=unit_cost,
                missing=missing,
                reason=rsn,
                executable_now=exec_now,
                manual_queue_id=qid,
                manual_sort_rank=srank,
            )
            prereq_met_for_reserve = has_wf and not unmet and not solar_blocked
            reserve_bundle = total_cost

        elif kind == "THROTTLE_BUILDING":
            bid = _safe_strategy_int(item.get("target_id"), 2) or 2
            b_info = game_data.resolve_unit("buildings", bid)
            b_name = _safe_strategy_str((b_info or {}).get("name") or item.get("target_name") or f"Building b{bid}")
            raw_pct = _safe_strategy_int(item.get("target_level"), 100)
            pct = max(0, min(100, (raw_pct // 10) * 10))
            cur_rate = (planet.economy_rates or {}).get(f"resource{bid}")
            if cur_rate is not None and _safe_strategy_int(cur_rate, -1) == pct:
                auto_satisfied_ids.append(qid)
                continue
            has_bld = planet.building_level(bid) > 0
            built_act = PlannedAction(
                priority=prio,
                kind="THROTTLE_BUILDING",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=bid,
                target_name=b_name,
                target_level=pct,
                cost=Resources(),
                missing=Resources(),
                reason=f"{base_tag}: Set {b_name} rate to {pct}%{note_suffix}",
                executable_now=has_bld,
                manual_queue_id=qid,
                manual_sort_rank=srank,
            )

        elif kind in ("SPY", "ATTACK", "RECYCLE", "COLONIZE"):
            tc = _normalize_manual_coords(item.get("target_coords") or item.get("target_name"))
            if not tc:
                continue
            ships_d = planet.ships or {}
            qty_opt = game_data.safe_optional_int(item.get("target_level"), None)
            if kind == "SPY":
                qty = max(1, qty_opt or 1)
                ready = _safe_strategy_int(ships_d.get("s10", 0), 0) >= 1
                t_lbl = f"{tc} (Spy)"
                rsn = f"{base_tag}: Dispatch {qty}x Spy probe to {tc}{note_suffix}" + ("" if ready else " (needs Spy probe s10)")
            elif kind == "COLONIZE":
                qty = 1
                ready = _safe_strategy_int(ships_d.get("s8", 0), 0) >= 1
                t_lbl = f"{tc} (Colonize)"
                rsn = f"{base_tag}: Dispatch Colony ship (s8) to {tc}{note_suffix}" + ("" if ready else " (needs Colony ship s8)")
            elif kind == "RECYCLE":
                owned_rec = _safe_strategy_int(ships_d.get("s11", 0), 0) + _safe_strategy_int(ships_d.get("s111", 0), 0)
                qty = max(1, qty_opt or max(1, owned_rec))
                ready = owned_rec >= 1
                t_lbl = f"{tc} (Debris)"
                rsn = f"{base_tag}: Dispatch {qty}x Recycler to {tc}{note_suffix}" + ("" if ready else " (needs Recycler s11)")
            else:
                qty = None
                combat_ships = sum(
                    max(0, _safe_strategy_int(v, 0))
                    for k, v in ships_d.items()
                    if k not in ("s8", "s9", "s10", "s11", "s111")
                )
                ready = combat_ships >= 1
                t_lbl = _safe_strategy_str(item.get("target_name")).strip() or tc
                if tc not in t_lbl:
                    t_lbl = f"{tc} ({t_lbl})"
                rsn = f"{base_tag}: Raid target {tc}{note_suffix}" + ("" if ready else " (no stationed raid/transport ships)")
            built_act = PlannedAction(
                priority=prio,
                kind=kind,
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=None,
                target_name=t_lbl,
                target_level=qty,
                cost=Resources(),
                missing=Resources(),
                reason=rsn,
                executable_now=ready,
                manual_queue_id=qid,
                manual_sort_rank=srank,
            )

        elif kind == "SCAN_GALAXY":
            sys_c = _normalize_manual_coords(item.get("target_coords") or item.get("target_name") or planet.coords[:6])
            m_gs = re.search(r"(\d{1,2}):(\d{1,3})", sys_c, re.ASCII)
            lbl_gs = f"{int(m_gs.group(1)):02d}:{int(m_gs.group(2)):03d}" if m_gs else (planet.coords[:6] or "01:001")
            built_act = PlannedAction(
                priority=prio,
                kind="SCAN_GALAXY",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=None,
                target_name=f"Galaxy [{lbl_gs}]",
                target_level=None,
                cost=Resources(),
                missing=Resources(),
                reason=f"{base_tag}: Scan system [{lbl_gs}]{note_suffix}",
                executable_now=True,
                manual_queue_id=qid,
                manual_sort_rank=srank,
            )

        elif kind == "CLAIM_TUTORIAL":
            tut_ok = bool(state.tutorial is not None and getattr(state.tutorial, "can_complete", False))
            built_act = PlannedAction(
                priority=prio,
                kind="CLAIM_TUTORIAL",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=None,
                target_name="Claim Tutorial Reward",
                target_level=None,
                cost=Resources(),
                missing=Resources(),
                reason=f"{base_tag}: Claim completed tutorial step{note_suffix}" + ("" if tut_ok else " (tasks incomplete)"),
                executable_now=tut_ok,
                manual_queue_id=qid,
                manual_sort_rank=srank,
            )

        elif kind == "ACTIVATE_TRANSMITTER":
            can_tm = bool(getattr(cfg, "allow_transmitter", True))
            built_act = PlannedAction(
                priority=prio,
                kind="ACTIVATE_TRANSMITTER",
                planet_id=planet.planet_id,
                planet_coords=planet.coords,
                target_id=None,
                target_name="Interdimensional Transmitter",
                target_level=None,
                cost=Resources(),
                missing=Resources(),
                reason=f"{base_tag}: Claim free Interdimensional Transmitter spin{note_suffix}",
                executable_now=can_tm,
                manual_queue_id=qid,
                manual_sort_rank=srank,
            )

        if built_act is not None:
            manual_actions.append(built_act)
            if prio in ("P0", "P1") and prereq_met_for_reserve and not reserve_bundle.is_zero():
                p_rank_val = prio_rank.get(prio, 1)
                prev_entry = reserved_by_pid.get(planet.planet_id)
                if prev_entry is None:
                    reserved_by_pid[planet.planet_id] = (p_rank_val, reserve_bundle)
                else:
                    prev_pr, prev_res = prev_entry
                    reserved_by_pid[planet.planet_id] = (
                        min(prev_pr, p_rank_val),
                        Resources(
                            pig_iron=prev_res.pig_iron + reserve_bundle.pig_iron,
                            metal=prev_res.metal + reserve_bundle.metal,
                            kryptonite=prev_res.kryptonite + reserve_bundle.kryptonite,
                            spice=prev_res.spice + reserve_bundle.spice,
                        ),
                    )

    if not manual_actions:
        return list(planned_actions), auto_satisfied_ids

    # Build lookup of manual targets so auto-planned duplicates are superseded cleanly
    manual_sigs: set[tuple[str, int, Optional[int], str]] = set()
    for ma in manual_actions:
        norm_k = "BUILD" if ma.kind in ("BUILD", "WAIT_RESOURCES") else ma.kind
        c_tok = _normalize_manual_coords(ma.target_name) if norm_k in ("SPY", "ATTACK", "RECYCLE", "COLONIZE", "SCAN_GALAXY") else ""
        manual_sigs.add((norm_k, ma.planet_id, ma.target_id, c_tok))

    filtered_auto: list[PlannedAction] = []
    for act in planned_actions:
        norm_k = "BUILD" if act.kind in ("BUILD", "WAIT_RESOURCES") else act.kind
        c_tok = _normalize_manual_coords(act.target_name) if norm_k in ("SPY", "ATTACK", "RECYCLE", "COLONIZE", "SCAN_GALAXY") else ""
        if (norm_k, act.planet_id, act.target_id, c_tok) in manual_sigs:
            continue
        act_pr = prio_rank.get(act.priority, 9)
        res_entry = reserved_by_pid.get(act.planet_id)
        if (
            res_entry is not None
            and act.executable_now
            and act_pr > res_entry[0]
            and not act.cost.is_zero()
        ):
            _, res_need = res_entry
            p_obj = planets_by_id.get(act.planet_id)
            qty_mult = (
                max(1, _safe_strategy_int(act.target_level, 1))
                if (act.kind == "BUILD_SHIP" and act.manual_queue_id is None)
                else 1
            )
            act_spend = Resources(
                pig_iron=act.cost.pig_iron * qty_mult,
                metal=act.cost.metal * qty_mult,
                kryptonite=act.cost.kryptonite * qty_mult,
                spice=act.cost.spice * qty_mult,
            )
            overlaps = (
                (res_need.pig_iron > 0 and act_spend.pig_iron > 0)
                or (res_need.metal > 0 and act_spend.metal > 0)
                or (res_need.kryptonite > 0 and act_spend.kryptonite > 0)
                or (res_need.spice > 0 and act_spend.spice > 0)
            )
            combined = Resources(
                pig_iron=res_need.pig_iron + act_spend.pig_iron,
                metal=res_need.metal + act_spend.metal,
                kryptonite=res_need.kryptonite + act_spend.kryptonite,
                spice=res_need.spice + act_spend.spice,
            )
            if overlaps and p_obj is not None and not p_obj.resources.can_afford(combined):
                act = replace(
                    act,
                    executable_now=False,
                    reason=f"{act.reason} [Saving resources for prioritized user queue]",
                )
        filtered_auto.append(act)

    combined_list = list(manual_actions) + filtered_auto

    def _merge_sort_key(item_idx_pair: tuple[int, PlannedAction]) -> tuple[int, int, int, int]:
        orig_idx, a = item_idx_pair
        p_band = prio_rank.get(a.priority, 9)
        if a.kind in ("ALERT_HOSTILE", "ESCAPE_FLIGHT") and a.priority == "P0" and a.manual_queue_id is None:
            return (-1, 0, 0, orig_idx)
        if a.manual_queue_id is not None:
            return (p_band, 0, _safe_strategy_int(a.manual_sort_rank, 1000), orig_idx)
        return (p_band, 1, 0 if a.executable_now else 1, orig_idx)

    ordered = [a for _, a in sorted(enumerate(combined_list), key=_merge_sort_key)]
    ordered, eta_state = _reconcile_queue_metal_production(state, ordered)
    return attach_action_etas(eta_state, ordered), auto_satisfied_ids


def _reconcile_queue_metal_production(
    state: GameState,
    ordered: list[PlannedAction],
) -> tuple[list[PlannedAction], GameState]:
    """Review and adjust Blast furnace (`b2`) Metal production based on pinned queue position.

    Walks each colony's merged queue in execution order:
    - When a pinned item needs Metal (`cumulative_missing_metal > 0`) and is NOT behind an
      earlier Pig-iron-starved item, cancels any auto `b2 -> 0%` pause and immediately
      schedules `THROTTLE_BUILDING` (`Blast furnace -> 100%` or sustainable `%`) right ahead
      of the pinned item when `b2` is throttled/paused (`< 100%`).
    - When a pinned Metal-needing item sits BEHIND an earlier Pig-iron-only item that still
      lacks Pig iron, holds `b2` at `0%` until the earlier item's Pig iron requirement is met.
    """
    if not ordered or not state.planets:
        return ordered, state

    mining_tech = max(0, _safe_strategy_int((state.researches or {}).get("r1", 0), 0))
    speedup = _safe_strategy_float(getattr(state, "speedup_factor", 1.0), 1.0)
    if speedup <= 0:
        speedup = 1.0

    projected_rates_by_pid: dict[int, int] = {}
    out_list = list(ordered)

    for planet in state.planets:
        pid = planet.planet_id
        b2_lvl = planet.building_level(BLAST_FURNACE)
        if b2_lvl <= 0:
            continue
        p_acts_all = [a for a in out_list if a.planet_id == pid]
        if not any(a.manual_queue_id is not None for a in p_acts_all):
            continue
        # Respect explicit user-pinned Blast furnace throttle if present
        if any(
            a.manual_queue_id is not None
            and a.kind == "THROTTLE_BUILDING"
            and a.target_id == BLAST_FURNACE
            for a in p_acts_all
        ):
            continue

        rates = planet.economy_rates or {}
        cur_b2_pct = max(0, min(100, _safe_strategy_int(rates.get("resource2", 100), 100)))
        cur_b1_pct = max(0, min(100, _safe_strategy_int(rates.get("resource1", 100), 100)))
        res_pig = _safe_strategy_int(getattr(planet.resources, "pig_iron", 0), 0)
        res_met = _safe_strategy_int(getattr(planet.resources, "metal", 0), 0)
        sto_pig = _safe_strategy_int(getattr(planet.storage, "pig_iron", 0), 0)
        sto_met = _safe_strategy_int(getattr(planet.storage, "metal", 0), 0)
        metal_is_full = sto_met > 0 and res_met * 100 >= sto_met * 98

        e_prod = _safe_strategy_int(getattr(planet.energy, "produced", 0), 0)
        e_used = _safe_strategy_int(getattr(planet.energy, "used", 0), 0)
        prod_ratio = game_data.safe_big_ratio(e_prod, e_used, cap=1.0) if (e_used > 0 and e_prod < e_used) else 1.0
        factor = speedup * prod_ratio
        p_pig_gross = (
            game_data.mine_production_per_hour(
                PIG_IRON_MINE,
                planet.building_level(PIG_IRON_MINE),
                temperature=_safe_strategy_int(planet.temperature, 0),
                mining_tech_level=mining_tech,
            )
            * factor
            * (max(0, cur_b1_pct) / 100.0)
        )
        bf_pig_used_full = game_data.blast_furnace_pig_iron_used(b2_lvl) * factor
        net_pig_at_100 = p_pig_gross - bf_pig_used_full

        # Walk resource-consuming items on this planet in exact queue order
        avail_pig = res_pig
        avail_met = res_met
        queue_eval: list[tuple[PlannedAction, Resources, int, int]] = []
        for act in p_acts_all:
            if act.kind not in ("BUILD", "WAIT_RESOURCES", "RESEARCH", "BUILD_SHIP"):
                continue
            rsn_low = _safe_strategy_str(act.reason).lower()
            if "[unmet prereqs:" in rsn_low or "(requires research lab" in rsn_low or "(requires weapons factory" in rsn_low:
                continue
            qty_m = (
                max(1, _safe_strategy_int(act.target_level, 1))
                if (act.kind == "BUILD_SHIP" and act.manual_queue_id is None)
                else 1
            )
            spend = Resources(
                pig_iron=max(0, _safe_strategy_int(act.cost.pig_iron, 0)) * qty_m,
                metal=max(0, _safe_strategy_int(act.cost.metal, 0)) * qty_m,
                kryptonite=max(0, _safe_strategy_int(act.cost.kryptonite, 0)) * qty_m,
                spice=max(0, _safe_strategy_int(act.cost.spice, 0)) * qty_m,
            )
            need_pig = max(0, spend.pig_iron - avail_pig)
            need_met = max(0, spend.metal - avail_met)
            avail_pig = max(0, avail_pig - spend.pig_iron)
            avail_met = max(0, avail_met - spend.metal)
            queue_eval.append((act, spend, need_pig, need_met))

        first_metal_idx: Optional[int] = None
        for idx_q, (act_q, spend_q, _np, nm) in enumerate(queue_eval):
            if act_q.manual_queue_id is not None and spend_q.metal > 0 and nm > 0:
                first_metal_idx = idx_q
                break

        if first_metal_idx is None:
            continue

        m_act, m_spend, m_need_pig, m_need_met = queue_eval[first_metal_idx]
        earlier_items = queue_eval[:first_metal_idx]
        earlier_pig_blocker = next(
            (
                (ea, esp, enp)
                for (ea, esp, enp, enm) in earlier_items
                if enp > 0 and enm == 0 and not (sto_pig > 0 and esp.pig_iron > sto_pig)
            ),
            None,
        )

        if earlier_pig_blocker is not None:
            blk_act, _blk_spend, blk_need_pig = earlier_pig_blocker
            # Remove any auto Blast furnace > 0% resume while earlier queue item needs Pig iron
            out_list = [
                a
                for a in out_list
                if not (
                    a.planet_id == pid
                    and a.manual_queue_id is None
                    and a.kind == "THROTTLE_BUILDING"
                    and a.target_id == BLAST_FURNACE
                    and _safe_strategy_int(a.target_level, 0) > 0
                )
            ]
            if cur_b2_pct > 0 and not any(
                a.planet_id == pid
                and a.kind == "THROTTLE_BUILDING"
                and a.target_id == BLAST_FURNACE
                and _safe_strategy_int(a.target_level, -1) == 0
                for a in out_list
            ):
                pause_act = PlannedAction(
                    priority=blk_act.priority,
                    kind="THROTTLE_BUILDING",
                    planet_id=pid,
                    planet_coords=_safe_strategy_str(planet.coords),
                    target_id=BLAST_FURNACE,
                    target_name="Blast furnace 0%",
                    target_level=0,
                    reason=(
                        f"Hold Blast furnace ({cur_b2_pct}% -> 0%) for earlier queued "
                        f"{blk_act.target_name} (missing {blk_need_pig:,} Pig iron) before "
                        f"smelting Metal for #{m_act.manual_queue_id} {m_act.target_name}"
                    ),
                    executable_now=True,
                )
                ins_pos = next((i for i, x in enumerate(out_list) if x is blk_act), 0)
                out_list.insert(ins_pos, pause_act)
                projected_rates_by_pid[pid] = 0
            out_list = [
                replace(
                    x,
                    reason=f"{x.reason} [Metal production queued after {blk_act.target_name} Pig iron completes]",
                )
                if x is m_act and "[Metal production queued" not in _safe_strategy_str(x.reason)
                else x
                for x in out_list
            ]
            continue

        # Check if an earlier unexecuted item reserved Pig iron that would be drained by negative net_pig
        sum_pig_ahead_waiting = sum(
            esp.pig_iron for (ea, esp, _enp, _enm) in earlier_items if not ea.executable_now
        )
        if sum_pig_ahead_waiting > 0 and net_pig_at_100 < 0 and res_pig < sum_pig_ahead_waiting + 500:
            continue

        # Active Metal frontier reached: strip any auto Blast furnace 0% pause on this colony
        out_list = [
            a
            for a in out_list
            if not (
                a.planet_id == pid
                and a.manual_queue_id is None
                and a.kind == "THROTTLE_BUILDING"
                and a.target_id == BLAST_FURNACE
                and _safe_strategy_int(a.target_level, -1) == 0
            )
        ]

        if not metal_is_full and cur_b2_pct < 100:
            target_pct = 100
            if m_need_pig > 0 and bf_pig_used_full > p_pig_gross > 0 and res_pig < m_spend.pig_iron:
                target_pct = 10
                for cand_pct in (90, 80, 70, 60, 50, 40, 30, 20, 10):
                    if bf_pig_used_full * (cand_pct / 100.0) <= p_pig_gross * 0.85:
                        target_pct = cand_pct
                        break

            if cur_b2_pct != target_pct:
                # Replace any existing auto resume throttle on this colony so we place it cleanly ahead of m_act
                out_list = [
                    a
                    for a in out_list
                    if not (
                        a.planet_id == pid
                        and a.manual_queue_id is None
                        and a.kind == "THROTTLE_BUILDING"
                        and a.target_id == BLAST_FURNACE
                    )
                ]
                resume_act = PlannedAction(
                    priority=m_act.priority,
                    kind="THROTTLE_BUILDING",
                    planet_id=pid,
                    planet_coords=_safe_strategy_str(planet.coords),
                    target_id=BLAST_FURNACE,
                    target_name=f"Blast furnace {target_pct}%",
                    target_level=target_pct,
                    reason=(
                        f"Enable Metal production ({cur_b2_pct}% -> {target_pct}%) for pinned "
                        f"#{m_act.manual_queue_id} {m_act.target_name} (needs {m_need_met:,} Metal)"
                    ),
                    executable_now=True,
                )
                m_idx_out = next((i for i, x in enumerate(out_list) if x is m_act), 0)
                out_list.insert(m_idx_out, resume_act)
                projected_rates_by_pid[pid] = target_pct

    if not projected_rates_by_pid:
        return out_list, state

    adj_planets = tuple(
        replace(
            p,
            economy_rates={**(p.economy_rates or {}), "resource2": projected_rates_by_pid[p.planet_id]},
        )
        if p.planet_id in projected_rates_by_pid
        else p
        for p in state.planets
    )
    return out_list, replace(state, planets=adj_planets)
