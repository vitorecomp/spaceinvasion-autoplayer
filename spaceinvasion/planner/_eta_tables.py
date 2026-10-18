"""Building and research row builders for `spaceinvasion.planner.eta`."""

from __future__ import annotations

from collections.abc import Mapping

from spaceinvasion.combat._points_helpers import (
    cap_eta_float,
    cap_json_int_eta,
    res_dict,
    safe_eta_str,
    safe_nonneg_int,
)
from spaceinvasion.combat.points import format_eta
from spaceinvasion.core.catalog import (
    BUILDING_BY_ID,
    RESEARCH_BY_ID,
    building_cost,
    building_energy_delta,
    building_level_stats,
    missing_prerequisites,
    safe_finite_float,
    safe_key,
    safe_optional_int,
)
from spaceinvasion.core.state import GameState, PlanetState
from spaceinvasion.planner._eta_helpers import (
    ETA_RESOURCE_KEYS,
    building_category,
    compute_resource_eta,
    format_duration,
    research_level_stats,
    safe_eta_int,
)


def _build_building_horizons(
    bid: int,
    nxt_lvl: int,
    first_cost: Mapping[str, int],
    res_have: Mapping[str, int],
    rates_hr: Mapping[str, float],
    *,
    dev_lvl: int,
    micro_lvl: int,
    speedup: float,
    safe_horizon: int,
) -> list[dict[str, object]]:
    horizons: list[dict[str, object]] = []
    cum_cost = {rk: 0 for rk in ETA_RESOURCE_KEYS}
    cum_build_s = 0.0
    sim_dev = dev_lvl
    sim_micro = micro_lvl
    for step_idx in range(safe_horizon):
        step_lvl = nxt_lvl + step_idx
        step_stats = building_level_stats(bid, step_lvl)
        step_cost_raw: Mapping[str, object] = (
            first_cost
            if step_idx == 0
            else (step_stats["cost"] if isinstance(step_stats.get("cost"), Mapping) else building_cost(bid, step_lvl))
        )
        for rk in ETA_RESOURCE_KEYS:
            cum_cost[rk] += safe_nonneg_int(step_cost_raw.get(rk, 0), 0)
        cum_missing = {rk: max(0, cum_cost[rk] - int(res_have.get(rk, 0) or 0)) for rk in ETA_RESOURCE_KEYS}
        step_wait_h, step_bn = compute_resource_eta(cum_missing, rates_hr)
        if step_wait_h is not None:
            step_wait_h = cap_eta_float(step_wait_h)
        step_div = max(
            1.0,
            float(min(max(0, sim_dev), 1_000_000) + 1) * float(2 ** min(max(0, sim_micro), 60)) * speedup,
        )
        step_b_s = cap_eta_float(safe_finite_float(step_stats.get("base_time_s", 0.0), 0.0) / step_div)
        cum_build_s = cap_eta_float(cum_build_s + step_b_s)
        if bid == 7:
            sim_dev = step_lvl
        elif bid == 17:
            sim_micro = step_lvl
        step_reach_h = cap_eta_float(step_wait_h + cum_build_s / 3600.0) if step_wait_h is not None else None
        horizons.append(
            {
                "level": cap_json_int_eta(step_lvl),
                "cost": {
                    rk: cap_json_int_eta(safe_nonneg_int(step_cost_raw.get(rk, 0), 0)) for rk in ETA_RESOURCE_KEYS
                },
                "cum_cost": {rk: cap_json_int_eta(v) for rk, v in cum_cost.items()},
                "build_time_text": format_duration(step_b_s),
                "wait_eta": format_eta(step_wait_h),
                "reach_eta": format_eta(step_reach_h),
                "bottleneck": step_bn,
            }
        )
    return horizons


