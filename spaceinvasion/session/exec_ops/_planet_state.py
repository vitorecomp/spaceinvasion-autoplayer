"""Shared helper protocols, pacing, settings-nav fallback, and post-action state builder for `exec_ops`."""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
from dataclasses import replace
import re
from typing import Protocol, cast

from bs4 import BeautifulSoup, Tag

from spaceinvasion.core import catalog
from spaceinvasion.core.coercion import coerce_int
from spaceinvasion.core.model import PlannedAction, normalize_planet_type
from spaceinvasion.parsing.models import GameState, PlanetState, QueueJob
from spaceinvasion.parsing.overview import parse_home_metadata
from spaceinvasion.parsing.pages import (
    parse_building_queue,
    parse_buildings_page,
    parse_research_page,
    parse_unit_factory_page,
)
from spaceinvasion.parsing.tutorial import parse_tutorial_from_home
from spaceinvasion.session.auth import SpaceInvasionAuthError
from spaceinvasion.session.browser_client._protocols import SELENIUM_NAV_ERRORS
from spaceinvasion.session.exec_ops._protocols import (
    BrowserClientLike,
    ExecutorContext,
    PlannedActionLike,
    coerce_planned_action,
)
from spaceinvasion.session.exec_ops._types import call_with_interactive_priority
from spaceinvasion.session.limiter import PageViewLimiter, RateLimitError

type ActionInput = PlannedAction | PlannedActionLike


def as_planned_action(action: ActionInput) -> PlannedAction:
    """Return a canonical `core.model.PlannedAction` from either canonical or legacy action objects."""
    if isinstance(action, PlannedAction):
        return action
    return coerce_planned_action(action)


_NEWPLANET_ID_RE = re.compile(r"newplanet=(\d+)", re.IGNORECASE | re.ASCII)
_EMPTY_BUILD_QUEUE_RE = re.compile(r"construction\s+queue\s*\(\s*0\b", re.IGNORECASE)
_SECURITY_ERR_TOKENS: tuple[str, ...] = ("captcha", "antibot", "ratelimit", "auth")
_OPTIONAL_CALL_ERRORS: tuple[type[BaseException], ...] = (AttributeError, *SELENIUM_NAV_ERRORS)


class HasReadMenuStatus(Protocol):
    """Optional `client.read_menu_status()` capability."""

    def read_menu_status(self) -> tuple[dict[str, int], int | None, int | None]: ...


class HasInternalUrlBuilder(Protocol):
    """Optional `client._build_internal_url` + `client._resolve_sid_query` capability."""

    def _build_internal_url(self, query: str) -> str: ...

    def _resolve_sid_query(self) -> str: ...


class HasSelectPlanet(Protocol):
    """Optional `client.select_planet()` capability."""

    def select_planet(self, planet_id: int, *, priority: str = "normal") -> object: ...


def extract_selected_planet_id(html: str, url: str = "") -> int | None:
    """Extract the selected `newPlanet=<id>` from `<select name='planet'>` in non-fleet HTML."""
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
            m = _NEWPLANET_ID_RE.search(val)
            if m:
                pid_s = m.group(1)
                if pid_s.isascii() and pid_s.isdigit() and len(pid_s) <= 18:
                    return int(pid_s)
                return None
    return None


def record_observed_planet(ctx: ExecutorContext, planet_id: int | None, *, confirmed: bool | None) -> None:
    """Update `ctx` observed active-planet tracking with exact legacy semantics."""
    if isinstance(planet_id, int) and not isinstance(planet_id, bool):
        ctx.observed_active_planet_id = planet_id
    elif confirmed is False:
        ctx.observed_active_planet_id = None
    ctx.observed_active_planet_confirmed = confirmed


def confirmed_planet_mismatch_id(ctx: ExecutorContext, planet_id: int) -> int | None:
    """Return observed planet ID when confirmed active planet differs from `planet_id`."""
    if ctx.confirmed_planet_mismatch(planet_id):
        return ctx.observed_active_planet_id
    return None


def pace_client_action(ctx: ExecutorContext, client: BrowserClientLike, *, priority: str = "interactive") -> None:
    """Pace an interactive action using `ctx` / `client` page-view limiter safely."""
    try:
        limiter: object = client.page_limiter
    except AttributeError:
        limiter = None
    if isinstance(limiter, PageViewLimiter):
        ctx.pace_action(client)
        return
    try:
        client._pace(priority=priority)
    except AttributeError:
        ctx.pace_action(None)
        return
    ctx.last_action_at = ctx.clock()


def sleep_action_delay(ctx: ExecutorContext) -> None:
    """Sleep a randomized human-like pre-click delay using `ctx`'s injectable RNG and sleeper."""
    low, high = ctx.action_delay_range
    if high > 0:
        ctx.sleeper(ctx.rng(low, high))


