"""Two-pass inter-planet transport selection, plan-order loans, cargo top-up, and action formatting."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from spaceinvasion.combat.cargo import (
    fleet_cargo_capacity,
    pack_transport_cargo,
    select_transport_ships,
    top_up_transport_cargo,
)
from spaceinvasion.combat.flight import (
    estimate_fleet_flight_seconds,
    estimate_resource_per_hour,
    format_flight_duration,
    normalize_coords,
)
from spaceinvasion.core import catalog
from spaceinvasion.core.model import GameState, PlanetState, Resources
from spaceinvasion.planner.eta import estimate_planet_hourly_economy
from spaceinvasion.planner.heuristics._common import (
    _safe_strategy_float,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._transport_helpers import (
    _DonorPools,
    _PlannedTrip,
    _PlanOrderContext,
    _TransportDemand,
    _TripCandidate,
)
from spaceinvasion.planner.heuristics._types import (
    _PLAN_ORDER_NO_RANK,
    AI_PLAN_ORDER_LOAN_TAG,
    PLAN_ORDER_LOAN_MIN_CARGO,
    PLAN_ORDER_LOAN_MIN_SHARE,
    PlannedAction,
)


def _evaluate_trip_candidate(
    state: GameState,
    donor: PlanetState,
    dest: PlanetState,
    ships_avail: Mapping[str, int],
    avail_vals: Sequence[int],
    need_res: Resources,
    dest_free: Resources,
    *,
    urgency: int,
    need_tot: int,
    miner_spec: int,
) -> _TripCandidate | None:
    """Evaluate a single donor->dest transport trip candidate and return throughput metrics."""
    max_cap = fleet_cargo_capacity(ships_avail, miner_spec_level=miner_spec)
    if max_cap <= 0:
        return None
    avail_res = Resources(avail_vals[0], avail_vals[1], avail_vals[2], avail_vals[3])
    cargo_c = pack_transport_cargo(need_res, avail_res, dest_free, max_cap)
    cargo_tot_c = cargo_c.pig_iron + cargo_c.metal + cargo_c.kryptonite + cargo_c.spice
    if cargo_tot_c <= 0:
        return None
    if cargo_tot_c < 500 and not (urgency <= 1 and cargo_tot_c >= need_tot):
        return None
    ships_c = select_transport_ships(ships_avail, cargo_tot_c, miner_spec_level=miner_spec)
    if not ships_c:
        return None
    flight_c = estimate_fleet_flight_seconds(
        donor.coords,
        dest.coords,
        ships_c,
        researches=state.researches,
        round_trip=True,
    )
    rph_c = estimate_resource_per_hour(cargo_tot_c, flight_c)
    return _TripCandidate(
        rph=rph_c,
        cargo_tot=cargo_tot_c,
        donor=donor,
        cargo=cargo_c,
        chosen_ships=ships_c,
        flight_s=flight_c,
    )


def _borrower_hourly_rates(state: GameState, dest: PlanetState) -> tuple[float, float, float, float]:
    econ_d = estimate_planet_hourly_economy(state, dest)
    return (
        max(0.0, _safe_strategy_float(econ_d.get("pig_iron_per_hr"), 0.0)),
        max(0.0, _safe_strategy_float(econ_d.get("metal_per_hr"), 0.0)),
        max(0.0, _safe_strategy_float(econ_d.get("kryptonite_per_hr"), 0.0)),
        max(0.0, _safe_strategy_float(econ_d.get("spice_per_hr"), 0.0)),
    )


def _pick_best_trip_for_demand(
    state: GameState,
    demand: _TransportDemand,
    dest_free: Resources,
    pools: _DonorPools,
    planet_by_id: Mapping[int, PlanetState],
    plan_ctx: _PlanOrderContext,
    *,
    miner_spec: int,
) -> tuple[_TripCandidate, bool] | None:
    """Pick the best spare-stock or Plan-v4 plan-order loan candidate for `demand`."""
    dest = demand.dest
    need_res = demand.need_res
    need_tot = need_res.pig_iron + need_res.metal + need_res.kryptonite + need_res.spice
    borrow_step = (
        plan_ctx.waiting_step_by_pid.get(dest.planet_id)
        if (plan_ctx.plan_order_on and demand.urgency == -1 and demand.need_by_sec <= 0.0)
        else None
    )
    dest_rank = plan_ctx.step_rank_by_pid.get(dest.planet_id, _PLAN_ORDER_NO_RANK)
    borrow_cap = (
        [
            min(need_res.pig_iron, max(0, _safe_strategy_int(borrow_step.missing.pig_iron, 0))),
            min(need_res.metal, max(0, _safe_strategy_int(borrow_step.missing.metal, 0))),
            min(need_res.kryptonite, max(0, _safe_strategy_int(borrow_step.missing.kryptonite, 0))),
            min(need_res.spice, max(0, _safe_strategy_int(borrow_step.missing.spice, 0))),
        ]
        if borrow_step is not None
        else None
    )
    borrower_rates = _borrower_hourly_rates(state, dest) if borrow_cap is not None else None
    loan_floor = min(need_tot, max(PLAN_ORDER_LOAN_MIN_CARGO, int(PLAN_ORDER_LOAN_MIN_SHARE * need_tot)))

    best_cand: _TripCandidate | None = None
    best_loan: _TripCandidate | None = None
    for d_pid, d_ships in pools.ships_rem.items():
        if d_pid == dest.planet_id or not d_ships:
            continue
        donor = planet_by_id[d_pid]
        surp_vals = pools.surplus_rem.get(d_pid, [0, 0, 0, 0])
        if sum(surp_vals) > 0:
            cand = _evaluate_trip_candidate(
                state,
                donor,
                dest,
                d_ships,
                surp_vals,
                need_res,
                dest_free,
                urgency=demand.urgency,
                need_tot=need_tot,
                miner_spec=miner_spec,
            )
            if cand is not None and (
                best_cand is None or (cand.rph, cand.cargo_tot) > (best_cand.rph, best_cand.cargo_tot)
            ):
                best_cand = cand
        lend_vals = pools.lend_rem.get(d_pid) if borrow_cap is not None else None
        if (
            lend_vals
            and borrow_cap is not None
            and plan_ctx.step_rank_by_pid.get(d_pid, _PLAN_ORDER_NO_RANK) > dest_rank
        ):
            loan_vals = [max(surp_vals[i], min(surp_vals[i] + lend_vals[i], borrow_cap[i])) for i in range(4)]
            if loan_vals != list(surp_vals):
                loan = _evaluate_trip_candidate(
                    state,
                    donor,
                    dest,
                    d_ships,
                    loan_vals,
                    need_res,
                    dest_free,
                    urgency=demand.urgency,
                    need_tot=need_tot,
                    miner_spec=miner_spec,
                )
                if loan is not None and borrower_rates is not None:
                    one_way_h = loan.flight_s / 7200.0
                    if all(borrow_cap[i] <= borrower_rates[i] * one_way_h for i in range(4) if borrow_cap[i] > 0):
                        loan = None
                if (
                    loan is not None
                    and loan.cargo_tot >= loan_floor
                    and (best_loan is None or (loan.cargo_tot, loan.rph) > (best_loan.cargo_tot, best_loan.rph))
                ):
                    best_loan = loan

    use_loan = best_loan is not None and (
        best_cand is None or (best_cand.cargo_tot < need_tot and best_loan.cargo_tot > best_cand.cargo_tot)
    )
    chosen = best_loan if use_loan else best_cand
    return (chosen, use_loan) if chosen is not None else None


def _deduct_pass1_trip(cand: _TripCandidate, use_loan: bool, pools: _DonorPools) -> int:
    """Deduct Pass-1 reserved ships, spare stock, and loan draw from donor pools; return `lent_tot`."""
    d_pid = cand.donor.planet_id
    d_rem_ships = pools.ships_rem[d_pid]
    for sk, sq in cand.chosen_ships.items():
        left_q = max(0, d_rem_ships.get(sk, 0) - sq)
        if left_q > 0:
            d_rem_ships[sk] = left_q
        else:
            d_rem_ships.pop(sk, None)
    d_rem_surp = pools.surplus_rem[d_pid]
    cargo = cand.cargo
    cargo_vals = (cargo.pig_iron, cargo.metal, cargo.kryptonite, cargo.spice)
    lent_tot = 0
    if use_loan:
        d_rem_lend = pools.lend_rem[d_pid]
        for idx_r in range(4):
            lent_r = max(0, cargo_vals[idx_r] - d_rem_surp[idx_r])
            d_rem_lend[idx_r] = max(0, d_rem_lend[idx_r] - lent_r)
            lent_tot += lent_r
    d_rem_surp[0] = max(0, d_rem_surp[0] - cargo.pig_iron)
    d_rem_surp[1] = max(0, d_rem_surp[1] - cargo.metal)
    d_rem_surp[2] = max(0, d_rem_surp[2] - cargo.kryptonite)
    d_rem_surp[3] = max(0, d_rem_surp[3] - cargo.spice)
    return lent_tot


def _plan_pass1_trips(
    state: GameState,
    demands: Sequence[_TransportDemand],
    pools: _DonorPools,
    planet_by_id: Mapping[int, PlanetState],
    plan_ctx: _PlanOrderContext,
    *,
    miner_spec: int,
) -> list[_PlannedTrip]:
    """Pass 1: satisfy concrete requested shortfalls in strict priority order first."""
    planned_trips: list[_PlannedTrip] = []
    for demand in demands:
        dest = demand.dest
        need_res = demand.need_res
        cap_ratio = demand.cap_ratio
        dest_free = Resources(
            pig_iron=max(0, int(dest.storage.pig_iron * cap_ratio) - max(0, dest.resources.pig_iron))
            if dest.storage.pig_iron > 0
            else need_res.pig_iron,
            metal=max(0, int(dest.storage.metal * cap_ratio) - max(0, dest.resources.metal))
            if dest.storage.metal > 0
            else need_res.metal,
            kryptonite=max(0, int(dest.storage.kryptonite * cap_ratio) - max(0, dest.resources.kryptonite))
            if dest.storage.kryptonite > 0
            else need_res.kryptonite,
            spice=max(0, int(dest.storage.spice * cap_ratio) - max(0, dest.resources.spice))
            if dest.storage.spice > 0
            else need_res.spice,
        )
        picked = _pick_best_trip_for_demand(
            state,
            demand,
            dest_free,
            pools,
            planet_by_id,
            plan_ctx,
            miner_spec=miner_spec,
        )
        if picked is None:
            continue
        best_cand, use_loan = picked
        lent_tot = _deduct_pass1_trip(best_cand, use_loan, pools)
        planned_trips.append(
            _PlannedTrip(
                donor=best_cand.donor,
                dest=dest,
                need_res=need_res,
                need_label=demand.need_label,
                dest_free=dest_free,
                cargo=best_cand.cargo,
                chosen_ships=dict(best_cand.chosen_ships),
                lent_tot=lent_tot,
                lender_rank=plan_ctx.step_rank_by_pid.get(best_cand.donor.planet_id),
                borrower_rank=plan_ctx.step_rank_by_pid.get(dest.planet_id),
            )
        )
    return planned_trips


def _top_up_trip_cargo(
    trip: _PlannedTrip,
    pools: _DonorPools,
    *,
    miner_spec: int,
    fuel_reserve: int,
) -> tuple[Resources, dict[str, int], int, int]:
    """Pass 2: top up a planned transport with remaining spare donor resources."""
    cargo = trip.cargo
    chosen_ships = trip.chosen_ships
    orig_cargo_tot = cargo.pig_iron + cargo.metal + cargo.kryptonite + cargo.spice
    d_pid = trip.donor.planet_id
    d_rem_ships = pools.ships_rem.get(d_pid, {})
    d_rem_surp = pools.surplus_rem.get(d_pid, [0, 0, 0, 0])
    if sum(d_rem_surp) <= 0:
        return cargo, chosen_ships, orig_cargo_tot, 0

    avail_ships = {
        k: chosen_ships.get(k, 0) + d_rem_ships.get(k, 0)
        for k in ("s3", "s2", "s1")
        if chosen_ships.get(k, 0) + d_rem_ships.get(k, 0) > 0
    }
    raw_fleet_cap = fleet_cargo_capacity(avail_ships, miner_spec_level=miner_spec)
    hold_fuel_buf = min(raw_fleet_cap // 10, max(fuel_reserve, int(raw_fleet_cap * 0.03))) if raw_fleet_cap > 0 else 0
    max_usable_cap = max(orig_cargo_tot, raw_fleet_cap - hold_fuel_buf)
    if max_usable_cap <= orig_cargo_tot:
        return cargo, chosen_ships, orig_cargo_tot, 0

    rem_surp_res = Resources(d_rem_surp[0], d_rem_surp[1], d_rem_surp[2], d_rem_surp[3])
    topped = top_up_transport_cargo(cargo, trip.need_res, rem_surp_res, trip.dest_free, max_usable_cap)
    topped_tot = topped.pig_iron + topped.metal + topped.kryptonite + topped.spice
    if topped_tot <= orig_cargo_tot:
        return cargo, chosen_ships, orig_cargo_tot, 0

    new_ships = select_transport_ships(avail_ships, topped_tot, miner_spec_level=miner_spec)
    if not new_ships:
        return cargo, chosen_ships, orig_cargo_tot, 0
    new_cap = fleet_cargo_capacity(new_ships, miner_spec_level=miner_spec)
    new_buf = min(new_cap // 10, max(fuel_reserve, int(new_cap * 0.03))) if new_cap > 0 else 0
    if topped_tot > max(orig_cargo_tot, new_cap - new_buf):
        cand_ships = select_transport_ships(avail_ships, topped_tot + new_buf, miner_spec_level=miner_spec)
        if cand_ships:
            new_ships = cand_ships
            new_cap = fleet_cargo_capacity(new_ships, miner_spec_level=miner_spec)
            new_buf = min(new_cap // 10, max(fuel_reserve, int(new_cap * 0.03))) if new_cap > 0 else 0
        if topped_tot > max(orig_cargo_tot, new_cap - new_buf):
            capped_usable = max(orig_cargo_tot, new_cap - new_buf)
            topped = top_up_transport_cargo(cargo, trip.need_res, rem_surp_res, trip.dest_free, capped_usable)
            topped_tot = topped.pig_iron + topped.metal + topped.kryptonite + topped.spice
    if topped_tot <= orig_cargo_tot:
        return cargo, chosen_ships, orig_cargo_tot, 0

    d_rem_surp[0] = max(0, d_rem_surp[0] - max(0, topped.pig_iron - cargo.pig_iron))
    d_rem_surp[1] = max(0, d_rem_surp[1] - max(0, topped.metal - cargo.metal))
    d_rem_surp[2] = max(0, d_rem_surp[2] - max(0, topped.kryptonite - cargo.kryptonite))
    d_rem_surp[3] = max(0, d_rem_surp[3] - max(0, topped.spice - cargo.spice))
    pools.ships_rem[d_pid] = {
        sk: left_q for sk, tot_q in avail_ships.items() if (left_q := max(0, tot_q - new_ships.get(sk, 0))) > 0
    }
    return topped, new_ships, topped_tot, topped_tot - orig_cargo_tot


def _format_transport_action(
    state: GameState,
    trip: _PlannedTrip,
    cargo: Resources,
    chosen_ships: Mapping[str, int],
    cargo_tot: int,
    extra_added: int,
) -> PlannedAction:
    """Format a finalized inter-planet `TRANSPORT` `PlannedAction`."""
    flight_s = estimate_fleet_flight_seconds(
        trip.donor.coords,
        trip.dest.coords,
        chosen_ships,
        researches=state.researches,
        round_trip=True,
    )
    rph = estimate_resource_per_hour(cargo_tot, flight_s)
    norm_dest = normalize_coords(trip.dest.coords)
    ship_desc = ", ".join(f"{q}x {k}" for k, q in chosen_ships.items())
    ow_txt = format_flight_duration(flight_s / 2.0)
    dur_txt = format_flight_duration(flight_s)
    rph_txt = catalog.fmt_compact_int(round(rph), grouped=True)
    parts_r: list[str] = []
    if cargo.pig_iron > 0:
        parts_r.append(f"{catalog.fmt_compact_int(cargo.pig_iron, grouped=True)}P")
    if cargo.metal > 0:
        parts_r.append(f"{catalog.fmt_compact_int(cargo.metal, grouped=True)}M")
    if cargo.kryptonite > 0:
        parts_r.append(f"{catalog.fmt_compact_int(cargo.kryptonite, grouped=True)}K")
    if cargo.spice > 0:
        parts_r.append(f"{catalog.fmt_compact_int(cargo.spice, grouped=True)}S")
    res_summary = "/".join(parts_r)
    fill_note = f" (+{catalog.fmt_compact_int(extra_added, grouped=True)} spare fill)" if extra_added > 0 else ""
    loan_note = (
        (
            f" {AI_PLAN_ORDER_LOAN_TAG} lends {catalog.fmt_compact_int(trip.lent_tot, grouped=True)} "
            f"held for its own step #{trip.lender_rank} to step #{trip.borrower_rank}"
        )
        if trip.lent_tot > 0
        else ""
    )
    return PlannedAction(
        priority="P1",
        kind="TRANSPORT",
        planet_id=trip.donor.planet_id,
        planet_coords=_safe_strategy_str(trip.donor.coords),
        target_id=trip.dest.planet_id,
        target_level=None,
        target_name=f"Transport -> {norm_dest} ({trip.dest.name})",
        cost=cargo,
        missing=Resources(),
        reason=(
            f"Supply {trip.dest.name} [{norm_dest}] ({res_summary} · "
            f"{catalog.fmt_compact_int(cargo_tot, grouped=True)} res"
            f"{fill_note} / 1-way {ow_txt} · RT {dur_txt}, {rph_txt} res/h via {ship_desc}) for {trip.need_label}"
            f"{loan_note}"
        ),
        executable_now=True,
    )
