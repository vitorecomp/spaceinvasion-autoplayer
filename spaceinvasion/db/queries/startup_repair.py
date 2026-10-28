"""SQLAlchemy startup maintenance queries: `spy_targets` repair and `game_messages` report backfill."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import case, func, or_, select, update
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
    PlanetSnapshot,
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
    to_float,
    to_int,
    to_opt_int,
)
from spaceinvasion.db.repos._hot_refresh import refresh_colony_candidates_hot, refresh_galaxy_sectors_hot
from spaceinvasion.db.repos._message_ingest import RecycleReportInput
from spaceinvasion.db.repos._telemetry_rollups import refresh_loot_recycle
from spaceinvasion.db.repos.messages import MessagesRepository
from spaceinvasion.db.repos.raids import BattleReportInput, RaidsRepository

_POISON_PATTERNS: tuple[str, ...] = (
    "%fleet return%",
    "%flottenrückkehr%",
    "%flottenrueckkehr%",
    "%returning from duty%",
    "%were unloaded%",
    "%the assignment was to:%",
)
_RECONCILE_STATUSES: tuple[str, ...] = ("free", "blue", "green", "white", "dark_red")


@dataclass(frozen=True)
class GameMessageBackfillRow:
    """Minimal `game_messages` row returned for non-destructive parser backfill."""

    id: int
    ingested_ts: float
    msg_uid: str
    server_msg_id: str
    folder_type: str
    category: str
    msg_date_text: str
    sender: str
    subject: str
    coords: str
    body_text: str
    raw_html: str


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


def _restore_poisoned_target(
    session: Session,
    p_coords: str,
    *,
    is_non_spy_fn: Callable[[str], bool],
) -> bool:
    rep_rows = (
        session.execute(
            select(SpyReportRecord)
            .where(SpyReportRecord.target_coords == p_coords)
            .order_by(SpyReportRecord.ts.desc(), SpyReportRecord.id.desc())
        )
        .scalars()
        .all()
    )
    if not rep_rows or not is_non_spy_fn(str(rep_rows[0].raw_text or "")):
        return False
    valid_rep = next((rr for rr in rep_rows if not is_non_spy_fn(str(rr.raw_text or ""))), None)
    gs_row = session.get(GalaxySlot, p_coords)
    if valid_rep is not None:
        v_is_spacia = 1 if (gs_row.is_spacia if gs_row is not None else valid_rep.is_spacia) else 0
        v_status = (
            sanitize_text(gs_row.status_color) if gs_row is not None else ("yellow" if v_is_spacia else "light_red")
        )
        v_inactive = (
            (1 if gs_row.is_inactive else 0)
            if gs_row is not None
            else (1 if v_is_spacia or v_status == "yellow" else 0)
        )
        v_scanned = max(
            to_float(gs_row.last_scanned_ts, 0.0) if gs_row is not None else 0.0,
            to_float(valid_rep.ts, 0.0),
        )
        session.execute(
            update(SpyTarget)
            .where(SpyTarget.coords == p_coords)
            .values(
                owner=sanitize_text(gs_row.owner) if gs_row is not None else sanitize_text(valid_rep.owner),
                planet_name=sanitize_text(gs_row.planet_name)
                if gs_row is not None
                else sanitize_text(valid_rep.planet_name),
                status_color=v_status,
                is_inactive=v_inactive,
                is_spacia=v_is_spacia,
                has_debris=(1 if gs_row.has_debris else 0) if gs_row is not None else 0,
                pig_iron=clamp_int64(to_int(valid_rep.pig_iron, 0)),
                metal=clamp_int64(to_int(valid_rep.metal, 0)),
                kryptonite=clamp_int64(to_int(valid_rep.kryptonite, 0)),
                spice=clamp_int64(to_int(valid_rep.spice, 0)),
                fleet_json=str(valid_rep.fleet_json or "{}"),
                defense_json=str(valid_rep.defense_json or "{}"),
                dug_in=1 if valid_rep.dug_in else 0,
                last_scanned_ts=v_scanned,
                last_spied_ts=to_float(valid_rep.ts, 0.0),
            )
        )
        return True
    r_status = sanitize_text(gs_row.status_color) if gs_row is not None else "free"
    r_scanned = to_float(gs_row.last_scanned_ts, 0.0) if gs_row is not None else 0.0
    clear_blk = r_status == "free"
    session.execute(
        update(SpyTarget)
        .where(SpyTarget.coords == p_coords)
        .values(
            owner=sanitize_text(gs_row.owner) if gs_row is not None else "",
            planet_name=sanitize_text(gs_row.planet_name) if gs_row is not None else "",
            status_color=r_status,
            is_inactive=(1 if gs_row.is_inactive else 0) if gs_row is not None else 0,
            is_spacia=(1 if gs_row.is_spacia else 0) if gs_row is not None else 0,
            has_debris=(1 if gs_row.has_debris else 0) if gs_row is not None else 0,
            pig_iron=0,
            metal=0,
            kryptonite=0,
            spice=0,
            fleet_json="{}",
            defense_json="{}",
            dug_in=0,
            last_scanned_ts=r_scanned if r_scanned > 0.0 else SpyTarget.last_scanned_ts,
            last_spied_ts=0.0,
            blocked_until_ts=0.0 if clear_blk else SpyTarget.blocked_until_ts,
            block_reason="" if clear_blk else SpyTarget.block_reason,
        )
    )
    return True


def _reconcile_spy_targets_from_galaxy_slots(session: Session) -> list[str]:
    rows = session.execute(
        select(
            SpyTarget.coords,
            GalaxySlot.owner,
            GalaxySlot.planet_name,
            GalaxySlot.status_color,
            GalaxySlot.is_inactive,
            GalaxySlot.is_spacia,
            GalaxySlot.has_debris,
        )
        .join(GalaxySlot, GalaxySlot.coords == SpyTarget.coords)
        .where(
            SpyTarget.is_own_planet == 0,
            GalaxySlot.last_scanned_ts >= SpyTarget.last_scanned_ts,
            GalaxySlot.status_color.in_(_RECONCILE_STATUSES),
            or_(
                SpyTarget.status_color != GalaxySlot.status_color,
                SpyTarget.owner != GalaxySlot.owner,
                SpyTarget.planet_name != GalaxySlot.planet_name,
                SpyTarget.is_inactive != GalaxySlot.is_inactive,
                SpyTarget.is_spacia != GalaxySlot.is_spacia,
                SpyTarget.has_debris != GalaxySlot.has_debris,
            ),
        )
    ).all()
    touched: list[str] = []
    for r in rows:
        coords = str(r[0] or "")
        if not coords:
            continue
        session.execute(
            update(SpyTarget)
            .where(SpyTarget.coords == coords)
            .values(
                owner=str(r[1] or ""),
                planet_name=str(r[2] or ""),
                status_color=str(r[3] or "free"),
                is_inactive=to_int(r[4], 0),
                is_spacia=to_int(r[5], 0),
                has_debris=to_int(r[6], 0),
            )
        )
        touched.append(coords)
    return touched


def repair_spy_targets_sql(
    session: Session,
    *,
    is_non_spy_fn: Callable[[str], bool],
    ts: float | None = None,
) -> int:
    """Repair empty yellow slots, stale blocks, poisoned non-spy targets, and hot-table caches."""
    touched: set[str] = set()
    r1 = (
        session.execute(
            update(SpyTarget)
            .where(
                SpyTarget.owner == "",
                SpyTarget.planet_name == "",
                SpyTarget.is_spacia == 0,
                SpyTarget.status_color == "yellow",
            )
            .values(status_color="free", is_inactive=0)
            .returning(SpyTarget.coords)
        )
        .scalars()
        .all()
    )
    touched.update(str(c) for c in r1 if c)

    r2 = (
        session.execute(
            update(SpyTarget)
            .where(
                SpyTarget.status_color == "free",
                SpyTarget.owner == "",
                SpyTarget.planet_name == "",
                SpyTarget.is_spacia == 0,
                SpyTarget.is_own_planet == 0,
                or_(
                    func.lower(SpyTarget.block_reason).like("%can't be attacked%"),
                    func.lower(SpyTarget.block_reason).like("%cannot be attacked%"),
                    func.lower(SpyTarget.block_reason).like("%can not be attacked%"),
                ),
            )
            .values(blocked_until_ts=0.0, block_reason="")
            .returning(SpyTarget.coords)
        )
        .scalars()
        .all()
    )
    touched.update(str(c) for c in r2 if c)

    poisoned = (
        session.execute(
            select(SpyTarget.coords)
            .distinct()
            .join(SpyReportRecord, SpyReportRecord.target_coords == SpyTarget.coords)
            .where(
                SpyTarget.is_own_planet == 0,
                or_(*(func.lower(SpyReportRecord.raw_text).like(pat) for pat in _POISON_PATTERNS)),
            )
        )
        .scalars()
        .all()
    )
    for raw_c in poisoned:
        p_coords = str(raw_c or "").strip()
        if p_coords and _restore_poisoned_target(session, p_coords, is_non_spy_fn=is_non_spy_fn):
            touched.add(p_coords)

    touched.update(_reconcile_spy_targets_from_galaxy_slots(session))
    if touched:
        now_ts = round_ts(ts)
        refresh_colony_candidates_hot(session, sorted(touched), ts=now_ts)
        sectors = sorted(
            {
                (gsp[0], gsp[1])
                for c in touched
                if (gsp := parse_coords_gsp(c)) is not None and 1 <= gsp[0] <= 14 and 1 <= gsp[1] <= 400
            }
        )
        if sectors:
            refresh_galaxy_sectors_hot(session, sectors, ts=now_ts)
    return len(touched)


def load_game_messages_for_backfill(
    session: Session,
) -> tuple[int, tuple[GameMessageBackfillRow, ...], frozenset[str]]:
    """Return `(max_id, messages, own_coords)` if `reports_backfill_v2` is behind `MAX(game_messages.id)`."""
    max_id = to_int(session.execute(select(func.coalesce(func.max(GameMessage.id), 0))).scalar_one(), 0)
    if max_id <= 0:
        return 0, (), frozenset()
    meta_row = session.get(MetaEntry, "reports_backfill_v2")
    if meta_row is not None and str(meta_row.value) == str(max_id):
        return 0, (), frozenset()

    own_coords: set[str] = set()
    for raw_c in session.execute(select(PlanetSnapshot.coords).distinct()).scalars().all():
        if nc := normalize_coords(str(raw_c or "")):
            own_coords.add(nc)
    for raw_c in session.execute(select(SpyTarget.coords).where(SpyTarget.is_own_planet == 1)).scalars().all():
        if nc := normalize_coords(str(raw_c or "")):
            own_coords.add(nc)

    msg_rows = (
        session.execute(select(GameMessage).where(~GameMessage.msg_uid.like("id:msg-%")).order_by(GameMessage.id.asc()))
        .scalars()
        .all()
    )
    items = tuple(
        GameMessageBackfillRow(
            id=to_int(r.id, 0),
            ingested_ts=to_float(r.ingested_ts, 0.0),
            msg_uid=str(r.msg_uid or ""),
            server_msg_id=str(r.server_msg_id or ""),
            folder_type=str(r.folder_type or ""),
            category=str(r.category or ""),
            msg_date_text=str(r.msg_date_text or ""),
            sender=str(r.sender or ""),
            subject=str(r.subject or ""),
            coords=str(r.coords or ""),
            body_text=str(r.body_text or ""),
            raw_html=str(r.raw_html or ""),
        )
        for r in msg_rows
    )
    return max_id, items, frozenset(own_coords)


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
    "GameMessageBackfillRow",
    "GameMessageBackfillUpdate",
    "SpyBackfillUpdate",
    "apply_game_message_backfill",
    "format_backfill_json",
    "load_game_messages_for_backfill",
    "rebuild_hot_tables_sql",
    "repair_spy_targets_sql",
]
