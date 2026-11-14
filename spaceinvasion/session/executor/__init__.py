"""Decomposed Selenium action executor package (`spaceinvasion.session.executor`)."""

from __future__ import annotations

import time

from selenium.webdriver.support.ui import Select

from spaceinvasion.parsing.fleet_page import extract_internal_fleet_planet_coords
from spaceinvasion.session.client.reader import extract_selected_planet_id
from spaceinvasion.session.executor._dom import find_newplanet_switch_target
from spaceinvasion.session.executor._fleet_errors import pick_send_fleet_form_css
from spaceinvasion.session.executor._fleet_radio import match_mission_radio
from spaceinvasion.session.executor._types import ExecutionResult, safe_str
from spaceinvasion.session.executor.build import BuildOps
from spaceinvasion.session.executor.colony import ColonyOps
from spaceinvasion.session.executor.executor import ActionExecutor
from spaceinvasion.session.executor.fleet_dispatch import (
    FleetMissionDispatcher,
    extract_fleet_server_error,
    extract_mission_forbidden_reason,
    is_unattackable_target_html,
)
from spaceinvasion.session.executor.missions import MissionRunner
from spaceinvasion.session.executor.planet_select import PlanetSelector
from spaceinvasion.session.executor.settings_sync import SettingsSyncOps
from spaceinvasion.session.executor.transmitter import TransmitterOps

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
