"""Internal data structures and demand/pool builders for inter-planet resource transports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re

from spaceinvasion.combat.flight import normalize_coords
from spaceinvasion.core import catalog
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, PlanetState, Resources, is_in_flight_fleet_job
from spaceinvasion.planner.heuristics._cargo_helpers import _adaptive_execution_enabled
from spaceinvasion.planner.heuristics._common import (
    _resources_from_dict,
    _safe_strategy_int,
    _safe_strategy_str,
)
from spaceinvasion.planner.heuristics._types import (
    _AI_PLAN_STEP_RANK_RE,
    _PLAN_ORDER_NO_RANK,
    PlannedAction,
)

_NON_RESOURCE_ACTION_KINDS: frozenset[str] = frozenset({"TRANSPORT", "STATION", "ATTACK", "RECYCLE", "SPY", "COLONIZE"})
_DEMAND_ACTION_KINDS: frozenset[str] = frozenset({"BUILD", "WAIT_RESOURCES", "BUILD_SHIP", "RESEARCH"})
_RES_KEYS: tuple[str, ...] = ("pig_iron", "metal", "kryptonite", "spice")


@dataclass(frozen=True)
class _PrioNeedMeta:
    label: str = ""
    need_by_sec: float = 0.0
    lead_sec: float = 0.0
    storage_cap_ratio: float = 0.85


@dataclass(frozen=True)
class _PlanOrderContext:
    plan_order_on: bool
    step_rank_by_pid: dict[int, int]
    waiting_step_by_pid: dict[int, PlannedAction]
    lendable_cost_by_pid: dict[int, list[int]]


@dataclass(frozen=True)
class _TransportDemand:
    urgency: int
    need_by_sec: float
    dest: PlanetState
    need_res: Resources
    need_label: str
    cap_ratio: float


@dataclass(frozen=True)
class _DonorPools:
    ships_rem: dict[int, dict[str, int]]
    surplus_rem: dict[int, list[int]]
    lend_rem: dict[int, list[int]]


@dataclass(frozen=True)
class _TripCandidate:
    rph: float
    cargo_tot: int
    donor: PlanetState
    cargo: Resources
    chosen_ships: dict[str, int]
    flight_s: float


@dataclass(frozen=True)
class _PlannedTrip:
    donor: PlanetState
    dest: PlanetState
    need_res: Resources
    need_label: str
    dest_free: Resources
    cargo: Resources
    chosen_ships: dict[str, int]
    lent_tot: int
    lender_rank: int | None
    borrower_rank: int | None


def _normalize_priority_needs(
    priority_resource_need: Mapping[int, object] | None,
) -> tuple[dict[int, Resources], dict[int, _PrioNeedMeta]]:
    """Normalize optional AI-Planner priority resource needs (`planet_id -> Resources | Mapping`)."""
    norm_prio_needs: dict[int, Resources] = {}
    norm_prio_meta: dict[int, _PrioNeedMeta] = {}
    if not isinstance(priority_resource_need, Mapping):
        return norm_prio_needs, norm_prio_meta
    for raw_pid, raw_need in priority_resource_need.items():
        pid_k = _safe_strategy_int(raw_pid, 0)
        if pid_k <= 0:
            continue
        label = ""
        need_by_sec = 0.0
        lead_sec = 0.0
        storage_cap_ratio = 0.85
        if isinstance(raw_need, Resources):
            r_obj = Resources(
                pig_iron=max(0, _safe_strategy_int(raw_need.pig_iron, 0)),
                metal=max(0, _safe_strategy_int(raw_need.metal, 0)),
                kryptonite=max(0, _safe_strategy_int(raw_need.kryptonite, 0)),
                spice=max(0, _safe_strategy_int(raw_need.spice, 0)),
            )
        elif isinstance(raw_need, Mapping):
            r_obj = _resources_from_dict(raw_need)
            label = _safe_strategy_str(raw_need.get("label"))
            need_by_sec = max(0.0, catalog.safe_finite_float(raw_need.get("need_by_sec"), 0.0))
            lead_sec = max(0.0, catalog.safe_finite_float(raw_need.get("lead_sec"), 0.0))
            raw_cap = raw_need.get("storage_cap_ratio")
            if raw_cap is not None:
                storage_cap_ratio = min(0.98, max(0.85, catalog.safe_finite_float(raw_cap, 0.85)))
        else:
            continue
        if not r_obj.is_zero():
            norm_prio_needs[pid_k] = r_obj
            norm_prio_meta[pid_k] = _PrioNeedMeta(
                label=label,
                need_by_sec=need_by_sec,
                lead_sec=lead_sec,
                storage_cap_ratio=storage_cap_ratio,
            )
    return norm_prio_needs, norm_prio_meta


def _collect_inbound_transport_coords(state: GameState) -> set[str]:
    """Return normalized coordinates that already have an outbound Transport flight inbound."""
    inbound: set[str] = set()
    for q in state.active_queues or ():
        if not is_in_flight_fleet_job(q):
            continue
        q_name = _safe_strategy_str(getattr(q, "name", ""))
        q_det = _safe_strategy_str(getattr(q, "detail", "")).lower()
        if "return" in q_det or "↩" in q_name:
            continue
        if "transport" in q_name.lower():
            m_tr = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", q_name, re.ASCII)
            if m_tr:
                inbound.add(normalize_coords(m_tr.group(1)))
    return inbound


def _collect_p01_reserved_by_planet(existing_plan: Sequence[PlannedAction]) -> dict[int, list[int]]:
    """Sum each donor's own P0/P1 pending non-fleet resource costs."""
    reserved: dict[int, list[int]] = {}
    for act in existing_plan:
        if act.priority in ("P0", "P1") and act.kind not in _NON_RESOURCE_ACTION_KINDS:
            buf = reserved.setdefault(act.planet_id, [0, 0, 0, 0])
            buf[0] += max(0, _safe_strategy_int(act.cost.pig_iron, 0))
            buf[1] += max(0, _safe_strategy_int(act.cost.metal, 0))
            buf[2] += max(0, _safe_strategy_int(act.cost.kryptonite, 0))
            buf[3] += max(0, _safe_strategy_int(act.cost.spice, 0))
    return reserved


