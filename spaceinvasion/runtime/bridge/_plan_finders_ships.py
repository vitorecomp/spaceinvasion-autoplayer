"""Direct shipyard action finder helpers."""

from __future__ import annotations

from collections.abc import Mapping
import re

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.catalog import SHIP_SLUG_BY_KEY
from spaceinvasion.core.model import GameState, PlanetState, PlannedAction, Resources, UnitOffer, normalize_planet_type
from spaceinvasion.planner.heuristics import _resources_from_dict
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import safe_api_int

_SHIP_KEY_RE = re.compile(r"^s?(\d{1,3})$", re.IGNORECASE | re.ASCII)


def parse_ship_id(ship_key: str | int) -> int:
    """Parse a ship identifier from an integer ID, `'s10'` key, or ship slug."""
    if isinstance(ship_key, int) and not isinstance(ship_key, bool):
        if ship_key < 1:
            raise ApiRequestError(f"Invalid ship_id {ship_key!r}.")
        return ship_key
    raw = str(ship_key or "").strip()
    m = _SHIP_KEY_RE.fullmatch(raw)
    if m:
        sid = int(m.group(1))
        if sid >= 1:
            return sid
    low = raw.lower()
    for skey, slug in SHIP_SLUG_BY_KEY.items():
        if low in (slug.lower(), slug.lower().replace("-", " ")):
            return int(skey[1:])
    unit = game_data.resolve_unit("ships", raw)
    if isinstance(unit, Mapping):
        uid = safe_api_int(unit.get("id"), 0)
        if uid >= 1:
            return uid
    raise ApiRequestError(f"Unknown ship_key {ship_key!r}.")


def _find_ship_without_offer(
    state: GameState,
    planet: PlanetState,
    ship_id: int,
    slug: str,
    qty: int,
    *,
    allow_live_verify: bool,
    has_missing_prereqs: bool,
) -> PlannedAction:
    unit = game_data.resolve_unit("ships", slug)
    raw_cost = unit.get("base_cost") if isinstance(unit, Mapping) else None
    uname = str(unit.get("name") or f"Ship {ship_id}") if isinstance(unit, Mapping) else f"Ship {ship_id}"
    can_fallback = planet.planet_id != state.active_planet_id or (allow_live_verify and not planet.ship_offers)
    if isinstance(raw_cost, dict) and can_fallback and planet.building_level(9) >= 1 and not has_missing_prereqs:
        unit_cost = _resources_from_dict(raw_cost)
        total_cost = Resources(
            pig_iron=unit_cost.pig_iron * qty,
            metal=unit_cost.metal * qty,
            kryptonite=unit_cost.kryptonite * qty,
            spice=unit_cost.spice * qty,
        )
        can_exec = not unit_cost.is_zero() and planet.resources.can_afford(total_cost)
        return PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=ship_id,
            target_name=uname,
            target_level=qty,
            cost=unit_cost,
            missing=planet.resources.missing(total_cost),
            reason="API build_ship command (live verify)"
            if can_exec
            else "API build_ship command (insufficient resources)",
            executable_now=can_exec,
        )
    return PlannedAction(
        priority="P2",
        kind="BUILD_SHIP",
        planet_id=planet.planet_id,
        planet_coords=planet.coords,
        target_id=ship_id,
        target_name=uname,
        target_level=qty,
        reason="API build_ship command (offer unavailable/not unlocked)",
        executable_now=False,
    )


def _find_ship_with_offer(
    planet: PlanetState,
    offer: UnitOffer,
    qty: int,
    *,
    allow_live_verify: bool,
    has_missing_prereqs: bool,
) -> PlannedAction:
    total_offer_cost = Resources(
        pig_iron=offer.cost.pig_iron * qty,
        metal=offer.cost.metal * qty,
        kryptonite=offer.cost.kryptonite * qty,
        spice=offer.cost.spice * qty,
    )
    can_exec = offer.can_build and not has_missing_prereqs and planet.resources.can_afford(total_offer_cost)
    status_l = (offer.status_text or "").lower()
    if (
        not offer.can_build
        and allow_live_verify
        and planet.building_level(9) >= 1
        and not has_missing_prereqs
        and ("not enough" in status_l or "insufficient" in status_l)
        and planet.resources.can_afford(total_offer_cost)
    ):
        can_exec = True
    if not offer.can_build and not can_exec:
        return PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=offer.unit_id,
            target_name=offer.name,
            target_level=qty,
            cost=offer.cost,
            missing=planet.resources.missing(total_offer_cost),
            reason="API build_ship command (offer unavailable/not unlocked)",
            executable_now=False,
        )
    return PlannedAction(
        priority="P2",
        kind="BUILD_SHIP",
        planet_id=planet.planet_id,
        planet_coords=planet.coords,
        target_id=offer.unit_id,
        target_name=offer.name,
        target_level=qty,
        cost=offer.cost,
        missing=planet.resources.missing(total_offer_cost),
        reason="API build_ship command",
        executable_now=can_exec,
    )


def find_ship_action(
    state: GameState,
    ship_key: str | int,
    count: int,
    planet_id: int | None = None,
    *,
    allow_live_verify: bool = False,
) -> PlannedAction:
    """Resolve an explicit `BUILD_SHIP` `PlannedAction` for `ship_key` x `count` on `planet_id`."""
    ship_id = parse_ship_id(ship_key)
    planet = BridgeContext.resolve_planet(state, planet_id)
    qty = max(1, int(count))
    slug = SHIP_SLUG_BY_KEY.get(f"s{ship_id}")
    ptype_norm = normalize_planet_type(planet)
    if ptype_norm == "raid" or slug is None:
        unit = game_data.resolve_unit("ships", slug) if slug else None
        uname = str(unit.get("name") or f"Ship {ship_id}") if isinstance(unit, Mapping) else f"Ship {ship_id}"
        return PlannedAction(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=planet.planet_id,
            planet_coords=planet.coords,
            target_id=ship_id,
            target_name=uname,
            target_level=qty,
            reason=(
                f"API build_ship command (shipyard not permitted on planet_type={ptype_norm})"
                if ptype_norm == "raid"
                else f"API build_ship command (unknown ship_id={ship_id})"
            ),
            executable_now=False,
            action_href=None,
        )
    has_missing = bool(
        game_data.missing_prerequisites("ships", slug, buildings=planet.buildings, researches=state.researches)
    )
    offer = next((o for o in planet.ship_offers if o.unit_id == ship_id), None)
    if offer is None:
        return _find_ship_without_offer(
            state, planet, ship_id, slug, qty, allow_live_verify=allow_live_verify, has_missing_prereqs=has_missing
        )
    return _find_ship_with_offer(
        planet, offer, qty, allow_live_verify=allow_live_verify, has_missing_prereqs=has_missing
    )
