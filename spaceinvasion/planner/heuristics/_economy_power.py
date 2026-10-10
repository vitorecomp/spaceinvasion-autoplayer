"""Proactive mine power and paused Blast Furnace energy guard helpers for economy planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import BuildingOffer, GameState, PlanetState, UnitOffer
from spaceinvasion.planner.heuristics._actions import _make_build_action, _pick_best_power_building
from spaceinvasion.planner.heuristics._common import (
    _apply_queue_cap_if_full,
    _is_building_allowed_on_planet,
    _norm_planet_type,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._queues import _eff_level, _energy_starvation_delta
from spaceinvasion.planner.heuristics._types import (
    _MINE_LABELS,
    BLAST_FURNACE,
    FUSION_PLANT,
    SOLAR_CELLS_ID,
    THERMOSOLAR_PLANT,
    WEAPONS_FACTORY,
    PlannedAction,
)


def _plan_proactive_mine_power(
    *,
    state: GameState,
    planet: PlanetState,
    offers: Mapping[int, BuildingOffer],
    ship_offers: Mapping[int, UnitOffer],
    eff_buildings: Mapping[str, int],
    top_bid: int,
    top_nxt: int,
    projected_net: int,
    queue_full: bool,
    allow_remote_build: bool,
    config: StrategyConfig,
) -> PlannedAction | None:
    starve_delta = _energy_starvation_delta(top_bid, top_nxt, projected_net)
    if not (starve_delta < 0 and _safe_strategy_int(planet.energy.net, 0) >= 0):
        return None
    temp_i = _safe_strategy_int(planet.temperature, 0)
    temp_s = catalog.fmt_compact_int(temp_i)
    fields_rem_i = _safe_strategy_int(planet.fields_remaining, 0)
    forbid_solar = bool(getattr(config, "forbid_planetary_solar", False))
    power_bid, power_name, power_note = _pick_best_power_building(
        planet, eff_buildings, state.researches, forbid_planetary_solar=forbid_solar
    )
    solar_offer = ship_offers.get(SOLAR_CELLS_ID)
    hot_tight_s9 = (
        not forbid_solar
        and _norm_planet_type(planet) == "normal"
        and _safe_strategy_int(planet.fields_max, 0) > 0
        and 0 < fields_rem_i <= _safe_strategy_int(config.min_free_fields_warning, 20)
        and temp_i >= _safe_strategy_int(config.hot_planet_solar_temp_threshold, 40)
    )
    if (
        (_norm_planet_type(planet) == "asteroid" or hot_tight_s9)
        and planet.building_level(WEAPONS_FACTORY) >= 1
        and solar_offer is not None
        and solar_offer.can_build
        and config.allow_shipyard
    ):
        s9_lbl = "asteroid" if _norm_planet_type(planet) == "asteroid" else "colony"
        return PlannedAction(
            priority="P1",
            kind="BUILD_SHIP",
            planet_id=planet.planet_id,
            planet_coords=_safe_strategy_str(planet.coords),
            target_id=SOLAR_CELLS_ID,
            target_name="Solar cells",
            target_level=1,
            cost=solar_offer.cost,
            missing=planet.resources.missing(solar_offer.cost),
            reason=f"0-field orbital power on {temp_s}C {s9_lbl} ({catalog.fmt_compact_int(fields_rem_i)} fields left)",
            executable_now=planet.resources.can_afford(solar_offer.cost),
        )
    if _is_building_allowed_on_planet(planet, power_bid, offers):
        power_act = _make_build_action(
            "P1",
            planet,
            power_bid,
            power_name,
            (
                f"Proactive power ({power_note}) for {_MINE_LABELS[top_bid]} L{catalog.fmt_compact_int(top_nxt)} "
                f"(needs {catalog.fmt_compact_int(-starve_delta)} energy, "
                f"net {catalog.fmt_compact_int(projected_net, signed=True)})"
            ),
            offers,
            effective_level=_eff_level(planet, eff_buildings, power_bid),
            researches=state.researches,
            allow_remote_colony_build=allow_remote_build,
        )
        return _apply_queue_cap_if_full(power_act, queue_full)
    return None


def _guard_mines_for_paused_blast(
    mine_actions: list[tuple[float, PlannedAction]],
    *,
    net_after_b2: int,
    blast_lvl_s: str,
    b2_latent_energy: int,
) -> tuple[list[tuple[float, PlannedAction]], bool]:
    held_any_mine = False
    guarded_mines: list[tuple[float, PlannedAction]] = []
    for pb_h, m_act in mine_actions:
        m_tid = catalog.safe_optional_int(m_act.target_id, None)
        m_lvl = _safe_strategy_int(m_act.target_level, 0)
        if m_act.executable_now and m_tid is not None and _energy_starvation_delta(m_tid, m_lvl, net_after_b2) < 0:
            held_any_mine = True
            guarded_mines.append(
                (
                    pb_h,
                    replace(
                        m_act,
                        kind="WAIT_RESOURCES",
                        executable_now=False,
                        reason=(
                            f"{m_act.reason} (hold until power covers paused Blast furnace "
                            f"L{blast_lvl_s} +{catalog.fmt_compact_int(b2_latent_energy)} energy)"
                        ),
                    ),
                )
            )
        else:
            guarded_mines.append((pb_h, m_act))
    return guarded_mines, held_any_mine


def _apply_paused_blast_power_guard(
    *,
    state: GameState,
    planet: PlanetState,
    offers: Mapping[int, BuildingOffer],
    eff_buildings: Mapping[str, int],
    projected_net: int,
    queue_full: bool,
    allow_remote_build: bool,
    config: StrategyConfig,
    blast_lvl: int,
    unlocks: Sequence[PlannedAction],
    advisories: Sequence[PlannedAction],
    mine_actions: list[tuple[float, PlannedAction]],
    priority_builds: list[PlannedAction],
) -> list[tuple[float, PlannedAction]]:
    b2_latent_energy = abs(
        _safe_strategy_int(catalog.building_level_stats(BLAST_FURNACE, blast_lvl).get("energy", 0) or 0, 0)
    )
    colony_needs_metal = any(a.missing.metal > 0 for a in (*unlocks, *advisories, *(m for _, m in mine_actions)))
    if not (colony_needs_metal and b2_latent_energy > 0):
        return mine_actions

    power_bid, power_name, power_note = _pick_best_power_building(
        planet,
        eff_buildings,
        state.researches,
        forbid_planetary_solar=bool(getattr(config, "forbid_planetary_solar", False)),
    )
    already_has_power = any(a.target_id in (FUSION_PLANT, THERMOSOLAR_PLANT, SOLAR_CELLS_ID) for a in priority_builds)
    blast_lvl_s = catalog.fmt_compact_int(blast_lvl)
    if (
        projected_net < b2_latent_energy
        and _safe_strategy_int(planet.energy.net, 0) >= 0
        and not already_has_power
        and _is_building_allowed_on_planet(planet, power_bid, offers)
    ):
        p1_bf_pw = _make_build_action(
            "P1",
            planet,
            power_bid,
            power_name,
            (
                f"Proactive power ({power_note}) to resume Blast furnace L{blast_lvl_s} "
                f"(needs {catalog.fmt_compact_int(b2_latent_energy)} energy, "
                f"net {catalog.fmt_compact_int(projected_net, signed=True)})"
            ),
            offers,
            effective_level=_eff_level(planet, eff_buildings, power_bid),
            researches=state.researches,
            allow_remote_colony_build=allow_remote_build,
        )
        priority_builds.insert(0, _apply_queue_cap_if_full(p1_bf_pw, queue_full))
        return mine_actions

    if projected_net < b2_latent_energy:
        return mine_actions

    pw_offer = offers.get(power_bid)
    if not queue_full and pw_offer is not None and pw_offer.can_build and planet.resources.can_afford(pw_offer.cost):
        return mine_actions

    guarded_mines, held_any_mine = _guard_mines_for_paused_blast(
        mine_actions,
        net_after_b2=projected_net - b2_latent_energy,
        blast_lvl_s=blast_lvl_s,
        b2_latent_energy=b2_latent_energy,
    )
    if held_any_mine and not already_has_power and _is_building_allowed_on_planet(planet, power_bid, offers):
        p2_bf_pw = _make_build_action(
            "P2",
            planet,
            power_bid,
            power_name,
            f"Scale power ({power_note}) so Blast furnace L{blast_lvl_s} can resume 100% alongside next mine",
            offers,
            effective_level=_eff_level(planet, eff_buildings, power_bid),
            researches=state.researches,
            allow_remote_colony_build=allow_remote_build,
        )
        priority_builds.append(_apply_queue_cap_if_full(p2_bf_pw, queue_full))
    return guarded_mines