def _build_plan_order_context(
    cfg: StrategyConfig,
    existing_plan: Sequence[PlannedAction],
    norm_prio_needs: Mapping[int, Resources],
) -> _PlanOrderContext:
    """Compute Plan v4 plan-order step ranks, waiting steps, and lendable step reservations."""
    plan_order_on = _adaptive_execution_enabled(cfg)
    step_rank_by_pid: dict[int, int] = {}
    waiting_step_by_pid: dict[int, PlannedAction] = {}
    lendable_cost_by_pid: dict[int, list[int]] = {}
    if not plan_order_on:
        return _PlanOrderContext(False, step_rank_by_pid, waiting_step_by_pid, lendable_cost_by_pid)
    for act in existing_plan:
        if act.priority != "P1" or act.kind not in ("BUILD", "WAIT_RESOURCES") or act.planet_id in step_rank_by_pid:
            continue
        m_rank = _AI_PLAN_STEP_RANK_RE.match(_safe_strategy_str(act.reason))
        if m_rank is None:
            continue
        step_rank_by_pid[act.planet_id] = int(m_rank.group(1))
        if act.kind != "WAIT_RESOURCES" or act.executable_now or act.missing.is_zero():
            continue
        waiting_step_by_pid[act.planet_id] = act
        own_need = norm_prio_needs.get(act.planet_id)
        if own_need is not None and any(
            _safe_strategy_int(getattr(act.missing, rk, 0), 0) > 0
            and _safe_strategy_int(getattr(own_need, rk, 0), 0) > 0
            for rk in _RES_KEYS
        ):
            lendable_cost_by_pid[act.planet_id] = [
                max(0, _safe_strategy_int(act.cost.pig_iron, 0)),
                max(0, _safe_strategy_int(act.cost.metal, 0)),
                max(0, _safe_strategy_int(act.cost.kryptonite, 0)),
                max(0, _safe_strategy_int(act.cost.spice, 0)),
            ]
    return _PlanOrderContext(True, step_rank_by_pid, waiting_step_by_pid, lendable_cost_by_pid)


