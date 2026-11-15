"""Configuration, protocols, dataclasses, and caches for yearly ships/week projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from backend.core import catalog as game_data
from backend.core.catalog import SHIP_SLUG_BY_KEY
from backend.core.coercion import coerce_finite_float, coerce_int
from backend.planner.config_optimizer._overrides_helpers import StateInput
from backend.planner.config_optimizer._protocols import as_mapping
from backend.planner.config_optimizer.catalog_math import (
    build_monotonic_base_times,
    building_cost_map,
    building_level_economy,
    building_level_flows,
    building_levels_map,
    building_time_divisor,
    full_cost,
    research_catalog,
    research_info,
    research_time_divisor,
    ship_info,
    solar_cell_energy_per_unit,
    spice_eq,
)
from backend.planner.config_optimizer.constants import RESOURCE_KEYS
from backend.planner.config_optimizer.tech_graph import (
    TECH_GRAPH,
    research_requirements,
    same_planet_building_requirements,
)


@runtime_checkable
class _ResourcesLike(Protocol):
    @property
    def pig_iron(self) -> int: ...

    @property
    def metal(self) -> int: ...

    @property
    def kryptonite(self) -> int: ...

    @property
    def spice(self) -> int: ...


@runtime_checkable
class _HasResources(Protocol):
    @property
    def resources(self) -> _ResourcesLike | None: ...


@runtime_checkable
class _HasSpeedupMultiplier(Protocol):
    @property
    def speedup_multiplier(self) -> float | None: ...


@dataclass(frozen=True)
class _ProjectionConfig:
    years_n: int
    total_weeks: int
    seconds_per_week: float
    ship_key: str
    ship_name: str
    cost_resources: tuple[str, ...]
    ship_cost_map: dict[str, float]
    base_ship_time_s: float
    ship_b_reqs: dict[str, int]
    ship_r_reqs: dict[str, int]
    req_b8_for_ship: int
    speedup: float
    summary_ships_mo: float
    target_ships_wk: float
    all_bkeys: tuple[str, ...]
    b_base_times: dict[str, dict[int, float]]
    r_base_times: dict[str, dict[int, float]]
    b_prereqs_map: dict[str, dict[str, int]]
    b_r_prereqs_map: dict[str, dict[str, int]]


@dataclass
class _SimPlanet:
    idx: int
    planet_id: int
    is_placeholder: bool
    temperature_c: int
    fields_max: int
    solar_cells: int
    cur: dict[str, int]
    tgt: dict[str, int]


@dataclass(frozen=True)
class _Snapshot:
    ships_per_week: float
    resource_cap_per_week: float
    shipyard_cap_per_week: float
    bottleneck: str
    resource_bottleneck: str
    sp_per_s: float
    power_deficits: list[bool]
    smelt_deficits: list[bool]
    res_unlocked: bool


@dataclass(frozen=True)
class _JobCandidate:
    score: float
    cost_sp: float
    dur_s: float
    kind: str
    p_idx: int
    key: str
    next_lvl: int


@dataclass
class _SimCaches:
    speedup: float
    b_base_times: Mapping[str, Mapping[int, float]]
    r_base_times: Mapping[str, Mapping[int, float]]
    solar_unit_by_temp: dict[int, float]
    flow_cache: dict[tuple[int, int, int, str, int], list[float]] = field(default_factory=dict)
    econ_cache: dict[tuple[int, int, int, int, int], float] = field(default_factory=dict)
    b_cost_cache: dict[tuple[str, int], float] = field(default_factory=dict)
    r_cost_cache: dict[tuple[str, int], float] = field(default_factory=dict)

    def flows(self, temp_c: int, r1_lvl: int, r2_lvl: int, bk: str, lvl: int) -> list[float]:
        ck = (temp_c, r1_lvl, r2_lvl, bk, lvl)
        hit = self.flow_cache.get(ck)
        if hit is None:
            hit = building_level_flows(
                bk,
                lvl,
                {
                    "speedup_factor": self.speedup,
                    "r1_mining_tech": r1_lvl,
                    "r2_energy_tech": r2_lvl,
                    "planet_temperature_c": temp_c,
                },
            )
            self.flow_cache[ck] = hit
        return hit

    def net_sp_hr(self, temp_c: int, r1_lvl: int, r2_lvl: int, bid: int, lvl: int) -> float:
        ck = (temp_c, r1_lvl, r2_lvl, bid, lvl)
        hit = self.econ_cache.get(ck)
        if hit is None:
            hit = float(
                building_level_economy(
                    bid,
                    lvl,
                    {
                        "speedup_factor": self.speedup,
                        "r1_mining_tech": r1_lvl,
                        "r2_energy_tech": r2_lvl,
                        "planet_temperature_c": temp_c,
                    },
                )["net_spice_eq_hr"]
            )
            self.econ_cache[ck] = hit
        return hit

    def building_cost_sp(self, bk: str, lvl: int) -> float:
        ck = (bk, lvl)
        hit = self.b_cost_cache.get(ck)
        if hit is None:
            hit = max(1.0, spice_eq(full_cost(building_cost_map(int(bk[1:]), lvl))))
            self.b_cost_cache[ck] = hit
        return hit

    def building_dur_s(self, sp: _SimPlanet, bk: str, lvl: int) -> float:
        div = building_time_divisor(int(sp.cur.get("b7", 0)), int(sp.cur.get("b17", 0)), self.speedup)
        return max(1.0, float(self.b_base_times[bk].get(lvl, 1.0)) / div)

    def research_cost_sp(self, rk: str, lvl: int) -> float:
        ck = (rk, lvl)
        hit = self.r_cost_cache.get(ck)
        if hit is None:
            raw_levels = research_info(rk).get("levels")
            row = raw_levels.get(lvl) if isinstance(raw_levels, Mapping) else None
            raw_cost = row.get("cost") if isinstance(row, Mapping) else None
            hit = max(1.0, spice_eq(full_cost(raw_cost)))
            self.r_cost_cache[ck] = hit
        return hit

    def research_dur_s(self, sim_planets: Sequence[_SimPlanet], rk: str, lvl: int) -> float:
        best_b8 = max((int(sp.cur.get("b8", 0)) for sp in sim_planets), default=0)
        div = research_time_divisor(best_b8, self.speedup)
        return max(1.0, float(self.r_base_times[rk].get(lvl, 1.0)) / div)


def _build_base_time_tables() -> tuple[dict[str, dict[int, float]], dict[str, dict[int, float]]]:
    b_base_times: dict[str, dict[int, float]] = {}
    for bid_raw in sorted(game_data.BUILDING_BY_ID):
        bid = int(bid_raw)
        levels = building_levels_map(bid)
        raw_info = game_data.BUILDING_BY_ID[bid]
        max_lvl = coerce_int(raw_info.get("max_table_level") if isinstance(raw_info, Mapping) else None, len(levels))
        b_base_times[f"b{bid}"] = build_monotonic_base_times(levels, max_lvl or len(levels))

    r_base_times: dict[str, dict[int, float]] = {}
    for rk, info in research_catalog():
        raw_lvls = info.get("levels")
        levels_r: dict[int, dict[str, object]] = (
            {int(k): dict(v) for k, v in raw_lvls.items() if isinstance(v, Mapping)}
            if isinstance(raw_lvls, Mapping)
            else {}
        )
        max_lvl_r = coerce_int(info.get("max_table_level"), len(levels_r)) or len(levels_r)
        r_base_times[rk] = build_monotonic_base_times(levels_r, max_lvl_r)
    return b_base_times, r_base_times


def _build_caches(cfg: _ProjectionConfig, sim_planets: Sequence[_SimPlanet]) -> _SimCaches:
    solar_unit_by_temp = {sp.temperature_c: solar_cell_energy_per_unit(float(sp.temperature_c)) for sp in sim_planets}
    return _SimCaches(
        speedup=cfg.speedup,
        b_base_times=cfg.b_base_times,
        r_base_times=cfg.r_base_times,
        solar_unit_by_temp=solar_unit_by_temp,
    )


def _build_projection_config(
    result: Mapping[str, object],
    state: StateInput | None,
    horizon_years: int,
) -> _ProjectionConfig:
    """Resolve ship specs, speedup, prerequisites, and monotonic base-time tables."""
    years_n = max(1, min(15, coerce_int(horizon_years, 5) or 5))
    summary = as_mapping(result.get("summary"))
    meta_params = as_mapping(result.get("meta_parameters"))
    inputs = as_mapping(result.get("inputs"))

    ship_key = str(summary.get("target_ship_key") or meta_params.get("target_ship_key") or "s16")
    if ship_key not in SHIP_SLUG_BY_KEY:
        ship_key = "s16"
    s_info = ship_info(ship_key)
    ship_base_cost = full_cost(s_info.get("base_cost"))
    cost_resources = tuple(r for r in RESOURCE_KEYS if float(ship_base_cost[r]) > 0.0)
    ship_r_reqs = research_requirements([ship_key])

    fallback_speedup = 1.0
    if state is not None and isinstance(state, _HasSpeedupMultiplier):
        raw_mult = state.speedup_multiplier
        if raw_mult is not None:
            fallback_speedup = coerce_finite_float(raw_mult, 1.0) or 1.0
    speedup = max(0.1, coerce_finite_float(inputs.get("universe_speedup_factor"), fallback_speedup))

    all_bkeys = tuple(f"b{int(bid)}" for bid in sorted(game_data.BUILDING_BY_ID))
    b_base_times, r_base_times = _build_base_time_tables()
    hours_per_month = max(1.0, coerce_finite_float(summary.get("hours_per_month"), 720.0))
    summary_ships_mo = max(0.0, coerce_finite_float(summary.get("ships_per_month"), 0.0))
    req_b8_for_ship = max(
        [1, *(int(pr_l) for srk in ship_r_reqs for pr_k, pr_l in TECH_GRAPH.prereqs_of.get(srk, ()) if pr_k == "b8")]
    )
    return _ProjectionConfig(
        years_n=years_n,
        total_weeks=52 * years_n,
        seconds_per_week=168.0 * 3600.0,
        ship_key=ship_key,
        ship_name=str(s_info.get("name") or ship_key),
        cost_resources=cost_resources,
        ship_cost_map={r: float(ship_base_cost[r]) for r in cost_resources},
        base_ship_time_s=max(1.0, coerce_finite_float(s_info.get("base_build_time_s"), 1.0) or 1.0),
        ship_b_reqs=same_planet_building_requirements(ship_key),
        ship_r_reqs=ship_r_reqs,
        req_b8_for_ship=req_b8_for_ship,
        speedup=speedup,
        summary_ships_mo=summary_ships_mo,
        target_ships_wk=summary_ships_mo * (168.0 / hours_per_month),
        all_bkeys=all_bkeys,
        b_base_times=b_base_times,
        r_base_times=r_base_times,
        b_prereqs_map={
            bk: {rk_b: rl_b for rk_b, rl_b in same_planet_building_requirements(bk).items() if rk_b != bk}
            for bk in all_bkeys
        },
        b_r_prereqs_map={bk: research_requirements([bk]) for bk in all_bkeys},
    )
