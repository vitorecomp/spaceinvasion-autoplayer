"""Runtime database gateway wrapping SQLAlchemy 2.0 `DatabaseStore` for HTTP bridge & API handlers."""

from __future__ import annotations

import threading

from backend.db.dsn import redact_db_url
from backend.db.dto.serializers import (
    recent_action_to_dict,
    spy_target_to_dict,
    tutorial_progress_to_dict,
)
from backend.db.migrations import BASELINE_REVISION, NO_BACKUP, BackupOption, get_revision_status
from backend.db.queries import query_db_summary, query_db_verify
from backend.db.repos.notifications import format_notification_code, parse_notification_id
from backend.db.session import DEFAULT_MAX_OVERFLOW, DEFAULT_POOL_SIZE, DEFAULT_STATEMENT_TIMEOUT_MS
from backend.db.store import DatabaseStore
from backend.runtime.bridge._db_gateway_combat import _CombatGatewayMixin
from backend.runtime.bridge._db_gateway_common import server_day_key
from backend.runtime.bridge._db_gateway_intel import _IntelGatewayMixin
from backend.runtime.bridge._db_gateway_meta import _MetaGatewayMixin, _sanitize_custom_planet_name
from backend.runtime.bridge._db_gateway_ops import _OpsGatewayMixin
from backend.runtime.bridge._db_gateway_startup import run_gateway_startup_maintenance
from backend.runtime.bridge._db_gateway_state import _StateGatewayMixin
from backend.runtime.bridge._db_gateway_telemetry import _normalize_cargo_dict, _TelemetryGatewayMixin


