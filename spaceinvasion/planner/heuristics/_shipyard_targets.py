"""Shipyard target `BUILD_SHIP` planners shared by economy unlocks and Fleet Planner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import (
    GameState,
    PlanetState,
    Resources,
    UnitOffer,
    has_in_flight_fleet_from_planet,
)
from spaceinvasion.planner.heuristics._cargo_helpers import (
    _adaptive_execution_enabled,
    _in_flight_ship_counts,
    _max_affordable_units,
    compute_infra_transport_targets,
)
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _resolve_shipyard_targets,
    _resources_from_dict,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._tutorial_tasks import _milestone_helper_ship_id
from spaceinvasion.planner.heuristics._types import (
    _SLUG_TO_SHIP_KEY,
    AI_INFRA_CARGO_TAG,
    INFRA_TRANSPORT_MAX_BATCH,
    WEAPONS_FACTORY,
    PlannedAction,
)

_IN_FLIGHT_HOLD_SHIP_IDS: frozenset[int] = frozenset({1, 2, 3, 4, 5, 6, 7, 13, 14, 15, 16})


def _tutorial_target_ship_ids(state: GameState) -> set[int]:
    """Return ship IDs explicitly requested by active incomplete tutorial tasks."""
    tutorial_ship_ids: set[int] = set()
    if state.tutorial and not state.tutorial.can_complete:
        for t in state.tutorial.tasks:
            if t.completed:
                continue
            t_bid = catalog.safe_optional_int(t.building_id, None)
            if t.category == "ship" and t_bid is not None:
                tutorial_ship_ids.add(t_bid)
            elif t.category == "milestone":
                h_sid = _milestone_helper_ship_id(_safe_strategy_str(t.description).lower())
                if h_sid is not None:
                    tutorial_ship_ids.add(h_sid)
    return tutorial_ship_ids


def _extract_planet_flight_info(
    flights: Mapping[str, object],
    planet_id: int,
    *,
    adaptive: bool,
) -> tuple[Mapping[str, object], bool]:
    if not adaptive:
        return {}, False
    by_pid_raw = flights.get("by_planet_id")
    flying_raw = by_pid_raw.get(planet_id) if isinstance(by_pid_raw, Mapping) else None
    flying_here: Mapping[str, object] = flying_raw if isinstance(flying_raw, Mapping) else {}
    det_raw = flights.get("detail_complete_by_planet_id")
    detail_ok = bool(det_raw.get(planet_id, True)) if isinstance(det_raw, Mapping) else True
    return flying_here, detail_ok


def _format_shipyard_reason_and_qty(
    *,
    planet: PlanetState,
    ship_slug: str,
    ship_name: str,
    owned: int,
    target_cnt: int,
    need: int,
    cost: Resources,
    hold_for_in_flight: bool,
    missing_b: Sequence[Mapping[str, object]],
    can_exec: bool,
    infra_slugs: set[str],
    configured_floor: Mapping[str, int] | None,
) -> tuple[str, int]:
    ship_reason = (
        f"Fleet build target ({catalog.fmt_compact_int(owned)}/{catalog.fmt_compact_int(target_cnt)} {ship_name})"
    )
    if hold_for_in_flight:
        ship_reason += " (hold while own fleet is in transit)"
    elif planet.building_level(WEAPONS_FACTORY) < 1:
        ship_reason += " (Weapons factory b9 required)"
    elif missing_b:
        first_req = missing_b[0]
        cur_s = catalog.fmt_compact_int(_safe_strategy_int(first_req.get("current", 0), 0))
        req_s = catalog.fmt_compact_int(_safe_strategy_int(first_req.get("required", 0), 0))
        ship_reason += f" (requires {first_req['name']} {cur_s}/{req_s})"

    order_qty = 1
    if configured_floor is not None and ship_slug in infra_slugs and owned >= configured_floor.get(ship_slug, 0):
        ship_reason += f" {AI_INFRA_CARGO_TAG}"
        if can_exec:
            order_qty = max(1, min(need, INFRA_TRANSPORT_MAX_BATCH, _max_affordable_units(planet.resources, cost)))
    return ship_reason, order_qty


def _resolve_ship_offer_meta(
    ship_slug: str,
    offer: UnitOffer | None,
) -> tuple[str, Resources]:
    if offer is not None:
        return _safe_strategy_str(offer.name) or ship_slug, offer.cost
    info = catalog.resolve_unit("ships", ship_slug) or {}
    return str(info.get("name") or ship_slug), _resources_from_dict(info.get("base_cost", {}))


def _build_shipyard_target_action(
    *,
    state: GameState,
    planet: PlanetState,
    ship_slug: str,
    target_cnt: int,
    ship_offers: Mapping[int, UnitOffer],
    config: StrategyConfig,
    tutorial_ship_ids: set[int],
    adaptive: bool,
    flying_here: Mapping[str, object],
    detail_ok: bool,
    infra_slugs: set[str],
    configured_floor: Mapping[str, int] | None,
) -> tuple[PlannedAction | None, bool, int]:
    if target_cnt <= 0:
        return None, False, 0
    if (
        ship_slug == "solar-cells"
        and _norm_planet_type(planet) != "asteroid"
        and bool(getattr(config, "forbid_planetary_solar", False))
        and 9 not in tutorial_ship_ids
    ):
        return None, False, 0
    skey = _SLUG_TO_SHIP_KEY.get(ship_slug)
    if not skey or len(skey) > 8 or not skey[1:].isascii() or not skey[1:].isdigit():
        return None, False, 0
    sid_num = int(skey[1:])
    offer = ship_offers.get(sid_num)
    if offer is None and catalog.missing_prerequisites(
        "ships", ship_slug, buildings=planet.buildings, researches=state.researches
    ):
        return None, False, sid_num

    owned = max(0, _safe_strategy_int(planet.ships.get(skey, offer.owned if offer is not None else 0), 0))
    if adaptive:
        owned += max(0, _safe_strategy_int(flying_here.get(skey, 0), 0))
    need = target_cnt - owned
    if need <= 0:
        return None, False, sid_num

    ship_name, cost = _resolve_ship_offer_meta(ship_slug, offer)
    missing = planet.resources.missing(cost)
    missing_b: list[Mapping[str, object]] = [
        m
        for m in catalog.missing_prerequisites(
            "ships", ship_slug, buildings=planet.buildings, researches=state.researches
        )
        if m.get("category") == "buildings"
    ]
    hold_for_in_flight = (
        has_in_flight_fleet_from_planet(state, planet)
        and not detail_ok
        and sid_num not in tutorial_ship_ids
        and sid_num in _IN_FLIGHT_HOLD_SHIP_IDS
    )
    can_exec = (
        not hold_for_in_flight
        and planet.building_level(WEAPONS_FACTORY) >= 1
        and not missing_b
        and offer is not None
        and offer.can_build
        and planet.resources.can_afford(cost)
    )
    ship_reason, order_qty = _format_shipyard_reason_and_qty(
        planet=planet,
        ship_slug=ship_slug,
        ship_name=ship_name,
        owned=owned,
        target_cnt=target_cnt,
        need=need,
        cost=cost,
        hold_for_in_flight=hold_for_in_flight,
        missing_b=missing_b,
        can_exec=can_exec,
        infra_slugs=infra_slugs,
        configured_floor=configured_floor,
    )
    return (
        PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=planet.planet_id,
            planet_coords=_safe_strategy_str(planet.coords),
            target_id=sid_num,
            target_name=ship_name,
            target_level=order_qty,
            cost=cost,
            missing=missing,
            reason=ship_reason,
            executable_now=can_exec,
        ),
        can_exec,
        sid_num,
    )


def _plan_shipyard_target_entries(
    state: GameState,
    planet: PlanetState,
    ship_offers: Mapping[int, UnitOffer],
    config: StrategyConfig,
    *,
    norm_shipyard_targets: Mapping[str, int],
    adaptive: bool,
    flights: Mapping[str, object],
    infra_slugs: set[str],
    configured_floor: Mapping[str, int] | None,
) -> list[PlannedAction]:
    tutorial_ship_ids = _tutorial_target_ship_ids(state)
    flying_here, detail_ok = _extract_planet_flight_info(flights, planet.planet_id, adaptive=adaptive)
    out_ships: list[PlannedAction] = []
    waiting_ships: list[tuple[float, PlannedAction]] = []

    for ship_slug, target_cnt in norm_shipyard_targets.items():
        ship_act, can_exec, sid_num = _build_shipyard_target_action(
            state=state,
            planet=planet,
            ship_slug=ship_slug,
            target_cnt=target_cnt,
            ship_offers=ship_offers,
            config=config,
            tutorial_ship_ids=tutorial_ship_ids,
            adaptive=adaptive,
            flying_here=flying_here,
            detail_ok=detail_ok,
            infra_slugs=infra_slugs,
            configured_floor=configured_floor,
        )
        if ship_act is None:
            continue
        if can_exec:
            out_ships.append(ship_act)
        elif sid_num not in tutorial_ship_ids:
            waiting_ships.append((catalog.spice_equivalent(ship_act.missing), ship_act))

    waiting_ships.sort(key=lambda pair: pair[0])
    for _, wait_act in waiting_ships[:3]:
        out_ships.append(wait_act)
    return out_ships


def _plan_planet_shipyard_targets(
    state: GameState,
    planet: PlanetState,
    ship_offers: Mapping[int, UnitOffer],
    config: StrategyConfig,
    *,
    transport_demand: Mapping[str, object] | None = None,
    in_flight_counts: Mapping[str, object] | None = None,
    infra_transport_targets: Mapping[int, Mapping[str, int]] | None = None,
) -> list[PlannedAction]:
    """Emit Shipyard `BUILD_SHIP` target actions for a single non-raid planet with Weapons factory."""
    if planet.building_level(WEAPONS_FACTORY) < 1 or not config.allow_shipyard or _norm_planet_type(planet) == "raid":
        return []
    adaptive = _adaptive_execution_enabled(config)
    flights: Mapping[str, object] = {}
    if adaptive:
        flights = in_flight_counts if isinstance(in_flight_counts, Mapping) else _in_flight_ship_counts(state)

    norm_shipyard_targets = _resolve_shipyard_targets(config.shipyard_targets)
    configured_shipyard_floor = dict(norm_shipyard_targets)
    infra_slugs: set[str] = set()
    if adaptive:
        if infra_transport_targets is None and isinstance(transport_demand, Mapping):
            infra_transport_targets = compute_infra_transport_targets(
                state, config, transport_demand, in_flight_counts=flights
            )
        for i_slug, i_target in ((infra_transport_targets or {}).get(planet.planet_id) or {}).items():
            i_val = _safe_strategy_int(i_target, 0)
            if i_val > norm_shipyard_targets.get(i_slug, 0):
                norm_shipyard_targets[i_slug] = i_val
                infra_slugs.add(i_slug)

    return _plan_shipyard_target_entries(
        state,
        planet,
        ship_offers,
        config,
        norm_shipyard_targets=norm_shipyard_targets,
        adaptive=adaptive,
        flights=flights,
        infra_slugs=infra_slugs,
        configured_floor=configured_shipyard_floor,
    )
