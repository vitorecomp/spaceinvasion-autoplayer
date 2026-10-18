"""Daily Interdimensional Transmitter and login-bonus telemetry helpers."""

from __future__ import annotations

import time

from spaceinvasion.db.storage import server_day_key
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._helpers import bounded_api_str, safe_api_float, safe_api_int


def get_transmitter_status(ctx: BridgeContext, *, now_ts: float | None = None) -> dict[str, object]:
    """Return daily Interdimensional Transmitter and daily login bonus status."""
    eff_now = safe_api_float(now_ts, time.time()) if now_ts is not None else time.time()
    if ctx.db is not None:
        return dict(ctx.db.get_transmitter_status(now_ts=eff_now))
    today = server_day_key(eff_now)
    mem = ctx.transmitter_state_mem
    last_act_day = str(mem.get("last_activated_day") or "")
    last_chk_day = str(mem.get("last_checked_day") or "")
    db_chk_day = str(mem.get("daily_bonus_checked_day") or "")
    db_col_day = str(mem.get("daily_bonus_collected_day") or "")
    db_avail_day = str(mem.get("daily_bonus_available_day") or "")
    db_avail = bool(mem.get("daily_bonus_available", False)) and (db_avail_day in ("", today))
    free_rem = mem.get("free_spins_remaining")
    already_done = (last_act_day == today) or (
        last_chk_day == today and free_rem == 0 and db_chk_day == today and not db_avail
    )
    return {
        "today_day_key": today,
        "due": not already_done,
        "last_activated_day": last_act_day,
        "last_checked_day": last_chk_day,
        "daily_bonus_checked_day": db_chk_day,
        "daily_bonus_collected_day": db_col_day,
        "daily_bonus_available": db_avail,
        "last_activated_ts": safe_api_float(mem.get("last_activated_ts"), 0.0),
        "last_checked_ts": safe_api_float(mem.get("last_checked_ts"), 0.0),
        "free_spins_remaining": free_rem,
        "last_reward": str(mem.get("last_reward") or ""),
        "total_activations": safe_api_int(mem.get("total_activations"), 0),
    }


def record_transmitter_state(
    ctx: BridgeContext,
    *,
    activated: bool,
    free_spins_remaining: int | None = None,
    reward: str = "",
    daily_bonus_checked: bool | None = None,
    daily_bonus_collected: bool | None = None,
    daily_bonus_available: bool | None = None,
    ts: float | None = None,
) -> dict[str, object]:
    """Persist transmitter activation/check telemetry to DB or in-memory mirror."""
    eff_now = round(safe_api_float(ts, time.time()) if ts is not None else time.time(), 3)
    if ctx.db is not None:
        return dict(
            ctx.db.record_transmitter_state(
                activated=activated,
                free_spins_remaining=free_spins_remaining,
                reward=reward,
                daily_bonus_checked=daily_bonus_checked,
                daily_bonus_collected=daily_bonus_collected,
                daily_bonus_available=daily_bonus_available,
                ts=eff_now,
            )
        )
    today = server_day_key(eff_now)
    mem = ctx.transmitter_state_mem
    if activated:
        mem["last_activated_day"] = today
        mem["last_activated_ts"] = eff_now
        mem["total_activations"] = safe_api_int(mem.get("total_activations"), 0) + 1
    if daily_bonus_checked is True or daily_bonus_collected is True:
        mem["daily_bonus_checked_day"] = today
    if daily_bonus_collected is True:
        mem["daily_bonus_collected_day"] = today
    if daily_bonus_available is not None:
        mem["daily_bonus_available"] = bool(daily_bonus_available)
        mem["daily_bonus_available_day"] = today if daily_bonus_available else ""
    elif activated or daily_bonus_collected is True or daily_bonus_checked is True:
        mem["daily_bonus_available"] = False
        mem["daily_bonus_available_day"] = ""
    mem["last_checked_day"] = today
    mem["last_checked_ts"] = eff_now
    if free_spins_remaining is not None:
        mem["free_spins_remaining"] = max(0, safe_api_int(free_spins_remaining, 0))
    if reward:
        mem["last_reward"] = bounded_api_str(reward, default="")[:160]
    return get_transmitter_status(ctx, now_ts=eff_now)


def note_daily_login_bonus_seen(
    ctx: BridgeContext,
    present: bool,
    *,
    ts: float | None = None,
) -> dict[str, object]:
    """Record whether the daily login bonus banner was observed on `internalHome`."""
    eff_now = round(safe_api_float(ts, time.time()) if ts is not None else time.time(), 3)
    if ctx.db is not None:
        return dict(ctx.db.note_daily_login_bonus_seen(present=present, ts=eff_now))
    today = server_day_key(eff_now)
    mem = ctx.transmitter_state_mem
    if present:
        if (
            str(mem.get("last_activated_day") or "") != today
            and str(mem.get("daily_bonus_collected_day") or "") != today
        ):
            mem["daily_bonus_available"] = True
            mem["daily_bonus_available_day"] = today
    else:
        mem["daily_bonus_available"] = False
        mem["daily_bonus_available_day"] = ""
    return get_transmitter_status(ctx, now_ts=eff_now)
