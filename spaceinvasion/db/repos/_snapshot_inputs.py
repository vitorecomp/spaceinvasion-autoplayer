"""Typed input payloads and DTO mappers for `SnapshotsRepository`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from spaceinvasion.db.dto import (
    EmpirePlanetHotRecord,
    LatestStateHotRecord,
    PlanetSnapshotRecord,
    RankSnapshotRecord,
    StateSnapshotRecord,
)
from spaceinvasion.db.models import EmpirePlanetHot, LatestStateHot, PlanetSnapshot, RankSnapshot, StateSnapshot
from spaceinvasion.db.repos._helpers import to_float, to_int


@dataclass(frozen=True)
class StateSnapshotInput:
    """Typed input payload for `state_snapshots` and `latest_state_hot`."""

    state_payload: Mapping[str, object]
    plan_payload: Sequence[object] = ()
    source: str = "advisor"
    universe_id: int = 0
    user_id: int = 0
    nickname: str = ""
    active_planet_id: int = 0
    planet_count: int = 0
    ts: float | None = None


@dataclass(frozen=True)
class PlanetSnapshotInput:
    """Typed per-planet payload for `planet_snapshots` and `empire_planets_hot`."""

    planet_id: int
    coords: str
    name: str
    planet_type: str = "normal"
    temperature: int = 0
    fields_used: int = 0
    fields_max: int = 0
    pig_iron: int = 0
    metal: int = 0
    kryptonite: int = 0
    spice: int = 0
    energy_produced: int = 0
    energy_used: int = 0
    buildings: Mapping[str, object] = field(default_factory=dict)
    ships: Mapping[str, object] = field(default_factory=dict)
    defense: Mapping[str, object] = field(default_factory=dict)
    economy_rates: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RankSnapshotInput:
    """Typed account rank and score evolution sample."""

    universe_id: int = 0
    nickname: str = ""
    server_points: int | None = None
    server_rank: int | None = None
    estimated_points: float = 0.0
    pts_per_hr: float = 0.0
    spice_value: float = 0.0
    planets: int = 0
    ts: float | None = None


def state_to_dto(row: StateSnapshot, *, include_payload: bool) -> StateSnapshotRecord:
    """Convert a `StateSnapshot` ORM row into a frozen `StateSnapshotRecord` DTO."""
    return StateSnapshotRecord(
        id=to_int(row.id, 0),
        ts=to_float(row.ts, 0.0),
        source=row.source,
        universe_id=to_int(row.universe_id, 0),
        user_id=to_int(row.user_id, 0),
        nickname=row.nickname,
        active_planet_id=to_int(row.active_planet_id, 0),
        planet_count=to_int(row.planet_count, 0),
        planned_action_count=to_int(row.planned_action_count, 0),
        state_json=row.state_json if include_payload else None,
        plan_json=row.plan_json if include_payload else None,
    )


def hot_state_to_dto(row: LatestStateHot, *, include_payload: bool) -> LatestStateHotRecord:
    """Convert a `LatestStateHot` ORM row into a frozen `LatestStateHotRecord` DTO."""
    return LatestStateHotRecord(
        source_snapshot_id=to_int(row.source_snapshot_id, 0),
        ts=to_float(row.ts, 0.0),
        source=row.source,
        universe_id=to_int(row.universe_id, 0),
        user_id=to_int(row.user_id, 0),
        nickname=row.nickname,
        active_planet_id=to_int(row.active_planet_id, 0),
        planet_count=to_int(row.planet_count, 0),
        planned_action_count=to_int(row.planned_action_count, 0),
        state_json=row.state_json if include_payload else None,
        plan_json=row.plan_json if include_payload else None,
        updated_ts=to_float(row.updated_ts, 0.0),
    )


def planet_snap_to_dto(row: PlanetSnapshot) -> PlanetSnapshotRecord:
    """Convert a `PlanetSnapshot` ORM row into a frozen `PlanetSnapshotRecord` DTO."""
    return PlanetSnapshotRecord(
        id=to_int(row.id, 0),
        ts=to_float(row.ts, 0.0),
        universe_id=to_int(row.universe_id, 0),
        planet_id=to_int(row.planet_id, 0),
        coords=row.coords,
        name=row.name,
        planet_type=row.planet_type,
        temperature=to_int(row.temperature, 0),
        fields_used=to_int(row.fields_used, 0),
        fields_max=to_int(row.fields_max, 0),
        pig_iron=to_int(row.pig_iron, 0),
        metal=to_int(row.metal, 0),
        kryptonite=to_int(row.kryptonite, 0),
        spice=to_int(row.spice, 0),
        energy_produced=to_int(row.energy_produced, 0),
        energy_used=to_int(row.energy_used, 0),
        buildings_json=row.buildings_json,
        ships_json=row.ships_json,
        defense_json=row.defense_json,
        economy_rates_json=row.economy_rates_json,
        researches_json=row.researches_json,
    )


def empire_hot_to_dto(row: EmpirePlanetHot) -> EmpirePlanetHotRecord:
    """Convert an `EmpirePlanetHot` ORM row into a frozen `EmpirePlanetHotRecord` DTO."""
    return EmpirePlanetHotRecord(
        planet_id=to_int(row.planet_id, 0),
        universe_id=to_int(row.universe_id, 0),
        coords=row.coords,
        name=row.name,
        planet_type=row.planet_type,
        temperature=to_int(row.temperature, 0),
        fields_used=to_int(row.fields_used, 0),
        fields_max=to_int(row.fields_max, 0),
        pig_iron=to_int(row.pig_iron, 0),
        metal=to_int(row.metal, 0),
        kryptonite=to_int(row.kryptonite, 0),
        spice=to_int(row.spice, 0),
        energy_produced=to_int(row.energy_produced, 0),
        energy_used=to_int(row.energy_used, 0),
        is_active=bool(row.is_active),
        updated_ts=to_float(row.updated_ts, 0.0),
    )


def rank_to_dto(row: RankSnapshot) -> RankSnapshotRecord:
    """Convert a `RankSnapshot` ORM row into a frozen `RankSnapshotRecord` DTO."""
    return RankSnapshotRecord(
        id=to_int(row.id, 0),
        ts=to_float(row.ts, 0.0),
        universe_id=to_int(row.universe_id, 0),
        nickname=row.nickname,
        server_points=to_int(row.server_points, 0) if row.server_points is not None else None,
        server_rank=to_int(row.server_rank, 0) if row.server_rank is not None else None,
        estimated_points=to_float(row.estimated_points, 0.0),
        pts_per_hr=to_float(row.pts_per_hr, 0.0),
        spice_value=to_float(row.spice_value, 0.0),
        planets=to_int(row.planets, 0),
    )


def snap_to_latest_hot_dto(row: StateSnapshot, *, include_payload: bool) -> LatestStateHotRecord:
    """Convert a `StateSnapshot` fallback row into a frozen `LatestStateHotRecord` DTO."""
    ts_val = to_float(row.ts, 0.0)
    return LatestStateHotRecord(
        source_snapshot_id=to_int(row.id, 0),
        ts=ts_val,
        source=row.source,
        universe_id=to_int(row.universe_id, 0),
        user_id=to_int(row.user_id, 0),
        nickname=row.nickname,
        active_planet_id=to_int(row.active_planet_id, 0),
        planet_count=to_int(row.planet_count, 0),
        planned_action_count=to_int(row.planned_action_count, 0),
        state_json=row.state_json if include_payload else None,
        plan_json=row.plan_json if include_payload else None,
        updated_ts=ts_val,
    )


LATEST_PLANET_SNAPSHOTS_SQL: str = (
    "WITH RECURSIVE pids AS ("
    "  (SELECT planet_id FROM planet_snapshots ORDER BY planet_id ASC LIMIT 1)"
    "  UNION ALL"
    "  SELECT (SELECT ps.planet_id FROM planet_snapshots ps"
    "          WHERE ps.planet_id > pids.planet_id ORDER BY ps.planet_id ASC LIMIT 1)"
    "  FROM pids WHERE pids.planet_id IS NOT NULL"
    ") "
    "SELECT latest.* FROM pids "
    "CROSS JOIN LATERAL ("
    "  SELECT ps.* FROM planet_snapshots ps WHERE ps.planet_id = pids.planet_id"
    "  ORDER BY ps.ts DESC, ps.id DESC LIMIT 1"
    ") latest WHERE pids.planet_id IS NOT NULL ORDER BY latest.planet_id ASC"
)


__all__ = [
    "LATEST_PLANET_SNAPSHOTS_SQL",
    "PlanetSnapshotInput",
    "RankSnapshotInput",
    "StateSnapshotInput",
    "empire_hot_to_dto",
    "hot_state_to_dto",
    "planet_snap_to_dto",
    "rank_to_dto",
    "snap_to_latest_hot_dto",
    "state_to_dto",
]
