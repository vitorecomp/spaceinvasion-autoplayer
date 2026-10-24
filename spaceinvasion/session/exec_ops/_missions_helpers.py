"""Ship-selection and own-planet guard helpers for `MissionRunner`."""

from __future__ import annotations

import re

from spaceinvasion.core import catalog
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.core.model import Resources as ModelResources
from spaceinvasion.parsing.fleet_page import parse_station_ships_spec
from spaceinvasion.parsing.models import GameState, PlanetState, Resources
from spaceinvasion.session.exec_ops._cargo_spec import fleet_cargo_capacity, select_transport_ships
from spaceinvasion.session.exec_ops._types import ExecutionResult, safe_str
from spaceinvasion.session.exec_ops.fleet_dispatch import to_parsing_resources

_ESCAPE_PREFERRED_ORDER: tuple[str, ...] = (
    "s1",
    "s10",
    "s2",
    "s4",
    "s5",
    "s11",
    "s3",
    "s6",
    "s7",
    "s8",
    "s12",
    "s13",
    "s14",
    "s15",
    "s16",
)
_ATTACK_SHIP_KEYS: tuple[str, ...] = ("s4", "s5", "s6", "s7", "s13", "s14", "s15", "s16", "s1", "s2", "s3")
_OWN_DEST_COORDS_RE = re.compile(r"(\d{1,2}:\d{1,3}:\d{1,2})", re.ASCII)


def find_origin_planet(state: GameState, planet_id: int) -> PlanetState | None:
    """Resolve `planet_id` in `state.planets`, falling back to `state.active_planet()`."""
    return next((p for p in state.planets if p.planet_id == planet_id), None) or state.active_planet()


def select_escape_ships(planet: PlanetState | None, action: PlannedAction) -> dict[str, int]:
    """Select all stationed mobile ships (or 1 ship for tutorial escape) in preferred order."""
    stationed: dict[str, int] = {}
    if planet is not None:
        for k in _ESCAPE_PREFERRED_ORDER:
            qty = catalog.cap_json_int(catalog.safe_int((planet.ships or {}).get(k, 0), 0))
            if qty > 0:
                stationed[k] = qty
        for raw_k, raw_v in (planet.ships or {}).items():
            sk = safe_str(raw_k).strip()
            if sk == "s9" or sk in stationed:
                continue
            if sk.startswith("s") and sk[1:].isascii() and sk[1:].isdigit():
                qty = catalog.cap_json_int(catalog.safe_int(raw_v, 0))
                if qty > 0:
                    stationed[sk] = qty
    if not stationed:
        return {}
    if "tutorial" in safe_str(action.reason).lower():
        return {next(iter(stationed)): 1}
    return dict(stationed)


def select_attack_ships(planet: PlanetState | None) -> dict[str, int]:
    """Select all stationed combat and cargo ships eligible for an `ATTACK` dispatch."""
    if planet is None:
        return {}
    return {
        k: qty
        for k in _ATTACK_SHIP_KEYS
        if (qty := catalog.cap_json_int(catalog.safe_int(planet.ships.get(k, 0), 0))) > 0
    }


def resolve_own_origin_and_dest(
    state: GameState,
    action: PlannedAction,
    *,
    mission_label: str,
) -> tuple[PlanetState | None, PlanetState | None, ExecutionResult | None]:
    """Verify both origin and destination belong to the player's empire and are distinct planets."""
    origin = find_origin_planet(state, action.planet_id)
    if origin is None:
        return (
            None,
            None,
            ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: origin planet_id={action.planet_id} not found in empire state.",
            ),
        )
    tname_s = safe_str(action.target_name)
    m_c = _OWN_DEST_COORDS_RE.search(tname_s)
    norm_dest = normalize_coords(m_c.group(1)) if m_c else ""
    dest = next(
        (
            p
            for p in state.planets
            if (action.target_id is not None and p.planet_id == action.target_id)
            or (norm_dest and normalize_coords(p.coords) == norm_dest)
        ),
        None,
    )
    if dest is None:
        return (
            origin,
            None,
            ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: {mission_label} destination {norm_dest or tname_s!r} is not one of your own planets.",
            ),
        )
    if dest.planet_id == origin.planet_id:
        return (
            origin,
            dest,
            ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: {mission_label} origin and destination are the same planet [{origin.coords}].",
            ),
        )
    return origin, dest, None