class RuntimeDatabaseGateway(
    _MetaGatewayMixin,
    _OpsGatewayMixin,
    _StateGatewayMixin,
    _TelemetryGatewayMixin,
    _CombatGatewayMixin,
    _IntelGatewayMixin,
):
    """ORM-backed runtime gateway exposing the 92-method persistence surface over `DatabaseStore`."""

    def __init__(
        self,
        store: DatabaseStore,
        *,
        postgres_url: str = "",
        run_startup_maintenance: bool = True,
    ) -> None:
        self._store = store
        self._postgres_url = postgres_url.strip() or store.postgres_url
        self._change_lock = threading.Lock()
        self._change_counter = 0
        self._last_slot_history_sig: tuple[float, tuple[str, ...]] | None = None
        if run_startup_maintenance:
            run_gateway_startup_maintenance(self._store)

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        pool_size: int = DEFAULT_POOL_SIZE,
        max_overflow: int = DEFAULT_MAX_OVERFLOW,
        pool_pre_ping: bool = True,
        statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
        ensure_ready: bool = True,
        verify_schema: bool = True,
        backup_fn: BackupOption = NO_BACKUP,
        allow_stamp_existing: bool = True,
        run_startup_maintenance: bool = True,
    ) -> RuntimeDatabaseGateway:
        """Construct a `RuntimeDatabaseGateway` from a PostgreSQL DSN, optionally migrating/stamping."""
        store = DatabaseStore.from_dsn(
            dsn,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=pool_pre_ping,
            statement_timeout_ms=statement_timeout_ms,
        )
        if ensure_ready:
            store.ensure_ready(
                backup_fn=backup_fn,
                allow_stamp_existing=allow_stamp_existing,
                verify_schema=verify_schema,
            )
        return cls(store, postgres_url=dsn, run_startup_maintenance=run_startup_maintenance)

    @staticmethod
    def sanitize_custom_planet_name(raw: object, *, max_len: int = 48) -> str:
        """Sanitize and clamp a custom planet display name."""
        return _sanitize_custom_planet_name(raw, max_len=max_len)

    @staticmethod
    def parse_notification_id(raw: int | str | None) -> int | None:
        """Parse a numeric or `#N`-formatted notification identifier."""
        return parse_notification_id(raw)

    @staticmethod
    def format_notification_code(notification_id: int) -> str:
        """Format a notification identifier as `NTF-XXXX`."""
        return format_notification_code(notification_id)

    @staticmethod
    def normalize_cargo_dict(cargo: object) -> dict[str, int]:
        """Normalize a cargo mapping or `Resources` into non-negative integer resource keys."""
        return _normalize_cargo_dict(cargo)

    @property
    def backend(self) -> str:
        """Active database backend identifier (`'postgres'`)."""
        return "postgres"

    @property
    def backend_name(self) -> str:
        """Active database backend identifier (`'postgres'`)."""
        return "postgres"

    @property
    def postgres_url(self) -> str:
        """Configured PostgreSQL DSN."""
        return self._postgres_url

    @property
    def display_target(self) -> str:
        """Password-redacted PostgreSQL target string for diagnostics and API responses."""
        return redact_db_url(self._postgres_url) if self._postgres_url else self._store.display_target

    @property
    def storage(self) -> RuntimeDatabaseGateway:
        """Self-reference preserved for bridge callers accessing `ctx.db.storage`."""
        return self

    def change_token(self) -> tuple[int, int, int]:
        """Return a monotonic change token `(gw_id, local_writes, pg_xmax)` for cache invalidation."""
        with self._change_lock:
            n = self._change_counter
        return (id(self), n, self._store.snapshot_xid())

    def close(self) -> None:
        """Dispose the underlying `DatabaseStore` connection pool."""
        self._store.close()

    def summary(self) -> dict[str, object]:
        """Return PostgreSQL table counts, tiers, migration info, and dashboard previews."""
        with self._store.session() as session:
            rec = query_db_summary(session, recent_actions_limit=12, spy_targets_limit=25)
            migs = self._store.settings_repo(session).list_schema_migrations()

        counts_no_meta = {k: v for k, v in rec.counts.items() if k != "meta"}
        spy_dicts = [spy_target_to_dict(t) for t in rec.spy_targets]
        self._enrich_debris(spy_dicts)
        rev = rec.alembic_revision or ""
        target_rev, pending_revs = get_revision_status(rec.alembic_revision)
        return {
            "backend": rec.backend,
            "db_path": self.display_target,
            "size_bytes": rec.size_bytes,
            "schema": {
                "version": rec.legacy_schema_version,
                "version_tag": rev
                or (f"V{rec.legacy_schema_version:03d}" if rec.legacy_schema_version > 0 else BASELINE_REVISION),
                "target_version": rec.legacy_schema_version,
                "target_version_tag": target_rev,
                "alembic_revision": rec.alembic_revision,
                "up_to_date": rec.alembic_revision == target_rev,
                "applied_count": len(migs) if migs else (1 if rec.alembic_revision else 0),
                "pending_count": len(pending_revs),
                "checksum_drift": [],
                "applied_migrations": [
                    {
                        "version": m.version,
                        "version_tag": m.version_tag,
                        "name": m.name,
                        "applied_ts": m.applied_ts,
                    }
                    for m in migs
                ],
            },
            "counts": counts_no_meta,
            "table_tiers": {
                "hot": list(rec.table_tiers.hot_tables),
                "history": list(rec.table_tiers.history_tables),
                "hot_counts": dict(rec.table_tiers.hot_counts),
                "history_counts": dict(rec.table_tiers.history_counts),
                "hot_total_rows": rec.table_tiers.hot_total_rows,
                "history_total_rows": rec.table_tiers.history_total_rows,
            },
            "message_categories": dict(rec.message_categories),
            "persisted_meta_keys": list(rec.persisted_meta_keys),
            "tutorial_history": [tutorial_progress_to_dict(t) for t in rec.tutorial_history],
            "recent_actions": [recent_action_to_dict(a) for a in rec.recent_actions],
            "spy_targets": spy_dicts,
        }

    def verify_schema(self, *, full_integrity: bool = False) -> dict[str, object]:
        """Verify PostgreSQL catalog tables, ORM zero-drift equivalence, Alembic revision, and ledger."""
        with self._store.session() as session:
            ver = query_db_verify(session, self._store.db_engine)
        return {
            "ok": ver.ok,
            "backend": ver.backend,
            "db_path": self.display_target,
            "full_integrity": bool(full_integrity),
            "alembic_revision": ver.alembic_revision,
            "target_revision": ver.target_revision,
            "up_to_date": ver.up_to_date,
            "missing_tables": list(ver.missing_tables),
            "schema_diffs": list(ver.schema_diffs),
            "ledger_ok": ver.ledger_ok,
            "ledger_events_count": ver.ledger_events_count,
            "table_tiers": {
                "hot": list(ver.table_tiers.hot_tables),
                "history": list(ver.table_tiers.history_tables),
                "hot_counts": dict(ver.table_tiers.hot_counts),
                "history_counts": dict(ver.table_tiers.history_counts),
                "hot_total_rows": ver.table_tiers.hot_total_rows,
                "history_total_rows": ver.table_tiers.history_total_rows,
            },
        }


__all__ = [
    "RuntimeDatabaseGateway",
    "format_notification_code",
    "parse_notification_id",
    "server_day_key",
]
