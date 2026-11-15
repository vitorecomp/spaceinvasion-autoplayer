"""Plan v4 live fill rule: opportunistic later plan steps while a colony's top step waits."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from backend.core import catalog as game_data
from backend.core.coercion import coerce_finite_float, coerce_int
from backend.core.model import PlannedAction, Resources
from backend.planner.ai._fill_helpers import (
    AI_FILL_NEVER_HOURS,
    AI_FILL_POOL_LEAD_HOURS,
    AI_STEP_SEQ_RE,
    _hours_until_paid,
    collect_waiting_colonies,
    compute_pool_wait_hours,
    fill_respects_delay_bounds,
    make_fill_evaluators,
    resolve_fill_offer_cost,
    summarize_empire_commitments,
)
from backend.planner.ai._problem_in_progress import compute_in_progress_research_rem_seconds
from backend.planner.ai._problem_queues import compute_inbound_friendly_cargo_by_planet
from backend.planner.ai._reconcile_helpers import (
    PlannedActionLike,
    ReadConfigLike,
    _resolve_in_flight_buildings,
    index_planets,
    match_step_planet,
    resource_value,
    resources_float_list,
)
from backend.planner.ai._types import RESOURCE_DISPLAY_LABELS, GameStateLike, PlanetLike, safe_speedup
from backend.planner.config_optimizer import RESOURCE_KEYS

AI_FILL_REASON_PREFIX: str = "AI-Planner fill"
AI_FILL_MIN_PATIENCE: float = 0.25
AI_FILL_MAX_PATIENCE: float = 0.75
_AI_FILL_NEVER_HOURS: float = AI_FILL_NEVER_HOURS
_AI_STEP_SEQ_RE = AI_STEP_SEQ_RE


def plan_fill_patience(plan_result: Mapping[str, object]) -> float:
    """Share of a fill step's own duration by which it may hold the waiting step back."""
    raw: object = None
    search = plan_result.get("search") if isinstance(plan_result, Mapping) else None
    if isinstance(search, Mapping):
        tuned = search.get("tuned_genes")
        if isinstance(tuned, Mapping):
            raw = tuned.get("patience")
    if raw is None:
        meta = plan_result.get("meta_parameters") if isinstance(plan_result, Mapping) else None
        if isinstance(meta, Mapping):
            raw = meta.get("patience")
    val = coerce_finite_float(raw, AI_FILL_MIN_PATIENCE)
    return min(AI_FILL_MAX_PATIENCE, max(AI_FILL_MIN_PATIENCE, val))


_plan_fill_patience = plan_fill_patience


def _evaluate_fill_step(
    state: GameStateLike,
    step: Mapping[str, object],
    *,
    planets_by_id: Mapping[int, PlanetLike],
    planets_by_coords: Mapping[str, PlanetLike],
    waiting: Mapping[int, PlannedActionLike],
    filled: set[int],
    in_flight_buildings: Mapping[int, Mapping[str, int]],
    spare: list[float],
    ready_cost_by_pid: Mapping[int, Sequence[float]],
    research_cost_by_pid: Mapping[int, Sequence[float]],
    inbound_by_pid: Mapping[int, Resources],
    pool_wait_build: Mapping[int, float],
    pool_wait_research: Mapping[int, float],
    duration_hours: Callable[[PlanetLike, str, int], float],
    rates_fn: Callable[[PlanetLike], list[float]],
    patience: float,
    research_rem_h: float,
) -> PlannedAction | None:
    if str(step.get("kind") or "").strip().upper() != "BUILD":
        return None
    entity_key = str(step.get("entity_key") or "").strip()
    target_level = coerce_int(step.get("target_level"), 0)
    if target_level <= 0 or not (entity_key.startswith("b") and entity_key[1:].isdigit()):
        return None
    planet = match_step_planet(step, planets_by_id, planets_by_coords)
    if planet is None:
        return None
    pid = int(planet.planet_id)
    top = waiting.get(pid)
    bid = int(entity_key[1:])
    b_info = game_data.BUILDING_BY_ID.get(bid)
    if top is None or pid in filled or not b_info or bid == int(top.target_id or -1):
        return None
    live_level = planet.building_level(bid)
    if max(live_level, in_flight_buildings.get(pid, {}).get(entity_key, 0)) >= target_level:
        return None
    if target_level != live_level + 1:
        return None
    resolved = resolve_fill_offer_cost(state, planet, bid, target_level)
    if resolved is None:
        return None
    cost, href = resolved
    cost_f = resources_float_list(cost)
    if any(cost_f[pos] > 0.0 and spare[pos] < cost_f[pos] for pos in range(4)):
        return None
    ready_cost = ready_cost_by_pid.get(pid) or (0.0, 0.0, 0.0, 0.0)
    stock = [max(0.0, val - ready_cost[pos]) for pos, val in enumerate(resources_float_list(planet.resources))]
    if any(stock[pos] < cost_f[pos] for pos in range(4)):
        return None
    if not fill_respects_delay_bounds(
        cost_f=cost_f,
        stock=stock,
        inbound=resources_float_list(inbound_by_pid.get(pid)),
        rate=rates_fn(planet),
        dur_h=duration_hours(planet, entity_key, target_level),
        patience=patience,
        top_cost=resources_float_list(top.cost),
        pool_top=pool_wait_build.get(pid, AI_FILL_NEVER_HOURS),
        research_cost=research_cost_by_pid.get(pid),
        pool_research=pool_wait_research.get(pid, AI_FILL_NEVER_HOURS),
        research_rem_h=research_rem_h,
    ):
        return None

    seq = coerce_int(step.get("seq"), 0)
    b_name = str(step.get("entity_name") or b_info["name"])
    short_txt = ", ".join(RESOURCE_DISPLAY_LABELS[r] for r in RESOURCE_KEYS if resource_value(top.missing, r) > 0)
    filled.add(pid)
    for pos in range(4):
        spare[pos] -= cost_f[pos]
    return PlannedAction(
        priority="P1",
        kind="BUILD",
        planet_id=pid,
        planet_coords=str(planet.coords),
        target_id=bid,
        target_name=b_name,
        target_level=target_level,
        cost=cost,
        missing=Resources(),
        reason=(
            f"{AI_FILL_REASON_PREFIX} #{seq}: build {b_name} L{target_level} while "
            f"{top.target_name} L{top.target_level} waits for {short_txt}"
        ),
        executable_now=True,
        action_href=href,
    )


