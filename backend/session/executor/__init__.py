"""Decomposed Selenium action executor package (`backend.session.executor`)."""

from __future__ import annotations

import time

from selenium.webdriver.support.ui import Select

from backend.parsing.fleet_page import extract_internal_fleet_planet_coords
from backend.session.client.reader import extract_selected_planet_id
from backend.session.executor._dom import find_newplanet_switch_target
from backend.session.executor._fleet_errors import pick_send_fleet_form_css
from backend.session.executor._fleet_radio import match_mission_radio
from backend.session.executor._types import ExecutionResult, safe_str
from backend.session.executor.build import BuildOps
from backend.session.executor.colony import ColonyOps
from backend.session.executor.executor import ActionExecutor
from backend.session.executor.fleet_dispatch import (
    FleetMissionDispatcher,
    extract_fleet_server_error,
    extract_mission_forbidden_reason,
    is_unattackable_target_html,
)
from backend.session.executor.missions import MissionRunner
from backend.session.executor.planet_select import PlanetSelector
from backend.session.executor.settings_sync import SettingsSyncOps
from backend.session.executor.transmitter import TransmitterOps

_is_unattackable_target_html = is_unattackable_target_html
_extract_mission_forbidden_reason = extract_mission_forbidden_reason
_safe_str = safe_str

__all__ = [
    "ActionExecutor",
    "BuildOps",
    "ColonyOps",
    "ExecutionResult",
    "FleetMissionDispatcher",
    "MissionRunner",
    "PlanetSelector",
    "Select",
    "SettingsSyncOps",
    "TransmitterOps",
    "_extract_mission_forbidden_reason",
    "_is_unattackable_target_html",
    "_safe_str",
    "extract_fleet_server_error",
    "extract_internal_fleet_planet_coords",
    "extract_mission_forbidden_reason",
    "extract_selected_planet_id",
    "find_newplanet_switch_target",
    "is_unattackable_target_html",
    "match_mission_radio",
    "pick_send_fleet_form_css",
    "safe_str",
    "time",
]
