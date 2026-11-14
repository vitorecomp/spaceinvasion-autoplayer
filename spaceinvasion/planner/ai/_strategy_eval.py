"""Pure candidate evaluation, enabler collection, demolition, and colony payback helpers for ``PathStrategy``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from spaceinvasion.core import catalog as game_data
from spaceinvasion.planner.ai._sim_types import (
    Blocker,
    PriorityKey,
    Proposal,
    SimulatorView,
    Vec4,
    int_mapping,
)
from spaceinvasion.planner.ai._types import PATH_ACCELERATOR_KEYS, PATH_MINE_KEYS, PATH_NEVER
from spaceinvasion.planner.ai.problem import planet_hourly_output
from spaceinvasion.planner.config_optimizer import RESOURCE_KEYS, WAREHOUSE_KEYS, full_cost

ColonyCacheKey = tuple[int, tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]


@dataclass(slots=True)
class StrategyView:
    """Cached per-owner proposals and dirty flags for either normal or forced scan mode."""

    entries: list[list[Proposal]]
    requests: list[dict[int, PriorityKey]]
    dirty: set[int]
    ranked: list[Proposal] | None


@dataclass(slots=True)
class AcceleratorOption:
    """Pre-evaluated Development center (``b7``) or Microsystems (``b17``) speedup bundle."""

    step: int
    needs: dict[int, list[Blocker]]
    faster: float
    needs_price: float
    needs_hours: float


def value_of(values: Vec4, amounts: Sequence[float]) -> float:
    """Return weighted resource value across Pig iron, Metal, Kryptonite, and Spice."""
    return values[0] * amounts[0] + values[1] * amounts[1] + values[2] * amounts[2] + values[3] * amounts[3]


def planet_gain(sim: SimulatorView, planet: int, changes: Mapping[str, int]) -> Vec4:
    """Compute net hourly resource gain from applying ``changes`` on ``planet``."""
    after = planet_hourly_output(sim.changed_totals(planet, changes))
    before = sim.output[planet]
    keep = sim.keep
    dl_b10 = 0
    if "b10" in changes and sim.fleet_command_center_level is None:
        dl_b10 = max(0, int(changes["b10"]) - sim.levels[planet].get("b10", 0))
    return (
        keep[0] * (after[1] - before[1] + dl_b10 * sim.slot_attack[0]),
        keep[1] * (after[2] - before[2] + dl_b10 * sim.slot_attack[1]),
        keep[2] * (after[3] - before[3] + dl_b10 * sim.slot_attack[2]),
        keep[3] * (after[4] - before[4] + dl_b10 * sim.slot_attack[3]),
    )


def rank_candidate(
    sim: SimulatorView, added: float, price: float, urgent: float, hours: float, income_value: float
) -> float:
    """Score a candidate by discounted NPV payback rate and critical-path urgency."""
    if price <= 0.0:
        return PATH_NEVER if (added > 0.0 or urgent > 0.0) else 0.0
    if sim.run_limit_hours < PATH_NEVER:
        now_window = sim.remaining_discounted_hours(sim.clock)
        future_window = sim.remaining_discounted_hours(sim.clock + hours)
        if now_window > 0.0 and future_window <= 0.0:
            return 0.0
        window_factor = (future_window / now_window) if now_window > 0.0 else 1.0
        total_window = sim.suffix_discounted_hours[0]
        rem_share = (future_window / total_window) if total_window > 0.0 else 1.0
    else:
        window_factor = rem_share = 1.0
    rank = (added * window_factor) / (price + added * hours) if added > 0.0 else 0.0
    if urgent > 0.0:
        rank = max(rank, urgent * rem_share * income_value / price)
    return rank


def choose_demolition(sim: SimulatorView, planet: int, values: Vec4) -> int:
    """Choose the next planned demolition step on an out-of-fields planet whose output loss costs least."""
    if sim.fields_used[planet] < sim.fields_max[planet]:
        return -1
    best_step = -1
    best_key: tuple[int, float, int] = (2, PATH_NEVER, 0)
    for chain in sim.demolish_chains[planet]:
        position = sim.next_step[chain]
        if position >= len(sim.chain_steps[chain]):
            continue
        step = sim.chain_steps[chain][position]
        bkey = sim.key[step]
        new_lvl = sim.level_to[step]
        energy_penalty = 0
        loss = 0.0
        if sim.founded[planet]:
            after_totals = sim.changed_totals(planet, {bkey: new_lvl})
            if bkey in ("b5", "b6") and after_totals[7] > after_totals[6] + 1e-9:
                energy_penalty = 1
            loss = -value_of(values, planet_gain(sim, planet, {bkey: new_lvl}))
        cand_key = (energy_penalty, loss, step)
        if cand_key < best_key:
            best_step, best_key = step, cand_key
    return best_step


def collect_enablers(
    sim: SimulatorView, blockers: Sequence[Blocker], enablers: dict[int, list[Blocker]], values: Vec4
) -> None:
    """Recursively collect causal prerequisite, storage, colony, and field-freeing steps."""
    added: list[int] = []
    for blocker in blockers:
        reason = blocker[0]
        candidates: list[int] = []
        if reason == "colony":
            step = sim.colonize_step[blocker[1]]
            if step >= 0 and not sim.started[step]:
                candidates.append(step)
        elif reason == "field":
            planet = blocker[1]
            if sim.fields_used[planet] >= sim.fields_max[planet]:
                running = sim.queue_step[planet]
                if not (running >= 0 and sim.kind[running] == "demolish"):
                    step = choose_demolition(sim, planet, values)
                    if step >= 0:
                        candidates.append(step)
        elif len(blocker) == 4:
            if reason == "level":
                owner = blocker[1]
                req_key = str(blocker[2])
                level = blocker[3]
                chain = sim.research_chains.get(req_key) if owner < 0 else sim.build_chains[owner].get(req_key)
            else:
                owner = blocker[1]
                pos = int(blocker[2])
                level = blocker[3]
                chain = sim.build_chains[owner].get(WAREHOUSE_KEYS[pos])
            if chain is not None:
                for step in sim.chain_steps[chain][sim.next_step[chain] :]:
                    if sim.level_to[step] > level:
                        break
                    candidates.append(step)
        for step in candidates:
            if step not in enablers:
                enablers[step] = sim.blockers(step)
                added.append(step)
    for step in added:
        if enablers[step] and len(enablers) < 400:
            collect_enablers(sim, enablers[step], enablers, values)


def energy_steps(sim: SimulatorView, planet: int, totals: Sequence[float]) -> list[int]:
    """Return planned Fusion plant (``b5``) steps needed to cover an energy deficit on ``planet``."""
    energy_produced, energy_used = totals[6], totals[7]
    if energy_used <= energy_produced + 1e-9:
        return []
    chain = sim.build_chains[planet].get("b5")
    if chain is None or sim.next_step[chain] >= len(sim.chain_steps[chain]):
        return []
    temperature = sim.temperature[planet]
    mining, energy = sim.research["r1"], sim.research["r2"]
    standing = sim.levels[planet].get("b5", 0)
    standing_energy = sim.level_flows("b5", standing, temperature, mining, energy)[6] if standing > 0 else 0.0
    needed: list[int] = []
    for step in sim.chain_steps[chain][sim.next_step[chain] :]:
        needed.append(step)
        fusion_energy = sim.level_flows("b5", sim.level_to[step], temperature, mining, energy)[6]
        if energy_used <= energy_produced - standing_energy + fusion_energy + 1e-9:
            break
    return needed


def compute_research_gain(sim: SimulatorView, key: str, cache: dict[str, tuple[int, Vec4]]) -> Vec4:
    """Compute empire-wide hourly net production gain for the next level of ``r1`` or ``r2``."""
    token = sim.supply_version
    cached = cache.get(key)
    if cached is not None and cached[0] == token:
        return cached[1]
    mining = sim.research["r1"] + (1 if key == "r1" else 0)
    energy = sim.research["r2"] + (1 if key == "r2" else 0)
    outputs = [
        planet_hourly_output(sim.planet_totals(planet, sim.levels[planet], sim.solar[planet], mining, energy))
        for planet in range(sim.planet_count)
    ]
    gain: Vec4 = (
        sim.keep[0] * sum(outputs[p][1] - sim.output[p][1] for p in range(sim.planet_count) if sim.founded[p]),
        sim.keep[1] * sum(outputs[p][2] - sim.output[p][2] for p in range(sim.planet_count) if sim.founded[p]),
        sim.keep[2] * sum(outputs[p][3] - sim.output[p][3] for p in range(sim.planet_count) if sim.founded[p]),
        sim.keep[3] * sum(outputs[p][4] - sim.output[p][4] for p in range(sim.planet_count) if sim.founded[p]),
    )
    cache[key] = (token, gain)
    return gain


def compute_colony_payback(
    sim: SimulatorView,
    planet: int,
    values: Vec4,
    cache: dict[ColonyCacheKey, float],
    depth: int = 25,
) -> float:
    """Estimate peak cumulative payback ratio of founding ``planet`` and upgrading its mines."""
    target = int_mapping(sim.planets[planet].get("target_levels"))
    token: ColonyCacheKey = (
        sim.temperature[planet],
        tuple(sorted(target.items())),
        tuple(sorted(sim.levels[planet].items())),
    )
    cached = cache.get(token)
    if cached is not None:
        return cached
    mining, energy = sim.research["r1"], sim.research["r2"]
    solar, keep, fixed_cc = sim.solar[planet], sim.keep, sim.fleet_command_center_level

    def supply_value(levels: Mapping[str, int]) -> float:
        output = planet_hourly_output(sim.planet_totals(planet, levels, solar, mining, energy))
        slots = 1 + (levels.get("b10", 0) if fixed_cc is None else fixed_cc)
        return sum(values[pos] * keep[pos] * (output[pos + 1] + slots * sim.slot_attack[pos]) for pos in range(4))

    def level_price(bkey: str, level: int) -> float:
        cost = full_cost(game_data.building_cost(int(bkey[1:]), level))
        return sum(values[pos] * float(cost[resource]) for pos, resource in enumerate(RESOURCE_KEYS))

    levels = dict(sim.levels[planet])
    ship = sim.colonize_step[planet]
    spent = value_of(values, sim.cost[ship]) if ship >= 0 else 0.0
    current = supply_value(levels)
    added = current
    best = (added / spent) if spent > 0.0 else 0.0
    for _ in range(depth):
        choice: tuple[float, dict[str, int], float, float] | None = None
        for bkey in PATH_MINE_KEYS:
            level = levels.get(bkey, 0) + 1
            if level > target.get(bkey, 0):
                continue
            trial = dict(levels)
            trial[bkey] = level
            price = level_price(bkey, level)
            while True:
                totals = sim.planet_totals(planet, trial, solar, mining, energy)
                fusion = trial.get("b5", 0) + 1
                if totals[7] <= totals[6] + 1e-9 or fusion > target.get("b5", 0):
                    break
                trial["b5"] = fusion
                price += level_price("b5", fusion)
            val = supply_value(trial) - current
            ratio = val / price if price > 0.0 else 0.0
            if val > 0.0 and (choice is None or ratio > choice[0]):
                choice = (ratio, trial, val, price)
        if choice is None:
            break
        _, levels, val, price = choice
        current += val
        added += val
        spent += price
        if spent > 0.0:
            best = max(best, added / spent)
    cache[token] = best
    return best


def planet_accelerators(sim: SimulatorView, planet: int, values: Vec4) -> list[AcceleratorOption]:
    """Collect candidate ``b7`` and ``b17`` accelerator options for ``planet``."""
    accelerators: list[AcceleratorOption] = []
    for key in PATH_ACCELERATOR_KEYS:
        chain = sim.build_chains[planet].get(key)
        if chain is None or sim.next_step[chain] >= len(sim.chain_steps[chain]):
            continue
        step = sim.chain_steps[chain][sim.next_step[chain]]
        needs: dict[int, list[Blocker]] = {step: sim.blockers(step)}
        if needs[step]:
            collect_enablers(sim, needs[step], needs, values)
        level = sim.level_to[step]
        accelerators.append(
            AcceleratorOption(
                step=step,
                needs=needs,
                faster=2.0 if key == "b17" else (level + 1.0) / level,
                needs_price=sum(value_of(values, sim.cost[need]) for need in needs),
                needs_hours=sum(sim.duration(need) for need in needs if sim.queue[need] == planet),
            )
        )
    return accelerators


def offer_step(
    sim: SimulatorView,
    step: int,
    gain: Sequence[float],
    urgent: float,
    entries: list[Proposal],
    requests: dict[int, PriorityKey],
    *,
    values: Vec4,
    income_value: float,
    extra_enablers: Sequence[int] | None = None,
    bundle_planet: int = -1,
    accelerators: Sequence[AcceleratorOption] = (),
) -> None:
    """Evaluate ``step`` (with enablers and optional accelerator bundles) and emit entry or requests."""
    blockers = sim.blockers(step)
    enablers: dict[int, list[Blocker]] = {}
    if blockers:
        collect_enablers(sim, blockers, enablers, values)
    for extra in extra_enablers or ():
        if extra not in enablers:
            enablers[extra] = sim.blockers(extra)
            if enablers[extra]:
                collect_enablers(sim, enablers[extra], enablers, values)
    if blockers and not enablers:
        return
    cost = list(sim.cost[step])
    for enabler in enablers:
        enabler_cost = sim.cost[enabler]
        for position in range(4):
            cost[position] += enabler_cost[position]
    if enablers and bundle_planet >= 0:
        changes = {sim.key[step]: sim.level_to[step]}
        for enabler in enablers:
            if sim.kind[enabler] == "build" and sim.step_planet[enabler] == bundle_planet:
                changes[sim.key[enabler]] = max(changes.get(sim.key[enabler], 0), sim.level_to[enabler])
        gain = planet_gain(sim, bundle_planet, changes)
    added = value_of(values, gain)
    own_hours = other_hours = 0.0
    if added > 0.0 or urgent > 0.0:
        own_hours = sim.duration(step)
        queue = sim.queue[step]
        other_hours = sum(sim.duration(enabler) for enabler in enablers if sim.queue[enabler] == queue)
    price = value_of(values, cost)
    rank = rank_candidate(sim, added, price, urgent, own_hours + other_hours, income_value)
    if own_hours > 0.0 and added > 0.0:
        for acc in accelerators:
            if acc.step in enablers:
                continue
            faster_rank = rank_candidate(
                sim,
                added,
                price + acc.needs_price,
                urgent,
                own_hours / acc.faster + other_hours + acc.needs_hours,
                income_value,
            )
            if faster_rank > rank:
                rank = faster_rank
                price += acc.needs_price
                other_hours += acc.needs_hours
                own_hours /= acc.faster
                enablers = {**enablers, **acc.needs}
    if rank <= 0.0:
        return
    if not enablers:
        entries.append(((rank, -price, -step), step))
        return
    for enabler, enabler_blockers in enablers.items():
        if not enabler_blockers and sim.is_next_of_its_chain(enabler):
            priority: PriorityKey = (rank, -price, -enabler)
            if enabler not in requests or priority > requests[enabler]:
                requests[enabler] = priority
