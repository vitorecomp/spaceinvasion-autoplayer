"""Snapshot page enrichment helpers for buildings, economy, research, shipyard, and planet selection."""

from __future__ import annotations

from dataclasses import replace
import re

from bs4 import BeautifulSoup, Tag

from backend.core.model import normalize_planet_type
from backend.parsing.models import GameState, PlanetState, QueueJob, ResearchOffer, TutorialStatus
from backend.parsing.overview import parse_ticker_rates
from backend.parsing.pages import (
    parse_building_queue,
    parse_buildings_page,
    parse_economy_rates,
    parse_research_page,
    parse_research_queue_timer,
    parse_unit_factory_page,
)
from backend.parsing.tutorial import parse_tutorial_from_home
from backend.session.client._protocols import is_real_selenium_driver
from backend.session.client._state import ClientSessionState
from backend.session.client.frames import FrameNavigator
from backend.session.limiter import is_server_rate_limit_html

_SELECTED_PLANET_RE: re.Pattern[str] = re.compile(r"newplanet=(\d+)", re.IGNORECASE | re.ASCII)


def extract_selected_planet_id(html: str, url: str = "") -> int | None:
    """Extract the currently selected planet ID from `<select name="planet">` options."""
    if not html or "internalfleet" in (url or "").lower() or "newplanet=" not in html.lower():
        return None
    soup = BeautifulSoup(html, "html.parser")
    select_el = soup.find("select", attrs={"name": "planet"})
    if not isinstance(select_el, Tag):
        return None
    for opt in select_el.find_all("option"):
        if not isinstance(opt, Tag):
            continue
        val = str(opt.get("value") or "")
        if "action=internalfleet" in val.lower():
            return None
        if opt.has_attr("selected"):
            match = _SELECTED_PLANET_RE.search(val)
            if match:
                pid_s = match.group(1)
                return int(pid_s) if (pid_s.isascii() and pid_s.isdigit() and len(pid_s) <= 18) else None
    return None


def resolve_planet_idx(
    planets: list[PlanetState],
    active_pid: int | None,
    html: str,
    action: str,
    active_idx: int | None,
) -> tuple[int | None, int | None]:
    """Infer active planet ID and index from `<select name="planet">` when still unknown."""
    if active_pid is not None:
        return active_pid, active_idx
    sel_pid = extract_selected_planet_id(html, action)
    if sel_pid is None:
        return active_pid, active_idx
    new_idx = next((i for i, p in enumerate(planets) if p.planet_id == sel_pid), active_idx)
    return sel_pid, new_idx


def apply_passive_metal_ticker(
    planets: list[PlanetState],
    active_idx: int | None,
    b_html: str,
    home_html: str,
    raw_main_html: str,
    main_url: str,
) -> None:
    """Detect Metal production enabled/disabled from `<form id="ress">` ticker on zero extra views."""
    if active_idx is None or not planets:
        return
    cur_ap = planets[active_idx]
    if normalize_planet_type(cur_ap) in ("asteroid", "raid") or cur_ap.building_level(2) < 1:
        return
    ticker_src = b_html or home_html
    if not ticker_src and raw_main_html:
        raw_sel = extract_selected_planet_id(raw_main_html, main_url)
        if raw_sel is None or raw_sel == cur_ap.planet_id:
            ticker_src = raw_main_html
    ticker = parse_ticker_rates(ticker_src)
    if "metal" not in ticker:
        return
    econ = dict(cur_ap.economy_rates or {})
    metal_storage_full = cur_ap.storage.metal > 0 and cur_ap.resources.metal >= cur_ap.storage.metal
    has_pig_feed = cur_ap.resources.pig_iron > 0 or ticker.get("pig_iron", 0.0) > 0.0 or cur_ap.building_level(1) >= 1
    if ticker["metal"] <= 0.0 and cur_ap.energy.produced > 0 and not metal_storage_full and has_pig_feed:
        if econ.get("resource2") != 0:
            econ["resource2"] = 0
            planets[active_idx] = replace(cur_ap, economy_rates=econ)
    elif ticker["metal"] > 0.0 and econ.get("resource2") == 0:
        econ["resource2"] = 100
        planets[active_idx] = replace(cur_ap, economy_rates=econ)


