"""Discounted NPV payback, scarcity, and Fleet-base slot-loot path strategy."""

from __future__ import annotations

from backend.core import catalog as game_data
from backend.planner.ai._sim_types import Blocker, PriorityKey, Proposal, SimulatorView, Vec4
from backend.planner.ai._strategy_eval import (
    ColonyCacheKey,
    StrategyView,
    choose_demolition,
    collect_enablers,
    compute_colony_payback,
    compute_research_gain,
    energy_steps,
    offer_step,
    planet_accelerators,
    rank_candidate,
    value_of,
)
from backend.planner.ai._types import (
    PATH_ACCELERATOR_KEYS,
    PATH_FLOW_BUILDING_KEYS,
    PATH_MINE_KEYS,
    PATH_NEVER,
)
from backend.planner.ai.problem import planet_hourly_output


class PathStrategy:
    """Discounted NPV payback / scarcity / b10 slot-loot strategy matching ``full_planner.ipynb`` §11.5."""

    def __init__(
        self,
        name: str,
        *,
        rule: str,
        values: str = "merchant",
        focus: float = 1.0,
        critical_share: float = 0.9,
        patience: float = 0.25,
        speed_weight: float = 1.5,
        bias: tuple[float, ...] = (1.0, 1.0, 1.0, 1.0),
        revalue_every_steps: int = 25,
    ) -> None:
        if rule not in ("cheapest", "payback") or values not in ("merchant", "scarcity"):
            raise ValueError(f"Unknown rule {rule!r} or values {values!r}")
        self.name = name
        self.rule = rule
        self.value_mode = values
        self.focus = float(focus)
        self.critical_share = float(critical_share)
        self.patience = float(patience)
        self.speed_weight = float(speed_weight)
        self.bias = tuple(float(value) for value in bias)
        self.revalue_every = max(1, int(revalue_every_steps))
        self.forced: bool = False
        self.views: dict[bool, StrategyView] = {}
        self.entries: list[list[Proposal]] = []
        self.requests: list[dict[int, PriorityKey]] = []
        self.research_gains: dict[str, tuple[int, Vec4]] = {}
        self.critical: list[bool] = []
        self.needs_speed: list[bool] = []
        self.merchant_values: Vec4 = (1.0, 1.0, 1.0, 1.0)
        self.values: Vec4 = (1.0, 1.0, 1.0, 1.0)
        self.income_value: float = 0.0
        self.horizon: float = PATH_NEVER
        self.colony_cache: dict[ColonyCacheKey, float] = {}

    def parameters(self) -> dict[str, object]:
        """Return JSON-serializable dictionary of strategy hyperparameters."""
        if self.rule == "cheapest":
            return {"rule": self.rule}
        return {
            "rule": self.rule,
            "values": self.value_mode,
            "focus": self.focus,
            "critical_share": self.critical_share,
            "patience": self.patience,
            "speed_weight": self.speed_weight,
            "bias": list(self.bias),
        }

    def begin(self, sim: SimulatorView) -> None:
        """Initialize per-owner scan views and valuation caches for a new rollout."""
        owners = sim.planet_count + 1
        self.forced = False
        self.views = {
            forced: StrategyView(
                entries=[[] for _ in range(owners)],
                requests=[{} for _ in range(owners)],
                dirty=set(range(owners)),
                ranked=None,
            )
            for forced in (False, True)
        }
        self.entries = self.views[False].entries
        self.requests = self.views[False].requests
        self.research_gains = {}
        self.critical = [False] * sim.queue_count
        self.needs_speed = [False] * sim.queue_count
        self.merchant_values = (
            1.0 / float(game_data.MERCHANT_RATES_IN_SPICE["pig_iron"]),
            1.0 / float(game_data.MERCHANT_RATES_IN_SPICE["metal"]),
            1.0 / float(game_data.MERCHANT_RATES_IN_SPICE["kryptonite"]),
            1.0 / float(game_data.MERCHANT_RATES_IN_SPICE["spice"]),
        )
        self.values = self.merchant_values
        self.income_value = 0.0
        self.horizon = PATH_NEVER
        self.colony_cache = {}

    def review(self, sim: SimulatorView) -> tuple[float, int, bool]:
        """Re-evaluate critical queues, scarcity weights, and next review cadence."""
        if self.rule == "cheapest":
            return PATH_NEVER, len(sim.steps) + 1, False
        work, slow_work = sim.queue_work()
        bounded = sim.run_limit_hours < PATH_NEVER
        missing = sim.horizon_missing_resources() if bounded else sim.missing_resources()
        full_missing = sim.missing_resources()
        full_horizon = max(work) if work else 0.0
        for position in range(4):
            if full_missing[position] > 0.0:
                supply = sim.final_supply[position]
                full_horizon = max(full_horizon, full_missing[position] / supply if supply > 0.0 else PATH_NEVER)
        rem_hours = max(0.0, sim.run_limit_hours - sim.clock) if bounded else full_horizon
        horizon = rem_hours if (bounded and rem_hours > 0.0) else full_horizon
        self.horizon = horizon
        measurable = 0.0 < horizon < PATH_NEVER
        can_finish_all = (not bounded) or (full_horizon <= sim.run_limit_hours)
        critical = [
            can_finish_all and measurable and hours > 0.0 and hours >= self.critical_share * horizon for hours in work
        ]
        needs_speed = [measurable and hours > 0.0 and hours >= self.critical_share * horizon for hours in slow_work]
        supply_hours = [
            (missing[pos] / sim.supply[pos] if sim.supply[pos] > 0.0 else PATH_NEVER) if missing[pos] > 0.0 else 0.0
            for pos in range(4)
        ]
        starved = any(hours == PATH_NEVER for hours in supply_hours)
        if self.value_mode == "scarcity":
            longest = max((hours for hours in supply_hours if hours < PATH_NEVER), default=0.0)
            vals_list: list[float] = []
            for pos in range(4):
                if supply_hours[pos] == PATH_NEVER:
                    share = 1.0
                elif starved or longest <= 0.0:
                    share = 0.01 if starved else 1.0
                else:
                    share = max(0.01, supply_hours[pos] / longest)
                vals_list.append(self.bias[pos] * (share**self.focus) / max(sim.supply[pos], 1e-9))
        else:
            vals_list = [self.bias[pos] * self.merchant_values[pos] for pos in range(4)]
        new_values: Vec4 = (vals_list[0], vals_list[1], vals_list[2], vals_list[3])
        income_value = sum(new_values[pos] * sim.supply[pos] for pos in range(4))

        def moved(new: float, old: float) -> bool:
            return abs(new - old) > 0.15 * abs(old)

        changed = (
            self.income_value <= 0.0
            or critical != self.critical
            or needs_speed != self.needs_speed
            or moved(income_value, self.income_value)
            or any(moved(new, old) for new, old in zip(new_values, self.values, strict=False))
        )
        if changed:
            self.critical, self.needs_speed = critical, needs_speed
            self.values, self.income_value = new_values, income_value
            self.colony_cache.clear()
        return horizon, (1 if starved else self.revalue_every), changed

    def proposals(self, sim: SimulatorView, *, forced: bool) -> list[Proposal]:
        """Return priority-sorted candidate steps across planets and research."""
        if sim.dirty:
            for view in self.views.values():
                view.dirty.update(sim.dirty)
            sim.dirty.clear()
        view = self.views[forced]
        if not view.dirty and view.ranked is not None:
            return view.ranked
        self.forced = forced
        self.entries, self.requests = view.entries, view.requests
        for owner in sorted(view.dirty):
            if owner == sim.research_queue:
                self._scan_research(sim)
            else:
                self._scan_planet(sim, owner)
        view.dirty.clear()
        wanted: dict[int, PriorityKey] = {}
        for owner_entries in self.entries:
            for priority, step in owner_entries:
                if step not in wanted or priority > wanted[step]:
                    wanted[step] = priority
        for owner_requests in self.requests:
            for step, priority in owner_requests.items():
                if step not in wanted or priority > wanted[step]:
                    wanted[step] = priority
        view.ranked = sorted(((priority, step) for step, priority in wanted.items()), reverse=True)
        return view.ranked

    def _scan_unfounded_planet(
        self, sim: SimulatorView, planet: int, entries: list[Proposal], requests: dict[int, PriorityKey]
    ) -> None:
        step = sim.colonize_step[planet]
        if step < 0 or sim.started[step]:
            return
        blockers = sim.blockers(step)
        if self.rule == "cheapest":
            if not blockers:
                entries.append(((-sim.cost_spice_eq[step], 0.0, -step), step))
            return
        price = value_of(self.values, sim.cost[step])
        colony_hours = sim.duration(step)
        if sim.run_limit_hours < PATH_NEVER and sim.clock + colony_hours >= sim.run_limit_hours and not self.forced:
            return
        forced_rank = rank_candidate(sim, 0.0, price, 1.0 if self.forced else 0.0, colony_hours, self.income_value)
        rank = max(compute_colony_payback(sim, planet, self.values, self.colony_cache), forced_rank)
        if rank <= 0.0:
            return
        if not blockers:
            entries.append(((rank, -price, -step), step))
            return
        enablers: dict[int, list[Blocker]] = {}
        collect_enablers(sim, blockers, enablers, self.values)
        for enabler, enabler_blockers in enablers.items():
            if not enabler_blockers and sim.is_next_of_its_chain(enabler):
                requests[enabler] = (rank, -price, -enabler)

    def _scan_cheapest_planet(self, sim: SimulatorView, planet: int, entries: list[Proposal]) -> None:
        builds_left_cheap = 0
        for chain in sim.build_chains[planet].values():
            position = sim.next_step[chain]
            if position < len(sim.chain_steps[chain]):
                builds_left_cheap += 1
                step = sim.chain_steps[chain][position]
                if not sim.blockers(step):
                    entries.append(((-sim.cost_spice_eq[step], 0.0, -step), step))
        if builds_left_cheap > 0 and sim.fields_used[planet] >= sim.fields_max[planet]:
            step = choose_demolition(sim, planet, self.values)
            if step >= 0 and not sim.blockers(step):
                entries.append(((0.0, 0.0, -step), step))

    def _scan_planet(self, sim: SimulatorView, planet: int) -> None:
        entries: list[Proposal] = []
        requests: dict[int, PriorityKey] = {}
        self.entries[planet], self.requests[planet] = entries, requests
        if not sim.founded[planet]:
            self._scan_unfounded_planet(sim, planet, entries, requests)
            return
        if self.rule == "cheapest":
            self._scan_cheapest_planet(sim, planet, entries)
            return
        nothing: Vec4 = (0.0, 0.0, 0.0, 0.0)
        urgent = 1.0 if (self.critical[planet] or self.forced) else 0.0
        needs_speed = self.needs_speed[planet]
        research_needs_speed = planet == sim.lab_planet and self.needs_speed[sim.research_queue]
        before = sim.output[planet]
        accelerators = planet_accelerators(sim, planet, self.values)
        for key, chain in sim.build_chains[planet].items():
            position = sim.next_step[chain]
            if position >= len(sim.chain_steps[chain]):
                continue
            step = sim.chain_steps[chain][position]
            level = sim.level_to[step]
            if key in PATH_FLOW_BUILDING_KEYS:
                totals = sim.changed_totals(planet, {key: level})
                after = planet_hourly_output(totals)
                gain: Vec4 = (
                    sim.keep[0] * (after[1] - before[1]),
                    sim.keep[1] * (after[2] - before[2]),
                    sim.keep[2] * (after[3] - before[3]),
                    sim.keep[3] * (after[4] - before[4]),
                )
                extra = energy_steps(sim, planet, totals) if key in PATH_MINE_KEYS else None
                offer_step(
                    sim,
                    step,
                    gain,
                    urgent,
                    entries,
                    requests,
                    values=self.values,
                    income_value=self.income_value,
                    extra_enablers=extra,
                    bundle_planet=planet,
                    accelerators=accelerators,
                )
            elif key == "b10" and sim.fleet_command_center_level is None and any(x > 0.0 for x in sim.slot_attack):
                slot_gain: Vec4 = (
                    sim.keep[0] * sim.slot_attack[0],
                    sim.keep[1] * sim.slot_attack[1],
                    sim.keep[2] * sim.slot_attack[2],
                    sim.keep[3] * sim.slot_attack[3],
                )
                offer_step(
                    sim,
                    step,
                    slot_gain,
                    urgent,
                    entries,
                    requests,
                    values=self.values,
                    income_value=self.income_value,
                    bundle_planet=planet,
                    accelerators=accelerators,
                )
            elif key in PATH_ACCELERATOR_KEYS:
                saves = self.speed_weight * (0.5 if key == "b17" else 1.0 / (level + 1.0))
                speed_urgent = max(urgent, saves if needs_speed else 0.0)
                if speed_urgent > 0.0:
                    offer_step(
                        sim,
                        step,
                        nothing,
                        speed_urgent,
                        entries,
                        requests,
                        values=self.values,
                        income_value=self.income_value,
                    )
            elif key == "b8" and research_needs_speed:
                offer_step(
                    sim,
                    step,
                    nothing,
                    max(urgent, self.speed_weight / (level + 1.0)),
                    entries,
                    requests,
                    values=self.values,
                    income_value=self.income_value,
                )
            elif urgent > 0.0:
                offer_step(
                    sim, step, nothing, urgent, entries, requests, values=self.values, income_value=self.income_value
                )

    def _scan_research(self, sim: SimulatorView) -> None:
        owner = sim.research_queue
        entries: list[Proposal] = []
        requests: dict[int, PriorityKey] = {}
        self.entries[owner], self.requests[owner] = entries, requests
        urgent = 1.0 if (self.critical[owner] or self.forced) else 0.0
        for key, chain in sim.research_chains.items():
            position = sim.next_step[chain]
            if position >= len(sim.chain_steps[chain]):
                continue
            step = sim.chain_steps[chain][position]
            if self.rule == "cheapest":
                if not sim.blockers(step):
                    entries.append(((-sim.cost_spice_eq[step], 0.0, -step), step))
                continue
            if key in ("r1", "r2"):
                offer_step(
                    sim,
                    step,
                    compute_research_gain(sim, key, self.research_gains),
                    urgent,
                    entries,
                    requests,
                    values=self.values,
                    income_value=self.income_value,
                )
            elif urgent > 0.0:
                offer_step(
                    sim,
                    step,
                    (0.0, 0.0, 0.0, 0.0),
                    urgent,
                    entries,
                    requests,
                    values=self.values,
                    income_value=self.income_value,
                )
