"""Helper protocols, evaluation math, and setup routines for manual planet overrides."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Protocol, runtime_checkable

import numpy as np

from spaceinvasion.core import game_data
from spaceinvasion.core.coercion import coerce_finite_float, coerce_int, coerce_optional_int
from spaceinvasion.core.model import normalize_planet_type
from spaceinvasion.planner.config_optimizer._model_plans import build_planet_plans, plan_research_levels
from spaceinvasion.planner.config_optimizer._params_norm import _json_ready, _normalize_params
from spaceinvasion.planner.config_optimizer._protocols import (
    GameStateLike,
    PlanetLike,
    as_int_dict,
    as_mapping,
    as_str_dict,
    map_float,
    map_int,
)
from spaceinvasion.planner.config_optimizer.catalog_math import (
    building_level_flows,
    research_catalog,
    shipyard_time_divisor,
    solar_cell_energy_per_unit,
    technology_multiplier,
)
from spaceinvasion.planner.config_optimizer.constants import (
    PRODUCTION_FLOW_KEYS,
    RESOURCE_KEYS,
    FloatArray,
)
from spaceinvasion.planner.config_optimizer.params import (
    _empty_colony_slot,
    check_optimizer_settings,
)
from spaceinvasion.planner.config_optimizer.tech_graph import (
    research_requirements,
    research_upgrade_costs,
)


@runtime_checkable
class _HasResearches(Protocol):
    @property
    def researches(self) -> Mapping[str, int]: ...


@runtime_checkable
class _HasResearch(Protocol):
    @property
    def research(self) -> Mapping[str, int]: ...


@runtime_checkable
class OptimizerStateLike(Protocol):
    """Read-only state contract satisfied by ``core.model.GameState`` and ``core.state.GameState``."""

    @property
    def planets(self) -> Sequence[PlanetLike]: ...

    @property
    def researches(self) -> Mapping[str, int]: ...

    @property
    def speedup_factor(self) -> float | None: ...


StateInput = OptimizerStateLike | GameStateLike


@dataclass(frozen=True)
class PlanetEvalResult:
    """Typed steady-state evaluation for a single planet building map."""

    energy_produced: float
    energy_used: float
    power_ratio: float
    smelting_ratio: float
    net_per_hour: FloatArray
    shipyard_ships: float
    planet_limits: FloatArray


@dataclass(frozen=True)
class _OverrideSetup:
    """Normalized inputs, research plan, and planet plans for a manual override run."""

    cfg: dict[str, object]
    owned_count: int
    own_researches: dict[str, int]
    uni_max: dict[str, int]
    planned_research_rows: list[dict[str, object]]
    planned_research_levels: dict[str, int]
    speedup_factor: float
    plan_account_context: dict[str, object]
    planet_plans: list[dict[str, object]]


def state_researches(state: StateInput) -> Mapping[str, int]:
    """Read the research-level mapping from either ``.researches`` or ``.research``."""
    if isinstance(state, _HasResearches):
        return state.researches or {}
    if isinstance(state, _HasResearch):
        return state.research or {}
    return {}


def resolve_state_speedup(state: StateInput, params: Mapping[str, object]) -> float:
    """Return a finite positive universe speedup factor from ``state`` or ``params``."""
    raw_state_speedup: object = state.speedup_factor
    for candidate in (raw_state_speedup, params.get("speedup_factor")):
        if candidate is None or isinstance(candidate, bool):
            continue
        val = coerce_finite_float(candidate, 0.0)
        if math.isfinite(val) and val > 0.0:
            return val
    return 1.0


def json_ready_dict(payload: Mapping[str, object]) -> dict[str, object]:
    """Run ``_json_ready`` and narrow the root mapping back to ``dict[str, object]``."""
    ready = _json_ready(payload)
    if isinstance(ready, Mapping):
        return {str(k): v for k, v in ready.items()}
    return {}


def _extract_planned_building_map_from_entry(
    entry: Mapping[str, object] | None,
    fallback_levels: Mapping[str, int],
) -> dict[str, int]:
    """Extract ``{b1..b21: target_level}`` from a Config Optimizer planet record."""
    out = {bk: max(0, int(lvl)) for bk, lvl in fallback_levels.items()}
    if not isinstance(entry, Mapping):
        return out
    raw_blds = entry.get("buildings")
    if isinstance(raw_blds, (list, tuple)):
        seen: set[str] = set()
        for row in raw_blds:
            if not isinstance(row, Mapping):
                continue
            bk = str(row.get("key") or row.get("building_key") or "").strip().lower()
            if bk in out:
                out[bk] = max(0, coerce_int(row.get("target_level", row.get("level", 0)), 0))
                seen.add(bk)
        for bk in list(out):
            if bk not in seen and int(fallback_levels.get(bk, 0)) == 0:
                out[bk] = 0
    elif isinstance(raw_blds, Mapping):
        for bk_raw, raw_v in raw_blds.items():
            k = str(bk_raw).strip().lower()
            if k not in out:
                continue
            val = raw_v.get("target_level", raw_v.get("level", 0)) if isinstance(raw_v, Mapping) else raw_v
            out[k] = max(0, coerce_int(val, 0))
    return out


def _evaluate_planet_typed(
    planned_map: Mapping[str, int],
    plan: Mapping[str, object],
    context: Mapping[str, object],
    *,
    target_requirements: Mapping[str, int],
    cost_resources: tuple[str, ...],
    ship_cost: FloatArray,
    hours_per_month: float,
    base_ship_time_s: float,
) -> PlanetEvalResult:
    """Evaluate exact steady-state production, energy, smelting, and shipyard limits for one planet."""
    totals: FloatArray = np.zeros(len(PRODUCTION_FLOW_KEYS), dtype=np.float64)
    for bid_raw in sorted(game_data.BUILDING_BY_ID):
        bk = f"b{int(bid_raw)}"
        lvl = max(0, coerce_int(planned_map.get(bk, 0), 0))
        if lvl > 0:
            totals += np.asarray(building_level_flows(bk, lvl, context), dtype=np.float64)
    totals[PRODUCTION_FLOW_KEYS.index("energy_produced")] += (
        map_float(plan, "solar_cells", 0.0)
        * solar_cell_energy_per_unit(map_float(context, "planet_temperature_c", 0.0))
        * technology_multiplier("r2", map_int(context, "r2_energy_tech", 0))
    )
    flows = dict(zip(PRODUCTION_FLOW_KEYS, totals.tolist(), strict=True))
    e_prod, e_used = float(flows["energy_produced"]), float(flows["energy_used"])
    power_ratio = min(1.0, e_prod / e_used) if e_used > 0.0 else 1.0
    pig_mined = float(flows["pig_iron"]) * power_ratio
    pig_wanted = float(flows["pig_iron_used"]) * power_ratio
    smelting_ratio = min(1.0, pig_mined / pig_wanted) if pig_wanted > 0.0 else 1.0

    net = {
        "pig_iron": pig_mined - pig_wanted * smelting_ratio,
        "metal": float(flows["metal"]) * power_ratio * smelting_ratio,
        "kryptonite": float(flows["kryptonite"]) * power_ratio,
        "spice": float(flows["spice"]) * power_ratio - float(flows["spice_used"]),
    }
    net_vec: FloatArray = np.array([net[r] for r in RESOURCE_KEYS], dtype=np.float64)
    can_build = all(
        coerce_int(planned_map.get(req_k, 0), 0) >= int(req_lvl) for req_k, req_lvl in target_requirements.items()
    )
    if can_build and base_ship_time_s > 0.0:
        div = shipyard_time_divisor(
            coerce_int(planned_map.get("b9", 0), 0),
            coerce_int(planned_map.get("b17", 0), 0),
            map_float(context, "speedup_factor", 1.0),
        )
        shipyard_ships = 3600.0 * float(hours_per_month) * div / float(base_ship_time_s)
    else:
        shipyard_ships = 0.0

    limits_vec: FloatArray = np.array(
        [
            *(
                net[r] * float(hours_per_month) / float(c)
                for r, c in zip(cost_resources, ship_cost.tolist(), strict=True)
            ),
            shipyard_ships,
        ],
        dtype=np.float64,
    )
    return PlanetEvalResult(e_prod, e_used, power_ratio, smelting_ratio, net_vec, shipyard_ships, limits_vec)


def _evaluate_planet_map(
    planned_map: Mapping[str, int],
    plan: Mapping[str, object],
    context: Mapping[str, object],
    *,
    target_ship_key: str,
    target_requirements: Mapping[str, int],
    cost_resources: tuple[str, ...],
    ship_cost: FloatArray,
    hours_per_month: float,
    base_ship_time_s: float,
) -> dict[str, object]:
    """Evaluate exact steady-state production, energy, smelting, and shipyard limits as a dict."""
    _ = target_ship_key
    ev = _evaluate_planet_typed(
        planned_map,
        plan,
        context,
        target_requirements=target_requirements,
        cost_resources=cost_resources,
        ship_cost=ship_cost,
        hours_per_month=hours_per_month,
        base_ship_time_s=base_ship_time_s,
    )
    return {
        "energy_produced": ev.energy_produced,
        "energy_used": ev.energy_used,
        "power_ratio": ev.power_ratio,
        "smelting_ratio": ev.smelting_ratio,
        "net_per_hour": ev.net_per_hour,
        "shipyard_ships": ev.shipyard_ships,
        "planet_limits": ev.planet_limits,
    }


def _nested_int_dict(raw: object) -> dict[str, dict[str, int]]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): as_int_dict(v) for k, v in raw.items() if isinstance(v, Mapping)}


def _merge_and_validate_cfg(
    base_result: Mapping[str, object] | None,
    params: Mapping[str, object] | None,
) -> tuple[dict[str, object], dict[int, dict[str, int]]]:
    """Merge ``base_result['meta_parameters']`` + ``params``, normalize, and validate."""
    merged_raw: dict[str, object] = {}
    if isinstance(base_result, Mapping):
        raw_base = base_result.get("meta_parameters")
        if isinstance(raw_base, Mapping):
            merged_raw.update({str(k): v for k, v in raw_base.items()})
    if isinstance(params, Mapping):
        merged_raw.update({str(k): v for k, v in params.items()})
    cfg = _normalize_params(merged_raw)

    misc_by_slot_int: dict[int, dict[str, int]] = {}
    raw_by_slot = cfg.get("misc_building_levels_by_slot")
    if isinstance(raw_by_slot, Mapping):
        for slot_k, bmap in raw_by_slot.items():
            slot_i = coerce_optional_int(slot_k, None)
            if slot_i is not None and isinstance(bmap, Mapping):
                misc_by_slot_int[slot_i] = as_int_dict(bmap)

    check_optimizer_settings(
        as_int_dict(cfg["optimized_building_max_levels"]),
        as_int_dict(cfg["misc_building_levels"]),
        misc_by_slot_int,
        as_int_dict(cfg["min_fleet_per_planet"]),
        str(cfg["target_ship_key"]),
        map_float(cfg, "warehouse_hours_of_production", 24.0),
        map_int(cfg, "central_planet_min_warehouse_level", 15),
        map_int(cfg, "min_warehouse_level", 0),
    )
    return cfg, misc_by_slot_int


def _build_override_research_plan(
    state: StateInput,
    cfg: Mapping[str, object],
    misc_by_slot_int: Mapping[int, Mapping[str, int]],
    base_result: Mapping[str, object] | None,
    universe_research_max: Mapping[str, int] | None,
) -> tuple[dict[str, int], dict[str, int], list[dict[str, object]], dict[str, int]]:
    state_res = state_researches(state)
    own = {key: max(0, coerce_int(state_res.get(key, 0), 0)) for key, _ in research_catalog()}
    if universe_research_max is not None:
        uni_src: Mapping[str, object] = {str(k): v for k, v in universe_research_max.items()}
    elif isinstance(base_result, Mapping):
        uni_src = as_mapping(as_mapping(base_result.get("inputs")).get("universe_research_max_levels"))
    else:
        uni_src = {}
    uni_max = {key: max(own[key], max(0, coerce_int(uni_src.get(key, 0), 0))) for key in own}
    opt_max = as_int_dict(cfg["optimized_building_max_levels"])
    misc_lvls = as_int_dict(cfg["misc_building_levels"])
    min_fleet = as_int_dict(cfg["min_fleet_per_planet"])
    req_res = research_requirements(
        [
            str(cfg["target_ship_key"]),
            *(ship for ship, count in min_fleet.items() if count > 0),
            *opt_max,
            *(key for key, level in misc_lvls.items() if level > 0),
            *(key for levels in misc_by_slot_int.values() for key, level in levels.items() if level > 0),
        ]
    )
    rows = plan_research_levels(uni_max, own, req_res, map_float(cfg, "research_percent_of_universe_max", 100.0))
    levels = {str(row["key"]): map_int(row, "planned_level", 0) for row in rows}
    return own, uni_max, rows, levels


def _prepare_override_setup(
    state: StateInput,
    *,
    base_result: Mapping[str, object] | None,
    universe_research_max: Mapping[str, int] | None,
    params: Mapping[str, object] | None,
) -> _OverrideSetup:
    """Build normalized settings, research plan, and planet plans for manual override."""
    cfg, misc_by_slot_int = _merge_and_validate_cfg(base_result, params)
    owned_normal: list[PlanetLike] = [p for p in (state.planets or ()) if normalize_planet_type(p) == "normal"]
    roster_planets: list[PlanetLike] = list(owned_normal)
    max_planets = map_int(cfg, "max_planets", 11)
    if bool(cfg["pad_generic_colonies"]) and len(owned_normal) < max_planets:
        empty_temp = map_int(cfg, "empty_colony_temperature_c", 0)
        empty_fields = map_int(cfg, "empty_colony_fields", 300)
        for slot_num in range(1, max_planets - len(owned_normal) + 1):
            roster_planets.append(_empty_colony_slot(slot_num, empty_fields, empty_temp))
    if not roster_planets:
        raise ValueError("No normal planets available in GameState (enable pad_generic_colonies for preview)")

    own, uni_max, planned_rows, planned_lvls = _build_override_research_plan(
        state, cfg, misc_by_slot_int, base_result, universe_research_max
    )
    speedup = resolve_state_speedup(state, cfg)
    account_ctx: dict[str, object] = {
        "speedup_factor": speedup,
        "r1_mining_tech": planned_lvls.get("r1", 0),
        "r2_energy_tech": planned_lvls.get("r2", 0),
    }
    planet_plans = build_planet_plans(
        roster_planets,
        owned_count=len(owned_normal),
        optimized_max_levels=as_int_dict(cfg["optimized_building_max_levels"]),
        misc_levels=as_int_dict(cfg["misc_building_levels"]),
        misc_levels_by_slot=misc_by_slot_int,
        min_fleet=as_int_dict(cfg["min_fleet_per_planet"]),
        central_planet_slot=map_int(cfg, "central_planet_slot", 1),
        central_min_warehouse_level=map_int(cfg, "central_planet_min_warehouse_level", 15),
        research_costs=research_upgrade_costs(own, planned_lvls),
        reserved_floors_by_planet=_nested_int_dict(cfg.get("reserved_floors_by_planet")),
        min_warehouse_level=map_int(cfg, "min_warehouse_level", 0),
        planet_specializations=as_str_dict(cfg.get("planet_specializations")),
        allow_demolition=bool(cfg["allow_demolition"]),
        planet_names=as_str_dict(cfg.get("planet_names")),
    )
    return _OverrideSetup(
        cfg, len(owned_normal), own, uni_max, planned_rows, planned_lvls, speedup, account_ctx, planet_plans
    )
