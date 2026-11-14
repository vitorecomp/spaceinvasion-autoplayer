"""Fleet-slot cooldowns, observed fleet-slot tracking, and hierarchical slot-hold helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import math
import time

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import (
    GameState,
    PlanetState,
    PlannedAction,
    count_in_flight_fleets_for_planet,
    count_in_flight_spy_fleets_for_planet,
    is_in_flight_fleet_job,
)
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._helpers import safe_api_int
from spaceinvasion.runtime.bridge._helpers_sig import fleet_subrank

_FLEET_COOLDOWN_KINDS: frozenset[str] = frozenset(
    {"ATTACK", "SPY", "TRANSPORT", "STATION", "RECYCLE", "ESCAPE_FLIGHT", "COLONIZE"}
)
_FLEET_HOLD_KINDS: frozenset[str] = frozenset({"SPY", "TRANSPORT", "COLONIZE", "STATION", "ATTACK", "RECYCLE"})
_PRIO_RANK: dict[str, int] = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _sort_actions_by_priority(actions: list[PlannedAction]) -> None:
    actions.sort(
        key=lambda act: (
            _PRIO_RANK.get(act.priority, 9),
            -1 if act.kind == "ALERT_HOSTILE" else (0 if act.executable_now else 1),
            fleet_subrank(act),
        )
    )


def get_fleet_slot_cooldowns(ctx: BridgeContext) -> dict[str, float]:
    """Return active per-planet fleet-slot cooldowns in remaining seconds, pruning expired entries."""
    now_m = time.monotonic()
    active: dict[str, float] = {}
    expired: list[int] = []
    for pid, until_m in list(ctx.fleet_slot_cooldown_until.items()):
        if not isinstance(until_m, (int, float)) or isinstance(until_m, bool) or not math.isfinite(until_m):
            expired.append(pid)
            continue
        rem = float(until_m) - now_m
        if rem > 0.0:
            active[str(int(pid))] = round(rem, 2)
        else:
            expired.append(pid)
    for pid in expired:
        ctx.fleet_slot_cooldown_until.pop(pid, None)
    return active


def apply_fleet_slot_cooldowns(
    ctx: BridgeContext,
    actions: list[PlannedAction],
    *,
    re_sort_priority: bool = False,
) -> list[PlannedAction]:
    """Mark fleet actions on slot-cooldown planets non-executable with a hold suffix."""
    active_cd = get_fleet_slot_cooldowns(ctx)
    if not active_cd or not actions:
        return actions
    busy_pids = {int(k) for k in active_cd}
    hold_suffix = " [Hold: fleet slots busy / returning]"
    changed = False
    out: list[PlannedAction] = []
    for act in actions:
        if act.kind in _FLEET_COOLDOWN_KINDS and act.planet_id in busy_pids:
            cur_reason = str(act.reason or "")
            new_reason = (
                cur_reason if "[Hold: fleet slots busy / returning]" in cur_reason else f"{cur_reason}{hold_suffix}"
            )
            if act.executable_now or new_reason != cur_reason:
                changed = True
                out.append(replace(act, executable_now=False, reason=new_reason))
                continue
        out.append(act)
    if changed and re_sort_priority:
        _sort_actions_by_priority(out)
    return out


def max_fleet_slots_for_planet(ctx: BridgeContext, planet: PlanetState | None) -> int:
    """Return effective max concurrent fleet slots for `planet` using live observations when available."""
    if planet is None:
        return 1
    b10_lvl = max(0, planet.building_level(10))
    obs_pair = ctx.observed_fleet_slots_by_pid.get(int(planet.planet_id))
    if obs_pair is not None and obs_pair[0] == b10_lvl:
        return max(0, int(obs_pair[1]))
    if ctx.observed_fleet_slot_base_offset is not None:
        return max(0, b10_lvl + int(ctx.observed_fleet_slot_base_offset))
    return 1 + b10_lvl


def record_observed_fleet_slots(
    ctx: BridgeContext,
    planet: PlanetState | None,
    available: int | None,
) -> None:
    """Record live observed free/total fleet slots on `planet` and infer base-offset when possible."""
    if planet is None or available is None or isinstance(available, bool):
        return
    avail_i = safe_api_int(available, -1)
    if avail_i < 0:
        return
    pid_i = int(planet.planet_id)
    b10_lvl = max(0, planet.building_level(10))
    changed = False
    if pid_i > 0 and ctx.observed_fleet_slots_by_pid.get(pid_i) != (b10_lvl, avail_i):
        ctx.observed_fleet_slots_by_pid[pid_i] = (b10_lvl, avail_i)
        changed = True
    if b10_lvl >= 1 and avail_i in (b10_lvl, b10_lvl + 1):
        new_off = avail_i - b10_lvl
        if ctx.observed_fleet_slot_base_offset != new_off:
            ctx.observed_fleet_slot_base_offset = new_off
            changed = True
    if changed:
        ctx.invalidate_dashboard_cache()


def compute_fleet_slot_busy_cooldown_s(state: GameState, planet: PlanetState | None) -> float:
    """Estimate cooldown duration until the earliest in-flight fleet on `planet` frees a slot."""
    if planet is None:
        return BridgeConstants.FLEET_SLOT_COOLDOWN_S
    norm_p = normalize_coords(planet.coords)
    earliest_rem: float | None = None
    for q in state.active_queues:
        if not is_in_flight_fleet_job(q):
            continue
        q_orig = normalize_coords(q.origin_coords)
        if q_orig and norm_p and q_orig != norm_p:
            continue
        rem_s = q.remaining_seconds
        if isinstance(rem_s, (int, float)) and not isinstance(rem_s, bool) and math.isfinite(rem_s) and rem_s > 0.0:
            if earliest_rem is None or float(rem_s) < earliest_rem:
                earliest_rem = float(rem_s)
    if earliest_rem is not None:
        return max(30.0, min(earliest_rem + 2.0, 1800.0))
    return BridgeConstants.FLEET_SLOT_COOLDOWN_S


def _compute_tier_reservations(
    ctx: BridgeContext,
    actions: list[PlannedAction],
    planets_by_id: Mapping[int, PlanetState],
    default_p: PlanetState,
    eff_cfg: StrategyConfig,
    spy_res_by_pid: Mapping[int, int],
) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
    tr_cnt: dict[int, int] = {}
    col_cnt: dict[int, int] = {}
    st_cnt: dict[int, int] = {}
    for act in actions:
        if act.priority == "P3":
            continue
        p_o = planets_by_id.get(act.planet_id, default_p)
        if act.kind == "TRANSPORT" and eff_cfg.allow_transport:
            if any(safe_api_int(p_o.ships.get(k, 0), 0) > 0 for k in ("s1", "s2", "s3")):
                tr_cnt[p_o.planet_id] = tr_cnt.get(p_o.planet_id, 0) + 1
        elif act.kind == "COLONIZE" and eff_cfg.allow_colonize:
            if safe_api_int(p_o.ships.get("s8", 0), 0) > 0:
                col_cnt[p_o.planet_id] = col_cnt.get(p_o.planet_id, 0) + 1
        elif act.kind == "STATION" and eff_cfg.allow_transport:
            if any(safe_api_int(v, 0) > 0 for k, v in p_o.ships.items() if k != "s9"):
                st_cnt[p_o.planet_id] = st_cnt.get(p_o.planet_id, 0) + 1

    tr_res: dict[int, int] = {}
    for pid_t, n_t in tr_cnt.items():
        p_o = planets_by_id.get(pid_t, default_p)
        tr_res[pid_t] = min(n_t, max(0, max_fleet_slots_for_planet(ctx, p_o) - spy_res_by_pid.get(pid_t, 0)))

    col_res: dict[int, int] = {}
    for pid_c, n_c in col_cnt.items():
        p_o = planets_by_id.get(pid_c, default_p)
        avail = max_fleet_slots_for_planet(ctx, p_o) - spy_res_by_pid.get(pid_c, 0) - tr_res.get(pid_c, 0)
        col_res[pid_c] = min(n_c, max(0, safe_api_int(p_o.ships.get("s8", 0), 0)), max(0, avail))

    st_res: dict[int, int] = {}
    for pid_st, n_st in st_cnt.items():
        p_o = planets_by_id.get(pid_st, default_p)
        avail = (
            max_fleet_slots_for_planet(ctx, p_o)
            - spy_res_by_pid.get(pid_st, 0)
            - tr_res.get(pid_st, 0)
            - col_res.get(pid_st, 0)
        )
        st_res[pid_st] = min(n_st, max(0, avail))
    return tr_res, col_res, st_res


def _maybe_hold_reason(
    act: PlannedAction,
    *,
    used_slots: int,
    max_slots: int,
    eff_res: int,
    tr_res: int,
    col_res: int,
    st_res: int,
) -> tuple[str, str] | None:
    if used_slots >= max_slots:
        return "full", f"[Hold: Fleet slots full ({used_slots}/{max_slots} in flight)]"
    if eff_res > 0 and used_slots >= max_slots - eff_res:
        return (
            "reserved for spy",
            f"[Hold: Fleet slot reserved for Spy probes ({used_slots}/{max_slots} in flight, {eff_res} reserved)]",
        )
    if (
        act.kind in ("COLONIZE", "STATION", "ATTACK", "RECYCLE")
        and tr_res > 0
        and used_slots >= max_slots - eff_res - tr_res
    ):
        return (
            "reserved for transport",
            f"[Hold: Fleet slot reserved for Transport ({used_slots}/{max_slots} in flight, {tr_res} reserved)]",
        )
    if (
        act.kind in ("STATION", "ATTACK", "RECYCLE")
        and col_res > 0
        and used_slots >= max_slots - eff_res - tr_res - col_res
    ):
        return (
            "reserved for colony",
            f"[Hold: Fleet slot reserved for Colony ship ({used_slots}/{max_slots} in flight, {col_res} reserved)]",
        )
    if (
        act.kind in ("ATTACK", "RECYCLE")
        and st_res > 0
        and used_slots >= max_slots - eff_res - tr_res - col_res - st_res
    ):
        return (
            "reserved for station",
            f"[Hold: Fleet slot reserved for Station deployment ({used_slots}/{max_slots} in flight, {st_res} reserved)]",
        )
    return None


def _hold_single_action(act: PlannedAction, *, marker: str, suffix: str) -> tuple[PlannedAction, bool]:
    cur_r = str(act.reason or "")
    cur_l = cur_r.lower()
    already = (
        ("in flight" in cur_l or "in transit" in cur_l or "fleet slots" in cur_l)
        if marker == "full"
        else (marker in cur_l)
    )
    new_r = cur_r if already else f"{cur_r} {suffix}"
    if act.executable_now or new_r != cur_r:
        return replace(act, executable_now=False, reason=new_r), True
    return act, False


def apply_fleet_slot_holds(
    ctx: BridgeContext,
    state: GameState,
    actions: list[PlannedAction],
    *,
    cfg: StrategyConfig | None = None,
    re_sort_priority: bool = False,
) -> list[PlannedAction]:
    """Enforce hierarchical per-planet fleet slot reservations (Spy -> Transport -> Colonize -> Station -> Raid)."""
    if not actions or not state.planets:
        return actions
    eff_cfg = cfg or ctx.strategy_config
    res_spy_cfg = max(0, safe_api_int(eff_cfg.reserved_spy_slots, 1))
    spy_only_res = bool(eff_cfg.spy_reserved_slots_only)
    planets_by_id = {p.planet_id: p for p in state.planets}
    default_p = state.active_planet() or state.planets[0]
    used_by_pid = {p.planet_id: count_in_flight_fleets_for_planet(state, p) for p in state.planets}
    spy_used_by_pid = {p.planet_id: count_in_flight_spy_fleets_for_planet(state, p) for p in state.planets}
    spy_pids = {
        a.planet_id
        for a in actions
        if a.kind == "SPY" and (a.executable_now or eff_cfg.allow_galaxy_scan or eff_cfg.allow_colonize)
    }

    spy_res_by_pid: dict[int, int] = {}
    for p_o in state.planets:
        has_dem = res_spy_cfg > 0 and safe_api_int(p_o.ships.get("s10", 0), 0) > 0 and p_o.planet_id in spy_pids
        if not has_dem:
            spy_res_by_pid[p_o.planet_id] = 0
        else:
            base_res = min(res_spy_cfg, max(0, max_fleet_slots_for_planet(ctx, p_o) - 1))
            spy_res_by_pid[p_o.planet_id] = (
                max(0, base_res - spy_used_by_pid[p_o.planet_id]) if spy_only_res else base_res
            )

    tr_res_by_pid, col_res_by_pid, st_res_by_pid = _compute_tier_reservations(
        ctx, actions, planets_by_id, default_p, eff_cfg, spy_res_by_pid
    )
    changed = False
    spy_dispatched_by_pid: dict[int, int] = {}
    out: list[PlannedAction] = []
    for act in actions:
        if act.kind not in _FLEET_HOLD_KINDS or act.priority == "P3":
            out.append(act)
            continue
        p_obj = planets_by_id.get(act.planet_id, default_p)
        max_slots = max_fleet_slots_for_planet(ctx, p_obj)
        used_slots = used_by_pid.get(p_obj.planet_id, count_in_flight_fleets_for_planet(state, p_obj))
        if act.kind == "SPY":
            if used_slots >= max_slots:
                cur_r = str(act.reason or "")
                new_r = (
                    cur_r
                    if ("in flight" in cur_r.lower() or "fleet slots" in cur_r.lower())
                    else f"{cur_r} [Hold: Fleet slots full ({used_slots}/{max_slots} in flight)]"
                )
                if act.executable_now or new_r != cur_r:
                    changed = True
                    out.append(replace(act, executable_now=False, reason=new_r))
                    continue
            elif spy_only_res:
                spy_cap = min(max_slots, res_spy_cfg)
                spy_used_here = spy_used_by_pid.get(p_obj.planet_id, 0) + spy_dispatched_by_pid.get(p_obj.planet_id, 0)
                if spy_used_here >= spy_cap:
                    upd, did = _hold_single_action(
                        act,
                        marker="restricted to reserved",
                        suffix=f"[Hold: Spy restricted to reserved slot(s) ({spy_used_here}/{spy_cap} spy slots used)]",
                    )
                    changed = changed or did
                    out.append(upd)
                    continue
                if act.executable_now:
                    spy_dispatched_by_pid[p_obj.planet_id] = spy_dispatched_by_pid.get(p_obj.planet_id, 0) + 1
            out.append(act)
            continue
        hold = _maybe_hold_reason(
            act,
            used_slots=used_slots,
            max_slots=max_slots,
            eff_res=spy_res_by_pid.get(p_obj.planet_id, 0),
            tr_res=tr_res_by_pid.get(p_obj.planet_id, 0),
            col_res=col_res_by_pid.get(p_obj.planet_id, 0),
            st_res=st_res_by_pid.get(p_obj.planet_id, 0),
        )
        if hold is not None:
            upd, did = _hold_single_action(act, marker=hold[0], suffix=hold[1])
            changed = changed or did
            out.append(upd)
            continue
        out.append(act)
    if changed and re_sort_priority:
        _sort_actions_by_priority(out)
    return out