def homeworld_planet_id_from_state(state: GameState) -> int | None:
    """Return the Pos. 1 Homeworld `planet_id` (first normal planet in `state.planets`)."""
    for planet in state.planets:
        if normalize_planet_type(planet) == "normal":
            pid = coerce_int(planet.planet_id, 0)
            if pid > 0:
                return pid
    if state.planets:
        pid0 = coerce_int(state.planets[0].planet_id, 0)
        if pid0 > 0:
            return pid0
    return None


def read_planets_overview_safe(client: BrowserClientLike) -> object:
    """Call `client.read_planets_overview(refresh=True, priority='interactive')` safely."""
    return call_with_interactive_priority(client.read_planets_overview, refresh=True)


def try_build_internal_url(client: BrowserClientLike, query: str) -> str | None:
    """Invoke optional `client._build_internal_url(query)` safely on real clients or mocks."""
    try:
        res = cast(HasInternalUrlBuilder, client)._build_internal_url(query)
    except _OPTIONAL_CALL_ERRORS:
        return None
    return res if isinstance(res, str) and res else None


def try_resolve_sid_query(client: BrowserClientLike) -> str:
    """Invoke optional `client._resolve_sid_query()` safely on real clients or mocks."""
    try:
        res = cast(HasInternalUrlBuilder, client)._resolve_sid_query()
    except _OPTIONAL_CALL_ERRORS:
        return ""
    return res if isinstance(res, str) else ""


def try_select_planet_on_client(client: BrowserClientLike, planet_id: int) -> None:
    """Invoke optional `client.select_planet(planet_id)` safely on real clients or mocks."""
    with contextlib.suppress(AttributeError, *SELENIUM_NAV_ERRORS):
        call_with_interactive_priority(cast(HasSelectPlanet, client).select_planet, planet_id)


def navigate_settings_with_fallback(client: BrowserClientLike) -> str:
    """Navigate to `internalSettings`, falling back to direct frame URL only on non-security nav errors."""
    try:
        res = call_with_interactive_priority(client.navigate_menu, "internalSettings")
        return res if isinstance(res, str) else ""
    except (SpaceInvasionAuthError, RateLimitError):
        raise
    except SELENIUM_NAV_ERRORS as nav_exc:
        low_exc = type(nav_exc).__name__.lower()
        if any(tok in low_exc for tok in _SECURITY_ERR_TOKENS):
            raise
        try:
            sid_q = str(cast(HasInternalUrlBuilder, client)._resolve_sid_query() or "")
            q_str = "action=internalSettings" + (f"&{sid_q}" if sid_q else "")
            tgt_url = cast(HasInternalUrlBuilder, client)._build_internal_url(q_str)
            tok = client._mark_frame_navigation("F_Hauptframe")
            client._switch_frame("F_Hauptframe")
            client.driver.execute_script("location.href = arguments[0];", tgt_url)
            html = client._wait_frame_ready("F_Hauptframe", expected_action="internalSettings", nav_token=tok)
        except AttributeError:
            raise nav_exc from None
        return html if isinstance(html, str) else ""


def _read_menu_status_overlay(
    client: BrowserClientLike,
    prev_state: GameState | None,
) -> tuple[dict[str, int], int | None, int | None]:
    unread: dict[str, int] = dict(prev_state.unread_messages) if prev_state is not None else {}
    account_points = prev_state.account_points if prev_state is not None else None
    account_rank = prev_state.account_rank if prev_state is not None else None
    with contextlib.suppress(AttributeError, *SELENIUM_NAV_ERRORS):
        menu_res = cast(HasReadMenuStatus, client).read_menu_status()
        if isinstance(menu_res, tuple) and len(menu_res) == 3 and isinstance(menu_res[0], dict):
            unread = dict(menu_res[0])
            if menu_res[1] is not None:
                account_points = menu_res[1]
            if menu_res[2] is not None:
                account_rank = menu_res[2]
    return unread, account_points, account_rank


def _resolve_active_planet_id(
    ctx: ExecutorContext,
    prev_state: GameState | None,
    action: ActionInput,
    updated_overview: GameState,
    planet_ids: set[int],
    parsed_pid: int | None,
) -> int | None:
    if parsed_pid is not None and parsed_pid in planet_ids:
        return parsed_pid
    obs_pid = ctx.observed_active_planet_id
    if isinstance(obs_pid, int) and not isinstance(obs_pid, bool) and obs_pid in planet_ids:
        return obs_pid
    if action.planet_id in planet_ids:
        return action.planet_id
    if prev_state is not None and prev_state.active_planet_id in planet_ids:
        return prev_state.active_planet_id
    return updated_overview.active_planet_id


