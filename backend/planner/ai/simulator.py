"""Event-driven W-week empire rollout simulator composing explicit domain collaborators."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from backend.core.coercion import coerce_int
from backend.planner.ai._sim_engine import RolloutEngine
from backend.planner.ai._sim_production import ProductionModel
from backend.planner.ai._sim_proposals import ProposalDispatcher
from backend.planner.ai._sim_queries import SimStateQueries
from backend.planner.ai._sim_setup import build_problem_data, compute_final_metrics
from backend.planner.ai._sim_smelting import SmeltingTracker
from backend.planner.ai._sim_transport import TransportScheduler
from backend.planner.ai._sim_types import (
    Blocker,
    FinalMetrics,
    PathStrategyLike,
    PlanetOutput,
    SimProblemData,
    Vec4,
    int_mapping,
)
from backend.planner.ai._types import PATH_NEVER
from backend.planner.ai.problem import planet_hourly_output
from backend.planner.config_optimizer import PRODUCTION_FLOW_KEYS


class EmpirePathSimulator:
    """Event-driven W-week empire rollout simulator matching ``full_planner.ipynb`` §11.4."""

    def __init__(
        self,
        problem: Mapping[str, object],
        attack_income: Mapping[str, float],
        *,
        slot_attack_income: Mapping[str, float] | None = None,
        fleet_command_center_level: int | None = None,
        spare_percent: Mapping[str, float] | None = None,
        reward_weeks: int = 26,
        weekly_discount: float = 0.90,
        transport_dispatch_delay_minutes: float = 120.0,
        model_transport_travel: bool = True,
    ) -> None:
        data = build_problem_data(
            problem,
            attack_income,
            slot_attack_income=slot_attack_income,
            fleet_command_center_level=fleet_command_center_level,
            spare_percent=spare_percent,
            reward_weeks=reward_weeks,
            weekly_discount=weekly_discount,
            transport_dispatch_delay_minutes=transport_dispatch_delay_minutes,
            model_transport_travel=model_transport_travel,
        )
        self._data = data
        self._prod = ProductionModel(data)
        self._final = compute_final_metrics(data, self._prod)
        self._transport = TransportScheduler(data)
        self._smelt = SmeltingTracker(data)
        self._queries = SimStateQueries(data, self._final, self._prod, self._transport)
        self._engine = RolloutEngine(data, self._final, self._transport, self._smelt)
        self._dispatcher = ProposalDispatcher(data, self._final, self._prod, self._transport, self._smelt, self._engine)
        self._bind_static(data, self._final)
        self.strategy: PathStrategyLike | None = None
        self._load_start()

    def _bind_static(self, data: SimProblemData, final: FinalMetrics) -> None:
        self.spare_percent = data.spare_percent
        self.keep = data.keep
        self.fleet_command_center_level = data.fleet_command_center_level
        self.reward_weeks = data.reward_weeks
        self.weekly_discount = data.weekly_discount
        self.transport_dispatch_delay_minutes = data.transport_dispatch_delay_minutes
        self.dispatch_delay_hours = data.dispatch_delay_hours
        self.horizon_hours = data.horizon_hours
        self.week_weights = data.week_weights
        self.suffix_discounted_hours = data.suffix_discounted_hours
        self.merchant_rates = data.merchant_rates
        self.problem = data.problem
        self.planets = data.planets
        self.steps = data.steps
        self.speedup = data.speedup
        self.fixed_attack = data.fixed_attack
        self.slot_attack = data.slot_attack
        self.attack = data.attack
        self.lab_planet = data.lab_planet
        self.central_planet = data.central_planet
        self.planet_count = data.planet_count
        self.research_queue = data.research_queue
        self.queue_count = data.queue_count
        self.temperature = data.temperature
        self.fields_max = data.fields_max
        self.model_transport_travel_requested = data.model_transport_travel_requested
        self.model_transport_travel = data.model_transport_travel
        self.initial_jobs = data.initial_jobs
        self.initial_arrivals = data.initial_arrivals
        self.initial_blast_furnace = data.initial_blast_furnace
        self.kind = data.kind
        self.step_planet = data.step_planet
        self.key = data.key
        self.level_to = data.level_to
        self.cost = data.cost
        self.cost_floor = data.cost_floor
        self.cost_spice_eq = data.cost_spice_eq
        self.base_hours = data.base_hours
        self.queue = data.queue
        self.pay_planet = data.pay_planet
        self.requires = data.requires
        self.warehouse_levels = data.warehouse_levels
        self.chain_steps = data.chains.chain_steps
        self.step_chain = data.chains.step_chain
        self.build_chains = data.chains.build_chains
        self.demolish_chains = data.chains.demolish_chains
        self.ship_chains = data.chains.ship_chains
        self.colonize_step = data.chains.colonize_step
        self.research_chains = data.chains.research_chains
        self._target_step_count = data.chains.target_step_count
        self.research_dependents = data.chains.research_dependents
        self.final_levels = final.final_levels
        self.final_solar = final.final_solar
        self.final_building_divisor = final.final_building_divisor
        self.final_shipyard_divisor = final.final_shipyard_divisor
        self.final_research_divisor = final.final_research_divisor
        self.final_output = final.final_output
        self.final_fleet_slots = final.final_fleet_slots
        self.final_supply = final.final_supply
        self.final_output_total = final.final_output_total
        self.final_net_output_total = final.final_net_output_total
        self.in_horizon = final.in_horizon
        self.horizon_need_init = final.horizon_need_init
        self.reward_ceiling_spice_eq_per_hour = final.reward_ceiling_spice_eq_per_hour
        self.reward_upper_bound = final.reward_upper_bound

    @property
    def travel_hours(self) -> list[list[float]]:
        return self._transport.travel_hours

    @property
    def lead_hours(self) -> list[list[float]]:
        return self._transport.lead_hours

    @property
    def smelting_spans(self) -> list[list[float]]:
        return self._smelt.smelting_spans

    @property
    def smelting_events(self) -> list[dict[str, object]]:
        return self._smelt.smelting_events

    @property
    def metal_smelted(self) -> float:
        return self._smelt.metal_smelted

    def remaining_discounted_hours(self, hour: float) -> float:
        return self._prod.remaining_discounted_hours(hour)

    def level_flows(self, key: str, level: int, temperature: int, mining: int, energy: int) -> tuple[float, ...]:
        return self._prod.level_flows(key, level, temperature, mining, energy)

    def planet_totals(
        self, planet: int, levels: Mapping[str, int], solar_cells: int, mining: int, energy: int
    ) -> list[float]:
        return self._prod.planet_totals(planet, levels, solar_cells, mining, energy)

    def changed_totals(self, planet: int, changes: Mapping[str, int]) -> list[float]:
        return self._prod.changed_totals(
            planet, self.totals[planet], self.levels[planet], self.research["r1"], self.research["r2"], changes
        )

    def fleet_slots_of(self, levels_list: Sequence[Mapping[str, int]], founded: Sequence[bool]) -> int:
        return self._prod.fleet_slots_of(levels_list, founded)

    def gross_attack_of(self, slots: int) -> Vec4:
        return self._prod.gross_attack_of(slots)

    def supply_of(
        self, outputs: Sequence[tuple[float, ...]], founded: Sequence[bool], slots: int | None = None
    ) -> Vec4:
        return self._prod.supply_of(outputs, founded, self.levels, slots)

    def accelerator_hours(self, planet: int) -> float:
        return self._prod.accelerator_hours(planet, self.next_step, self.final_levels)

    def queue_work(self) -> tuple[list[float], list[float]]:
        return self._queries.queue_work(self)

    def missing_resources(self) -> Vec4:
        return self._queries.missing_resources(self)

    def horizon_missing_resources(self) -> Vec4:
        return self._queries.horizon_missing_resources(self)

    def duration(self, step: int) -> float:
        return self._prod.step_duration(step, self.levels)

    def blockers(self, step: int) -> list[Blocker]:
        return self._queries.blockers(self, step)

    def is_next_of_its_chain(self, step: int) -> bool:
        chain = self.step_chain[step]
        position = self.next_step[chain]
        return position < len(self.chain_steps[chain]) and self.chain_steps[chain][position] == step

    def wait_hours(self, stock: Sequence[float], step: int) -> float:
        return self._queries.wait_hours(self, stock, step)

    def lower_bounds(self) -> list[tuple[str, float]]:
        """Reset to start state and compute theoretical completion-hour lower bounds."""
        self._load_start()
        return self._dispatcher.compute_lower_bounds(self)

    def _load_start(self) -> None:
        data = self._data
        count = data.planet_count
        self.clock: float = 0.0
        self.stock: list[float] = [0.0, 0.0, 0.0, 0.0]
        self.planet_stock: list[list[float]] = []
        self._queries.init_start_stocks(self)
        self.levels: list[dict[str, int]] = [int_mapping(p.get("levels")) for p in data.planets]
        self.research: dict[str, int] = int_mapping(data.problem.get("research_levels"))
        self.solar: list[int] = [coerce_int(p.get("solar_cells"), 0) for p in data.planets]
        self.founded: list[bool] = [bool(p.get("founded")) for p in data.planets]
        self.fields_used: list[int] = [sum(lvls.values()) for lvls in self.levels]
        self.next_step: list[int] = [0] * len(self.chain_steps)
        self.queue_step: list[int] = [-1] * self.queue_count
        self.queue_end: list[float] = [0.0] * self.queue_count
        self.queue_initial_count: list[int] = [0] * self.queue_count
        self.heap: list[tuple[float, int]] = []
        self._transport.reset()
        self._queries.load_initial_jobs(self)
        self.started: list[bool] = [False] * len(self.steps)
        self.order: list[int] = []
        self.start: list[float | None] = [None] * len(self.steps)
        self.end: list[float | None] = [None] * len(self.steps)
        self.unstarted: int = len(self.steps)
        self.unstarted_targets: int = self._target_step_count
        self.remaining_builds: list[int] = list(data.chains.builds_per_planet)
        self.need: list[float] = [sum(cost[pos] for cost in self.cost) for pos in range(4)]
        self.work_accelerators: list[float] = [0.0] * count
        self.work_buildings: list[float] = [0.0] * count
        self.work_demolitions: list[float] = [0.0] * count
        self.work_ships: list[float] = [0.0] * count
        self.work_research: float = 0.0
        for step in range(len(self.steps)):
            self._engine.count_work(self, step, 1.0)
        for planet in range(count):
            self._engine.sync_demolish_work(self, planet)
        self.supply_version: int = 0
        self.totals: list[list[float]] = [[0.0] * len(PRODUCTION_FLOW_KEYS) for _ in range(count)]
        self.output: list[PlanetOutput] = [(0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0)] * count
        for planet in range(count):
            self._refresh_planet(planet)
        self._refresh_rates()
        self.reserved: int = -1
        self.smelting: bool = False
        self._smelt.reset()
        self.reviews: int = 0
        self.completions_since_review: int = 0
        self.review_at: float = PATH_NEVER
        self.review_every: int = 1
        self.dirty: set[int] = set(range(count + 1))
        self.run_limit_hours: float = self.horizon_hours
        self.horizon_need: list[float] = list(self.horizon_need_init)
        self.weekly_resources: list[list[float]] = [[0.0, 0.0, 0.0, 0.0] for _ in range(self.reward_weeks)]
        self.loot_collected: list[float] = [0.0, 0.0, 0.0, 0.0]
        self.spare_diverted: list[float] = [0.0, 0.0, 0.0, 0.0]
        self.start_fleet_slots: int = self.fleet_slots

    def _refresh_planet(self, planet: int) -> None:
        self.totals[planet] = self.planet_totals(
            planet, self.levels[planet], self.solar[planet], self.research["r1"], self.research["r2"]
        )
        self.output[planet] = planet_hourly_output(self.totals[planet])

    def _refresh_rates(self) -> None:
        snap = self._prod.compute_rates(self.levels, self.founded, self.output)
        self.b2_founded_count = snap.b2_founded_count
        self.fleet_slots = snap.fleet_slots
        self.attack_now = snap.attack_now
        self.supply = snap.supply
        self.supply_version += 1
        self.furnace_metal = snap.furnace_metal
        self.rate_off = snap.rate_off
        self.rate_on = snap.rate_on
        self.planet_rate_off = snap.planet_rate_off
        self.planet_rate_on = snap.planet_rate_on

    def _review(self) -> None:
        if self.strategy is None:
            return
        self.reviews += 1
        self.completions_since_review = 0
        horizon, self.review_every, look_again = self.strategy.review(self)
        if 0.0 < horizon < PATH_NEVER:
            self.review_at = self.clock + max(0.02 * horizon, 1e-3, 1e-9 * self.clock)
        else:
            self.review_at = PATH_NEVER
        if look_again:
            self.dirty.update(range(self.planet_count + 1))

    def _decide(self) -> None:
        if self.strategy is None:
            return
        self._transport.early_dispatch_at = PATH_NEVER
        for _ in range(self.queue_count + 2):
            if not self._dispatcher.start_proposals(self, self.strategy.proposals(self, forced=False)):
                break
        if self.reserved < 0 and not self.heap and not self._transport.transit_heap and self.unstarted_targets > 0:
            allow_forced = (
                self.clock == 0.0
                or self.run_limit_hours == PATH_NEVER
                or self._queries.full_horizon_hours(self) <= self.run_limit_hours
            )
            if allow_forced:
                for _ in range(self.queue_count + 2):
                    if not self._dispatcher.start_proposals(self, self.strategy.proposals(self, forced=True)):
                        break
        self._smelt.record_transition(self.clock, self.smelting, self.b2_founded_count, self.reserved)

    def _reset(self, strategy: PathStrategyLike, *, limit_hours: float | None = None) -> None:
        self._load_start()
        self.run_limit_hours = self.horizon_hours if limit_hours is None else float(limit_hours)
        self.strategy = strategy
        strategy.begin(self)
        self._review()

    def run(self, strategy: PathStrategyLike, *, horizon_hours: float | None = None) -> dict[str, object]:
        """Run the event-driven simulation under ``strategy`` until completion or ``horizon_hours``."""
        limit = self.horizon_hours if horizon_hours is None else float(horizon_hours)
        self._reset(strategy, limit_hours=limit)
        events = 0
        event_limit = 160 * len(self.steps) + 20_000
        status = "reached"
        while True:
            if self.clock < limit:
                self._decide()
            if self.unstarted_targets == 0 and not self.heap:
                status = "reached"
                break
            if self.clock >= limit:
                status = "horizon"
                break
            elapsed, until_done, until_transit = self._engine.next_event_step(self)
            if elapsed == PATH_NEVER:
                if self.reserved < 0 and limit < PATH_NEVER:
                    self._engine.advance(self, max(0.0, limit - self.clock), ends_a_step=False, ends_a_transit=False)
                    status = "reached" if (self.unstarted_targets == 0 and not self.heap) else "horizon"
                else:
                    status = "stuck"
                break
            until_limit = limit - self.clock
            if until_limit < elapsed:
                self._engine.advance(self, max(0.0, until_limit), ends_a_step=False, ends_a_transit=False)
                status = "reached" if (self.unstarted_targets == 0 and not self.heap) else "horizon"
                break
            events += 1
            if events > event_limit:
                raise RuntimeError(f"Path simulation did not finish within {event_limit} events")
            self._engine.advance(
                self,
                max(0.0, elapsed),
                ends_a_step=(elapsed == until_done and bool(self.heap)),
                ends_a_transit=(elapsed == until_transit and bool(self._transport.transit_heap)),
            )
        return self._dispatcher.build_run_result(self, status, events)
