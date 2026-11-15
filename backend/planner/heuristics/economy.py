"""Per-planet mine ROI scoring, proactive power, and paused Blast Furnace energy guards."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from backend.core import catalog
from backend.core.config import StrategyConfig
from backend.core.model import (
    BuildingOffer,
    GameState,
    PlanetState,
    ResearchOffer,
    UnitOffer,
)
from backend.planner.heuristics._actions import _make_build_action
from backend.planner.heuristics._common import (
    _apply_queue_cap_if_full,
    _is_building_allowed_on_planet,
    _planet_has_free_building_fields,
    _safe_strategy_int,
)
from backend.planner.heuristics._econ_helpers import _mine_payback_hours
from backend.planner.heuristics._economy_power import (
    _apply_paused_blast_power_guard,
    _plan_proactive_mine_power,
)
from backend.planner.heuristics._queues import _eff_level
from backend.planner.heuristics._types import (
    _MINE_IDS,
    _MINE_LABELS,
    BLAST_FURNACE,
    PIG_IRON_MINE,
    PlannedAction,
)
from backend.planner.heuristics.shipyard_budgets import _plan_unlock_and_advisory_actions

__all__ = ["_plan_economy_for_planet"]

_ScoredMineRow = tuple[float, int, int, str]


def _score_planet_mines(
    state: GameState,
    planet: PlanetState,
    offers: Mapping[int, BuildingOffer],
    eff_buildings: Mapping[str, int],
    config: StrategyConfig,
) -> tuple[list[_ScoredMineRow], _ScoredMineRow | None, bool, int, int]:
    pig_lvl = _eff_level(planet, eff_buildings, PIG_IRON_MINE)
    blast_lvl = _eff_level(planet, eff_buildings, BLAST_FURNACE)
    mining_tech = max(0, _safe_strategy_int((state.researches or {}).get("r1", 0), 0))
    temp_i = _safe_strategy_int(planet.temperature, 0)
    temp_s = catalog.fmt_compact_int(temp_i)
    pig_prod = catalog.mine_production_per_hour(
        PIG_IRON_MINE, pig_lvl, temperature=temp_i, mining_tech_level=mining_tech
    )
    blast_used_next = catalog.blast_furnace_pig_iron_used(blast_lvl + 1)
    blast_pig_capped = pig_prod <= 0 or (blast_used_next > pig_prod * config.blast_pig_iron_max_share)
    rates = getattr(planet, "economy_rates", None) or {}
    cur_b2_pct = max(0, min(100, _safe_strategy_int(rates.get("resource2", 100), 100)))
    tut_needs_b2 = bool(
        state.tutorial
        and not state.tutorial.can_complete
        and any(
            not t.completed and t.category == "building" and t.building_id == BLAST_FURNACE
            for t in state.tutorial.tasks
        )
    )
    res_pig_eco = _safe_strategy_int(getattr(planet.resources, "pig_iron", 0), 0)
    res_met_eco = _safe_strategy_int(getattr(planet.resources, "metal", 0), 0)
    sto_pig_eco = _safe_strategy_int(getattr(planet.storage, "pig_iron", 0), 0)
    sto_met_eco = _safe_strategy_int(getattr(planet.storage, "metal", 0), 0)
    metal_full_pig_space_eco = (
        sto_met_eco > 0
        and res_met_eco * 100 >= sto_met_eco * 98
        and (sto_pig_eco <= 0 or res_pig_eco * 100 < sto_pig_eco * 99)
    )
    b2_offer = offers.get(BLAST_FURNACE)
    blast_paused_hold = (
        (cur_b2_pct == 0 or metal_full_pig_space_eco)
        and blast_lvl >= 1
        and config.auto_throttle_blast_furnace
        and not config.merchant_mode
        and not tut_needs_b2
        and (b2_offer is None or b2_offer.cost.metal == 0)
    )

    scored_mines: list[_ScoredMineRow] = []
    deferred_blast: _ScoredMineRow | None = None
    for bid in _MINE_IDS:
        if not _is_building_allowed_on_planet(planet, bid, offers):
            continue
        if bid == BLAST_FURNACE and config.merchant_mode:
            continue
        payback_h, delta_sp, nxt_lvl = _mine_payback_hours(planet, eff_buildings, bid, mining_tech, offers)
        nxt_lvl_s = catalog.fmt_compact_int(nxt_lvl)
        reason = f"Best mine ROI ({payback_h:.1f}h Spice payback, +{delta_sp:.1f} Spice-eq/h @ {temp_s}C)"
        if bid == BLAST_FURNACE:
            if blast_paused_hold:
                deferred_blast = (
                    payback_h,
                    bid,
                    nxt_lvl,
                    f"Blast furnace L{catalog.fmt_compact_int(blast_lvl)} is paused at 0%; "
                    f"resume Metal smelting before upgrading to L{nxt_lvl_s}",
                )
                continue
            if blast_pig_capped:
                deferred_blast = (
                    payback_h,
                    bid,
                    nxt_lvl,
                    f"Blast furnace L{nxt_lvl_s} needs {blast_used_next:.0f} Pig iron/h vs "
                    f"{pig_prod:.0f}/h produced; expand Pig iron first",
                )
                continue
            reason = (
                f"Blast furnace ROI ({payback_h:.1f}h payback; uses {blast_used_next:.0f}/{pig_prod:.0f} Pig iron/h)"
            )
        scored_mines.append((payback_h, bid, nxt_lvl, reason))

    scored_mines.sort(key=lambda row: (row[0], row[1]))
    return scored_mines, deferred_blast, blast_paused_hold, cur_b2_pct, blast_lvl


def _build_mine_and_deferred_actions(
    *,
    state: GameState,
    planet: PlanetState,
    offers: Mapping[int, BuildingOffer],
    scored_mines: list[_ScoredMineRow],
    deferred_blast: _ScoredMineRow | None,
    blast_paused_hold: bool,
    queue_full: bool,
    allow_remote_build: bool,
    advisories: list[PlannedAction],
) -> list[tuple[float, PlannedAction]]:
    mine_actions: list[tuple[float, PlannedAction]] = []
    for payback_h, bid, nxt_lvl, reason in scored_mines:
        act = _make_build_action(
            "P2",
            planet,
            bid,
            _MINE_LABELS[bid],
            reason,
            offers,
            effective_level=nxt_lvl - 1,
            researches=state.researches,
            allow_remote_colony_build=allow_remote_build,
        )
        mine_actions.append((payback_h, _apply_queue_cap_if_full(act, queue_full)))

    if deferred_blast is not None:
        _, bid, nxt_lvl, reason = deferred_blast
        act = _make_build_action(
            "P3",
            planet,
            bid,
            _MINE_LABELS[bid],
            reason,
            offers,
            effective_level=nxt_lvl - 1,
            researches=state.researches,
            allow_remote_colony_build=allow_remote_build,
        )
        capped_act = _apply_queue_cap_if_full(act, queue_full)
        if blast_paused_hold and capped_act.executable_now:
            capped_act = replace(capped_act, kind="WAIT_RESOURCES", executable_now=False)
        advisories.append(capped_act)
    return mine_actions


def _plan_economy_for_planet(
    state: GameState,
    planet: PlanetState,
    offers: Mapping[int, BuildingOffer],
    research_offers: Mapping[str, ResearchOffer],
    ship_offers: Mapping[int, UnitOffer],
    eff_buildings: Mapping[str, int],
    projected_net: int,
    queue_full: bool,
    research_queue_busy: bool,
    allow_remote_build: bool,
    config: StrategyConfig,
) -> tuple[list[tuple[float, PlannedAction]], list[PlannedAction], list[PlannedAction]]:
    """Return `(scored_mines, power_or_unlock_actions, advisories)` for one planet."""
    scored_mines, deferred_blast, blast_paused_hold, cur_b2_pct, blast_lvl = _score_planet_mines(
        state, planet, offers, eff_buildings, config
    )
    priority_builds: list[PlannedAction] = []
    has_free_fields = _planet_has_free_building_fields(planet)
    if scored_mines and has_free_fields:
        _, top_bid, top_nxt, _ = scored_mines[0]
        power_act = _plan_proactive_mine_power(
            state=state,
            planet=planet,
            offers=offers,
            ship_offers=ship_offers,
            eff_buildings=eff_buildings,
            top_bid=top_bid,
            top_nxt=top_nxt,
            projected_net=projected_net,
            queue_full=queue_full,
            allow_remote_build=allow_remote_build,
            config=config,
        )
        if power_act is not None:
            priority_builds.append(power_act)

    unlocks, advisories = _plan_unlock_and_advisory_actions(
        state,
        planet,
        eff_buildings,
        offers,
        research_offers,
        ship_offers,
        queue_full,
        research_queue_busy,
        allow_remote_build,
        config,
    )
    priority_builds.extend(unlocks)
    mine_actions = _build_mine_and_deferred_actions(
        state=state,
        planet=planet,
        offers=offers,
        scored_mines=scored_mines,
        deferred_blast=deferred_blast,
        blast_paused_hold=blast_paused_hold,
        queue_full=queue_full,
        allow_remote_build=allow_remote_build,
        advisories=advisories,
    )
    if (
        cur_b2_pct == 0
        and blast_lvl >= 1
        and config.auto_throttle_blast_furnace
        and not config.merchant_mode
        and has_free_fields
    ):
        mine_actions = _apply_paused_blast_power_guard(
            state=state,
            planet=planet,
            offers=offers,
            eff_buildings=eff_buildings,
            projected_net=projected_net,
            queue_full=queue_full,
            allow_remote_build=allow_remote_build,
            config=config,
            blast_lvl=blast_lvl,
            unlocks=unlocks,
            advisories=advisories,
            mine_actions=mine_actions,
            priority_builds=priority_builds,
        )
    return mine_actions, priority_builds, advisories
