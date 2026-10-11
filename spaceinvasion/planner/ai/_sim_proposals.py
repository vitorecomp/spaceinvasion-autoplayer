"""Proposal execution, lower-bound analysis, and rollout summary serialization."""

from __future__ import annotations

from collections.abc import Sequence

from spaceinvasion.planner.ai._sim_engine import RolloutEngine
from spaceinvasion.planner.ai._sim_production import ProductionModel
from spaceinvasion.planner.ai._sim_queries import SimMutableState
from spaceinvasion.planner.ai._sim_smelting import SmeltingTracker
from spaceinvasion.planner.ai._sim_transport import TransportScheduler
from spaceinvasion.planner.ai._sim_types import FinalMetrics, Proposal, SimProblemData, int_mapping
from spaceinvasion.planner.ai._types import PATH_MINE_KEYS, PATH_NEVER


class ProposalDispatcher:
    """Starts prioritized strategy proposals, triggers secondary transports, and builds rollout reports."""

    def __init__(
        self,
        data: SimProblemData,
        final: FinalMetrics,
        prod: ProductionModel,
        transport: TransportScheduler,
        smelt: SmeltingTracker,
        engine: RolloutEngine,
    ) -> None:
        self._data = data
        self._final = final
        self._prod = prod
        self._transport = transport
        self._smelt = smelt
        self._engine = engine

    def _passes_patience_gate(
        self,
        sim: SimMutableState,
        cand_step: int,
        reserved: int,
        res_queue: int,
        limit: float,
        patience: float,
        eff_stock: Sequence[float],
    ) -> bool:
        c_hours = sim.duration(cand_step)
        if self._data.queue[cand_step] == res_queue and c_hours > limit:
            return False
        c_cost = self._data.cost[cand_step]
        delayed = sim.wait_hours(
            (
                eff_stock[0] - c_cost[0],
                eff_stock[1] - c_cost[1],
                eff_stock[2] - c_cost[2],
                eff_stock[3] - c_cost[3],
            ),
            reserved,
        )
        return not (delayed > limit + patience * c_hours + 1e-9 * limit + 1e-9)

    def _dispatch_transport_wave(
        self, sim: SimMutableState, step: int, *, free_in: float, protect_reserved: bool
    ) -> bool:
        return self._transport.dispatch_for_step(
            step,
            clock=sim.clock,
            smelting=sim.smelting,
            reserved=sim.reserved,
            stock=sim.stock,
            planet_stock=sim.planet_stock,
            started=sim.started,
            planet_rate_off=sim.planet_rate_off,
            planet_rate_on=sim.planet_rate_on,
            free_in=free_in,
            protect_reserved=protect_reserved,
        )

    def _update_travel_smelting(self, sim: SimMutableState, reserved: int) -> None:
        if reserved < 0:
            sim.smelting = False
            return
        res_dst = self._data.pay_planet[reserved]
        res_floor_m = self._data.cost_floor[reserved][1]
        in_dst_m = self._transport.in_transit_to[res_dst][1]
        dst_metal = sim.planet_stock[res_dst][1] + in_dst_m
        if dst_metal >= res_floor_m:
            sim.smelting = False
        elif sim.stock[1] + in_dst_m < res_floor_m:
            sim.smelting = True
        else:
            sim.smelting = sim.output[res_dst][2] > 0.0

    def start_proposals(self, sim: SimMutableState, proposals: list[Proposal]) -> bool:
        """Start eligible proposals, reserve the top unaffordable step, and dispatch inter-planet cargo."""
        data = self._data
        reserved, limit, started_any = -1, 0.0, False
        stock = sim.stock
        patience = sim.strategy.patience if sim.strategy is not None else 0.0
        use_travel = data.model_transport_travel
        busy_candidates: list[tuple[int, float]] = []
        free_wait_candidates: list[int] = []
        seen_busy_queues: set[int] = set()
        seen_free_wait_queues: set[int] = set()
        in_tr = self._transport.total_in_transit
        eff_stock = [stock[p] + in_tr[p] for p in range(4)] if use_travel else stock

        for _priority, step in proposals:
            queue = data.queue[step]
            if sim.started[step]:
                continue
            if queue >= 0 and sim.queue_step[queue] != -1:
                if (
                    use_travel
                    and queue not in seen_busy_queues
                    and sim.is_next_of_its_chain(step)
                    and not sim.blockers(step)
                ):
                    seen_busy_queues.add(queue)
                    busy_candidates.append((step, max(0.0, sim.queue_end[queue] - sim.clock)))
                continue
            floor = data.cost_floor[step]
            pay_stock = sim.planet_stock[data.pay_planet[step]] if use_travel else stock
            if (
                pay_stock[0] >= floor[0]
                and pay_stock[1] >= floor[1]
                and pay_stock[2] >= floor[2]
                and pay_stock[3] >= floor[3]
            ):
                if reserved >= 0:
                    hours = sim.duration(step)
                    if queue >= 0 and queue == data.queue[reserved] and hours > limit:
                        continue
                    cost = data.cost[step]
                    delayed = sim.wait_hours([eff_stock[pos] - cost[pos] for pos in range(4)], reserved)
                    if delayed > limit + patience * hours + 1e-9 * limit + 1e-9:
                        continue
                    if not sim.is_next_of_its_chain(step) or sim.blockers(step):
                        continue
                    limit = max(limit, delayed)
                elif not sim.is_next_of_its_chain(step) or sim.blockers(step):
                    continue
                self._engine.start_step(sim, step)
                started_any = True
                if use_travel:
                    eff_stock = [stock[p] + in_tr[p] for p in range(4)]
            elif reserved < 0 and sim.is_next_of_its_chain(step) and not sim.blockers(step):
                reserved = step
                limit = sim.wait_hours(eff_stock, step)
            elif (
                use_travel
                and reserved >= 0
                and queue >= 0
                and queue != data.queue[reserved]
                and queue not in seen_free_wait_queues
                and sim.is_next_of_its_chain(step)
                and not sim.blockers(step)
            ):
                seen_free_wait_queues.add(queue)
                free_wait_candidates.append(step)

        sim.reserved = reserved
        if not use_travel:
            sim.smelting = reserved >= 0 and stock[1] < data.cost_floor[reserved][1]
            return started_any

        if reserved >= 0:
            self._dispatch_transport_wave(sim, reserved, free_in=0.0, protect_reserved=False)
        res_queue = data.queue[reserved] if reserved >= 0 else -1
        for cand_step, free_in in busy_candidates:
            if reserved >= 0 and not self._passes_patience_gate(
                sim, cand_step, reserved, res_queue, limit, patience, eff_stock
            ):
                continue
            self._dispatch_transport_wave(sim, cand_step, free_in=free_in, protect_reserved=True)
        for cand_step in free_wait_candidates:
            if reserved >= 0 and not self._passes_patience_gate(
                sim, cand_step, reserved, res_queue, limit, patience, eff_stock
            ):
                continue
            self._dispatch_transport_wave(sim, cand_step, free_in=0.0, protect_reserved=True)
        self._update_travel_smelting(sim, reserved)
        return started_any

    def compute_lower_bounds(self, sim: SimMutableState) -> list[tuple[str, float]]:
        """Compute theoretical minimum completion-hour bounds across queues and resource supplies."""
        data, final = self._data, self._final
        count = data.planet_count
        least, _ = sim.queue_work()
        founding = [
            data.base_hours[data.chains.colonize_step[planet]] if data.chains.colonize_step[planet] >= 0 else 0.0
            for planet in range(count)
        ]
        rows: list[tuple[str, float]] = [
            (f"building queue of {data.planets[planet]['coords']}", founding[planet] + least[planet])
            for planet in range(count)
        ]
        rows.append(("research queue", least[count]))
        rows.append(
            ("shipyards", max((founding[planet] + least[count + 1 + planet] for planet in range(count)), default=0.0))
        )
        res_now = int_mapping(data.problem.get("research_levels"))
        res_tgt = int_mapping(data.problem.get("research_target"))
        mining = max(res_now.get("r1", 0), res_tgt.get("r1", 0))
        energy = max(res_now.get("r2", 0), res_tgt.get("r2", 0))
        top_levels = [
            {
                key: max(sim.levels[planet].get(key, 0), final.final_levels[planet].get(key, 0))
                for key in (*PATH_MINE_KEYS, "b10")
            }
            for planet in range(count)
        ]
        top_slots = self._prod.fleet_slots_of(top_levels, [True] * count)
        top_attack = self._prod.gross_attack_of(top_slots)
        ceiling = [0.0, 0.0, 0.0, 0.0]
        for planet in range(count):
            flows = {
                key: self._prod.level_flows(key, level, data.temperature[planet], mining, energy)
                for key, level in top_levels[planet].items()
                if key in PATH_MINE_KEYS and level > 0
            }
            pig_iron = flows["b1"][0] if "b1" in flows else 0.0
            ceiling[0] += pig_iron
            ceiling[1] += min(flows["b2"][1], pig_iron / 2.0) if "b2" in flows else 0.0
            ceiling[2] += flows["b3"][2] if "b3" in flows else 0.0
            ceiling[3] += flows["b4"][3] if "b4" in flows else 0.0
        keep = data.keep
        in_tr = self._transport.total_in_transit
        avail = [sim.stock[p] + in_tr[p] for p in range(4)]
        metal_missing = max(0.0, sim.need[1] - avail[1])
        missing = (
            max(0.0, sim.need[0] - avail[0] + 2.0 * metal_missing),
            metal_missing,
            max(0.0, sim.need[2] - avail[2]),
            max(0.0, sim.need[3] - avail[3]),
        )
        smelt_extra = max(0.0, 2.0 * (keep[1] - keep[0]) * ceiling[1])
        supply = (
            keep[0] * (ceiling[0] + top_attack[0]) + 2.0 * keep[1] * top_attack[1] + smelt_extra,
            keep[1] * (ceiling[1] + top_attack[1]),
            keep[2] * (ceiling[2] + top_attack[2]),
            keep[3] * (ceiling[3] + top_attack[3]),
        )
        labels = ("Pig iron, with the Metal to smelt", "Metal", "Kryptonite", "Spice")
        for position in range(4):
            hours = (
                0.0
                if missing[position] <= 0.0
                else (missing[position] / supply[position] if supply[position] > 0.0 else PATH_NEVER)
            )
            rows.append((f"supply of {labels[position]}", hours))
        return rows

    def build_run_result(self, sim: SimMutableState, status: str, events: int) -> dict[str, object]:
        """Finalize weekly reward accrual and assemble the legacy ``EmpirePathSimulator.run`` result dict."""
        data = self._data
        if not self._smelt.initialized:
            self._smelt.record_transition(sim.clock, sim.smelting, sim.b2_founded_count, sim.reserved)
        if sim.clock < data.horizon_hours:
            self._engine.accrue_weekly(sim, sim.clock, data.horizon_hours - sim.clock)
        rates = data.merchant_rates
        weekly_resources = [tuple(row) for row in sim.weekly_resources]
        weekly_spice_eq = tuple(
            row[0] * rates[0] + row[1] * rates[1] + row[2] * rates[2] + row[3] * rates[3] for row in weekly_resources
        )
        discounted_reward = float(
            sum(weight * amount for weight, amount in zip(data.week_weights, weekly_spice_eq, strict=False))
        )
        completed = sum(1 for end in sim.end if end is not None and end <= sim.clock)
        started_count = len(data.steps) - sim.unstarted
        return {
            "status": status,
            "hours": sim.clock if status == "reached" else PATH_NEVER,
            "stopped_at_hours": sim.clock,
            "order": list(sim.order),
            "start": list(sim.start),
            "end": list(sim.end),
            "steps_started": started_count,
            "steps_completed": completed,
            "steps_running": started_count - completed,
            "waiting_for_step": sim.reserved if status == "stuck" else -1,
            "metal_smelted": self._smelt.metal_smelted,
            "smelting_spans": [tuple(span) for span in self._smelt.smelting_spans],
            "initial_blast_furnace": data.initial_blast_furnace,
            "initial_smelting_target": bool(self._smelt.initial_smelting_target),
            "initial_toggle_needed": bool(self._smelt.initial_toggle_needed),
            "initial_planets_needing_toggle": [dict(row) for row in self._smelt.initial_planets_needing_toggle],
            "smelting_events": [dict(ev) for ev in self._smelt.smelting_events],
            "events": events,
            "reviews": sim.reviews,
            "final_stock": tuple(sim.stock),
            "final_planet_stock": [tuple(row) for row in sim.planet_stock],
            "final_levels": [dict(levels) for levels in sim.levels],
            "final_research": dict(sim.research),
            "final_solar": list(sim.solar),
            "final_founded": list(sim.founded),
            "start_fleet_slots": int(sim.start_fleet_slots),
            "final_fleet_slots": int(sim.fleet_slots),
            "loot_collected": tuple(sim.loot_collected),
            "spare_diverted": tuple(sim.spare_diverted),
            "weekly_resources": weekly_resources,
            "weekly_spice_eq": weekly_spice_eq,
            "discounted_reward": discounted_reward,
            "total_spice_eq_produced": float(sum(weekly_spice_eq)),
            "transports_dispatched": int(self._transport.transports_dispatched),
            "early_transports_dispatched": int(self._transport.early_transports_dispatched),
            "resources_transported": tuple(self._transport.resources_transported),
            "step_transport_info": self._transport.export_step_transport_info(),
        }
