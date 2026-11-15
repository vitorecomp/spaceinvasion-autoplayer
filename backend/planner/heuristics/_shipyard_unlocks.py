"""Research Lab, Weapons Factory, Fleet Base unlocks, and advisory orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from backend.core import catalog
from backend.core.config import StrategyConfig
from backend.core.model import (
    BuildingOffer,
    GameState,
    PlanetState,
    ResearchOffer,
    UnitOffer,
    count_in_flight_fleets_for_planet,
)
from backend.planner.heuristics._actions import _make_build_action, _make_research_action
from backend.planner.heuristics._cargo_helpers import _adaptive_execution_enabled, _in_flight_ship_counts
from backend.planner.heuristics._common import (
    _apply_queue_cap_if_full,
    _is_building_allowed_on_planet,
    _norm_planet_type,
    _planet_has_free_building_fields,
    _resolve_shipyard_targets,
    _safe_strategy_int,
)
from backend.planner.heuristics._queues import _eff_level
from backend.planner.heuristics._shipyard_advisories import (
    _plan_goal_ship_advisories,
    _plan_mining_tech_advisory,
)
from backend.planner.heuristics._shipyard_targets import _plan_shipyard_target_entries
from backend.planner.heuristics._types import (
    BLAST_FURNACE,
    DEVELOPMENT_CENTER,
    FLEET_BASE,
    KRYPTONITE_MINE,
    PIG_IRON_MINE,
    RESEARCH_LAB,
    WEAPONS_FACTORY,
    PlannedAction,
)


@dataclass(frozen=True)
class _UnlockContext:
    state: GameState
    planet: PlanetState
    eff_buildings: Mapping[str, int]
    offers: Mapping[int, BuildingOffer]
    research_offers: Mapping[str, ResearchOffer]
    queue_full: bool
    research_queue_busy: bool
    allow_remote_build: bool
    config: StrategyConfig


def _plan_lab_unlock(ctx: _UnlockContext, build_unlocks: list[PlannedAction]) -> None:
    unmet_lab = catalog.missing_prerequisites(
        "buildings", RESEARCH_LAB, buildings=ctx.eff_buildings, researches=ctx.state.researches
    )
    if unmet_lab:
        for req in unmet_lab:
            if req["category"] != "buildings":
                continue
            req_info = catalog.resolve_unit("buildings", req["slug"])
            if not req_info or not req_info.get("id"):
                continue
            bid = int(req_info["id"])
            if not _is_building_allowed_on_planet(ctx.planet, bid, ctx.offers):
                continue
            cur_s = catalog.fmt_compact_int(_safe_strategy_int(req.get("current", 0), 0))
            req_s = catalog.fmt_compact_int(_safe_strategy_int(req.get("required", 0), 0))
            act = _make_build_action(
                "P2",
                ctx.planet,
                bid,
                str(req["name"]),
                f"Prerequisite for Research lab ({cur_s}/{req_s})",
                ctx.offers,
                effective_level=_eff_level(ctx.planet, ctx.eff_buildings, bid),
                researches=ctx.state.researches,
                allow_remote_colony_build=ctx.allow_remote_build,
            )
            build_unlocks.append(_apply_queue_cap_if_full(act, ctx.queue_full))
    else:
        act = _make_build_action(
            "P2",
            ctx.planet,
            RESEARCH_LAB,
            "Research lab",
            "Unlock empire research and Mining/Energy/Engine technologies",
            ctx.offers,
            effective_level=0,
            researches=ctx.state.researches,
            allow_remote_colony_build=ctx.allow_remote_build,
        )
        build_unlocks.append(_apply_queue_cap_if_full(act, ctx.queue_full))


def _plan_weapons_factory_unlock(
    ctx: _UnlockContext,
    build_unlocks: list[PlannedAction],
    advisories: list[PlannedAction],
) -> None:
    unmet_wf = catalog.missing_prerequisites(
        "buildings", WEAPONS_FACTORY, buildings=ctx.eff_buildings, researches=ctx.state.researches
    )
    if unmet_wf:
        for req in unmet_wf:
            cur_s = catalog.fmt_compact_int(_safe_strategy_int(req.get("current", 0), 0))
            req_s = catalog.fmt_compact_int(_safe_strategy_int(req.get("required", 0), 0))
            if req["category"] == "buildings":
                req_info = catalog.resolve_unit("buildings", req["slug"])
                if req_info and req_info.get("id"):
                    bid = int(req_info["id"])
                    if not _is_building_allowed_on_planet(ctx.planet, bid, ctx.offers):
                        continue
                    act = _make_build_action(
                        "P2",
                        ctx.planet,
                        bid,
                        str(req["name"]),
                        f"Prerequisite for Weapons factory ({cur_s}/{req_s})",
                        ctx.offers,
                        effective_level=_eff_level(ctx.planet, ctx.eff_buildings, bid),
                        researches=ctx.state.researches,
                        allow_remote_colony_build=ctx.allow_remote_build,
                    )
                    build_unlocks.append(_apply_queue_cap_if_full(act, ctx.queue_full))
            elif req["category"] == "research":
                res_info = catalog.resolve_unit("research", req["slug"])
                rkey = str((res_info or {}).get("id") or "r2")
                advisories.append(
                    _make_research_action(
                        "P3",
                        ctx.planet,
                        ctx.state,
                        rkey,
                        f"Unlock prerequisite for Weapons factory ({cur_s}/{req_s})",
                        ctx.research_offers,
                        ctx.research_queue_busy,
                        allow_live=ctx.config.allow_research,
                    )
                )
    else:
        act = _make_build_action(
            "P2",
            ctx.planet,
            WEAPONS_FACTORY,
            "Weapons factory",
            "Unlock fleet & solar cell construction",
            ctx.offers,
            effective_level=0,
            researches=ctx.state.researches,
            allow_remote_colony_build=ctx.allow_remote_build,
        )
        build_unlocks.append(_apply_queue_cap_if_full(act, ctx.queue_full))


def _plan_fleet_base_upgrade(
    ctx: _UnlockContext,
    *,
    empire_has_shipyard: bool,
    pig: int,
    kryp: int,
    dev: int,
    wf: int,
    build_unlocks: list[PlannedAction],
    advisories: list[PlannedAction],
) -> None:
    fb = _eff_level(ctx.planet, ctx.eff_buildings, FLEET_BASE)
    in_flight_here = count_in_flight_fleets_for_planet(ctx.state, ctx.planet)
    mission_ships = sum(
        max(0, _safe_strategy_int(cnt, 0))
        for skey, cnt in (getattr(ctx.planet, "ships", None) or {}).items()
        if skey != "s9"
    )
    is_raid_fb = _norm_planet_type(ctx.planet) == "raid"
    staging_colony_ready = bool(
        empire_has_shipyard
        and not is_raid_fb
        and min(pig, kryp) >= 5
        and (ctx.config.allow_raids or ctx.config.allow_galaxy_scan or getattr(ctx.config, "allow_transport", False))
    )
    if (
        is_raid_fb
        or not (wf >= 1 or mission_ships >= 1 or in_flight_here > 0 or staging_colony_ready)
        or not _planet_has_free_building_fields(ctx.planet)
        or not _is_building_allowed_on_planet(ctx.planet, FLEET_BASE, ctx.offers)
    ):
        return

    slots_saturated = in_flight_here >= (1 + fb)
    is_fleeter = ctx.config.playstyle == "fleeter"
    target_fb = 0
    if slots_saturated:
        target_fb = max(fb + 1, in_flight_here + 1)
    elif mission_ships >= 1 or in_flight_here > 0:
        target_fb = min(15, max(4 if is_fleeter else 2, dev // 2 + (3 if is_fleeter else 1)))
    elif staging_colony_ready:
        target_fb = min(15, max(3 if is_fleeter else 2, dev // 2 + (2 if is_fleeter else 1)))
    elif wf >= 1 and is_fleeter and min(pig, kryp) >= 8:
        target_fb = min(15, max(2, dev // 2 + 2))
    if fb >= target_fb:
        return

    fb_prio = "P2" if (slots_saturated or fb < 3 or in_flight_here >= max(1, fb)) else "P3"
    if slots_saturated:
        fb_reason = (
            f"Expand Fleet base to L{fb + 1} (+1 per-planet concurrent flight slot: "
            f"{1 + fb} -> {2 + fb} flights; {in_flight_here}/{1 + fb} currently in transit)"
        )
    elif wf == 0 and mission_ships == 0:
        fb_reason = (
            f"Build Fleet base L{fb + 1} ({1 + fb} -> {2 + fb} independent per-planet flight slots "
            f"for stationed raids & recyclers — no Weapons factory required)"
        )
    else:
        fb_reason = f"Increase Fleet base to L{fb + 1} ({1 + fb} -> {2 + fb} per-planet concurrent fleet flights)"
    fb_act = _make_build_action(
        fb_prio,
        ctx.planet,
        FLEET_BASE,
        "Fleet base",
        fb_reason,
        ctx.offers,
        effective_level=fb,
        researches=ctx.state.researches,
        allow_remote_colony_build=ctx.allow_remote_build,
    )
    capped_fb = _apply_queue_cap_if_full(fb_act, ctx.queue_full)
    if fb_prio == "P2":
        build_unlocks.append(capped_fb)
    else:
        advisories.append(capped_fb)


def _append_lab_and_shipyard_advisories(
    ctx: _UnlockContext,
    ship_offers: Mapping[int, UnitOffer],
    *,
    base_ready: bool,
    lab: int,
    wf: int,
    need_local_wf: bool,
    build_unlocks: list[PlannedAction],
    advisories: list[PlannedAction],
) -> None:
    is_raid = _norm_planet_type(ctx.planet) == "raid"
    if lab >= 1 and not is_raid:
        _plan_mining_tech_advisory(
            state=ctx.state,
            planet=ctx.planet,
            eff_buildings=ctx.eff_buildings,
            research_offers=ctx.research_offers,
            research_queue_busy=ctx.research_queue_busy,
            config=ctx.config,
            advisories=advisories,
        )
        _plan_goal_ship_advisories(
            state=ctx.state,
            planet=ctx.planet,
            eff_buildings=ctx.eff_buildings,
            offers=ctx.offers,
            research_offers=ctx.research_offers,
            queue_full=ctx.queue_full,
            research_queue_busy=ctx.research_queue_busy,
            allow_remote_build=ctx.allow_remote_build,
            config=ctx.config,
            base_ready=base_ready,
            wf=wf,
            need_local_wf=need_local_wf,
            build_unlocks=build_unlocks,
            advisories=advisories,
        )
    if wf >= 1 and ctx.config.allow_shipyard and not is_raid:
        adaptive = _adaptive_execution_enabled(ctx.config)
        flights: Mapping[str, object] = _in_flight_ship_counts(ctx.state) if adaptive else {}
        advisories.extend(
            _plan_shipyard_target_entries(
                ctx.state,
                ctx.planet,
                ship_offers,
                ctx.config,
                norm_shipyard_targets=_resolve_shipyard_targets(ctx.config.shipyard_targets),
                adaptive=adaptive,
                flights=flights,
                infra_slugs=set(),
                configured_floor=None,
            )
        )


def _plan_unlock_and_advisory_actions(
    state: GameState,
    planet: PlanetState,
    eff_buildings: Mapping[str, int],
    offers: Mapping[int, BuildingOffer],
    research_offers: Mapping[str, ResearchOffer],
    ship_offers: Mapping[int, UnitOffer],
    queue_full: bool,
    research_queue_busy: bool,
    allow_remote_build: bool,
    config: StrategyConfig,
) -> tuple[list[PlannedAction], list[PlannedAction]]:
    """Plan Research lab/Weapons factory unlocks, research ROI steps, and utility ship builds."""
    ctx = _UnlockContext(
        state=state,
        planet=planet,
        eff_buildings=eff_buildings,
        offers=offers,
        research_offers=research_offers,
        queue_full=queue_full,
        research_queue_busy=research_queue_busy,
        allow_remote_build=allow_remote_build,
        config=config,
    )
    build_unlocks: list[PlannedAction] = []
    advisories: list[PlannedAction] = []

    pig = _eff_level(planet, eff_buildings, PIG_IRON_MINE)
    blast = _eff_level(planet, eff_buildings, BLAST_FURNACE)
    kryp = _eff_level(planet, eff_buildings, KRYPTONITE_MINE)
    dev = _eff_level(planet, eff_buildings, DEVELOPMENT_CENTER)
    lab = _eff_level(planet, eff_buildings, RESEARCH_LAB)
    wf = _eff_level(planet, eff_buildings, WEAPONS_FACTORY)
    base_ready = min(pig, blast, kryp) >= 3 or (config.merchant_mode and min(pig, kryp) >= 3)

    if base_ready and lab == 0 and _is_building_allowed_on_planet(planet, RESEARCH_LAB, offers):
        _plan_lab_unlock(ctx, build_unlocks)

    empire_has_shipyard = any(
        p.building_level(WEAPONS_FACTORY) >= 1 for p in (state.planets or ()) if _norm_planet_type(p) != "raid"
    )
    req_spy_pid_unlock = max(0, _safe_strategy_int(getattr(config, "spy_dispatch_planet_id", 0), 0))
    need_local_wf = (
        not empire_has_shipyard
        or wf >= 1
        or min(pig, kryp) >= 16
        or (req_spy_pid_unlock > 0 and planet.planet_id == req_spy_pid_unlock)
    )
    if (
        need_local_wf
        and min(pig, kryp) >= 4
        and lab >= 1
        and wf == 0
        and _is_building_allowed_on_planet(planet, WEAPONS_FACTORY, offers)
    ):
        _plan_weapons_factory_unlock(ctx, build_unlocks, advisories)

    if (
        base_ready
        and lab >= 1
        and dev < max(2, min(pig, kryp) - 2)
        and _is_building_allowed_on_planet(planet, DEVELOPMENT_CENTER, offers)
    ):
        dev_act = _make_build_action(
            "P2",
            planet,
            DEVELOPMENT_CENTER,
            "Development center",
            "Cut local building times and scale infrastructure",
            offers,
            effective_level=dev,
            researches=state.researches,
            allow_remote_colony_build=allow_remote_build,
        )
        build_unlocks.append(_apply_queue_cap_if_full(dev_act, queue_full))

    _plan_fleet_base_upgrade(
        ctx,
        empire_has_shipyard=empire_has_shipyard,
        pig=pig,
        kryp=kryp,
        dev=dev,
        wf=wf,
        build_unlocks=build_unlocks,
        advisories=advisories,
    )
    _append_lab_and_shipyard_advisories(
        ctx,
        ship_offers,
        base_ready=base_ready,
        lab=lab,
        wf=wf,
        need_local_wf=need_local_wf,
        build_unlocks=build_unlocks,
        advisories=advisories,
    )
    return build_unlocks, advisories