def build_building_eta_rows(
    state: GameState,
    target: PlanetState,
    p_econ: Mapping[str, object],
    rates_hr: Mapping[str, float],
    res_have: Mapping[str, int],
    *,
    dev_lvl: int,
    micro_lvl: int,
    b_divisor: float,
    speedup: float,
    safe_horizon: int,
) -> list[dict[str, object]]:
    """Compute ETA rows for all buildings `b1`-`b21` on `target`."""
    queued_bids: dict[int, int] = {}
    if target.planet_id == state.active_planet_id:
        for q in state.active_queues:
            if q.category == "building" and q.building_id is not None:
                q_bid = safe_optional_int(q.building_id, None)
                if q_bid is not None:
                    queued_bids[q_bid] = queued_bids.get(q_bid, 0) + 1

    b_offers = {
        o_bid: o
        for o in (target.building_offers or ())
        if (o_bid := safe_optional_int(o.building_id, None)) is not None
    }
    rates_pct_obj = p_econ.get("rates_pct")
    rates_pct = rates_pct_obj if isinstance(rates_pct_obj, Mapping) else {}

    rows: list[dict[str, object]] = []
    for bid in sorted(BUILDING_BY_ID.keys()):
        info = BUILDING_BY_ID[bid]
        cur_lvl = target.building_level(bid)
        nxt_lvl = cur_lvl + 1
        offer = b_offers.get(bid)
        stats_next = building_level_stats(bid, nxt_lvl)
        if offer is not None and not offer.cost.is_zero() and safe_eta_int(offer.target_level, -1) == nxt_lvl:
            raw_cost: Mapping[str, object] = res_dict(offer.cost)
        else:
            st_cost = stats_next.get("cost")
            raw_cost = st_cost if isinstance(st_cost, Mapping) else building_cost(bid, nxt_lvl)
        cost = {rk: safe_nonneg_int(raw_cost.get(rk, 0), 0) for rk in ETA_RESOURCE_KEYS}
        missing = {rk: max(0, cost[rk] - int(res_have.get(rk, 0) or 0)) for rk in ETA_RESOURCE_KEYS}
        missing_total = sum(missing.values())
        can_afford = missing_total == 0
        wait_h, bottleneck = compute_resource_eta(missing, rates_hr)
        if wait_h is not None:
            wait_h = cap_eta_float(wait_h)
        build_s = cap_eta_float(safe_finite_float(stats_next.get("base_time_s", 0.0), 0.0) / b_divisor)
        reach_h = cap_eta_float(wait_h + build_s / 3600.0) if wait_h is not None else None
        unmet = missing_prerequisites("buildings", bid, buildings=target.buildings, researches=state.researches)
        rate_key = f"resource{bid}" if bid in (1, 2, 3, 4) else None
        rate_pct = rates_pct.get(rate_key, 100) if rate_key else None

        rows.append(
            {
                "building_id": bid,
                "key": f"b{bid}",
                "slug": str(info["slug"]),
                "name": safe_eta_str(target.building_names.get(f"b{bid}")) or str(info["name"]),
                "category": building_category(bid),
                "asteroid_ok": bool(info.get("asteroid_ok", False)),
                "current_level": cap_json_int_eta(cur_lvl),
                "next_level": cap_json_int_eta(nxt_lvl),
                "queued_count": queued_bids.get(bid, 0),
                "energy_delta": cap_json_int_eta(building_energy_delta(bid, nxt_lvl)),
                "cost": {rk: cap_json_int_eta(v) for rk, v in cost.items()},
                "missing": {rk: cap_json_int_eta(v) for rk, v in missing.items()},
                "missing_total": cap_json_int_eta(missing_total),
                "can_afford": can_afford,
                "can_build_live": bool(offer.can_build and can_afford) if offer is not None else False,
                "offer_visible": offer is not None,
                "unmet_prerequisites": [str(u) for u in unmet],
                "wait_hours": round(wait_h, 3) if wait_h is not None else None,
                "wait_eta": format_eta(wait_h),
                "build_time_seconds": round(build_s, 1),
                "build_time_text": format_duration(build_s),
                "reach_hours": round(reach_h, 3) if reach_h is not None else None,
                "reach_eta": format_eta(reach_h),
                "bottleneck": bottleneck,
                "economy_rate_key": rate_key,
                "economy_rate_pct": rate_pct,
                "horizons": _build_building_horizons(
                    bid,
                    nxt_lvl,
                    cost,
                    res_have,
                    rates_hr,
                    dev_lvl=dev_lvl,
                    micro_lvl=micro_lvl,
                    speedup=speedup,
                    safe_horizon=safe_horizon,
                ),
            }
        )
    return rows


