"""Pure SpaceInvasion Classic hull cargo capacity, transport packing, and Escape flight planning."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Protocol

from backend.core.catalog import (
    SHIP_SLUG_BY_KEY,
    fmt_compact_int,
    resolve_unit,
    safe_int,
    safe_key,
)
from backend.core.model import PlanetState, Resources

RESOURCE_KEYS: tuple[str, ...] = ("pig_iron", "metal", "kryptonite", "spice")


class ResourcesLike(Protocol):
    """Minimal read-only structural protocol for 4-resource bundles."""

    @property
    def pig_iron(self) -> int: ...

    @property
    def metal(self) -> int: ...

    @property
    def kryptonite(self) -> int: ...

    @property
    def spice(self) -> int: ...


@dataclass(frozen=True)
class EscapeFlightPlan:
    """Simulated or executable fleet-save dispatch maximizing saved resource value."""

    planet_id: int
    planet_coords: str
    target_coords: str
    ships: dict[str, int]
    cargo: Resources
    total_capacity: int
    fuel_reserved_spice: int
    duration_hint: str
    feasible: bool
    reason: str


def _safe_fleet_int(value: object, default: int = 0) -> int:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset, BaseException)):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    return safe_int(value, default)


def fleet_cargo_capacity(ships: Mapping[str, int], *, miner_spec_level: int = 0) -> int:
    """Return total fleet cargo capacity in units across all non-zero ships."""
    total = 0
    if not isinstance(ships, Mapping):
        return 0
    for key, count in ships.items():
        cnt = _safe_fleet_int(count, 0)
        if cnt <= 0:
            continue
        key_s = safe_key(key)
        slug = SHIP_SLUG_BY_KEY.get(key_s, key_s)
        info = resolve_unit("ships", slug)
        if not info:
            continue
        stats = info.get("stats")
        cargo = _safe_fleet_int(stats.get("cargo", 0), 0) if isinstance(stats, Mapping) else 0
        total += cnt * cargo
    spec_lvl = min(100_000, max(0, _safe_fleet_int(miner_spec_level, 0)))
    if spec_lvl > 0:
        try:
            total = int(total * (1.0 + 0.05 * spec_lvl))
        except OverflowError:
            total = (total * (100 + 5 * spec_lvl)) // 100
    return total


def plan_escape_flight(
    planet: PlanetState,
    *,
    target_coords: str,
    fuel_reserve_spice: int = 500,
    duration_hint: str = "next_login",
    save_solar_cells: bool = False,
) -> EscapeFlightPlan:
    """Pack saved resources in optimal value density: Spice -> Metal -> Kryptonite -> Pig iron.

    SpaceInvasion Classic Escape flights (`SAVE`) are invisible to Asteroid Galaxy scanners.
    """
    fuel_reserve = max(0, _safe_fleet_int(fuel_reserve_spice, 0))
    p_coords_s = safe_key(planet.coords)
    t_coords_s = safe_key(target_coords)
    dur_hint_s = safe_key(duration_hint)

    ships_to_save: dict[str, int] = {}
    for key, count in planet.ships.items():
        cnt = _safe_fleet_int(count, 0)
        if cnt <= 0:
            continue
        key_s = safe_key(key)
        if key_s == "s9" and not save_solar_cells:
            continue
        ships_to_save[key_s] = cnt

    cap = fleet_cargo_capacity(ships_to_save)
    if not ships_to_save or cap <= 0:
        return EscapeFlightPlan(
            planet_id=planet.planet_id,
            planet_coords=p_coords_s,
            target_coords=t_coords_s,
            ships={},
            cargo=Resources(),
            total_capacity=0,
            fuel_reserved_spice=fuel_reserve,
            duration_hint=dur_hint_s,
            feasible=False,
            reason="No mobile ships with cargo capacity stationed on planet",
        )

    res = planet.resources
    res_spice = _safe_fleet_int(res.spice, 0)
    res_metal = max(0, _safe_fleet_int(res.metal, 0))
    res_kryp = max(0, _safe_fleet_int(res.kryptonite, 0))
    res_pig = max(0, _safe_fleet_int(res.pig_iron, 0))
    avail_spice = max(0, res_spice - fuel_reserve)

    if res_spice < min(fuel_reserve, 50) and cap > 0:
        return EscapeFlightPlan(
            planet_id=planet.planet_id,
            planet_coords=p_coords_s,
            target_coords=t_coords_s,
            ships=ships_to_save,
            cargo=Resources(),
            total_capacity=cap,
            fuel_reserved_spice=fuel_reserve,
            duration_hint=dur_hint_s,
            feasible=False,
            reason=f"Insufficient Spice for escape-flight fuel ({fmt_compact_int(res_spice)} available)",
        )

    rem = max(0, cap - fuel_reserve)
    take_spice = min(avail_spice, rem)
    rem -= take_spice
    take_metal = min(res_metal, rem)
    rem -= take_metal
    take_kryp = min(res_kryp, rem)
    rem -= take_kryp
    take_pig = min(res_pig, rem)

    cargo = Resources(
        pig_iron=take_pig,
        metal=take_metal,
        kryptonite=take_kryp,
        spice=take_spice,
    )
    ship_cnt_str = fmt_compact_int(sum(ships_to_save.values()))
    return EscapeFlightPlan(
        planet_id=planet.planet_id,
        planet_coords=p_coords_s,
        target_coords=t_coords_s,
        ships=ships_to_save,
        cargo=cargo,
        total_capacity=cap,
        fuel_reserved_spice=fuel_reserve,
        duration_hint=dur_hint_s,
        feasible=True,
        reason=f"Packed {cargo.format_short()} into {ship_cnt_str} ships",
    )


def extract_target_debris_resources(raw_target: Mapping[str, object]) -> int:
    """Extract known total debris resources (Pig iron + Metal + Kryptonite + Spice) from a target mapping."""
    if not isinstance(raw_target, Mapping):
        return 0
    explicit_total = max(0, _safe_fleet_int(raw_target.get("debris_total", 0), 0))
    if explicit_total > 0:
        return explicit_total
    for nested_key in ("debris", "debris_available"):
        nested = raw_target.get(nested_key)
        if isinstance(nested, Mapping):
            nested_total = sum(max(0, _safe_fleet_int(nested.get(rk, 0), 0)) for rk in RESOURCE_KEYS)
            if nested_total > 0:
                return nested_total
    return sum(max(0, _safe_fleet_int(raw_target.get(f"debris_{rk}", 0), 0)) for rk in RESOURCE_KEYS)


def select_transport_ships(
    stationed_ships: Mapping[str, int],
    cargo_units: int,
    *,
    miner_spec_level: int = 0,
) -> dict[str, int]:
    """Select the minimal stationed cargo ship mix (`s3` -> `s2` -> `s1`) able to carry `cargo_units`."""
    if not isinstance(stationed_ships, Mapping) or cargo_units <= 0:
        return {}
    rem = int(cargo_units)
    chosen: dict[str, int] = {}
    for skey in ("s3", "s2", "s1"):
        avail = max(0, _safe_fleet_int(stationed_ships.get(skey, 0), 0))
        if avail <= 0 or rem <= 0:
            continue
        cap_one = fleet_cargo_capacity({skey: 1}, miner_spec_level=miner_spec_level)
        if cap_one <= 0:
            continue
        need_n = min(avail, (rem + cap_one - 1) // cap_one)
        if need_n > 0:
            chosen[skey] = need_n
            rem = max(0, rem - need_n * cap_one)
    return chosen


def _pack_resource_budget(avail_by_res: Mapping[str, int], cap_left: int) -> dict[str, int]:
    """Pack up to `cap_left` units from `avail_by_res`, proportionally when total exceeds `cap_left`."""
    cap = max(0, int(cap_left))
    clean = {rk: max(0, int(avail_by_res.get(rk, 0))) for rk in RESOURCE_KEYS}
    total_want = sum(clean.values())
    if cap <= 0 or total_want <= 0:
        return {rk: 0 for rk in RESOURCE_KEYS}
    if total_want <= cap:
        return clean
    packed = {rk: int((clean[rk] * cap) // total_want) for rk in RESOURCE_KEYS}
    rem_cap = cap - sum(packed.values())
    for rk in ("spice", "metal", "kryptonite", "pig_iron"):
        if rem_cap <= 0:
            break
        take = min(clean[rk] - packed[rk], rem_cap)
        if take > 0:
            packed[rk] += take
            rem_cap -= take
    return packed


def _res_to_dict(bundle: ResourcesLike) -> dict[str, int]:
    return {
        "pig_iron": max(0, int(bundle.pig_iron)),
        "metal": max(0, int(bundle.metal)),
        "kryptonite": max(0, int(bundle.kryptonite)),
        "spice": max(0, int(bundle.spice)),
    }


def top_up_transport_cargo(
    packed: Resources,
    need: Resources,
    surplus: Resources,
    dest_free_storage: Resources | None,
    capacity: int,
) -> Resources:
    """Top up `packed` with additional spare `surplus` up to `capacity` and `dest_free_storage`."""
    cap_left = max(0, int(capacity))
    base = _res_to_dict(packed)
    rem_cap = max(0, cap_left - sum(base.values()))
    if rem_cap <= 0:
        return Resources(**base)

    need_d = _res_to_dict(need)
    surplus_d = _res_to_dict(surplus)
    dest_d = _res_to_dict(dest_free_storage) if dest_free_storage is not None else None

    extra_avail: dict[str, int] = {}
    for rk in RESOURCE_KEYS:
        if dest_d is not None:
            extra_avail[rk] = min(surplus_d[rk], max(0, dest_d[rk] - base[rk]))
        else:
            extra_avail[rk] = surplus_d[rk]

    req_extra = {rk: extra_avail[rk] if need_d[rk] > 0 else 0 for rk in RESOURCE_KEYS}
    add1 = _pack_resource_budget(req_extra, rem_cap)
    for rk in RESOURCE_KEYS:
        if add1[rk] > 0:
            base[rk] += add1[rk]
            extra_avail[rk] = max(0, extra_avail[rk] - add1[rk])
            rem_cap = max(0, rem_cap - add1[rk])

    if rem_cap > 0:
        add2 = _pack_resource_budget(extra_avail, rem_cap)
        for rk in RESOURCE_KEYS:
            base[rk] += add2[rk]

    return Resources(**base)


def pack_transport_cargo(
    need: Resources,
    surplus: Resources,
    dest_free_storage: Resources | None,
    capacity: int,
    *,
    fill_to_capacity: bool = False,
) -> Resources:
    """Pack transport cargo satisfying `need` bounded by `surplus`, `dest_free_storage`, and `capacity`."""
    cap_left = max(0, int(capacity))
    if cap_left <= 0:
        return Resources()
    need_d = _res_to_dict(need)
    surplus_d = _res_to_dict(surplus)
    dest_d = _res_to_dict(dest_free_storage) if dest_free_storage is not None else None

    avail_by_res: dict[str, int] = {}
    for rk in RESOURCE_KEYS:
        bound = min(need_d[rk], surplus_d[rk])
        if dest_d is not None:
            bound = min(bound, dest_d[rk])
        avail_by_res[rk] = bound

    packed_map = _pack_resource_budget(avail_by_res, cap_left)
    packed_res = Resources(**packed_map)
    if fill_to_capacity and packed_res.total() < cap_left:
        rem_surplus = Resources(
            pig_iron=max(0, surplus_d["pig_iron"] - packed_res.pig_iron),
            metal=max(0, surplus_d["metal"] - packed_res.metal),
            kryptonite=max(0, surplus_d["kryptonite"] - packed_res.kryptonite),
            spice=max(0, surplus_d["spice"] - packed_res.spice),
        )
        return top_up_transport_cargo(packed_res, need, rem_surplus, dest_free_storage, cap_left)
    return packed_res
