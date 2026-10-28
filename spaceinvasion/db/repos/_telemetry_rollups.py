"""SQLAlchemy rollup helpers for `loot_recycle_hourly` and `universe_research_max`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import BigInteger, case, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from spaceinvasion.db.dto import (
    LootRecycleHourlyRecord,
    LootRecycleWindowSummary,
    UniverseResearchMaxRecord,
)
from spaceinvasion.db.models import (
    BattleReport,
    LootRecycleHourly,
    RecycleReport,
    UniverseResearchMax,
    UniverseResearchMaxHistory,
)
from spaceinvasion.db.repos._helpers import (
    clamp_int64,
    dumps_json,
    is_non_recycle_notice_text,
    normalize_coords,
    round_ts,
    sanitize_text,
    to_float,
    to_int,
)


@dataclass(frozen=True)
class UniverseResearchMaxInput:
    """Typed input row for bulk `universe_research_max` upserts."""

    research_key: str
    research_id: int
    slug: str
    name: str
    own_level: int = 0
    spied_max_level: int = 0
    server_record_level: int | None = None
    universe_max_level: int = 0
    catalog_max_level: int | None = None
    holder: str = ""
    holder_coords: str = ""


def _research_to_dto(row: UniverseResearchMax) -> UniverseResearchMaxRecord:
    own_l = max(0, to_int(row.own_level, 0))
    univ_l = max(own_l, to_int(row.universe_max_level, 0))
    return UniverseResearchMaxRecord(
        research_key=row.research_key,
        research_id=to_int(row.research_id, 0),
        slug=row.slug,
        name=row.name,
        own_level=own_l,
        spied_max_level=max(0, to_int(row.spied_max_level, 0)),
        server_record_level=to_int(row.server_record_level, 0) if row.server_record_level is not None else None,
        universe_max_level=univ_l,
        catalog_max_level=to_int(row.catalog_max_level, 0) if row.catalog_max_level is not None else None,
        gap_to_max=max(0, univ_l - own_l),
        holder=row.holder,
        holder_coords=row.holder_coords,
        updated_ts=to_float(row.updated_ts, 0.0),
    )


def _loot_bucket_dto(hb: int, row: LootRecycleHourly | None) -> LootRecycleHourlyRecord:
    if row is None:
        return LootRecycleHourlyRecord(hb, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0)
    return LootRecycleHourlyRecord(
        hour_bucket=hb,
        stolen_pig_iron=round(max(0.0, to_float(row.stolen_pig_iron, 0.0)), 1),
        stolen_metal=round(max(0.0, to_float(row.stolen_metal, 0.0)), 1),
        stolen_kryptonite=round(max(0.0, to_float(row.stolen_kryptonite, 0.0)), 1),
        stolen_spice=round(max(0.0, to_float(row.stolen_spice, 0.0)), 1),
        stolen_total=round(max(0.0, to_float(row.stolen_total, 0.0)), 1),
        recycled_pig_iron=round(max(0.0, to_float(row.recycled_pig_iron, 0.0)), 1),
        recycled_metal=round(max(0.0, to_float(row.recycled_metal, 0.0)), 1),
        recycled_kryptonite=round(max(0.0, to_float(row.recycled_kryptonite, 0.0)), 1),
        recycled_spice=round(max(0.0, to_float(row.recycled_spice, 0.0)), 1),
        recycled_total=round(max(0.0, to_float(row.recycled_total, 0.0)), 1),
        attack_count=max(0, to_int(row.attack_count, 0)),
        recycle_count=max(0, to_int(row.recycle_count, 0)),
        updated_at=round(to_float(row.updated_at, 0.0), 3),
    )


def refresh_loot_recycle(session: Session, *, ts: float | None = None) -> int:
    """Aggregate `battle_reports` and `recycle_reports` in SQL and bulk-upsert `loot_recycle_hourly`."""
    now_ts = round_ts(ts)
    b_hb = cast(func.floor(BattleReport.ts / 3600.0), BigInteger) * 3600
    b_rows = session.execute(
        select(
            b_hb,
            func.coalesce(func.sum(case((BattleReport.pig_iron > 0, BattleReport.pig_iron), else_=0)), 0),
            func.coalesce(func.sum(case((BattleReport.metal > 0, BattleReport.metal), else_=0)), 0),
            func.coalesce(func.sum(case((BattleReport.kryptonite > 0, BattleReport.kryptonite), else_=0)), 0),
            func.coalesce(func.sum(case((BattleReport.spice > 0, BattleReport.spice), else_=0)), 0),
            func.count(),
        )
        .where(BattleReport.ts > 0)
        .group_by(b_hb)
    ).all()

    r_rows = session.execute(
        select(
            RecycleReport.ts,
            RecycleReport.harvested_pig_iron,
            RecycleReport.harvested_metal,
            RecycleReport.harvested_kryptonite,
            RecycleReport.harvested_spice,
            RecycleReport.raw_text,
        ).where(RecycleReport.ts > 0)
    ).all()

    buckets: dict[int, list[float]] = {}
    for row in b_rows:
        hb = to_int(row[0], 0)
        if hb <= 0:
            continue
        acc = buckets.setdefault(hb, [0.0] * 10)
        for idx in range(4):
            acc[idx] += max(0.0, to_float(row[idx + 1], 0.0))
        acc[8] += float(max(0, to_int(row[5], 0)))
    for row in r_rows:
        r_ts = to_float(row[0], 0.0)
        if r_ts <= 0.0 or is_non_recycle_notice_text(str(row[5] or "")):
            continue
        hb = int(r_ts // 3600.0) * 3600
        if hb <= 0:
            continue
        acc = buckets.setdefault(hb, [0.0] * 10)
        for idx in range(4):
            acc[4 + idx] += max(0.0, to_float(row[idx + 1], 0.0))
        acc[9] += 1.0

    if not buckets:
        return 0
    payload = [
        {
            "hour_bucket": hb,
            "stolen_pig_iron": (sp := round(acc[0], 3)),
            "stolen_metal": (sm := round(acc[1], 3)),
            "stolen_kryptonite": (sk := round(acc[2], 3)),
            "stolen_spice": (ss := round(acc[3], 3)),
            "stolen_total": round(sp + sm + sk + ss, 3),
            "recycled_pig_iron": (rp := round(acc[4], 3)),
            "recycled_metal": (rm := round(acc[5], 3)),
            "recycled_kryptonite": (rk := round(acc[6], 3)),
            "recycled_spice": (rs := round(acc[7], 3)),
            "recycled_total": round(rp + rm + rk + rs, 3),
            "attack_count": max(0, int(round(acc[8]))),
            "recycle_count": max(0, int(round(acc[9]))),
            "updated_at": now_ts,
        }
        for hb, acc in sorted(buckets.items())
    ]
    stmt = pg_insert(LootRecycleHourly).values(payload)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[LootRecycleHourly.hour_bucket],
            set_={k: getattr(stmt.excluded, k) for k in payload[0] if k != "hour_bucket"},
        )
    )
    return len(payload)


def read_loot_recycle_window(
    session: Session,
    *,
    hours: int = 24,
    now_ts: float | None = None,
) -> tuple[tuple[LootRecycleHourlyRecord, ...], LootRecycleWindowSummary]:
    """Return continuous chronological hourly loot/recycle buckets and window summary."""
    eff_now = round_ts(now_ts)
    win_hours = max(1, min(168, int(hours)))
    end_bucket = int(eff_now // 3600) * 3600
    start_bucket = end_bucket - (win_hours - 1) * 3600
    rows = (
        session.execute(
            select(LootRecycleHourly)
            .where(LootRecycleHourly.hour_bucket >= start_bucket, LootRecycleHourly.hour_bucket <= end_bucket)
            .order_by(LootRecycleHourly.hour_bucket.asc())
        )
        .scalars()
        .all()
    )
    by_bucket = {int(r.hour_bucket): r for r in rows}
    history = tuple(_loot_bucket_dto(hb, by_bucket.get(hb)) for hb in range(start_bucket, end_bucket + 3600, 3600))
    tot_stolen = round(sum(item.stolen_total for item in history), 1)
    tot_recycled = round(sum(item.recycled_total for item in history), 1)
    summary = LootRecycleWindowSummary(
        total_stolen=tot_stolen,
        total_recycled=tot_recycled,
        grand_total=round(tot_stolen + tot_recycled, 1),
        attack_count=sum(item.attack_count for item in history),
        recycle_count=sum(item.recycle_count for item in history),
        window_hours=win_hours,
        stolen_pig_iron=round(sum(item.stolen_pig_iron for item in history), 1),
        stolen_metal=round(sum(item.stolen_metal for item in history), 1),
        stolen_kryptonite=round(sum(item.stolen_kryptonite for item in history), 1),
        stolen_spice=round(sum(item.stolen_spice for item in history), 1),
        recycled_pig_iron=round(sum(item.recycled_pig_iron for item in history), 1),
        recycled_metal=round(sum(item.recycled_metal for item in history), 1),
        recycled_kryptonite=round(sum(item.recycled_kryptonite for item in history), 1),
        recycled_spice=round(sum(item.recycled_spice for item in history), 1),
    )
    return history, summary


def bulk_upsert_research_max(
    session: Session,
    entries: Sequence[UniverseResearchMaxInput],
    *,
    source: str = "refresh",
    append_history: bool = True,
    ts: float | None = None,
) -> tuple[UniverseResearchMaxRecord, ...]:
    """Bulk-upsert `universe_research_max` monotonically in 1 SQL statement (+1 history insert)."""
    if not entries:
        return ()
    now_ts = round_ts(ts)
    payload = [
        {
            "research_key": sanitize_text(e.research_key),
            "research_id": clamp_int64(int(e.research_id)),
            "slug": sanitize_text(e.slug),
            "name": sanitize_text(e.name),
            "own_level": max(0, clamp_int64(int(e.own_level))),
            "spied_max_level": max(0, clamp_int64(int(e.spied_max_level))),
            "server_record_level": clamp_int64(int(e.server_record_level))
            if e.server_record_level is not None
            else None,
            "universe_max_level": max(0, clamp_int64(int(e.universe_max_level))),
            "catalog_max_level": clamp_int64(int(e.catalog_max_level)) if e.catalog_max_level is not None else None,
            "holder": sanitize_text(e.holder, 80),
            "holder_coords": normalize_coords(e.holder_coords)[:24] if e.holder_coords else "",
            "updated_ts": now_ts,
        }
        for e in entries
    ]
    base_stmt = pg_insert(UniverseResearchMax).values(payload)
    upsert_stmt = base_stmt.on_conflict_do_update(
        index_elements=[UniverseResearchMax.research_key],
        set_={
            "research_id": base_stmt.excluded.research_id,
            "slug": base_stmt.excluded.slug,
            "name": base_stmt.excluded.name,
            "own_level": base_stmt.excluded.own_level,
            "spied_max_level": func.greatest(UniverseResearchMax.spied_max_level, base_stmt.excluded.spied_max_level),
            "server_record_level": case(
                (base_stmt.excluded.server_record_level.is_(None), UniverseResearchMax.server_record_level),
                (UniverseResearchMax.server_record_level.is_(None), base_stmt.excluded.server_record_level),
                else_=func.greatest(UniverseResearchMax.server_record_level, base_stmt.excluded.server_record_level),
            ),
            "universe_max_level": func.greatest(
                UniverseResearchMax.universe_max_level, base_stmt.excluded.universe_max_level
            ),
            "catalog_max_level": base_stmt.excluded.catalog_max_level,
            "holder": case(
                (base_stmt.excluded.holder != "", base_stmt.excluded.holder), else_=UniverseResearchMax.holder
            ),
            "holder_coords": case(
                (base_stmt.excluded.holder != "", base_stmt.excluded.holder_coords),
                else_=UniverseResearchMax.holder_coords,
            ),
            "updated_ts": base_stmt.excluded.updated_ts,
        },
    ).returning(UniverseResearchMax)
    rows: list[UniverseResearchMax] = list(session.execute(upsert_stmt).scalars().all())
    rows.sort(key=lambda r: (int(r.research_id), r.slug))
    dtos = tuple(_research_to_dto(r) for r in rows)
    if append_history and source != "auto_seed":
        clean_src = sanitize_text(source or "refresh", 32) or "refresh"
        history_payload = [
            {
                "research_id": d.research_id,
                "key": d.research_key,
                "slug": d.slug,
                "name": d.name,
                "own_level": d.own_level,
                "universe_max_level": d.universe_max_level,
                "spied_max_level": d.spied_max_level,
                "server_record_level": d.server_record_level,
                "catalog_max_level": d.catalog_max_level,
                "gap_to_max": d.gap_to_max,
                "holder": d.holder,
                "holder_coords": d.holder_coords,
                "updated_ts": d.updated_ts,
            }
            for d in dtos
        ]
        session.add(UniverseResearchMaxHistory(ts=now_ts, source=clean_src, records_json=dumps_json(history_payload)))
        session.flush()
    return dtos


def list_research_max(session: Session) -> tuple[UniverseResearchMaxRecord, ...]:
    """List persisted universe research maxima ordered by `(research_id, slug)`."""
    rows = (
        session.execute(
            select(UniverseResearchMax).order_by(UniverseResearchMax.research_id.asc(), UniverseResearchMax.slug.asc())
        )
        .scalars()
        .all()
    )
    return tuple(_research_to_dto(r) for r in rows)


__all__ = [
    "UniverseResearchMaxInput",
    "bulk_upsert_research_max",
    "list_research_max",
    "read_loot_recycle_window",
    "refresh_loot_recycle",
]