def compute_transport_cargo_and_ships(
    state: GameState,
    origin: PlanetState,
    action: PlannedAction,
) -> tuple[Resources | None, dict[str, int], ExecutionResult | None]:
    """Clamp requested transport cargo to safe surplus, select cargo ships, and apply fuel-headroom scaling."""
    raw_cost = action.cost
    req_cargo = to_parsing_resources(raw_cost) if isinstance(raw_cost, (Resources, ModelResources)) else Resources()
    if req_cargo is None or req_cargo.is_zero():
        return (
            None,
            {},
            ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message="Aborted: Transport cargo is zero.",
            ),
        )
    fuel_reserve = 500
    safe_cargo = Resources(
        pig_iron=min(max(0, req_cargo.pig_iron), max(0, origin.resources.pig_iron)),
        metal=min(max(0, req_cargo.metal), max(0, origin.resources.metal)),
        kryptonite=min(max(0, req_cargo.kryptonite), max(0, origin.resources.kryptonite)),
        spice=min(max(0, req_cargo.spice), max(0, origin.resources.spice - fuel_reserve)),
    )
    total_units = safe_cargo.pig_iron + safe_cargo.metal + safe_cargo.kryptonite + safe_cargo.spice
    if total_units <= 0:
        return (
            None,
            {},
            ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: insufficient surplus resources on [{origin.coords}] to transport.",
            ),
        )
    miner_spec = catalog.safe_int((state.specs or {}).get("miner", 0), 0)
    ships_to_send = select_transport_ships(origin.ships, total_units + fuel_reserve, miner_spec_level=miner_spec)
    if not ships_to_send:
        return (
            None,
            {},
            ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: no cargo ships (s1/s2/s3) stationed on [{origin.coords}].",
            ),
        )
    cap = fleet_cargo_capacity(ships_to_send, miner_spec_level=miner_spec)
    hold_fuel_buf = min(cap // 10, max(fuel_reserve, int(cap * 0.03))) if cap > 0 else 0
    effective_cap = max(0, cap - hold_fuel_buf)
    if total_units > effective_cap and effective_cap > 0:
        ratio = effective_cap / float(total_units)
        safe_cargo = Resources(
            pig_iron=int(safe_cargo.pig_iron * ratio),
            metal=int(safe_cargo.metal * ratio),
            kryptonite=int(safe_cargo.kryptonite * ratio),
            spice=int(safe_cargo.spice * ratio),
        )
    return safe_cargo, ships_to_send, None


def resolve_station_ships(
    origin: PlanetState,
    dest: PlanetState,
    action: PlannedAction,
) -> tuple[dict[str, int], ExecutionResult | None]:
    """Validate destination type, parse requested station ship counts, and check Spice fuel."""
    if str(dest.planet_type or "normal").strip().lower() == "raid":
        return (
            {},
            ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: cannot Station ships to raid colony [{dest.coords}].",
            ),
        )
    tname_s = safe_str(action.target_name)
    req_ships = parse_station_ships_spec(tname_s) or parse_station_ships_spec(safe_str(action.reason))
    if not req_ships:
        return (
            {},
            ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: no valid mobile ship counts specified for Station to [{dest.coords}].",
            ),
        )
    ships_to_send: dict[str, int] = {}
    for skey, req_qty in req_ships.items():
        if skey == "s9":
            continue
        avail = max(0, catalog.safe_int((origin.ships or {}).get(skey, 0), 0))
        send_qty = min(max(0, int(req_qty)), avail)
        if send_qty > 0:
            ships_to_send[skey] = send_qty
    if not ships_to_send:
        return (
            {},
            ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: requested Station ships are not currently stationed on [{origin.coords}].",
            ),
        )
    if max(0, catalog.safe_int(origin.resources.spice, 0)) < 200:
        return (
            {},
            ExecutionResult(
                executed=False,
                dry_run=False,
                action=action,
                message=f"Aborted: insufficient Spice fuel on [{origin.coords}] for Station flight.",
            ),
        )
    return ships_to_send, None
