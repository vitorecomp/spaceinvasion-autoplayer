"""Adaptive AI-Planner cargo, in-flight hull accounting, and infra transport target helpers."""

from __future__ import annotations

from collections.abc import Mapping
import math
import re

from spaceinvasion.combat.cargo import fleet_cargo_capacity
from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.coords import extract_coords_from_text
from spaceinvasion.core.model import (
    GameState,
    PlanetState,
    count_in_flight_ships_by_planet,
    is_in_flight_fleet_job,
)
from spaceinvasion.planner.heuristics._common import (
    _norm_planet_type,
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._types import (
    _IN_FLIGHT_DETAIL_CAP,
    _TRANSPORT_HULL_KEYS,
    INFRA_TRANSPORT_FLEET_CAP_RATIO,
    INFRA_TRANSPORT_MIN_UTILIZATION,
    INFRA_TRANSPORT_RAID_ALLOWANCE_CARGO,
    SHIP_SLUG_BY_KEY,
    WEAPONS_FACTORY,
)

_SHIP_NAME_TO_KEY_CACHE: dict[str, str] = {}


def _adaptive_execution_enabled(cfg: object) -> bool:
    """Return True when the `ai_adaptive_execution` master switch is on (default off)."""
    return bool(getattr(cfg, "ai_adaptive_execution", False))


def _adaptive_enabled_cfg(config: StrategyConfig | None) -> bool:
    return config is not None and _adaptive_execution_enabled(config)


def _ship_name_to_key_map() -> dict[str, str]:
    if not _SHIP_NAME_TO_KEY_CACHE:
        for skey, slug in SHIP_SLUG_BY_KEY.items():
            info = catalog.resolve_unit("ships", slug) or {}
            for raw in (str(info.get("name") or ""), slug):
                norm = " ".join(raw.lower().replace("-", " ").replace("_", " ").split())
                if norm:
                    _SHIP_NAME_TO_KEY_CACHE.setdefault(norm, skey)
    return _SHIP_NAME_TO_KEY_CACHE


def _ship_key_from_name(raw: str) -> str | None:
    norm = " ".join(str(raw or "").lower().replace("-", " ").replace("_", " ").split())
    if not norm:
        return None
    name_map = _ship_name_to_key_map()
    if norm in name_map:
        return name_map[norm]
    if norm.endswith("es") and norm[:-2] in name_map:
        return name_map[norm[:-2]]
    if norm.endswith("s") and norm[:-1] in name_map:
        return name_map[norm[:-1]]
    return None


def _parse_in_flight_ship_detail(detail: str) -> tuple[dict[str, int], bool]:
    """Parse `'Outbound · 5 Small transport, 2 Spy probe'` into `({skey: n}, detail_complete)`."""
    txt = str(detail or "")
    if "·" in txt:
        txt = txt.split("·", 1)[1]
    elif txt.strip().lower() in ("", "outbound", "returning"):
        return {}, False
    txt = txt.strip()
    if not txt:
        return {}, False
    complete = len(txt) < _IN_FLIGHT_DETAIL_CAP and not txt.endswith(("…", "..."))
    ships: dict[str, int] = {}
    for part in (p.strip() for p in re.split(r"[,;\n]", txt)):
        if not part:
            continue
        m_pre = re.match(r"^(\d[\d.,']*)\s*[x×]?\s+(.+)$", part)
        m_post = None if m_pre else re.match(r"^(.+?)\s*[:x×]?\s*(\d[\d.,']*)$", part)
        if m_pre:
            num_s, name_s = m_pre.group(1), m_pre.group(2)
        elif m_post:
            name_s, num_s = m_post.group(1), m_post.group(2)
        else:
            if _ship_key_from_name(part) is not None:
                complete = False
            continue
        skey = _ship_key_from_name(name_s)
        cnt = _safe_strategy_int(re.sub(r"\D", "", num_s), 0)
        if skey is None or cnt <= 0:
            complete = False
            continue
        ships[skey] = ships.get(skey, 0) + cnt
    if not ships:
        complete = False
    return ships, complete


def _norm_queue_coords(raw: object) -> str:
    return extract_coords_from_text(_safe_strategy_str(raw))


def _fallback_in_flight_ship_counts(state: GameState) -> dict[str, object]:
    """Internal fallback for `state.count_in_flight_ships_by_planet` (same return contract)."""
    planets = list(getattr(state, "planets", ()) or ())
    pid_by_coords: dict[str, int] = {}
    for p in planets:
        c = _norm_queue_coords(getattr(p, "coords", ""))
        if c:
            pid_by_coords.setdefault(c, p.planet_id)

    by_pid: dict[int, dict[str, int]] = {}
    complete_by_pid: dict[int, bool] = {}
    empire: dict[str, int] = {}
    unattributed = False
    for q in getattr(state, "active_queues", None) or ():
        if not is_in_flight_fleet_job(q):
            continue
        q_name = _safe_strategy_str(getattr(q, "name", ""))
        q_detail = _safe_strategy_str(getattr(q, "detail", ""))
        ships, complete = _parse_in_flight_ship_detail(q_detail)
        is_ret = "↩" in q_name or q_detail.strip().lower().startswith("returning")
        is_station = q_name.strip().lower().startswith("station")
        orig = _norm_queue_coords(getattr(q, "origin_coords", ""))
        orig_pid = pid_by_coords.get(orig) if orig else None
        owner: int | None = pid_by_coords.get(_norm_queue_coords(q_name)) if (is_station and not is_ret) else None
        if owner is None:
            owner = orig_pid
        for skey, cnt in ships.items():
            empire[skey] = empire.get(skey, 0) + cnt
        if not orig:
            unattributed = True
        for pid_v in {owner, orig_pid}:
            if pid_v is not None:
                if complete:
                    complete_by_pid.setdefault(pid_v, True)
                else:
                    complete_by_pid[pid_v] = False
        if owner is not None:
            bucket = by_pid.setdefault(owner, {})
            for skey, cnt in ships.items():
                bucket[skey] = bucket.get(skey, 0) + cnt
    if unattributed:
        for p in planets:
            complete_by_pid[p.planet_id] = False
    return {"by_planet_id": by_pid, "detail_complete_by_planet_id": complete_by_pid, "empire_in_flight": empire}


def _in_flight_ship_counts(state: GameState) -> dict[str, object]:
    """Ships in flight per home colony; delegates to `count_in_flight_ships_by_planet`."""
    raw: object = None
    try:
        raw = count_in_flight_ships_by_planet(state)
    except (TypeError, ValueError, KeyError, AttributeError):
        raw = None
    if not isinstance(raw, Mapping):
        raw = _fallback_in_flight_ship_counts(state)

    by_pid: dict[int, dict[str, int]] = {}
    raw_by_pid = raw.get("by_planet_id")
    if isinstance(raw_by_pid, Mapping):
        for pid_raw, ships in raw_by_pid.items():
            pid_v = _safe_strategy_int(pid_raw, 0)
            if pid_v > 0 and isinstance(ships, Mapping):
                by_pid[pid_v] = {str(k): max(0, _safe_strategy_int(v, 0)) for k, v in ships.items()}

    complete_by_pid: dict[int, bool] = {}
    raw_comp = raw.get("detail_complete_by_planet_id")
    if isinstance(raw_comp, Mapping):
        for pid_raw, flag in raw_comp.items():
            pid_v = _safe_strategy_int(pid_raw, 0)
            if pid_v > 0:
                complete_by_pid[pid_v] = bool(flag)

    empire: dict[str, int] = {}
    emp_raw = raw.get("empire_in_flight")
    if isinstance(emp_raw, Mapping):
        empire = {str(k): max(0, _safe_strategy_int(v, 0)) for k, v in emp_raw.items()}
    else:
        for ships in by_pid.values():
            for skey, cnt in ships.items():
                empire[skey] = empire.get(skey, 0) + cnt
    return {"by_planet_id": by_pid, "detail_complete_by_planet_id": complete_by_pid, "empire_in_flight": empire}


def _ship_cargo_per_hull(slug: str, default: int) -> int:
    info = catalog.resolve_unit("ships", slug) or {}
    stats = info.get("stats")
    cargo = _safe_strategy_int(stats.get("cargo", 0) if isinstance(stats, Mapping) else 0, 0)
    return cargo if cargo > 0 else default


def _max_affordable_units(resources: object, unit_cost: object) -> int:
    """Return how many units costing `unit_cost` each `resources` can pay for (0 for a free unit)."""
    units: int | None = None
    for rk in ("pig_iron", "metal", "kryptonite", "spice"):
        per_unit = max(0, _safe_strategy_int(getattr(unit_cost, rk, 0), 0))
        if per_unit <= 0:
            continue
        have = max(0, _safe_strategy_int(getattr(resources, rk, 0), 0))
        fit = have // per_unit
        units = fit if units is None else min(units, fit)
    return max(0, units or 0)


def _transport_hulls_on_raids(state: GameState) -> dict[str, int]:
    """Transport hulls (`s1`/`s2`/`s3`) flying an Attack mission, outbound or returning."""
    out: dict[str, int] = {k: 0 for k in _TRANSPORT_HULL_KEYS}
    for q in getattr(state, "active_queues", None) or ():
        if not is_in_flight_fleet_job(q):
            continue
        if not _safe_strategy_str(getattr(q, "name", "")).strip().lower().startswith("attack"):
            continue
        ships, _complete = _parse_in_flight_ship_detail(_safe_strategy_str(getattr(q, "detail", "")))
        for k in _TRANSPORT_HULL_KEYS:
            out[k] += max(0, _safe_strategy_int(ships.get(k, 0), 0))
    return out


def compute_infra_transport_targets(
    state: GameState,
    config: StrategyConfig | None,
    transport_demand: Mapping[str, object] | None,
    *,
    in_flight_counts: Mapping[str, object] | None = None,
) -> dict[int, dict[str, int]]:
    """Plan-sized transport fleet targets (Plan v2 Step 4a + Plan v4 E4 raid allowance)."""
    if not _adaptive_enabled_cfg(config) or not isinstance(transport_demand, Mapping):
        return {}
    cfg = config or StrategyConfig()
    if not cfg.allow_shipyard:
        return {}
    planned_cph = max(0.0, _safe_strategy_float(transport_demand.get("cargo_per_hour_24h", 0.0) or 0.0, 0.0))
    rt_per_h = max(0.25, _safe_strategy_float(transport_demand.get("round_trips_per_hour", 2.5) or 2.5, 2.5))
    util_raw = transport_demand.get("realized_utilization", transport_demand.get("utilization_assumed", 0.5))
    util = min(0.9, max(INFRA_TRANSPORT_MIN_UTILIZATION, _safe_strategy_float(util_raw or 0.5, 0.5)))
    target_cargo = int(math.ceil(planned_cph / rt_per_h / util)) if planned_cph > 0 else 0
    if target_cargo <= 0:
        return {}

    flights = in_flight_counts if isinstance(in_flight_counts, Mapping) else _in_flight_ship_counts(state)
    emp_raw = flights.get("empire_in_flight")
    empire_in_flight: Mapping[str, object] = emp_raw if isinstance(emp_raw, Mapping) else {}
    by_pid_raw = flights.get("by_planet_id")
    by_pid_flight: Mapping[object, object] = by_pid_raw if isinstance(by_pid_raw, Mapping) else {}

    non_raid = [p for p in (getattr(state, "planets", ()) or ()) if _norm_planet_type(p) != "raid"]
    hulls: dict[str, int] = {k: 0 for k in _TRANSPORT_HULL_KEYS}
    for p in non_raid:
        ships_m = getattr(p, "ships", None) or {}
        if isinstance(ships_m, Mapping):
            for k in _TRANSPORT_HULL_KEYS:
                hulls[k] += max(0, _safe_strategy_int(ships_m.get(k, 0), 0))
    for k in _TRANSPORT_HULL_KEYS:
        hulls[k] += max(0, _safe_strategy_int(empire_in_flight.get(k, 0), 0))
    current_cargo = _safe_strategy_int(fleet_cargo_capacity(hulls), 0)
    pending_cargo = max(0, _safe_strategy_int(transport_demand.get("infra_cargo_ordered_last_hour", 0) or 0, 0))
    raid_cargo = _safe_strategy_int(fleet_cargo_capacity(_transport_hulls_on_raids(state)), 0)
    logistics_cargo = max(0, current_cargo - raid_cargo)
    fleet_ceiling = min(
        int(target_cargo * INFRA_TRANSPORT_FLEET_CAP_RATIO),
        target_cargo + INFRA_TRANSPORT_RAID_ALLOWANCE_CARGO,
    )
    deficit_cargo = min(target_cargo - logistics_cargo, fleet_ceiling - current_cargo) - pending_cargo
    if deficit_cargo <= 0:
        return {}

    active = state.active_planet()
    pool = (
        non_raid
        if cfg.allow_multi_planet
        else ([active] if active is not None and _norm_planet_type(active) != "raid" else [])
    )
    yards = [p for p in pool if p.building_level(WEAPONS_FACTORY) >= 1]
    yards.sort(key=lambda p: (0 if active is not None and p.planet_id == active.planet_id else 1, p.planet_id))

    def _can_build(p: PlanetState, slug: str) -> bool:
        return not catalog.missing_prerequisites("ships", slug, buildings=p.buildings, researches=state.researches)

    caps = [p for p in yards if _can_build(p, "large-transport")]
    slug, skey, per_hull = "large-transport", "s2", _ship_cargo_per_hull("large-transport", 30_000)
    if not caps:
        caps = [p for p in yards if _can_build(p, "small-transport")]
        slug, skey, per_hull = "small-transport", "s1", _ship_cargo_per_hull("small-transport", 6_000)
    if not caps:
        return {}

    n_hulls = int(math.ceil(deficit_cargo / float(per_hull)))
    base, extra = divmod(n_hulls, len(caps))
    out: dict[int, dict[str, int]] = {}
    for idx, p in enumerate(caps):
        share = base + (1 if idx < extra else 0)
        if share <= 0:
            continue
        parked = max(0, _safe_strategy_int((getattr(p, "ships", None) or {}).get(skey, 0), 0))
        pf_m = by_pid_flight.get(p.planet_id)
        flying = max(0, _safe_strategy_int(pf_m.get(skey, 0) if isinstance(pf_m, Mapping) else 0, 0))
        out[p.planet_id] = {slug: parked + flying + share}
    return out


def _inbound_resource_value(raw: object, key: str) -> int:
    if raw is None:
        return 0
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return max(0, int(raw)) if key == "metal" else 0
    if isinstance(raw, Mapping):
        return max(0, _safe_strategy_int(raw.get(key, 0), 0))
    return max(0, _safe_strategy_int(getattr(raw, key, 0), 0))


def _friendly_cargo_flights(state: GameState) -> list[tuple[object, str, str, bool]]:
    """Return `(cargo, dest_coords, origin_coords, is_return)` for non-hostile transport/station/return flights."""
    out: list[tuple[object, str, str, bool]] = []
    for fq in getattr(state, "active_queues", None) or ():
        if _safe_strategy_str(getattr(fq, "category", "")).lower() != "fleet":
            continue
        cargo = getattr(fq, "cargo", None)
        if cargo is None:
            continue
        name_s = _safe_strategy_str(getattr(fq, "name", ""))
        detail_s = _safe_strategy_str(getattr(fq, "detail", ""))
        fq_txt = f"{name_s} {detail_s}".lower()
        if "hostile" in fq_txt or "enemy" in fq_txt:
            continue
        if not any(tok in fq_txt for tok in ("transport", "station", "return")):
            continue
        is_ret = "↩" in name_s or detail_s.strip().lower().startswith("returning")
        out.append((cargo, _norm_queue_coords(name_s), _norm_queue_coords(getattr(fq, "origin_coords", "")), is_ret))
    return out


def _fallback_inbound_cargo_by_planet(state: GameState) -> dict[int, dict[str, int]]:
    """Friendly cargo flying to each colony (outbound -> destination, returning -> origin)."""
    pid_by_coords: dict[str, int] = {}
    for p in getattr(state, "planets", ()) or ():
        c = _norm_queue_coords(p.coords)
        if c:
            pid_by_coords.setdefault(c, p.planet_id)
    out: dict[int, dict[str, int]] = {}
    for cargo, dest, orig, is_ret in _friendly_cargo_flights(state):
        tgt = pid_by_coords.get(orig if is_ret else dest)
        if tgt is None:
            continue
        bucket = out.setdefault(tgt, {"metal": 0, "pig_iron": 0})
        bucket["metal"] += _inbound_resource_value(cargo, "metal")
        bucket["pig_iron"] += _inbound_resource_value(cargo, "pig_iron")
    return out
