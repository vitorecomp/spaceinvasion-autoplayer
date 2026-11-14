"""Mutable rollout state protocol and state-query/initialization helpers for ``EmpirePathSimulator``."""

from __future__ import annotations

from collections.abc import Sequence
import heapq
from typing import Protocol

from spaceinvasion.core.coercion import coerce_finite_float, coerce_int
from spaceinvasion.planner.ai._sim_production import ProductionModel
from spaceinvasion.planner.ai._sim_transport import TransportScheduler
from spaceinvasion.planner.ai._sim_types import (
    Blocker,
    FinalMetrics,
    PathStrategyLike,
    PlanetOutput,
    SimProblemData,
    Vec4,
    vec4_from_object,
)
from spaceinvasion.planner.ai._types import PATH_NEVER
from spaceinvasion.planner.config_optimizer import (
    WAREHOUSE_KEYS,
    building_time_divisor,
    research_time_divisor,
    shipyard_time_divisor,
)


class SimMutableState(Protocol):
    """Typed mutable runtime state owned by ``EmpirePathSimulator`` and manipulated by its engines."""

    clock: float
    stock: list[float]
    planet_stock: list[list[float]]
    levels: list[dict[str, int]]
    research: dict[str, int]
    solar: list[int]
    founded: list[bool]
    fields_used: list[int]
    next_step: list[int]
    queue_step: list[int]
    queue_end: list[float]
    queue_initial_count: list[int]
    heap: list[tuple[float, int]]
    started: list[bool]
    order: list[int]
    start: list[float | None]
    end: list[float | None]
    unstarted: int
    unstarted_targets: int
    remaining_builds: list[int]
    need: list[float]
    work_accelerators: list[float]
    work_buildings: list[float]
    work_demolitions: list[float]
    work_ships: list[float]
    work_research: float
    supply_version: int
    totals: list[list[float]]
    output: list[PlanetOutput]
    b2_founded_count: int
    fleet_slots: int
    attack_now: Vec4
    supply: Vec4
    furnace_metal: float
    rate_off: Vec4
    rate_on: Vec4
    planet_rate_off: list[Vec4]
    planet_rate_on: list[Vec4]
    reserved: int
    smelting: bool
    reviews: int
    completions_since_review: int
    review_at: float
    review_every: int
    dirty: set[int]
    run_limit_hours: float
    horizon_need: list[float]
    weekly_resources: list[list[float]]
    loot_collected: list[float]
    spare_diverted: list[float]
    start_fleet_slots: int
    strategy: PathStrategyLike | None

    def _refresh_planet(self, planet: int) -> None: ...
    def _refresh_rates(self) -> None: ...
    def _review(self) -> None: ...
    def duration(self, step: int) -> float: ...
    def blockers(self, step: int) -> list[Blocker]: ...
    def is_next_of_its_chain(self, step: int) -> bool: ...
    def wait_hours(self, stock: Sequence[float], step: int) -> float: ...
    def queue_work(self) -> tuple[list[float], list[float]]: ...


