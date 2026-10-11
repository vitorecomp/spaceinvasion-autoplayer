"""Inter-planet transport matrix, transit heap, arrivals, and stock-event wait scheduler."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import heapq

from spaceinvasion.core.coercion import coerce_finite_float, coerce_int
from spaceinvasion.planner.ai._problem_queues import estimate_interplanet_transport_seconds
from spaceinvasion.planner.ai._sim_dispatch import dispatch_transports_for_step
from spaceinvasion.planner.ai._sim_types import (
    SimProblemData,
    TransitEntry,
    TransportRecord,
    Vec4,
    mapping_or_empty,
    vec4_from_object,
)
from spaceinvasion.planner.ai._types import PATH_NEVER


class TransportScheduler:
    """Manages pairwise flight/lead times, in-transit cargo queues, and dispatch triggers."""

    def __init__(self, data: SimProblemData) -> None:
        self._data = data
        self._base_travel_hours = self.compute_travel_hours_matrix(
            mapping_or_empty(data.problem.get("research_levels")),
            precomputed=data.problem.get("travel_hours"),
        )
        self.travel_hours: list[list[float]] = [list(row) for row in self._base_travel_hours]
        self.lead_hours: list[list[float]] = [[0.0] * data.planet_count for _ in range(data.planet_count)]
        self.donors_by_dst: list[list[int]] = [[] for _ in range(data.planet_count)]
        self.min_lead_hours_by_dst: list[float] = [0.0] * data.planet_count
        self.max_lead_hours_by_dst: list[float] = [0.0] * data.planet_count
        self.transit_heap: list[TransitEntry] = []
        self._transit_seq: int = 0
        self.in_transit_to: list[list[float]] = [[0.0, 0.0, 0.0, 0.0] for _ in range(data.planet_count)]
        self.total_in_transit: list[float] = [0.0, 0.0, 0.0, 0.0]
        self.early_dispatch_at: float = PATH_NEVER
        self.step_transport_info: dict[int, TransportRecord] = {}
        self.planet_earmark_step: list[int] = [-1] * data.planet_count
        self.transports_dispatched: int = 0
        self.early_transports_dispatched: int = 0
        self.resources_transported: list[float] = [0.0, 0.0, 0.0, 0.0]
        self.sync_lead_matrix()

    def compute_travel_hours_matrix(
        self, research_levels: Mapping[str, object], *, precomputed: object = None
    ) -> list[list[float]]:
        """Return the pairwise one-way travel-hours matrix using precomputed values or drive physics."""
        count = self._data.planet_count
        if (
            isinstance(precomputed, Sequence)
            and not isinstance(precomputed, (str, bytes, bytearray))
            and len(precomputed) == count
            and all(
                isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)) and len(row) == count
                for row in precomputed
            )
        ):
            return [
                [max(0.0, coerce_finite_float(val, 0.0)) if i != j else 0.0 for j, val in enumerate(row)]
                for i, row in enumerate(precomputed)
            ]
        mat: list[list[float]] = [[0.0] * count for _ in range(count)]
        for i in range(count):
            src_ships = mapping_or_empty(self._data.planets[i].get("ships"))
            src_coords = str(self._data.planets[i].get("coords") or "")
            for j in range(count):
                if i == j:
                    continue
                dst_coords = str(self._data.planets[j].get("coords") or "")
                ow_sec, _, _ = estimate_interplanet_transport_seconds(
                    src_coords,
                    dst_coords,
                    origin_ships=src_ships,
                    empire_ships=self._data.empire_transport_ships,
                    researches=research_levels,
                    fallback_distance_units=self._data.fallback_distance_units,
                )
                mat[i][j] = ow_sec / 3600.0
        return mat

    def sync_lead_matrix(self) -> None:
        """Synchronize dispatch-delay-inclusive lead times and nearest-donor ordering per destination."""
        count = self._data.planet_count
        delay = self._data.dispatch_delay_hours
        self.lead_hours = [
            [0.0 if i == j else (delay + self.travel_hours[i][j]) for j in range(count)] for i in range(count)
        ]
        self.donors_by_dst = []
        self.min_lead_hours_by_dst = []
        self.max_lead_hours_by_dst = []
        for dst in range(count):
            donors = sorted(
                (src for src in range(count) if src != dst),
                key=lambda src: (self.lead_hours[src][dst], self.travel_hours[src][dst], src),
            )
            self.donors_by_dst.append(donors)
            if donors:
                self.min_lead_hours_by_dst.append(self.lead_hours[donors[0]][dst])
                self.max_lead_hours_by_dst.append(self.lead_hours[donors[-1]][dst])
            else:
                self.min_lead_hours_by_dst.append(0.0)
                self.max_lead_hours_by_dst.append(0.0)

    def rebuild_matrix(self, research_levels: Mapping[str, object]) -> None:
        """Recompute flight/lead matrices after drive technology (``r4``/``r5``) completes."""
        self.travel_hours = self.compute_travel_hours_matrix(research_levels, precomputed=None)
        self.sync_lead_matrix()

    def reset(self) -> None:
        """Reset transient transport state and pre-load live incoming friendly cargo arrivals."""
        count = self._data.planet_count
        self.transit_heap = []
        self._transit_seq = 0
        self.in_transit_to = [[0.0, 0.0, 0.0, 0.0] for _ in range(count)]
        self.total_in_transit = [0.0, 0.0, 0.0, 0.0]
        self.early_dispatch_at = PATH_NEVER
        self.step_transport_info = {}
        self.planet_earmark_step = [-1] * count
        self.transports_dispatched = 0
        self.early_transports_dispatched = 0
        self.resources_transported = [0.0, 0.0, 0.0, 0.0]
        self.travel_hours = [list(row) for row in self._base_travel_hours]
        self.sync_lead_matrix()
        for arrival in self._data.initial_arrivals:
            dst = coerce_int(arrival.get("planet"), -1)
            if not (0 <= dst < count):
                continue
            rem_hr = max(
                1e-6,
                coerce_finite_float(
                    arrival.get("remaining_hours"),
                    coerce_finite_float(arrival.get("remaining_seconds"), 60.0) / 3600.0,
                ),
            )
            cargo_raw = arrival.get("cargo_tuple")
            if cargo_raw is not None:
                cargo = vec4_from_object(cargo_raw)
            elif isinstance(arrival.get("cargo"), Mapping):
                c_map = mapping_or_empty(arrival.get("cargo"))
                cargo = (
                    max(0.0, coerce_finite_float(c_map.get("pig_iron"), 0.0)),
                    max(0.0, coerce_finite_float(c_map.get("metal"), 0.0)),
                    max(0.0, coerce_finite_float(c_map.get("kryptonite"), 0.0)),
                    max(0.0, coerce_finite_float(c_map.get("spice"), 0.0)),
                )
            else:
                continue
            if cargo[0] + cargo[1] + cargo[2] + cargo[3] <= 0.0:
                continue
            self._transit_seq += 1
            heapq.heappush(self.transit_heap, (rem_hr, self._transit_seq, dst, cargo, -1))
            for pos in range(4):
                self.in_transit_to[dst][pos] += cargo[pos]
                self.total_in_transit[pos] += cargo[pos]

    def enqueue_batch(
        self,
        dst: int,
        cargo: Vec4,
        *,
        clock: float,
        sources: Sequence[str],
        max_travel_hr: float,
        max_lead_hr: float,
        step: int,
        is_early: bool,
    ) -> None:
        """Schedule an outbound cargo batch into ``transit_heap`` and update step telemetry."""
        arrival = clock + max_lead_hr
        self._transit_seq += 1
        heapq.heappush(self.transit_heap, (arrival, self._transit_seq, dst, cargo, step))
        in_dst, tot_tr, res_tr = self.in_transit_to[dst], self.total_in_transit, self.resources_transported
        for pos in range(4):
            in_dst[pos] += cargo[pos]
            tot_tr[pos] += cargo[pos]
            res_tr[pos] += cargo[pos]
        n_flights = max(1, len(sources))
        self.transports_dispatched += n_flights
        if is_early:
            self.early_transports_dispatched += n_flights
        if step < 0:
            return
        self.planet_earmark_step[dst] = step
        info = self.step_transport_info.get(step)
        if info is None:
            self.step_transport_info[step] = TransportRecord(
                earliest_request_hr=clock,
                latest_arrival_hr=arrival,
                max_travel_hr=max_travel_hr,
                max_lead_hr=max_lead_hr,
                sources=list(sources),
                total_shipped=[cargo[0], cargo[1], cargo[2], cargo[3]],
            )
            return
        if clock < info.earliest_request_hr:
            info.earliest_request_hr = clock
        if arrival > info.latest_arrival_hr:
            info.latest_arrival_hr = arrival
        if max_travel_hr > info.max_travel_hr:
            info.max_travel_hr = max_travel_hr
        if max_lead_hr > info.max_lead_hr:
            info.max_lead_hr = max_lead_hr
        for sc in sources:
            if sc not in info.sources:
                info.sources.append(sc)
        for pos in range(4):
            info.total_shipped[pos] += cargo[pos]

    def dispatch_for_step(
        self,
        step: int,
        *,
        clock: float,
        smelting: bool,
        reserved: int,
        stock: list[float],
        planet_stock: list[list[float]],
        started: Sequence[bool],
        planet_rate_off: Sequence[Vec4],
        planet_rate_on: Sequence[Vec4],
        free_in: float = 0.0,
        protect_reserved: bool = False,
    ) -> bool:
        """Delegate donor-wave selection and dispatch for ``step``."""
        return dispatch_transports_for_step(
            self._data,
            self,
            step,
            clock=clock,
            smelting=smelting,
            reserved=reserved,
            stock=stock,
            planet_stock=planet_stock,
            started=started,
            planet_rate_off=planet_rate_off,
            planet_rate_on=planet_rate_on,
            free_in=free_in,
            protect_reserved=protect_reserved,
        )

    def deliver_arrivals(self, clock: float, stock: list[float], planet_stock: list[list[float]]) -> None:
        """Deliver all cargo arrivals whose timestamp has reached ``clock``."""
        use_travel = self._data.model_transport_travel
        while self.transit_heap and self.transit_heap[0][0] <= clock + 1e-12:
            _arr_hr, _seq_id, dst, cargo, _for_step = heapq.heappop(self.transit_heap)
            for pos in range(4):
                self.in_transit_to[dst][pos] = max(0.0, self.in_transit_to[dst][pos] - cargo[pos])
                self.total_in_transit[pos] = max(0.0, self.total_in_transit[pos] - cargo[pos])
                if use_travel:
                    planet_stock[dst][pos] += cargo[pos]
                stock[pos] += cargo[pos]

    def local_finish_wait_hours(
        self,
        dst: int,
        step: int,
        smelting: bool,
        planet_stock: Sequence[Sequence[float]],
        local_rates: Vec4,
    ) -> float:
        """Return hours until ``dst`` reaches ``step``'s cost from local stock, inbound cargo, and local output."""
        cost, floor = self._data.cost[step], self._data.cost_floor[step]
        have_or_inbound = [planet_stock[dst][pos] + self.in_transit_to[dst][pos] for pos in range(4)]
        positions = (1,) if smelting else (0, 2, 3)
        wait = 0.0
        for pos in positions:
            if have_or_inbound[pos] < floor[pos]:
                lr = local_rates[pos]
                if lr <= 0.0:
                    return PATH_NEVER
                wait = max(wait, (cost[pos] - have_or_inbound[pos]) / lr)
        return wait

    def hours_to_stock_event(
        self,
        reserved: int,
        smelting: bool,
        stock: Sequence[float],
        planet_stock: Sequence[Sequence[float]],
        rate_off: Vec4,
        rate_on: Vec4,
        planet_rate_off: Sequence[Vec4],
        planet_rate_on: Sequence[Vec4],
    ) -> float:
        """Compute hours until the reserved step triggers a stock-ready or transport-dispatch event."""
        if reserved < 0:
            return PATH_NEVER
        data = self._data
        cost, floor = data.cost[reserved], data.cost_floor[reserved]
        if not data.model_transport_travel:
            if smelting:
                return (cost[1] - stock[1]) / rate_on[1] if rate_on[1] > 0.0 else PATH_NEVER
            wait = 0.0
            for pos in (0, 2, 3):
                if stock[pos] < floor[pos]:
                    if rate_off[pos] <= 0.0:
                        return PATH_NEVER
                    wait = max(wait, (cost[pos] - stock[pos]) / rate_off[pos])
            return wait
        dst = data.pay_planet[reserved]
        have_or_inbound = [planet_stock[dst][pos] + self.in_transit_to[dst][pos] for pos in range(4)]
        if all(have_or_inbound[pos] >= floor[pos] for pos in range(4)):
            return PATH_NEVER
        local_rates = (
            (planet_rate_on[dst] if smelting else planet_rate_off[dst])
            if 0 <= dst < data.planet_count
            else (
                0.0,
                0.0,
                0.0,
                0.0,
            )
        )
        max_lead, min_lead = self.max_lead_hours_by_dst[dst], self.min_lead_hours_by_dst[dst]
        positions = (1,) if smelting else (0, 2, 3)
        rates = rate_on if smelting else rate_off
        dispatch_wait, dispatch_possible = 0.0, True
        for pos in positions:
            if have_or_inbound[pos] >= floor[pos]:
                continue
            eff_avail = stock[pos] + self.in_transit_to[dst][pos] + max(0.0, local_rates[pos]) * max_lead
            if eff_avail < floor[pos]:
                if rates[pos] <= 0.0:
                    dispatch_possible = False
                    break
                dispatch_wait = max(dispatch_wait, (cost[pos] - eff_avail) / rates[pos])
        local_wait = self.local_finish_wait_hours(dst, reserved, smelting, planet_stock, local_rates)
        if dispatch_possible and dispatch_wait > 1e-12:
            return min(dispatch_wait, local_wait)
        if local_wait < PATH_NEVER and local_wait > 1e-12:
            return local_wait
        if dispatch_possible and min_lead < max_lead:
            alt_wait = 0.0
            for pos in positions:
                if have_or_inbound[pos] >= floor[pos]:
                    continue
                eff_min = stock[pos] + self.in_transit_to[dst][pos] + max(0.0, local_rates[pos]) * min_lead
                if eff_min < floor[pos]:
                    if rates[pos] <= 0.0:
                        return PATH_NEVER
                    alt_wait = max(alt_wait, (cost[pos] - eff_min) / rates[pos])
            if alt_wait > 1e-12:
                return alt_wait
        return PATH_NEVER

    def export_step_transport_info(self) -> dict[int, dict[str, object]]:
        """Serialize per-step transport records into plain JSON-ready dicts."""
        return {step_id: rec.to_dict() for step_id, rec in self.step_transport_info.items()}
