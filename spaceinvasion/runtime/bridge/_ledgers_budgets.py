"""Rolling infra-transport spend and spare-ship budget ledger helpers."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import time

from spaceinvasion.combat.fleet import SHIP_SLUG_BY_KEY, fleet_cargo_capacity
from spaceinvasion.core import game_data
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import GameState, Resources
from spaceinvasion.planner.strategy import PlannedAction, compute_spare_ship_budget
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._helpers import BRIDGE_RUNTIME_ERRORS, safe_api_float, safe_api_int

_LOGGER = logging.getLogger(__name__)


def prune_infra_transport_spend(ctx: BridgeContext, now_ts: float | None = None) -> None:
    """Drop infra transport spend entries older than the 1h rolling window."""
    if not ctx.infra_transport_spend_ledger:
        return
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    cutoff = eff_now - BridgeConstants.INFRA_TRANSPORT_SPEND_WINDOW_SECONDS
    ctx.infra_transport_spend_ledger = [
        e
        for e in ctx.infra_transport_spend_ledger
        if isinstance(e, Mapping)
        and safe_api_float(e.get("ts"), 0.0) >= cutoff
        and (safe_api_int(e.get("metal"), 0) > 0 or safe_api_int(e.get("kryptonite"), 0) > 0)
    ][-200:]


def get_infra_transport_spend_last_hour(
    ctx: BridgeContext,
    now_ts: float | None = None,
) -> tuple[dict[str, float], float]:
    """Return `(spent_last_hour_map, carryover_hours)` for the 25%/h infra transport budget."""
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    prune_infra_transport_spend(ctx, eff_now)
    spent_m = float(sum(max(0, safe_api_int(e.get("metal"), 0)) for e in ctx.infra_transport_spend_ledger))
    spent_k = float(sum(max(0, safe_api_int(e.get("kryptonite"), 0)) for e in ctx.infra_transport_spend_ledger))
    carryover_h = 1.0
    if spent_m <= 0.0 and spent_k <= 0.0 and ctx.infra_transport_last_spend_ts > 0.0:
        idle_h = (eff_now - ctx.infra_transport_last_spend_ts) / 3600.0
        if idle_h > 1.0:
            carryover_h = round(min(BridgeConstants.INFRA_TRANSPORT_MAX_CARRYOVER_HOURS, idle_h), 4)
    return {"metal": spent_m, "kryptonite": spent_k}, carryover_h


def record_infra_transport_spend(
    ctx: BridgeContext,
    action: PlannedAction,
    *,
    now_ts: float | None = None,
    quantity: int | None = None,
) -> None:
    """Debit a transport hull order from the rolling 1h infra transport spend ledger."""
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    mult = max(1, safe_api_int(quantity, 1)) if quantity is not None else 1
    met = max(0, safe_api_int(action.cost.metal, 0)) * mult
    kry = max(0, safe_api_int(action.cost.kryptonite, 0)) * mult
    sid = game_data.safe_optional_int(action.target_id, None)
    units = max(1, safe_api_int(action.target_level, 1))
    if met <= 0 and kry <= 0 and sid is not None:
        slug = SHIP_SLUG_BY_KEY.get(f"s{sid}")
        info = game_data.resolve_unit("ships", slug) if slug else None
        raw_base = info.get("base_cost") if isinstance(info, Mapping) else None
        if isinstance(raw_base, Mapping):
            met = max(0, safe_api_int(raw_base.get("metal", 0), 0)) * units
            kry = max(0, safe_api_int(raw_base.get("kryptonite", 0), 0)) * units
    if met <= 0 and kry <= 0:
        return
    entry: dict[str, object] = {"ts": round(eff_now, 3), "metal": int(met), "kryptonite": int(kry)}
    if sid in (1, 2, 3):
        try:
            ordered_cargo = int(fleet_cargo_capacity({f"s{sid}": units}))
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.debug("Skipping ordered cargo calculation for s%s due to catalog error", sid, exc_info=True)
            ordered_cargo = 0
        if ordered_cargo > 0:
            entry["cargo"] = ordered_cargo
    ctx.infra_transport_spend_ledger.append(entry)
    ctx.infra_transport_last_spend_ts = eff_now
    prune_infra_transport_spend(ctx, eff_now)


def get_infra_cargo_ordered_last_hour(ctx: BridgeContext, now_ts: float | None = None) -> int:
    """Return total cargo capacity of transport hulls ordered in the trailing 1h window."""
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    prune_infra_transport_spend(ctx, eff_now)
    return int(sum(max(0, safe_api_int(e.get("cargo"), 0)) for e in ctx.infra_transport_spend_ledger))


def prune_spare_ship_spend(ctx: BridgeContext, now_ts: float | None = None) -> None:
    """Drop spare-ship spend entries older than `SPARE_SHIP_BUDGET_WINDOW_HOURS`."""
    if not ctx.spare_ship_spend_ledger:
        return
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    cutoff = eff_now - BridgeConstants.SPARE_SHIP_BUDGET_WINDOW_HOURS * 3600.0
    ctx.spare_ship_spend_ledger = [
        e for e in ctx.spare_ship_spend_ledger if isinstance(e, Mapping) and safe_api_float(e.get("ts"), 0.0) >= cutoff
    ][-400:]


def get_spare_ship_spend_in_window(ctx: BridgeContext, now_ts: float | None = None) -> dict[str, float]:
    """Return per-resource spare-ship spend across the trailing rolling window."""
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    prune_spare_ship_spend(ctx, eff_now)
    return {
        rk: float(sum(max(0, safe_api_int(e.get(rk), 0)) for e in ctx.spare_ship_spend_ledger))
        for rk in ("pig_iron", "metal", "kryptonite", "spice")
    }


def record_spare_ship_spend(
    ctx: BridgeContext,
    action: PlannedAction,
    *,
    now_ts: float | None = None,
    quantity: int | None = None,
) -> None:
    """Debit a non-transport ship order from the rolling spare-ship budget ledger."""
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if eff_now <= 0.0:
        eff_now = time.time()
    mult = max(1, safe_api_int(quantity, 1)) if quantity is not None else 1
    row: dict[str, int] = {
        "pig_iron": max(0, safe_api_int(action.cost.pig_iron, 0)) * mult,
        "metal": max(0, safe_api_int(action.cost.metal, 0)) * mult,
        "kryptonite": max(0, safe_api_int(action.cost.kryptonite, 0)) * mult,
        "spice": max(0, safe_api_int(action.cost.spice, 0)) * mult,
    }
    if not any(v > 0 for v in row.values()):
        sid = game_data.safe_optional_int(action.target_id, None)
        units = max(1, safe_api_int(action.target_level, 1))
        slug = SHIP_SLUG_BY_KEY.get(f"s{sid}") if sid is not None else None
        info = game_data.resolve_unit("ships", slug) if slug else None
        raw_base = info.get("base_cost") if isinstance(info, Mapping) else None
        if isinstance(raw_base, Mapping):
            row = {rk: max(0, safe_api_int(raw_base.get(rk, 0), 0)) * units for rk in row}
    if not any(v > 0 for v in row.values()):
        return
    entry: dict[str, object] = {"ts": round(eff_now, 3)}
    for rk, val in row.items():
        entry[rk] = int(val)
    ctx.spare_ship_spend_ledger.append(entry)
    prune_spare_ship_spend(ctx, eff_now)


def compute_infra_transport_budget(ctx: BridgeContext, *, now_ts: float | None = None) -> dict[str, object]:
    """Return structured rolling infra-transport spend, carryover hours, and ordered cargo."""
    spent, carryover = get_infra_transport_spend_last_hour(ctx, now_ts)
    return {
        "spent_last_hour": spent,
        "carryover_hours": carryover,
        "ordered_cargo_last_hour": get_infra_cargo_ordered_last_hour(ctx, now_ts),
    }


def compute_spare_ship_budget_for_ctx(
    ctx: BridgeContext,
    state: GameState,
    spare_pct: Mapping[str, float] | float | None,
    *,
    attack_telemetry: Mapping[str, object] | None = None,
    reserved_cost: Resources | Mapping[str, object] | None = None,
    adaptive_execution: bool = False,
    config: StrategyConfig | None = None,
    now_ts: float | None = None,
) -> dict[str, object]:
    """Compute the strategy spare-ship budget incorporating live infra and spare spend ledgers."""
    eff_cfg = config or ctx.strategy_config
    infra_spend: dict[str, float] | None = None
    infra_carryover = 1.0
    spare_spend: dict[str, float] | None = None
    spare_win_h = 1.0
    if adaptive_execution:
        infra_spend, infra_carryover = get_infra_transport_spend_last_hour(ctx, now_ts)
        spare_spend = get_spare_ship_spend_in_window(ctx, now_ts)
        spare_win_h = BridgeConstants.SPARE_SHIP_BUDGET_WINDOW_HOURS
    return dict(
        compute_spare_ship_budget(
            state,
            spare_pct,
            attack_telemetry=attack_telemetry,
            reserved_cost=reserved_cost,
            adaptive_execution=adaptive_execution,
            config=eff_cfg,
            infra_spend_last_hour=infra_spend,
            infra_carryover_hours=infra_carryover,
            spare_spend_in_window=spare_spend,
            spare_window_hours=spare_win_h,
        )
    )
