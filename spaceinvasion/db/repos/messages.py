"""SQLAlchemy ORM repository for `game_messages` and `recycle_reports`."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from spaceinvasion.db.dto import GameMessageRecord, RecycleHarvestSummary, RecycleReportRecord
from spaceinvasion.db.models import GameMessage, RecycleReport
from spaceinvasion.db.repos._helpers import affected_rows, normalize_coords, round_ts, sanitize_text
from spaceinvasion.db.repos._message_ingest import (
    FRACTION_ONLY_RE,
    GameMessageInput,
    RecycleReportInput,
    merge_recycle_rows,
    msg_to_dto,
    prepare_message_batch,
    recycle_to_dto,
    upsert_message_rows,
)
from spaceinvasion.db.repos._telemetry_rollups import refresh_loot_recycle
from spaceinvasion.db.repos.raids import RaidsRepository

_GENERAL_CATEGORIES: tuple[str, ...] = ("general", "player", "system", "other")


class MessagesRepository:
    """Append-only BitMeUp inbox message and recycle-report persistence."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def ingest_messages(
        self,
        messages: Sequence[GameMessageInput],
        *,
        refresh_hourly_rollup: bool = True,
        ts: float | None = None,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Batch-ingest inbox messages + derived battle/recycle reports in O(1) SQL."""
        if not messages:
            return ((), ())
        now_ts = round_ts(ts)
        dedup_msgs, ordered_uids, ordered_lengths, battle_inputs, recycle_inputs = prepare_message_batch(
            messages, now_ts
        )
        if not dedup_msgs:
            return ((), ())

        existing_rows = self._session.execute(
            select(GameMessage.msg_uid, func.length(GameMessage.body_text)).where(
                GameMessage.msg_uid.in_(list(dedup_msgs))
            )
        ).all()
        seen_len: dict[str, int] = {str(r[0]): int(r[1] or 0) for r in existing_rows}

        upsert_message_rows(self._session, list(dedup_msgs.values()))
        if battle_inputs:
            RaidsRepository(self._session).upsert_battle_reports(battle_inputs, ts=now_ts)
        if recycle_inputs:
            self.upsert_recycle_reports(recycle_inputs, ts=now_ts)
        if refresh_hourly_rollup and (battle_inputs or recycle_inputs):
            refresh_loot_recycle(self._session, ts=now_ts)

        newly: list[str] = []
        for uid, blen in ordered_lengths:
            prev_l = seen_len.get(uid)
            if prev_l is None or blen > prev_l:
                newly.append(uid)
                seen_len[uid] = max(prev_l or 0, blen)
        return (tuple(ordered_uids), tuple(newly))

    def upsert_recycle_reports(
        self,
        reports: Sequence[RecycleReportInput],
        *,
        ts: float | None = None,
    ) -> int:
        """Bulk-upsert structured recycle reports in 1 PostgreSQL `ON CONFLICT` statement."""
        payload = merge_recycle_rows(reports, round_ts(ts))
        if not payload:
            return 0
        stmt = pg_insert(RecycleReport).values(payload)
        self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[RecycleReport.msg_uid],
                set_={
                    "coords": case((stmt.excluded.coords != "", stmt.excluded.coords), else_=RecycleReport.coords),
                    "recycler_count": func.greatest(RecycleReport.recycler_count, stmt.excluded.recycler_count),
                    "cargo_capacity": func.greatest(RecycleReport.cargo_capacity, stmt.excluded.cargo_capacity),
                    "debris_avail_pig": func.greatest(RecycleReport.debris_avail_pig, stmt.excluded.debris_avail_pig),
                    "debris_avail_metal": func.greatest(
                        RecycleReport.debris_avail_metal, stmt.excluded.debris_avail_metal
                    ),
                    "harvested_pig_iron": func.greatest(
                        RecycleReport.harvested_pig_iron, stmt.excluded.harvested_pig_iron
                    ),
                    "harvested_metal": func.greatest(RecycleReport.harvested_metal, stmt.excluded.harvested_metal),
                    "harvested_kryptonite": func.greatest(
                        RecycleReport.harvested_kryptonite, stmt.excluded.harvested_kryptonite
                    ),
                    "harvested_spice": func.greatest(RecycleReport.harvested_spice, stmt.excluded.harvested_spice),
                    "harvested_spice_eq": func.greatest(
                        RecycleReport.harvested_spice_eq, stmt.excluded.harvested_spice_eq
                    ),
                    "raw_text": case(
                        (
                            func.length(stmt.excluded.raw_text) > func.length(RecycleReport.raw_text),
                            stmt.excluded.raw_text,
                        ),
                        else_=RecycleReport.raw_text,
                    ),
                },
            )
        )
        return len(payload)

    def mark_deleted_in_game(self, msg_uids: Sequence[str], *, ts: float | None = None) -> int:
        """Mark messages as deleted on the game server without deleting DB archive rows."""
        clean_uids = [u for raw in msg_uids if (u := sanitize_text(raw, 120).strip())]
        if not clean_uids:
            return 0
        now_ts = round_ts(ts)
        res = self._session.execute(
            update(GameMessage)
            .where(GameMessage.msg_uid.in_(clean_uids))
            .values(deleted_in_game=1, deleted_ts=func.coalesce(GameMessage.deleted_ts, now_ts))
        )
        return affected_rows(res)

    def list_messages(
        self,
        *,
        category: str | None = None,
        exclude_categories: Sequence[str] = (),
        coords: str | None = None,
        folder_type: str | None = None,
        include_deleted: bool = True,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[GameMessageRecord, ...]:
        """Return paginated, filtered inbox messages ordered newest first in 1 SQL statement."""
        stmt = select(GameMessage).where(~GameMessage.msg_uid.like("id:msg-%"))
        if category is not None:
            cat_c = sanitize_text(category).strip()
            if cat_c == "general":
                stmt = stmt.where(GameMessage.category.in_(_GENERAL_CATEGORIES))
            else:
                stmt = stmt.where(GameMessage.category == cat_c)
        excl_c = [c for raw in exclude_categories if (c := sanitize_text(raw).strip())]
        if excl_c:
            stmt = stmt.where(~GameMessage.category.in_(excl_c))
        if coords is not None:
            stmt = stmt.where(GameMessage.coords == normalize_coords(coords))
        if folder_type is not None:
            stmt = stmt.where(GameMessage.folder_type == sanitize_text(folder_type).strip())
        if not include_deleted:
            stmt = stmt.where(GameMessage.deleted_in_game == 0)
        rows = (
            self._session.execute(
                stmt.order_by(GameMessage.ingested_ts.desc(), GameMessage.id.desc())
                .offset(max(0, int(offset)))
                .limit(max(1, int(limit)))
            )
            .scalars()
            .all()
        )
        return tuple(msg_to_dto(r) for r in rows if not FRACTION_ONLY_RE.fullmatch(r.body_text.strip()))

    def list_recycle_reports(
        self,
        *,
        coords: str | None = None,
        limit: int = 200,
    ) -> tuple[RecycleReportRecord, ...]:
        """List persisted recycle reports ordered newest first in 1 SQL statement."""
        stmt = select(RecycleReport)
        if coords is not None:
            stmt = stmt.where(RecycleReport.coords == normalize_coords(coords))
        rows = (
            self._session.execute(
                stmt.order_by(RecycleReport.ts.desc(), RecycleReport.id.desc()).limit(max(1, int(limit)))
            )
            .scalars()
            .all()
        )
        return tuple(recycle_to_dto(r) for r in rows)

    def summarize_recycle_harvests(
        self,
        *,
        since_ts: float | None = None,
        coords: str | None = None,
    ) -> RecycleHarvestSummary:
        """Aggregate recycler harvests in 1 set-based SQL `SELECT`."""
        stmt = select(
            func.count(),
            func.coalesce(func.sum(RecycleReport.recycler_count), 0),
            func.coalesce(func.sum(RecycleReport.harvested_pig_iron), 0),
            func.coalesce(func.sum(RecycleReport.harvested_metal), 0),
            func.coalesce(func.sum(RecycleReport.harvested_kryptonite), 0),
            func.coalesce(func.sum(RecycleReport.harvested_spice), 0),
            func.coalesce(func.sum(RecycleReport.harvested_spice_eq), 0.0),
            func.max(RecycleReport.ts),
        )
        if since_ts is not None:
            stmt = stmt.where(RecycleReport.ts >= float(since_ts))
        if coords is not None:
            stmt = stmt.where(RecycleReport.coords == normalize_coords(coords))
        row = self._session.execute(stmt).one()
        return RecycleHarvestSummary(
            report_count=int(row[0] or 0),
            total_recyclers=int(row[1] or 0),
            total_pig_iron=int(row[2] or 0),
            total_metal=int(row[3] or 0),
            total_kryptonite=int(row[4] or 0),
            total_spice=int(row[5] or 0),
            total_spice_eq=round(float(row[6] or 0.0), 3),
            latest_ts=float(row[7]) if row[7] is not None else None,
        )


__all__ = ["GameMessageInput", "MessagesRepository", "RecycleReportInput"]