def _compute_colony_demand(
    dest: PlanetState,
    existing_plan: Sequence[PlannedAction],
    norm_prio_needs: Mapping[int, Resources],
    norm_prio_meta: Mapping[int, _PrioNeedMeta],
) -> _TransportDemand | None:
    """Compute a single colony's resource demand from pending actions, AI-Planner needs, and bootstrap."""
    need_vals = [0, 0, 0, 0]
    urgency = 9
    need_by_sec = 0.0
    cap_ratio = 0.85
    need_label = ""
    for act in existing_plan:
        if act.planet_id != dest.planet_id:
            continue
        if act.priority not in ("P1", "P2") or act.kind not in _DEMAND_ACTION_KINDS:
            continue
        if act.executable_now or act.missing.is_zero():
            continue
        act_urg = 0 if act.priority == "P1" else 1
        if act_urg < urgency:
            urgency = act_urg
            need_label = _safe_strategy_str(act.target_name) or act.kind
        need_vals[0] = max(need_vals[0], max(0, _safe_strategy_int(act.missing.pig_iron, 0)))
        need_vals[1] = max(need_vals[1], max(0, _safe_strategy_int(act.missing.metal, 0)))
        need_vals[2] = max(need_vals[2], max(0, _safe_strategy_int(act.missing.kryptonite, 0)))
        need_vals[3] = max(need_vals[3], max(0, _safe_strategy_int(act.missing.spice, 0)))

    prio_need = norm_prio_needs.get(dest.planet_id)
    if prio_need is not None:
        prio_meta = norm_prio_meta.get(dest.planet_id) or _PrioNeedMeta()
        urgency = -1
        need_by_sec = prio_meta.need_by_sec
        cap_ratio = prio_meta.storage_cap_ratio
        need_vals[0] = max(need_vals[0], prio_need.pig_iron)
        need_vals[1] = max(need_vals[1], prio_need.metal)
        need_vals[2] = max(need_vals[2], prio_need.kryptonite)
        need_vals[3] = max(need_vals[3], prio_need.spice)
        base_lbl = prio_meta.label or need_label
        need_label = (
            f"AI-Planner: {base_lbl}"
            if base_lbl and not base_lbl.lower().startswith("ai-planner")
            else (base_lbl or "AI-Planner target upgrade")
        )

    total_on_dest = (
        max(0, dest.resources.pig_iron)
        + max(0, dest.resources.metal)
        + max(0, dest.resources.kryptonite)
        + max(0, dest.resources.spice)
    )
    if (dest.fields_used <= 15 or dest.building_level(1) < 8) and total_on_dest < 20_000:
        starter = (15_000, 10_000, 6_000, 1_000)
        cur_r = (dest.resources.pig_iron, dest.resources.metal, dest.resources.kryptonite, dest.resources.spice)
        sto_r = (dest.storage.pig_iron, dest.storage.metal, dest.storage.kryptonite, dest.storage.spice)
        for idx_r in range(4):
            cap_r = int(sto_r[idx_r] * 0.70) if sto_r[idx_r] > 0 else starter[idx_r]
            boot_gap = max(0, min(starter[idx_r], cap_r) - max(0, cur_r[idx_r]))
            need_vals[idx_r] = max(need_vals[idx_r], boot_gap)
        if not need_label:
            urgency = 2
            need_label = "colony bootstrap"

    if sum(need_vals) <= 0:
        return None
    return _TransportDemand(
        urgency=urgency,
        need_by_sec=need_by_sec,
        dest=dest,
        need_res=Resources(
            pig_iron=need_vals[0],
            metal=need_vals[1],
            kryptonite=need_vals[2],
            spice=need_vals[3],
        ),
        need_label=need_label or "colony supply",
        cap_ratio=cap_ratio,
    )


