"""Typed data structures, protocols, and payload coercers for ``EmpirePathSimulator``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from spaceinvasion.core.coercion import coerce_finite_float, coerce_int

Vec4 = tuple[float, float, float, float]
Int4 = tuple[int, int, int, int]
PlanetOutput = tuple[float, float, float, float, float, float, float]
StepReq = tuple[int, str, int]
Blocker = tuple[str, int] | tuple[str, int, str, int] | tuple[str, int, int, int]
PriorityKey = tuple[float, float, int]
Proposal = tuple[PriorityKey, int]
TransitEntry = tuple[float, int, int, Vec4, int]


def mapping_or_empty(raw: object) -> Mapping[str, object]:
    """Return ``raw`` as a string-keyed mapping or an empty dict."""
    return raw if isinstance(raw, Mapping) else {}


def list_of_mappings(raw: object) -> list[dict[str, object]]:
    """Coerce a sequence of mappings into a list of plain ``dict[str, object]``."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return []
    return [{str(k): v for k, v in item.items()} for item in raw if isinstance(item, Mapping)]


def int_mapping(raw: object) -> dict[str, int]:
    """Coerce a mapping of integer values into ``dict[str, int]``."""
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): coerce_int(v, 0) for k, v in raw.items()}


def vec4_from_object(raw: object) -> Vec4:
    """Coerce a 4-element numeric sequence into ``Vec4``."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)) or len(raw) < 4:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        coerce_finite_float(raw[0], 0.0),
        coerce_finite_float(raw[1], 0.0),
        coerce_finite_float(raw[2], 0.0),
        coerce_finite_float(raw[3], 0.0),
    )


def int4_from_object(raw: object) -> Int4:
    """Coerce a 4-element integer sequence into ``Int4``."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)) or len(raw) < 4:
        return (0, 0, 0, 0)
    return (
        coerce_int(raw[0], 0),
        coerce_int(raw[1], 0),
        coerce_int(raw[2], 0),
        coerce_int(raw[3], 0),
    )


