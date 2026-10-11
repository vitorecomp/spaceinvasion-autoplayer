"""Set-based SQLAlchemy `select()` read queries returning typed DTO rows."""

from __future__ import annotations

from spaceinvasion.db.queries.colonies import (
    ColonyAttemptCoordSummary,
    ColonyAttemptsSummary,
    query_colony_attempts_summary,
    query_colony_candidates,
)
from spaceinvasion.db.queries.galaxy import query_galaxy_coverage, query_galaxy_sector_intel
from spaceinvasion.db.queries.intel import query_active_debris_targets, query_spy_targets
from spaceinvasion.db.queries.raids import query_bash_window, query_raid_stats

__all__ = [
    "ColonyAttemptCoordSummary",
    "ColonyAttemptsSummary",
    "query_active_debris_targets",
    "query_bash_window",
    "query_colony_attempts_summary",
    "query_colony_candidates",
    "query_galaxy_coverage",
    "query_galaxy_sector_intel",
    "query_raid_stats",
    "query_spy_targets",
]
