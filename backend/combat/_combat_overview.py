"""Pure SpaceInvasion Classic combat readiness overview helper (zero DB imports)."""

from __future__ import annotations

from collections.abc import Mapping

from backend.combat._points_helpers import (
    cap_json_int_eta,
    res_dict,
    safe_big_float,
    safe_eta_str,
    safe_nonneg_int,
)
from backend.combat.cargo import plan_escape_flight
from backend.core.catalog import (
    DEFENSE_SLUG_BY_KEY,
    DEFENSES,
    SHIP_SLUG_BY_KEY,
    SHIPS,
    safe_finite_float,
    safe_int,
    safe_key,
)
from backend.core.model import GameState, PlanetState


def _build_unit_catalog(
    slug_map: Mapping[str, str],
    catalog: Mapping[str, Mapping[str, object]],
    owned_map: Mapping[str, int],
    *,
    atk_mult: float,
    shd_mult: float,
    def_mult: float,
    include_mobility: bool,
) -> tuple[list[dict[str, object]], float, int]:
    rows: list[dict[str, object]] = []
    total_power = 0.0
    total_cargo = 0
    for ukey in sorted(slug_map.keys(), key=lambda k: int(k[1:]) if (k[1:].isascii() and k[1:].isdigit()) else 99):
        slug = slug_map[ukey]
        unit = catalog.get(slug) or {}
        raw_stats = unit.get("stats")
        stats: Mapping[str, object] = raw_stats if isinstance(raw_stats, Mapping) else {}
        owned = safe_nonneg_int(owned_map.get(ukey, 0), 0)
        owned_f = safe_big_float(owned, cap=1e100)
        atk_base = safe_int(stats.get("attack", 0), 0)
        shd_base = safe_int(stats.get("shield", 0), 0)
        hul_base = safe_int(stats.get("hull", 0), 0)
        total_power += (atk_base * atk_mult + shd_base * shd_mult + hul_base * def_mult * 0.25) * owned_f
        row: dict[str, object] = {
            "key": ukey,
            "unit_id": int(ukey[1:]) if (ukey[1:].isascii() and ukey[1:].isdigit()) else 0,
            "slug": slug,
            "name": str(unit.get("name", slug)),
            "owned": cap_json_int_eta(owned),
            "attack": atk_base,
            "shield": shd_base,
            "hull": hul_base,
            "base_cost": unit.get("base_cost") or {},
        }
        if include_mobility:
            cargo = safe_int(stats.get("cargo", 0), 0)
            total_cargo += cargo * owned
            row["cargo"] = cargo
            row["speed"] = safe_int(stats.get("speed", 0), 0)
        rows.append(row)
    return rows, total_power, total_cargo


def _build_escape_plan_dict(state: GameState, target: PlanetState) -> dict[str, object]:
    other_coords = next((p.coords for p in state.planets if p.planet_id != target.planet_id), target.coords)
    esc = plan_escape_flight(target, target_coords=other_coords)
    esc_cargo = res_dict(esc.cargo)
    return {
        "feasible": esc.feasible,
        "target_coords": safe_eta_str(esc.target_coords),
        "ships": {safe_key(k): cap_json_int_eta(safe_nonneg_int(v, 0)) for k, v in (esc.ships or {}).items()},
        "cargo": esc_cargo,
        "total_cargo": cap_json_int_eta(sum(esc_cargo.values())),
        "total_capacity": cap_json_int_eta(safe_nonneg_int(esc.total_capacity, 0)),
        "fuel_reserved_spice": cap_json_int_eta(safe_nonneg_int(esc.fuel_reserved_spice, 0)),
        "duration_hint": safe_eta_str(esc.duration_hint),
        "spice_saved": esc_cargo.get("spice", 0),
        "reason": safe_eta_str(esc.reason),
    }


