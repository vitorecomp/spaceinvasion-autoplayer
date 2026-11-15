"""Tutorial building, research, prerequisite, and fleet-milestone planning."""

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
    TutorialTask,
)
from backend.planner.heuristics._actions import _make_build_action, _make_research_action
from backend.planner.heuristics._common import (
    _apply_queue_cap_if_full,
    _is_building_allowed_on_planet,
    _norm_planet_type,
    _planet_has_free_building_fields,
    _safe_strategy_int,
    _safe_strategy_str,
)
from backend.planner.heuristics._queues import _eff_level, _energy_starvation_delta
from backend.planner.heuristics._tutorial_fleet import _plan_tutorial_fleet_milestones
from backend.planner.heuristics._tutorial_prereqs import (
    _build_effective_researches,
    _emit_tutorial_prereq_actions,
)
from backend.planner.heuristics._types import (
    DEVELOPMENT_CENTER,
    FUSION_PLANT,
    RESEARCH_LAB,
    THERMOSOLAR_PLANT,
    PlannedAction,
)

__all__ = [
    "_emit_tutorial_prereq_actions",
    "_plan_tutorial_actions",
    "_plan_tutorial_fleet_milestones",
]


@dataclass(frozen=True)
class _TutorialContext:
    state: GameState
    planet: PlanetState
    offers: Mapping[int, BuildingOffer]
    research_offers: Mapping[str, ResearchOffer]
    eff_buildings: Mapping[str, int]
    eff_researches: Mapping[str, int]
    queue_full: bool
    research_queue_busy: bool
    config: StrategyConfig
    can_research_planet: bool
    allow_remote_colony_build: bool
    allow_live_research: bool
    tut_step_s: str
    tut_total_s: str
    emitted_build_ids: set[tuple[int, int]]
    emitted_res_keys: set[str]


def _collect_tutorial_tasks(
    state: GameState,
    planet: PlanetState,
    offers: Mapping[int, BuildingOffer],
    queued_counts: Mapping[int, int],
    eff_buildings: Mapping[str, int],
) -> tuple[bool, dict[str, int], list[tuple[TutorialTask, int]], list[tuple[TutorialTask, int]]]:
    ptype = _norm_planet_type(planet)
    lab_eff = _eff_level(planet, eff_buildings, RESEARCH_LAB)
    can_research_planet = ptype != "raid" and (
        ptype != "asteroid" or lab_eff >= 1 or _is_building_allowed_on_planet(planet, RESEARCH_LAB, offers)
    )
    active_r_inc, eff_researches = _build_effective_researches(state)
    building_tasks: list[tuple[TutorialTask, int]] = []
    research_tasks: list[tuple[TutorialTask, int]] = []
    if state.tutorial is None:
        return can_research_planet, eff_researches, building_tasks, research_tasks
    for t in state.tutorial.tasks:
        if t.completed:
            continue
        t_cur = _safe_strategy_int(t.current, 0)
        t_tgt = _safe_strategy_int(t.target, 0)
        if t.category == "research" and t.research_id:
            rk_s = _safe_strategy_str(t.research_id).strip()
            eff_r_progress = t_cur + active_r_inc.get(rk_s, 0)
            if can_research_planet and eff_r_progress < t_tgt:
                research_tasks.append((t, eff_r_progress))
        elif t.category == "building" and t.building_id is not None:
            t_bid = catalog.safe_optional_int(t.building_id, None)
            if t_bid is not None and t_bid > 0 and _is_building_allowed_on_planet(planet, t_bid, offers):
                eff_progress = t_cur + queued_counts.get(t_bid, 0)
                if eff_progress < t_tgt:
                    building_tasks.append((t, eff_progress))
    return can_research_planet, eff_researches, building_tasks, research_tasks


def _find_tutorial_power_starvation_note(
    planet: PlanetState,
    eff_buildings: Mapping[str, int],
    building_tasks: list[tuple[TutorialTask, int]],
    projected_net: int,
) -> str:
    for t, _eff in building_tasks:
        bid = catalog.safe_optional_int(t.building_id, 0) or 0
        if bid in (FUSION_PLANT, THERMOSOLAR_PLANT):
            continue
        nxt_lvl = _eff_level(planet, eff_buildings, bid) + 1
        starve_delta = _energy_starvation_delta(bid, nxt_lvl, projected_net)
        if starve_delta < 0:
            name_s = _safe_strategy_str(t.building_name) or f"Building {bid}"
            return (
                f" (proactive power: {name_s} L{catalog.fmt_compact_int(nxt_lvl)} "
                f"needs {catalog.fmt_compact_int(-starve_delta)} energy, "
                f"projected net {catalog.fmt_compact_int(projected_net, signed=True)})"
            )
    return ""


