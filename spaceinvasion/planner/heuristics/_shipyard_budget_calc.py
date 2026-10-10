"""Rolling spare-% and infrastructure-transport budget calculations for hybrid Fleet Planner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, PlanetState, Resources
from spaceinvasion.planner.eta import estimate_hourly_economy
from spaceinvasion.planner.heuristics._cargo_helpers import _adaptive_enabled_cfg, _in_flight_ship_counts
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._types import _BUDGET_RES_KEYS, INFRA_TRANSPORT_INCOME_SHARE


def _normalize_spare_percent(spare_percent: Mapping[str, float] | float | None) -> dict[str, float]:
    if isinstance(spare_percent, (int, float)) and not isinstance(spare_percent, bool):
        p_val = max(0.0, min(100.0, _safe_strategy_float(spare_percent, 10.0)))
        return {rk: p_val for rk in _BUDGET_RES_KEYS}
    if isinstance(spare_percent, Mapping):
        return {
            rk: max(0.0, min(100.0, _safe_strategy_float(spare_percent.get(rk, 10.0), 10.0))) for rk in _BUDGET_RES_KEYS
        }
    return {rk: 10.0 for rk in _BUDGET_RES_KEYS}


def _collect_empire_stock_and_urgent_hulls(
    state: GameState,
    non_raid_planets: Sequence[PlanetState],
    *,
    adaptive: bool,
) -> tuple[dict[str, float], int, int]:
    empire_stock: dict[str, float] = {rk: 0.0 for rk in _BUDGET_RES_KEYS}
    total_s10 = 0
    total_transports = 0
    for p in non_raid_planets:
        res_obj = getattr(p, "resources", None)
        if res_obj is not None:
            empire_stock["pig_iron"] += float(max(0, _safe_strategy_int(getattr(res_obj, "pig_iron", 0), 0)))
            empire_stock["metal"] += float(max(0, _safe_strategy_int(getattr(res_obj, "metal", 0), 0)))
            empire_stock["kryptonite"] += float(max(0, _safe_strategy_int(getattr(res_obj, "kryptonite", 0), 0)))
            empire_stock["spice"] += float(max(0, _safe_strategy_int(getattr(res_obj, "spice", 0), 0)))
        ships_m = getattr(p, "ships", None) or {}
        if isinstance(ships_m, Mapping):
            total_s10 += max(0, _safe_strategy_int(ships_m.get("s10", 0), 0))
            total_transports += max(0, _safe_strategy_int(ships_m.get("s1", 0), 0))
            total_transports += max(0, _safe_strategy_int(ships_m.get("s2", 0), 0))
    if adaptive:
        emp_raw = _in_flight_ship_counts(state).get("empire_in_flight")
        emp_flying: Mapping[str, object] = emp_raw if isinstance(emp_raw, Mapping) else {}
        total_s10 += max(0, _safe_strategy_int(emp_flying.get("s10", 0), 0))
        total_transports += max(0, _safe_strategy_int(emp_flying.get("s1", 0), 0))
        total_transports += max(0, _safe_strategy_int(emp_flying.get("s2", 0), 0))
    return empire_stock, total_s10, total_transports


def _extract_attack_hourly_loot(attack_telemetry: Mapping[str, object] | None) -> dict[str, float]:
    attack_hourly: dict[str, float] = {rk: 0.0 for rk in _BUDGET_RES_KEYS}
    if not isinstance(attack_telemetry, Mapping):
        return attack_hourly
    hl = attack_telemetry.get("hourly_loot")
    if isinstance(hl, Mapping):
        for rk in _BUDGET_RES_KEYS:
            attack_hourly[rk] = max(0.0, _safe_strategy_float(hl.get(rk, 0.0), 0.0))
        return attack_hourly
    ml = attack_telemetry.get("median_loot")
    aph = max(0.0, _safe_strategy_float(attack_telemetry.get("estimated_attacks_per_hour", 0.0), 0.0))
    if isinstance(ml, Mapping) and aph > 0.0:
        for rk in _BUDGET_RES_KEYS:
            attack_hourly[rk] = max(0.0, _safe_strategy_float(ml.get(rk, 0.0), 0.0)) * aph
    return attack_hourly


def _extract_reserved_cost(reserved_cost: Resources | Mapping[str, object] | None) -> dict[str, float]:
    reserved: dict[str, float] = {rk: 0.0 for rk in _BUDGET_RES_KEYS}
    if isinstance(reserved_cost, Resources):
        reserved["pig_iron"] = float(max(0, _safe_strategy_int(reserved_cost.pig_iron, 0)))
        reserved["metal"] = float(max(0, _safe_strategy_int(reserved_cost.metal, 0)))
        reserved["kryptonite"] = float(max(0, _safe_strategy_int(reserved_cost.kryptonite, 0)))
        reserved["spice"] = float(max(0, _safe_strategy_int(reserved_cost.spice, 0)))
    elif isinstance(reserved_cost, Mapping):
        for rk in _BUDGET_RES_KEYS:
            reserved[rk] = max(0.0, _safe_strategy_float(reserved_cost.get(rk, 0.0), 0.0))
    return reserved


def _compute_allowances_and_infra_budget(
    *,
    norm_pct: Mapping[str, float],
    empire_stock: Mapping[str, float],
    reserved: Mapping[str, float],
    mine_hourly: Mapping[str, float],
    attack_hourly: Mapping[str, float],
    eff_window_h: float,
    adaptive: bool,
    infra_spend_last_hour: Mapping[str, object] | None,
    infra_carryover_hours: float,
    spare_spend_in_window: Mapping[str, object] | None,
    spare_window_hours: float,
) -> tuple[
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
    bool,
    float,
]:
    hourly_income: dict[str, float] = {}
    hourly_spare_allowance: dict[str, float] = {}
    free_after_reserve: dict[str, float] = {}
    available_budget: dict[str, float] = {}
    spare_spent: dict[str, float] = {rk: 0.0 for rk in _BUDGET_RES_KEYS}
    track_spare_spend = adaptive and isinstance(spare_spend_in_window, Mapping)
    spare_window_h = eff_window_h
    if track_spare_spend and isinstance(spare_spend_in_window, Mapping):
        spare_window_h = max(eff_window_h, min(24.0, max(1.0, _safe_strategy_float(spare_window_hours, 1.0))))
        for rk in _BUDGET_RES_KEYS:
            spare_spent[rk] = max(0.0, _safe_strategy_float(spare_spend_in_window.get(rk, 0.0), 0.0))
    for rk in _BUDGET_RES_KEYS:
        inc = mine_hourly[rk] + attack_hourly[rk]
        hourly_income[rk] = round(inc, 2)
        sp_hr = inc * (norm_pct[rk] / 100.0)
        hourly_spare_allowance[rk] = round(sp_hr, 2)
        free_r = max(0.0, empire_stock[rk] - reserved[rk])
        free_after_reserve[rk] = round(free_r, 2)
        if norm_pct[rk] <= 0.0:
            avail = 0.0
        elif inc > 0.0:
            avail = min(free_r, max(0.0, sp_hr * spare_window_h - spare_spent[rk]))
        else:
            avail = free_r * (norm_pct[rk] / 100.0)
        available_budget[rk] = round(max(0.0, avail), 2)

    infra_transport_budget: dict[str, float] = {}
    eff_infra_window_h = max(eff_window_h, min(2.0, max(1.0, _safe_strategy_float(infra_carryover_hours, 1.0))))
    for rk in ("metal", "kryptonite"):
        inc_r = mine_hourly[rk] + attack_hourly[rk]
        spent_r = max(0.0, _safe_strategy_float((infra_spend_last_hour or {}).get(rk, 0.0), 0.0))
        if inc_r > 0.0:
            hourly_cap_r = inc_r * INFRA_TRANSPORT_INCOME_SHARE * eff_infra_window_h
            infra_v = min(free_after_reserve[rk], max(0.0, hourly_cap_r - spent_r))
        else:
            infra_v = 0.0
        infra_transport_budget[rk] = round(max(0.0, infra_v), 2)

    return (
        hourly_income,
        hourly_spare_allowance,
        free_after_reserve,
        available_budget,
        infra_transport_budget,
        spare_spent,
        track_spare_spend,
        spare_window_h,
    )


def compute_spare_ship_budget(
    state: GameState,
    spare_percent: Mapping[str, float] | float | None = None,
    *,
    attack_telemetry: Mapping[str, object] | None = None,
    reserved_cost: Resources | Mapping[str, object] | None = None,
    window_hours: float = 1.0,
    adaptive_execution: bool = False,
    config: StrategyConfig | None = None,
    infra_spend_last_hour: Mapping[str, object] | None = None,
    infra_carryover_hours: float = 1.0,
    spare_spend_in_window: Mapping[str, object] | None = None,
    spare_window_hours: float = 1.0,
) -> dict[str, object]:
    """Compute Soft Reserve + Rolling `spare %` Shipyard resource budget for hybrid AI-Planner + Fleet mode."""
    adaptive = bool(adaptive_execution) or _adaptive_enabled_cfg(config)
    norm_pct = _normalize_spare_percent(spare_percent)
    eff_window_h = max(0.1, min(168.0, _safe_strategy_float(window_hours, 1.0)))
    non_raid_planets = [p for p in (getattr(state, "planets", ()) or ()) if _norm_planet_type(p) != "raid"]
    empire_stock, total_s10, total_transports = _collect_empire_stock_and_urgent_hulls(
        state, non_raid_planets, adaptive=adaptive
    )
    econ = estimate_hourly_economy(state) if non_raid_planets else {}
    mine_hourly: dict[str, float] = {
        "pig_iron": max(0.0, _safe_strategy_float(econ.get("pig_iron_per_hr", 0.0), 0.0)),
        "metal": max(0.0, _safe_strategy_float(econ.get("metal_per_hr", 0.0), 0.0)),
        "kryptonite": max(0.0, _safe_strategy_float(econ.get("kryptonite_per_hr", 0.0), 0.0)),
        "spice": max(0.0, _safe_strategy_float(econ.get("spice_per_hr", 0.0), 0.0)),
    }
    attack_hourly = _extract_attack_hourly_loot(attack_telemetry)
    reserved = _extract_reserved_cost(reserved_cost)
    (
        hourly_income,
        hourly_spare_allowance,
        free_after_reserve,
        available_budget,
        infra_transport_budget,
        spare_spent,
        track_spare_spend,
        spare_window_h,
    ) = _compute_allowances_and_infra_budget(
        norm_pct=norm_pct,
        empire_stock=empire_stock,
        reserved=reserved,
        mine_hourly=mine_hourly,
        attack_hourly=attack_hourly,
        eff_window_h=eff_window_h,
        adaptive=adaptive,
        infra_spend_last_hour=infra_spend_last_hour,
        infra_carryover_hours=infra_carryover_hours,
        spare_spend_in_window=spare_spend_in_window,
        spare_window_hours=spare_window_hours,
    )

    urgent_replacements = {
        "s10": total_s10 == 0,
        "s1": total_transports == 0,
        "s2": total_transports == 0,
    }
    urgent_zero_count_ships = [k for k in ("s10", "s1", "s2") if urgent_replacements[k]]
    extra_out: dict[str, object] = {}
    if track_spare_spend:
        extra_out["spare_spend_in_window"] = {rk: round(spare_spent[rk], 2) for rk in _BUDGET_RES_KEYS}
        extra_out["spare_window_hours"] = round(spare_window_h, 2)
    if isinstance(attack_telemetry, Mapping) and attack_telemetry.get("income_basis"):
        extra_out["income_basis"] = _safe_strategy_str(attack_telemetry.get("income_basis"))

    return {
        **extra_out,
        "spare_percent": {rk: round(norm_pct[rk], 2) for rk in _BUDGET_RES_KEYS},
        "window_hours": round(eff_window_h, 2),
        "empire_stock": {rk: int(round(empire_stock[rk])) for rk in _BUDGET_RES_KEYS},
        "reserved_cost": {rk: int(round(reserved[rk])) for rk in _BUDGET_RES_KEYS},
        "reserved_cost_spice_eq": round(catalog.spice_equivalent(reserved), 2),
        "free_after_reserve": free_after_reserve,
        "mine_hourly": {rk: round(mine_hourly[rk], 2) for rk in _BUDGET_RES_KEYS},
        "attack_hourly": {rk: round(attack_hourly[rk], 2) for rk in _BUDGET_RES_KEYS},
        "hourly_income": hourly_income,
        "hourly_income_spice_eq": round(catalog.spice_equivalent(hourly_income), 2),
        "hourly_spare_allowance": hourly_spare_allowance,
        "hourly_spare_spice_eq": round(catalog.spice_equivalent(hourly_spare_allowance), 2),
        "available_budget": available_budget,
        "available_budget_spice_eq": round(catalog.spice_equivalent(available_budget), 2),
        "urgent_replacements": urgent_replacements,
        "urgent_zero_count_ships": urgent_zero_count_ships,
        "infra_transport_budget": infra_transport_budget,
    }
