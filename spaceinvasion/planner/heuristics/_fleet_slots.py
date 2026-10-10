"""Strict per-planet fleet slot reservation and gating (SPY -> TRANSPORT -> COLONIZE -> STATION -> HARVEST)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TypeVar

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, PlanetState
from spaceinvasion.planner.heuristics._common import _safe_strategy_int, _safe_strategy_str
from spaceinvasion.planner.heuristics._fleet_context import _FleetContext, _S8PoolEntry
from spaceinvasion.planner.heuristics._fleet_helper_builds import _ColonizeSortKey, _ColonySpySortKey
from spaceinvasion.planner.heuristics._fleet_targets import _SpySortKey
from spaceinvasion.planner.heuristics._types import PlannedAction
from spaceinvasion.planner.heuristics.transport_station import (
    plan_interplanet_stationing,
    plan_interplanet_transports,
)

_KT = TypeVar("_KT", _SpySortKey, _ColonySpySortKey)


@dataclass(frozen=True)
class _SlotReservations:
    res_spy_cfg: int
    spy_only_res: bool
    all_spy_planet_ids: set[int]
    tr_res_by_planet: dict[int, int]
    col_res_by_planet: dict[int, int]
    st_res_by_planet: dict[int, int]

    def spy_res_for(self, ctx: _FleetContext, p_obj: PlanetState) -> int:
        has_spy_demand_p = (
            self.res_spy_cfg > 0
            and _safe_strategy_int(p_obj.ships.get("s10", 0), 0) > 0
            and p_obj.planet_id in self.all_spy_planet_ids
        )
        if not has_spy_demand_p:
            return 0
        base_res = min(self.res_spy_cfg, max(0, ctx.max_slots_for(p_obj) - 1))
        if self.spy_only_res:
            return max(0, base_res - ctx.used_spy_slots_for(p_obj))
        return base_res

    def free_harvest_slots(self, ctx: _FleetContext, p_obj: PlanetState) -> int:
        return max(
            0,
            ctx.max_slots_for(p_obj)
            - ctx.used_slots_for(p_obj)
            - self.spy_res_for(ctx, p_obj)
            - self.tr_res_by_planet.get(p_obj.planet_id, 0)
            - self.col_res_by_planet.get(p_obj.planet_id, 0)
            - self.st_res_by_planet.get(p_obj.planet_id, 0),
        )

    def gate_non_spy_slot(self, ctx: _FleetContext, act: PlannedAction) -> PlannedAction:
        p_obj = ctx.planet_by_id.get(act.planet_id, ctx.pools.origin)
        max_p_slots = ctx.max_slots_for(p_obj)
        used_p_slots = ctx.used_slots_for(p_obj)
        if used_p_slots >= max_p_slots:
            rsn = act.reason
            if "in flight" not in rsn.lower() and "in transit" not in rsn.lower():
                rsn = f"{rsn} [Hold: Fleet slots full ({used_p_slots}/{max_p_slots} in flight)]"
            return replace(act, executable_now=False, reason=rsn) if act.executable_now or rsn != act.reason else act
        eff_res = self.spy_res_for(ctx, p_obj)
        tr_res = self.tr_res_by_planet.get(p_obj.planet_id, 0)
        col_res = self.col_res_by_planet.get(p_obj.planet_id, 0)
        st_res = self.st_res_by_planet.get(p_obj.planet_id, 0)
        checks = (
            (eff_res, max_p_slots - eff_res, "reserved for spy", "Spy probes"),
            (tr_res, max_p_slots - eff_res - tr_res, "reserved for transport", "Transport"),
            (col_res, max_p_slots - eff_res - tr_res - col_res, "reserved for colony", "Colony ship"),
            (st_res, max_p_slots - eff_res - tr_res - col_res - st_res, "reserved for station", "Station deployment"),
        )
        for res_cnt, threshold, needle, label in checks:
            if res_cnt > 0 and used_p_slots >= threshold:
                rsn = act.reason
                if needle not in rsn.lower():
                    rsn = (
                        f"{rsn} [Hold: Fleet slot reserved for {label} "
                        f"({used_p_slots}/{max_p_slots} in flight, {res_cnt} reserved)]"
                    )
                return (
                    replace(act, executable_now=False, reason=rsn) if act.executable_now or rsn != act.reason else act
                )
        return act


def _gate_strict_spy_list(
    entries: list[tuple[_KT, PlannedAction]],
    ctx: _FleetContext,
    res_spy_cfg: int,
    spy_dispatch_by_pid: dict[int, int],
) -> list[tuple[_KT, PlannedAction]]:
    gated_out: list[tuple[_KT, PlannedAction]] = []
    for sk_s, act_s in entries:
        p_sp = ctx.planet_by_id.get(act_s.planet_id, ctx.pools.origin)
        spy_cap = min(ctx.max_slots_for(p_sp), res_spy_cfg)
        spy_used = ctx.used_spy_slots_for(p_sp) + spy_dispatch_by_pid.get(p_sp.planet_id, 0)
        if act_s.executable_now:
            if spy_used >= spy_cap:
                rsn_s = act_s.reason
                if "restricted to reserved" not in rsn_s.lower():
                    rsn_s += f" [Hold: Spy restricted to reserved slot(s) ({spy_used}/{spy_cap} spy slots used)]"
                new_key: _KT = (1, sk_s[1], sk_s[2], sk_s[3])
                gated_out.append((new_key, replace(act_s, executable_now=False, reason=rsn_s)))
                continue
            spy_dispatch_by_pid[p_sp.planet_id] = spy_dispatch_by_pid.get(p_sp.planet_id, 0) + 1
        gated_out.append((sk_s, act_s))
    return gated_out


def _apply_strict_spy_slot_gating(
    ctx: _FleetContext,
    res_spy_cfg: int,
    spy_only_res: bool,
    colony_spy_actions: list[tuple[_ColonySpySortKey, PlannedAction]],
    spy_actions: list[tuple[_SpySortKey, PlannedAction]],
) -> tuple[list[tuple[_ColonySpySortKey, PlannedAction]], list[tuple[_SpySortKey, PlannedAction]]]:
    if not spy_only_res:
        return colony_spy_actions, spy_actions
    colony_spy_actions.sort(key=lambda item: item[0])
    spy_actions.sort(key=lambda item: item[0])
    spy_dispatch_by_pid: dict[int, int] = {}
    gated_col = _gate_strict_spy_list(colony_spy_actions, ctx, res_spy_cfg, spy_dispatch_by_pid)
    gated_spy = _gate_strict_spy_list(spy_actions, ctx, res_spy_cfg, spy_dispatch_by_pid)
    return gated_col, gated_spy


def _gate_transport_tier(
    state: GameState,
    cfg: StrategyConfig,
    ctx: _FleetContext,
    slots: _SlotReservations,
    existing_plan: Sequence[PlannedAction],
    priority_resource_need: Mapping[int, object] | None,
) -> tuple[list[PlannedAction], dict[int, int]]:
    raw_transports = plan_interplanet_transports(
        state, cfg, existing_plan, now_ts=ctx.ref_now, priority_resource_need=priority_resource_need
    )
    transport_actions: list[PlannedAction] = []
    tr_dispatch_by_pid: dict[int, int] = {}
    tr_count_by_planet: dict[int, int] = {}
    for act_t in raw_transports:
        exec_p_t = ctx.planet_by_id.get(act_t.planet_id, ctx.pools.origin)
        max_t = ctx.max_slots_for(exec_p_t)
        spy_res_t = slots.spy_res_for(ctx, exec_p_t)
        slot_idx_t = ctx.used_slots_for(exec_p_t) + tr_dispatch_by_pid.get(exec_p_t.planet_id, 0)
        rsn_t = act_t.reason
        can_t = bool(act_t.executable_now and getattr(cfg, "allow_transport", False))
        if can_t and slot_idx_t >= max_t:
            can_t = False
            if "in flight" not in rsn_t.lower():
                rsn_t += f" [Hold: Fleet slots full ({slot_idx_t}/{max_t} in flight)]"
        elif can_t and spy_res_t > 0 and slot_idx_t >= max_t - spy_res_t:
            can_t = False
            if "reserved for spy" not in rsn_t.lower():
                rsn_t += f" [Hold: Fleet slot reserved for Spy probes ({slot_idx_t}/{max_t} in flight, {spy_res_t} reserved)]"
        if can_t:
            tr_dispatch_by_pid[exec_p_t.planet_id] = tr_dispatch_by_pid.get(exec_p_t.planet_id, 0) + 1
        if getattr(cfg, "allow_transport", False):
            tr_count_by_planet[exec_p_t.planet_id] = tr_count_by_planet.get(exec_p_t.planet_id, 0) + 1
        transport_actions.append(replace(act_t, executable_now=can_t, reason=rsn_t))
    tr_res_by_planet: dict[int, int] = {}
    for pid_t, n_t in tr_count_by_planet.items():
        p_t = ctx.planet_by_id.get(pid_t)
        if p_t is not None:
            tr_res_by_planet[pid_t] = min(n_t, max(0, ctx.max_slots_for(p_t) - slots.spy_res_for(ctx, p_t)))
    return transport_actions, tr_res_by_planet


def _gate_colonize_tier(
    cfg: StrategyConfig,
    ctx: _FleetContext,
    slots: _SlotReservations,
    tr_res_by_planet: Mapping[int, int],
    colonize_actions: list[tuple[_ColonizeSortKey, PlannedAction]],
) -> tuple[list[tuple[_ColonizeSortKey, PlannedAction]], dict[int, int]]:
    colonize_actions.sort(key=lambda item: item[0])
    n_col_dispatch = min(ctx.open_colony_slots, max(1, ctx.pools.total_stationed_s8))
    pool_left = [_S8PoolEntry(p, cnt) for p, cnt in ctx.pools.s8_pool]
    col_dispatch_by_pid: dict[int, int] = {}
    col_count_by_planet: dict[int, int] = {}
    selected: list[tuple[_ColonizeSortKey, PlannedAction]] = []
    for sk_c, act_c in colonize_actions:
        if len(selected) >= n_col_dispatch:
            break
        assign = next(
            (
                e
                for e in pool_left
                if e.count > 0
                and (ctx.used_slots_for(e.planet) + col_dispatch_by_pid.get(e.planet.planet_id, 0))
                < (
                    ctx.max_slots_for(e.planet)
                    - slots.spy_res_for(ctx, e.planet)
                    - tr_res_by_planet.get(e.planet.planet_id, 0)
                )
            ),
            None,
        ) or next((e for e in pool_left if e.count > 0), None)
        if assign is not None:
            assign.count -= 1
            exec_p = assign.planet
            act_c = replace(act_c, planet_id=exec_p.planet_id, planet_coords=_safe_strategy_str(exec_p.coords))
        else:
            exec_p = ctx.planet_by_id.get(act_c.planet_id, ctx.pools.origin)
        max_c = ctx.max_slots_for(exec_p)
        spy_res_c = slots.spy_res_for(ctx, exec_p)
        tr_res_c = tr_res_by_planet.get(exec_p.planet_id, 0)
        slot_idx = ctx.used_slots_for(exec_p) + col_dispatch_by_pid.get(exec_p.planet_id, 0)
        rsn_c = act_c.reason
        can_c = bool(act_c.executable_now and assign is not None)
        if can_c and slot_idx >= max_c:
            can_c = False
            if "in flight" not in rsn_c.lower():
                rsn_c += f" [Hold: Fleet slots full ({slot_idx}/{max_c} in flight)]"
        elif can_c and spy_res_c > 0 and slot_idx >= max_c - spy_res_c:
            can_c = False
            if "reserved for spy" not in rsn_c.lower():
                rsn_c += (
                    f" [Hold: Fleet slot reserved for Spy probes ({slot_idx}/{max_c} in flight, {spy_res_c} reserved)]"
                )
        elif can_c and tr_res_c > 0 and slot_idx >= max_c - spy_res_c - tr_res_c:
            can_c = False
            if "reserved for transport" not in rsn_c.lower():
                rsn_c += (
                    f" [Hold: Fleet slot reserved for Transport ({slot_idx}/{max_c} in flight, {tr_res_c} reserved)]"
                )
        if n_col_dispatch > 1:
            rsn_c += f" [Parallel colony {len(selected) + 1}/{n_col_dispatch}]"
        if can_c:
            col_dispatch_by_pid[exec_p.planet_id] = col_dispatch_by_pid.get(exec_p.planet_id, 0) + 1
        if cfg.allow_colonize and assign is not None:
            col_count_by_planet[exec_p.planet_id] = col_count_by_planet.get(exec_p.planet_id, 0) + 1
        selected.append(
            (
                (0 if can_c else 1, sk_c[1], sk_c[2], sk_c[3], sk_c[4]),
                replace(act_c, executable_now=can_c, reason=rsn_c),
            )
        )

    col_res_by_planet: dict[int, int] = {}
    for pid_c, n_c in col_count_by_planet.items():
        p_c = ctx.planet_by_id.get(pid_c)
        if p_c is None:
            continue
        s8_here = max(0, _safe_strategy_int(p_c.ships.get("s8", 0), 0))
        tr_res_here = tr_res_by_planet.get(pid_c, 0)
        col_res_by_planet[pid_c] = min(
            n_c,
            ctx.open_colony_slots,
            s8_here,
            max(0, ctx.max_slots_for(p_c) - slots.spy_res_for(ctx, p_c) - tr_res_here),
        )
    return selected, col_res_by_planet


def _gate_station_tier(
    state: GameState,
    cfg: StrategyConfig,
    ctx: _FleetContext,
    slots: _SlotReservations,
    tr_res_by_planet: Mapping[int, int],
    col_res_by_planet: Mapping[int, int],
    existing_plan: Sequence[PlannedAction],
) -> tuple[list[PlannedAction], dict[int, int]]:
    raw_stations = plan_interplanet_stationing(state, cfg, existing_plan, now_ts=ctx.ref_now)
    station_actions: list[PlannedAction] = []
    st_dispatch_by_pid: dict[int, int] = {}
    st_count_by_planet: dict[int, int] = {}
    for act_st in raw_stations:
        exec_p_st = ctx.planet_by_id.get(act_st.planet_id, ctx.pools.origin)
        max_st = ctx.max_slots_for(exec_p_st)
        spy_res_st = slots.spy_res_for(ctx, exec_p_st)
        tr_res_st = tr_res_by_planet.get(exec_p_st.planet_id, 0)
        col_res_st = col_res_by_planet.get(exec_p_st.planet_id, 0)
        slot_idx_st = ctx.used_slots_for(exec_p_st) + st_dispatch_by_pid.get(exec_p_st.planet_id, 0)
        rsn_st = act_st.reason
        can_st = bool(act_st.executable_now and getattr(cfg, "allow_transport", False))
        if can_st and slot_idx_st >= max_st:
            can_st = False
            if "in flight" not in rsn_st.lower():
                rsn_st += f" [Hold: Fleet slots full ({slot_idx_st}/{max_st} in flight)]"
        elif can_st and spy_res_st > 0 and slot_idx_st >= max_st - spy_res_st:
            can_st = False
            if "reserved for spy" not in rsn_st.lower():
                rsn_st += f" [Hold: Fleet slot reserved for Spy probes ({slot_idx_st}/{max_st} in flight, {spy_res_st} reserved)]"
        elif can_st and tr_res_st > 0 and slot_idx_st >= max_st - spy_res_st - tr_res_st:
            can_st = False
            if "reserved for transport" not in rsn_st.lower():
                rsn_st += f" [Hold: Fleet slot reserved for Transport ({slot_idx_st}/{max_st} in flight, {tr_res_st} reserved)]"
        elif can_st and col_res_st > 0 and slot_idx_st >= max_st - spy_res_st - tr_res_st - col_res_st:
            can_st = False
            if "reserved for colony" not in rsn_st.lower():
                rsn_st += f" [Hold: Fleet slot reserved for Colony ship ({slot_idx_st}/{max_st} in flight, {col_res_st} reserved)]"
        if can_st:
            st_dispatch_by_pid[exec_p_st.planet_id] = st_dispatch_by_pid.get(exec_p_st.planet_id, 0) + 1
        if getattr(cfg, "allow_transport", False):
            st_count_by_planet[exec_p_st.planet_id] = st_count_by_planet.get(exec_p_st.planet_id, 0) + 1
        station_actions.append(replace(act_st, executable_now=can_st, reason=rsn_st))

    st_res_by_planet: dict[int, int] = {}
    for pid_st, n_st in st_count_by_planet.items():
        p_st = ctx.planet_by_id.get(pid_st)
        if p_st is not None:
            st_res_by_planet[pid_st] = min(
                n_st,
                max(
                    0,
                    ctx.max_slots_for(p_st)
                    - slots.spy_res_for(ctx, p_st)
                    - tr_res_by_planet.get(pid_st, 0)
                    - col_res_by_planet.get(pid_st, 0),
                ),
            )
    return station_actions, st_res_by_planet