def _order_tutorial_buildings(
    building_tasks: list[tuple[TutorialTask, int]],
    *,
    need_power_first: bool,
) -> list[tuple[TutorialTask, int]]:
    def _sort_key(item: tuple[TutorialTask, int]) -> tuple[int, int, int]:
        task, eff = item
        bid = catalog.safe_optional_int(task.building_id, 99) or 99
        if bid == FUSION_PLANT and need_power_first:
            bucket = 0
        elif bid == DEVELOPMENT_CENTER:
            bucket = 1
        elif bid == FUSION_PLANT:
            # Defer non-urgent Fusion upgrades behind mines when ample spare energy exists
            bucket = 4
        else:
            bucket = 3
        return (bucket, eff, bid)

    return sorted(building_tasks, key=_sort_key)


def _emit_tutorial_building_actions(
    ctx: _TutorialContext,
    building_tasks: list[tuple[TutorialTask, int]],
    projected_net: int,
) -> list[PlannedAction]:
    has_fusion_task = any(catalog.safe_optional_int(t.building_id, None) == FUSION_PLANT for t, _ in building_tasks)
    starving_task_note = _find_tutorial_power_starvation_note(
        ctx.planet, ctx.eff_buildings, building_tasks, projected_net
    )
    need_power_first = projected_net <= 0 or bool(starving_task_note)
    ordered = _order_tutorial_buildings(building_tasks, need_power_first=need_power_first)
    actions: list[PlannedAction] = []

    if (
        need_power_first
        and not has_fusion_task
        and building_tasks
        and _safe_strategy_int(ctx.planet.energy.net, 0) >= 0
        and _planet_has_free_building_fields(ctx.planet)
        and _is_building_allowed_on_planet(ctx.planet, FUSION_PLANT, ctx.offers)
    ):
        fusion_act = _make_build_action(
            "P2",
            ctx.planet,
            FUSION_PLANT,
            "Fusion plant",
            f"Proactive power before tutorial mines{starving_task_note}",
            ctx.offers,
            effective_level=_eff_level(ctx.planet, ctx.eff_buildings, FUSION_PLANT),
            researches=ctx.state.researches,
            allow_remote_colony_build=ctx.allow_remote_colony_build,
        )
        actions.append(_apply_queue_cap_if_full(fusion_act, ctx.queue_full))
        ctx.emitted_build_ids.add((ctx.planet.planet_id, FUSION_PLANT))

    for task, eff in ordered:
        bid = catalog.safe_optional_int(task.building_id, None)
        if bid is None:
            continue
        b_label = _safe_strategy_str(task.building_name) or f"Building {bid}"
        prereq_acts = _emit_tutorial_prereq_actions(
            tut_step_s=ctx.tut_step_s,
            tut_total_s=ctx.tut_total_s,
            category="buildings",
            slug_or_id=bid,
            goal_label=b_label,
            state=ctx.state,
            build_planet=ctx.planet,
            build_offers=ctx.offers,
            eff_buildings=ctx.eff_buildings,
            queue_full=ctx.queue_full,
            allow_remote_colony_build=ctx.allow_remote_colony_build,
            research_planet=ctx.planet if ctx.can_research_planet else None,
            research_offers=ctx.research_offers,
            research_queue_busy=ctx.research_queue_busy,
            allow_live_research=ctx.allow_live_research,
            config=ctx.config,
            emitted_build_ids=ctx.emitted_build_ids,
            emitted_res_keys=ctx.emitted_res_keys,
            eff_researches=ctx.eff_researches,
        )
        actions.extend(prereq_acts)
        if prereq_acts and catalog.missing_prerequisites(
            "buildings", bid, buildings=ctx.planet.buildings, researches=ctx.state.researches
        ):
            continue
        if (ctx.planet.planet_id, bid) in ctx.emitted_build_ids:
            continue
        ctx.emitted_build_ids.add((ctx.planet.planet_id, bid))
        reason = (
            f"Tutorial {ctx.tut_step_s}/{ctx.tut_total_s}: "
            f"{_safe_strategy_str(task.description)} "
            f"(progress {catalog.fmt_compact_int(eff)}/{catalog.fmt_compact_int(task.target)})"
        )
        if bid == FUSION_PLANT and need_power_first:
            reason += starving_task_note or " (energy first for 100% mine output)"
        act = _make_build_action(
            "P2",
            ctx.planet,
            bid,
            b_label,
            reason,
            ctx.offers,
            effective_level=_eff_level(ctx.planet, ctx.eff_buildings, bid),
            researches=ctx.state.researches,
            allow_remote_colony_build=ctx.allow_remote_colony_build,
        )
        actions.append(_apply_queue_cap_if_full(act, ctx.queue_full))
    return actions


