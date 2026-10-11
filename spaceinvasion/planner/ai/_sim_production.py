"""Production flow caches, hourly rates, fleet slots, and timing divisors for ``EmpirePathSimulator``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.planner.ai._sim_types import PlanetOutput, RateSnapshot, SimProblemData, Vec4, int_mapping
from spaceinvasion.planner.ai._types import (
    PATH_ACCELERATOR_KEYS,
    PATH_FLOW_BUILDING_KEYS,
    PATH_HOURS_PER_WEEK,
    PATH_MINE_KEYS,
    PATH_NEVER,
)
from spaceinvasion.planner.config_optimizer import (
    PRODUCTION_FLOW_KEYS,
    building_level_flows,
    building_time_divisor,
    research_time_divisor,
    shipyard_time_divisor,
    solar_cell_energy_per_unit,
    technology_multiplier,
)


class ProductionModel:
    """Caches building/solar flows and evaluates empire production, loot supply, and durations."""

    def __init__(self, data: SimProblemData) -> None:
        self._data = data
        self._flow_cache: dict[tuple[str, int, int, int], tuple[float, ...]] = {}
        self._solar_cache: dict[tuple[int, int], float] = {}
        self._accelerator_cache: dict[tuple[int, int, int], float] = {}

    def remaining_discounted_hours(self, hour: float) -> float:
        """Return discounted remaining hours from ``hour`` to the W-week reward horizon."""
        data = self._data
        if hour >= data.horizon_hours:
            return 0.0
        if hour <= 0.0:
            return data.suffix_discounted_hours[0]
        week = int(hour // PATH_HOURS_PER_WEEK)
        if week >= data.reward_weeks:
            return 0.0
        in_week = (week + 1) * PATH_HOURS_PER_WEEK - hour
        return data.week_weights[week] * in_week + data.suffix_discounted_hours[week + 1]

    def level_flows(self, key: str, level: int, temperature: int, mining: int, energy: int) -> tuple[float, ...]:
        """Return cached hourly resource/energy flow vector for ``(key, level)``."""
        token = (key, level, temperature, mining if key in PATH_MINE_KEYS else energy)
        flows = self._flow_cache.get(token)
        if flows is None:
            context = {
                "speedup_factor": self._data.speedup,
                "planet_temperature_c": temperature,
                "r1_mining_tech": mining,
                "r2_energy_tech": energy,
            }
            flows = self._flow_cache[token] = tuple(float(v) for v in building_level_flows(key, level, context))
        return flows

    def planet_totals(
        self, planet: int, levels: Mapping[str, int], solar_cells: int, mining: int, energy: int
    ) -> list[float]:
        """Sum raw production/consumption/energy flows for ``planet`` at ``levels``."""
        temperature = self._data.temperature[planet]
        totals = [0.0] * len(PRODUCTION_FLOW_KEYS)
        for key in PATH_FLOW_BUILDING_KEYS:
            level = levels.get(key, 0)
            if level > 0:
                for position, flow in enumerate(self.level_flows(key, level, temperature, mining, energy)):
                    totals[position] += flow
        if solar_cells > 0:
            token = (temperature, energy)
            per_cell = self._solar_cache.get(token)
            if per_cell is None:
                per_cell = self._solar_cache[token] = float(solar_cell_energy_per_unit(temperature)) * float(
                    technology_multiplier("r2", energy)
                )
            totals[6] += solar_cells * per_cell
        return totals

    def changed_totals(
        self,
        planet: int,
        base_totals: Sequence[float],
        levels: Mapping[str, int],
        mining: int,
        energy: int,
        changes: Mapping[str, int],
    ) -> list[float]:
        """Return ``base_totals`` incrementally updated for building-level ``changes``."""
        totals = list(base_totals)
        temperature = self._data.temperature[planet]
        for key, new_level in changes.items():
            if key not in PATH_FLOW_BUILDING_KEYS:
                continue
            old_level = levels.get(key, 0)
            if old_level > 0:
                for position, flow in enumerate(self.level_flows(key, old_level, temperature, mining, energy)):
                    totals[position] -= flow
            if new_level > 0:
                for position, flow in enumerate(self.level_flows(key, new_level, temperature, mining, energy)):
                    totals[position] += flow
        return totals

    def fleet_slots_of(self, levels_list: Sequence[Mapping[str, int]], founded: Sequence[bool]) -> int:
        """Compute active fleet slots across founded colonies."""
        fixed = self._data.fleet_command_center_level
        return sum(
            1 + (levels_list[planet].get("b10", 0) if fixed is None else fixed)
            for planet in range(self._data.planet_count)
            if founded[planet]
        )

    def gross_attack_of(self, slots: int) -> Vec4:
        """Compute gross hourly raid income across ``slots`` active fleet slots."""
        fixed_a, slot_a = self._data.fixed_attack, self._data.slot_attack
        return (
            fixed_a[0] + slots * slot_a[0],
            fixed_a[1] + slots * slot_a[1],
            fixed_a[2] + slots * slot_a[2],
            fixed_a[3] + slots * slot_a[3],
        )

    def supply_of(
        self,
        outputs: Sequence[tuple[float, ...]],
        founded: Sequence[bool],
        levels_list: Sequence[Mapping[str, int]],
        slots: int | None = None,
    ) -> Vec4:
        """Compute net hourly resource supply (mines + attack income after spare diversion)."""
        active_slots = self.fleet_slots_of(levels_list, founded) if slots is None else int(slots)
        gross = list(self.gross_attack_of(active_slots))
        for output, is_founded in zip(outputs, founded, strict=False):
            if is_founded:
                gross[0] += output[0]
                gross[1] += output[2]
                gross[2] += output[3]
                gross[3] += output[4]
        keep = self._data.keep
        return (
            keep[0] * gross[0],
            keep[1] * gross[1],
            keep[2] * gross[2],
            keep[3] * gross[3],
        )

    def reward_ceiling_per_hour(self, final_levels: Sequence[Mapping[str, int]]) -> float:
        """Compute the theoretical hourly net SpiceEq production ceiling across target levels."""
        data = self._data
        res_now = int_mapping(data.problem.get("research_levels"))
        res_tgt = int_mapping(data.problem.get("research_target"))
        mining = max(res_now.get("r1", 0), res_tgt.get("r1", 0))
        energy = max(res_now.get("r2", 0), res_tgt.get("r2", 0))
        rates, keep = data.merchant_rates, data.keep
        total = 0.0
        for planet in range(data.planet_count):
            cur_lvls = int_mapping(data.planets[planet].get("levels"))
            top = {key: max(cur_lvls.get(key, 0), final_levels[planet].get(key, 0)) for key in PATH_MINE_KEYS}
            flows = {
                key: self.level_flows(key, level, data.temperature[planet], mining, energy)
                for key, level in top.items()
                if level > 0
            }
            mined = flows["b1"][0] if "b1" in flows else 0.0
            smelted = min(flows["b2"][1], mined / 2.0) if "b2" in flows else 0.0
            kryptonite = flows["b3"][2] if "b3" in flows else 0.0
            spice = flows["b4"][3] if "b4" in flows else 0.0
            smelt_bonus = max(0.0, smelted * (keep[1] * rates[1] - 2.0 * keep[0] * rates[0]))
            total += (
                mined * keep[0] * rates[0] + smelt_bonus + kryptonite * keep[2] * rates[2] + spice * keep[3] * rates[3]
            )
        return total

    def compute_rates(
        self,
        levels: Sequence[Mapping[str, int]],
        founded: Sequence[bool],
        outputs: Sequence[PlanetOutput],
    ) -> RateSnapshot:
        """Compute empire and per-planet net hourly rates for smelting OFF and ON."""
        data = self._data
        mined = left = metal = kry = spice = 0.0
        b2_founded = 0
        for planet in range(data.planet_count):
            if founded[planet]:
                if levels[planet].get("b2", 0) > 0:
                    b2_founded += 1
                out = outputs[planet]
                mined += out[0]
                left += out[1]
                metal += out[2]
                kry += out[3]
                spice += out[4]
        fleet_slots = self.fleet_slots_of(levels, founded)
        attack = self.gross_attack_of(fleet_slots)
        k0, k1, k2, k3 = data.keep
        a0, a1, a2, a3 = attack
        supply: Vec4 = (k0 * (mined + a0), k1 * (metal + a1), k2 * (kry + a2), k3 * (spice + a3))
        rate_off: Vec4 = (k0 * (mined + a0), k1 * a1, k2 * (kry + a2), k3 * (spice + a3))
        rate_on: Vec4 = (k0 * (left + a0), k1 * (metal + a1), k2 * (kry + a2), k3 * (spice + a3))
        fixed = data.fleet_command_center_level
        inv_slots = (1.0 / float(fleet_slots)) if fleet_slots > 0 else 0.0
        p_off: list[Vec4] = []
        p_on: list[Vec4] = []
        for planet in range(data.planet_count):
            if not founded[planet]:
                p_off.append((0.0, 0.0, 0.0, 0.0))
                p_on.append((0.0, 0.0, 0.0, 0.0))
                continue
            out = outputs[planet]
            slots_p = 1 + (levels[planet].get("b10", 0) if fixed is None else fixed)
            share = slots_p * inv_slots
            sa0, sa1 = share * a0, share * a1
            rk, rs = k2 * (out[3] + share * a2), k3 * (out[4] + share * a3)
            p_off.append((k0 * (out[0] + sa0), k1 * sa1, rk, rs))
            p_on.append((k0 * (out[1] + sa0), k1 * (out[2] + sa1), rk, rs))
        return RateSnapshot(
            b2_founded_count=b2_founded,
            fleet_slots=fleet_slots,
            attack_now=attack,
            supply=supply,
            furnace_metal=metal,
            rate_off=rate_off,
            rate_on=rate_on,
            planet_rate_off=p_off,
            planet_rate_on=p_on,
        )

    def accelerator_hours(
        self, planet: int, next_step: Sequence[int], final_levels: Sequence[Mapping[str, int]]
    ) -> float:
        """Compute minimum build hours across remaining Development center (b7) and Microsystems (b17) steps."""
        data = self._data
        pending: list[list[int]] = []
        for key in PATH_ACCELERATOR_KEYS:
            chain = data.chains.build_chains[planet].get(key)
            pending.append(data.chains.chain_steps[chain][next_step[chain] :] if chain is not None else [])
        centers, microsystems = pending
        if not centers and not microsystems:
            return 0.0
        token = (planet, len(centers), len(microsystems))
        cached = self._accelerator_cache.get(token)
        if cached is not None:
            return cached
        center_level = data.level_to[centers[0]] - 1 if centers else final_levels[planet].get("b7", 0)
        microsystem_level = data.level_to[microsystems[0]] - 1 if microsystems else final_levels[planet].get("b17", 0)
        best = [[PATH_NEVER] * (len(microsystems) + 1) for _ in range(len(centers) + 1)]
        best[0][0] = 0.0
        for done_c in range(len(centers) + 1):
            for done_m in range(len(microsystems) + 1):
                hours = best[done_c][done_m]
                divisor = float(building_time_divisor(center_level + done_c, microsystem_level + done_m, data.speedup))
                if done_c < len(centers):
                    longer = hours + data.base_hours[centers[done_c]] / divisor
                    if longer < best[done_c + 1][done_m]:
                        best[done_c + 1][done_m] = longer
                if done_m < len(microsystems):
                    longer = hours + data.base_hours[microsystems[done_m]] / divisor
                    if longer < best[done_c][done_m + 1]:
                        best[done_c][done_m + 1] = longer
        self._accelerator_cache[token] = best[-1][-1]
        return best[-1][-1]

    def step_duration(self, step: int, levels_list: Sequence[Mapping[str, int]]) -> float:
        """Compute active build/research/shipyard/colonize duration in hours for ``step``."""
        data = self._data
        kind, planet = data.kind[step], data.step_planet[step]
        if kind == "colonize":
            return data.base_hours[step]
        if kind == "research":
            lab_b8 = levels_list[data.lab_planet].get("b8", 0) if data.lab_planet >= 0 else 0
            return data.base_hours[step] / float(research_time_divisor(lab_b8, data.speedup))
        levels = levels_list[planet]
        if kind == "ships":
            return data.base_hours[step] / float(
                shipyard_time_divisor(levels.get("b9", 0), levels.get("b17", 0), data.speedup)
            )
        return data.base_hours[step] / float(
            building_time_divisor(levels.get("b7", 0), levels.get("b17", 0), data.speedup)
        )
