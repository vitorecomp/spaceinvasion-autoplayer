"""Typed SQLAlchemy 2.0 ORM repositories for all 11 SpaceInvasion database table families."""

from __future__ import annotations

from spaceinvasion.db.repos.colonies import ColoniesRepository, ColonyCandidateHotInput
from spaceinvasion.db.repos.galaxy import GalaxyRepository, GalaxySlotInput
from spaceinvasion.db.repos.intel import IntelRepository, SpyReportInput, SpyTargetUpsertInput
from spaceinvasion.db.repos.messages import GameMessageInput, MessagesRepository, RecycleReportInput
from spaceinvasion.db.repos.notifications import (
    NotificationsRepository,
    format_notification_code,
    parse_notification_id,
)
from spaceinvasion.db.repos.planner_runs import PlannerRunsRepository
from spaceinvasion.db.repos.queue import ManualQueueRepository
from spaceinvasion.db.repos.raids import BattleReportInput, RaidsRepository
from spaceinvasion.db.repos.settings import SettingsRepository
from spaceinvasion.db.repos.snapshots import (
    PlanetSnapshotInput,
    RankSnapshotInput,
    SnapshotsRepository,
    StateSnapshotInput,
)
from spaceinvasion.db.repos.telemetry import TelemetryRepository, UniverseResearchMaxInput

__all__ = [
    "BattleReportInput",
    "ColoniesRepository",
    "ColonyCandidateHotInput",
    "GalaxyRepository",
    "GalaxySlotInput",
    "GameMessageInput",
    "IntelRepository",
    "ManualQueueRepository",
    "MessagesRepository",
    "NotificationsRepository",
    "PlanetSnapshotInput",
    "PlannerRunsRepository",
    "RaidsRepository",
    "RankSnapshotInput",
    "RecycleReportInput",
    "SettingsRepository",
    "SnapshotsRepository",
    "SpyReportInput",
    "SpyTargetUpsertInput",
    "StateSnapshotInput",
    "TelemetryRepository",
    "UniverseResearchMaxInput",
    "format_notification_code",
    "parse_notification_id",
]
