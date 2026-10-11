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
        id=int(row.id),
        ts=float(row.ts),
        source=row.source,
        universe_id=int(row.universe_id),
        user_id=int(row.user_id),
        nickname=row.nickname,
        active_planet_id=int(row.active_planet_id),
        planet_count=int(row.planet_count),
        planned_action_count=int(row.planned_action_count),
        state_json=row.state_json if include_payload else None,
        plan_json=row.plan_json if include_payload else None,
    )


def hot_state_to_dto(row: LatestStateHot, *, include_payload: bool) -> LatestStateHotRecord:
    """Convert a `LatestStateHot` ORM row into a frozen `LatestStateHotRecord` DTO."""
    return LatestStateHotRecord(
        source_snapshot_id=int(row.source_snapshot_id),
        ts=float(row.ts),
        source=row.source,
        universe_id=int(row.universe_id),
        user_id=int(row.user_id),
        nickname=row.nickname,
        active_planet_id=int(row.active_planet_id),
        planet_count=int(row.planet_count),
        planned_action_count=int(row.planned_action_count),
        state_json=row.state_json if include_payload else None,
        plan_json=row.plan_json if include_payload else None,
        updated_ts=float(row.updated_ts),
    )


def planet_snap_to_dto(row: PlanetSnapshot) -> PlanetSnapshotRecord:
    """Convert a `PlanetSnapshot` ORM row into a frozen `PlanetSnapshotRecord` DTO."""
    return PlanetSnapshotRecord(
        id=int(row.id),
        ts=float(row.ts),
        universe_id=int(row.universe_id),
        planet_id=int(row.planet_id),
        coords=row.coords,
        name=row.name,
        planet_type=row.planet_type,
        temperature=int(row.temperature),
        fields_used=int(row.fields_used),
        fields_max=int(row.fields_max),
        pig_iron=int(row.pig_iron),
        metal=int(row.metal),
        kryptonite=int(row.kryptonite),
        spice=int(row.spice),
        energy_produced=int(row.energy_produced),
        energy_used=int(row.energy_used),
        buildings_json=row.buildings_json,
        ships_json=row.ships_json,
        defense_json=row.defense_json,
        economy_rates_json=row.economy_rates_json,
        researches_json=row.researches_json,
    )


def empire_hot_to_dto(row: EmpirePlanetHot) -> EmpirePlanetHotRecord:
    """Convert an `EmpirePlanetHot` ORM row into a frozen `EmpirePlanetHotRecord` DTO."""
    return EmpirePlanetHotRecord(
        planet_id=int(row.planet_id),
        universe_id=int(row.universe_id),
        coords=row.coords,
        name=row.name,
        planet_type=row.planet_type,
        temperature=int(row.temperature),
        fields_used=int(row.fields_used),
        fields_max=int(row.fields_max),
        pig_iron=int(row.pig_iron),
        metal=int(row.metal),
        kryptonite=int(row.kryptonite),
        spice=int(row.spice),
        energy_produced=int(row.energy_produced),
        energy_used=int(row.energy_used),
        is_active=bool(row.is_active),
        updated_ts=float(row.updated_ts),
    )


def rank_to_dto(row: RankSnapshot) -> RankSnapshotRecord:
    """Convert a `RankSnapshot` ORM row into a frozen `RankSnapshotRecord` DTO."""
    return RankSnapshotRecord(
        id=int(row.id),
        ts=float(row.ts),
        universe_id=int(row.universe_id),
        nickname=row.nickname,
        server_points=int(row.server_points) if row.server_points is not None else None,
        server_rank=int(row.server_rank) if row.server_rank is not None else None,
        estimated_points=float(row.estimated_points),
        pts_per_hr=float(row.pts_per_hr),
        spice_value=float(row.spice_value),
        planets=int(row.planets),
    )


def snap_to_latest_hot_dto(row: StateSnapshot, *, include_payload: bool) -> LatestStateHotRecord:
    """Convert a `StateSnapshot` fallback row into a frozen `LatestStateHotRecord` DTO."""
    return LatestStateHotRecord(
        source_snapshot_id=int(row.id),
        ts=float(row.ts),
        source=row.source,
        universe_id=int(row.universe_id),
        user_id=int(row.user_id),
        nickname=row.nickname,
        active_planet_id=int(row.active_planet_id),
        planet_count=int(row.planet_count),
        planned_action_count=int(row.planned_action_count),
        state_json=row.state_json if include_payload else None,
        plan_json=row.plan_json if include_payload else None,
        updated_ts=float(row.ts),
    )


__all__ = [
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
