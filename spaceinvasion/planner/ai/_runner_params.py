"""Parameter normalization and attack-income basis resolution for ``run_ai_planner``."""

from __future__ import annotations

from collections.abc import Mapping
import copy

from spaceinvasion.core.coercion import coerce_finite_float, coerce_int
from spaceinvasion.planner.ai._types import (
    DEFAULT_AI_PLANNER_PARAMS,
    DEFAULT_MEDIAN_ROUND_TRIP_SECONDS,
    GENE_NAMES,
    UPPER_AI_PARAM_ALIASES,
)
from spaceinvasion.planner.ai.telemetry import is_realized_income_basis
from spaceinvasion.planner.ai.tuning import _parse_warm_start_genes
from spaceinvasion.planner.config_optimizer import RESOURCE_KEYS


def _normalize_ai_params(raw_params: Mapping[str, object] | None) -> dict[str, object]:
    """Normalize and clamp AI-Planner parameter overrides against ``DEFAULT_AI_PLANNER_PARAMS``."""
    merged: dict[str, object] = copy.deepcopy(DEFAULT_AI_PLANNER_PARAMS)
    base_spare = merged["resource_spare_percent"]
    spare_dict: dict[str, float] = (
        {r: coerce_finite_float(base_spare.get(r), 10.0) for r in RESOURCE_KEYS}
        if isinstance(base_spare, Mapping)
        else {r: 10.0 for r in RESOURCE_KEYS}
    )
    if isinstance(raw_params, Mapping):
        for raw_key, raw_val in raw_params.items():
            key = UPPER_AI_PARAM_ALIASES.get(str(raw_key), str(raw_key))
            if key == "resource_spare_percent" and isinstance(raw_val, Mapping):
                spare_dict = {r: coerce_finite_float(raw_val.get(r), spare_dict[r]) for r in RESOURCE_KEYS}
                merged[key] = spare_dict
            elif raw_val is None and key in ("fleet_command_center_level", "attacks_per_day"):
                merged[key] = None
            elif raw_val is not None:
                merged[key] = raw_val

    merged["reward_weeks"] = coerce_int(merged["reward_weeks"], 26)
    merged["weekly_discount"] = coerce_finite_float(merged["weekly_discount"], 0.90)
    merged["resource_spare_percent"] = {r: float(spare_dict[r]) for r in RESOURCE_KEYS}
    fc_raw, apd_raw = merged["fleet_command_center_level"], merged["attacks_per_day"]
    merged["fleet_command_center_level"] = None if fc_raw is None else coerce_int(fc_raw, 0)
    merged["attacks_per_day"] = None if apd_raw is None else coerce_finite_float(apd_raw, 0.0)
    merged["scarcity_focus"] = coerce_finite_float(merged["scarcity_focus"], 1.0)
    merged["critical_queue_share"] = coerce_finite_float(merged["critical_queue_share"], 0.9)
    merged["patience"] = coerce_finite_float(merged["patience"], 0.25)
    merged["speed_weight"] = coerce_finite_float(merged["speed_weight"], 1.5)
    merged["revalue_every_steps"] = max(1, coerce_int(merged["revalue_every_steps"], 25))
    merged["search_tolerance"] = max(0.0, coerce_finite_float(merged["search_tolerance"], 0.01))
    pop_size = max(2, coerce_int(merged["ga_population_size"], 10))
    merged["ga_population_size"] = pop_size
    merged["ga_generations"] = max(0, coerce_int(merged["ga_generations"], 6))
    merged["ga_tournament_size"] = max(1, coerce_int(merged["ga_tournament_size"], 3))
    merged["ga_crossover_rate"] = coerce_finite_float(merged["ga_crossover_rate"], 0.9)
    merged["ga_mutation_rate"] = coerce_finite_float(merged["ga_mutation_rate"], 0.3)
    merged["ga_elite_count"] = max(1, min(coerce_int(merged["ga_elite_count"], 2), pop_size - 1))
    merged["ga_random_seed"] = coerce_int(merged["ga_random_seed"], 20261117)
    merged["pad_generic_colonies"] = bool(merged["pad_generic_colonies"])
    merged["transport_dispatch_delay_minutes"] = min(
        1440.0, max(0.0, coerce_finite_float(merged.get("transport_dispatch_delay_minutes"), 120.0))
    )
    if "warm_start_genes" in merged:
        parsed_warm = _parse_warm_start_genes(merged.get("warm_start_genes"))
        if parsed_warm is not None:
            merged["warm_start_genes"] = {
                name: round(float(g), 6) for name, g in zip(GENE_NAMES, parsed_warm, strict=False)
            }
        else:
            merged.pop("warm_start_genes", None)
    return merged


