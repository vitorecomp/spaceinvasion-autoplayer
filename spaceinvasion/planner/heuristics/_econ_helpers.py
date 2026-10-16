"""Mine payback, empire raw Spice-equivalent output, and prerequisite chain DFS helpers."""

from __future__ import annotations

from collections.abc import Mapping

from spaceinvasion.core import catalog
from spaceinvasion.core.model import BuildingOffer, GameState, PlanetState
from spaceinvasion.planner.heuristics._common import _safe_strategy_int
from spaceinvasion.planner.heuristics._queues import _eff_level
from spaceinvasion.planner.heuristics._types import (
    _MINE_RES_KEY,
    KRYPTONITE_MINE,
    PIG_IRON_MINE,
    SPICE_MINE,
)


def _mine_payback_hours(
    planet: PlanetState,
    eff_buildings: Mapping[str, int],
    building_id: int,
    mining_tech_level: int,
    offers: Mapping[int, BuildingOffer],
) -> tuple[float, float, int]:
    """Return `(payback_hours, delta_spice_per_hour, next_level)` at Merchant parity."""
    cur_lvl = _eff_level(planet, eff_buildings, building_id)
    nxt_lvl = cur_lvl + 1
    cur_prod = catalog.mine_production_per_hour(
        building_id, cur_lvl, temperature=planet.temperature, mining_tech_level=mining_tech_level
    )
    nxt_prod = catalog.mine_production_per_hour(
        building_id, nxt_lvl, temperature=planet.temperature, mining_tech_level=mining_tech_level
    )
    delta_prod = max(0.0, nxt_prod - cur_prod)
    delta_spice = delta_prod / catalog.MERCHANT_RATES_IN_SPICE[_MINE_RES_KEY[building_id]]
    if delta_spice <= 0:
        return 999999.0, 0.0, nxt_lvl

    offer = offers.get(building_id)
    if (
        offer is not None
        and catalog.safe_optional_int(offer.target_level, None) == nxt_lvl
        and not offer.cost.is_zero()
    ):
        cost_spice = catalog.spice_equivalent(offer.cost)
    else:
        cost_spice = catalog.spice_equivalent(catalog.building_cost(building_id, nxt_lvl))
    return (cost_spice / delta_spice if cost_spice > 0 else 0.0), delta_spice, nxt_lvl


def _empire_raw_spice_eq_per_hour(state: GameState) -> float:
    """Sum hourly Pig iron + Kryptonite + Spice output across all planets in Spice equivalents."""
    mining_lvl = max(0, _safe_strategy_int((state.researches or {}).get("r1", 0), 0))
    total = 0.0
    for planet in state.planets:
        for bid in (PIG_IRON_MINE, KRYPTONITE_MINE, SPICE_MINE):
            lvl = int(planet.building_level(bid))
            if lvl <= 0:
                continue
            prod = catalog.mine_production_per_hour(
                bid, lvl, temperature=planet.temperature, mining_tech_level=mining_lvl
            )
            total += prod / catalog.MERCHANT_RATES_IN_SPICE[_MINE_RES_KEY[bid]]
    return total


def _resolve_prereq_chain(
    category: str,
    slug: str,
    goal_label: str,
    eff_buildings: Mapping[str, int],
    researches: Mapping[str, int],
    visited: set[tuple[str, str]] | None = None,
) -> list[dict[str, object]]:
    """Walk `missing_prerequisites` DFS to surface immediately actionable unlock steps."""
    if visited is None:
        visited = set()
    if (category, slug) in visited:
        return []
    visited.add((category, slug))
    unmet = catalog.missing_prerequisites(category, slug, buildings=eff_buildings, researches=researches)
    steps: list[dict[str, object]] = []
    for req in unmet:
        sub = _resolve_prereq_chain(
            str(req["category"]), str(req["slug"]), goal_label, eff_buildings, researches, visited
        )
        if sub:
            steps.extend(sub)
        else:
            steps.append({**req, "goal": goal_label})
    return steps
