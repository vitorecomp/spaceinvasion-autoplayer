"""Commitment summaries, pooled wait timing, and delay-bound helpers for Plan v4 live fills."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import re
from typing import cast

from spaceinvasion.combat.points import estimate_planet_hourly_economy
from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coercion import coerce_finite_float, coerce_int
from spaceinvasion.core.model import (
    GameState as LegacyGameState,
)
from spaceinvasion.core.model import (
    PlanetState as LegacyPlanetState,
)
from spaceinvasion.core.model import (
    Resources,
    is_building_allowed_on_planet_type,
)
from spaceinvasion.planner.ai._reconcile_helpers import (
    PlannedActionLike,
    _is_stale_remote_resource_wait,
    offers_by_building_id,
    resources_can_afford,
    resources_float_list,
    resources_is_zero,
    storage_exceeds_cost,
)
from spaceinvasion.planner.ai._types import GameStateLike, PlanetLike, coerce_resources_obj
from spaceinvasion.planner.config_optimizer import (
    RESOURCE_KEYS,
    build_monotonic_base_times,
    building_time_divisor,
    full_cost,
)

AI_FILL_POOL_LEAD_HOURS: float = 1.0 / 3.0
AI_FILL_NEVER_HOURS: float = 1e9
AI_STEP_SEQ_RE: re.Pattern[str] = re.compile(r"^AI-Planner #(\d+):", re.ASCII)


def _hours_until_paid(
    need: Sequence[float],
    stock: Sequence[float],
    inbound: Sequence[float],
    rate: Sequence[float],
) -> float:
    """Hours until ``stock + inbound + rate * t`` covers ``need`` in every resource."""
    worst = 0.0
    for pos in range(4):
        gap = float(need[pos]) - float(stock[pos]) - float(inbound[pos])
        if gap <= 0.0:
            continue
        if rate[pos] <= 0.0:
            return AI_FILL_NEVER_HOURS
        worst = max(worst, gap / float(rate[pos]))
    return min(worst, AI_FILL_NEVER_HOURS)


def summarize_empire_commitments(
    actions: Sequence[PlannedActionLike],
    planets_by_id: Mapping[int, PlanetLike],
    inbound_by_pid: Mapping[int, Resources],
) -> tuple[
    dict[int, PlannedActionLike],
    dict[int, list[float]],
    dict[int, list[float]],
    list[float],
    list[float],
]:
    """Compute per-colony top actions, research reservations, pooled empire stock, and spare stock."""
    top_by_pid: dict[int, PlannedActionLike] = {}
    research_cost_by_pid: dict[int, list[float]] = {}
    ready_cost_by_pid: dict[int, list[float]] = {}
    committed = [0.0, 0.0, 0.0, 0.0]
    for act in actions:
        if act.kind in ("BUILD", "WAIT_RESOURCES", "DEMOLISH_BUILDING"):
            top_by_pid.setdefault(int(act.planet_id), act)
        if act.kind not in ("BUILD", "WAIT_RESOURCES", "RESEARCH"):
            continue
        cost_vals = resources_float_list(act.cost)
        for pos in range(4):
            committed[pos] += cost_vals[pos]
        if act.kind == "RESEARCH":
            by_pid = ready_cost_by_pid if act.executable_now else research_cost_by_pid
            buf = by_pid.setdefault(int(act.planet_id), [0.0, 0.0, 0.0, 0.0])
            for pos in range(4):
                buf[pos] += cost_vals[pos]

    pooled = [0.0, 0.0, 0.0, 0.0]
    for planet in planets_by_id.values():
        for pos, val in enumerate(resources_float_list(planet.resources)):
            pooled[pos] += val
    for cargo in inbound_by_pid.values():
        for pos, val in enumerate(resources_float_list(cargo)):
            pooled[pos] += val
    spare = [pooled[pos] - committed[pos] for pos in range(4)]
    return top_by_pid, research_cost_by_pid, ready_cost_by_pid, pooled, spare


def collect_waiting_colonies(
    state: GameStateLike,
    top_by_pid: Mapping[int, PlannedActionLike],
    planets_by_id: Mapping[int, PlanetLike],
    planet_queue_counts: Mapping[int, int],
    *,
    allow_multi_planet: bool,
) -> dict[int, PlannedActionLike]:
    """Select normal colonies with a free builder, >= 2 free fields, and an unaffordable waiting top step."""
    waiting: dict[int, PlannedActionLike] = {}
    for pid, top in top_by_pid.items():
        planet = planets_by_id.get(pid)
        if planet is None or top.kind != "WAIT_RESOURCES" or top.executable_now or resources_is_zero(top.missing):
            continue
        if top.target_id is None or not top.target_level or planet_queue_counts.get(pid, 0) >= 1:
            continue
        is_remote = state.active_planet_id is not None and pid != int(state.active_planet_id)
        if is_remote and not allow_multi_planet:
            continue
        if planet.fields_max > 0 and planet.fields_max - planet.fields_used < 2:
            continue
        waiting[pid] = top
    return waiting


def compute_pool_wait_hours(
    actions: Sequence[PlannedActionLike],
    planets_by_id: Mapping[int, PlanetLike],
    pooled: Sequence[float],
    rates_fn: Callable[[PlanetLike], list[float]],
) -> tuple[dict[int, float], dict[int, float]]:
    """Estimate plan-order pooled funding hours for each colony's top building step and research step."""
    income = [0.0, 0.0, 0.0, 0.0]
    for planet in planets_by_id.values():
        for pos, val in enumerate(rates_fn(planet)):
            income[pos] += val
    ranked: list[tuple[int, PlannedActionLike]] = []
    for act in actions:
        if act.kind not in ("BUILD", "WAIT_RESOURCES", "RESEARCH"):
            continue
        m_seq = AI_STEP_SEQ_RE.match(str(act.reason or ""))
        ranked.append((int(m_seq.group(1)) if m_seq else 10**9, act))
    ranked.sort(key=lambda item: item[0])

    pool_wait_build: dict[int, float] = {}
    pool_wait_research: dict[int, float] = {}
    cumulative = [0.0, 0.0, 0.0, 0.0]
    for _seq, act in ranked:
        hours = 0.0
        for pos, val in enumerate(resources_float_list(act.cost)):
            cumulative[pos] += val
            gap = cumulative[pos] - pooled[pos]
            if gap > 0.0:
                hours = max(hours, gap / income[pos] if income[pos] > 0.0 else AI_FILL_NEVER_HOURS)
        by_kind = pool_wait_research if act.kind == "RESEARCH" else pool_wait_build
        by_kind.setdefault(int(act.planet_id), max(AI_FILL_POOL_LEAD_HOURS, min(hours, AI_FILL_NEVER_HOURS)))
    return pool_wait_build, pool_wait_research


