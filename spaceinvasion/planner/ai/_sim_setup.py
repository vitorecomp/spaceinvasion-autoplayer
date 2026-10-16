"""Constructor argument validation, static problem indexing, and final-state metrics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coercion import coerce_finite_float, coerce_int
from spaceinvasion.planner.ai._problem_queues import summarize_blast_furnace_entries
from spaceinvasion.planner.ai._sim_production import ProductionModel
from spaceinvasion.planner.ai._sim_types import (
    ChainIndex,
    FinalMetrics,
    SimProblemData,
    Vec4,
    chain_key_from_object,
    int4_from_object,
    int_mapping,
    list_of_mappings,
    mapping_or_empty,
    reqs_from_object,
    vec4_from_object,
)
from spaceinvasion.planner.ai._types import PATH_HOURS_PER_WEEK
from spaceinvasion.planner.ai.problem import planet_hourly_output
from spaceinvasion.planner.config_optimizer import (
    RESOURCE_KEYS,
    building_time_divisor,
    research_time_divisor,
    shipyard_time_divisor,
)


def validate_simulator_args(
    *,
    spare_percent: Mapping[str, float] | None,
    fleet_command_center_level: int | None,
    reward_weeks: int,
    weekly_discount: float,
    transport_dispatch_delay_minutes: float,
) -> tuple[Vec4, Vec4, int | None, int, float, float]:
    """Validate simulator hyperparameters and return normalized tuples/scalars."""
    if int(reward_weeks) < 1:
        raise ValueError(f"reward_weeks must be at least 1, got {reward_weeks!r}")
    if not (0.0 < float(weekly_discount) <= 1.0):
        raise ValueError(f"weekly_discount must be in (0, 1], got {weekly_discount!r}")
    if fleet_command_center_level is not None and int(fleet_command_center_level) < 0:
        raise ValueError(f"fleet_command_center_level must be >= 0 or None, got {fleet_command_center_level!r}")
    if float(transport_dispatch_delay_minutes) < 0.0:
        raise ValueError(f"transport_dispatch_delay_minutes must be >= 0, got {transport_dispatch_delay_minutes!r}")
    if spare_percent is not None:
        if set(spare_percent) != set(RESOURCE_KEYS):
            raise ValueError(f"spare_percent must set {RESOURCE_KEYS}, got {sorted(spare_percent)}")
        for resource, pct in spare_percent.items():
            if not (0.0 <= float(pct) < 100.0):
                raise ValueError(f"spare_percent[{resource!r}] must be in [0, 100), got {pct!r}")
    spare_t: Vec4 = (
        float(spare_percent["pig_iron"]) if spare_percent is not None else 0.0,
        float(spare_percent["metal"]) if spare_percent is not None else 0.0,
        float(spare_percent["kryptonite"]) if spare_percent is not None else 0.0,
        float(spare_percent["spice"]) if spare_percent is not None else 0.0,
    )
    keep_t: Vec4 = (
        1.0 - spare_t[0] / 100.0,
        1.0 - spare_t[1] / 100.0,
        1.0 - spare_t[2] / 100.0,
        1.0 - spare_t[3] / 100.0,
    )
    fixed_cc = None if fleet_command_center_level is None else int(fleet_command_center_level)
    delay_min = min(1440.0, max(0.0, float(transport_dispatch_delay_minutes)))
    return spare_t, keep_t, fixed_cc, int(reward_weeks), float(weekly_discount), delay_min


def _build_initial_blast_furnace(
    problem: Mapping[str, object], planets: Sequence[Mapping[str, object]]
) -> tuple[dict[str, object], tuple[dict[str, object], ...]]:
    raw_init_bf = problem.get("initial_blast_furnace")
    if isinstance(raw_init_bf, Mapping) and isinstance(raw_init_bf.get("planets"), Sequence):
        summary = summarize_blast_furnace_entries(list_of_mappings(raw_init_bf.get("planets")))
    else:
        entries: list[dict[str, object]] = []
        for idx, p in enumerate(planets):
            if not bool(p.get("founded")):
                continue
            lvls = int_mapping(p.get("levels"))
            b2_lvl = lvls.get("b2", 0)
            entries.append(
                {
                    "planet_index": coerce_int(p.get("index"), idx),
                    "planet_id": coerce_int(p.get("planet_id"), idx + 1),
                    "coords": str(p.get("coords") or ""),
                    "name": str(p.get("name") or p.get("coords") or ""),
                    "b2_level": b2_lvl,
                    "initial_pct": coerce_int(p.get("blast_furnace_pct"), 100 if b2_lvl > 0 else 0),
                    "rate_known": bool(p.get("blast_furnace_rate_known", False)),
                }
            )
        summary = summarize_blast_furnace_entries(entries)
    capable = tuple(row for row in list_of_mappings(summary.get("planets")) if coerce_int(row.get("b2_level"), 0) > 0)
    return summary, capable


def _index_step_chains(
    steps: Sequence[Mapping[str, object]],
    kind: Sequence[str],
    step_planet: Sequence[int],
    planet_count: int,
    research_queue: int,
) -> ChainIndex:
    chains: dict[tuple[str, int, str], list[int]] = {}
    for step in steps:
        chains.setdefault(chain_key_from_object(step.get("chain")), []).append(coerce_int(step.get("id"), 0))
    chain_steps = list(chains.values())
    step_chain = [0] * len(steps)
    build_chains: list[dict[str, int]] = [{} for _ in range(planet_count)]
    demolish_chains: list[list[int]] = [[] for _ in range(planet_count)]
    ship_chains: list[list[int]] = [[] for _ in range(planet_count)]
    colonize_step = [-1] * planet_count
    research_chains: dict[str, int] = {}
    for chain_index, ((ckind, cplanet, ckey), members) in enumerate(chains.items()):
        for member in members:
            step_chain[member] = chain_index
        if ckind == "build":
            build_chains[cplanet][ckey] = chain_index
        elif ckind == "demolish":
            demolish_chains[cplanet].append(chain_index)
        elif ckind == "ships":
            ship_chains[cplanet].append(chain_index)
        elif ckind == "colonize":
            colonize_step[cplanet] = members[0]
        else:
            research_chains[ckey] = chain_index
    target_step_count = sum(1 for k in kind if k != "demolish")
    builds_per_planet = [
        sum(1 for p, k in zip(step_planet, kind, strict=False) if p == idx and k == "build")
        for idx in range(planet_count)
    ]
    research_dependents: dict[str, set[int]] = {}
    for step in steps:
        p_idx = coerce_int(step.get("planet"), -1)
        owner = research_queue if p_idx < 0 else p_idx
        for req_owner, rkey, _lvl in reqs_from_object(step.get("requires")):
            if req_owner < 0:
                research_dependents.setdefault(rkey, set()).add(owner)
    return ChainIndex(
        chain_steps=chain_steps,
        step_chain=step_chain,
        build_chains=build_chains,
        demolish_chains=demolish_chains,
        ship_chains=ship_chains,
        colonize_step=colonize_step,
        research_chains=research_chains,
        target_step_count=target_step_count,
        builds_per_planet=builds_per_planet,
        research_dependents=research_dependents,
    )


def _compute_attack_vectors(
    planets: Sequence[Mapping[str, object]],
    attack_income: Mapping[str, float],
    slot_attack_income: Mapping[str, float] | None,
    fixed_cc: int | None,
) -> tuple[Vec4, Vec4, Vec4]:
    fixed_atk: Vec4 = (
        float(attack_income.get("pig_iron", 0.0)),
        float(attack_income.get("metal", 0.0)),
        float(attack_income.get("kryptonite", 0.0)),
        float(attack_income.get("spice", 0.0)),
    )
    slot_atk: Vec4 = (
        float(slot_attack_income.get("pig_iron", 0.0)) if slot_attack_income is not None else 0.0,
        float(slot_attack_income.get("metal", 0.0)) if slot_attack_income is not None else 0.0,
        float(slot_attack_income.get("kryptonite", 0.0)) if slot_attack_income is not None else 0.0,
        float(slot_attack_income.get("spice", 0.0)) if slot_attack_income is not None else 0.0,
    )
    start_slots = sum(
        1 + (int_mapping(p.get("levels")).get("b10", 0) if fixed_cc is None else fixed_cc)
        for p in planets
        if bool(p.get("founded"))
    )
    attack: Vec4 = (
        fixed_atk[0] + start_slots * slot_atk[0],
        fixed_atk[1] + start_slots * slot_atk[1],
        fixed_atk[2] + start_slots * slot_atk[2],
        fixed_atk[3] + start_slots * slot_atk[3],
    )
    return fixed_atk, slot_atk, attack


def build_problem_data(
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
) -> SimProblemData:
    """Validate simulator arguments and extract all immutable step/planet tables from ``problem``."""
    spare_t, keep_t, fixed_cc, weeks, discount, delay_min = validate_simulator_args(
        spare_percent=spare_percent,
        fleet_command_center_level=fleet_command_center_level,
        reward_weeks=reward_weeks,
        weekly_discount=weekly_discount,
        transport_dispatch_delay_minutes=transport_dispatch_delay_minutes,
    )
    week_weights = tuple(discount**week for week in range(weeks))
    suffix = [0.0] * (weeks + 1)
    for week in range(weeks - 1, -1, -1):
        suffix[week] = suffix[week + 1] + PATH_HOURS_PER_WEEK * week_weights[week]
    merchant_rates: Vec4 = (
        1.0 / float(game_data.MERCHANT_RATES_IN_SPICE["pig_iron"]),
        1.0 / float(game_data.MERCHANT_RATES_IN_SPICE["metal"]),
        1.0 / float(game_data.MERCHANT_RATES_IN_SPICE["kryptonite"]),
        1.0 / float(game_data.MERCHANT_RATES_IN_SPICE["spice"]),
    )
    planets = list_of_mappings(problem.get("planets"))
    steps = list_of_mappings(problem.get("steps"))
    fixed_atk, slot_atk, attack = _compute_attack_vectors(planets, attack_income, slot_attack_income, fixed_cc)
    planet_count = len(planets)
    research_queue = planet_count
    init_bf, b2_capable = _build_initial_blast_furnace(problem, planets)
    kind = [str(s.get("kind") or "") for s in steps]
    step_planet = [coerce_int(s.get("planet"), -1) for s in steps]
    costs = [vec4_from_object(s.get("cost")) for s in steps]
    cost_floor: list[Vec4] = [
        (
            c[0] - (1e-9 * c[0] + 1e-6),
            c[1] - (1e-9 * c[1] + 1e-6),
            c[2] - (1e-9 * c[2] + 1e-6),
            c[3] - (1e-9 * c[3] + 1e-6),
        )
        for c in costs
    ]
    chains = _index_step_chains(steps, kind, step_planet, planet_count, research_queue)
    return SimProblemData(
        problem=problem,
        planets=planets,
        steps=steps,
        spare_percent=spare_t,
        keep=keep_t,
        fleet_command_center_level=fixed_cc,
        reward_weeks=weeks,
        weekly_discount=discount,
        transport_dispatch_delay_minutes=delay_min,
        dispatch_delay_hours=delay_min / 60.0,
        horizon_hours=PATH_HOURS_PER_WEEK * weeks,
        week_weights=week_weights,
        suffix_discounted_hours=tuple(suffix),
        merchant_rates=merchant_rates,
        speedup=coerce_finite_float(problem.get("speedup_factor"), 1.0),
        fixed_attack=fixed_atk,
        slot_attack=slot_atk,
        attack=attack,
        lab_planet=coerce_int(problem.get("lab_planet"), -1),
        central_planet=coerce_int(problem.get("central_planet"), 0),
        planet_count=planet_count,
        research_queue=research_queue,
        queue_count=2 * planet_count + 1,
        temperature=[coerce_int(p.get("temperature_c"), 0) for p in planets],
        fields_max=[coerce_int(p.get("fields_max"), 0) for p in planets],
        model_transport_travel_requested=bool(model_transport_travel),
        model_transport_travel=bool(model_transport_travel) and planet_count >= 2,
        initial_jobs=tuple(
            list_of_mappings(problem.get("in_progress_builds")) + list_of_mappings(problem.get("in_progress_research"))
        ),
        initial_arrivals=tuple(list_of_mappings(problem.get("in_flight_arrivals"))),
        initial_blast_furnace=init_bf,
        initial_b2_capable_planets=b2_capable,
        empire_transport_ships=int_mapping(problem.get("empire_transport_ships")),
        fallback_distance_units=coerce_finite_float(problem.get("fallback_distance_units"), 1175.0),
        kind=kind,
        step_planet=step_planet,
        key=[str(s.get("key") or "") for s in steps],
        level_to=[coerce_int(s.get("level_to"), 0) for s in steps],
        cost=costs,
        cost_floor=cost_floor,
        cost_spice_eq=[coerce_finite_float(s.get("cost_spice_eq"), 0.0) for s in steps],
        base_hours=[coerce_finite_float(s.get("base_hours"), 0.0) for s in steps],
        queue=[coerce_int(s.get("queue"), -1) for s in steps],
        pay_planet=[coerce_int(s.get("pay_planet"), 0) for s in steps],
        requires=[reqs_from_object(s.get("requires")) for s in steps],
        warehouse_levels=[int4_from_object(s.get("warehouse_levels")) for s in steps],
        chains=chains,
    )


def compute_final_metrics(data: SimProblemData, prod: ProductionModel) -> FinalMetrics:
    """Precompute target-state steady-state outputs, divisors, in-horizon needs, and reward ceiling."""
    final_research = int_mapping(data.problem.get("research_target"))
    r1, r2 = final_research.get("r1", 0), final_research.get("r2", 0)
    final_levels = [int_mapping(p.get("target_levels")) for p in data.planets]
    final_solar = [
        coerce_int(p.get("solar_cells"), 0) + coerce_int(mapping_or_empty(p.get("missing_ships")).get("s9"), 0)
        for p in data.planets
    ]
    final_b_div = [
        float(building_time_divisor(lvls.get("b7", 0), lvls.get("b17", 0), data.speedup)) for lvls in final_levels
    ]
    final_s_div = [
        float(shipyard_time_divisor(lvls.get("b9", 0), lvls.get("b17", 0), data.speedup)) for lvls in final_levels
    ]
    lab_b8 = final_levels[data.lab_planet].get("b8", 0) if data.lab_planet >= 0 else 0
    final_r_div = float(research_time_divisor(lab_b8, data.speedup))
    final_output = [
        planet_hourly_output(prod.planet_totals(planet, lvls, final_solar[planet], r1, r2))
        for planet, lvls in enumerate(final_levels)
    ]
    all_founded = [True] * data.planet_count
    final_fleet_slots = prod.fleet_slots_of(final_levels, all_founded)
    final_supply = prod.supply_of(final_output, all_founded, final_levels, final_fleet_slots)
    final_out_total: Vec4 = (
        sum(out[1] for out in final_output),
        sum(out[2] for out in final_output),
        sum(out[3] for out in final_output),
        sum(out[4] for out in final_output),
    )
    final_net_out: Vec4 = (
        data.keep[0] * final_out_total[0],
        data.keep[1] * final_out_total[1],
        data.keep[2] * final_out_total[2],
        data.keep[3] * final_out_total[3],
    )
    in_horizon = [False] * len(data.steps)
    h_need = [0.0, 0.0, 0.0, 0.0]
    for step in range(len(data.steps)):
        planet, skind = data.step_planet[step], data.kind[step]
        divisor = (
            1.0
            if skind == "colonize"
            else final_r_div
            if skind == "research"
            else final_s_div[planet]
            if skind == "ships"
            else final_b_div[planet]
        )
        if data.base_hours[step] / divisor <= data.horizon_hours:
            in_horizon[step] = True
            c = data.cost[step]
            h_need[0] += c[0]
            h_need[1] += c[1]
            h_need[2] += c[2]
            h_need[3] += c[3]
    ceiling_hr = prod.reward_ceiling_per_hour(final_levels)
    return FinalMetrics(
        final_levels=final_levels,
        final_solar=final_solar,
        final_building_divisor=final_b_div,
        final_shipyard_divisor=final_s_div,
        final_research_divisor=final_r_div,
        final_output=final_output,
        final_fleet_slots=final_fleet_slots,
        final_supply=final_supply,
        final_output_total=final_out_total,
        final_net_output_total=final_net_out,
        in_horizon=in_horizon,
        horizon_need_init=(h_need[0], h_need[1], h_need[2], h_need[3]),
        reward_ceiling_spice_eq_per_hour=ceiling_hr,
        reward_upper_bound=ceiling_hr * data.suffix_discounted_hours[0],
    )
