"""Focused ORM read queries supporting `RuntimeDatabaseGateway` without leaking SQLAlchemy."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from spaceinvasion.core.catalog import cap_json_int
from spaceinvasion.db.dto import ActionHistoryRecord, SpyTargetRecord
from spaceinvasion.db.models import (
    ActionHistory,
    BattleReport,
    LootRecycleHourly,
    PlanetSnapshot,
    RaidHistory,
    RankSnapshot,
    RecycleReport,
    SpyReportRecord,
    SpyTarget,
)
from spaceinvasion.db.repos._helpers import (
    clamp_int64,
    clamp_limit,
    normalize_coords,
    sanitize_text,
    to_float,
    to_int,
)
from spaceinvasion.db.repos.intel import spy_target_to_dto


def query_last_throttle_timestamps(session: Session, *, building_id: int = 2) -> dict[int, float]:
    """Return `{planet_id: max_ts}` for executed live `THROTTLE_BUILDING` actions."""
    bid = clamp_int64(to_int(building_id, 2))
    rows = session.execute(
        select(ActionHistory.planet_id, func.max(ActionHistory.ts))
        .where(
            ActionHistory.kind == "THROTTLE_BUILDING",
            ActionHistory.executed == 1,
            ActionHistory.dry_run == 0,
            ActionHistory.planet_id.is_not(None),
            (ActionHistory.target_id == bid) | ActionHistory.target_id.is_(None),
        )
        .group_by(ActionHistory.planet_id)
    ).all()
    out: dict[int, float] = {}
    for row in rows:
        pid = cap_json_int(to_int(row[0], 0))
        if pid > 0:
            out[pid] = to_float(row[1], 0.0)
    return out


def query_executed_transport_actions(
    session: Session,
    *,
    since_ts: float,
    limit: int = 60,
) -> tuple[ActionHistoryRecord, ...]:
    """List recent executed live `TRANSPORT` actions chronologically for fallback cargo intel."""
    lim = clamp_limit(limit, 60, max_limit=200)
    rows = (
        session.execute(
            select(ActionHistory)
            .where(
                ActionHistory.kind == "TRANSPORT",
                ActionHistory.executed == 1,
                ActionHistory.dry_run == 0,
                ActionHistory.ts >= to_float(since_ts, 0.0),
            )
            .order_by(ActionHistory.ts.asc(), ActionHistory.id.asc())
            .limit(lim)
        )
        .scalars()
        .all()
    )
    return tuple(
        ActionHistoryRecord(
            id=to_int(r.id, 0),
            ts=to_float(r.ts, 0.0),
            source=r.source,
            command=r.command,
            priority=r.priority,
            kind=r.kind,
            planet_id=to_int(r.planet_id, 0) if r.planet_id is not None else None,
            planet_coords=r.planet_coords,
            target_id=to_int(r.target_id, 0) if r.target_id is not None else None,
            target_name=r.target_name,
            target_level=to_int(r.target_level, 0) if r.target_level is not None else None,
            executed=bool(r.executed),
            dry_run=bool(r.dry_run),
            reason=r.reason,
            message=r.message,
        )
        for r in rows
    )


def query_battle_debris_fallbacks(
    session: Session,
    missing_coords: Sequence[str],
) -> dict[str, tuple[int, int, int, int]]:
    """Read newest non-zero `(pig, metal, 0, 0)` debris amounts from `battle_reports`."""
    if not missing_coords:
        return {}
    missing_set = {nc for c in missing_coords if (nc := normalize_coords(c))}
    if not missing_set:
        return {}
    stmt = select(BattleReport.target_coords, BattleReport.debris_pig_iron, BattleReport.debris_metal).where(
        (BattleReport.debris_pig_iron > 0) | (BattleReport.debris_metal > 0)
    )
    if len(missing_set) <= 100:
        stmt = stmt.where(BattleReport.target_coords.in_(sorted(missing_set)))
    rows = session.execute(stmt.order_by(BattleReport.ts.desc(), BattleReport.id.desc())).all()
    out: dict[str, tuple[int, int, int, int]] = {}
    for row in rows:
        nc = normalize_coords(str(row[0] or ""))
        if nc and nc in missing_set and nc not in out:
            dp = cap_json_int(max(0, int(row[1] or 0)))
            dm = cap_json_int(max(0, int(row[2] or 0)))
            if dp + dm > 0:
                out[nc] = (dp, dm, 0, 0)
    return out


def query_recycle_debris_candidates(
    session: Session,
    missing_coords: Sequence[str],
) -> tuple[tuple[str, int, int, int, int, str], ...]:
    """Read newest `recycle_reports` debris rows as `(coords, avail_pig, avail_met, harv_pig, harv_met, raw_text)`."""
    if not missing_coords:
        return ()
    missing_set = {nc for c in missing_coords if (nc := normalize_coords(c))}
    if not missing_set:
        return ()
    stmt = select(
        RecycleReport.coords,
        RecycleReport.debris_avail_pig,
        RecycleReport.debris_avail_metal,
        RecycleReport.harvested_pig_iron,
        RecycleReport.harvested_metal,
        RecycleReport.raw_text,
    )
    if len(missing_set) <= 100:
        stmt = stmt.where(RecycleReport.coords.in_(sorted(missing_set)))
    rows = session.execute(stmt.order_by(RecycleReport.ts.desc(), RecycleReport.id.desc())).all()
    return tuple(
        (
            normalize_coords(str(r[0] or "")),
            max(0, int(r[1] or 0)),
            max(0, int(r[2] or 0)),
            max(0, int(r[3] or 0)),
            max(0, int(r[4] or 0)),
            str(r[5] or ""),
        )
        for r in rows
    )


def query_spy_research_observations(session: Session) -> tuple[tuple[str, str, str], ...]:
    """List chronological `(target_coords, owner, researches_json)` rows from `spy_reports`."""
    rows = session.execute(
        select(SpyReportRecord.target_coords, SpyReportRecord.owner, SpyReportRecord.researches_json)
        .where(SpyReportRecord.researches_json != "{}")
        .order_by(SpyReportRecord.ts.asc(), SpyReportRecord.id.asc())
    ).all()
    return tuple((normalize_coords(str(r[0] or "")), sanitize_text(r[1] or ""), str(r[2] or "{}")) for r in rows)


def query_latest_own_researches_and_nick(session: Session) -> tuple[str, str]:
    """Return `(latest_researches_json, latest_nonempty_nickname)` for research-max refresh."""
    res_json = (
        session.execute(
            select(PlanetSnapshot.researches_json).order_by(PlanetSnapshot.id.desc()).limit(1)
        ).scalar_one_or_none()
        or ""
    )
    nick = (
        session.execute(
            select(RankSnapshot.nickname).where(RankSnapshot.nickname != "").order_by(RankSnapshot.id.desc()).limit(1)
        ).scalar_one_or_none()
        or ""
    )
    return (str(res_json), sanitize_text(nick))


def query_active_blocked_targets(
    session: Session,
    *,
    now_ts: float,
) -> tuple[tuple[str, float, str, int, float], ...]:
    """Return `(coords, blocked_until_ts, block_reason, bash_hits, last_blocked_ts)` where active."""
    rows = session.execute(
        select(
            SpyTarget.coords,
            SpyTarget.blocked_until_ts,
            SpyTarget.block_reason,
            SpyTarget.bash_hits,
            SpyTarget.last_blocked_ts,
        )
        .where(SpyTarget.blocked_until_ts > float(now_ts))
        .order_by(SpyTarget.blocked_until_ts.desc(), SpyTarget.coords.asc())
    ).all()
    return tuple(
        (
            normalize_coords(str(r[0] or "")),
            to_float(r[1], 0.0),
            sanitize_text(r[2] or ""),
            cap_json_int(max(0, to_int(r[3], 0))),
            to_float(r[4], 0.0),
        )
        for r in rows
    )


def query_recent_raids_for_bash(
    session: Session,
    *,
    cutoff_ts: float,
) -> tuple[tuple[str, float, bool], ...]:
    """Return chronological `(target_coords, attacked_ts, is_exempt)` rows since `cutoff_ts`."""
    rows = session.execute(
        select(RaidHistory.target_coords, RaidHistory.attacked_ts, RaidHistory.is_exempt)
        .where(RaidHistory.attacked_ts >= to_float(cutoff_ts, 0.0))
        .order_by(RaidHistory.attacked_ts.asc(), RaidHistory.id.asc())
    ).all()
    return tuple((normalize_coords(str(r[0] or "")), to_float(r[1], 0.0), bool(r[2])) for r in rows)


def query_needs_loot_recycle_backfill(session: Session) -> bool:
    """Return True when `loot_recycle_hourly` is empty while battle or recycle reports exist."""
    has_hourly = session.execute(select(LootRecycleHourly.hour_bucket).limit(1)).first() is not None
    if has_hourly:
        return False
    has_battle = session.execute(select(BattleReport.id).where(BattleReport.ts > 0.0).limit(1)).first() is not None
    if has_battle:
        return True
    return session.execute(select(RecycleReport.id).where(RecycleReport.ts > 0.0).limit(1)).first() is not None


def query_active_debris_targets_raw(
    session: Session,
    *,
    include_own: bool = False,
    limit: int = 2000,
) -> tuple[SpyTargetRecord, ...]:
    """Return active `has_debris = 1` targets ordered newest-scanned first for debris-enriched ranking."""
    stmt = select(SpyTarget).where(SpyTarget.merged_into == "", SpyTarget.has_debris == 1)
    if not include_own:
        stmt = stmt.where(SpyTarget.is_own_planet == 0)
    rows = (
        session.execute(stmt.order_by(SpyTarget.last_scanned_ts.desc()).limit(clamp_limit(limit, 2000))).scalars().all()
    )
    return tuple(spy_target_to_dto(r) for r in rows)


__all__ = [
    "query_active_blocked_targets",
    "query_active_debris_targets_raw",
    "query_battle_debris_fallbacks",
    "query_executed_transport_actions",
    "query_last_throttle_timestamps",
    "query_latest_own_researches_and_nick",
    "query_needs_loot_recycle_backfill",
    "query_recent_raids_for_bash",
    "query_recycle_debris_candidates",
    "query_spy_research_observations",
]
