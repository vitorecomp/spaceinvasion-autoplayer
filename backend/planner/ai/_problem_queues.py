"""Transport-time estimation, friendly inbound cargo, and Blast furnace summaries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TypeVar

from backend.combat.flight import estimate_fleet_flight_seconds, ship_effective_speed
from backend.core.coercion import coerce_finite_float, coerce_int, coerce_optional_int
from backend.core.coords import flight_distance as _flight_distance_units
from backend.core.coords import normalize_coords
from backend.core.model import Resources, normalize_planet_type
from backend.planner.ai._types import (
    EMBEDDED_COORDS_RE,
    GameStateLike,
    HostileFlagged,
    PlanetLike,
    QueueJobLike,
    ResourcesLike,
    is_active_construction_status,
    is_queue_job_expired,
    is_valid_coords,
    resources_tuple,
)

_T = TypeVar("_T")


def _pick_transport_hull(pool: Mapping[str, object] | None) -> str | None:
    if not isinstance(pool, Mapping):
        return None
    s3 = max(0, coerce_int(pool.get("s3"), 0))
    s2 = max(0, coerce_int(pool.get("s2"), 0))
    s1 = max(0, coerce_int(pool.get("s1"), 0))
    if s3 > 0 and s2 == 0 and s1 == 0:
        return "s3"
    if s2 > 0 and s1 == 0:
        return "s2"
    if s1 > 0:
        return "s2" if s2 * 25_000 >= max(1, int((s2 * 25_000 + s1 * 5_000) * 0.80)) else "s1"
    return "s2" if s2 > 0 else ("s3" if s3 > 0 else None)


def estimate_interplanet_transport_seconds(
    origin_coords: str,
    target_coords: str,
    *,
    origin_ships: Mapping[str, object] | None = None,
    empire_ships: Mapping[str, object] | None = None,
    researches: Mapping[str, object] | None = None,
    fallback_distance_units: float = 1175.0,
) -> tuple[float, float, str]:
    """Return ``(one_way_seconds, distance_units, representative_hull)`` between two colonies."""
    orig, dest = str(origin_coords or "").strip(), str(target_coords or "").strip()
    if orig and dest and normalize_coords(orig) == normalize_coords(dest):
        return 0.0, 0.0, "local"
    hull = _pick_transport_hull(origin_ships) or _pick_transport_hull(empire_ships) or "s2"
    if is_valid_coords(orig) and is_valid_coords(dest):
        dist = float(_flight_distance_units(orig, dest))
        one_way = float(estimate_fleet_flight_seconds(orig, dest, {hull: 1}, researches=researches, round_trip=False))
        return round(max(1.0, one_way), 2), round(dist, 2), hull
    dist = max(100.0, float(fallback_distance_units))
    eff_speed = max(100.0, float(ship_effective_speed(hull, researches)))
    return round(max(1.0, 10.0 + (4765.0 * dist) / max(1.0, eff_speed)), 2), round(dist, 2), hull


def resolve_untagged_building_job_planet(
    bid: int | None,
    tgt_lvl: int | None,
    planets: Sequence[PlanetLike],
    active_p: PlanetLike | None,
) -> PlanetLike | None:
    """Safely attribute a legacy ``QueueJob(category='building', origin_coords='')`` without falsely blocking ``active_p``."""
    if active_p is None:
        return None
    active_offers = active_p.building_offers
    if any(is_active_construction_status(off.status_text) for off in active_offers):
        return active_p
    if active_offers and bid is not None and tgt_lvl is not None and active_p.building_level(bid) + 1 != tgt_lvl:
        offer_matches = [
            p
            for p in planets
            if p.planet_id != active_p.planet_id
            and any(
                coerce_optional_int(off.building_id, None) == bid and is_active_construction_status(off.status_text)
                for off in p.building_offers
            )
        ]
        if len(offer_matches) == 1:
            return offer_matches[0]
        level_matches = [
            p for p in planets if p.planet_id != active_p.planet_id and p.building_level(bid) + 1 == tgt_lvl
        ]
        return level_matches[0] if len(level_matches) == 1 else None
    return active_p


def parse_friendly_inbound_fleet_job(
    job: QueueJobLike,
    coords_to_key: Mapping[str, _T],
    *,
    state_ts: float = 0.0,
    skip_expired: bool = False,
    strict_hostile_text: bool = True,
    use_last_coords: bool = True,
) -> tuple[_T, tuple[float, float, float, float], bool, str] | None:
    """Parse one non-hostile fleet queue job into ``(dest_key, cargo_tuple, is_returning, name)`` if inbound."""
    if str(job.category or "").strip().lower() != "fleet":
        return None
    if skip_expired and is_queue_job_expired(job, state_ts):
        return None
    q_name, q_det = str(job.name or ""), str(job.detail or "")
    q_name_l, q_det_l = q_name.lower(), q_det.lower()
    if isinstance(job, HostileFlagged) and bool(job.hostile):
        return None
    if strict_hostile_text and (
        "hostile" in q_name_l or "hostile" in q_det_l or "enemy" in q_name_l or "enemy" in q_det_l
    ):
        return None
    raw_cargo = job.cargo
    if raw_cargo is None:
        return None
    if isinstance(raw_cargo, Mapping):
        cargo_tuple: tuple[float, float, float, float] = (
            max(0.0, coerce_finite_float(raw_cargo.get("pig_iron"), 0.0)),
            max(0.0, coerce_finite_float(raw_cargo.get("metal"), 0.0)),
            max(0.0, coerce_finite_float(raw_cargo.get("kryptonite"), 0.0)),
            max(0.0, coerce_finite_float(raw_cargo.get("spice"), 0.0)),
        )
    elif isinstance(raw_cargo, ResourcesLike):
        cargo_tuple = resources_tuple(raw_cargo)
    else:
        return None
    if sum(cargo_tuple) <= 0.0:
        return None
    is_returning = ("return" in q_det_l) or ("↩" in q_name)
    dest_key: _T | None = None
    if is_returning:
        orig = str(job.origin_coords or "").strip()
        if orig:
            dest_key = coords_to_key.get(orig)
            if dest_key is None:
                dest_key = coords_to_key.get(normalize_coords(orig))
    elif any(tok in q_name_l or tok in q_det_l for tok in ("transport", "station")):
        coord_matches = EMBEDDED_COORDS_RE.findall(q_name) or EMBEDDED_COORDS_RE.findall(q_det)
        if coord_matches:
            raw_dst = coord_matches[-1] if use_last_coords else coord_matches[0]
            dest_key = coords_to_key.get(raw_dst)
            if dest_key is None:
                dest_key = coords_to_key.get(normalize_coords(raw_dst))
    return None if dest_key is None else (dest_key, cargo_tuple, is_returning, q_name)


def compute_inbound_friendly_cargo_by_planet(state: GameStateLike | None) -> dict[int, Resources]:
    """Return ``{planet_id: Resources}`` of non-hostile inbound fleet cargo heading to each normal colony."""
    if state is None:
        return {}
    state_ts = max(0.0, coerce_finite_float(state.timestamp, 0.0))
    pid_by_coords: dict[str, int] = {}
    for p in state.planets:
        if normalize_planet_type(p) != "normal":
            continue
        c = str(p.coords or "").strip()
        if c:
            pid_by_coords[c] = int(p.planet_id)
            pid_by_coords[normalize_coords(c)] = int(p.planet_id)
    if not pid_by_coords:
        return {}
    totals: dict[int, list[float]] = {}
    for job in state.active_queues:
        parsed = parse_friendly_inbound_fleet_job(job, pid_by_coords, state_ts=state_ts, skip_expired=True)
        if parsed is None:
            continue
        dest_pid, cargo_tuple, _ret, _name = parsed
        buf = totals.setdefault(int(dest_pid), [0.0, 0.0, 0.0, 0.0])
        for pos in range(4):
            buf[pos] += float(cargo_tuple[pos])
    out: dict[int, Resources] = {}
    for pid, buf in totals.items():
        res = Resources(
            pig_iron=max(0, int(round(buf[0]))),
            metal=max(0, int(round(buf[1]))),
            kryptonite=max(0, int(round(buf[2]))),
            spice=max(0, int(round(buf[3]))),
        )
        if not res.is_zero():
            out[pid] = res
    return out


def summarize_blast_furnace_entries(entries: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Summarize per-colony Blast furnace (``b2``) levels and live throttle percentages."""
    planets_out: list[dict[str, object]] = []
    for row in entries:
        if not isinstance(row, Mapping):
            continue
        b2_lvl = max(0, coerce_int(row.get("b2_level"), 0))
        pct = max(0, min(100, coerce_int(row.get("initial_pct"), 100))) if b2_lvl > 0 else 0
        planets_out.append(
            {
                "planet_index": coerce_int(row.get("planet_index"), -1),
                "planet_id": coerce_int(row.get("planet_id"), 0),
                "coords": str(row.get("coords") or ""),
                "name": str(row.get("name") or row.get("coords") or ""),
                "b2_level": b2_lvl,
                "initial_pct": pct,
                "initial_enabled": bool(b2_lvl > 0 and pct > 0),
                "rate_known": bool(row.get("rate_known", False)),
            }
        )
    b2_cap = [row for row in planets_out if coerce_int(row["b2_level"], 0) > 0]
    enabled = sum(1 for row in b2_cap if bool(row["initial_enabled"]))
    full_on = sum(1 for row in b2_cap if coerce_int(row["initial_pct"], 0) >= 100)
    full_off = sum(1 for row in b2_cap if coerce_int(row["initial_pct"], 0) <= 0)
    if not b2_cap:
        mode, any_on, all_on = "none", False, False
    elif full_on == len(b2_cap):
        mode, any_on, all_on = "on", True, True
    elif full_off == len(b2_cap):
        mode, any_on, all_on = "off", False, False
    else:
        mode, any_on, all_on = "mixed", enabled > 0, enabled == len(b2_cap)
    return {
        "mode": mode,
        "any_enabled": any_on,
        "all_enabled": all_on,
        "enabled_count": enabled,
        "disabled_count": len(b2_cap) - enabled,
        "total_b2_planets": len(b2_cap),
        "planets": planets_out,
    }


def summarize_live_blast_furnace_state(state: GameStateLike | None) -> dict[str, object]:
    """Capture live Blast furnace (``b2``) enabled/disabled state across owned normal colonies."""
    if state is None:
        return summarize_blast_furnace_entries(())
    normal_planets = [p for p in state.planets if normalize_planet_type(p) == "normal"]
    entries: list[dict[str, object]] = []
    for idx, p in enumerate(normal_planets):
        b2_lvl = max(0, int(p.buildings.get("b2", 0) or 0))
        eco_rates = p.economy_rates
        pct = max(0, min(100, coerce_int(eco_rates.get("resource2", 100), 100))) if b2_lvl > 0 else 0
        entries.append(
            {
                "planet_index": idx,
                "planet_id": int(p.planet_id),
                "coords": str(p.coords or ""),
                "name": str(p.name or p.coords or ""),
                "b2_level": b2_lvl,
                "initial_pct": pct,
                "rate_known": "resource2" in eco_rates,
            }
        )
    return summarize_blast_furnace_entries(entries)
