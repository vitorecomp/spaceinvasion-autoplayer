"""Typed input payloads, DTO mappers, and upsert helpers for `IntelRepository`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.dto import SpyReportDTO, SpyTargetRecord
from backend.db.models import SpyReportRecord, SpyTarget
from backend.db.repos._helpers import coord_query_variants, normalize_coords, to_float, to_int


@dataclass(frozen=True)
class SpyReportInput:
    """Typed input payload for recording an espionage report and updating `spy_targets`."""

    target_coords: str
    owner: str = ""
    planet_name: str = ""
    is_spacia: bool = False
    pig_iron: int = 0
    metal: int = 0
    kryptonite: int = 0
    spice: int = 0
    buildings: Mapping[str, object] = field(default_factory=dict)
    fleet: Mapping[str, object] = field(default_factory=dict)
    defense: Mapping[str, object] = field(default_factory=dict)
    researches: Mapping[str, object] = field(default_factory=dict)
    dug_in: bool | None = None
    uninhabited_fields: int | None = None
    server_msg_id: str = ""
    msg_date_text: str = ""
    raw_text: str = ""


@dataclass(frozen=True)
class SpyTargetUpsertInput:
    """Typed non-destructive upsert payload for `spy_targets`."""

    coords: str
    owner: str = ""
    planet_name: str = ""
    status_color: str = "yellow"
    is_inactive: bool = False
    is_spacia: bool = False
    has_debris: bool = False
    points: int | None = None
    pig_iron: int = 0
    metal: int = 0
    kryptonite: int = 0
    spice: int = 0
    fleet: Mapping[str, object] = field(default_factory=dict)
    defense: Mapping[str, object] = field(default_factory=dict)
    dug_in: bool = False
    last_scanned_ts: float = 0.0
    last_spied_ts: float = 0.0
    uninhabited_fields: int | None = None
    colonize_min_fields: int | None = None
    colonize_max_fields: int | None = None
    temperature: int | None = None


def spy_target_to_dto(row: SpyTarget) -> SpyTargetRecord:
    """Convert a `SpyTarget` ORM row into a frozen `SpyTargetRecord` DTO."""
    return SpyTargetRecord(
        coords=normalize_coords(row.coords),
        owner=row.owner,
        planet_name=row.planet_name,
        status_color=row.status_color,
        is_inactive=bool(row.is_inactive),
        is_spacia=bool(row.is_spacia),
        has_debris=bool(row.has_debris),
        points=to_int(row.points, 0) if row.points is not None else None,
        pig_iron=to_int(row.pig_iron, 0),
        metal=to_int(row.metal, 0),
        kryptonite=to_int(row.kryptonite, 0),
        spice=to_int(row.spice, 0),
        fleet_json=row.fleet_json,
        defense_json=row.defense_json,
        dug_in=bool(row.dug_in),
        last_scanned_ts=to_float(row.last_scanned_ts, 0.0),
        last_spied_ts=to_float(row.last_spied_ts, 0.0),
        blocked_until_ts=to_float(row.blocked_until_ts, 0.0),
        block_reason=row.block_reason,
        bash_hits=to_int(row.bash_hits, 0),
        last_blocked_ts=to_float(row.last_blocked_ts, 0.0),
        uninhabited_fields=to_int(row.uninhabited_fields, 0) if row.uninhabited_fields is not None else None,
        spy_count=to_int(row.spy_count, 0),
        is_own_planet=bool(row.is_own_planet),
        merged_into=row.merged_into,
        colonize_min_fields=to_int(row.colonize_min_fields, 0) if row.colonize_min_fields is not None else None,
        colonize_max_fields=to_int(row.colonize_max_fields, 0) if row.colonize_max_fields is not None else None,
        temperature=to_int(row.temperature, 0) if row.temperature is not None else None,
    )


def spy_report_to_dto(row: SpyReportRecord) -> SpyReportDTO:
    """Convert a `SpyReportRecord` ORM row into a frozen `SpyReportDTO`."""
    return SpyReportDTO(
        id=to_int(row.id, 0),
        ts=to_float(row.ts, 0.0),
        target_coords=normalize_coords(row.target_coords),
        owner=row.owner,
        planet_name=row.planet_name,
        is_spacia=bool(row.is_spacia),
        pig_iron=to_int(row.pig_iron, 0),
        metal=to_int(row.metal, 0),
        kryptonite=to_int(row.kryptonite, 0),
        spice=to_int(row.spice, 0),
        loot_spice_eq=to_float(row.loot_spice_eq, 0.0),
        buildings_json=row.buildings_json,
        fleet_json=row.fleet_json,
        defense_json=row.defense_json,
        researches_json=row.researches_json,
        dug_in=bool(row.dug_in) if row.dug_in is not None else None,
        uninhabited_fields=to_int(row.uninhabited_fields, 0) if row.uninhabited_fields is not None else None,
        server_msg_id=row.server_msg_id,
        msg_date_text=row.msg_date_text,
        raw_text=row.raw_text,
    )


def coalesce_legacy_spy_target_coords(session: Session, norm_coords_list: Sequence[str]) -> None:
    """Promote legacy unpadded `spy_targets` keys (e.g. `'2:380:4'`) to canonical `'02:380:04'` non-destructively."""
    alt_to_canon: dict[str, str] = {}
    for raw_c in norm_coords_list:
        nc = normalize_coords(raw_c)
        if not nc:
            continue
        for variant in coord_query_variants(nc):
            if variant != nc:
                alt_to_canon[variant] = nc
    if not alt_to_canon:
        return
    legacy_rows = (
        session.execute(
            select(SpyTarget).where(SpyTarget.coords.in_(sorted(alt_to_canon)), SpyTarget.merged_into == "")
        )
        .scalars()
        .all()
    )
    if not legacy_rows:
        return
    canon_needed = sorted({alt_to_canon[r.coords] for r in legacy_rows if r.coords in alt_to_canon})
    existing_canon = set(
        session.execute(select(SpyTarget.coords).where(SpyTarget.coords.in_(canon_needed))).scalars().all()
    )
    for leg in legacy_rows:
        canon = alt_to_canon.get(leg.coords)
        if not canon:
            continue
        if canon not in existing_canon:
            session.execute(update(SpyTarget).where(SpyTarget.coords == leg.coords).values(coords=canon))
            existing_canon.add(canon)
        else:
            session.execute(update(SpyTarget).where(SpyTarget.coords == leg.coords).values(merged_into=canon))


def upsert_spy_target_from_report(
    session: Session,
    *,
    norm_c: str,
    owner_s: str,
    pname_s: str,
    insert_color: str,
    insert_inactive: bool,
    explicit_status: bool,
    is_spacia: bool,
    pts_opt: int | None,
    resources: tuple[int, int, int, int],
    flt_j: str,
    def_j: str,
    dug_in: bool | None,
    uf_opt: int | None,
    now_ts: float,
) -> None:
    """Upsert `spy_targets` from a newly ingested spy report without overwriting protection flags."""
    coalesce_legacy_spy_target_coords(session, (norm_c,))
    pig_i, met_i, kry_i, spi_i = resources
    stmt = pg_insert(SpyTarget).values(
        coords=norm_c,
        owner=owner_s,
        planet_name=pname_s,
        status_color=insert_color,
        is_inactive=1 if insert_inactive else 0,
        is_spacia=1 if is_spacia else 0,
        points=pts_opt,
        pig_iron=pig_i,
        metal=met_i,
        kryptonite=kry_i,
        spice=spi_i,
        fleet_json=flt_j,
        defense_json=def_j,
        dug_in=1 if dug_in else 0,
        last_scanned_ts=now_ts,
        last_spied_ts=now_ts,
        uninhabited_fields=uf_opt,
        spy_count=1,
        merged_into="",
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[SpyTarget.coords],
            set_={
                "merged_into": "",
                "owner": case((stmt.excluded.owner != "", stmt.excluded.owner), else_=SpyTarget.owner),
                "planet_name": case(
                    (stmt.excluded.planet_name != "", stmt.excluded.planet_name), else_=SpyTarget.planet_name
                ),
                "status_color": stmt.excluded.status_color if explicit_status else SpyTarget.status_color,
                "is_inactive": stmt.excluded.is_inactive if explicit_status else SpyTarget.is_inactive,
                "is_spacia": case((stmt.excluded.is_spacia == 1, 1), else_=SpyTarget.is_spacia),
                "points": func.coalesce(stmt.excluded.points, SpyTarget.points),
                "pig_iron": stmt.excluded.pig_iron,
                "metal": stmt.excluded.metal,
                "kryptonite": stmt.excluded.kryptonite,
                "spice": stmt.excluded.spice,
                "fleet_json": stmt.excluded.fleet_json,
                "defense_json": stmt.excluded.defense_json,
                "dug_in": stmt.excluded.dug_in,
                "last_scanned_ts": func.greatest(SpyTarget.last_scanned_ts, stmt.excluded.last_scanned_ts),
                "last_spied_ts": stmt.excluded.last_spied_ts,
                "uninhabited_fields": func.coalesce(stmt.excluded.uninhabited_fields, SpyTarget.uninhabited_fields),
                "spy_count": SpyTarget.spy_count + 1,
            },
        )
    )


__all__ = [
    "SpyReportInput",
    "SpyTargetUpsertInput",
    "coalesce_legacy_spy_target_coords",
    "spy_report_to_dto",
    "spy_target_to_dto",
    "upsert_spy_target_from_report",
]
