"""Canonical domain models, ``PlannedAction``, and pure empire/queue predicates (Phase 2.1)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
import re

from spaceinvasion.core._model_codec import game_state_from_dict, game_state_to_dict
from spaceinvasion.core._model_helpers import (
    ASTEROID_ALLOWED_BUILDINGS,
    BUILDING_ID_BY_NAME,
    RAID_COLONY_FORBIDDEN_BUILDINGS,
    is_asteroid_planet,
    is_building_allowed_on_planet_type,
    is_raid_planet,
    normalize_planet_type,
    safe_state_str,
)
from spaceinvasion.core._model_types import (
    BuildingOffer,
    DefenseOffer,
    EnergyBalance,
    FleetAlert,
    GameState,
    PlanetState,
    QueueJob,
    ResearchOffer,
    Resources,
    ShipOffer,
    TutorialStatus,
    TutorialTask,
    UnitOffer,
)
from spaceinvasion.core.coercion import coerce_int, safe_key
from spaceinvasion.core.coords import extract_coords_from_text

_COORD_TRIPLE_RE = re.compile(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", re.ASCII)
_SPY_QUEUE_MARKERS: tuple[str, ...] = ("spy", "spio", "espionage", "probe")
_COLONIZE_TOKENS: tuple[str, ...] = ("coloniz", "koloni", "siedel")
_RECYCLE_TOKENS: tuple[str, ...] = ("recycl", "rubble", "debris", "harvest", "abbau", "trümmer", "truemmer")


@dataclass(frozen=True)
class PlannedAction:
    """Single recommended or blocked empire action produced by the planner."""

    priority: str
    kind: str
    planet_id: int
    planet_coords: str
    target_name: str
    reason: str
    target_id: int | None = None
    target_level: int | None = None
    cost: Resources = Resources()
    missing: Resources = Resources()
    executable_now: bool = False
    action_href: str | None = None
    wait_hours: float | None = 0.0
    wait_seconds: float | None = 0.0
    wait_eta: str = "Ready"
    duration_seconds: float = 0.0
    duration_text: str = "Instant"
    total_hours: float | None = 0.0
    total_seconds: float | None = 0.0
    total_eta: str = "Ready"
    eta_bottleneck: str | None = None
    manual_queue_id: int | None = None
    manual_sort_rank: int | None = None


def planned_action_to_dict(action: PlannedAction) -> dict[str, object]:
    """Serialize a ``PlannedAction`` into a plain JSON-safe dictionary."""
    return {str(k): v for k, v in dataclasses.asdict(action).items()}


def is_in_flight_fleet_job(job: QueueJob) -> bool:
    """Return True when ``job`` represents an in-flight/returning fleet (excluding shipyard builds)."""
    if job.category != "fleet":
        return False
    return not safe_state_str(job.detail).strip().lower().startswith(("shipyard", "weapons factory"))


def is_in_flight_spy_job(job: QueueJob) -> bool:
    """Return True when an in-flight fleet ``QueueJob`` represents a SPY / espionage probe mission."""
    if not is_in_flight_fleet_job(job):
        return False
    txt = f"{safe_state_str(job.name)} {safe_state_str(job.detail)}".lower()
    return any(tok in txt for tok in _SPY_QUEUE_MARKERS)


def count_in_flight_fleets_for_planet(state: GameState, planet: PlanetState | None) -> int:
    """Count active in-flight fleet slots occupied on ``planet``."""
    norm_p = extract_coords_from_text(planet.coords if planet is not None else "")
    count = 0
    for q in state.active_queues:
        if not is_in_flight_fleet_job(q):
            continue
        q_orig = extract_coords_from_text(q.origin_coords)
        if not (q_orig and norm_p) or q_orig == norm_p:
            count += 1
    return count


def count_in_flight_spy_fleets_for_planet(state: GameState, planet: PlanetState | None) -> int:
    """Count active in-flight SPY probe fleet slots currently occupied on ``planet``."""
    norm_p = extract_coords_from_text(planet.coords if planet is not None else "")
    count = 0
    for q in state.active_queues:
        if not is_in_flight_spy_job(q):
            continue
        q_orig = extract_coords_from_text(q.origin_coords)
        if not (q_orig and norm_p) or q_orig == norm_p:
            count += 1
    return count


def has_in_flight_fleet_from_planet(state: GameState, planet: PlanetState | None) -> bool:
    """Return True when ``planet`` has an active in-flight or returning fleet."""
    return count_in_flight_fleets_for_planet(state, planet) > 0


def extract_in_flight_colonize_coords(state: GameState) -> set[str]:
    """Return normalized ``GG:SSS:PP`` coordinates of outbound in-flight ``COLONIZE`` missions."""
    out: set[str] = set()
    for q in state.active_queues:
        if not is_in_flight_fleet_job(q):
            continue
        combined = f"{safe_state_str(q.name)} {safe_state_str(q.detail)}"
        low = combined.lower()
        if "return" in low or "rück" in low or "rueck" in low or not any(tok in low for tok in _COLONIZE_TOKENS):
            continue
        for m in _COORD_TRIPLE_RE.finditer(combined):
            g, s, p = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= g <= 14 and 1 <= s <= 400 and 1 <= p <= 16:
                out.add(f"{g:02d}:{s:03d}:{p:02d}")
    return out


def extract_in_flight_recycle_coords(state: GameState) -> set[str]:
    """Return normalized ``GG:SSS:PP`` target coordinates of in-flight ``RECYCLE`` missions."""
    out: set[str] = set()
    for q in state.active_queues:
        if not is_in_flight_fleet_job(q):
            continue
        combined = f"{safe_state_str(q.name)} {safe_state_str(q.detail)}"
        low = combined.lower()
        if not any(tok in low for tok in _RECYCLE_TOKENS):
            continue
        for m in _COORD_TRIPLE_RE.finditer(combined):
            g, s, p = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= g <= 14 and 1 <= s <= 400 and 1 <= p <= 16:
                out.add(f"{g:02d}:{s:03d}:{p:02d}")
    return out


def extract_in_flight_station_dest_coords(state: GameState) -> set[str]:
    """Return normalized destination coordinates of outbound/in-flight ``STATION`` missions."""
    out: set[str] = set()
    for q in state.active_queues:
        if not is_in_flight_fleet_job(q):
            continue
        q_name = safe_state_str(q.name)
        q_low = q_name.lower()
        if not (q_low.startswith("station") or "station →" in q_low or "station ->" in q_low):
            continue
        if norm_c := extract_coords_from_text(q_name):
            out.add(norm_c)
    return out


def homeworld_planet_id(state: GameState) -> int | None:
    """Return the Pos. 1 Homeworld ``planet_id`` (first normal planet in ``state.planets``)."""
    for p in state.planets:
        if normalize_planet_type(p) == "normal" and (pid := coerce_int(p.planet_id, 0)) > 0:
            return pid
    if state.planets and (pid0 := coerce_int(state.planets[0].planet_id, 0)) > 0:
        return pid0
    return None


def count_allocated_normal_planets(state: GameState | None) -> int:
    """Return the count of owned normal colonies (excluding asteroids and raid colonies)."""
    return 0 if state is None else sum(1 for p in state.planets if normalize_planet_type(p) == "normal")


def is_undersized_fresh_colony(
    planet: PlanetState | None, state: GameState | None, min_colony_fields: int = 300
) -> bool:
    """Return True iff ``planet`` is a newly settled secondary normal colony with ``< min_colony_fields`` and zero development."""
    if (
        planet is None
        or state is None
        or normalize_planet_type(planet) != "normal"
        or count_allocated_normal_planets(state) < 2
    ):
        return False
    pid, hw_id = coerce_int(planet.planet_id, 0), homeworld_planet_id(state)
    if pid <= 0 or (hw_id is not None and pid == hw_id):
        return False
    pname_l = safe_state_str(planet.name).strip().lower()
    if "homeworld" in pname_l or "heimatplanet" in pname_l:
        return False
    min_tgt, f_max = max(50, coerce_int(min_colony_fields, 300)), max(0, coerce_int(planet.fields_max, 0))
    if f_max <= 0 or f_max >= min_tgt or max(0, coerce_int(planet.fields_used, 0)) > 0:
        return False
    if sum(max(0, coerce_int(v, 0)) for v in planet.buildings.values()) > 0:
        return False
    if sum(max(0, coerce_int(v, 0)) for k, v in planet.ships.items() if safe_key(k) != "s9") > 0:
        return False
    if sum(max(0, coerce_int(v, 0)) for v in planet.defense.values()) > 0:
        return False
    p_orig, active_pid = extract_coords_from_text(planet.coords), coerce_int(state.active_planet_id, 0)
    for q in state.active_queues:
        if q.category != "building":
            continue
        q_orig = extract_coords_from_text(q.origin_coords)
        if (q_orig and p_orig and q_orig == p_orig) or (not q_orig and pid == active_pid):
            return False
    return True


__all__ = [
    "ASTEROID_ALLOWED_BUILDINGS",
    "BUILDING_ID_BY_NAME",
    "BuildingOffer",
    "DefenseOffer",
    "EnergyBalance",
    "FleetAlert",
    "GameState",
    "PlanetState",
    "PlannedAction",
    "QueueJob",
    "RAID_COLONY_FORBIDDEN_BUILDINGS",
    "ResearchOffer",
    "Resources",
    "ShipOffer",
    "TutorialStatus",
    "TutorialTask",
    "UnitOffer",
    "count_allocated_normal_planets",
    "count_in_flight_fleets_for_planet",
    "count_in_flight_spy_fleets_for_planet",
    "extract_in_flight_colonize_coords",
    "extract_in_flight_recycle_coords",
    "extract_in_flight_station_dest_coords",
    "game_state_from_dict",
    "game_state_to_dict",
    "has_in_flight_fleet_from_planet",
    "homeworld_planet_id",
    "is_asteroid_planet",
    "is_building_allowed_on_planet_type",
    "is_in_flight_fleet_job",
    "is_in_flight_spy_job",
    "is_raid_planet",
    "is_undersized_fresh_colony",
    "normalize_planet_type",
    "planned_action_to_dict",
]