def reqs_from_object(raw: object) -> tuple[StepReq, ...]:
    """Coerce a step ``requires`` sequence into ``tuple[StepReq, ...]``."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    out: list[StepReq] = []
    for item in raw:
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)) and len(item) >= 3:
            out.append((coerce_int(item[0], -1), str(item[1]), coerce_int(item[2], 0)))
    return tuple(out)


def chain_key_from_object(raw: object) -> tuple[str, int, str]:
    """Coerce a step ``chain`` triple into ``(kind, planet, key)``."""
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)) and len(raw) >= 3:
        return (str(raw[0]), coerce_int(raw[1], -1), str(raw[2]))
    return ("", -1, "")


@dataclass(slots=True)
class TransportRecord:
    """Aggregated inter-planet transport metadata tracked per planned step."""

    earliest_request_hr: float
    latest_arrival_hr: float
    max_travel_hr: float
    max_lead_hr: float
    sources: list[str]
    total_shipped: list[float]

    def to_dict(self) -> dict[str, object]:
        """Serialize into the legacy ``step_transport_info`` dict shape."""
        return {
            "earliest_request_hr": float(self.earliest_request_hr),
            "latest_arrival_hr": float(self.latest_arrival_hr),
            "max_travel_hr": float(self.max_travel_hr),
            "max_lead_hr": float(self.max_lead_hr),
            "sources": list(self.sources),
            "total_shipped": (
                self.total_shipped[0],
                self.total_shipped[1],
                self.total_shipped[2],
                self.total_shipped[3],
            ),
        }


@dataclass(slots=True)
class RateSnapshot:
    """Empire and per-planet hourly production/loot rates for one state snapshot."""

    b2_founded_count: int
    fleet_slots: int
    attack_now: Vec4
    supply: Vec4
    furnace_metal: float
    rate_off: Vec4
    rate_on: Vec4
    planet_rate_off: list[Vec4]
    planet_rate_on: list[Vec4]


@dataclass(slots=True)
class ChainIndex:
    """Indexed per-planet and research step chains plus reverse research dependencies."""

    chain_steps: list[list[int]]
    step_chain: list[int]
    build_chains: list[dict[str, int]]
    demolish_chains: list[list[int]]
    ship_chains: list[list[int]]
    colonize_step: list[int]
    research_chains: dict[str, int]
    target_step_count: int
    builds_per_planet: list[int]
    research_dependents: dict[str, set[int]]


@dataclass(slots=True)
class SimProblemData:
    """Immutable validated problem tables and constants shared across simulator collaborators."""

    problem: Mapping[str, object]
    planets: list[dict[str, object]]
    steps: list[dict[str, object]]
    spare_percent: Vec4
    keep: Vec4
    fleet_command_center_level: int | None
    reward_weeks: int
    weekly_discount: float
    transport_dispatch_delay_minutes: float
    dispatch_delay_hours: float
    horizon_hours: float
    week_weights: tuple[float, ...]
    suffix_discounted_hours: tuple[float, ...]
    merchant_rates: Vec4
    speedup: float
    fixed_attack: Vec4
    slot_attack: Vec4
    attack: Vec4
    lab_planet: int
    central_planet: int
    planet_count: int
    research_queue: int
    queue_count: int
    temperature: list[int]
    fields_max: list[int]
    model_transport_travel_requested: bool
    model_transport_travel: bool
    initial_jobs: tuple[dict[str, object], ...]
    initial_arrivals: tuple[dict[str, object], ...]
    initial_blast_furnace: dict[str, object]
    initial_b2_capable_planets: tuple[dict[str, object], ...]
    empire_transport_ships: dict[str, int]
    fallback_distance_units: float
    kind: list[str]
    step_planet: list[int]
    key: list[str]
    level_to: list[int]
    cost: list[Vec4]
    cost_floor: list[Vec4]
    cost_spice_eq: list[float]
    base_hours: list[float]
    queue: list[int]
    pay_planet: list[int]
    requires: list[tuple[StepReq, ...]]
    warehouse_levels: list[Int4]
    chains: ChainIndex


@dataclass(slots=True)
class FinalMetrics:
    """Target-state steady-state divisors, outputs, horizon cost totals, and reward ceiling."""

    final_levels: list[dict[str, int]]
    final_solar: list[int]
    final_building_divisor: list[float]
    final_shipyard_divisor: list[float]
    final_research_divisor: float
    final_output: list[PlanetOutput]
    final_fleet_slots: int
    final_supply: Vec4
    final_output_total: Vec4
    final_net_output_total: Vec4
    in_horizon: list[bool]
    horizon_need_init: Vec4
    reward_ceiling_spice_eq_per_hour: float
    reward_upper_bound: float


class SimulatorView(Protocol):
    """Typed read/query surface of ``EmpirePathSimulator`` used by ``PathStrategy``."""

    planet_count: int
    queue_count: int
    research_queue: int
    lab_planet: int
    planets: list[dict[str, object]]
    steps: list[dict[str, object]]
    keep: Vec4
    slot_attack: Vec4
    fleet_command_center_level: int | None
    temperature: list[int]
    fields_max: list[int]
    fields_used: list[int]
    kind: list[str]
    step_planet: list[int]
    key: list[str]
    level_to: list[int]
    cost: list[Vec4]
    cost_spice_eq: list[float]
    queue: list[int]
    chain_steps: list[list[int]]
    next_step: list[int]
    build_chains: list[dict[str, int]]
    demolish_chains: list[list[int]]
    research_chains: dict[str, int]
    colonize_step: list[int]
    started: list[bool]
    founded: list[bool]
    levels: list[dict[str, int]]
    research: dict[str, int]
    solar: list[int]
    output: list[PlanetOutput]
    queue_step: list[int]
    supply: Vec4
    supply_version: int
    final_supply: Vec4
    suffix_discounted_hours: tuple[float, ...]
    clock: float
    run_limit_hours: float
    dirty: set[int]

    def queue_work(self) -> tuple[list[float], list[float]]: ...
    def missing_resources(self) -> Vec4: ...
    def horizon_missing_resources(self) -> Vec4: ...
    def changed_totals(self, planet: int, changes: Mapping[str, int]) -> list[float]: ...
    def remaining_discounted_hours(self, hour: float) -> float: ...
    def blockers(self, step: int) -> list[Blocker]: ...
    def duration(self, step: int) -> float: ...
    def is_next_of_its_chain(self, step: int) -> bool: ...
    def level_flows(self, key: str, level: int, temperature: int, mining: int, energy: int) -> tuple[float, ...]: ...
    def planet_totals(
        self, planet: int, levels: Mapping[str, int], solar_cells: int, mining: int, energy: int
    ) -> list[float]: ...


class PathStrategyLike(Protocol):
    """Protocol implemented by path-selection strategies passed to ``EmpirePathSimulator.run``."""

    patience: float

    def begin(self, sim: SimulatorView) -> None: ...
    def review(self, sim: SimulatorView) -> tuple[float, int, bool]: ...
    def proposals(self, sim: SimulatorView, *, forced: bool) -> list[Proposal]: ...
