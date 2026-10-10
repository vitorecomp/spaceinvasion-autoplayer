"""P0 survival, undersized-colony abandon, and P1 negative-energy guards for plan assembly."""

from __future__ import annotations

from collections.abc import Mapping

from spaceinvasion.combat.cargo import plan_escape_flight
from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import (
    BuildingOffer,
    GameState,
    PlanetState,
    is_undersized_fresh_colony,
)
from spaceinvasion.planner.heuristics._actions import _make_build_action, _pick_best_power_building
from spaceinvasion.planner.heuristics._common import (
    _apply_queue_cap_if_full,
    _is_building_allowed_on_planet,
    _planet_has_free_building_fields,
    _resources_from_dict,
    _safe_strategy_int,
    _safe_strategy_str,
    _ship_offer_map,
)
from spaceinvasion.planner.heuristics._queues import _eff_level
from spaceinvasion.planner.heuristics._tutorial_tasks import _tutorial_colonize_task
from spaceinvasion.planner.heuristics._types import SOLAR_CELLS_ID, WEAPONS_FACTORY, PlannedAction


def append_hostile_escape_actions(
    state: GameState,
    active: PlanetState,
    cfg: StrategyConfig,
    actions: list[PlannedAction],
) -> None:
    """Append P0 `ALERT_HOSTILE` + feasible `ESCAPE_FLIGHT` actions for incoming hostile alerts."""
    active_coords_s = _safe_strategy_str(active.coords)
    for alert in state.fleet_alerts:
        if not alert.hostile:
            continue
        actions.append(
            PlannedAction(
                priority="P0",
                kind="ALERT_HOSTILE",
                planet_id=active.planet_id,
                planet_coords=active_coords_s,
                target_name="Escape flight / Fleet Save",
                reason=f"Hostile fleet detected: {_safe_strategy_str(alert.summary)}",
                executable_now=False,
            )
        )
        other_coords = next((p.coords for p in state.planets if p.planet_id != active.planet_id), active.coords)
        escape = plan_escape_flight(
            active,
            target_coords=other_coords,
            fuel_reserve_spice=cfg.fleet_save_fuel_reserve_spice,
        )
        if escape.feasible:
            actions.append(
                PlannedAction(
                    priority="P0",
                    kind="ESCAPE_FLIGHT",
                    planet_id=active.planet_id,
                    planet_coords=active_coords_s,
                    target_name=f"Save -> {_safe_strategy_str(escape.target_coords)}",
                    cost=escape.cargo,
                    reason=_safe_strategy_str(escape.reason),
                    executable_now=cfg.allow_fleetsave,
                )
            )


def select_planets_and_abandon_actions(
    state: GameState,
    active: PlanetState,
    cfg: StrategyConfig,
    plan: list[PlannedAction],
) -> list[PlanetState]:
    """Emit P1 `ABANDON_COLONY` actions for undersized fresh colonies and return ordered planets to check."""
    min_fields = max(50, _safe_strategy_int(getattr(cfg, "min_colony_fields", 300), 300))
    undersized = [p for p in state.planets if is_undersized_fresh_colony(p, state, min_fields)]
    undersized_pids = {p.planet_id for p in undersized}
    if undersized and (cfg.allow_colonize or _tutorial_colonize_task(state) is not None):
        for uc in undersized:
            uc_coords = _safe_strategy_str(uc.coords)
            hold_uc = "" if cfg.allow_colonize else " [Hold: allow_colonize=false in Config]"
            plan.append(
                PlannedAction(
                    priority="P1",
                    kind="ABANDON_COLONY",
                    planet_id=uc.planet_id,
                    planet_coords=uc_coords,
                    target_level=min_fields,
                    target_name=f"Delete colony {uc_coords} ({uc.fields_max}/{min_fields} fields)",
                    reason=(
                        f"Settled colony {uc_coords} rolled {uc.fields_max} fields "
                        f"(< {min_fields} minimum) — delete in Settings to re-colonize{hold_uc}"
                    ),
                    executable_now=bool(cfg.allow_colonize),
                )
            )

    raw_planets = list(state.planets) if cfg.allow_multi_planet else [active]
    planets_to_check = [p for p in raw_planets if p.planet_id not in undersized_pids]
    if not planets_to_check and state.planets:
        fallback_p = next((p for p in state.planets if p.planet_id not in undersized_pids), None)
        if fallback_p is not None:
            planets_to_check = [fallback_p]
    planets_to_check.sort(key=lambda p: 0 if p.planet_id == active.planet_id else 1)
    return planets_to_check


