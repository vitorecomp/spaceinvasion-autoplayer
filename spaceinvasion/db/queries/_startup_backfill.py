"""SQLAlchemy startup backfill application and hot-table rebuild queries."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from spaceinvasion.db.models import (
    ColonyCandidateHot,
    EmpirePlanetHot,
    GalaxyScan,
    GalaxySectorHot,
    GalaxySlot,
    GameMessage,
    MetaEntry,
    SpyReportRecord,
    SpyTarget,
)
from spaceinvasion.db.repos._helpers import (
    clamp_int64,
    dumps_json,
    normalize_coords,
    parse_coords_gsp,
    round_ts,
    sanitize_text,
    to_int,
    to_opt_int,
)
from spaceinvasion.db.repos._hot_refresh import refresh_colony_candidates_hot, refresh_galaxy_sectors_hot
from spaceinvasion.db.repos._message_ingest import RecycleReportInput
from spaceinvasion.db.repos._telemetry_rollups import refresh_loot_recycle
from spaceinvasion.db.repos.messages import MessagesRepository
from spaceinvasion.db.repos.raids import BattleReportInput, RaidsRepository


@dataclass(frozen=True)
class GameMessageBackfillUpdate:
    """Re-classified metadata update to apply to one `game_messages` row."""

    row_id: int
    category: str
    coords: str
    body_text: str
    parsed_json: str


@dataclass(frozen=True)
class SpyBackfillUpdate:
    """Structured spy-report field updates parsed from an archived `game_messages` row."""

    server_msg_id: str
    coords: str
    planet_name: str
    pig_iron: int
    metal: int
    kryptonite: int
    spice: int
    buildings_json: str
    fleet_json: str
    defense_json: str
    researches_json: str
    uninhabited_fields: int | None


def _apply_spy_backfill_updates(session: Session, spy_updates: Sequence[SpyBackfillUpdate]) -> None:
    for su in spy_updates:
        if not su.server_msg_id:
            continue
        pname = sanitize_text(su.planet_name, 80)
        pig = clamp_int64(max(0, su.pig_iron))
        met = clamp_int64(max(0, su.metal))
        kry = clamp_int64(max(0, su.kryptonite))
        spi = clamp_int64(max(0, su.spice))
        uninh = to_opt_int(su.uninhabited_fields)
        session.execute(
            update(SpyReportRecord)
            .where(SpyReportRecord.server_msg_id == su.server_msg_id)
            .values(
                planet_name=pname if pname else SpyReportRecord.planet_name,
                pig_iron=pig if pig > 0 else SpyReportRecord.pig_iron,
                metal=met if met > 0 else SpyReportRecord.metal,
                kryptonite=kry if kry > 0 else SpyReportRecord.kryptonite,
                spice=spi if spi > 0 else SpyReportRecord.spice,
                buildings_json=su.buildings_json if su.buildings_json != "{}" else SpyReportRecord.buildings_json,
                fleet_json=su.fleet_json if su.fleet_json != "{}" else SpyReportRecord.fleet_json,
                defense_json=su.defense_json if su.defense_json != "{}" else SpyReportRecord.defense_json,
                researches_json=su.researches_json if su.researches_json != "{}" else SpyReportRecord.researches_json,
                uninhabited_fields=func.coalesce(uninh, SpyReportRecord.uninhabited_fields)
                if uninh is not None
                else SpyReportRecord.uninhabited_fields,
            )
        )
        if su.coords and (pig + met + kry + spi) > 0:
            session.execute(
                update(SpyTarget)
                .where(SpyTarget.coords == su.coords)
                .values(
                    planet_name=case(
                        ((SpyTarget.planet_name == "") & (pname != ""), pname), else_=SpyTarget.planet_name
                    ),
                    pig_iron=case((SpyTarget.pig_iron == 0, pig), else_=SpyTarget.pig_iron),
                    metal=case((SpyTarget.metal == 0, met), else_=SpyTarget.metal),
                    kryptonite=case((SpyTarget.kryptonite == 0, kry), else_=SpyTarget.kryptonite),
                    spice=case((SpyTarget.spice == 0, spi), else_=SpyTarget.spice),
                )
            )


def apply_game_message_backfill(
    session: Session,
    *,
    max_id: int,
    msg_updates: Sequence[GameMessageBackfillUpdate],
    battle_inputs: Sequence[BattleReportInput],
    recycle_inputs: Sequence[RecycleReportInput],
    spy_updates: Sequence[SpyBackfillUpdate],
    ts: float | None = None,
) -> None:
    """Apply parsed backfill updates to `game_messages` and report tables and stamp `reports_backfill_v2`."""
    now_ts = round_ts(ts)
    for upd in msg_updates:
        session.execute(
            update(GameMessage)
            .where(GameMessage.id == upd.row_id)
            .values(
                category=upd.category,
                coords=upd.coords if upd.coords else GameMessage.coords,
                body_text=upd.body_text if upd.body_text else GameMessage.body_text,
                parsed_json=upd.parsed_json if upd.parsed_json != "{}" else GameMessage.parsed_json,
            )
        )
    if battle_inputs:
        RaidsRepository(session).upsert_battle_reports(battle_inputs, ts=now_ts)
    if recycle_inputs:
        MessagesRepository(session).upsert_recycle_reports(recycle_inputs, ts=now_ts)
    if spy_updates:
        _apply_spy_backfill_updates(session, spy_updates)
    if battle_inputs or recycle_inputs:
        refresh_loot_recycle(session, ts=now_ts)
    stmt = pg_insert(MetaEntry).values(key="reports_backfill_v2", value=str(max_id), updated_ts=now_ts)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[MetaEntry.key],
            set_={"value": stmt.excluded.value, "updated_ts": stmt.excluded.updated_ts},
        )
    )


def format_backfill_json(payload: object) -> str:
    """Serialize a backfill JSON mapping via the canonical DB JSON serializer."""
    return dumps_json(payload)


def rebuild_hot_tables_sql(session: Session, *, ts: float | None = None) -> dict[str, int]:
    """Idempotently rebuild `colony_candidates_hot` and `galaxy_sector_hot` from source tables."""
    now_ts = round_ts(ts)
    all_coords: set[str] = set()
    sectors: set[tuple[int, int]] = set()
    for raw_c in session.execute(select(GalaxySlot.coords)).scalars().all():
        if nc := normalize_coords(str(raw_c or "")):
            all_coords.add(nc)
            if (gsp := parse_coords_gsp(nc)) is not None and 1 <= gsp[0] <= 14 and 1 <= gsp[1] <= 400:
                sectors.add((gsp[0], gsp[1]))
    for raw_c in session.execute(select(SpyTarget.coords).where(SpyTarget.merged_into == "")).scalars().all():
        if nc := normalize_coords(str(raw_c or "")):
            all_coords.add(nc)
            if (gsp := parse_coords_gsp(nc)) is not None and 1 <= gsp[0] <= 14 and 1 <= gsp[1] <= 400:
                sectors.add((gsp[0], gsp[1]))
    for raw_c in session.execute(select(SpyReportRecord.target_coords).distinct()).scalars().all():
        if nc := normalize_coords(str(raw_c or "")):
            all_coords.add(nc)
    for g, s in session.execute(select(GalaxyScan.galaxy, GalaxyScan.system)).all():
        gi, si = to_int(g, 0), to_int(s, 0)
        if 1 <= gi <= 14 and 1 <= si <= 400:
            sectors.add((gi, si))
    if all_coords:
        refresh_colony_candidates_hot(session, sorted(all_coords), ts=now_ts)
    if sectors:
        refresh_galaxy_sectors_hot(session, sorted(sectors), ts=now_ts)
    emp_cnt = to_int(
        session.execute(
            select(func.count()).select_from(EmpirePlanetHot).where(EmpirePlanetHot.is_active == 1)
        ).scalar_one(),
        0,
    )
    sec_cnt = to_int(session.execute(select(func.count()).select_from(GalaxySectorHot)).scalar_one(), 0)
    col_cnt = to_int(
        session.execute(
            select(func.count()).select_from(ColonyCandidateHot).where(ColonyCandidateHot.is_active == 1)
        ).scalar_one(),
        0,
    )
    return {
        "empire_planets_hot": emp_cnt,
        "galaxy_sector_hot": sec_cnt,
        "colony_candidates_hot": col_cnt,
    }


__all__ = [
    "GameMessageBackfillUpdate",
    "SpyBackfillUpdate",
    "apply_game_message_backfill",
    "format_backfill_json",
    "rebuild_hot_tables_sql",
]
