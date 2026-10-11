"""Shared typed protocols, resource/config helpers, and in-flight resolvers for live reconciliation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Protocol, runtime_checkable

from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_finite_float, coerce_int, coerce_optional_int
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import Resources, normalize_planet_type
from spaceinvasion.planner.ai._problem_queues import resolve_untagged_building_job_planet
from spaceinvasion.planner.ai._types import (
    LEVEL_RE,
    TIMER_RE,
    BuildingOfferLike,
    EnergyLike,
    GameStateLike,
    PlanetLike,
    extract_building_job_id_and_level,
    is_active_construction_status,
    is_queue_job_expired,
)
from spaceinvasion.planner.config_optimizer import RESOURCE_KEYS, research_catalog

_STALE_REMOTE_BLOCKING_TOKENS: tuple[str, ...] = (
    "in progress",
    "construction",
    "queue",
    "full",
    "require",
    "prereq",
    "locked",
    "blocked",
    "field",
    "max",
    "unmet",
    "demolish",
    "research",
    "occupied",
    "already",
    "not allowed",
    "unavailable",
)


@runtime_checkable
class ReadResourcesLike(Protocol):
    """Read-only structural protocol satisfied by frozen ``Resources`` dataclasses and ``ResourcesLike``."""

    @property
    def pig_iron(self) -> int: ...
    @property
    def metal(self) -> int: ...
    @property
    def kryptonite(self) -> int: ...
    @property
    def spice(self) -> int: ...


@runtime_checkable
class ReadConfigLike(Protocol):
    """Read-only structural protocol satisfied by frozen ``StrategyConfig`` and ``ConfigLike``."""

    @property
    def allow_multi_planet(self) -> bool: ...
    @property
    def ai_adaptive_execution(self) -> bool: ...


@runtime_checkable
class PlannedActionLike(Protocol):
    """Structural protocol satisfied by both ``core.model.PlannedAction`` and legacy ``strategy.PlannedAction``."""

    @property
    def priority(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def planet_id(self) -> int: ...
    @property
    def planet_coords(self) -> str: ...
    @property
    def target_name(self) -> str: ...
    @property
    def reason(self) -> str: ...
    @property
    def target_id(self) -> int | None: ...
    @property
    def target_level(self) -> int | None: ...
    @property
    def cost(self) -> ReadResourcesLike: ...
    @property
    def missing(self) -> ReadResourcesLike: ...
    @property
    def executable_now(self) -> bool: ...
    @property
    def action_href(self) -> str | None: ...


@runtime_checkable
class EnergyNetLike(Protocol):
    """Structural protocol for energy balances exposing a precomputed ``net`` attribute/property."""

    @property
    def net(self) -> int: ...


@runtime_checkable
class ConfigAllowResearchLike(Protocol):
    """Optional ``allow_research`` flag on strategy configs."""

    @property
    def allow_research(self) -> bool: ...


@runtime_checkable
class ConfigBuildSlotsLike(Protocol):
    """Optional ``max_build_queue_slots`` attribute on custom/legacy strategy configs."""

    @property
    def max_build_queue_slots(self) -> object: ...


def resource_value(res: ReadResourcesLike | None, key: str) -> int:
    """Return coerced integer amount for one primary resource key."""
    if res is None:
        return 0
    if key == "pig_iron":
        return coerce_int(res.pig_iron, 0)
    if key == "metal":
        return coerce_int(res.metal, 0)
    if key == "kryptonite":
        return coerce_int(res.kryptonite, 0)
    if key == "spice":
        return coerce_int(res.spice, 0)
    return 0


def resources_float_list(res: ReadResourcesLike | None) -> list[float]:
    """Extract a 4-element non-negative float list ``[pig_iron, metal, kryptonite, spice]``."""
    if res is None:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        max(0.0, float(res.pig_iron or 0)),
        max(0.0, float(res.metal or 0)),
        max(0.0, float(res.kryptonite or 0)),
        max(0.0, float(res.spice or 0)),
    ]


def resources_is_zero(res: ReadResourcesLike) -> bool:
    """Return True iff all four resource fields on ``res`` coerce to zero."""
    return (
        coerce_int(res.pig_iron, 0) == 0
        and coerce_int(res.metal, 0) == 0
        and coerce_int(res.kryptonite, 0) == 0
        and coerce_int(res.spice, 0) == 0
    )


def resources_can_afford(have: ReadResourcesLike, cost: ReadResourcesLike) -> bool:
    """Return True iff ``have`` meets or exceeds ``cost`` in all four resources."""
    return (
        coerce_int(have.pig_iron, 0) >= coerce_int(cost.pig_iron, 0)
        and coerce_int(have.metal, 0) >= coerce_int(cost.metal, 0)
        and coerce_int(have.kryptonite, 0) >= coerce_int(cost.kryptonite, 0)
        and coerce_int(have.spice, 0) >= coerce_int(cost.spice, 0)
    )


def resources_missing(have: ReadResourcesLike, cost: ReadResourcesLike) -> Resources:
    """Return a ``core.model.Resources`` shortfall needed to cover ``cost`` from ``have``."""
    return Resources(
        pig_iron=max(0, coerce_int(cost.pig_iron, 0) - coerce_int(have.pig_iron, 0)),
        metal=max(0, coerce_int(cost.metal, 0) - coerce_int(have.metal, 0)),
        kryptonite=max(0, coerce_int(cost.kryptonite, 0) - coerce_int(have.kryptonite, 0)),
        spice=max(0, coerce_int(cost.spice, 0) - coerce_int(have.spice, 0)),
    )


def energy_net_value(energy: EnergyLike) -> int:
    """Return net energy balance without dynamic attribute lookup."""
    if isinstance(energy, EnergyNetLike):
        return coerce_int(energy.net, 0)
    return coerce_int(energy.produced, 0) - coerce_int(energy.used, 0)


def config_max_build_slots(config: ReadConfigLike) -> int:
    """Return configured max build queue slots (default ``1``)."""
    if isinstance(config, ConfigBuildSlotsLike):
        return max(1, coerce_int(config.max_build_queue_slots, 1))
    return 1


def config_allow_research(config: ReadConfigLike) -> bool:
    """Return ``config.allow_research`` when present, otherwise ``True``."""
    if isinstance(config, ConfigAllowResearchLike):
        return bool(config.allow_research)
    return True


def index_planets(
    planets: Sequence[PlanetLike],
    *,
    normal_only: bool = False,
) -> tuple[dict[int, PlanetLike], dict[str, PlanetLike], list[PlanetLike]]:
    """Build ``(by_id, by_coords, non_raid_or_normal_list)`` lookup indices."""
    by_id: dict[int, PlanetLike] = {}
    by_coords: dict[str, PlanetLike] = {}
    filtered: list[PlanetLike] = []
    for planet in planets:
        ptype = normalize_planet_type(planet)
        if normal_only and ptype != "normal":
            continue
        pid = int(planet.planet_id)
        by_id[pid] = planet
        coords = str(planet.coords or "").strip()
        if coords:
            by_coords[coords] = planet
            by_coords[normalize_coords(coords)] = planet
        if normal_only or ptype != "raid":
            filtered.append(planet)
    return by_id, by_coords, filtered


def match_step_planet(
    step: Mapping[str, object],
    planets_by_id: Mapping[int, PlanetLike],
    planets_by_coords: Mapping[str, PlanetLike],
) -> PlanetLike | None:
    """Match a serialized plan step onto a live planet by ``planet_id`` or ``planet_coords``."""
    step_pid = coerce_optional_int(step.get("planet_id"), None)
    planet = planets_by_id.get(step_pid) if step_pid is not None else None
    if planet is None:
        step_coords = str(step.get("planet_coords") or "").strip()
        if step_coords:
            planet = planets_by_coords.get(step_coords) or planets_by_coords.get(normalize_coords(step_coords))
    return planet


def offers_by_building_id(planet: PlanetLike) -> dict[int, BuildingOfferLike]:
    """Index parsed building offers on ``planet`` by integer ``building_id``."""
    return {int(off.building_id): off for off in (planet.building_offers or ()) if off.building_id is not None}


def storage_exceeds_cost(planet: PlanetLike, bid: int, cost: ReadResourcesLike) -> bool:
    """Return True when a non-warehouse building's cost exceeds any positive populated warehouse capacity."""
    if bid in (11, 12, 13, 14):
        return False
    for res_key in RESOURCE_KEYS:
        cap = resource_value(planet.storage, res_key)
        need = resource_value(cost, res_key)
        if cap > 0 and need > cap:
            return True
    return False