def build_research_eta_rows(
    state: GameState,
    target: PlanetState,
    rates_hr: Mapping[str, float],
    res_have: Mapping[str, int],
    *,
    r_divisor: float,
    safe_horizon: int,
) -> list[dict[str, object]]:
    """Compute ETA rows for all researches `r1`-`r15` evaluated at `target`."""
    r_offers = {rk: o for o in (state.research_offers or ()) if (rk := safe_key(o.research_key or ""))}
    r_keys = sorted(
        RESEARCH_BY_ID.keys(),
        key=lambda rk: int(rk[1:]) if (rk[1:].isascii() and rk[1:].isdigit()) else 999,
    )
    rows: list[dict[str, object]] = []
    for rkey in r_keys:
        info = RESEARCH_BY_ID[rkey]
        rid_num = int(rkey[1:]) if (rkey[1:].isascii() and rkey[1:].isdigit()) else None
        cur_lvl = safe_nonneg_int((state.researches or {}).get(rkey, 0), 0)
        nxt_lvl = cur_lvl + 1
        offer = r_offers.get(rkey)
        stats_next = research_level_stats(info, nxt_lvl)
        if offer is not None and not offer.cost.is_zero() and safe_eta_int(offer.target_level, -1) == nxt_lvl:
            raw_cost: Mapping[str, object] = res_dict(offer.cost)
        else:
            st_cost = stats_next.get("cost")
            raw_cost = st_cost if isinstance(st_cost, Mapping) else {}
        cost = {rk: safe_nonneg_int(raw_cost.get(rk, 0), 0) for rk in ETA_RESOURCE_KEYS}
        missing = {rk: max(0, cost[rk] - int(res_have.get(rk, 0) or 0)) for rk in ETA_RESOURCE_KEYS}
        missing_total = sum(missing.values())
        can_afford = missing_total == 0
        wait_h, bottleneck = compute_resource_eta(missing, rates_hr)
        if wait_h is not None:
            wait_h = cap_eta_float(wait_h)
        res_s = cap_eta_float(safe_finite_float(stats_next.get("base_time_s", 0.0), 0.0) / r_divisor)
        reach_h = cap_eta_float(wait_h + res_s / 3600.0) if wait_h is not None else None
        unmet = missing_prerequisites("research", rkey, buildings=target.buildings, researches=state.researches)

        r_horizons: list[dict[str, object]] = []
        cum_cost = {rk: 0 for rk in ETA_RESOURCE_KEYS}
        cum_res_s = 0.0
        for step_idx in range(min(3, safe_horizon)):
            step_lvl = nxt_lvl + step_idx
            step_stats = research_level_stats(info, step_lvl)
            step_cost_obj = step_stats.get("cost")
            step_raw: Mapping[str, object] = (
                cost if step_idx == 0 else (step_cost_obj if isinstance(step_cost_obj, Mapping) else {})
            )
            for rk in ETA_RESOURCE_KEYS:
                cum_cost[rk] += safe_nonneg_int(step_raw.get(rk, 0), 0)
            cum_missing = {rk: max(0, cum_cost[rk] - int(res_have.get(rk, 0) or 0)) for rk in ETA_RESOURCE_KEYS}
            step_wait_h, step_bn = compute_resource_eta(cum_missing, rates_hr)
            if step_wait_h is not None:
                step_wait_h = cap_eta_float(step_wait_h)
            step_s = cap_eta_float(safe_finite_float(step_stats.get("base_time_s", 0.0), 0.0) / r_divisor)
            cum_res_s = cap_eta_float(cum_res_s + step_s)
            step_reach_h = cap_eta_float(step_wait_h + cum_res_s / 3600.0) if step_wait_h is not None else None
            r_horizons.append(
                {
                    "level": cap_json_int_eta(step_lvl),
                    "cost": {rk: cap_json_int_eta(safe_nonneg_int(step_raw.get(rk, 0), 0)) for rk in ETA_RESOURCE_KEYS},
                    "build_time_text": format_duration(step_s),
                    "wait_eta": format_eta(step_wait_h),
                    "reach_eta": format_eta(step_reach_h),
                    "bottleneck": step_bn,
                }
            )

        rows.append(
            {
                "research_id": rid_num,
                "key": rkey,
                "slug": str(info["slug"]),
                "name": safe_eta_str(state.research_names.get(rkey)) or str(info["name"]),
                "current_level": cap_json_int_eta(cur_lvl),
                "next_level": cap_json_int_eta(nxt_lvl),
                "cost": {rk: cap_json_int_eta(v) for rk, v in cost.items()},
                "missing": {rk: cap_json_int_eta(v) for rk, v in missing.items()},
                "missing_total": cap_json_int_eta(missing_total),
                "can_afford": can_afford,
                "can_research_live": bool(offer.can_research and can_afford) if offer is not None else False,
                "active": bool(offer.active) if offer is not None else False,
                "offer_visible": offer is not None,
                "unmet_prerequisites": [str(u) for u in unmet],
                "wait_hours": round(wait_h, 3) if wait_h is not None else None,
                "wait_eta": format_eta(wait_h),
                "build_time_seconds": round(res_s, 1),
                "build_time_text": format_duration(res_s),
                "reach_hours": round(reach_h, 3) if reach_h is not None else None,
                "reach_eta": format_eta(reach_h),
                "bottleneck": bottleneck,
                "horizons": r_horizons,
            }
        )
    return rows
