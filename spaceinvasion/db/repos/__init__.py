"""Typed SQLAlchemy 2.0 ORM repositories for SpaceInvasion database table families."""

from __future__ import annotations

from spaceinvasion.db.repos.colonies import ColoniesRepository, ColonyCandidateHotInput
from spaceinvasion.db.repos.notifications import (
    NotificationsRepository,
    format_notification_code,
    parse_notification_id,
)
from spaceinvasion.db.repos.planner_runs import PlannerRunsRepository
from spaceinvasion.db.repos.queue import ManualQueueRepository
from spaceinvasion.db.repos.settings import SettingsRepository
from spaceinvasion.db.repos.telemetry import TelemetryRepository, UniverseResearchMaxInput

__all__ = [
    "ColoniesRepository",
    "ColonyCandidateHotInput",
    "ManualQueueRepository",
    "NotificationsRepository",
    "PlannerRunsRepository",
    "SettingsRepository",
    "TelemetryRepository",
    "UniverseResearchMaxInput",
    "format_notification_code",
    "parse_notification_id",
]