def _resolve_in_flight_buildings(state: GameStateLike) -> tuple[dict[int, dict[str, int]], dict[int, int]]:
    """Return ``(in_flight_target_levels[planet_id][bkey], active_job_count[planet_id])``."""
    in_flight: dict[int, dict[str, int]] = {}
    counts: dict[int, int] = {}
    state_ts = max(0.0, coerce_finite_float(state.timestamp, 0.0))
    planets = list(state.planets or ())
    _, by_coords, _ = index_planets(planets)
    active_p = state.active_planet()

    for job in state.active_queues or ():
        if job.category != "building" or is_queue_job_expired(job, state_ts):
            continue
        bid, parsed_tgt = extract_building_job_id_and_level(job)
        origin = str(job.origin_coords or "").strip()
        target_planet = by_coords.get(origin) or (by_coords.get(normalize_coords(origin)) if origin else None)
        if target_planet is None and not origin:
            target_planet = resolve_untagged_building_job_planet(bid, parsed_tgt, planets, active_p)
        if target_planet is None:
            continue
        pid = int(target_planet.planet_id)
        counts[pid] = counts.get(pid, 0) + 1
        if bid is None:
            continue
        cur_lvl = target_planet.building_level(bid)
        job_name_low = str(job.name or "").lower()
        if (parsed_tgt is not None and parsed_tgt < cur_lvl) or "demolish" in job_name_low or "tear" in job_name_low:
            continue
        tgt_lvl = parsed_tgt
        if tgt_lvl is None or tgt_lvl <= cur_lvl:
            m_lvl = LEVEL_RE.search(str(job.name or ""))
            tgt_lvl = max(cur_lvl + 1, int(m_lvl.group(1))) if m_lvl else (cur_lvl + 1)
        bkey = f"b{bid}"
        pmap = in_flight.setdefault(pid, {})
        pmap[bkey] = max(pmap.get(bkey, 0), int(tgt_lvl))

    for planet in planets:
        pid = int(planet.planet_id)
        for off in planet.building_offers or ():
            if not is_active_construction_status(off.status_text):
                continue
            bid = coerce_optional_int(off.building_id, None)
            if bid is None:
                continue
            cur_lvl = planet.building_level(bid)
            tgt_lvl = max(cur_lvl + 1, coerce_int(off.target_level, cur_lvl + 1))
            bkey = f"b{bid}"
            pmap = in_flight.setdefault(pid, {})
            pmap[bkey] = max(pmap.get(bkey, 0), int(tgt_lvl))
            counts[pid] = max(counts.get(pid, 0), 1)
    return in_flight, counts