class SimStateQueries:
    """Read-only queue/resource/blocker queries and initial stock/job loaders for ``EmpirePathSimulator``."""

    def __init__(
        self,
        data: SimProblemData,
        final: FinalMetrics,
        prod: ProductionModel,
        transport: TransportScheduler,
    ) -> None:
        self._data = data
        self._final = final
        self._prod = prod
        self._transport = transport

    def init_start_stocks(self, sim: SimMutableState) -> None:
        """Initialize empire and per-colony starting stocks with exact sum consistency."""
        data = self._data
        count = data.planet_count
        raw_stock = vec4_from_object(data.problem.get("start_stock"))
        sim.stock = [raw_stock[0], raw_stock[1], raw_stock[2], raw_stock[3]]
        if all("resources" in planet for planet in data.planets):
            sim.planet_stock = [
                [max(0.0, v) for v in vec4_from_object(planet.get("resources"))] for planet in data.planets
            ]
        else:
            sim.planet_stock = [[0.0, 0.0, 0.0, 0.0] for _ in range(count)]
            if count > 0:
                sim.planet_stock[data.central_planet] = list(sim.stock)
        if count > 0:
            for pos in range(4):
                ps_sum = sum(sim.planet_stock[p][pos] for p in range(count))
                diff = sim.stock[pos] - ps_sum
                if diff > 1e-6:
                    sim.planet_stock[data.central_planet][pos] += diff
                elif data.model_transport_travel:
                    sim.stock[pos] = ps_sum

    def load_initial_jobs(self, sim: SimMutableState) -> None:
        """Pre-load already-paid live in-progress buildings and research onto active queues."""
        data = self._data
        count = data.planet_count
        for job_idx, job in enumerate(data.initial_jobs):
            q = coerce_int(job.get("queue"), -1)
            rem_hr = max(
                1e-6,
                coerce_finite_float(
                    job.get("remaining_hours"),
                    coerce_finite_float(job.get("remaining_seconds"), 60.0) / 3600.0,
                ),
            )
            neg_id = -(2 + job_idx)
            heapq.heappush(sim.heap, (rem_hr, neg_id))
            if 0 <= q < data.queue_count:
                sim.queue_initial_count[q] += 1
                sim.queue_step[q] = neg_id
                sim.queue_end[q] = max(sim.queue_end[q], rem_hr)
            if str(job.get("kind") or "") == "build":
                p_idx = coerce_int(job.get("planet"), -1)
                if 0 <= p_idx < count:
                    sim.fields_used[p_idx] += 1

    def queue_work(self, sim: SimMutableState) -> tuple[list[float], list[float]]:
        """Compute minimum-divisor (``least``) and current-divisor (``slow``) remaining queue hours."""
        data, final = self._data, self._final
        count = data.planet_count
        least = [0.0] * data.queue_count
        for queue in range(data.queue_count):
            if sim.queue_step[queue] != -1:
                least[queue] = max(0.0, sim.queue_end[queue] - sim.clock)
        slow = list(least)
        for planet in range(count):
            levels = sim.levels[planet]
            divisor = float(building_time_divisor(levels.get("b7", 0), levels.get("b17", 0), data.speedup))
            acc_hr = self._prod.accelerator_hours(planet, sim.next_step, final.final_levels)
            least[planet] += acc_hr + sim.work_buildings[planet] / final.final_building_divisor[planet]
            slow[planet] += (sim.work_accelerators[planet] + sim.work_buildings[planet]) / divisor
            yard = count + 1 + planet
            least[yard] += sim.work_ships[planet] / final.final_shipyard_divisor[planet]
            slow[yard] += sim.work_ships[planet] / float(
                shipyard_time_divisor(levels.get("b9", 0), levels.get("b17", 0), data.speedup)
            )
        least[count] += sim.work_research / final.final_research_divisor
        lab_level = sim.levels[data.lab_planet].get("b8", 0) if data.lab_planet >= 0 else 0
        slow[count] += sim.work_research / float(research_time_divisor(lab_level, data.speedup))
        return least, slow

    def missing_resources(self, sim: SimMutableState) -> Vec4:
        """Compute total remaining resources required across all unstarted steps."""
        in_tr = self._transport.total_in_transit
        s0, s1 = sim.stock[0] + in_tr[0], sim.stock[1] + in_tr[1]
        s2, s3 = sim.stock[2] + in_tr[2], sim.stock[3] + in_tr[3]
        metal = max(0.0, sim.need[1] - s1)
        return (
            max(0.0, sim.need[0] + 2.0 * metal - s0),
            metal,
            max(0.0, sim.need[2] - s2),
            max(0.0, sim.need[3] - s3),
        )

    def horizon_missing_resources(self, sim: SimMutableState) -> Vec4:
        """Compute remaining resources required across unstarted steps fitting within the horizon."""
        in_tr = self._transport.total_in_transit
        s0, s1 = sim.stock[0] + in_tr[0], sim.stock[1] + in_tr[1]
        s2, s3 = sim.stock[2] + in_tr[2], sim.stock[3] + in_tr[3]
        metal = max(0.0, sim.horizon_need[1] - s1)
        return (
            max(0.0, sim.horizon_need[0] + 2.0 * metal - s0),
            metal,
            max(0.0, sim.horizon_need[2] - s2),
            max(0.0, sim.horizon_need[3] - s3),
        )

    def full_horizon_hours(self, sim: SimMutableState) -> float:
        """Estimate minimum hours required to finish all remaining steps at target steady-state supply."""
        work, _ = self.queue_work(sim)
        missing = self.missing_resources(sim)
        horizon = max(work) if work else 0.0
        for position in range(4):
            if missing[position] > 0.0:
                supply = self._final.final_supply[position]
                horizon = max(horizon, missing[position] / supply if supply > 0.0 else PATH_NEVER)
        return horizon

    def blockers(self, sim: SimMutableState, step: int) -> list[Blocker]:
        """Return causal blockers preventing ``step`` from starting at the current state."""
        data = self._data
        result: list[Blocker] = []
        kind, planet = data.kind[step], data.step_planet[step]
        if planet >= 0 and kind != "colonize" and not sim.founded[planet]:
            result.append(("colony", planet))
        for owner, key, level in data.requires[step]:
            have = sim.research.get(key, 0) if owner < 0 else sim.levels[owner].get(key, 0)
            if have < level:
                result.append(("level", owner, key, level))
        pay_planet = data.pay_planet[step]
        pay_levels = sim.levels[pay_planet]
        for position, needed in enumerate(data.warehouse_levels[step]):
            if needed > 0 and needed > pay_levels.get(WAREHOUSE_KEYS[position], 0):
                result.append(("storage", pay_planet, position, needed))
        if kind == "build" and sim.fields_used[planet] >= data.fields_max[planet]:
            result.append(("field", planet))
        return result

    def wait_hours(self, sim: SimMutableState, stock: Sequence[float], step: int) -> float:
        """Compute empire-level wait hours until ``stock`` covers ``step``'s cost with smelting toggle."""
        cost, floor = self._data.cost[step], self._data.cost_floor[step]
        wait = 0.0
        for position in (2, 3):
            if stock[position] < floor[position]:
                rate = sim.rate_off[position]
                if rate <= 0.0:
                    return PATH_NEVER
                wait = max(wait, (cost[position] - stock[position]) / rate)
        pig_iron_lacking = cost[0] - stock[0]
        smelting_hours = 0.0
        if stock[1] < floor[1]:
            if sim.rate_on[1] <= 0.0:
                return PATH_NEVER
            smelting_hours = (cost[1] - stock[1]) / sim.rate_on[1]
            pig_iron_lacking -= sim.rate_on[0] * smelting_hours
        if pig_iron_lacking > cost[0] - floor[0]:
            if sim.rate_off[0] <= 0.0:
                return PATH_NEVER
            smelting_hours += pig_iron_lacking / sim.rate_off[0]
        return max(wait, smelting_hours)