def resolve_fill_offer_cost(
    state: GameStateLike,
    planet: PlanetLike,
    bid: int,
    target_level: int,
) -> tuple[Resources, str | None] | None:
    """Validate prerequisites, storage, and local affordability for a candidate fill step."""
    offers_by_id = offers_by_building_id(planet)
    if not is_building_allowed_on_planet_type(planet, bid, has_b8_offer=(8 in offers_by_id)):
        return None
    if game_data.missing_prerequisites("buildings", bid, buildings=planet.buildings, researches=state.researches):
        return None
    wiki_cost = Resources(**full_cost(game_data.building_cost(bid, target_level)))
    if storage_exceeds_cost(planet, bid, wiki_cost):
        return None

    pid = int(planet.planet_id)
    is_remote = state.active_planet_id is not None and pid != int(state.active_planet_id)
    offer = offers_by_id.get(bid)
    if offer is not None and coerce_int(offer.target_level, target_level) == target_level:
        cost = coerce_resources_obj(offer.cost)
        can_exec = (
            bool(offer.can_build) or (_is_stale_remote_resource_wait(offer) and not resources_is_zero(cost))
        ) and resources_can_afford(planet.resources, cost)
        href = (str(offer.build_href) or None) if (can_exec and offer.can_build and offer.build_href) else None
    else:
        cost = wiki_cost
        can_live_verify = (
            is_remote or (not offers_by_id) or (offer is not None and coerce_int(offer.target_level, 0) < target_level)
        )
        can_exec = can_live_verify and (not resources_is_zero(cost)) and resources_can_afford(planet.resources, cost)
        href = None
    return (cost, href) if can_exec else None


