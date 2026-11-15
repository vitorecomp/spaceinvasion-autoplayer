"""Inter-planet `TRANSPORT` and one-way `STATION` deployment planners."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from backend.combat.flight import (
    estimate_fleet_flight_seconds,
    format_flight_duration,
    format_station_ships_spec,
    normalize_coords,
)
from backend.core.config import StrategyConfig
from backend.core.model import (
    GameState,
    PlanetState,
    extract_in_flight_station_dest_coords,
    is_undersized_fresh_colony,
)
from backend.planner.heuristics._common import (
    _norm_planet_type,
    _safe_strategy_int,
    _safe_strategy_str,
)
from backend.planner.heuristics._transport_dispatch import (
    _format_transport_action,
    _plan_pass1_trips,
    _top_up_trip_cargo,
)
from backend.planner.heuristics._transport_helpers import (
    _build_donor_pools,
    _build_plan_order_context,
    _collect_inbound_transport_coords,
    _collect_p01_reserved_by_planet,
    _collect_transport_demands,
    _normalize_priority_needs,
)
from backend.planner.heuristics._types import (
    _AI_PLAN_STEP_RANK_RE,
    AI_PLAN_ORDER_LOAN_TAG,
    FLEET_BASE,
    PLAN_ORDER_LOAN_MIN_CARGO,
    PLAN_ORDER_LOAN_MIN_SHARE,
    WEAPONS_FACTORY,
    PlannedAction,
)

__all__ = [
    "AI_PLAN_ORDER_LOAN_TAG",
    "PLAN_ORDER_LOAN_MIN_CARGO",
    "PLAN_ORDER_LOAN_MIN_SHARE",
    "_AI_PLAN_STEP_RANK_RE",
    "plan_interplanet_stationing",
    "plan_interplanet_transports",
]

_STATION_COMBAT_KEYS: tuple[str, ...] = ("s4", "s1", "s2", "s5", "s6", "s7", "s13", "s14", "s15", "s16")


def plan_interplanet_transports(
    state: GameState,
    cfg: StrategyConfig,
    existing_plan: Sequence[PlannedAction] = (),
    *,
    now_ts: float | None = None,
    priority_resource_need: Mapping[int, object] | None = None,
) -> list[PlannedAction]:
    """Plan own-planet resource `TRANSPORT` shipments (Fleet Priority #2: after `SPY`, before `COLONIZE`).

    With `ai_adaptive_execution` on (Plan v4 plan-order pooling), AI-Planner needs are served in plan
    order, and a colony whose own next plan step still waits for resources lends what it holds back for
    that step to a colony whose waiting step comes earlier in the plan (`AI_PLAN_ORDER_LOAN_TAG` in the
    reason). A loan is only used when spare stock cannot cover the need, it covers only the borrower's
    own waiting building step, and it never touches what the lender keeps for a step it can already pay,
    for research, for the cushion, or for fuel. It is not flown for an amount the borrower makes itself
    before the cargo could land.
    """
    del now_ts
    if not getattr(cfg, "allow_transport", False) or not getattr(cfg, "allow_multi_planet", True):
        return []
    normal_planets = [p for p in (state.planets or ()) if _norm_planet_type(p) not in ("asteroid", "raid")]
    if len(normal_planets) < 2:
        return []

    miner_spec = _safe_strategy_int((getattr(state, "specs", None) or {}).get("miner", 0), 0)
    fuel_reserve = max(500, _safe_strategy_int(getattr(cfg, "fleet_save_fuel_reserve_spice", 500), 500))
    norm_prio_needs, norm_prio_meta = _normalize_priority_needs(priority_resource_need)
    inbound_coords = _collect_inbound_transport_coords(state)
    p01_reserved = _collect_p01_reserved_by_planet(existing_plan)
    plan_ctx = _build_plan_order_context(cfg, existing_plan, norm_prio_needs)
    demands = _collect_transport_demands(
        normal_planets,
        existing_plan,
        inbound_coords,
        norm_prio_needs,
        norm_prio_meta,
        plan_ctx,
    )
    if not demands:
        return []

    pools = _build_donor_pools(
        normal_planets,
        p01_reserved,
        norm_prio_needs,
        plan_ctx.lendable_cost_by_pid,
        fuel_reserve,
    )
    if not pools.ships_rem:
        return []

    planet_by_id = {p.planet_id: p for p in normal_planets}
    planned_trips = _plan_pass1_trips(state, demands, pools, planet_by_id, plan_ctx, miner_spec=miner_spec)
    out_transports: list[PlannedAction] = []
    for trip in planned_trips:
        cargo, chosen_ships, cargo_tot, extra_added = _top_up_trip_cargo(
            trip,
            pools,
            miner_spec=miner_spec,
            fuel_reserve=fuel_reserve,
        )
        out_transports.append(_format_transport_action(state, trip, cargo, chosen_ships, cargo_tot, extra_added))
    return out_transports


def _planet_rec_count(p: PlanetState) -> int:
    return max(0, _safe_strategy_int((p.ships or {}).get("s11", 0), 0))


def _planet_combat_total(p: PlanetState) -> int:
    return sum(max(0, _safe_strategy_int((p.ships or {}).get(k, 0), 0)) for k in _STATION_COMBAT_KEYS)


def _build_station_pools(
    donors: Sequence[PlanetState],
) -> tuple[dict[int, dict[str, int]], dict[int, dict[str, int]]]:
    donor_pool: dict[int, dict[str, int]] = {
        d.planet_id: {
            k: max(0, _safe_strategy_int((d.ships or {}).get(k, 0), 0)) for k in ("s11", *_STATION_COMBAT_KEYS)
        }
        for d in donors
    }
    donor_keep_min: dict[int, dict[str, int]] = {
        d.planet_id: {
            "s11": max(2, _planet_rec_count(d) // 3) if _planet_rec_count(d) >= 4 else _planet_rec_count(d),
            **{
                k: max(
                    (2 if k in ("s1", "s2") else 3),
                    (max(0, _safe_strategy_int((d.ships or {}).get(k, 0), 0)) + 1) // 2,
                )
                for k in _STATION_COMBAT_KEYS
            },
        }
        for d in donors
    }
    return donor_pool, donor_keep_min


def _pick_station_donor(
    state: GameState,
    dest: PlanetState,
    norm_dest: str,
    donors: Sequence[PlanetState],
    donor_pool: Mapping[int, Mapping[str, int]],
    donor_keep_min: Mapping[int, Mapping[str, int]],
    *,
    want_rec: bool,
    want_atk: bool,
    needy_rec_count: int,
    needy_atk_count: int,
) -> tuple[PlanetState, dict[str, int]] | None:
    """Pick the best donor hub with highest surplus ships (preferring shipyard hub & closest distance)."""
    best_donor: PlanetState | None = None
    best_bundle: dict[str, int] = {}
    best_key: tuple[int, int, float] = (0, 0, float("inf"))
    for d in donors:
        if d.planet_id == dest.planet_id:
            continue
        pool = donor_pool[d.planet_id]
        keep = donor_keep_min[d.planet_id]
        bundle: dict[str, int] = {}
        if want_rec:
            surplus_rec = max(0, pool.get("s11", 0) - keep.get("s11", 2))
            if surplus_rec >= 2:
                rec_share = min(
                    surplus_rec,
                    max(2, pool.get("s11", 0) // max(2, 1 + max(1, needy_rec_count))),
                )
                if rec_share >= 2:
                    bundle["s11"] = rec_share
        if want_atk:
            for k in _STATION_COMBAT_KEYS:
                cur_k = pool.get(k, 0)
                min_k = keep.get(k, cur_k)
                surplus_k = max(0, cur_k - min_k)
                min_send_k = 2 if k in ("s1", "s2") else 3
                if surplus_k >= min_send_k:
                    share_k = min(
                        surplus_k,
                        max(min_send_k, cur_k // max(2, 1 + max(1, needy_atk_count))),
                    )
                    if share_k >= min_send_k:
                        bundle[k] = share_k
        if not bundle:
            continue
        flight_s = estimate_fleet_flight_seconds(
            d.coords,
            norm_dest,
            bundle,
            researches=state.researches,
            round_trip=False,
        )
        score_k = (-sum(bundle.values()), 0 if d.building_level(WEAPONS_FACTORY) >= 1 else 1, flight_s)
        if best_donor is None or score_k < best_key:
            best_donor = d
            best_bundle = bundle
            best_key = score_k
    return (best_donor, best_bundle) if (best_donor is not None and best_bundle) else None


def _format_station_action(
    state: GameState,
    donor: PlanetState,
    dest: PlanetState,
    norm_dest: str,
    bundle: Mapping[str, int],
) -> PlannedAction:
    ship_spec = format_station_ships_spec(bundle)
    flight_s = estimate_fleet_flight_seconds(
        donor.coords,
        norm_dest,
        bundle,
        researches=state.researches,
        round_trip=False,
    )
    dur_txt = format_flight_duration(flight_s)
    dest_slots = 1 + max(0, dest.building_level(FLEET_BASE))
    return PlannedAction(
        priority="P2",
        kind="STATION",
        planet_id=donor.planet_id,
        planet_coords=_safe_strategy_str(donor.coords),
        target_id=dest.planet_id,
        target_level=sum(bundle.values()),
        target_name=f"Station -> {norm_dest} ({dest.name}) [{ship_spec}]",
        reason=(
            f"One-way deploy {ship_spec} to {dest.name} [{norm_dest}] "
            f"(b10 L{dest.building_level(FLEET_BASE)}, {dest_slots} independent flight slots · {dur_txt} one-way)"
        ),
        executable_now=True,
    )


def _select_station_recipients_and_donors(
    state: GameState,
    cfg: StrategyConfig,
) -> tuple[list[PlanetState], list[PlanetState]]:
    min_colony_fields = max(50, _safe_strategy_int(getattr(cfg, "min_colony_fields", 300), 300))
    undersized_pids = {p.planet_id for p in state.planets if is_undersized_fresh_colony(p, state, min_colony_fields)}
    eligible_planets = [
        p for p in state.planets if _norm_planet_type(p) != "raid" and p.planet_id not in undersized_pids
    ]
    if len(eligible_planets) < 2:
        return [], []
    in_flight_station_dests = extract_in_flight_station_dest_coords(state)
    recipients = [
        p
        for p in eligible_planets
        if (norm_p := normalize_coords(p.coords))
        and norm_p not in in_flight_station_dests
        and p.building_level(FLEET_BASE) >= 1
        and (_planet_rec_count(p) < 2 or _planet_combat_total(p) == 0)
    ]
    if not recipients:
        return [], []
    recipients.sort(
        key=lambda p: (-p.building_level(FLEET_BASE), _planet_rec_count(p) + _planet_combat_total(p), p.planet_id)
    )
    donors = [
        p
        for p in eligible_planets
        if max(0, _safe_strategy_int(p.resources.spice, 0)) >= 500
        and (
            _planet_rec_count(p) >= 4
            or any(
                max(0, _safe_strategy_int((p.ships or {}).get(k, 0), 0)) >= (4 if k in ("s1", "s2") else 6)
                for k in _STATION_COMBAT_KEYS
            )
        )
    ]
    return recipients, donors


def plan_interplanet_stationing(
    state: GameState,
    cfg: StrategyConfig,
    existing_plan: Sequence[PlannedAction] = (),
    *,
    now_ts: float | None = None,
) -> list[PlannedAction]:
    """Plan one-way `STATION` ship deployments from centralized shipyard hubs to colonies with `Fleet base` (`b10 >= 1`).

    Allows initial `Weapons factory` (`b9`) production to stay centralized on a hub planet while
    deploying Recyclers (`s11`) and raid/combat ships (`s4`, `s1`, `s2`, `s5`, `s6`, `s7`, `s13`–`s16`)
    in one-way trips to other owned colonies so each colony utilizes its independent per-planet
    `Fleet base` (`b10`) slots for parallel `ATTACK` and `RECYCLE` missions.
    """
    _ = existing_plan, now_ts
    if not getattr(cfg, "allow_transport", False) or not getattr(cfg, "allow_multi_planet", True):
        return []
    if len(state.planets or ()) < 2:
        return []
    if state.tutorial is not None and not state.tutorial.can_complete:
        return []
    if any(bool(getattr(fa, "is_hostile", False)) for fa in (state.fleet_alerts or ())):
        return []

    recipients, donors = _select_station_recipients_and_donors(state, cfg)
    if not recipients or not donors:
        return []

    donor_pool, donor_keep_min = _build_station_pools(donors)
    needy_rec_count = sum(1 for r in recipients if _planet_rec_count(r) < 2)
    needy_atk_count = sum(1 for r in recipients if _planet_combat_total(r) == 0)
    out_stations: list[PlannedAction] = []
    for dest in recipients:
        if len(out_stations) >= 3:
            break
        norm_dest = normalize_coords(dest.coords)
        picked = _pick_station_donor(
            state,
            dest,
            norm_dest,
            donors,
            donor_pool,
            donor_keep_min,
            want_rec=_planet_rec_count(dest) < 2,
            want_atk=_planet_combat_total(dest) == 0,
            needy_rec_count=needy_rec_count,
            needy_atk_count=needy_atk_count,
        )
        if picked is None:
            continue
        best_donor, best_bundle = picked
        for skey, qty in best_bundle.items():
            donor_pool[best_donor.planet_id][skey] = max(0, donor_pool[best_donor.planet_id].get(skey, 0) - qty)
        if "s11" in best_bundle:
            needy_rec_count = max(0, needy_rec_count - 1)
        if any(k in best_bundle for k in _STATION_COMBAT_KEYS):
            needy_atk_count = max(0, needy_atk_count - 1)
        out_stations.append(_format_station_action(state, best_donor, dest, norm_dest, best_bundle))
    return out_stations
