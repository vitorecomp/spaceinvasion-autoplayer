"""Delivered transport event pruning, realized throughput utilization, and fleet sizing summary helpers."""

from __future__ import annotations

from collections.abc import Mapping
import math
import time

from spaceinvasion.combat.fleet import fleet_cargo_capacity, normalize_coords
from spaceinvasion.core.state import (
    GameState,
    Resources,
    count_in_flight_ships_by_planet,
    is_in_flight_fleet_job,
    normalize_planet_type,
)
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._helpers import safe_api_float, safe_api_int
from spaceinvasion.runtime.bridge._helpers_queue import extract_coords_from_text, parse_ships_from_queue_detail
from spaceinvasion.runtime.bridge._ledgers_transport import is_returning_fleet, is_transport_name


def prune_delivered_transport_events(ctx: BridgeContext, now_ts: float | None = None) -> None:
    """Drop delivered transport events older than the 24h window and cap ring size."""
    if not ctx.delivered_transport_events:
        return
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    cutoff = eff_now - BridgeConstants.DELIVERED_TRANSPORT_WINDOW_SECONDS
    kept = [
        e
        for e in ctx.delivered_transport_events
        if isinstance(e, Mapping) and safe_api_float(e.get("ts"), 0.0) >= cutoff and safe_api_int(e.get("cargo"), 0) > 0
    ]
    if len(kept) > BridgeConstants.DELIVERED_TRANSPORT_MAX_EVENTS:
        kept = kept[-BridgeConstants.DELIVERED_TRANSPORT_MAX_EVENTS :]
    ctx.delivered_transport_events = kept


def compute_delivered_transport_stats(ctx: BridgeContext, now_ts: float | None = None) -> dict[str, object]:
    """Compute trailing 24h delivered transport cargo volume, hourly rate, and average fleet cargo."""
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    prune_delivered_transport_events(ctx, eff_now)
    events = ctx.delivered_transport_events
    if not events:
        return {
            "delivered_cargo_24h": 0,
            "delivered_cargo_per_hour": 0.0,
            "delivered_flights_24h": 0,
            "span_hours": 0.0,
            "avg_fleet_cargo": 0.0,
        }
    total_cargo = sum(max(0, safe_api_int(e.get("cargo"), 0)) for e in events)
    oldest_ts = min(safe_api_float(e.get("ts"), eff_now) for e in events)
    span_hours = max(0.0, (eff_now - oldest_ts) / 3600.0)
    obs_hours = max(1.0, min(24.0, span_hours))
    cph = round(float(total_cargo) / obs_hours, 2) if total_cargo > 0 else 0.0
    fleet_caps = [
        max(0, safe_api_int(e.get("fleet_cargo"), 0)) for e in events if safe_api_int(e.get("fleet_cargo"), 0) > 0
    ]
    avg_fc = (float(sum(fleet_caps)) / float(len(fleet_caps))) if fleet_caps else 0.0
    return {
        "delivered_cargo_24h": int(total_cargo),
        "delivered_cargo_per_hour": cph,
        "delivered_flights_24h": len(events),
        "span_hours": round(span_hours, 4),
        "avg_fleet_cargo": avg_fc,
    }


def recompute_transport_utilization(
    ctx: BridgeContext,
    now_ts: float | None = None,
    *,
    rt_per_hour: float = 2.5,
    fallback_fleet_cargo: int = 0,
) -> float:
    """Compute realized transport throughput utilization clamped to `[0.2, 0.9]`."""
    stats = compute_delivered_transport_stats(ctx, now_ts)
    flights = safe_api_int(stats["delivered_flights_24h"], 0)
    span_h = safe_api_float(stats["span_hours"], 0.0)
    if (
        flights < BridgeConstants.DELIVERED_TRANSPORT_MIN_SAMPLES
        or span_h < BridgeConstants.DELIVERED_TRANSPORT_MIN_SPAN_HOURS
    ):
        ctx.transport_realized_utilization = BridgeConstants.TRANSPORT_UTIL_DEFAULT
        return ctx.transport_realized_utilization
    avg_fc = safe_api_float(stats["avg_fleet_cargo"], 0.0)
    eff_fc = avg_fc if avg_fc > 0.0 else float(max(0, fallback_fleet_cargo))
    eff_rt = max(0.25, safe_api_float(rt_per_hour, 2.5))
    theoretical_cph = max(1.0, eff_fc * eff_rt) if eff_fc > 0.0 else 0.0
    if theoretical_cph <= 0.0:
        ctx.transport_realized_utilization = BridgeConstants.TRANSPORT_UTIL_DEFAULT
        return ctx.transport_realized_utilization
    raw_util = safe_api_float(stats["delivered_cargo_per_hour"], 0.0) / theoretical_cph
    clamped = round(min(BridgeConstants.TRANSPORT_UTIL_MAX, max(BridgeConstants.TRANSPORT_UTIL_MIN, raw_util)), 4)
    ctx.transport_realized_utilization = clamped
    return clamped


def _total_transport_fleet_cargo(state: GameState, miner_lvl: int) -> tuple[int, int, int]:
    parked_s: dict[str, int] = {"s1": 0, "s2": 0, "s3": 0}
    for p in state.planets:
        if normalize_planet_type(p) == "raid":
            continue
        for sk in ("s1", "s2", "s3"):
            parked_s[sk] += max(0, safe_api_int(p.ships.get(sk, 0), 0))
    owned_parked_cargo = int(fleet_cargo_capacity(parked_s, miner_spec_level=miner_lvl))
    if_info = count_in_flight_ships_by_planet(state)
    raw_emp = if_info.get("empire_in_flight") if isinstance(if_info, Mapping) else None
    emp_if: Mapping[str, object] = raw_emp if isinstance(raw_emp, Mapping) else {}
    if_ships = {sk: max(0, safe_api_int(emp_if.get(sk, 0), 0)) for sk in ("s1", "s2", "s3")}
    in_flight_cargo = int(fleet_cargo_capacity(if_ships, miner_spec_level=miner_lvl))
    return owned_parked_cargo, in_flight_cargo, owned_parked_cargo + in_flight_cargo


