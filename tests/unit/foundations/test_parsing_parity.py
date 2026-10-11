"""Byte-for-byte golden parity tests proving ``spaceinvasion.parsing`` reproduces all 23 parser goldens."""

from __future__ import annotations

from collections.abc import Callable
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

from spaceinvasion import parsing
from spaceinvasion.combat import fleet as legacy_fleet
from spaceinvasion.core import state as legacy_state
from spaceinvasion.session.executor import ActionExecutor
from tests.goldens.scenarios import GOLDEN_EPOCH

REPO_ROOT = Path(__file__).resolve().parents[3]
PAGES_DIR = REPO_ROOT / "tests" / "goldens" / "pages"
REFERENCE_PARSERS_DIR = REPO_ROOT / "tests" / "goldens" / "reference" / "parsers"
OWN_COORDS = "03:120:07"
_MISSION_KINDS = ("ATTACK", "SPY", "RECYCLE", "TRANSPORT", "STATION", "COLONIZE", "ESCAPE")


def _load_capture_module() -> ModuleType:
    cached = sys.modules.get("capture_goldens")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("capture_goldens", REPO_ROOT / "scripts" / "capture_goldens.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["capture_goldens"] = module
    spec.loader.exec_module(module)
    return module


def _page(name: str) -> str:
    return (PAGES_DIR / name).read_text(encoding="utf-8")


def _overview_levels() -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    overview = json.loads(_page("planets_overview.json"))
    home = overview["planets"][0]
    buildings = {k: int(v["level"]) for k, v in home["buildings"].items()}
    ships = {k: int(v["level"]) for k, v in home["ships"].items()}
    researches = {k: int(v["level"]) for k, v in overview["researches"].items()}
    return buildings, ships, researches


def _case_home() -> object:
    html = _page("home.html")
    planet_id, speedup, newbie, queues, alerts = parsing.parse_home_metadata(
        html, now_ts=GOLDEN_EPOCH, origin_coords=OWN_COORDS
    )
    return {
        "home_metadata": {
            "active_planet_id": planet_id,
            "speedup_factor": speedup,
            "newbie_protection": newbie,
            "queues": queues,
            "fleet_alerts": alerts,
        },
        "tutorial": parsing.parse_tutorial_from_home(html),
        "ticker_rates": parsing.parse_ticker_rates(html),
        "daily_login_bonus": parsing.parse_daily_login_bonus(html),
    }


def _case_menu() -> object:
    html = _page("menu.html")
    unread, points, rank = parsing.parse_menu_status(html)
    return {
        "menu_status": {"unread_messages": unread, "account_points": points, "account_rank": rank},
        "menu_messages": parsing.parse_menu_messages(html),
        "unread_banner_count": parsing.parse_unread_banner_count(html),
        "message_folder_links": parsing.extract_message_folder_links(html),
    }


def _case_buildings() -> object:
    html = _page("buildings.html")
    levels, _, _ = _overview_levels()
    return {
        "offers_with_levels": parsing.parse_buildings_page(html, levels),
        "offers_without_levels": parsing.parse_buildings_page(html),
        "building_queue": parsing.parse_building_queue(html, now_ts=GOLDEN_EPOCH, origin_coords=OWN_COORDS),
    }


def _case_research() -> object:
    html = _page("research.html")
    _, _, researches = _overview_levels()
    rem_txt, rem_s, fin_ts, detail = parsing.parse_research_queue_timer(html, now_ts=GOLDEN_EPOCH)
    return {
        "offers": parsing.parse_research_page(html, researches),
        "queue_timer": {
            "remaining_text": rem_txt,
            "remaining_seconds": rem_s,
            "finish_ts": fin_ts,
            "detail": detail,
        },
    }


def _case_weapon_factory() -> object:
    html = _page("weapon_factory.html")
    _, ships, _ = _overview_levels()
    return {
        "ship_offers": parsing.parse_unit_factory_page(html, unit_type="ship", owned_counts=ships),
        "ship_offers_without_counts": parsing.parse_unit_factory_page(html, unit_type="ship"),
    }


def _make_galaxy_case(name: str, url: str = "") -> Callable[[], object]:
    def _run() -> object:
        html = _page(name)
        max_galaxy, max_system = parsing.extract_galaxy_universe_bounds(html)
        return {
            "system_coords": parsing.extract_galaxy_system_coords(html, url=url),
            "coords_from_html": parsing.extract_galaxy_coords_from_html(html, url),
            "universe_bounds": {"max_galaxy": max_galaxy, "max_system": max_system},
            "slots_auto_coords": parsing.parse_galaxy_system_html(html, url=url),
            "slots_explicit_coords": parsing.parse_galaxy_system_html(html, galaxy=3, system=120),
        }

    return _run


def _make_spy_report_case(name: str) -> Callable[[], object]:
    def _run() -> object:
        html = _page(name)
        return {
            "report": parsing.parse_spy_report_html(html),
            "report_with_target_coords": parsing.parse_spy_report_html(html, target_coords="03:120:03"),
        }

    return _run


def _make_messages_case(name: str) -> Callable[[], object]:
    def _run() -> object:
        html = _page(name)
        return {
            "inbox": parsing.parse_inbox_messages_html(html, folder_type="inbox", exclude_coords=(OWN_COORDS,)),
            "inbox_fleet_folder": parsing.parse_inbox_messages_html(html, folder_type="fleet"),
            "spy_messages": parsing.parse_spy_messages_html(html, exclude_coords=(OWN_COORDS,)),
            "spy_messages_no_exclusion": parsing.parse_spy_messages_html(html),
            "message_folder_links": parsing.extract_message_folder_links(html),
            "unread_banner_count": parsing.parse_unread_banner_count(html),
        }

    return _run


def _make_fleet_dispatch_case(name: str) -> Callable[[], object]:
    def _run() -> object:
        html = _page(name)
        slot_status = parsing.parse_internal_fleet_slot_status(html)
        blocks: dict[str, object] = {}
        for kind in _MISSION_KINDS:
            block = parsing.parse_send_fleet_mission_block(html, kind)
            blocks[kind] = (
                None if block is None else {"reason": block[0], "cooldown_seconds": block[1], "is_bash_block": block[2]}
            )
        return {
            "fleet_slot_status": (
                None if slot_status is None else {"available": slot_status[0], "filled": slot_status[1]}
            ),
            "mission_blocks": blocks,
        }

    return _run


PARSING_GOLDEN_CASES: dict[str, Callable[[], object]] = {
    "planets_overview": lambda: parsing.parse_planets_overview_json(_page("planets_overview.json")),
    "home": _case_home,
    "menu": _case_menu,
    "buildings": _case_buildings,
    "research": _case_research,
    "weapon_factory": _case_weapon_factory,
    "economy": lambda: {"economy_rates": parsing.parse_economy_rates(_page("economy.html"))},
    "galaxy_classic_table": _make_galaxy_case("galaxy_classic_table.html"),
    "galaxy_icon_table": _make_galaxy_case("galaxy_icon_table.html"),
    "galaxy_modern_div": _make_galaxy_case("galaxy_modern_div.html"),
    "galaxy_2d_map": _make_galaxy_case(
        "galaxy_2d_map.html",
        url="https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&g=3&s=121",
    ),
    "spy_report_text": _make_spy_report_case("spy_report_text.html"),
    "spy_report_json": _make_spy_report_case("spy_report_json.html"),
    "spy_messages_fake_game": _make_messages_case("spio_messages.html"),
    "inbox_multirow": _make_messages_case("inbox_multirow.html"),
    "inbox_expanded": _make_messages_case("inbox_expanded.html"),
    "fleet_dispatch": _make_fleet_dispatch_case("fleet_dispatch.html"),
    "fleet_dispatch_mission_step": _make_fleet_dispatch_case("fleet_step2.html"),
    "fleet_dispatch_blocked": _make_fleet_dispatch_case("send_fleet_blocked.html"),
    "transmitter_free": lambda: parsing.parse_transmitter_page(_page("transmitter_free.html")),
    "transmitter_paid": lambda: parsing.parse_transmitter_page(_page("transmitter_paid.html")),
    "daily_bonus": lambda: parsing.parse_daily_login_bonus(_page("daily_bonus.html")),
    "universe_research_records": lambda: parsing.parse_universe_research_records(
        _page("universe_research_records.html")
    ),
}


def test_all_23_golden_reference_files_are_covered() -> None:
    recorded = sorted(p.stem for p in REFERENCE_PARSERS_DIR.glob("*.json"))
    assert len(recorded) == 23
    assert sorted(PARSING_GOLDEN_CASES) == recorded


@pytest.mark.parametrize("case_name", sorted(PARSING_GOLDEN_CASES))
def test_parsing_package_reproduces_golden_byte_for_byte(case_name: str) -> None:
    capture = _load_capture_module()
    expected = (REFERENCE_PARSERS_DIR / f"{case_name}.json").read_text(encoding="utf-8")
    actual = capture.render(PARSING_GOLDEN_CASES[case_name]())
    assert actual == expected, capture.diff_text(expected, actual, f"parsers/{case_name}.json")


def test_additional_parser_parity_beyond_goldens() -> None:
    """Verify edge cases not directly covered by the 23 golden HTML pages."""
    # Station spec parsing/formatting parity
    spec = "Station -> [03:120:07] [15x s11, 6x s4, 2x s9]"
    assert parsing.parse_station_ships_spec(spec) == legacy_fleet.parse_station_ships_spec(spec)
    ships = {"s11": 15, "s4": 6, "s9": 2}
    assert parsing.format_station_ships_spec(ships) == legacy_fleet.format_station_ships_spec(ships)

    # Fleet planet coords extraction parity
    fleet_hdr = '<div id="planet-coords">[3:120:7]</div>'
    assert parsing.extract_internal_fleet_planet_coords(
        fleet_hdr
    ) == ActionExecutor._extract_internal_fleet_planet_coords(fleet_hdr)
    fleet_only = "<p>Only flights from here: Homeworld [3:120:7]</p>"
    assert parsing.extract_internal_fleet_planet_coords(
        fleet_only
    ) == ActionExecutor._extract_internal_fleet_planet_coords(fleet_only)

    # Non-spy & non-recycle notice classifiers parity
    notices = [
        "Your fleet is returning from duty [03:120:07]",
        "Espionage report on Quiet Rock [03:120:03]",
        "Your fleet was re-stationed with 5 Recycler",
        "Space rubble [03:120:03] recycling report: harvested 1000 Metal",
    ]
    for txt in notices:
        assert parsing.is_non_spy_notice_text(txt) == legacy_fleet.is_non_spy_notice_text(txt)
        assert parsing.is_non_recycle_fleet_notice_text(txt) == legacy_fleet.is_non_recycle_fleet_notice_text(txt)

    # Galaxy #json-element rubblefield1 precedence over stale rubblefield
    rubble_html = """
    <div id="json-element">{
      "position": {"galaxy": 3, "system": 120},
      "planets": {
        "2": {"pla": 2, "uid": 0, "colonize": [280, 320], "temperature": 25,
              "rubblefield1": [0, 0, 0, 0, 0], "rubblefield": [99999, 99999, 0, 0]},
        "5": {"pla": 5, "uid": 10, "name": "Mine", "uname": "Rival",
              "rubblefield1": [12000, 8000, 0, 0, 1]}
      }
    }</div>
    """
    capture = _load_capture_module()
    assert capture.render(parsing.parse_galaxy_system_html(rubble_html)) == capture.render(
        legacy_fleet.parse_galaxy_system_html(rubble_html)
    )

    # Home faData outbound+return pairing + cargo parity
    fadata_html = """
    <script>
    var faData = [
      {"title": "Own fleets", "rows": [
        {"id": 101, "t": 300, "a": "TRANSPORT", "start": "03:120:07", "dest": "03:120:08",
         "tt": "10 Large transport<hr/>5.000 Pig iron<br/>2.000 Metal"},
        {"id": 102, "t": 600, "a": "TRANSPORT", "return": true, "start": "03:120:07", "dest": "03:120:08",
         "tt": "10 Large transport"}
      ]}
    ];
    </script>
    """
    assert capture.render(
        parsing.parse_home_metadata(fadata_html, now_ts=GOLDEN_EPOCH, origin_coords=OWN_COORDS)
    ) == capture.render(legacy_state.parse_home_metadata(fadata_html, now_ts=GOLDEN_EPOCH, origin_coords=OWN_COORDS))
