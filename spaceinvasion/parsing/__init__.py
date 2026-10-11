"""Pure HTML/DOM/JSON game-page parsers for SpaceInvasion Classic (Phase 2.4 / Phase 3).

Layering contract: imports only Python stdlib, ``bs4``, and ``spaceinvasion.core``.
Never imports ``combat``, ``db``, ``planner``, ``session``, or ``runtime``.
"""

from __future__ import annotations

from spaceinvasion.parsing.account_pages import (
    parse_daily_login_bonus,
    parse_transmitter_page,
    parse_universe_research_records,
)
from spaceinvasion.parsing.fleet_page import (
    extract_internal_fleet_planet_coords,
    format_station_ships_spec,
    parse_internal_fleet_slot_status,
    parse_send_fleet_mission_block,
    parse_station_ships_spec,
)
from spaceinvasion.parsing.galaxy import (
    extract_galaxy_coords_from_html,
    extract_galaxy_system_coords,
    extract_galaxy_universe_bounds,
    parse_galaxy_system_html,
)
from spaceinvasion.parsing.inbox import (
    extract_message_folder_links,
    is_non_recycle_fleet_notice_text,
    parse_inbox_messages_html,
)
from spaceinvasion.parsing.models import (
    DEFENSE_SLUG_BY_KEY,
    SHIP_SLUG_BY_KEY,
    BuildingOffer,
    EnergyBalance,
    FleetAlert,
    GalaxySlot,
    GameState,
    InboxMessage,
    JsonObject,
    JsonScalar,
    JsonValue,
    PlanetState,
    QueueJob,
    ResearchOffer,
    Resources,
    SpyReport,
    TutorialStatus,
    TutorialTask,
    UnitOffer,
)
from spaceinvasion.parsing.overview import (
    parse_home_metadata,
    parse_menu_messages,
    parse_menu_status,
    parse_planets_overview_json,
    parse_ticker_rates,
    parse_unread_banner_count,
)
from spaceinvasion.parsing.pages import (
    parse_building_queue,
    parse_buildings_page,
    parse_economy_rates,
    parse_research_page,
    parse_research_queue_timer,
    parse_unit_factory_page,
)
from spaceinvasion.parsing.spy import (
    is_non_spy_notice_text,
    parse_spy_messages_html,
    parse_spy_report_html,
)
from spaceinvasion.parsing.tutorial import parse_tutorial_from_home

__all__ = [
    "BuildingOffer",
    "DEFENSE_SLUG_BY_KEY",
    "EnergyBalance",
    "FleetAlert",
    "GalaxySlot",
    "GameState",
    "InboxMessage",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "PlanetState",
    "QueueJob",
    "ResearchOffer",
    "Resources",
    "SHIP_SLUG_BY_KEY",
    "SpyReport",
    "TutorialStatus",
    "TutorialTask",
    "UnitOffer",
    "extract_galaxy_coords_from_html",
    "extract_galaxy_system_coords",
    "extract_galaxy_universe_bounds",
    "extract_internal_fleet_planet_coords",
    "extract_message_folder_links",
    "format_station_ships_spec",
    "is_non_recycle_fleet_notice_text",
    "is_non_spy_notice_text",
    "parse_building_queue",
    "parse_buildings_page",
    "parse_daily_login_bonus",
    "parse_economy_rates",
    "parse_galaxy_system_html",
    "parse_home_metadata",
    "parse_inbox_messages_html",
    "parse_internal_fleet_slot_status",
    "parse_menu_messages",
    "parse_menu_status",
    "parse_planets_overview_json",
    "parse_research_page",
    "parse_research_queue_timer",
    "parse_send_fleet_mission_block",
    "parse_spy_messages_html",
    "parse_spy_report_html",
    "parse_station_ships_spec",
    "parse_ticker_rates",
    "parse_transmitter_page",
    "parse_tutorial_from_home",
    "parse_unit_factory_page",
    "parse_universe_research_records",
    "parse_unread_banner_count",
]
