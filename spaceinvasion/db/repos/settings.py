"""SQLAlchemy 2.0 ORM repository for metadata, strategy config, and migration ledgers."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from spaceinvasion.db.dto import (
    ConfigHistoryRecord,
    MetaRecord,
    SchemaMigrationEventRecord,
    SchemaMigrationRecord,
    StrategyConfigRecord,
)
from spaceinvasion.db.models import (
    ConfigHistory,
    MetaEntry,
    SchemaMigration,
    SchemaMigrationEvent,
    StrategyConfigStore,
)
from spaceinvasion.db.repos._helpers import (
    PROTECTED_META_KEYS,
    dumps_json,
    loads_json_dict,
    round_ts,
    sanitize_text,
)


def _meta_to_dto(row: MetaEntry) -> MetaRecord:
    return MetaRecord(key=row.key, value=row.value, updated_ts=float(row.updated_ts))


def _cfg_to_dto(row: StrategyConfigStore) -> StrategyConfigRecord:
    return StrategyConfigRecord(
        id=int(row.id),
        config_json=row.config_json,
        updated_ts=float(row.updated_ts),
        source=row.source,
    )


class SettingsRepository:
    """SQLAlchemy ORM repository over `meta`, `strategy_config_store`, `config_history`, and ledgers."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_meta(self, key: str) -> MetaRecord | None:
        """Read one `meta` entry (returns `None` for protected schema marker keys)."""
        clean_key = sanitize_text(key).strip()
        if not clean_key or clean_key in PROTECTED_META_KEYS:
            return None
        row = self._session.get(MetaEntry, clean_key)
        return _meta_to_dto(row) if row is not None else None

    def get_meta_json(self, key: str) -> dict[str, object] | None:
        """Read and JSON-decode a `meta` dictionary entry."""
        rec = self.get_meta(key)
        if rec is None or not rec.value:
            return None
        parsed = loads_json_dict(rec.value)
        return parsed if parsed or rec.value.strip() == "{}" else None

    def upsert_meta(
        self,
        key: str,
        value: str,
        *,
        ts: float | None = None,
    ) -> MetaRecord | None:
        """Upsert a single `meta` row via PostgreSQL `INSERT ... ON CONFLICT DO UPDATE`."""
        clean_key = sanitize_text(key).strip()
        if not clean_key or clean_key in PROTECTED_META_KEYS:
            return None
        now_ts = round_ts(ts)
        stmt = pg_insert(MetaEntry).values(key=clean_key, value=value, updated_ts=now_ts)
        stmt = stmt.on_conflict_do_update(
            index_elements=[MetaEntry.key],
            set_={"value": stmt.excluded.value, "updated_ts": stmt.excluded.updated_ts},
        )
        self._session.execute(stmt)
        return MetaRecord(key=clean_key, value=value, updated_ts=now_ts)

    def upsert_meta_json(
        self,
        key: str,
        payload: object,
        *,
        ts: float | None = None,
    ) -> MetaRecord | None:
        """Serialize `payload` to redacted canonical JSON and upsert into `meta`."""
        return self.upsert_meta(key, dumps_json(payload), ts=ts)

    def bulk_upsert_meta(
        self,
        entries: Mapping[str, str],
        *,
        ts: float | None = None,
    ) -> int:
        """Bulk-upsert multiple `meta` key-value rows in a single PostgreSQL statement."""
        now_ts = round_ts(ts)
        rows = [
            {"key": clean_k, "value": val, "updated_ts": now_ts}
            for raw_k, val in entries.items()
            if (clean_k := sanitize_text(raw_k).strip()) and clean_k not in PROTECTED_META_KEYS
        ]
        if not rows:
            return 0
        stmt = pg_insert(MetaEntry).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[MetaEntry.key],
            set_={"value": stmt.excluded.value, "updated_ts": stmt.excluded.updated_ts},
        )
        self._session.execute(stmt)
        return len(rows)

    def save_strategy_config(
        self,
        config_payload: Mapping[str, object],
        *,
        ts: float | None = None,
        source: str = "sqlite",
    ) -> StrategyConfigRecord:
        """Upsert `strategy_config_store(id=1)` + `meta['strategy_config']` and append `config_history`."""
        now_ts = round_ts(ts)
        cfg_json = dumps_json(dict(config_payload))
        src_clean = sanitize_text(source or "sqlite", 32)

        cfg_stmt = pg_insert(StrategyConfigStore).values(
            id=1,
            config_json=cfg_json,
            updated_ts=now_ts,
            source=src_clean,
        )
        cfg_stmt = cfg_stmt.on_conflict_do_update(
            index_elements=[StrategyConfigStore.id],
            set_={
                "config_json": cfg_stmt.excluded.config_json,
                "updated_ts": cfg_stmt.excluded.updated_ts,
                "source": cfg_stmt.excluded.source,
            },
        )
        self._session.execute(cfg_stmt)
        self.upsert_meta("strategy_config", cfg_json, ts=now_ts)

        last_json = self._session.execute(
            select(ConfigHistory.config_json).order_by(ConfigHistory.id.desc()).limit(1)
        ).scalar_one_or_none()
        if last_json != cfg_json:
            self._session.add(ConfigHistory(ts=now_ts, config_json=cfg_json))
            self._session.flush()

        return StrategyConfigRecord(id=1, config_json=cfg_json, updated_ts=now_ts, source=src_clean)

    def load_strategy_config(self) -> StrategyConfigRecord | None:
        """Load active strategy config from `strategy_config_store` with `meta` fallback."""
        row = self._session.get(StrategyConfigStore, 1)
        if row is not None and row.config_json.strip():
            return _cfg_to_dto(row)
        meta_row = self.get_meta("strategy_config")
        if meta_row is not None and meta_row.value.strip():
            return StrategyConfigRecord(
                id=1,
                config_json=meta_row.value,
                updated_ts=meta_row.updated_ts,
                source="meta",
            )
        return None

    def list_config_history(self, *, limit: int = 50) -> tuple[ConfigHistoryRecord, ...]:
        """List append-only strategy config history ordered newest first."""
        lim = max(1, min(500, int(limit)))
        rows = self._session.execute(select(ConfigHistory).order_by(ConfigHistory.id.desc()).limit(lim)).scalars().all()
        return tuple(ConfigHistoryRecord(id=r.id, ts=float(r.ts), config_json=r.config_json) for r in rows)

    def list_schema_migrations(self) -> tuple[SchemaMigrationRecord, ...]:
        """List applied legacy migration records ordered by ascending version."""
        rows = self._session.execute(select(SchemaMigration).order_by(SchemaMigration.version.asc())).scalars().all()
        return tuple(
            SchemaMigrationRecord(
                version=int(r.version),
                version_tag=r.version_tag,
                name=r.name,
                description=r.description,
                checksum=r.checksum,
                applied_ts=float(r.applied_ts),
                duration_ms=float(r.duration_ms),
                app_schema_version=r.app_schema_version,
                details_json=r.details_json,
            )
            for r in rows
        )

    def list_schema_migration_events(self, *, limit: int = 100) -> tuple[SchemaMigrationEventRecord, ...]:
        """List append-only migration audit events ordered newest first."""
        lim = max(1, min(500, int(limit)))
        rows = (
            self._session.execute(select(SchemaMigrationEvent).order_by(SchemaMigrationEvent.id.desc()).limit(lim))
            .scalars()
            .all()
        )
        return tuple(
            SchemaMigrationEventRecord(
                id=r.id,
                ts=float(r.ts),
                event_type=r.event_type,
                from_version=int(r.from_version),
                to_version=int(r.to_version),
                version=int(r.version) if r.version is not None else None,
                name=r.name,
                checksum=r.checksum,
                backup_path=r.backup_path,
                details_json=r.details_json,
            )
            for r in rows
        )


__all__ = ["SettingsRepository"]
