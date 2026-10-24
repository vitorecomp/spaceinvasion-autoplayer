"""Decomposed Selenium action executor package (`spaceinvasion.session.exec_ops`)."""

from __future__ import annotations

from spaceinvasion.session.exec_ops._types import ExecutionResult
from spaceinvasion.session.exec_ops.build import BuildOps
from spaceinvasion.session.exec_ops.colony import ColonyOps
from spaceinvasion.session.exec_ops.executor import ActionExecutor
from spaceinvasion.session.exec_ops.fleet_dispatch import (
    FleetMissionDispatcher,
    extract_fleet_server_error,
    extract_mission_forbidden_reason,
    is_unattackable_target_html,
)
from spaceinvasion.session.exec_ops.missions import MissionRunner
from spaceinvasion.session.exec_ops.planet_select import PlanetSelector
from spaceinvasion.session.exec_ops.settings_sync import SettingsSyncOps
from spaceinvasion.session.exec_ops.transmitter import TransmitterOps

_is_unattackable_target_html = is_unattackable_target_html
_extract_mission_forbidden_reason = extract_mission_forbidden_reason

__all__ = [
    "ActionExecutor",
    "BuildOps",
    "ColonyOps",
    "ExecutionResult",
    "FleetMissionDispatcher",
    "MissionRunner",
    "PlanetSelector",
    "SettingsSyncOps",
    "TransmitterOps",
    "_extract_mission_forbidden_reason",
    "_is_unattackable_target_html",
    "extract_fleet_server_error",
    "extract_mission_forbidden_reason",
    "is_unattackable_target_html",
]