def resolve_attack_income_basis(
    telemetry: Mapping[str, object], cfg: Mapping[str, object]
) -> tuple[dict[str, float], dict[str, float], dict[str, float], float, int, bool, float, bool, str]:
    """Compute fixed vs per-slot attack income dictionaries and pace attribution label."""
    raw_ml = telemetry.get("median_loot")
    raw_median_loot: Mapping[str, object] = raw_ml if isinstance(raw_ml, Mapping) else {}
    median_loot = {r: max(0.0, coerce_finite_float(raw_median_loot.get(r), 0.0)) for r in RESOURCE_KEYS}
    median_rt = coerce_finite_float(
        telemetry.get("median_round_trip_seconds", telemetry.get("median_round_trip_sec")),
        DEFAULT_MEDIAN_ROUND_TRIP_SECONDS,
    )
    if median_rt <= 0.0:
        median_rt = DEFAULT_MEDIAN_ROUND_TRIP_SECONDS
    sample_count = coerce_int(telemetry.get("sample_count", telemetry.get("attacks_in_history")), -1)
    has_active_loot = (sample_count > 0) or (sample_count < 0 and any(v > 0.0 for v in median_loot.values()))
    per_slot_hour = (3600.0 / median_rt) if (has_active_loot and median_rt > 0.0) else 0.0
    raw_realized_slot = telemetry.get("income_per_slot_per_hour")
    apd = cfg.get("attacks_per_day")
    use_realized = apd is None and is_realized_income_basis(telemetry) and isinstance(raw_realized_slot, Mapping)
    fixed_cc = cfg.get("fleet_command_center_level")
    if isinstance(fixed_cc, int) and fixed_cc < 0:
        raise ValueError(f"fleet_command_center_level must be >= 0 or None, got {fixed_cc!r}")

    if use_realized and isinstance(raw_realized_slot, Mapping):
        slot_inc = {r: max(0.0, coerce_finite_float(raw_realized_slot.get(r), 0.0)) for r in RESOURCE_KEYS}
        fixed_inc = {r: 0.0 for r in RESOURCE_KEYS}
        pace_set_by = (
            "realized loot and recycling per Fleet base (b10) slot"
            if fixed_cc is None
            else f"realized loot and recycling per fleet_command_center_level ({fixed_cc}) slot"
        )
    elif apd is None:
        slot_inc = {r: median_loot[r] * per_slot_hour for r in RESOURCE_KEYS}
        fixed_inc = {r: 0.0 for r in RESOURCE_KEYS}
        pace_set_by = (
            "Fleet base (b10) slots x median round-trip"
            if fixed_cc is None
            else f"fleet_command_center_level ({fixed_cc}) x median round-trip"
        )
    else:
        per_day = max(0.0, coerce_finite_float(apd, 0.0))
        slot_inc = {r: 0.0 for r in RESOURCE_KEYS}
        fixed_inc = {r: median_loot[r] * per_day / 24.0 for r in RESOURCE_KEYS}
        pace_set_by = "attacks_per_day"
    return (
        fixed_inc,
        slot_inc,
        median_loot,
        median_rt,
        sample_count,
        has_active_loot,
        per_slot_hour,
        use_realized,
        pace_set_by,
    )
