"""Live reconciliation mapping a saved AI-Planner path onto causally ready ``PlannedAction``s."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import logging
from typing import cast

from spaceinvasion.combat.ranking import attach_action_etas
from spaceinvasion.core.coercion import coerce_int
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.core.state import GameState as LegacyGameState
from spaceinvasion.planner.ai._reconcile_fields import (
    _field_conflict_candidates,
    _is_planet_demolish_energy_safe,
    _resolve_plan_demolish_floors_by_planet,
)
from spaceinvasion.planner.ai._reconcile_helpers import (
    ReadConfigLike,
    _is_stale_remote_resource_wait,
    _resolve_in_flight_buildings,
    _resolve_in_flight_research,
    config_max_build_slots,
    index_planets,
)
from spaceinvasion.planner.ai._reconcile_steps import reconcile_build_step, reconcile_research_step
from spaceinvasion.planner.ai._types import GameStateLike, PlanetLike, ResearchOfferLike
from spaceinvasion.planner.ai.live_fill import plan_live_fill_actions

_LOGGER = logging.getLogger(__name__)
_FILL_RECOVERABLE_ERRORS = (ValueError, TypeError, KeyError, LookupError, ArithmeticError, AttributeError)


def _select_lab_planet(state: GameStateLike, non_raid_planets: Sequence[PlanetLike]) -> PlanetLike | None:
    if not non_raid_planets:
        return None
    best_lab = max(
        non_raid_planets,
        key=lambda p: (
            p.building_level(8),
            1 if (state.active_planet_id is not None and p.planet_id == state.active_planet_id) else 0,
        ),
    )
    return best_lab if best_lab.building_level(8) >= 1 else None


def _attach_etas(state: GameStateLike, actions: Sequence[PlannedAction]) -> list[PlannedAction]:
    raw_attached = attach_action_etas(cast(LegacyGameState, state), actions)
    return [act for act in raw_attached if isinstance(act, PlannedAction)]


def reconcile_live_ai_actions(
    state: GameStateLike,
    plan_result: Mapping[str, object],
    config: ReadConfigLike,
) -> tuple[list[PlannedAction], list[dict[str, object]]]:
    """Map a saved AI-Planner sequential plan onto live causally ready ``PlannedAction``s."""
    steps = plan_result.get("steps") if isinstance(plan_result, Mapping) else None
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)) or not steps:
        return [], []

    planets_by_id, planets_by_coords, non_raid_planets = index_planets(state.planets or ())
    in_flight_buildings, planet_queue_counts = _resolve_in_flight_buildings(state)
    in_flight_research, research_queue_busy = _resolve_in_flight_research(state)
    max_build_slots = config_max_build_slots(config)
    allow_multi_planet = bool(config.allow_multi_planet)
    research_offers_by_key: dict[str, ResearchOfferLike] = {
        rk: roff for roff in (state.research_offers or ()) if (rk := str(roff.research_key or "").strip())
    }
    lab_planet = _select_lab_planet(state, non_raid_planets)
    allow_live_demolition, demolish_floors_by_pid = _resolve_plan_demolish_floors_by_planet(
        plan_result, planets_by_id, planets_by_coords
    )

    actions: list[PlannedAction] = []
    field_conflicts: list[dict[str, object]] = []
    handled_planets: set[int] = set()
    handled_research = False

    for step in steps:
        if not isinstance(step, Mapping):
            continue
        kind = str(step.get("kind") or "").strip().upper()
        if kind in ("COLONIZE_PREVIEW", "COLONIZE", "DEMOLISH", "DEMOLISH_BUILDING"):
            continue
        entity_key = str(step.get("entity_key") or "").strip()
        target_level = coerce_int(step.get("target_level"), 0)
        seq = coerce_int(step.get("seq"), 0)
        if not entity_key or target_level <= 0:
            continue

        if kind == "BUILD":
            action, conflict = reconcile_build_step(
                state,
                step,
                entity_key=entity_key,
                target_level=target_level,
                seq=seq,
                planets_by_id=planets_by_id,
                planets_by_coords=planets_by_coords,
                handled_planets=handled_planets,
                in_flight_buildings=in_flight_buildings,
                planet_queue_counts=planet_queue_counts,
                max_build_slots=max_build_slots,
                allow_multi_planet=allow_multi_planet,
                allow_live_demolition=allow_live_demolition,
                demolish_floors_by_pid=demolish_floors_by_pid,
            )
            if action is not None:
                actions.append(action)
            if conflict is not None:
                field_conflicts.append(conflict)
        elif kind == "RESEARCH" and not handled_research:
            res_action = reconcile_research_step(
                state,
                step,
                config,
                entity_key=entity_key,
                target_level=target_level,
                seq=seq,
                lab_planet=lab_planet,
                in_flight_research=in_flight_research,
                research_queue_busy=research_queue_busy,
                research_offers_by_key=research_offers_by_key,
                allow_multi_planet=allow_multi_planet,
            )
            if res_action is not None:
                actions.append(res_action)
                handled_research = True

    if actions and bool(config.ai_adaptive_execution):
        try:
            actions.extend(plan_live_fill_actions(state, plan_result, config, actions))
        except _FILL_RECOVERABLE_ERRORS as exc:
            _LOGGER.warning("AI-Planner live fill rule failed; continuing with core actions: %s", exc)
    if actions:
        actions = _attach_etas(state, actions)
    return actions, field_conflicts


__all__ = [
    "_field_conflict_candidates",
    "_is_planet_demolish_energy_safe",
    "_is_stale_remote_resource_wait",
    "_resolve_in_flight_buildings",
    "_resolve_in_flight_research",
    "_resolve_plan_demolish_floors_by_planet",
    "reconcile_live_ai_actions",
]