def plan_live_fill_actions(
    state: GameStateLike,
    plan_result: Mapping[str, object],
    config: ReadConfigLike,
    actions: Sequence[PlannedActionLike],
) -> list[PlannedAction]:
    """Plan v4 live fill rule: start a later plan step on an idle colony while its next step waits."""
    steps = plan_result.get("steps") if isinstance(plan_result, Mapping) else None
    if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)) or not steps or not actions:
        return []
    planets_by_id, planets_by_coords, _ = index_planets(state.planets or (), normal_only=True)
    if not planets_by_id:
        return []

    in_flight_buildings, planet_queue_counts = _resolve_in_flight_buildings(state)
    inbound_by_pid = compute_inbound_friendly_cargo_by_planet(state)
    research_rem_h = max(0.0, float(compute_in_progress_research_rem_seconds(state) or 0.0)) / 3600.0
    patience = _plan_fill_patience(plan_result)
    speedup = safe_speedup(state, {})

    top_by_pid, research_cost_by_pid, ready_cost_by_pid, pooled, spare = summarize_empire_commitments(
        actions, planets_by_id, inbound_by_pid
    )
    waiting = collect_waiting_colonies(
        state,
        top_by_pid,
        planets_by_id,
        planet_queue_counts,
        allow_multi_planet=bool(config.allow_multi_planet),
    )
    if not waiting:
        return []

    duration_hours, rates_fn = make_fill_evaluators(state, speedup)
    pool_wait_build, pool_wait_research = compute_pool_wait_hours(actions, planets_by_id, pooled, rates_fn)

    out: list[PlannedAction] = []
    filled: set[int] = set()
    for step in steps:
        if len(filled) >= len(waiting):
            break
        if not isinstance(step, Mapping):
            continue
        fill_action = _evaluate_fill_step(
            state,
            step,
            planets_by_id=planets_by_id,
            planets_by_coords=planets_by_coords,
            waiting=waiting,
            filled=filled,
            in_flight_buildings=in_flight_buildings,
            spare=spare,
            ready_cost_by_pid=ready_cost_by_pid,
            research_cost_by_pid=research_cost_by_pid,
            inbound_by_pid=inbound_by_pid,
            pool_wait_build=pool_wait_build,
            pool_wait_research=pool_wait_research,
            duration_hours=duration_hours,
            rates_fn=rates_fn,
            patience=patience,
            research_rem_h=research_rem_h,
        )
        if fill_action is not None:
            out.append(fill_action)
    return out


__all__ = [
    "AI_FILL_MAX_PATIENCE",
    "AI_FILL_MIN_PATIENCE",
    "AI_FILL_POOL_LEAD_HOURS",
    "AI_FILL_REASON_PREFIX",
    "AI_STEP_SEQ_RE",
    "_hours_until_paid",
    "_plan_fill_patience",
    "plan_fill_patience",
    "plan_live_fill_actions",
]