def fill_respects_delay_bounds(
    *,
    cost_f: Sequence[float],
    stock: Sequence[float],
    inbound: Sequence[float],
    rate: Sequence[float],
    dur_h: float,
    patience: float,
    top_cost: Sequence[float],
    pool_top: float,
    research_cost: Sequence[float] | None,
    pool_research: float,
    research_rem_h: float,
) -> bool:
    """Return True when paying ``cost_f`` and running for ``dur_h`` stays within patience bounds."""
    after = [stock[pos] - cost_f[pos] for pos in range(4)]
    allowed_delay = patience * dur_h + 1e-9
    start_now = min(_hours_until_paid(top_cost, stock, inbound, rate), pool_top)
    start_after = min(_hours_until_paid(top_cost, after, inbound, rate), pool_top)
    if max(start_after, dur_h) - start_now > allowed_delay:
        return False
    if any(
        cost_f[pos] > 0.0 and rate[pos] <= 0.0 and top_cost[pos] - after[pos] - inbound[pos] > 0.0 for pos in range(4)
    ):
        return False
    if research_cost is not None:
        need_both = [top_cost[pos] + research_cost[pos] for pos in range(4)]
        research_now = max(min(_hours_until_paid(need_both, stock, inbound, rate), pool_research), research_rem_h)
        research_after = max(min(_hours_until_paid(need_both, after, inbound, rate), pool_research), research_rem_h)
        if research_after - research_now > allowed_delay:
            return False
        if any(
            cost_f[pos] > 0.0 and rate[pos] <= 0.0 and need_both[pos] - after[pos] - inbound[pos] > 0.0
            for pos in range(4)
        ):
            return False
    return True


def make_fill_evaluators(
    state: GameStateLike,
    speedup: float,
) -> tuple[Callable[[PlanetLike, str, int], float], Callable[[PlanetLike], list[float]]]:
    """Build cached duration-hour and hourly-rate evaluators for live fill checks."""
    base_times_cache: dict[str, dict[int, float]] = {}
    rates_cache: dict[int, list[float]] = {}

    def _duration_hours(planet: PlanetLike, key: str, level: int) -> float:
        if key not in base_times_cache:
            info = game_data.BUILDING_BY_ID[int(key[1:])]
            levels_map = info.get("levels") or {}
            top_level = max(int(info.get("max_table_level") or len(levels_map)), level)
            base_times_cache[key] = build_monotonic_base_times(levels_map, top_level)
        base_h = float(base_times_cache[key].get(level, 0.0)) / 3600.0
        div = building_time_divisor(planet.building_level(7), planet.building_level(17), speedup)
        return base_h / max(1e-9, div)

    def _rates(planet: PlanetLike) -> list[float]:
        pid = int(planet.planet_id)
        if pid not in rates_cache:
            econ = estimate_planet_hourly_economy(cast(LegacyGameState, state), cast(LegacyPlanetState, planet))
            rates_cache[pid] = [max(0.0, coerce_finite_float(econ.get(f"{r}_per_hr"), 0.0)) for r in RESOURCE_KEYS]
        return rates_cache[pid]

    return _duration_hours, _rates
