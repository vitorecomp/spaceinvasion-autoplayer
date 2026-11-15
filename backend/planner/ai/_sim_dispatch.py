"""Coordinate-aware donor-wave cargo dispatch algorithm for ``TransportScheduler``."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from backend.planner.ai._sim_types import SimProblemData, TransportRecord, Vec4
from backend.planner.ai._types import PATH_NEVER

DonorCandidate = tuple[int, float, float, float, float, float]


class TransportDispatchTarget(Protocol):
    """Subset of ``TransportScheduler`` mutated during donor cargo dispatch."""

    travel_hours: list[list[float]]
    lead_hours: list[list[float]]
    donors_by_dst: list[list[int]]
    in_transit_to: list[list[float]]
    early_dispatch_at: float
    step_transport_info: dict[int, TransportRecord]
    planet_earmark_step: list[int]

    def enqueue_batch(
        self,
        dst: int,
        cargo: Vec4,
        *,
        clock: float,
        sources: Sequence[str],
        max_travel_hr: float,
        max_lead_hr: float,
        step: int,
        is_early: bool,
    ) -> None: ...


def _compute_reserved_surplus(
    data: SimProblemData,
    transport: TransportDispatchTarget,
    reserved: int,
    stock: Sequence[float],
) -> tuple[int, Vec4] | None:
    res_dst = data.pay_planet[reserved]
    rc0, rc1, rc2, rc3 = data.cost[reserved]
    in_res = transport.in_transit_to[res_dst]
    gs0 = max(0.0, (stock[0] + in_res[0]) - rc0)
    gs1 = max(0.0, (stock[1] + in_res[1]) - rc1)
    gs2 = max(0.0, (stock[2] + in_res[2]) - rc2)
    gs3 = max(0.0, (stock[3] + in_res[3]) - rc3)
    if gs0 + gs1 + gs2 + gs3 <= 1e-6:
        return None
    return res_dst, (gs0, gs1, gs2, gs3)


def _collect_eligible_donors(
    data: SimProblemData,
    transport: TransportDispatchTarget,
    *,
    dst: int,
    clock: float,
    free_in: float,
    protect_reserved: bool,
    res_dst: int,
    planet_stock: Sequence[Sequence[float]],
    started: Sequence[bool],
) -> list[DonorCandidate]:
    elig_donors: list[DonorCandidate] = []
    lead_mat = transport.lead_hours
    earmarks = transport.planet_earmark_step
    costs_all = data.cost
    earliest_future_req = PATH_NEVER
    for src in transport.donors_by_dst[dst]:
        if protect_reserved and src == res_dst:
            continue
        ps = planet_stock[src]
        a0, a1, a2, a3 = ps[0], ps[1], ps[2], ps[3]
        if protect_reserved:
            ek_src = earmarks[src]
            if ek_src >= 0:
                if started[ek_src]:
                    earmarks[src] = -1
                else:
                    ec0, ec1, ec2, ec3 = costs_all[ek_src]
                    a0 = a0 - ec0 if a0 > ec0 else 0.0
                    a1 = a1 - ec1 if a1 > ec1 else 0.0
                    a2 = a2 - ec2 if a2 > ec2 else 0.0
                    a3 = a3 - ec3 if a3 > ec3 else 0.0
        if a0 + a1 + a2 + a3 <= 1e-6:
            continue
        lead = lead_mat[src][dst]
        if free_in > lead + 1e-6:
            req_at = clock + free_in - lead
            if req_at > clock + 1e-6 and req_at < earliest_future_req:
                earliest_future_req = req_at
            continue
        elig_donors.append((src, lead, a0, a1, a2, a3))
    if earliest_future_req < transport.early_dispatch_at:
        transport.early_dispatch_at = earliest_future_req
    return elig_donors


def _select_donor_wave(
    elig_donors: Sequence[DonorCandidate],
    *,
    cost: Vec4,
    tol: Vec4,
    have: Vec4,
    lr: Vec4,
    free_in: float,
    protect_reserved: bool,
    gs: Vec4,
) -> tuple[int, Vec4]:
    pa0 = pa1 = pa2 = pa3 = 0.0
    c0, c1, c2, c3 = cost
    t0, t1, t2, t3 = tol
    h0, h1, h2, h3 = have
    lr0, lr1, lr2, lr3 = lr
    gs0, gs1, gs2, gs3 = gs
    for idx, (_src, lead, a0, a1, a2, a3) in enumerate(elig_donors):
        pa0 += a0
        pa1 += a1
        pa2 += a2
        pa3 += a3
        if protect_reserved:
            if pa0 > gs0:
                pa0 = gs0
            if pa1 > gs1:
                pa1 = gs1
            if pa2 > gs2:
                pa2 = gs2
            if pa3 > gs3:
                pa3 = gs3
        eff_wait = free_in if free_in > lead else lead
        r0 = max(0.0, c0 - (h0 + lr0 * eff_wait))
        r1 = max(0.0, c1 - (h1 + lr1 * eff_wait))
        r2 = max(0.0, c2 - (h2 + lr2 * eff_wait))
        r3 = max(0.0, c3 - (h3 + lr3 * eff_wait))
        if r0 <= t0 and r1 <= t1 and r2 <= t2 and r3 <= t3:
            return -1, (0.0, 0.0, 0.0, 0.0)
        if pa0 + t0 >= r0 and pa1 + t1 >= r1 and pa2 + t2 >= r2 and pa3 + t3 >= r3:
            return idx, (r0, r1, r2, r3)
    return -1, (0.0, 0.0, 0.0, 0.0)


def _extract_donor_cargo(
    data: SimProblemData,
    transport: TransportDispatchTarget,
    selected: Sequence[DonorCandidate],
    *,
    dst: int,
    req: Vec4,
    protect_reserved: bool,
    gs: Vec4,
    stock: list[float],
    planet_stock: list[list[float]],
) -> tuple[Vec4, list[str], float, float]:
    rq = [req[0], req[1], req[2], req[3]]
    gs_l = [gs[0], gs[1], gs[2], gs[3]]
    batch = [0.0, 0.0, 0.0, 0.0]
    used_sources: list[str] = []
    max_lead_used = 0.0
    max_travel_used = 0.0
    travel_mat = transport.travel_hours
    earmarks = transport.planet_earmark_step
    planets_meta = data.planets
    for d_src, d_lead, a0, a1, a2, a3 in selected:
        avail = (a0, a1, a2, a3)
        ps = planet_stock[d_src]
        shipped = [0.0, 0.0, 0.0, 0.0]
        for pos in range(4):
            if rq[pos] > 1e-9:
                a_val = avail[pos]
                cap = a_val if (not protect_reserved or a_val <= gs_l[pos]) else gs_l[pos]
                take = rq[pos] if rq[pos] <= cap else cap
                if take > 1e-9:
                    shipped[pos] = take
                    rq[pos] = rq[pos] - take if rq[pos] > take else 0.0
                    ps[pos] = ps[pos] - take if ps[pos] > take else 0.0
                    stock[pos] = stock[pos] - take if stock[pos] > take else 0.0
                    if protect_reserved:
                        gs_l[pos] = gs_l[pos] - take if gs_l[pos] > take else 0.0
        if shipped[0] + shipped[1] + shipped[2] + shipped[3] > 1e-9:
            batch[0] += shipped[0]
            batch[1] += shipped[1]
            batch[2] += shipped[2]
            batch[3] += shipped[3]
            used_sources.append(str(planets_meta[d_src]["coords"]))
            if d_lead > max_lead_used:
                max_lead_used = d_lead
            tr_hr = travel_mat[d_src][dst]
            if tr_hr > max_travel_used:
                max_travel_used = tr_hr
            if not protect_reserved and earmarks[d_src] >= 0:
                earmarks[d_src] = -1
    return (batch[0], batch[1], batch[2], batch[3]), used_sources, max_travel_used, max_lead_used


def _prepare_step_need(
    data: SimProblemData,
    transport: TransportDispatchTarget,
    step: int,
    *,
    clock: float,
    smelting: bool,
    planet_stock: Sequence[Sequence[float]],
    started: Sequence[bool],
    planet_rate_off: Sequence[Vec4],
    planet_rate_on: Sequence[Vec4],
    free_in: float,
    protect_reserved: bool,
) -> tuple[int, Vec4, Vec4, Vec4, Vec4] | None:
    dst = data.pay_planet[step]
    earmarks = transport.planet_earmark_step
    if protect_reserved:
        ek_dst = earmarks[dst]
        if ek_dst >= 0:
            if started[ek_dst]:
                earmarks[dst] = -1
            elif ek_dst != step:
                return None
    existing = transport.step_transport_info.get(step)
    existing_rem = (existing.latest_arrival_hr - clock) if existing is not None else 0.0
    if existing is not None and existing_rem > 1e-6 and protect_reserved:
        return None
    c0, c1, c2, c3 = data.cost[step]
    f0, f1, f2, f3 = data.cost_floor[step]
    tol: Vec4 = (c0 - f0, c1 - f1, c2 - f2, c3 - f3)
    ps_dst, in_dst = planet_stock[dst], transport.in_transit_to[dst]
    h0, h1, h2, h3 = ps_dst[0] + in_dst[0], ps_dst[1] + in_dst[1], ps_dst[2] + in_dst[2], ps_dst[3] + in_dst[3]
    if h0 >= f0 and h1 >= f1 and h2 >= f2 and h3 >= f3:
        return None
    raw_lr = planet_rate_on[dst] if (h1 < f1 or smelting) else planet_rate_off[dst]
    lr: Vec4 = (max(0.0, raw_lr[0]), max(0.0, raw_lr[1]), max(0.0, raw_lr[2]), max(0.0, raw_lr[3]))
    check_wait = free_in if free_in > existing_rem else existing_rem
    if check_wait > 0.0 and (
        h0 + lr[0] * check_wait >= f0
        and h1 + lr[1] * check_wait >= f1
        and h2 + lr[2] * check_wait >= f2
        and h3 + lr[3] * check_wait >= f3
    ):
        return None
    return dst, (c0, c1, c2, c3), tol, (h0, h1, h2, h3), lr


def dispatch_transports_for_step(
    data: SimProblemData,
    transport: TransportDispatchTarget,
    step: int,
    *,
    clock: float,
    smelting: bool,
    reserved: int,
    stock: list[float],
    planet_stock: list[list[float]],
    started: Sequence[bool],
    planet_rate_off: Sequence[Vec4],
    planet_rate_on: Sequence[Vec4],
    free_in: float = 0.0,
    protect_reserved: bool = False,
) -> bool:
    """Dispatch the minimal nearest-donor transport wave needed for ``step`` on its pay planet."""
    if not data.model_transport_travel:
        return False
    need = _prepare_step_need(
        data,
        transport,
        step,
        clock=clock,
        smelting=smelting,
        planet_stock=planet_stock,
        started=started,
        planet_rate_off=planet_rate_off,
        planet_rate_on=planet_rate_on,
        free_in=free_in,
        protect_reserved=protect_reserved,
    )
    if need is None:
        return False
    dst, cost, tol, have, lr = need
    res_dst, gs = -1, (PATH_NEVER, PATH_NEVER, PATH_NEVER, PATH_NEVER)
    if protect_reserved and reserved >= 0:
        surplus = _compute_reserved_surplus(data, transport, reserved, stock)
        if surplus is None:
            return False
        res_dst, gs = surplus
    elig_donors = _collect_eligible_donors(
        data,
        transport,
        dst=dst,
        clock=clock,
        free_in=free_in,
        protect_reserved=protect_reserved,
        res_dst=res_dst,
        planet_stock=planet_stock,
        started=started,
    )
    if not elig_donors:
        return False
    chosen_idx, req = _select_donor_wave(
        elig_donors, cost=cost, tol=tol, have=have, lr=lr, free_in=free_in, protect_reserved=protect_reserved, gs=gs
    )
    if chosen_idx < 0:
        return False
    cargo, used_sources, max_travel_used, max_lead_used = _extract_donor_cargo(
        data,
        transport,
        elig_donors[: chosen_idx + 1],
        dst=dst,
        req=req,
        protect_reserved=protect_reserved,
        gs=gs,
        stock=stock,
        planet_stock=planet_stock,
    )
    if cargo[0] + cargo[1] + cargo[2] + cargo[3] <= 1e-9 or not used_sources:
        return False
    transport.enqueue_batch(
        dst,
        cargo,
        clock=clock,
        sources=used_sources,
        max_travel_hr=max_travel_used,
        max_lead_hr=max_lead_used,
        step=step,
        is_early=(free_in > 0.0),
    )
    return True