def enrich_buildings(
    state: ClientSessionState,
    frames: FrameNavigator,
    planets: list[PlanetState],
    active_pid: int | None,
    active_idx: int | None,
    main_url: str,
    raw_main_html: str,
    *,
    include_home: bool,
    has_portal_redirect: bool,
    priority: str,
    merged_queues: list[QueueJob],
    tutorial: TutorialStatus | None,
) -> tuple[str, int | None, int | None, list[QueueJob], TutorialStatus | None]:
    """Fetch `internalBuildings` (reusing live DOM when safe) and update planet building offers/queues."""
    if active_idx is None or not planets:
        return "", active_pid, active_idx, merged_queues, tutorial
    can_reuse_b = (
        not include_home
        and is_real_selenium_driver(state.driver)
        and "action=internalBuildings" in main_url
        and "internalbuilditem" not in main_url.lower()
        and bool(raw_main_html.strip())
        and not is_server_rate_limit_html(raw_main_html)
        and not has_portal_redirect
    )
    b_html = raw_main_html if can_reuse_b else frames.navigate_menu("internalBuildings", priority=priority)
    active_pid, active_idx = resolve_planet_idx(planets, active_pid, b_html, "internalBuildings", active_idx)
    if tutorial is None:
        tutorial = parse_tutorial_from_home(b_html)
    b_queues = parse_building_queue(b_html)
    if b_queues:
        merged_queues = [q for q in merged_queues if q.category != "building"] + list(b_queues)
    if active_idx is not None:
        planet = planets[active_idx]
        planets[active_idx] = replace(planet, building_offers=parse_buildings_page(b_html, planet.buildings))
    return b_html, active_pid, active_idx, merged_queues, tutorial


def enrich_economy(
    frames: FrameNavigator,
    planets: list[PlanetState],
    active_pid: int | None,
    active_idx: int | None,
    *,
    priority: str,
) -> tuple[int | None, int | None]:
    """Fetch `internalEconomy` dropdown rates (`resource1`..`resource6`) for normal planets."""
    if active_idx is None or not planets or normalize_planet_type(planets[active_idx]) in ("asteroid", "raid"):
        return active_pid, active_idx
    e_html = frames.navigate_menu("internalEconomy", priority=priority)
    active_pid, active_idx = resolve_planet_idx(planets, active_pid, e_html, "internalEconomy", active_idx)
    econ_rates = parse_economy_rates(e_html)
    if econ_rates and active_idx is not None:
        planets[active_idx] = replace(planets[active_idx], economy_rates=econ_rates)
    return active_pid, active_idx


def enrich_research(
    frames: FrameNavigator,
    state: GameState,
    planets: list[PlanetState],
    active_pid: int | None,
    active_idx: int | None,
    *,
    priority: str,
    merged_queues: list[QueueJob],
    tutorial: TutorialStatus | None,
) -> tuple[tuple[ResearchOffer, ...], int | None, int | None, list[QueueJob]]:
    """Fetch `internalResearch` cards when Research Lab exists and slot is idle or needed by tutorial."""
    active_p = planets[active_idx] if active_idx is not None else None
    research_busy = any(q.category == "research" for q in merged_queues)
    tut_needs_research = bool(tutorial and any(t.category == "research" and not t.completed for t in tutorial.tasks))
    if active_p is None or active_p.building_level(8) < 1 or (research_busy and not tut_needs_research):
        return state.research_offers, active_pid, active_idx, merged_queues

    r_html = frames.navigate_menu("internalResearch", priority=priority)
    active_pid, active_idx = resolve_planet_idx(planets, active_pid, r_html, "internalResearch", active_idx)
    research_offers = parse_research_page(r_html, state.researches)
    active_res = [o for o in research_offers if o.active]
    if active_res:
        r_rem_txt, r_rem_s, r_fin_ts, r_det = parse_research_queue_timer(r_html)
        prev_home_res = next((q for q in merged_queues if q.category == "research"), None)
        if r_fin_ts is None and prev_home_res is not None:
            r_rem_txt = r_rem_txt or prev_home_res.remaining_text
            r_rem_s = prev_home_res.remaining_seconds
            r_fin_ts = prev_home_res.finish_ts
            r_det = r_det or prev_home_res.detail
        res_jobs = [
            QueueJob(
                category="research",
                name=o.name,
                target_level=o.target_level,
                remaining_text=r_rem_txt,
                remaining_seconds=r_rem_s,
                finish_ts=r_fin_ts,
                queue_slot=1,
                detail=r_det or "Researching",
            )
            for o in active_res
        ]
        merged_queues = [q for q in merged_queues if q.category != "research"] + res_jobs
    elif research_offers:
        merged_queues = [q for q in merged_queues if q.category != "research"]
    return research_offers, active_pid, active_idx, merged_queues


def enrich_shipyard(
    frames: FrameNavigator,
    planets: list[PlanetState],
    active_pid: int | None,
    active_idx: int | None,
    *,
    priority: str,
) -> tuple[int | None, int | None]:
    """Fetch `internalWeaponFactory` ship offers when Weapons Factory L1+ exists."""
    active_p = planets[active_idx] if active_idx is not None else None
    if active_p is None or active_p.building_level(9) < 1:
        return active_pid, active_idx
    wf_html = frames.navigate_menu("internalWeaponFactory", priority=priority)
    active_pid, active_idx = resolve_planet_idx(planets, active_pid, wf_html, "internalWeaponFactory", active_idx)
    if active_idx is not None:
        active_p = planets[active_idx]
        ship_offers = parse_unit_factory_page(wf_html, unit_type="ship", owned_counts=active_p.ships)
        planets[active_idx] = replace(planets[active_idx], ship_offers=ship_offers)
    return active_pid, active_idx