def _emit_tutorial_research_actions(
    ctx: _TutorialContext,
    research_tasks: list[tuple[TutorialTask, int]],
) -> list[PlannedAction]:
    actions: list[PlannedAction] = []
    for rtask, eff_r in research_tasks:
        rid = _safe_strategy_str(rtask.research_id).strip()
        if not rid:
            continue
        r_label = _safe_strategy_str(rtask.building_name) or rid
        prereq_acts = _emit_tutorial_prereq_actions(
            tut_step_s=ctx.tut_step_s,
            tut_total_s=ctx.tut_total_s,
            category="research",
            slug_or_id=rid,
            goal_label=r_label,
            state=ctx.state,
            build_planet=ctx.planet,
            build_offers=ctx.offers,
            eff_buildings=ctx.eff_buildings,
            queue_full=ctx.queue_full,
            allow_remote_colony_build=ctx.allow_remote_colony_build,
            research_planet=ctx.planet,
            research_offers=ctx.research_offers,
            research_queue_busy=ctx.research_queue_busy,
            allow_live_research=ctx.allow_live_research,
            config=ctx.config,
            emitted_build_ids=ctx.emitted_build_ids,
            emitted_res_keys=ctx.emitted_res_keys,
            eff_researches=ctx.eff_researches,
        )
        actions.extend(prereq_acts)
        if rid in ctx.emitted_res_keys:
            continue
        ctx.emitted_res_keys.add(rid)
        desc_s = _safe_strategy_str(rtask.description)
        reason = (
            f"Tutorial {ctx.tut_step_s}/{ctx.tut_total_s}: "
            f"{desc_s} (progress {catalog.fmt_compact_int(eff_r)}/{catalog.fmt_compact_int(rtask.target)})"
        )
        actions.append(
            _make_research_action(
                "P2",
                ctx.planet,
                ctx.state,
                rid,
                reason,
                ctx.research_offers,
                ctx.research_queue_busy,
                allow_live=ctx.config.allow_research and ctx.allow_live_research,
            )
        )
    return actions


def _plan_tutorial_actions(
    state: GameState,
    planet: PlanetState,
    offers: Mapping[int, BuildingOffer],
    research_offers: Mapping[str, ResearchOffer],
    queued_counts: Mapping[int, int],
    eff_buildings: Mapping[str, int],
    projected_net: int,
    queue_full: bool,
    research_queue_busy: bool,
    config: StrategyConfig,
    *,
    allow_remote_colony_build: bool = False,
    allow_live_research: bool = True,
) -> list[PlannedAction]:
    """Order incomplete tutorial building & research tasks with proactive power & prerequisite lookahead."""
    if not state.tutorial:
        return []
    can_research_planet, eff_researches, building_tasks, research_tasks = _collect_tutorial_tasks(
        state, planet, offers, queued_counts, eff_buildings
    )
    if not building_tasks and not research_tasks:
        return []
    ctx = _TutorialContext(
        state=state,
        planet=planet,
        offers=offers,
        research_offers=research_offers,
        eff_buildings=eff_buildings,
        eff_researches=eff_researches,
        queue_full=queue_full,
        research_queue_busy=research_queue_busy,
        config=config,
        can_research_planet=can_research_planet,
        allow_remote_colony_build=allow_remote_colony_build,
        allow_live_research=allow_live_research,
        tut_step_s=catalog.fmt_compact_int(state.tutorial.step),
        tut_total_s=catalog.fmt_compact_int(state.tutorial.total_steps),
        emitted_build_ids=set(),
        emitted_res_keys=set(),
    )
    actions = _emit_tutorial_building_actions(ctx, building_tasks, projected_net)
    if can_research_planet:
        actions.extend(_emit_tutorial_research_actions(ctx, research_tasks))
    return actions
