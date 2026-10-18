"""Pure SpaceInvasion Classic building, research, and action ETA calculations (zero DB imports)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass, replace
import math

from spaceinvasion.combat._points_helpers import (
    cap_eta_float,
    cap_json_int_eta,
    estimate_hourly_economy,
    estimate_planet_hourly_economy,
    res_dict,
    safe_big_mul_div,
    safe_eta_str,
    safe_nonneg_int,
    safe_speedup_factor,
)
from spaceinvasion.combat.points import format_eta
from spaceinvasion.core.catalog import (
    DEFENSE_SLUG_BY_KEY,
    DEFENSES,
    RESEARCH_BY_ID,
    SHIP_SLUG_BY_KEY,
    SHIPS,
    building_level_stats,
    safe_finite_float,
)
from spaceinvasion.core.state import GameState, PlanetState, normalize_planet_type
from spaceinvasion.planner._eta_helpers import (
    ETA_RESOURCE_KEYS,
    INSTANT_ACTION_KINDS,
    action_field,
    compute_resource_eta,
    extract_resource_map,
    format_duration,
    research_level_stats,
    resolve_building_id,
    resolve_planet_for_eta,
    resolve_research_key,
    resolve_unit_slug,
    round_seconds,
    safe_eta_int,
)
from spaceinvasion.planner._eta_tables import build_building_eta_rows, build_research_eta_rows

__all__ = [
    "attach_action_etas",
    "build_buildings_eta",
    "compute_action_eta",
    "estimate_hourly_economy",
    "estimate_planet_hourly_economy",
]


def build_buildings_eta(
    state: GameState,
    planet: PlanetState | None = None,
    *,
    horizon_count: int = 4,
) -> dict[str, object]:
    """Compute next-level costs, missing resources, build times, and reach ETAs for `b1`-`b21` and `r1`-`r15`."""
    target = planet or state.active_planet()
    if target is None:
        return {"planet": None, "planet_economy": {}, "buildings": [], "researches": []}

    safe_horizon = min(100, max(0, safe_nonneg_int(horizon_count, 4)))
    p_econ = estimate_planet_hourly_economy(state, target)
    rates_hr = {
        "pig_iron": safe_finite_float(p_econ["pig_iron_per_hr"], 0.0),
        "metal": safe_finite_float(p_econ["metal_per_hr"], 0.0),
        "kryptonite": safe_finite_float(p_econ["kryptonite_per_hr"], 0.0),
        "spice": safe_finite_float(p_econ["spice_per_hr"], 0.0),
    }
    res_have = res_dict(target.resources)
    speedup = safe_finite_float(p_econ["speedup_factor"], 1.0)
    dev_lvl = max(0, target.building_level(7))
    lab_lvl = max(0, target.building_level(8))
    micro_lvl = max(0, target.building_level(17))
    b_divisor = max(1.0, float(min(dev_lvl, 1_000_000) + 1) * float(2 ** min(micro_lvl, 60)) * speedup)
    r_divisor = max(1.0, float(min(lab_lvl, 1_000_000) + 1) * speedup)

    return {
        "planet": {
            "planet_id": cap_json_int_eta(safe_eta_int(target.planet_id, 0)),
            "name": safe_eta_str(target.name),
            "coords": safe_eta_str(target.coords),
            "planet_type": normalize_planet_type(target),
            "temperature": cap_json_int_eta(safe_eta_int(target.temperature, 0)),
            "fields_used": cap_json_int_eta(safe_nonneg_int(target.fields_used, 0)),
            "fields_max": cap_json_int_eta(safe_nonneg_int(target.fields_max, 0)),
            "resources": res_have,
            "dev_center_level": cap_json_int_eta(dev_lvl),
            "research_lab_level": cap_json_int_eta(lab_lvl),
            "microsystem_level": cap_json_int_eta(micro_lvl),
            "building_time_divisor": round(cap_eta_float(b_divisor), 2),
            "research_time_divisor": round(cap_eta_float(r_divisor), 2),
        },
        "planet_economy": p_econ,
        "buildings": build_building_eta_rows(
            state,
            target,
            p_econ,
            rates_hr,
            res_have,
            dev_lvl=dev_lvl,
            micro_lvl=micro_lvl,
            b_divisor=b_divisor,
            speedup=speedup,
            safe_horizon=safe_horizon,
        ),
        "researches": build_research_eta_rows(
            state,
            target,
            rates_hr,
            res_have,
            r_divisor=r_divisor,
            safe_horizon=safe_horizon,
        ),
    }


def _compute_action_duration_seconds(
    kind: str,
    action: object,
    *,
    dev_lvl: int,
    lab_lvl: int,
    wf_lvl: int,
    micro_lvl: int,
    speedup: float,
) -> float:
    raw_tid = action_field(action, "target_id", None)
    raw_tname = action_field(action, "target_name", None)
    raw_tlvl = action_field(action, "target_level", None)
    micro_mult = float(2 ** min(micro_lvl, 60))

    if kind in ("BUILD", "WAIT_RESOURCES"):
        bid = resolve_building_id(raw_tid, raw_tname)
        tgt_lvl = max(0, safe_eta_int(raw_tlvl, 0))
        if bid is not None and tgt_lvl > 0:
            base_s = safe_finite_float(building_level_stats(bid, tgt_lvl).get("base_time_s", 0.0), 0.0)
            b_div = max(1.0, float(min(dev_lvl, 1_000_000) + 1) * micro_mult * speedup)
            return cap_eta_float(base_s / b_div)
        return 0.0
    if kind == "DEMOLISH_BUILDING":
        bid = resolve_building_id(raw_tid, raw_tname)
        step_lvl = max(1, safe_eta_int(raw_tlvl, 0) + 1)
        if bid is not None and step_lvl > 0:
            base_s = safe_finite_float(building_level_stats(bid, step_lvl).get("base_time_s", 0.0), 0.0)
            b_div = max(1.0, float(min(dev_lvl, 1_000_000) + 1) * micro_mult * speedup)
            return cap_eta_float(base_s / b_div)
        return 0.0
    if kind == "RESEARCH":
        rkey = resolve_research_key(raw_tid, raw_tname)
        tgt_lvl = max(0, safe_eta_int(raw_tlvl, 0))
        if rkey is not None and tgt_lvl > 0:
            stats = research_level_stats(RESEARCH_BY_ID[rkey], tgt_lvl)
            base_s = safe_finite_float(stats.get("base_time_s", 0.0), 0.0)
            r_div = max(1.0, float(min(lab_lvl, 1_000_000) + 1) * speedup)
            return cap_eta_float(base_s / r_div)
        return 0.0
    if kind in ("BUILD_SHIP", "BUILD_DEFENSE", "DEFENSE"):
        is_ship = kind == "BUILD_SHIP"
        slug = resolve_unit_slug(
            "ships" if is_ship else "defense",
            "s" if is_ship else "d",
            SHIP_SLUG_BY_KEY if is_ship else DEFENSE_SLUG_BY_KEY,
            SHIPS if is_ship else DEFENSES,
            raw_tid,
            raw_tname,
        )
        has_tlvl = raw_tlvl is not None and (not isinstance(raw_tlvl, str) or raw_tlvl.strip() != "")
        raw_qty = raw_tlvl if has_tlvl else action_field(action, "quantity", None)
        has_qty = raw_qty is not None and (not isinstance(raw_qty, str) or raw_qty.strip() != "")
        qty = 1 if not has_qty else max(0, safe_eta_int(raw_qty, 0))
        if slug is not None and qty > 0:
            catalog = SHIPS if is_ship else DEFENSES
            base_s = safe_finite_float((catalog.get(slug) or {}).get("base_build_time_s", 0.0), 0.0)
            wf_div = max(1.0, float(min(max(1, wf_lvl), 1_000_000)) * micro_mult * speedup)
            return cap_eta_float(safe_big_mul_div(qty, base_s, wf_div))
    return 0.0


def compute_action_eta(state: GameState, action: object) -> dict[str, object]:
    """Compute resource wait ETA, action duration, and total completion ETA for one planned action."""
    kind = safe_eta_str(action_field(action, "kind", "")).strip().upper()
    if kind in INSTANT_ACTION_KINDS:
        return {
            "wait_hours": 0.0,
            "wait_seconds": 0.0,
            "wait_eta": "Ready",
            "duration_seconds": 0.0,
            "duration_text": "Instant",
            "total_hours": 0.0,
            "total_seconds": 0.0,
            "total_eta": "Ready",
            "eta_bottleneck": None,
        }

    planet = resolve_planet_for_eta(state, action)
    if planet is not None:
        p_econ = estimate_planet_hourly_economy(state, planet)
        rates_hr = {
            "pig_iron": safe_finite_float(p_econ["pig_iron_per_hr"], 0.0),
            "metal": safe_finite_float(p_econ["metal_per_hr"], 0.0),
            "kryptonite": safe_finite_float(p_econ["kryptonite_per_hr"], 0.0),
            "spice": safe_finite_float(p_econ["spice_per_hr"], 0.0),
        }
        speedup = safe_finite_float(p_econ["speedup_factor"], 1.0)
        dev_lvl = max(0, planet.building_level(7))
        lab_lvl = max(0, planet.building_level(8))
        wf_lvl = max(0, planet.building_level(9))
        micro_lvl = max(0, planet.building_level(17))
    else:
        rates_hr = {rk: 0.0 for rk in ETA_RESOURCE_KEYS}
        speedup = safe_speedup_factor(state.speedup_factor)
        dev_lvl = lab_lvl = wf_lvl = micro_lvl = 0

    missing_map = extract_resource_map(action_field(action, "missing", None))
    if missing_map is None:
        cost_map = extract_resource_map(action_field(action, "cost", None))
        if cost_map is not None:
            if planet is not None:
                have = res_dict(planet.resources)
                missing_map = {rk: max(0, cost_map[rk] - int(have.get(rk, 0) or 0)) for rk in ETA_RESOURCE_KEYS}
            else:
                missing_map = dict(cost_map)
        else:
            missing_map = {rk: 0 for rk in ETA_RESOURCE_KEYS}

    wait_h_raw, bottleneck = compute_resource_eta(missing_map, rates_hr)
    dur_s_raw = _compute_action_duration_seconds(
        kind,
        action,
        dev_lvl=dev_lvl,
        lab_lvl=lab_lvl,
        wf_lvl=wf_lvl,
        micro_lvl=micro_lvl,
        speedup=speedup,
    )
    dur_s_raw = 0.0 if (not math.isfinite(dur_s_raw) or dur_s_raw <= 0.0) else cap_eta_float(dur_s_raw)
    duration_seconds = round_seconds(dur_s_raw)
    duration_text = "Instant" if dur_s_raw <= 0.0 else ("1s" if dur_s_raw <= 0.5 else format_duration(dur_s_raw))

    if wait_h_raw is None or not math.isfinite(wait_h_raw):
        return {
            "wait_hours": None,
            "wait_seconds": None,
            "wait_eta": "—",
            "duration_seconds": duration_seconds,
            "duration_text": duration_text,
            "total_hours": None,
            "total_seconds": None,
            "total_eta": "—",
            "eta_bottleneck": bottleneck,
        }

    wait_h = cap_eta_float(float(wait_h_raw))
    wait_s = cap_eta_float(wait_h * 3600.0)
    total_s = cap_eta_float(wait_s + dur_s_raw)
    total_h = cap_eta_float(total_s / 3600.0)
    return {
        "wait_hours": 0.0 if wait_h == 0.0 else round(wait_h, 6),
        "wait_seconds": round_seconds(wait_s),
        "wait_eta": format_eta(wait_h, zero_label="Ready"),
        "duration_seconds": duration_seconds,
        "duration_text": duration_text,
        "total_hours": 0.0 if total_h == 0.0 else round(total_h, 6),
        "total_seconds": round_seconds(total_s),
        "total_eta": format_eta(total_h, zero_label="Ready"),
        "eta_bottleneck": bottleneck,
    }


def attach_action_etas(state: GameState, actions: Sequence[object]) -> list[object]:
    """Attach computed ETA fields onto each `PlannedAction` or action mapping, returning a new list."""
    out: list[object] = []
    for act in actions:
        eta = compute_action_eta(state, act)
        if isinstance(act, Mapping):
            out.append({**act, **eta})
        elif is_dataclass(act) and not isinstance(act, type):
            field_names = {f.name for f in dataclass_fields(act)}
            updates = {k: v for k, v in eta.items() if k in field_names}
            out.append(replace(act, **updates))
        else:
            for k, v in eta.items():
                setattr(act, k, v)
            out.append(act)
    return out
