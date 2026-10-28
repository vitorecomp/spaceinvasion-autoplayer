"""SQLAlchemy 2.0 ORM repository for action, economy, production, loot, tutorial, and research telemetry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from spaceinvasion.db.dto import (
    ActionHistoryRecord,
    EconomyRatesRecord,
    HourlyProductionRecord,
    LootRecycleHourlyRecord,
    LootRecycleWindowSummary,
    TutorialProgressRecord,
    UniverseResearchMaxRecord,
)
from spaceinvasion.db.models import (
    ActionHistory,
    EconomyRatesCache,
    HourlyProductionHistory,
    TutorialProgress,
)
from spaceinvasion.db.repos._helpers import (
    affected_rows,
    clamp_int64,
    clamp_limit,
    dumps_json,
    loads_json_dict,
    round_ts,
    sanitize_text,
    to_float,
    to_int,
)
from spaceinvasion.db.repos._telemetry_rollups import (
    UniverseResearchMaxInput,
    bulk_upsert_research_max,
    list_research_max,
    read_loot_recycle_window,
    refresh_loot_recycle,
)


def _hourly_prod_to_dto(row: HourlyProductionHistory) -> HourlyProductionRecord:
    return HourlyProductionRecord(
        id=row.id,
        minute_bucket=to_int(row.minute_bucket, 0),
        recorded_at=round(to_float(row.recorded_at, 0.0), 3),
        pig_iron_per_hour=round(to_float(row.pig_iron_per_hour, 0.0), 1),
        metal_per_hour=round(to_float(row.metal_per_hour, 0.0), 1),
        kryptonite_per_hour=round(to_float(row.kryptonite_per_hour, 0.0), 1),
        spice_per_hour=round(to_float(row.spice_per_hour, 0.0), 1),
        total_per_hour=round(to_float(row.total_per_hour, 0.0), 1),
        speq_per_hour=round(to_float(row.speq_per_hour, 0.0), 1),
        planet_count=max(0, to_int(row.planet_count, 0)),
        active_builders=max(0, to_int(row.active_builders, 0)),
    )


def _action_to_dto(row: ActionHistory) -> ActionHistoryRecord:
    return ActionHistoryRecord(
        id=row.id,
        ts=to_float(row.ts, 0.0),
        source=row.source,
        command=row.command,
        priority=row.priority,
        kind=row.kind,
        planet_id=to_int(row.planet_id, 0) if row.planet_id is not None else None,
        planet_coords=row.planet_coords,
        target_id=to_int(row.target_id, 0) if row.target_id is not None else None,
        target_name=row.target_name,
        target_level=to_int(row.target_level, 0) if row.target_level is not None else None,
        executed=bool(row.executed),
        dry_run=bool(row.dry_run),
        reason=row.reason,
        message=row.message,
    )


class TelemetryRepository:
    """SQLAlchemy ORM repository over Wave A telemetry, rate cache, tutorial, and research tables."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_action(
        self,
        *,
        source: str,
        command: str,
        kind: str,
        executed: bool,
        dry_run: bool,
        message: str,
        priority: str | None = None,
        planet_id: int | None = None,
        planet_coords: str | None = None,
        target_id: int | None = None,
        target_name: str | None = None,
        target_level: int | None = None,
        reason: str = "",
        ts: float | None = None,
    ) -> ActionHistoryRecord:
        """Append one commander action audit record to `action_history`."""
        row = ActionHistory(
            ts=round_ts(ts),
            source=sanitize_text(source),
            command=sanitize_text(command),
            priority=sanitize_text(priority) if priority is not None else None,
            kind=sanitize_text(kind) or sanitize_text(command).upper(),
            planet_id=clamp_int64(int(planet_id)) if planet_id is not None else None,
            planet_coords=sanitize_text(planet_coords) if planet_coords is not None else None,
            target_id=clamp_int64(int(target_id)) if target_id is not None else None,
            target_name=sanitize_text(target_name) if target_name is not None else None,
            target_level=clamp_int64(int(target_level)) if target_level is not None else None,
            executed=1 if executed else 0,
            dry_run=1 if dry_run else 0,
            reason=sanitize_text(reason),
            message=sanitize_text(message),
        )
        self._session.add(row)
        self._session.flush()
        return _action_to_dto(row)

    def list_recent_actions(self, *, limit: int = 25) -> tuple[ActionHistoryRecord, ...]:
        """List recent commander actions ordered newest first (`id DESC`)."""
        lim = clamp_limit(limit, 25)
        rows = self._session.execute(select(ActionHistory).order_by(ActionHistory.id.desc()).limit(lim)).scalars().all()
        return tuple(_action_to_dto(r) for r in rows)

    def upsert_economy_rates(
        self,
        planet_id: int,
        coords: str,
        rates: Mapping[str, int],
        *,
        ts: float | None = None,
    ) -> EconomyRatesRecord | None:
        """Upsert cached economy throttle rates for one planet."""
        if not rates:
            return None
        now_ts = round_ts(ts)
        clean = {sanitize_text(k): clamp_int64(int(v)) for k, v in rates.items()}
        pid = clamp_int64(int(planet_id))
        stmt = pg_insert(EconomyRatesCache).values(
            planet_id=pid, coords=sanitize_text(coords), rates_json=dumps_json(clean), updated_ts=now_ts
        )
        self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[EconomyRatesCache.planet_id],
                set_={
                    "coords": stmt.excluded.coords,
                    "rates_json": stmt.excluded.rates_json,
                    "updated_ts": stmt.excluded.updated_ts,
                },
            )
        )
        return EconomyRatesRecord(planet_id=pid, coords=sanitize_text(coords), rates=clean, updated_ts=now_ts)

    def bulk_upsert_economy_rates(
        self,
        items: Sequence[tuple[int, str, Mapping[str, int]]],
        *,
        ts: float | None = None,
    ) -> int:
        """Bulk-upsert economy throttle rates for multiple planets in 1 PostgreSQL statement."""
        now_ts = round_ts(ts)
        payload = [
            {
                "planet_id": clamp_int64(int(pid)),
                "coords": sanitize_text(coords),
                "rates_json": dumps_json({sanitize_text(k): clamp_int64(int(v)) for k, v in rates.items()}),
                "updated_ts": now_ts,
            }
            for pid, coords, rates in items
            if rates
        ]
        if not payload:
            return 0
        stmt = pg_insert(EconomyRatesCache).values(payload)
        self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[EconomyRatesCache.planet_id],
                set_={
                    "coords": stmt.excluded.coords,
                    "rates_json": stmt.excluded.rates_json,
                    "updated_ts": stmt.excluded.updated_ts,
                },
            )
        )
        return len(payload)

    def get_all_economy_rates(self) -> dict[int, EconomyRatesRecord]:
        """Load all cached planet economy rate maps in one query."""
        rows = self._session.execute(select(EconomyRatesCache)).scalars().all()
        out: dict[int, EconomyRatesRecord] = {}
        for r in rows:
            parsed = loads_json_dict(r.rates_json)
            clean: dict[str, int] = {
                k: int(v) for k, v in parsed.items() if isinstance(v, int) and not isinstance(v, bool)
            }
            if not clean:
                continue
            out[to_int(r.planet_id, 0)] = EconomyRatesRecord(
                planet_id=to_int(r.planet_id, 0), coords=r.coords, rates=clean, updated_ts=to_float(r.updated_ts, 0.0)
            )
        return out

    def record_hourly_production(
        self,
        *,
        pig_iron_per_hour: float = 0.0,
        metal_per_hour: float = 0.0,
        kryptonite_per_hour: float = 0.0,
        spice_per_hour: float = 0.0,
        total_per_hour: float = 0.0,
        speq_per_hour: float = 0.0,
        planet_count: int = 0,
        active_builders: int = 0,
        ts: float | None = None,
    ) -> tuple[HourlyProductionRecord, bool]:
        """Insert at most one empire production sample per minute bucket (`ON CONFLICT DO NOTHING`)."""
        eff_now = round_ts(ts)
        minute_bucket = int(eff_now // 60) * 60
        pig_v = round(to_float(pig_iron_per_hour, 0.0), 1)
        met_v = round(max(0.0, to_float(metal_per_hour, 0.0)), 1)
        kry_v = round(max(0.0, to_float(kryptonite_per_hour, 0.0)), 1)
        spi_v = round(max(0.0, to_float(spice_per_hour, 0.0)), 1)
        tot_v = round(max(0.0, to_float(total_per_hour, 0.0)), 1)
        speq_v = round(max(0.0, to_float(speq_per_hour, 0.0)), 1)
        p_cnt, b_cnt = max(0, to_int(planet_count, 0)), max(0, to_int(active_builders, 0))
        ins = (
            pg_insert(HourlyProductionHistory)
            .values(
                minute_bucket=minute_bucket,
                recorded_at=eff_now,
                pig_iron_per_hour=pig_v,
                metal_per_hour=met_v,
                kryptonite_per_hour=kry_v,
                spice_per_hour=spi_v,
                total_per_hour=tot_v,
                speq_per_hour=speq_v,
                planet_count=p_cnt,
                active_builders=b_cnt,
            )
            .on_conflict_do_nothing(index_elements=[HourlyProductionHistory.minute_bucket])
            .returning(HourlyProductionHistory)
        )
        inserted_row = self._session.execute(ins).scalar_one_or_none()
        if inserted_row is not None:
            return _hourly_prod_to_dto(inserted_row), True
        return (
            HourlyProductionRecord(0, minute_bucket, eff_now, pig_v, met_v, kry_v, spi_v, tot_v, speq_v, p_cnt, b_cnt),
            False,
        )

    def list_hourly_production(self, *, limit: int = 120) -> tuple[HourlyProductionRecord, ...]:
        """Return chronological (oldest -> newest) snapshots from the newest `limit` minute buckets."""
        lim = clamp_limit(limit, 120, max_limit=1440)
        rows = (
            self._session.execute(
                select(HourlyProductionHistory).order_by(HourlyProductionHistory.minute_bucket.desc()).limit(lim)
            )
            .scalars()
            .all()
        )
        return tuple(_hourly_prod_to_dto(r) for r in reversed(rows))

    def record_tutorial_progress(
        self,
        *,
        step: int,
        total_steps: int,
        tasks: Sequence[Mapping[str, object]],
        reward_text: str,
        can_complete: bool,
        ts: float | None = None,
    ) -> TutorialProgressRecord:
        """Mark prior tutorial steps completed and upsert the current step in 2 SQL statements."""
        now_ts = round_ts(ts)
        step_i, total_i = clamp_int64(int(step)), clamp_int64(int(total_steps))
        self._session.execute(
            update(TutorialProgress)
            .where(TutorialProgress.step < step_i, TutorialProgress.completed_ts.is_(None))
            .values(completed_ts=func.coalesce(TutorialProgress.completed_ts, now_ts))
        )
        base_stmt = pg_insert(TutorialProgress).values(
            step=step_i,
            total_steps=total_i,
            tasks_json=dumps_json([dict(t) for t in tasks]),
            reward_text=sanitize_text(reward_text),
            can_complete=1 if can_complete else 0,
            first_seen_ts=now_ts,
            last_seen_ts=now_ts,
            completed_ts=None,
        )
        upsert_stmt = base_stmt.on_conflict_do_update(
            index_elements=[TutorialProgress.step],
            set_={
                "total_steps": base_stmt.excluded.total_steps,
                "tasks_json": base_stmt.excluded.tasks_json,
                "reward_text": base_stmt.excluded.reward_text,
                "can_complete": base_stmt.excluded.can_complete,
                "last_seen_ts": base_stmt.excluded.last_seen_ts,
            },
        ).returning(TutorialProgress)
        row: TutorialProgress = self._session.execute(upsert_stmt).scalar_one()
        return TutorialProgressRecord(
            step=to_int(row.step, 0),
            total_steps=to_int(row.total_steps, 0),
            tasks_json=row.tasks_json,
            reward_text=row.reward_text,
            can_complete=bool(row.can_complete),
            first_seen_ts=to_float(row.first_seen_ts, 0.0),
            last_seen_ts=to_float(row.last_seen_ts, 0.0),
            completed_ts=to_float(row.completed_ts, 0.0) if row.completed_ts is not None else None,
        )

    def complete_open_tutorial_steps(self, *, ts: float | None = None) -> int:
        """Mark all currently incomplete tutorial steps completed when tutorial disappears."""
        now_ts = round_ts(ts)
        res = self._session.execute(
            update(TutorialProgress)
            .where(TutorialProgress.completed_ts.is_(None))
            .values(completed_ts=func.coalesce(TutorialProgress.completed_ts, now_ts))
        )
        return affected_rows(res)

    def list_tutorial_progress(self) -> tuple[TutorialProgressRecord, ...]:
        """List observed tutorial progress steps ordered ascending by step."""
        rows = self._session.execute(select(TutorialProgress).order_by(TutorialProgress.step.asc())).scalars().all()
        return tuple(
            TutorialProgressRecord(
                step=to_int(r.step, 0),
                total_steps=to_int(r.total_steps, 0),
                tasks_json=r.tasks_json,
                reward_text=r.reward_text,
                can_complete=bool(r.can_complete),
                first_seen_ts=to_float(r.first_seen_ts, 0.0),
                last_seen_ts=to_float(r.last_seen_ts, 0.0),
                completed_ts=to_float(r.completed_ts, 0.0) if r.completed_ts is not None else None,
            )
            for r in rows
        )

    def refresh_loot_recycle_hourly(self, *, ts: float | None = None) -> int:
        """Aggregate `battle_reports` and `recycle_reports` into `loot_recycle_hourly`."""
        return refresh_loot_recycle(self._session, ts=ts)

    def get_loot_recycle_hourly(
        self, *, hours: int = 24, now_ts: float | None = None
    ) -> tuple[tuple[LootRecycleHourlyRecord, ...], LootRecycleWindowSummary]:
        """Return continuous hourly loot/recycle buckets and window summary."""
        return read_loot_recycle_window(self._session, hours=hours, now_ts=now_ts)

    def bulk_upsert_universe_research_max(
        self,
        entries: Sequence[UniverseResearchMaxInput],
        *,
        source: str = "refresh",
        append_history: bool = True,
        ts: float | None = None,
    ) -> tuple[UniverseResearchMaxRecord, ...]:
        """Bulk-upsert `universe_research_max` monotonically in 1 SQL statement."""
        return bulk_upsert_research_max(self._session, entries, source=source, append_history=append_history, ts=ts)

    def list_universe_research_max(self) -> tuple[UniverseResearchMaxRecord, ...]:
        """List persisted universe research maxima ordered by `(research_id, slug)`."""
        return list_research_max(self._session)


__all__ = ["TelemetryRepository", "UniverseResearchMaxInput"]