def observe_transport_utilization(
    ctx: BridgeContext,
    state: GameState | None,
    *,
    now_ts: float | None = None,
) -> bool:
    """Record each completed returning transport delivery once in the 24h ring and update utilization."""
    if state is None or not state.active_queues:
        return False
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    miner_lvl = safe_api_int(state.specs.get("miner", 0), 0)
    seen_keys = {str(e.get("key") or "") for e in ctx.delivered_transport_events if isinstance(e, Mapping)}
    total_fleet_cargo: int | None = None
    added = False
    for q in state.active_queues:
        if not is_in_flight_fleet_job(q):
            continue
        name_s = str(q.name or "")
        det_s = str(q.detail or "")
        if not is_transport_name(name_s.lower(), include_return=True):
            continue
        if not (bool(q.delivered) or is_returning_fleet(name_s, det_s.lower())):
            continue
        del_cargo = q.delivered_cargo
        if not isinstance(del_cargo, Resources) or del_cargo.is_zero():
            continue
        delivered_total = max(
            0,
            safe_api_int(del_cargo.pig_iron, 0)
            + safe_api_int(del_cargo.metal, 0)
            + safe_api_int(del_cargo.kryptonite, 0)
            + safe_api_int(del_cargo.spice, 0),
        )
        if delivered_total <= 0:
            continue
        orig_c = normalize_coords(str(q.origin_coords or ""))
        dest_c = extract_coords_from_text(name_s)
        fin_ts = safe_api_float(q.finish_ts, 0.0)
        fin_bucket = int(round(fin_ts / 180.0)) if fin_ts > 0.0 else int(eff_now // 600.0)
        ev_key = f"{orig_c}|{dest_c}|{delivered_total}|{fin_bucket}"
        if ev_key in seen_keys:
            continue
        if total_fleet_cargo is None:
            _, _, total_fleet_cargo = _total_transport_fleet_cargo(state, miner_lvl)
            if total_fleet_cargo <= 0:
                ships_m = parse_ships_from_queue_detail(det_s)
                total_fleet_cargo = int(fleet_cargo_capacity(ships_m, miner_spec_level=miner_lvl)) if ships_m else 0
        seen_keys.add(ev_key)
        ctx.delivered_transport_events.append(
            {
                "key": ev_key,
                "ts": round(eff_now, 3),
                "cargo": int(delivered_total),
                "fleet_cargo": int(total_fleet_cargo or 0),
            }
        )
        added = True
    if added:
        recompute_transport_utilization(ctx, eff_now, fallback_fleet_cargo=int(total_fleet_cargo or 0))
    return added


def build_transport_fleet_summary(
    ctx: BridgeContext,
    state: GameState | None,
    transport_demand: Mapping[str, object] | None,
    *,
    ordered_cargo_last_hour: int,
) -> dict[str, object] | None:
    """Build the adaptive transport fleet capacity vs planned demand telemetry summary."""
    if state is None:
        return None
    miner_lvl = safe_api_int(state.specs.get("miner", 0), 0)
    owned_parked_cargo, in_flight_cargo, total_fleet_cargo = _total_transport_fleet_cargo(state, miner_lvl)
    planned_cph = 0.0
    rt_per_h = 2.5
    if isinstance(transport_demand, Mapping):
        planned_cph = round(max(0.0, safe_api_float(transport_demand.get("cargo_per_hour_24h", 0.0), 0.0)), 2)
        rt_per_h = max(0.25, safe_api_float(transport_demand.get("round_trips_per_hour", 2.5), 2.5))
    util = recompute_transport_utilization(ctx, rt_per_hour=rt_per_h, fallback_fleet_cargo=total_fleet_cargo)
    deliv_stats = compute_delivered_transport_stats(ctx)
    sizing_util = max(BridgeConstants.TRANSPORT_UTIL_DEFAULT, util)
    target_cargo = int(math.ceil(planned_cph / rt_per_h / sizing_util)) if planned_cph > 0.0 else 0
    deliv_cph = safe_api_float(deliv_stats["delivered_cargo_per_hour"], 0.0)
    deliv_flights = safe_api_int(deliv_stats["delivered_flights_24h"], 0)
    delivered_vs_planned = (
        round(100.0 * deliv_cph / planned_cph, 2) if (planned_cph > 0.0 and deliv_flights > 0) else None
    )
    return {
        "owned_parked_cargo": owned_parked_cargo,
        "in_flight_cargo": in_flight_cargo,
        "total_fleet_cargo": total_fleet_cargo,
        "target_fleet_cargo": target_cargo,
        "planned_cargo_per_hour_24h": planned_cph,
        "realized_utilization": util,
        "sizing_utilization": sizing_util,
        "ordered_cargo_last_hour": ordered_cargo_last_hour,
        "delivered_cargo_24h": deliv_stats["delivered_cargo_24h"],
        "delivered_cargo_per_hour": deliv_cph,
        "delivered_cargo_per_hour_24h": deliv_cph,
        "delivered_flights_24h": deliv_flights,
        "delivered_vs_planned_pct": delivered_vs_planned,
    }
