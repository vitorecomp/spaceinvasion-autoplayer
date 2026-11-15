"""Non-browser database diagnostics `@command` handlers."""

from __future__ import annotations

from dataclasses import dataclass

from backend.runtime.api.handlers._bridge_proto import BridgeApiFacade
from backend.runtime.api.registry import CommandRegistry
from backend.runtime.api.validation import ApiRequestError
from backend.runtime.bridge._helpers import sanitize_json_dict


@dataclass(frozen=True)
class DbSummaryParams:
    """Parameters for retrieving database row-count and tier summaries."""


@dataclass(frozen=True)
class DbVerifyParams:
    """Parameters for running schema and optional integrity verification."""

    full_integrity: bool = False


def _handle_db_summary(bridge: BridgeApiFacade, _params: DbSummaryParams) -> dict[str, object]:
    """Return database row counts, hot/history table tiers, and recent telemetry summaries."""
    with bridge.ctx.intel_read_lock:
        db = bridge.ctx.db
        summary = db.summary() if db is not None else None
    return sanitize_json_dict({"ok": True, "command": "db_summary", "db": summary})


def _handle_db_verify(bridge: BridgeApiFacade, params: DbVerifyParams) -> dict[str, object]:
    """Verify database schema migrations and optional full integrity checks."""
    with bridge.ctx.intel_read_lock:
        db = bridge.ctx.db
        if db is None:
            raise ApiRequestError("Database is not configured on this API bridge.")
        ver = db.verify_schema(full_integrity=params.full_integrity)
    return sanitize_json_dict(
        {
            "ok": bool(ver.get("ok", False)),
            "command": "db_verify",
            "verification": ver,
        }
    )


def register_diagnostic_handlers(registry: CommandRegistry[BridgeApiFacade]) -> None:
    """Register all 2 non-browser database diagnostic commands."""
    registry.command(
        "db_summary",
        needs_browser=False,
        mutates=False,
        summary="Return database row counts, table tiers, and recent action/intel summaries.",
        params=DbSummaryParams,
    )(_handle_db_summary)
    registry.command(
        "db_verify",
        needs_browser=False,
        mutates=False,
        summary="Verify database schema migrations and optional full integrity.",
        params=DbVerifyParams,
    )(_handle_db_verify)