def _append_asteroid_solar_guard(
    planet: PlanetState,
    solar_base_raw: object,
    cfg: StrategyConfig,
    plan: list[PlannedAction],
) -> None:
    solar_offer = _ship_offer_map(planet).get(SOLAR_CELLS_ID)
    if planet.building_level(WEAPONS_FACTORY) < 1:
        return
    solar_cost = (
        solar_offer.cost
        if (solar_offer is not None and not solar_offer.cost.is_zero())
        else _resources_from_dict(solar_base_raw)
    )
    can_exec_s9 = bool(
        cfg.allow_shipyard
        and solar_offer is not None
        and solar_offer.can_build
        and planet.resources.can_afford(solar_cost)
    )
    plan.append(
        PlannedAction(
            priority="P1",
            kind="BUILD_SHIP",
            planet_id=planet.planet_id,
            planet_coords=_safe_strategy_str(planet.coords),
            target_id=SOLAR_CELLS_ID,
            target_name=(_safe_strategy_str(solar_offer.name) if solar_offer else "") or "Solar cells",
            target_level=1,
            cost=solar_cost,
            missing=planet.resources.missing(solar_cost),
            reason=(
                f"Negative energy on asteroid ({catalog.fmt_compact_int(planet.energy.produced)}/"
                f"{catalog.fmt_compact_int(planet.energy.used)}); build Solar cells (0 fields)"
            ),
            executable_now=can_exec_s9,
        )
    )


def append_negative_energy_guard(
    state: GameState,
    planet: PlanetState,
    offers: Mapping[int, BuildingOffer],
    eff_buildings: Mapping[str, int],
    *,
    ptype_now: str,
    queue_full: bool,
    allow_remote: bool,
    cfg: StrategyConfig,
    plan: list[PlannedAction],
) -> None:
    """Emit P1 negative-energy recovery (Solar cells on asteroids / full hot planets; power building otherwise)."""
    if _safe_strategy_int(planet.energy.net, 0) >= 0:
        return
    solar_base_raw = (catalog.SHIPS.get("solar-cells") or {}).get("base_cost", {})
    if ptype_now == "asteroid":
        _append_asteroid_solar_guard(planet, solar_base_raw, cfg, plan)
        return
    if ptype_now == "raid":
        return

    forbid_solar = bool(getattr(cfg, "forbid_planetary_solar", False))
    solar_offer = _ship_offer_map(planet).get(SOLAR_CELLS_ID)
    f_rem = _safe_strategy_int(planet.fields_remaining, 0)
    hot_tight = (
        not forbid_solar
        and _safe_strategy_int(planet.fields_max, 0) > 0
        and 0 < f_rem <= _safe_strategy_int(cfg.min_free_fields_warning, 20)
        and _safe_strategy_int(planet.temperature, 0) >= _safe_strategy_int(cfg.hot_planet_solar_temp_threshold, 40)
    )
    if (
        not forbid_solar
        and (not _planet_has_free_building_fields(planet) or hot_tight)
        and planet.building_level(WEAPONS_FACTORY) >= 1
        and solar_offer is not None
        and solar_offer.can_build
        and cfg.allow_shipyard
    ):
        solar_cost = solar_offer.cost if not solar_offer.cost.is_zero() else _resources_from_dict(solar_base_raw)
        plan.append(
            PlannedAction(
                priority="P1",
                kind="BUILD_SHIP",
                planet_id=planet.planet_id,
                planet_coords=_safe_strategy_str(planet.coords),
                target_id=SOLAR_CELLS_ID,
                target_name=_safe_strategy_str(solar_offer.name) or "Solar cells",
                target_level=1,
                cost=solar_cost,
                missing=planet.resources.missing(solar_cost),
                reason=(
                    f"Negative energy ({catalog.fmt_compact_int(planet.energy.produced)}/"
                    f"{catalog.fmt_compact_int(planet.energy.used)}) with 0 free building fields; build Solar cells (0 fields)"
                ),
                executable_now=bool(planet.resources.can_afford(solar_cost)),
            )
        )
        return

    power_bid, power_name, _ = _pick_best_power_building(
        planet, eff_buildings, state.researches, forbid_planetary_solar=forbid_solar
    )
    if _is_building_allowed_on_planet(planet, power_bid, offers):
        plan.append(
            _apply_queue_cap_if_full(
                _make_build_action(
                    "P1",
                    planet,
                    power_bid,
                    power_name,
                    f"Negative energy ({catalog.fmt_compact_int(planet.energy.produced)}/{catalog.fmt_compact_int(planet.energy.used)}); mines throttled",
                    offers,
                    effective_level=_eff_level(planet, eff_buildings, power_bid),
                    researches=state.researches,
                    allow_remote_colony_build=allow_remote,
                ),
                queue_full,
            )
        )
