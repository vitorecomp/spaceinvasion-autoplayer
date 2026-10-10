"""Priority-band ranking, deduplication, and tutorial/economy phase routing for plan assembly."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, PlanetState, ResearchOffer
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _offer_map,
    _safe_strategy_str,
    _ship_offer_map,
)
from spaceinvasion.planner.heuristics._queues import (
    _count_planet_building_queues,
    _effective_building_levels_and_energy,
    _max_build_slots,
    _queued_building_counts,
)
from spaceinvasion.planner.heuristics._types import AI_INFRA_CARGO_TAG, PlannedAction
from spaceinvasion.planner.heuristics.economy import _plan_economy_for_planet
from spaceinvasion.planner.heuristics.tutorial import _plan_tutorial_actions

_PRIORITY_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def dedupe_actions(actions: Sequence[PlannedAction]) -> list[PlannedAction]:
    deduped: list[PlannedAction] = []
    seen_keys: set[tuple[str, int, int | None, str]] = set()
    for act in actions:
        key = (act.kind, act.planet_id, act.target_id, act.target_name)
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(act)
    return deduped


def summarize_plan(actions: Sequence[PlannedAction]) -> dict[str, object]:
    """Return deterministic priority/kind/planet summary counts for a heuristic plan."""
    by_priority: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_planet: dict[str, int] = {}
    for act in actions:
        by_priority[act.priority] = by_priority.get(act.priority, 0) + 1
        by_kind[act.kind] = by_kind.get(act.kind, 0) + 1
        by_planet[str(act.planet_id)] = by_planet.get(str(act.planet_id), 0) + 1
    first = next((a for a in actions if a.executable_now), None)
    return {
        "total": len(actions),
        "executable_now": sum(1 for a in actions if a.executable_now),
        "by_priority": by_priority,
        "by_kind": by_kind,
        "by_planet_id": by_planet,
        "first_executable": (
            None
            if first is None
            else {
                "priority": first.priority,
                "kind": first.kind,
                "planet_id": first.planet_id,
                "target_name": first.target_name,
                "target_level": first.target_level,
            }
        ),
    }


def fleet_subrank(act: PlannedAction) -> tuple[int, int]:
    if act.priority == "P1":
        if act.kind == "ABANDON_COLONY":
            return (0, 0)
        if act.kind == "SPY":
            return (0, 1 if "(free slot)" in _safe_strategy_str(act.target_name).lower() else 2)
        if act.kind == "TRANSPORT":
            return (0, 3)
        if act.kind == "COLONIZE":
            return (0, 4)
        if act.kind == "BUILD_SHIP" and catalog.safe_optional_int(act.target_id, None) == 8:
            return (0, 5)
        if act.kind == "BUILD_SHIP" and catalog.safe_optional_int(act.target_id, None) == 10:
            return (0, 6)
        if act.kind == "SCAN_GALAXY":
            return (0, 7)
        return (1, 0)
    if act.priority == "P2":
        if act.kind == "BUILD_SHIP" and AI_INFRA_CARGO_TAG in _safe_strategy_str(act.reason):
            return (0, 5)
        if act.kind == "SPY":
            return (1 if "(free slot)" in _safe_strategy_str(act.target_name).lower() else 2, 0)
        if act.kind == "TRANSPORT":
            return (3, 0)
        if act.kind == "COLONIZE":
            return (4, 0)
        if act.kind == "STATION":
            return (4, 5)
        if act.kind in ("ATTACK", "RECYCLE"):
            return (5, 0)
        if act.kind == "RESEARCH":
            return (5, 5)
        return (6, 0)
    return (0, 0)


def plan_band_subrank(act: PlannedAction) -> tuple[int, int]:
    if act.priority == "P1":
        if act.kind == "ABANDON_COLONY":
            return (0, 0)
        if act.kind == "SPY":
            return (0, 1 if "(free slot)" in _safe_strategy_str(act.target_name).lower() else 2)
        if act.kind == "TRANSPORT":
            return (0, 3)
        if act.kind == "COLONIZE":
            return (0, 4)
        if act.kind == "BUILD_SHIP" and catalog.safe_optional_int(act.target_id, None) == 8:
            return (0, 5)
        if (
            act.kind == "BUILD_SHIP"
            and catalog.safe_optional_int(act.target_id, None) == 10
            and "designated spy" in _safe_strategy_str(act.reason).lower()
        ):
            return (0, 6)
        if act.kind == "SCAN_GALAXY":
            return (0, 7)
        if "negative energy" in _safe_strategy_str(act.reason).lower():
            return (1, 0)
        return (2, 0)
    if act.priority != "P2":
        return (0, 0)
    if act.kind == "CLAIM_TUTORIAL":
        return (0, 0)
    if act.kind == "SPY":
        return (1 if "(free slot)" in _safe_strategy_str(act.target_name).lower() else 2, 0)
    if act.kind == "TRANSPORT":
        return (3, 0)
    if act.kind == "COLONIZE":
        return (4, 0)
    if act.kind == "STATION":
        return (4, 5)
    return (5, 0)


def _append_cross_planet_economy(
    state: GameState,
    active: PlanetState,
    planets_to_check: Sequence[PlanetState],
    *,
    research_offers: Mapping[str, ResearchOffer],
    active_queue_full: bool,
    research_queue_busy: bool,
    cfg: StrategyConfig,
    plan: list[PlannedAction],
) -> None:
    all_mines: list[tuple[float, PlannedAction]] = []
    all_advisories: list[PlannedAction] = []
    for planet in planets_to_check:
        if _norm_planet_type(planet) == "raid":
            continue
        is_active = planet.planet_id == active.planet_id
        eff_b, proj_net = _effective_building_levels_and_energy(state, planet, is_active_planet=is_active)
        planet_q_full = (
            active_queue_full
            if is_active
            else (
                _count_planet_building_queues(state, planet, is_active_planet=False) >= _max_build_slots(planet, state)
            )
        )
        mines, extra_builds, adv = _plan_economy_for_planet(
            state,
            planet,
            _offer_map(planet),
            research_offers if is_active else {},
            _ship_offer_map(planet),
            eff_b,
            proj_net,
            planet_q_full,
            research_queue_busy,
            cfg.allow_multi_planet and not is_active,
            cfg,
        )
        all_mines.extend(mines)
        plan.extend(extra_builds)
        all_advisories.extend(adv)

    all_mines.sort(key=lambda pair: pair[0])
    plan.extend(act for _, act in all_mines)
    plan.extend(all_advisories)


def plan_tutorial_or_economy(
    state: GameState,
    active: PlanetState,
    planets_to_check: Sequence[PlanetState],
    *,
    research_offers: Mapping[str, ResearchOffer],
    queued_counts: Mapping[int, int],
    active_queue_full: bool,
    research_queue_busy: bool,
    cfg: StrategyConfig,
    plan: list[PlannedAction],
) -> list[PlannedAction]:
    active_offers = _offer_map(active)
    active_eff, active_proj_net = _effective_building_levels_and_energy(state, active, is_active_planet=True)
    if state.tutorial and state.tutorial.can_complete:
        plan.append(
            PlannedAction(
                priority="P2",
                kind="CLAIM_TUTORIAL",
                planet_id=active.planet_id,
                planet_coords=_safe_strategy_str(active.coords),
                target_name=(
                    f"Tutorial {catalog.fmt_compact_int(state.tutorial.step)}/"
                    f"{catalog.fmt_compact_int(state.tutorial.total_steps)} Reward"
                ),
                reason=f"All tasks complete; claim {_safe_strategy_str(state.tutorial.reward_text) or 'tutorial reward'}",
                executable_now=True,
            )
        )

    tutorial_actions: list[PlannedAction] = []
    if state.tutorial and not state.tutorial.can_complete:
        for tut_planet in planets_to_check:
            is_tut_active = tut_planet.planet_id == active.planet_id
            if is_tut_active:
                t_offers, t_eff, t_proj_net = active_offers, active_eff, active_proj_net
                t_q_counts: Mapping[int, int] = queued_counts
                t_q_full, t_r_offers = active_queue_full, research_offers
            else:
                if _norm_planet_type(tut_planet) == "raid":
                    continue
                t_offers = _offer_map(tut_planet)
                t_eff, t_proj_net = _effective_building_levels_and_energy(state, tut_planet, is_active_planet=False)
                t_q_counts = _queued_building_counts(state, tut_planet, is_active_planet=False)
                t_q_full = _count_planet_building_queues(state, tut_planet, is_active_planet=False) >= _max_build_slots(
                    tut_planet, state
                )
                t_r_offers = {}
            tutorial_actions = _plan_tutorial_actions(
                state,
                tut_planet,
                t_offers,
                t_r_offers,
                t_q_counts,
                t_eff,
                t_proj_net,
                t_q_full,
                research_queue_busy,
                cfg,
                allow_remote_colony_build=(cfg.allow_multi_planet and not is_tut_active),
                allow_live_research=is_tut_active,
            )
            if tutorial_actions:
                break

    if tutorial_actions:
        plan.extend(tutorial_actions)
        return tutorial_actions
    if state.tutorial and state.tutorial.can_complete:
        return tutorial_actions

    _append_cross_planet_economy(
        state,
        active,
        planets_to_check,
        research_offers=research_offers,
        active_queue_full=active_queue_full,
        research_queue_busy=research_queue_busy,
        cfg=cfg,
        plan=plan,
    )
    return tutorial_actions
