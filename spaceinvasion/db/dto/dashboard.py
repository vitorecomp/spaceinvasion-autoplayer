"""Frozen DTO dataclasses for database diagnostics, dashboard time series, and optimizer goals."""

from __future__ import annotations

from dataclasses import dataclass

from spaceinvasion.db.dto.telemetry import (
    ActionHistoryRecord,
    HourlyProductionRecord,
    LootRecycleHourlyRecord,
    LootRecycleWindowSummary,
    TutorialProgressRecord,
)
from spaceinvasion.db.dto.world import SpyTargetRecord


@dataclass(frozen=True)
class TableTiersSummary:
    """Bounded operational (`hot`) vs append-only (`history`) table tier row counts."""

    hot_tables: tuple[str, ...]
    history_tables: tuple[str, ...]
    hot_counts: dict[str, int]
    history_counts: dict[str, int]
    hot_total_rows: int
    history_total_rows: int


@dataclass(frozen=True)
class DbSummaryRecord:
    """Typed diagnostic rollup returned by `query_db_summary`."""

    backend: str
    size_bytes: int
    alembic_revision: str | None
    legacy_schema_version: int
    counts: dict[str, int]
    table_tiers: TableTiersSummary
    message_categories: dict[str, int]
    persisted_meta_keys: tuple[str, ...]
    latest_snapshot_ts: float | None
    latest_scan_ts: float | None
    latest_raid_ts: float | None
    tutorial_history: tuple[TutorialProgressRecord, ...]
    recent_actions: tuple[ActionHistoryRecord, ...]
    spy_targets: tuple[SpyTargetRecord, ...]


@dataclass(frozen=True)
class DbVerifyRecord:
    """PostgreSQL-native schema and ledger integrity report returned by `query_db_verify`."""

    ok: bool
    backend: str
    alembic_revision: str | None
    target_revision: str
    up_to_date: bool
    missing_tables: tuple[str, ...]
    schema_diffs: tuple[str, ...]
    ledger_ok: bool
    ledger_events_count: int
    table_tiers: TableTiersSummary


@dataclass(frozen=True)
class DashboardTimeSeriesRecord:
    """Combined production and hourly loot/recycle series for dashboard charts."""

    hourly_production: tuple[HourlyProductionRecord, ...]
    latest_minute_bucket: int | None
    incremental: bool
    loot_recycle_hourly: tuple[LootRecycleHourlyRecord, ...]
    loot_recycle_summary: LootRecycleWindowSummary


@dataclass(frozen=True)
class PlanetBuildingGoalsRecord:
    """Per-planet Config Optimizer building target levels (`b1..b21`) and specialization."""

    planet_id: int
    coords: str
    specialization: str
    goals: dict[str, int]


@dataclass(frozen=True)
class ConfigOptimizerGoalsRecord:
    """Active Config Optimizer per-planet building goals indexed by planet ID and coordinates."""

    run_id: int | None
    updated_ts: float
    by_planet_id: dict[str, PlanetBuildingGoalsRecord]
    by_coords: dict[str, PlanetBuildingGoalsRecord]


__all__ = [
    "ConfigOptimizerGoalsRecord",
    "DashboardTimeSeriesRecord",
    "DbSummaryRecord",
    "DbVerifyRecord",
    "PlanetBuildingGoalsRecord",
    "TableTiersSummary",
]