def build_combat_overview_payload(
    state: GameState,
    planet: PlanetState | None,
    *,
    baseline_pts: float,
    attack_zone: Mapping[str, object],
    newbie_protection_points: int,
    safety_margin: float = 1.4,
) -> dict[str, object]:
    """Assemble combat readiness, AZ bounds, stationed ships/defense, and Escape flight data."""
    target = planet or state.active_planet()
    r_map = state.researches if isinstance(state.researches, Mapping) else {}
    techs = {
        "weapons": cap_json_int_eta(safe_nonneg_int(r_map.get("r8", 0), 0)),
        "shield": cap_json_int_eta(safe_nonneg_int(r_map.get("r9", 0), 0)),
        "defense": cap_json_int_eta(safe_nonneg_int(r_map.get("r10", 0), 0)),
        "spy": cap_json_int_eta(safe_nonneg_int(r_map.get("r7", 0), 0)),
        "targeting": cap_json_int_eta(safe_nonneg_int(r_map.get("r11", 0), 0)),
        "particle": cap_json_int_eta(safe_nonneg_int(r_map.get("r12", 0), 0)),
        "rocket": cap_json_int_eta(safe_nonneg_int(r_map.get("r4", 0), 0)),
        "diffusion": cap_json_int_eta(safe_nonneg_int(r_map.get("r5", 0), 0)),
        "warp": cap_json_int_eta(safe_nonneg_int(r_map.get("r6", 0), 0)),
    }
    atk_mult = 1.0 + 0.10 * min(100_000, techs["weapons"])
    shd_mult = 1.0 + 0.10 * min(100_000, techs["shield"])
    def_mult = 1.0 + 0.10 * min(100_000, techs["defense"])

    ship_catalog, total_fleet_power, total_cargo = _build_unit_catalog(
        SHIP_SLUG_BY_KEY,
        SHIPS,
        target.ships if target else {},
        atk_mult=atk_mult,
        shd_mult=shd_mult,
        def_mult=def_mult,
        include_mobility=True,
    )
    defense_catalog, total_defense_power, _ = _build_unit_catalog(
        DEFENSE_SLUG_BY_KEY,
        DEFENSES,
        target.defense if target else {},
        atk_mult=atk_mult,
        shd_mult=shd_mult,
        def_mult=def_mult,
        include_mobility=False,
    )
    ship_offers = [
        {
            "unit_type": safe_eta_str(o.unit_type),
            "unit_id": cap_json_int_eta(safe_nonneg_int(o.unit_id, 0)),
            "name": safe_eta_str(o.name),
            "owned": cap_json_int_eta(safe_nonneg_int(o.owned, 0)),
            "cost": res_dict(o.cost),
            "can_build": o.can_build,
            "status_text": safe_eta_str(o.status_text),
        }
        for o in (target.ship_offers or () if target is not None else ())
    ]
    escape_plan_dict = _build_escape_plan_dict(state, target) if target is not None else None
    raw_margin = safe_finite_float(safety_margin, 1.4)
    eff_margin = raw_margin if raw_margin >= 0.0 else 1.4

    return {
        "planet_id": cap_json_int_eta(safe_int(target.planet_id, 0)) if target else None,
        "planet_coords": safe_eta_str(target.coords) if target else None,
        "weapons_factory_level": cap_json_int_eta(target.building_level(9)) if target else 0,
        "fleet_base_level": cap_json_int_eta(target.building_level(10)) if target else 0,
        "galaxy_scanner_level": cap_json_int_eta(target.building_level(19)) if target else 0,
        "combat_techs": techs,
        "attack_zone": dict(attack_zone),
        "newbie_protected": baseline_pts < newbie_protection_points,
        "safety_margin": eff_margin,
        "bash_rule": "Max 3 attacks / 24h on active planets (exempt: yellow inactives & Spacia)",
        "az_rule": "Eligible targets within AZ ±2 (Top 50 bypass AZ outside Newbie Shield)",
        "combat_model": "1:1 zero-rapidfire combat; attacker retreats unless ratio >= safety margin",
        "stationed_fleet_power": round(total_fleet_power, 1),
        "stationed_defense_power": round(total_defense_power, 1),
        "total_cargo_capacity": cap_json_int_eta(total_cargo),
        "ships": ship_catalog,
        "defenses": defense_catalog,
        "ship_offers": ship_offers,
        "escape_plan": escape_plan_dict,
    }
