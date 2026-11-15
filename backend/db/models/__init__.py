"""Typed SQLAlchemy 2.0 declarative ORM models covering all 40 PostgreSQL game tables."""

from __future__ import annotations

from backend.db.models.base import Base, identity_pk
from backend.db.models.colonies import ColonyAttempt, ColonyCandidateHot
from backend.db.models.galaxy import (
    GalaxyScan,
    GalaxyScanHistory,
    GalaxySectorHot,
    GalaxySlot,
    GalaxySlotHistory,
)
from backend.db.models.intel import (
    SpyReportRecord,
    SpyTarget,
    UniverseResearchMax,
    UniverseResearchMaxHistory,
)
from backend.db.models.messages import BattleReport, GameMessage, RecycleReport
from backend.db.models.notifications import UiNotification
from backend.db.models.planner_runs import (
    AiPlannerActive,
    AiPlannerRun,
    ConfigOptimizerActive,
    ConfigOptimizerRun,
    FieldConflictEvent,
    FieldConflictPrompt,
)
from backend.db.models.queue import ManualQueueEvent, ManualQueueItem
from backend.db.models.raids import BashBlock, LootRecycleHourly, RaidHistory
from backend.db.models.settings import (
    ConfigHistory,
    MetaEntry,
    SchemaMigration,
    SchemaMigrationEvent,
    StrategyConfigStore,
)
from backend.db.models.snapshots import (
    EconomyRatesCache,
    EmpirePlanetHot,
    LatestStateHot,
    PlanetSnapshot,
    RankSnapshot,
    StateSnapshot,
    TutorialProgress,
)
from backend.db.models.telemetry import ActionHistory, HourlyProductionHistory

__all__ = [
    "ActionHistory",
    "AiPlannerActive",
    "AiPlannerRun",
    "Base",
    "BashBlock",
    "BattleReport",
    "ColonyAttempt",
    "ColonyCandidateHot",
    "ConfigHistory",
    "ConfigOptimizerActive",
    "ConfigOptimizerRun",
    "EconomyRatesCache",
    "EmpirePlanetHot",
    "FieldConflictEvent",
    "FieldConflictPrompt",
    "GalaxyScan",
    "GalaxyScanHistory",
    "GalaxySectorHot",
    "GalaxySlot",
    "GalaxySlotHistory",
    "GameMessage",
    "HourlyProductionHistory",
    "LatestStateHot",
    "LootRecycleHourly",
    "ManualQueueEvent",
    "ManualQueueItem",
    "MetaEntry",
    "PlanetSnapshot",
    "RaidHistory",
    "RankSnapshot",
    "RecycleReport",
    "SchemaMigration",
    "SchemaMigrationEvent",
    "SpyReportRecord",
    "SpyTarget",
    "StateSnapshot",
    "StrategyConfigStore",
    "TutorialProgress",
    "UiNotification",
    "UniverseResearchMax",
    "UniverseResearchMaxHistory",
    "identity_pk",
]