def _apply_economy_rates_cache(
    planets: list[PlanetState],
    prev_state: GameState | None,
    last_economy_rates: Mapping[int, dict[str, int]],
) -> None:
    if prev_state is None:
        return
    prev_rates = {p.planet_id: p.economy_rates for p in prev_state.planets if p.economy_rates}
    for i, planet in enumerate(planets):
        rates = last_economy_rates.get(planet.planet_id) or prev_rates.get(planet.planet_id)
        if rates and not planet.economy_rates:
            planets[i] = replace(planet, economy_rates=dict(rates))


def build_updated_state_impl(
    ctx: ExecutorContext,
    last_economy_rates: Mapping[int, dict[str, int]],
    client: BrowserClientLike,
    prev_state: GameState | None,
    action: ActionInput,
    updated_overview: object,
    *,
    page_html: str = "",
    is_home: bool = False,
    is_fleet: bool = False,
    include_buildings: bool = False,
    include_research: bool = False,
    include_shipyard: bool = False,
) -> GameState | None:
    """Merge refreshed overview with current page offers, queues, menu status, and cached rates."""
    if not isinstance(updated_overview, GameState):
        return None
    planets = list(updated_overview.planets)
    planet_ids = {p.planet_id for p in planets}
    unread, account_points, account_rank = _read_menu_status_overlay(client, prev_state)

    tutorial = prev_state.tutorial if prev_state is not None else None
    speedup = prev_state.speedup_factor if prev_state is not None else None
    newbie = prev_state.newbie_protection if prev_state is not None else False
    merged_queues = list(prev_state.active_queues) if prev_state is not None else []
    alerts = prev_state.fleet_alerts if prev_state is not None else ()
    research_offers = prev_state.research_offers if prev_state is not None else ()

    parsed_pid: int | None = None
    if is_home and page_html:
        home_pid, h_speedup, h_newbie, h_queues, h_alerts = parse_home_metadata(page_html)
        parsed_pid = home_pid
        if h_speedup is not None:
            speedup = h_speedup
        newbie = bool(h_newbie)
        merged_queues = list(h_queues)
        alerts = h_alerts
        tutorial = parse_tutorial_from_home(page_html)
    elif not is_fleet and page_html:
        parsed_pid = extract_selected_planet_id(page_html)

    active_pid = _resolve_active_planet_id(ctx, prev_state, action, updated_overview, planet_ids, parsed_pid)
    active_idx = next((i for i, p in enumerate(planets) if p.planet_id == active_pid), 0 if planets else None)

    if include_buildings and page_html and active_idx is not None:
        parsed_tut = parse_tutorial_from_home(page_html)
        if parsed_tut is not None:
            tutorial = parsed_tut
        b_queues = parse_building_queue(page_html)
        if b_queues:
            merged_queues = [q for q in merged_queues if q.category != "building"] + list(b_queues)
        elif _EMPTY_BUILD_QUEUE_RE.search(page_html):
            merged_queues = [q for q in merged_queues if q.category != "building"]
        planet = planets[active_idx]
        planets[active_idx] = replace(planet, building_offers=parse_buildings_page(page_html, planet.buildings))

    if include_research and page_html:
        parsed_offers = parse_research_page(page_html, updated_overview.researches)
        research_offers = parsed_offers
        active_res = [o for o in parsed_offers if o.active]
        if active_res:
            res_jobs = [QueueJob(category="research", name=o.name, target_level=o.target_level) for o in active_res]
            merged_queues = [q for q in merged_queues if q.category != "research"] + res_jobs
        elif parsed_offers:
            merged_queues = [q for q in merged_queues if q.category != "research"]

    if include_shipyard and page_html and active_idx is not None:
        planet = planets[active_idx]
        ship_offers = parse_unit_factory_page(page_html, unit_type="ship", owned_counts=planet.ships)
        planets[active_idx] = replace(planet, ship_offers=ship_offers)

    _apply_economy_rates_cache(planets, prev_state, last_economy_rates)
    return replace(
        updated_overview,
        planets=tuple(planets),
        active_planet_id=active_pid,
        unread_messages=unread,
        tutorial=tutorial,
        active_queues=tuple(merged_queues),
        fleet_alerts=alerts,
        research_offers=research_offers,
        speedup_factor=speedup,
        newbie_protection=newbie,
        account_points=account_points,
        account_rank=account_rank,
        raid_targets=prev_state.raid_targets if prev_state is not None else updated_overview.raid_targets,
        raid_stats=prev_state.raid_stats if prev_state is not None else updated_overview.raid_stats,
    )


def format_planet_mismatch_message(mismatch_pid: int, requested_planet_id: int) -> str:
    """Format the standard active-planet mismatch abort message."""
    return f"Aborted: active planet is {mismatch_pid}, not requested planet {catalog.fmt_compact_int(requested_planet_id)}."
