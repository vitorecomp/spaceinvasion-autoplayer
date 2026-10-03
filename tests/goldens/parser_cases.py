"""Parser cases of the golden suite: which parser runs over which static page.

The pages in `tests/goldens/pages/` are synthetic. Half were rendered once by the fake
game server of `tests/e2e/harness.py` and then frozen as files, the rest are written
by hand in the page dialects the unit tests document. A page never changes when the
fake server changes, so a parser golden only moves when a parser does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from spaceinvasion.combat import fleet
from spaceinvasion.core import state
from spaceinvasion.session.executor import ActionExecutor
from tests.goldens.scenarios import GOLDEN_EPOCH

OWN_COORDS = "03:120:07"
_MISSION_KINDS = ("ATTACK", "SPY", "RECYCLE", "TRANSPORT", "STATION", "COLONIZE", "ESCAPE")

ParserCase = Callable[[Path], Any]


def _page(pages: Path, name: str) -> str:
    return (pages / name).read_text(encoding="utf-8")


def _planets_overview(pages: Path) -> Any:
    return state.parse_planets_overview_json(_page(pages, "planets_overview.json"))


def _home(pages: Path) -> Any:
    html = _page(pages, "home.html")
    planet_id, speedup, newbie, queues, alerts = state.parse_home_metadata(
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
        "tutorial": state.parse_tutorial_from_home(html),
        "ticker_rates": state.parse_ticker_rates(html),
        "daily_login_bonus": state.parse_daily_login_bonus(html),
    }


def _menu(pages: Path) -> Any:
    html = _page(pages, "menu.html")
    unread, points, rank = state.parse_menu_status(html)
    return {
        "menu_status": {"unread_messages": unread, "account_points": points, "account_rank": rank},
        "menu_messages": state.parse_menu_messages(html),
        "unread_banner_count": state.parse_unread_banner_count(html),
        "message_folder_links": fleet.extract_message_folder_links(html),
    }


def _overview_levels(pages: Path) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    overview = json.loads(_page(pages, "planets_overview.json"))
    home = overview["planets"][0]
    buildings = {k: int(v["level"]) for k, v in home["buildings"].items()}
    ships = {k: int(v["level"]) for k, v in home["ships"].items()}
    researches = {k: int(v["level"]) for k, v in overview["researches"].items()}
    return buildings, ships, researches


def _buildings(pages: Path) -> Any:
    html = _page(pages, "buildings.html")
    levels, _, _ = _overview_levels(pages)
    return {
        "offers_with_levels": state.parse_buildings_page(html, levels),
        "offers_without_levels": state.parse_buildings_page(html),
        "building_queue": state.parse_building_queue(html, now_ts=GOLDEN_EPOCH, origin_coords=OWN_COORDS),
    }


def _research(pages: Path) -> Any:
    html = _page(pages, "research.html")
    _, _, researches = _overview_levels(pages)
    remaining_text, remaining_seconds, finish_ts, detail = state.parse_research_queue_timer(html, now_ts=GOLDEN_EPOCH)
    return {
        "offers": state.parse_research_page(html, researches),
        "queue_timer": {
            "remaining_text": remaining_text,
            "remaining_seconds": remaining_seconds,
            "finish_ts": finish_ts,
            "detail": detail,
        },
    }


def _weapon_factory(pages: Path) -> Any:
    html = _page(pages, "weapon_factory.html")
    _, ships, _ = _overview_levels(pages)
    return {
        "ship_offers": state.parse_unit_factory_page(html, unit_type="ship", owned_counts=ships),
        "ship_offers_without_counts": state.parse_unit_factory_page(html, unit_type="ship"),
    }


def _economy(pages: Path) -> Any:
    return {"economy_rates": state.parse_economy_rates(_page(pages, "economy.html"))}


def _galaxy(name: str, url: str = "") -> ParserCase:
    def run(pages: Path) -> Any:
        html = _page(pages, name)
        max_galaxy, max_system = fleet.extract_galaxy_universe_bounds(html)
        return {
            "system_coords": fleet.extract_galaxy_system_coords(html, url=url),
            "coords_from_html": fleet.extract_galaxy_coords_from_html(html, url),
            "universe_bounds": {"max_galaxy": max_galaxy, "max_system": max_system},
            "slots_auto_coords": fleet.parse_galaxy_system_html(html, url=url),
            "slots_explicit_coords": fleet.parse_galaxy_system_html(html, galaxy=3, system=120),
        }

    return run


def _spy_report(name: str) -> ParserCase:
    def run(pages: Path) -> Any:
        html = _page(pages, name)
        return {
            "report": fleet.parse_spy_report_html(html),
            "report_with_target_coords": fleet.parse_spy_report_html(html, target_coords="03:120:03"),
        }

    return run


def _messages(name: str) -> ParserCase:
    def run(pages: Path) -> Any:
        html = _page(pages, name)
        return {
            "inbox": fleet.parse_inbox_messages_html(html, folder_type="inbox", exclude_coords=(OWN_COORDS,)),
            "inbox_fleet_folder": fleet.parse_inbox_messages_html(html, folder_type="fleet"),
            "spy_messages": fleet.parse_spy_messages_html(html, exclude_coords=(OWN_COORDS,)),
            "spy_messages_no_exclusion": fleet.parse_spy_messages_html(html),
            "message_folder_links": fleet.extract_message_folder_links(html),
            "unread_banner_count": state.parse_unread_banner_count(html),
        }

    return run


def _fleet_dispatch(name: str) -> ParserCase:
    def run(pages: Path) -> Any:
        html = _page(pages, name)
        slot_status = ActionExecutor._parse_internal_fleet_slot_status(html)
        blocks: dict[str, Any] = {}
        for kind in _MISSION_KINDS:
            block = fleet.parse_send_fleet_mission_block(html, kind)
            blocks[kind] = (
                None if block is None else {"reason": block[0], "cooldown_seconds": block[1], "is_bash_block": block[2]}
            )
        return {
            "fleet_slot_status": (
                None if slot_status is None else {"available": slot_status[0], "filled": slot_status[1]}
            ),
            "mission_blocks": blocks,
        }

    return run


def _transmitter(name: str) -> ParserCase:
    return lambda pages: state.parse_transmitter_page(_page(pages, name))


def _daily_bonus(pages: Path) -> Any:
    return state.parse_daily_login_bonus(_page(pages, "daily_bonus.html"))


def _universe_research_records(pages: Path) -> Any:
    return state.parse_universe_research_records(_page(pages, "universe_research_records.html"))


def parser_cases() -> dict[str, ParserCase]:
    return {
        "planets_overview": _planets_overview,
        "home": _home,
        "menu": _menu,
        "buildings": _buildings,
        "research": _research,
        "weapon_factory": _weapon_factory,
        "economy": _economy,
        "galaxy_classic_table": _galaxy("galaxy_classic_table.html"),
        "galaxy_icon_table": _galaxy("galaxy_icon_table.html"),
        "galaxy_modern_div": _galaxy("galaxy_modern_div.html"),
        "galaxy_2d_map": _galaxy(
            "galaxy_2d_map.html",
            url="https://spaceinvasion.bitmeup.com/indexInternal.es?action=internalGalaxy&g=3&s=121",
        ),
        "spy_report_text": _spy_report("spy_report_text.html"),
        "spy_report_json": _spy_report("spy_report_json.html"),
        "spy_messages_fake_game": _messages("spio_messages.html"),
        "inbox_multirow": _messages("inbox_multirow.html"),
        "inbox_expanded": _messages("inbox_expanded.html"),
        "fleet_dispatch": _fleet_dispatch("fleet_dispatch.html"),
        "fleet_dispatch_mission_step": _fleet_dispatch("fleet_step2.html"),
        "fleet_dispatch_blocked": _fleet_dispatch("send_fleet_blocked.html"),
        "transmitter_free": _transmitter("transmitter_free.html"),
        "transmitter_paid": _transmitter("transmitter_paid.html"),
        "daily_bonus": _daily_bonus,
        "universe_research_records": _universe_research_records,
    }
