"""Set-based SQLAlchemy `select()` read queries returning typed DTO rows."""

from __future__ import annotations

from spaceinvasion.db.queries.colonies import (
    ColonyAttemptCoordSummary,
    ColonyAttemptsSummary,
    query_colony_attempts_summary,
    query_colony_candidates,
)
from spaceinvasion.db.queries.dashboard import (
    GAME_REQUIRED_TABLES,
    HISTORY_TABLES,
    HOT_TABLES,
    TABLE_TIER_BY_NAME,
    query_config_optimizer_goals,
    query_dashboard_time_series,
    query_db_summary,
    query_db_verify,
)
from spaceinvasion.db.queries.galaxy import query_galaxy_coverage, query_galaxy_sector_intel
from spaceinvasion.db.queries.galaxy_detailed import query_galaxy_sector_intel_detailed
from spaceinvasion.db.queries.gateway_reads import (
    query_active_blocked_targets,
    query_active_debris_targets_raw,
    query_battle_debris_fallbacks,
    query_executed_transport_actions,
    query_last_throttle_timestamps,
    query_latest_own_researches_and_nick,
    query_needs_loot_recycle_backfill,
    query_recent_raids_for_bash,
    query_recycle_debris_candidates,
    query_spy_research_observations,
)
from spaceinvasion.db.queries.intel import query_active_debris_targets, query_spy_targets
from spaceinvasion.db.queries.raids import query_bash_window, query_raid_stats
from spaceinvasion.db.queries.startup_repair import (
    GameMessageBackfillRow,
    GameMessageBackfillUpdate,
    SpyBackfillUpdate,
    apply_game_message_backfill,
    format_backfill_json,
    load_game_messages_for_backfill,
    rebuild_hot_tables_sql,
    repair_spy_targets_sql,
)
from spaceinvasion.db.queries.telemetry_summary import (
    query_full_history_summary,
    query_raid_stats_detailed,
)

__all__ = [
    "ColonyAttemptCoordSummary",
    "ColonyAttemptsSummary",
    "GAME_REQUIRED_TABLES",
    "GameMessageBackfillRow",
    "GameMessageBackfillUpdate",
    "HISTORY_TABLES",
    "HOT_TABLES",
    "SpyBackfillUpdate",
    "TABLE_TIER_BY_NAME",
    "apply_game_message_backfill",
    "format_backfill_json",
    "load_game_messages_for_backfill",
    "query_active_blocked_targets",
    "query_active_debris_targets",
    "query_active_debris_targets_raw",
    "query_bash_window",
    "query_battle_debris_fallbacks",
    "query_colony_attempts_summary",
    "query_colony_candidates",
    "query_config_optimizer_goals",
    "query_dashboard_time_series",
    "query_db_summary",
    "query_db_verify",
    "query_executed_transport_actions",
    "query_full_history_summary",
    "query_galaxy_coverage",
    "query_galaxy_sector_intel",
    "query_galaxy_sector_intel_detailed",
    "query_last_throttle_timestamps",
    "query_latest_own_researches_and_nick",
    "query_needs_loot_recycle_backfill",
    "query_raid_stats",
    "query_raid_stats_detailed",
    "query_recent_raids_for_bash",
    "query_recycle_debris_candidates",
    "query_spy_research_observations",
    "query_spy_targets",
    "rebuild_hot_tables_sql",
    "repair_spy_targets_sql",
]
