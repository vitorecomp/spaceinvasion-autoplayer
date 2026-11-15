"""Wakeup timer computation and `/bot_status` control snapshot parsing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

from backend.runtime.commander._types import (
    BotControlSnapshot,
    CommanderConfigLike,
    GameStateCmdLike,
    PlannedActionCmdLike,
)
from backend.runtime.commander.errors import (
    is_captcha_error,
    safe_cmd_str,
    safe_finite_float,
)

_QUEUE_HMS_RE = re.compile(r"\b(\d{1,3}):(\d{2}):(\d{2})\b", re.ASCII)
_QUEUE_UNIT_RE = re.compile(
    r"(?:(\d+)\s*h\s*)?(?:(\d+)\s*m(?:in)?\s*)?(\d+)\s*s\b",
    re.IGNORECASE | re.ASCII,
)
_INT_KEY_RE = re.compile(r"-?\d{1,18}", re.ASCII)


def parse_queue_timer_seconds(text: str) -> float | None:
    """Parse ``HH:MM:SS`` or ``Xh Ym Zs`` countdown text into seconds."""
    if not text:
        return None
    m_hms = _QUEUE_HMS_RE.search(text)
    if m_hms:
        return float(int(m_hms.group(1)) * 3600 + int(m_hms.group(2)) * 60 + int(m_hms.group(3)))
    m_u = _QUEUE_UNIT_RE.search(text)
    if m_u:
        hours = int(m_u.group(1) or 0)
        mins = int(m_u.group(2) or 0)
        secs = int(m_u.group(3) or 0)
        total = float(hours * 3600 + mins * 60 + secs)
        return total if total > 0.0 else None
    return None


def _parse_int_float_map(
    raw: object,
    *,
    positive_only: bool = False,
) -> dict[int, float] | None:
    if not isinstance(raw, Mapping):
        return None
    parsed: dict[int, float] = {}
    for key, val in raw.items():
        fv = safe_finite_float(val)
        if fv is None or (positive_only and fv <= 0.0):
            continue
        if isinstance(key, int) and not isinstance(key, bool):
            if abs(key).bit_length() <= 64:
                parsed[key] = fv
        else:
            ks = safe_cmd_str(key).strip()
            if _INT_KEY_RE.fullmatch(ks):
                parsed[int(ks)] = fv
    return parsed


def _parse_str_float_map(raw: object) -> dict[str, float] | None:
    if not isinstance(raw, Mapping):
        return None
    parsed: dict[str, float] = {}
    for key, val in raw.items():
        fv = safe_finite_float(val)
        if fv is not None:
            parsed[safe_cmd_str(key)] = fv
    return parsed


def _resolve_galaxy_scan_blocking(
    bot_ctrl: Mapping[str, object],
    strategy_cfg: CommanderConfigLike,
    *,
    run_loop: bool,
    effective_live: bool,
) -> bool:
    if "galaxy_scan_blocking" in bot_ctrl:
        raw_blocking = bool(bot_ctrl.get("galaxy_scan_blocking"))
    else:
        raw_sweep = bot_ctrl.get("galaxy_sweep")
        sweep_active = bool(bot_ctrl.get("galaxy_sweep_in_progress")) or (
            isinstance(raw_sweep, Mapping) and bool(raw_sweep.get("in_progress"))
        )
        raw_blocking = sweep_active and bool(strategy_cfg.galaxy_scan_block_other_actions)
    return raw_blocking and not bool(bot_ctrl.get("p0_escape_flight_ready", False)) and (run_loop or effective_live)


def parse_bot_control_snapshot(
    bot_ctrl: Mapping[str, object],
    strategy_cfg: CommanderConfigLike,
    *,
    run_loop: bool,
    effective_live: bool,
) -> BotControlSnapshot:
    """Decode `/bot_status` payload into a typed ``BotControlSnapshot``."""
    captcha_blocked = is_captcha_error(bot_ctrl.get("captcha_message"), bot_ctrl=bot_ctrl)
    captcha_message = safe_cmd_str(
        bot_ctrl.get("captcha_message"),
        "CAPTCHA / anti-bot challenge active",
    )
    paused = bool(bot_ctrl.get("paused"))
    galaxy_blocking = _resolve_galaxy_scan_blocking(
        bot_ctrl,
        strategy_cfg,
        run_loop=run_loop,
        effective_live=effective_live,
    )
    override_raw = bot_ctrl.get("mode_override")
    mode_override = override_raw if isinstance(override_raw, str) and override_raw in ("live", "dry-run") else None

    raw_mq = bot_ctrl.get("manual_queue")
    manual_queue = [x for x in raw_mq if isinstance(x, Mapping)] if isinstance(raw_mq, list) else None
    raw_tm = bot_ctrl.get("transmitter_status")
    transmitter_status = {str(k): v for k, v in raw_tm.items()} if isinstance(raw_tm, Mapping) else None
    raw_scfg = bot_ctrl.get("strategy_config")
    strategy_config_raw = raw_scfg if isinstance(raw_scfg, Mapping) else None

    return BotControlSnapshot(
        captcha_blocked=captcha_blocked,
        captcha_message=captcha_message,
        paused=paused,
        galaxy_scan_blocking=galaxy_blocking,
        mode_override=mode_override,
        scanned_systems=_parse_str_float_map(bot_ctrl.get("scanned_systems")),
        throttle_cooldowns=_parse_int_float_map(bot_ctrl.get("throttle_cooldowns"), positive_only=False),
        economy_sync_timestamps=_parse_int_float_map(bot_ctrl.get("economy_sync_timestamps"), positive_only=False),
        fleet_slot_cooldowns=_parse_int_float_map(bot_ctrl.get("fleet_slot_cooldowns"), positive_only=True),
        manual_queue=manual_queue,
        transmitter_status=transmitter_status,
        strategy_config_raw=strategy_config_raw,
    )


def compute_next_wakeup_seconds(
    state: GameStateCmdLike | None,
    next_action: PlannedActionCmdLike | None,
    outcomes: Sequence[Mapping[str, object]] = (),
    *,
    min_interval_s: float = 120.0,
    max_idle_s: float = 900.0,
    fleet_slot_cooldowns: Mapping[int, float] | None = None,
) -> tuple[float, str]:
    """Compute exact seconds until next batch wakeup so pages are only visited when ready to act."""
    floor_s = max(0.01, float(min_interval_s))
    cap_s = max(floor_s, float(max_idle_s))

    slot_busy_seen = False
    for out in outcomes:
        if not isinstance(out, Mapping):
            continue
        if bool(out.get("executed")):
            cmd_s = safe_cmd_str(out.get("command")).lower()
            act_m = out.get("action")
            kind_s = safe_cmd_str(act_m.get("kind") if isinstance(act_m, Mapping) else "").upper()
            if cmd_s == "spy" or kind_s == "SPY":
                return floor_s, "awaiting spy report & next batch"
        else:
            out_msg_l = safe_cmd_str(out.get("message", "")).lower()
            if (
                "no ship input fields found on internalfleet" in out_msg_l
                or "nofleet" in out_msg_l
                or "fleet slots busy" in out_msg_l
            ):
                slot_busy_seen = True

    cand_waits: list[tuple[float, str]] = []
    fsc_vals = [
        fv for v in (fleet_slot_cooldowns or {}).values() if (fv := safe_finite_float(v)) is not None and fv > 0.0
    ]
    if slot_busy_seen or fsc_vals:
        slot_s = min(fsc_vals) + 2.0 if fsc_vals else max(floor_s, 90.0)
        cand_waits.append((slot_s, "fleet slot cooldown / returning ships"))

    if next_action is not None:
        t_lbl = safe_cmd_str(next_action.target_name, next_action.kind)
        if next_action.executable_now:
            cand_waits.append((floor_s, f"next batch ({next_action.kind}: {t_lbl})"))
        elif "[Hold: fleet slots busy / returning]" in safe_cmd_str(next_action.reason):
            cand_waits.append((max(floor_s, 90.0), f"fleet slots returning ({t_lbl})"))
        else:
            w_sec = safe_finite_float(next_action.wait_seconds)
            if w_sec is not None and w_sec > 0.0:
                cand_waits.append((w_sec + 2.0, f"build/resource timer for {t_lbl}"))

    if state is not None:
        queues = state.active_queues
        b_q_cnt = sum(1 for q in queues if q.category == "building")
        for q in queues:
            q_cat = safe_cmd_str(q.category).lower()
            if q_cat not in ("fleet", "research") and not (q_cat == "building" and b_q_cnt >= 5):
                continue
            q_rem = parse_queue_timer_seconds(f"{q.remaining_text or ''} {q.name or ''}")
            if q_rem is not None and q_rem > 0.0:
                cand_waits.append((q_rem + 2.0, f"{q_cat} queue completion ({safe_cmd_str(q.name, q_cat)})"))

    if cand_waits:
        best_s, best_reason = min(cand_waits, key=lambda item: item[0])
        return min(max(floor_s, best_s), cap_s), best_reason
    return floor_s, "batch interval"
