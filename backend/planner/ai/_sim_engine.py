"""Event-advancement, step start/completion, and weekly reward accrual engine."""

from __future__ import annotations

import heapq

from backend.core.coercion import coerce_int
from backend.planner.ai._sim_queries import SimMutableState
from backend.planner.ai._sim_smelting import SmeltingTracker
from backend.planner.ai._sim_transport import TransportScheduler
from backend.planner.ai._sim_types import FinalMetrics, SimProblemData
from backend.planner.ai._types import (
    PATH_ACCELERATOR_KEYS,
    PATH_FLOW_BUILDING_KEYS,
    PATH_HOURS_PER_WEEK,
    PATH_NEVER,
)


class RolloutEngine:
    """Executes clock advancement, step start/completion, work counters, and weekly reward accrual."""

    def __init__(
        self,
        data: SimProblemData,
        final: FinalMetrics,
        transport: TransportScheduler,
        smelt: SmeltingTracker,
    ) -> None:
        self._data = data
        self._final = final
        self._transport = transport
        self._smelt = smelt

    def count_work(self, sim: SimMutableState, step: int, sign: float) -> None:
        """Adjust remaining base-hour work counters when loading or starting ``step``."""
        data = self._data
        kind, planet = data.kind[step], data.step_planet[step]
        if kind == "research":
            sim.work_research += sign * data.base_hours[step]
        elif kind == "ships":
            sim.work_ships[planet] += sign * data.base_hours[step]
        elif kind == "build" and data.key[step] in PATH_ACCELERATOR_KEYS:
            sim.work_accelerators[planet] += sign * data.base_hours[step]
        elif kind == "build":
            sim.work_buildings[planet] += sign * data.base_hours[step]

    def sync_demolish_work(self, sim: SimMutableState, planet: int) -> None:
        """Count only the demolitions actually required when ``planet`` runs out of fields."""
        data = self._data
        old = sim.work_demolitions[planet]
        if not data.chains.demolish_chains[planet]:
            if old != 0.0:
                sim.work_buildings[planet] = max(0.0, sim.work_buildings[planet] - old)
                sim.work_demolitions[planet] = 0.0
            return
        running = sim.queue_step[planet]
        in_flight_dem = 1 if (running >= 0 and data.kind[running] == "demolish") else 0
        eff_used = sim.fields_used[planet] - in_flight_dem
        needed = max(0, eff_used + sim.remaining_builds[planet] - data.fields_max[planet])
        if needed <= 0:
            new_val = 0.0
        else:
            avail_hours: list[float] = []
            for chain in data.chains.demolish_chains[planet]:
                for step in data.chains.chain_steps[chain][sim.next_step[chain] :]:
                    avail_hours.append(data.base_hours[step])
            avail_hours.sort()
            new_val = sum(avail_hours[:needed])
        sim.work_buildings[planet] = max(0.0, sim.work_buildings[planet] - old + new_val)
        sim.work_demolitions[planet] = new_val

    def next_event_step(self, sim: SimMutableState) -> tuple[float, float, float]:
        """Return ``(elapsed, until_done, until_transit)`` for the earliest pending rollout event."""
        until_done = max(0.0, sim.heap[0][0] - sim.clock) if sim.heap else PATH_NEVER
        tr_heap = self._transport.transit_heap
        until_transit = max(0.0, tr_heap[0][0] - sim.clock) if tr_heap else PATH_NEVER
        until_stock = self._transport.hours_to_stock_event(
            sim.reserved,
            sim.smelting,
            sim.stock,
            sim.planet_stock,
            sim.rate_off,
            sim.rate_on,
            sim.planet_rate_off,
            sim.planet_rate_on,
        )
        early_at = self._transport.early_dispatch_at
        until_early = (
            max(1e-9, early_at - sim.clock)
            if (self._data.model_transport_travel and sim.unstarted_targets > 0 and early_at < PATH_NEVER)
            else PATH_NEVER
        )
        until_review = (
            max(1e-9, sim.review_at - sim.clock)
            if (
                sim.unstarted_targets > 0
                and (sim.heap or tr_heap or until_stock < PATH_NEVER)
                and sim.review_at < PATH_NEVER
            )
            else PATH_NEVER
        )
        elapsed = min(until_done, until_transit, until_stock, until_early, until_review)
        return elapsed, until_done, until_transit

    def accrue_weekly(self, sim: SimMutableState, start_hour: float, elapsed: float) -> None:
        """Credit net planetary resource production into weekly reward buckets."""
        data = self._data
        if elapsed <= 0.0 or start_hour >= data.horizon_hours:
            return
        end_hour = min(data.horizon_hours, start_hour + elapsed)
        g0 = g1 = g2 = g3 = 0.0
        for planet in range(data.planet_count):
            if sim.founded[planet]:
                made = sim.output[planet]
                g0 += made[1]
                g1 += made[2]
                g2 += made[3]
                g3 += made[4]
        keep = data.keep
        r0, r1, r2, r3 = keep[0] * g0, keep[1] * g1, keep[2] * g2, keep[3] * g3
        week = int(start_hour // PATH_HOURS_PER_WEEK)
        cursor = start_hour
        while cursor < end_hour and week < data.reward_weeks:
            boundary = min(end_hour, (week + 1) * PATH_HOURS_PER_WEEK)
            span = boundary - cursor
            if span > 0.0:
                bucket = sim.weekly_resources[week]
                bucket[0] += r0 * span
                bucket[1] += r1 * span
                bucket[2] += r2 * span
                bucket[3] += r3 * span
            cursor = boundary
            week += 1

    def _advance_planet_stocks(self, sim: SimMutableState, elapsed: float) -> None:
        count = self._data.planet_count
        p_rates = sim.planet_rate_on if sim.smelting else sim.planet_rate_off
        spice_deficit = 0.0
        for planet in range(count):
            if not sim.founded[planet]:
                continue
            pr0, pr1, pr2, pr3 = p_rates[planet]
            p_stock = sim.planet_stock[planet]
            v0, v1, v2 = p_stock[0] + pr0 * elapsed, p_stock[1] + pr1 * elapsed, p_stock[2] + pr2 * elapsed
            p_stock[0] = v0 if v0 > 0.0 else 0.0
            p_stock[1] = v1 if v1 > 0.0 else 0.0
            p_stock[2] = v2 if v2 > 0.0 else 0.0
            dp3 = pr3 * elapsed
            if dp3 >= 0.0:
                p_stock[3] += dp3
            else:
                burn = -dp3
                if p_stock[3] >= burn:
                    p_stock[3] -= burn
                else:
                    spice_deficit += burn - p_stock[3]
                    p_stock[3] = 0.0
        if spice_deficit > 1e-9:
            total_spice = sum(sim.planet_stock[p][3] for p in range(count))
            if total_spice > 1e-9:
                scale = 1.0 - min(1.0, spice_deficit / total_spice)
                for p in range(count):
                    sim.planet_stock[p][3] = max(0.0, sim.planet_stock[p][3] * scale)
            else:
                for p in range(count):
                    sim.planet_stock[p][3] = 0.0
        s0 = s1 = s2 = s3 = 0.0
        for p in range(count):
            ps = sim.planet_stock[p]
            s0 += ps[0]
            s1 += ps[1]
            s2 += ps[2]
            s3 += ps[3]
        sim.stock[0], sim.stock[1], sim.stock[2], sim.stock[3] = s0, s1, s2, s3

    def advance(self, sim: SimMutableState, elapsed: float, *, ends_a_step: bool, ends_a_transit: bool = False) -> None:
        """Advance simulation clock by ``elapsed`` hours, accrue resources, deliver cargo, and complete jobs."""
        data = self._data
        if elapsed > 0.0:
            self.accrue_weekly(sim, sim.clock, elapsed)
            rates = sim.rate_on if sim.smelting else sim.rate_off
            gross_attack, keep = sim.attack_now, data.keep
            for pos in range(4):
                sim.loot_collected[pos] += keep[pos] * gross_attack[pos] * elapsed
                if keep[pos] > 0.0:
                    gross_rate = rates[pos] / keep[pos]
                    sim.spare_diverted[pos] += (1.0 - keep[pos]) * gross_rate * elapsed
            if data.model_transport_travel:
                self._advance_planet_stocks(sim, elapsed)
            else:
                for pos in range(4):
                    sim.stock[pos] += rates[pos] * elapsed
            if sim.smelting:
                self._smelt.accrue(sim.clock, elapsed, sim.furnace_metal)
        if ends_a_step and sim.heap:
            sim.clock = sim.heap[0][0]
        elif ends_a_transit and self._transport.transit_heap:
            sim.clock = self._transport.transit_heap[0][0]
        else:
            sim.clock += elapsed
        self._transport.deliver_arrivals(sim.clock, sim.stock, sim.planet_stock)
        while sim.heap and sim.heap[0][0] <= sim.clock + 1e-12:
            self.complete_step(sim, heapq.heappop(sim.heap)[1])
        if sim.completions_since_review >= sim.review_every or sim.clock >= sim.review_at:
            sim._review()

    def start_step(self, sim: SimMutableState, step: int) -> None:
        """Pay resources, mark ``step`` running on its queue, and push its completion event."""
        data = self._data
        cost, dst = data.cost[step], data.pay_planet[step]
        for pos in range(4):
            if data.model_transport_travel:
                sim.planet_stock[dst][pos] = max(0.0, sim.planet_stock[dst][pos] - cost[pos])
            sim.stock[pos] = max(0.0, sim.stock[pos] - cost[pos])
            sim.need[pos] -= cost[pos]
            if self._final.in_horizon[step]:
                sim.horizon_need[pos] = max(0.0, sim.horizon_need[pos] - cost[pos])
        hours = sim.duration(step)
        sim.started[step] = True
        if self._transport.planet_earmark_step[dst] == step:
            self._transport.planet_earmark_step[dst] = -1
        sim.order.append(step)
        sim.start[step] = sim.clock
        sim.end[step] = sim.clock + hours
        heapq.heappush(sim.heap, (sim.clock + hours, step))
        sim.unstarted -= 1
        sim.next_step[data.chains.step_chain[step]] += 1
        self.count_work(sim, step, -1.0)
        queue, planet = data.queue[step], data.step_planet[step]
        if queue >= 0:
            sim.queue_step[queue] = step
            sim.queue_end[queue] = sim.clock + hours
        if data.kind[step] != "demolish":
            sim.unstarted_targets -= 1
        if data.kind[step] == "build":
            sim.fields_used[planet] += 1
            sim.remaining_builds[planet] = max(0, sim.remaining_builds[planet] - 1)
            self.sync_demolish_work(sim, planet)
        elif data.kind[step] == "demolish":
            self.sync_demolish_work(sim, planet)
        if planet < 0:
            sim.dirty.add(data.research_queue)

    def complete_initial_job(self, sim: SimMutableState, job_idx: int) -> None:
        """Complete a pre-loaded live in-progress building or research job."""
        data = self._data
        job = data.initial_jobs[job_idx]
        q = coerce_int(job.get("queue"), -1)
        if 0 <= q < data.queue_count:
            sim.queue_initial_count[q] = max(0, sim.queue_initial_count[q] - 1)
            if sim.queue_initial_count[q] == 0:
                sim.queue_step[q] = -1
                sim.queue_end[q] = 0.0
        sim.completions_since_review += 1
        if str(job.get("kind") or "") == "research":
            rkey = str(job.get("key") or "")
            sim.research[rkey] = max(sim.research.get(rkey, 0), coerce_int(job.get("level_to"), 0))
            sim.dirty.add(data.research_queue)
            sim.dirty.update(data.chains.research_dependents.get(rkey, ()))
            if rkey in ("r1", "r2"):
                for other in range(data.planet_count):
                    sim._refresh_planet(other)
                sim._refresh_rates()
                sim.dirty.update(range(data.planet_count))
            if rkey in ("r4", "r5") and data.model_transport_travel:
                self._transport.rebuild_matrix(sim.research)
            return
        planet = coerce_int(job.get("planet"), 0)
        bkey = str(job.get("key") or "")
        sim.levels[planet][bkey] = max(sim.levels[planet].get(bkey, 0), coerce_int(job.get("level_to"), 0))
        if bkey in PATH_FLOW_BUILDING_KEYS:
            sim._refresh_planet(planet)
            if sim.founded[planet]:
                sim._refresh_rates()
        elif bkey == "b10" and sim.founded[planet] and data.fleet_command_center_level is None:
            sim._refresh_rates()
        sim.dirty.add(planet)
        if planet == data.lab_planet:
            sim.dirty.add(data.research_queue)
        if planet == data.central_planet:
            sim.dirty.update(other for other in range(data.planet_count) if not sim.founded[other])

    def complete_step(self, sim: SimMutableState, step: int) -> None:
        """Complete a scheduled step (or negative-ID initial job) and invalidate dirty owners."""
        if step < 0:
            self.complete_initial_job(sim, (-step) - 2)
            return
        data = self._data
        kind, planet, key = data.kind[step], data.step_planet[step], data.key[step]
        if data.queue[step] >= 0:
            sim.queue_step[data.queue[step]] = -1
        sim.completions_since_review += 1
        if kind == "research":
            sim.research[key] = data.level_to[step]
            sim.dirty.add(data.research_queue)
            sim.dirty.update(data.chains.research_dependents.get(key, ()))
            if key in ("r1", "r2"):
                for other in range(data.planet_count):
                    sim._refresh_planet(other)
                sim._refresh_rates()
                sim.dirty.update(range(data.planet_count))
            if key in ("r4", "r5") and data.model_transport_travel:
                self._transport.rebuild_matrix(sim.research)
            return
        if kind == "colonize":
            sim.founded[planet] = True
            sim._refresh_rates()
        elif kind == "ships":
            if key == "s9":
                sim.solar[planet] += data.level_to[step]
                sim._refresh_planet(planet)
                sim._refresh_rates()
        else:
            if data.level_to[step] > 0:
                sim.levels[planet][key] = data.level_to[step]
            else:
                sim.levels[planet].pop(key, None)
            if kind == "demolish":
                sim.fields_used[planet] -= 1
                self.sync_demolish_work(sim, planet)
            if key in PATH_FLOW_BUILDING_KEYS:
                sim._refresh_planet(planet)
                if sim.founded[planet]:
                    sim._refresh_rates()
            elif key == "b10" and sim.founded[planet] and data.fleet_command_center_level is None:
                sim._refresh_rates()
        sim.dirty.add(planet)
        if planet == data.lab_planet:
            sim.dirty.add(data.research_queue)
        if planet == data.central_planet:
            sim.dirty.update(other for other in range(data.planet_count) if not sim.founded[other])