def _resolve_in_flight_research(state: GameStateLike) -> tuple[dict[str, int], bool]:
    """Return ``(in_flight_target_level_by_rkey, research_queue_busy)``."""
    in_flight: dict[str, int] = {}
    busy = False
    researches = state.researches or {}
    for off in state.research_offers or ():
        if off.active:
            busy = True
            rkey = str(off.research_key or "").strip()
            if rkey:
                cur = max(0, int(researches.get(rkey, 0) or 0))
                tgt = max(cur + 1, coerce_int(off.target_level, cur + 1))
                in_flight[rkey] = max(in_flight.get(rkey, 0), tgt)

    name_to_rkey = {game_data.normalize_unit_key(str(info["name"])): str(rkey) for rkey, info in research_catalog()}
    for job in state.active_queues or ():
        if job.category != "research":
            continue
        busy = True
        raw_name = str(job.name or "")
        clean = re.sub(r"\(.*?\)", "", raw_name)
        clean = TIMER_RE.sub("", clean)
        clean = re.sub(r"\b(?:level|lvl|l)\s*\d+\b", "", clean, flags=re.IGNORECASE).strip()
        rkey_opt = name_to_rkey.get(game_data.normalize_unit_key(clean))
        if not rkey_opt:
            continue
        cur = max(0, int(researches.get(rkey_opt, 0) or 0))
        tgt_opt = coerce_optional_int(job.target_level, None)
        if tgt_opt is None or tgt_opt <= cur:
            m_lvl = LEVEL_RE.search(raw_name)
            tgt_opt = max(cur + 1, int(m_lvl.group(1))) if m_lvl else (cur + 1)
        in_flight[rkey_opt] = max(in_flight.get(rkey_opt, 0), int(tgt_opt))
    return in_flight, busy


def _is_stale_remote_resource_wait(offer: BuildingOfferLike | None) -> bool:
    """Return True when a non-buildable cached offer shows no hard construction/prereq/field block."""
    if offer is None or offer.can_build:
        return False
    status_low = str(offer.status_text or "").strip().lower()
    if not status_low:
        return True
    return not any(tok in status_low for tok in _STALE_REMOTE_BLOCKING_TOKENS)