def _collect_transport_demands(
    normal_planets: Sequence[PlanetState],
    existing_plan: Sequence[PlannedAction],
    inbound_transport_coords: set[str],
    norm_prio_needs: Mapping[int, Resources],
    norm_prio_meta: Mapping[int, _PrioNeedMeta],
    plan_ctx: _PlanOrderContext,
) -> list[_TransportDemand]:
    """Collect and priority-sort per-colony transport demands across eligible normal planets."""
    demands: list[_TransportDemand] = []
    for dest in normal_planets:
        norm_dest = normalize_coords(dest.coords)
        if not norm_dest or norm_dest in inbound_transport_coords:
            continue
        demand = _compute_colony_demand(dest, existing_plan, norm_prio_needs, norm_prio_meta)
        if demand is not None:
            demands.append(demand)
    demands.sort(
        key=lambda item: (
            item.urgency,
            item.need_by_sec,
            plan_ctx.step_rank_by_pid.get(item.dest.planet_id, _PLAN_ORDER_NO_RANK) if plan_ctx.plan_order_on else 0,
            -(item.need_res.pig_iron + item.need_res.metal + item.need_res.kryptonite + item.need_res.spice),
        )
    )
    return demands


def _build_donor_pools(
    normal_planets: Sequence[PlanetState],
    p01_reserved_by_pid: Mapping[int, list[int]],
    norm_prio_needs: Mapping[int, Resources],
    lendable_cost_by_pid: Mapping[int, list[int]],
    fuel_reserve: int,
) -> _DonorPools:
    """Compute remaining donor transport ships, spare surpluses, and plan-order lendable pools."""
    ships_rem: dict[int, dict[str, int]] = {}
    surplus_rem: dict[int, list[int]] = {}
    lend_rem: dict[int, list[int]] = {}
    for p in normal_planets:
        c_ships = {
            k: cnt for k in ("s3", "s2", "s1") if (cnt := max(0, _safe_strategy_int((p.ships or {}).get(k, 0), 0))) > 0
        }
        if not c_ships:
            continue
        resv = p01_reserved_by_pid.get(p.planet_id, [0, 0, 0, 0])
        cur_vals = [
            max(0, p.resources.pig_iron),
            max(0, p.resources.metal),
            max(0, p.resources.kryptonite),
            max(0, p.resources.spice),
        ]
        sto_vals = [
            max(0, p.storage.pig_iron),
            max(0, p.storage.metal),
            max(0, p.storage.kryptonite),
            max(0, p.storage.spice),
        ]
        surp: list[int] = []
        lend: list[int] = [0, 0, 0, 0]
        lendable_cost = lendable_cost_by_pid.get(p.planet_id)
        donor_own_prio = norm_prio_needs.get(p.planet_id)
        donor_prio_vals = (
            (donor_own_prio.pig_iron, donor_own_prio.metal, donor_own_prio.kryptonite, donor_own_prio.spice)
            if donor_own_prio is not None
            else (0, 0, 0, 0)
        )
        for idx_r in range(4):
            near_cap = sto_vals[idx_r] > 0 and cur_vals[idx_r] >= int(sto_vals[idx_r] * 0.85)
            cushion = 0 if near_cap else min(int(cur_vals[idx_r] * 0.10), 5_000)
            extra = fuel_reserve if idx_r == 3 else 0
            spare_r = 0 if donor_prio_vals[idx_r] > 0 else max(0, cur_vals[idx_r] - resv[idx_r] - cushion - extra)
            surp.append(spare_r)
            if lendable_cost is not None:
                kept = max(0, resv[idx_r] - lendable_cost[idx_r])
                lend[idx_r] = max(0, cur_vals[idx_r] - kept - cushion - extra - spare_r)
        if sum(surp) > 0 or sum(lend) > 0:
            ships_rem[p.planet_id] = c_ships
            surplus_rem[p.planet_id] = surp
            if sum(lend) > 0:
                lend_rem[p.planet_id] = lend
    return _DonorPools(ships_rem=ships_rem, surplus_rem=surplus_rem, lend_rem=lend_rem)
