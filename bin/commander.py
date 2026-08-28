#!/usr/bin/env python3
"""Hot-reloadable Commander client communicating exclusively over HTTP with `game_api.py`."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import math
import os
from pathlib import Path
import random
import re
import signal
import sys
import threading
import time
from typing import Any, Mapping, Optional, Sequence

import requests


_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _maybe_reexec_venv() -> None:
    if os.environ.get("SPACEINVASION_NO_REEXEC") == "1":
        return
    if sys.prefix != sys.base_prefix:
        return
    venv_python = _REPO_ROOT / ".venv" / "bin" / "python3"
    if venv_python.exists() and os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


_maybe_reexec_venv()

from spaceinvasion.core import game_data # noqa: E402
from spaceinvasion.runtime.commander_client import GameApiClient, GameApiError  # noqa: E402
from spaceinvasion.core.config import StrategyConfig, load_strategy_config  # noqa: E402
from spaceinvasion.core.logging_utils import (  # noqa: E402
    append_tick_logs,
    format_action_summary,
    pick_next_action,
    redact_sensitive,
)
from spaceinvasion.runtime.reloader import (  # noqa: E402
    current_python_cmd,
    should_spawn_reloader,
    watch_and_run,
)
from spaceinvasion.core.state import (  # noqa: E402
    GameState,
    game_state_from_dict,
    is_asteroid_planet,
    normalize_planet_type,
)
from spaceinvasion.planner.strategy import PlannedAction, merge_manual_queue_actions, plan_actions  # noqa: E402

WATCH_PATHS = (
    Path(__file__).resolve(),
    Path("config.yaml"),
    Path("spaceinvasion/core/config.py"),
    Path("spaceinvasion/combat/fleet.py"),
    Path("spaceinvasion/core/game_data.py"),
    Path("spaceinvasion/planner/optimizer.py"),
    Path("spaceinvasion/combat/ranking.py"),
    Path("spaceinvasion/planner/strategy.py"),
    Path("spaceinvasion/runtime/commander_client.py"),
    Path("spaceinvasion/core/state.py"),
    Path("spaceinvasion/core/logging_utils.py"),
)

_RATE_LIMIT_MARKERS = (
    "rate-limit",
    "rate limit",
    "page-view throttle",
    "page-view budget",
    "page views",
    "cooldown",
    "too many requests",
    "http 429",
    "retry after",
)


def _safe_finite_float(val: Any) -> Optional[float]:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def compute_rate_limit_backoff(
    error_text: str,
    health_payload: Optional[Mapping[str, Any]] = None,
    *,
    min_backoff: float = 45.0,
) -> Optional[float]:
    """Return backoff seconds (>= `min_backoff`) when `error_text` or `health_payload` signals throttling."""
    low = _safe_cmd_str(error_text).lower()
    is_rl = any(marker in low for marker in _RATE_LIMIT_MARKERS) or (
        isinstance(health_payload, Mapping) and bool(health_payload.get("rate_limited"))
    )
    if not is_rl:
        return None

    min_f = _safe_finite_float(min_backoff)
    base_min = min_f if (min_f is not None and min_f > 0) else 45.0
    candidates: list[float] = [min(3600.0, max(1.0, base_min))]
    if isinstance(health_payload, Mapping):
        ra = _safe_finite_float(health_payload.get("retry_after"))
        if ra is not None and ra > 0:
            candidates.append(min(3600.0, ra))

    for pat in (
        r"(?:retry[-_ ]?after|cooldown(?:\s+active)?|wait(?:ing)?)\D{0,24}(\d+(?:\.\d+)?)\s*s?",
        r"(\d+(?:\.\d+)?)\s*s(?:ec(?:onds?)?)?\b",
    ):
        for m in re.finditer(pat, low, re.ASCII):
            try:
                val = float(m.group(1))
                if math.isfinite(val) and val > 0:
                    candidates.append(min(3600.0, val))
            except (ValueError, OverflowError):
                pass
    return min(3600.0, max(candidates))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SpaceInvasion Commander: evaluates strategy and sends commands to game_api.py."
    )
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8787",
        help="Base URL of game_api.py (default: http://127.0.0.1:8787).",
    )
    parser.add_argument("--live", action="store_true", help="Send live commands (default is dry-run).")
    parser.add_argument("--once", action="store_true", help="Run one tick and exit.")
    parser.add_argument("--loop", action="store_true", help="Run continuously.")
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="Min seconds between batch wakeups (0 = use config.action_batch_interval_seconds).",
    )
    parser.add_argument("--max-ticks", type=int, default=0, help="Max ticks in loop mode (0=unlimited).")
    parser.add_argument(
        "--max-actions",
        type=int,
        default=None,
        help="Max accumulated ready actions sent per tick (default: 10 in --loop mode, 1 in --once mode).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Optional non-secret strategy config file (default: config.yaml).",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs"),
        help="Directory for commander JSONL and readable `.log` files (default: logs).",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Hot-reload Commander automatically when strategy/commander files change.",
    )
    return parser


def _safe_cmd_int(val: Any, default: int = 0) -> int:
    return game_data.cap_json_int(game_data.safe_int(val, default))


def _safe_cmd_opt_int(val: Any) -> Optional[int]:
    parsed = game_data.safe_optional_int(val, None)
    if parsed is None:
        return None
    return game_data.cap_json_int(parsed)


def _safe_cmd_str(
    val: Any,
    default: str = "",
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> str:
    if val is None:
        return default
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except Exception:
            return default
        if val is None:
            return default
        if isinstance(val, os.PathLike):
            return default
    if isinstance(val, (bytes, bytearray)):
        txt = bytes(val).decode("utf-8", errors="replace")
    elif isinstance(val, BaseException):
        str_fn = getattr(type(val), "__str__", BaseException.__str__)
        used_custom_str = False
        txt = ""
        if str_fn not in (BaseException.__str__, KeyError.__str__):
            try:
                txt = str(val)[:16_384]
                used_custom_str = True
            except Exception:
                txt = ""
        if not used_custom_str:
            args = getattr(val, "args", ())
            if not isinstance(args, (tuple, list)) or not args:
                txt = "" if str_fn is BaseException.__str__ else type(val).__name__
            else:
                seen = _seen if _seen is not None else set()
                budget = _budget if _budget is not None else [256]
                vid = id(val)
                if vid in seen or _depth >= 16 or budget[0] <= 0:
                    txt = f"<{type(val).__name__}>"
                else:
                    budget[0] -= 1
                    seen.add(vid)
                    try:
                        if len(args) == 1:
                            a0 = args[0]
                            if isinstance(a0, str):
                                txt = (repr(a0) if isinstance(val, KeyError) else a0)[:16_384]
                            else:
                                txt = _safe_cmd_str(
                                    a0,
                                    f"<{type(a0).__name__}>",
                                    _depth=_depth + 1,
                                    _seen=seen,
                                    _budget=budget,
                                )
                        else:
                            parts = []
                            for a in args[:8]:
                                if budget[0] <= 0:
                                    parts.append("...")
                                    break
                                if isinstance(a, str):
                                    parts.append(repr(a))
                                else:
                                    parts.append(
                                        _safe_cmd_str(
                                            a,
                                            f"<{type(a).__name__}>",
                                            _depth=_depth + 1,
                                            _seen=seen,
                                            _budget=budget,
                                        )
                                    )
                            if len(args) > 8:
                                parts.append(f"...+{len(args) - 8}")
                            txt = ("(" + ", ".join(parts) + ")")[:4096]
                    finally:
                        seen.discard(vid)
    elif isinstance(val, int) and not isinstance(val, bool):
        txt = game_data.fmt_compact_int(val) if abs(val).bit_length() >= 12_000 else str(val)
    else:
        try:
            txt = game_data.safe_key(val)
        except Exception:
            return default
    txt = str(redact_sensitive(txt))
    if _depth == 0 and len(txt) > 1024:
        return txt[:1024]
    return txt[:4096] if len(txt) > 4096 else txt


def _action_to_command_payload(action: PlannedAction, *, dry_run: bool) -> dict:
    pid = _safe_cmd_int(action.planet_id, 0)
    if action.kind == "CLAIM_TUTORIAL":
        return {"command": "claim_tutorial", "dry_run": dry_run}
    if action.kind == "BUILD" and action.target_id is not None:
        return {
            "command": "build",
            "building_id": _safe_cmd_opt_int(action.target_id),
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "DEMOLISH_BUILDING" and action.target_id is not None:
        return {
            "command": "demolish_building",
            "building_id": _safe_cmd_opt_int(action.target_id) or 6,
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "RESEARCH" and action.target_id is not None:
        return {
            "command": "research",
            "research_id": _safe_cmd_opt_int(action.target_id),
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "BUILD_SHIP" and action.target_id is not None:
        return {
            "command": "build_ship",
            "ship_id": _safe_cmd_opt_int(action.target_id),
            "quantity": max(1, _safe_cmd_int(action.target_level, 1)),
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "THROTTLE_BUILDING" and action.target_id is not None:
        return {
            "command": "throttle_building",
            "building_id": _safe_cmd_opt_int(action.target_id),
            "percentage": 0 if action.target_level is None else _safe_cmd_int(action.target_level, 0),
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "ESCAPE_FLIGHT":
        t_name = _safe_cmd_str(action.target_name)
        m = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", t_name, re.ASCII)
        return {
            "command": "escape_flight",
            "planet_id": pid,
            "target_coords": m.group(1) if m else None,
            "reason": _safe_cmd_str(action.reason),
            "dry_run": dry_run,
        }
    if action.kind == "SCAN_GALAXY":
        t_name = _safe_cmd_str(action.target_name)
        p_coords = _safe_cmd_str(action.planet_coords)
        m = re.search(r"(\d{1,2}):(\d{1,3})", t_name, re.ASCII) or re.search(
            r"(\d{1,2}):(\d{1,3})", p_coords, re.ASCII
        )
        gal, sys_num = (int(m.group(1)), int(m.group(2))) if m else (1, 1)
        return {
            "command": "scan_galaxy",
            "galaxy": gal,
            "system": sys_num,
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "SPY":
        t_name = _safe_cmd_str(action.target_name)
        m = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", t_name, re.ASCII)
        return {
            "command": "spy",
            "target_coords": m.group(1) if m else t_name,
            "quantity": max(1, _safe_cmd_int(action.target_level, 1)),
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "RECYCLE":
        t_name = _safe_cmd_str(action.target_name)
        m = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", t_name, re.ASCII)
        return {
            "command": "recycle",
            "target_coords": m.group(1) if m else t_name,
            "quantity": max(1, _safe_cmd_int(action.target_level, 1)),
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "COLONIZE":
        t_name = _safe_cmd_str(action.target_name)
        m = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", t_name, re.ASCII)
        return {
            "command": "colonize",
            "target_coords": m.group(1) if m else t_name,
            "planet_id": pid,
            "dry_run": dry_run,
        }
    if action.kind == "ATTACK":
        t_name = _safe_cmd_str(action.target_name)
        m = re.search(r"(\d{1,2}:\d{1,3}:\d{1,2})", t_name, re.ASCII)
        return {
            "command": "attack",
            "target_coords": m.group(1) if m else t_name,
            "planet_id": pid,
            "dry_run": dry_run,
        }
    return {"command": "plan", "dry_run": True}


def _hold_busy_fleet_slots(
    actions: list[PlannedAction],
    fleet_slot_cooldowns: Optional[Mapping[int, float]],
    *,
    re_sort_priority: bool = False,
) -> list[PlannedAction]:
    if not fleet_slot_cooldowns or not actions:
        return actions
    busy_pids: set[int] = set()
    for k, v in fleet_slot_cooldowns.items():
        fv = _safe_finite_float(v)
        if fv is None or fv <= 0.0:
            continue
        if isinstance(k, int) and not isinstance(k, bool):
            busy_pids.add(k)
        else:
            ks = _safe_cmd_str(k).strip()
            if re.fullmatch(r"-?\d{1,18}", ks, re.ASCII):
                busy_pids.add(int(ks))
    if not busy_pids:
        return actions
    hold_suffix = " [Hold: fleet slots busy / returning]"
    changed = False
    out: list[PlannedAction] = []
    for act in actions:
        if act.kind in ("ATTACK", "SPY", "RECYCLE", "ESCAPE_FLIGHT", "COLONIZE") and act.planet_id in busy_pids:
            cur_reason = _safe_cmd_str(act.reason)
            new_reason = (
                cur_reason
                if "[Hold: fleet slots busy / returning]" in cur_reason
                else f"{cur_reason}{hold_suffix}"
            )
            if act.executable_now or new_reason != cur_reason:
                changed = True
                out.append(replace(act, executable_now=False, reason=new_reason))
                continue
        out.append(act)
    if changed and re_sort_priority:
        prio_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        out.sort(
            key=lambda a: (
                prio_rank.get(a.priority, 9),
                -1 if a.kind == "ALERT_HOSTILE" else (0 if a.executable_now else 1),
            )
        )
    return out


def run_commander_tick(
    api: GameApiClient,
    *,
    live: bool,
    max_actions: int,
    config: Optional[StrategyConfig] = None,
    include_buildings: bool = True,
    scanned_systems: Optional[Mapping[str, float]] = None,
    throttle_cooldowns: Optional[Mapping[int, float]] = None,
    economy_sync_timestamps: Optional[Mapping[int, float]] = None,
    fleet_slot_cooldowns: Optional[Mapping[int, float]] = None,
    manual_queue: Optional[Sequence[Mapping[str, Any]]] = None,
) -> tuple[GameState, Optional[PlannedAction], Optional[dict], Optional[PlannedAction], list[dict]]:
    """Fetch state via HTTP, compute `plan_actions`, and send commands.

    Returns `(state, actual_action, actual_result, next_action, outcomes)`.
    """
    cfg = config or StrategyConfig()
    state = api.get_state(include_buildings=include_buildings)
    active_for_econ = state.active_planet()
    if (
        live
        and economy_sync_timestamps is not None
        and active_for_econ is not None
        and normalize_planet_type(active_for_econ) not in ("asteroid", "raid")
        and active_for_econ.building_level(2) >= 1
    ):
        has_r2 = isinstance(active_for_econ.economy_rates, Mapping) and "resource2" in active_for_econ.economy_rates
        raw_ts = economy_sync_timestamps.get(
            active_for_econ.planet_id,
            economy_sync_timestamps.get(str(active_for_econ.planet_id), 0.0),
        )
        last_econ_ts = _safe_finite_float(raw_ts) or 0.0
        econ_refresh_ttl = max(
            3600.0,
            float(_safe_cmd_int(getattr(cfg, "action_batch_interval_seconds", 120), 120)) * 10.0,
        )
        if (not has_r2) or (last_econ_ts <= 0.0) or (include_buildings and (time.time() - last_econ_ts) >= econ_refresh_ttl):
            try:
                sync_res = api.send_command(
                    {"command": "sync_economy", "planet_id": active_for_econ.planet_id}
                )
                if isinstance(sync_res, Mapping) and isinstance(sync_res.get("state"), Mapping):
                    state = game_state_from_dict(sync_res["state"])
            except (requests.RequestException, GameApiError) as sync_exc:
                if compute_rate_limit_backoff(_safe_cmd_str(sync_exc)) is not None:
                    raise
    is_ga_nn = str(getattr(cfg, "planner_mode", "priority") or "priority").strip().lower() == "ga_nn"
    cur_mq: Optional[Sequence[Mapping[str, Any]]] = manual_queue
    active_fsc: dict[int, float] = dict(fleet_slot_cooldowns) if fleet_slot_cooldowns else {}

    def _compute_tick_actions(cur_st: GameState, mq_items: Optional[Sequence[Mapping[str, Any]]]) -> list[PlannedAction]:
        base = _hold_busy_fleet_slots(
            plan_actions(
                cur_st,
                config=cfg,
                scanned_systems=scanned_systems,
                throttle_cooldowns=throttle_cooldowns,
            ),
            active_fsc,
            re_sort_priority=not is_ga_nn,
        )
        if mq_items:
            merged, _ = merge_manual_queue_actions(cur_st, base, mq_items, config=cfg)
            return _hold_busy_fleet_slots(merged, active_fsc, re_sort_priority=False)
        return base

    actions = _compute_tick_actions(state, cur_mq)
    if not include_buildings and not any(a.executable_now for a in actions):
        # Refresh buildings immediately ONLY if resources now cover an affordable wait item
        # not blocked by a full build queue or un-unlocked/future card.
        active_p = state.active_planet()
        b_queued = sum(1 for q in state.active_queues if q.category == "building")
        is_astro = active_p is not None and is_asteroid_planet(active_p)
        raw_cmd = (state.specs or {}).get("construction_commander", 0)
        has_cmd = game_data.safe_int(raw_cmd, 0) > 0
        queue_cap = 1 if is_astro else (6 if has_cmd else 5)
        active_queue_full = b_queued >= queue_cap
        for act in actions:
            if act.kind not in ("WAIT_RESOURCES", "BUILD") or act.target_id is None or act.cost.is_zero():
                continue
            reason_l = (act.reason or "").lower()
            if (
                "build queue full" in reason_l
                or "waiting/queued" in reason_l
                or "not currently visible/unlocked" in reason_l
                or "future/queued" in reason_l
            ):
                continue
            if active_p is not None and act.planet_id == active_p.planet_id and active_queue_full:
                continue
            p_match = next((p for p in state.planets if p.planet_id == act.planet_id), None)
            if p_match is None or not p_match.resources.can_afford(act.cost):
                continue
            offer = next((o for o in p_match.building_offers if o.building_id == act.target_id), None)
            if offer is not None and offer.can_build:
                continue
            state = api.get_state(include_buildings=True)
            actions = _compute_tick_actions(state, cur_mq)
            break

    outcomes: list[dict] = []
    actual_action: Optional[PlannedAction] = None
    actual_result: Optional[dict] = None
    after_live = False

    for alert in [a for a in actions if a.priority == "P0"]:
        print(f"[CMD][P0 ALERT] {redact_sensitive(_safe_cmd_str(alert.reason))}")

    acted = 0
    attempted_keys: set[tuple[str, int, Optional[int], Optional[int], str]] = set()
    while acted < max_actions:
        ready = next(
            (
                a
                for a in actions
                if a.executable_now
                and (
                    a.kind,
                    _safe_cmd_int(a.planet_id, 0),
                    _safe_cmd_opt_int(a.target_id),
                    _safe_cmd_opt_int(a.target_level),
                    _safe_cmd_str(a.target_name),
                )
                not in attempted_keys
            ),
            None,
        )
        if ready is None:
            break
        act_key = (
            ready.kind,
            _safe_cmd_int(ready.planet_id, 0),
            _safe_cmd_opt_int(ready.target_id),
            _safe_cmd_opt_int(ready.target_level),
            _safe_cmd_str(ready.target_name),
        )
        attempted_keys.add(act_key)
        actual_action = ready
        payload = _action_to_command_payload(ready, dry_run=not live)
        res = api.send_command(payload)
        if not isinstance(res, Mapping):
            raise GameApiError(
                f"Expected JSON object from send_command, got {type(res).__name__}"
            )
        res_dict = dict(res) if not isinstance(res, dict) else res
        if isinstance(res_dict.get("manual_queue"), list):
            cur_mq = [x for x in res_dict["manual_queue"] if isinstance(x, Mapping)]
        actual_result = res_dict
        outcomes.append(res_dict)
        acted += 1
        if not live:
            break
        if not res_dict.get("executed"):
            fail_msg_l = _safe_cmd_str(res_dict.get("message", "")).lower()
            if (
                ready.kind in ("ATTACK", "SPY", "RECYCLE", "ESCAPE_FLIGHT", "COLONIZE")
                and ("no ship input fields found on internalfleet" in fail_msg_l or "nofleet" in fail_msg_l)
            ):
                pid_busy = _safe_cmd_int(ready.planet_id, 0)
                if pid_busy > 0:
                    active_fsc[pid_busy] = max(active_fsc.get(pid_busy, 0.0), 90.0)
                if isinstance(res_dict.get("state"), Mapping):
                    state = game_state_from_dict(res_dict["state"])
                actions = _compute_tick_actions(state, cur_mq)
                continue
            if isinstance(res_dict.get("state"), Mapping):
                state = game_state_from_dict(res_dict["state"])
                actions = _compute_tick_actions(state, cur_mq)
                continue
            if ready.kind in ("ATTACK", "SPY", "RECYCLE", "COLONIZE"):
                continue
            break
        after_live = True
        if isinstance(res_dict.get("state"), Mapping):
            state = game_state_from_dict(res_dict["state"])
        elif acted < max_actions:
            state = api.get_state(include_buildings=False)
        actions = _compute_tick_actions(state, cur_mq)

    if actual_action is None and actions:
        actual_action = actions[0]
        actual_result = {
            "ok": True,
            "executed": False,
            "dry_run": not live,
            "message": redact_sensitive(
                f"Waiting: {_safe_cmd_str(actual_action.target_name)} ({_safe_cmd_str(actual_action.reason)})"
            ),
            "action": redact_sensitive(asdict(actual_action)),
        }
        outcomes.append(actual_result)

    next_action = pick_next_action(actions, actual_action, after_live_execution=after_live)
    if (
        next_action is not None
        and not next_action.executable_now
        and any(a.executable_now for a in actions)
    ):
        next_ready = next((a for a in actions if a.executable_now), None)
        if next_ready is not None:
            next_action = next_ready
    return state, actual_action, actual_result, next_action, outcomes


_QUEUE_HMS_RE = re.compile(r"\b(\d{1,3}):(\d{2}):(\d{2})\b", re.ASCII)
_QUEUE_UNIT_RE = re.compile(
    r"(?:(\d+)\s*h\s*)?(?:(\d+)\s*m(?:in)?\s*)?(\d+)\s*s\b",
    re.IGNORECASE | re.ASCII,
)


def _parse_queue_timer_seconds(text: str) -> Optional[float]:
    if not text:
        return None
    m_hms = _QUEUE_HMS_RE.search(text)
    if m_hms:
        return float(int(m_hms.group(1)) * 3600 + int(m_hms.group(2)) * 60 + int(m_hms.group(3)))
    m_u = _QUEUE_UNIT_RE.search(text)
    if m_u:
        h = int(m_u.group(1) or 0)
        m = int(m_u.group(2) or 0)
        s = int(m_u.group(3) or 0)
        tot = float(h * 3600 + m * 60 + s)
        return tot if tot > 0.0 else None
    return None


def compute_next_wakeup_seconds(
    state: Optional[GameState],
    next_action: Optional[PlannedAction],
    outcomes: list[dict] | tuple[dict, ...] = (),
    *,
    min_interval_s: float = 120.0,
    max_idle_s: float = 900.0,
    fleet_slot_cooldowns: Optional[Mapping[int, float]] = None,
) -> tuple[float, str]:
    """Compute exact seconds until next batch wakeup so pages are only visited when ready to act."""
    floor_s = max(0.01, float(min_interval_s))
    cap_s = max(floor_s, float(max_idle_s))

    slot_busy_seen = False
    for out in outcomes or ():
        if not isinstance(out, Mapping):
            continue
        if bool(out.get("executed")):
            cmd_s = _safe_cmd_str(out.get("command")).lower()
            act_m = out.get("action")
            kind_s = _safe_cmd_str(act_m.get("kind") if isinstance(act_m, Mapping) else "").upper()
            if cmd_s == "spy" or kind_s == "SPY":
                return floor_s, "awaiting spy report & next batch"
        else:
            out_msg_l = _safe_cmd_str(out.get("message", "")).lower()
            if "no ship input fields found on internalfleet" in out_msg_l or "nofleet" in out_msg_l or "fleet slots busy" in out_msg_l:
                slot_busy_seen = True

    cand_waits: list[tuple[float, str]] = []
    if slot_busy_seen or (fleet_slot_cooldowns and any((_safe_finite_float(v) or 0.0) > 0.0 for v in fleet_slot_cooldowns.values())):
        fsc_vals = [
            fv
            for v in (fleet_slot_cooldowns or {}).values()
            if (fv := _safe_finite_float(v)) is not None and fv > 0.0
        ]
        slot_s = min(fsc_vals) + 2.0 if fsc_vals else max(floor_s, 90.0)
        cand_waits.append((slot_s, "fleet slot cooldown / returning ships"))

    if next_action is not None:
        t_lbl = _safe_cmd_str(next_action.target_name, next_action.kind)
        if next_action.executable_now:
            cand_waits.append((floor_s, f"next batch ({next_action.kind}: {t_lbl})"))
        elif "[Hold: fleet slots busy / returning]" in _safe_cmd_str(next_action.reason):
            cand_waits.append((max(floor_s, 90.0), f"fleet slots returning ({t_lbl})"))
        else:
            w_sec = _safe_finite_float(getattr(next_action, "wait_seconds", None))
            if w_sec is not None and w_sec > 0.0:
                cand_waits.append((w_sec + 2.0, f"build/resource timer for {t_lbl}"))

    if state is not None:
        queues = getattr(state, "active_queues", ()) or ()
        b_q_cnt = sum(1 for q in queues if getattr(q, "category", "") == "building")
        for q in queues:
            q_cat = _safe_cmd_str(getattr(q, "category", "")).lower()
            if q_cat not in ("fleet", "research") and not (q_cat == "building" and b_q_cnt >= 5):
                continue
            q_rem = _parse_queue_timer_seconds(
                f"{getattr(q, 'remaining_text', '') or ''} {getattr(q, 'name', '') or ''}"
            )
            if q_rem is not None and q_rem > 0.0:
                cand_waits.append(
                    (q_rem + 2.0, f"{q_cat} queue completion ({_safe_cmd_str(getattr(q, 'name', q_cat))})")
                )

    if cand_waits:
        best_s, best_reason = min(cand_waits, key=lambda item: item[0])
        return min(max(floor_s, best_s), cap_s), best_reason
    return floor_s, "batch interval"


_CAPTCHA_STOP_MARKERS = (
    "antibot",
    "anti-bot",
    "captcha",
    "turnstile",
    "sicherheitsprüfung",
    "sicherheitspruefung",
    "kein bot",
    "not a bot",
    "cloudflare gate",
)


def is_captcha_error(
    error_text: Any = "",
    *,
    bot_ctrl: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Return True when an error message or `/bot_status` payload indicates a CAPTCHA / anti-bot challenge."""
    if isinstance(bot_ctrl, Mapping) and bool(bot_ctrl.get("captcha_blocked")):
        return True
    low = _safe_cmd_str(error_text).lower()
    return any(marker in low for marker in _CAPTCHA_STOP_MARKERS)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if should_spawn_reloader(args.reload):
        raw_argv = sys.argv[1:] if argv is None else argv
        return watch_and_run(
            label="CMD",
            cmd=current_python_cmd(Path(__file__).resolve(), raw_argv),
            watch_paths=WATCH_PATHS,
        )

    is_main = threading.current_thread() is threading.main_thread()
    prev_sigterm = signal.getsignal(signal.SIGTERM) if is_main else None

    def _shutdown_handler(_signum, _frame) -> None:
        raise KeyboardInterrupt

    if is_main:
        signal.signal(signal.SIGTERM, _shutdown_handler)
    strategy_cfg = load_strategy_config(args.config)
    api = GameApiClient(base_url=args.api_url)
    run_loop = args.loop and not args.once
    iv_f = _safe_finite_float(args.interval)
    has_cli_interval = iv_f is not None and iv_f > 0.0
    cli_interval_s = max(0.01, min(86400.0, iv_f)) if has_cli_interval else 0.0

    try:
        tick = 0
        last_recovery_at = 0.0
        last_full_buildings_at = 0.0
        while True:
            if args.max_ticks > 0 and tick >= args.max_ticks:
                break
            tick += 1
            loop_sleep: Optional[float] = None
            try:
                strategy_cfg = load_strategy_config(args.config)
                cfg_batch_s = max(
                    5.0,
                    min(
                        86400.0,
                        float(_safe_cmd_int(getattr(strategy_cfg, "action_batch_interval_seconds", 120), 120)),
                    ),
                )
                eff_min_interval_s = cli_interval_s if has_cli_interval else cfg_batch_s
                eff_max_idle_s = (
                    eff_min_interval_s
                    if (has_cli_interval and cli_interval_s < 5.0)
                    else max(eff_min_interval_s, 900.0)
                )
                effective_live = args.live
                scanned_systems: Optional[dict[str, float]] = None
                throttle_cooldowns: Optional[dict[int, float]] = None
                economy_sync_timestamps: Optional[dict[int, float]] = None
                fleet_slot_cooldowns: Optional[dict[int, float]] = None
                manual_queue: Optional[list[Mapping[str, Any]]] = None
                paused_via_ui = False
                try:
                    bot_ctrl = api.get_bot_control()
                    if not isinstance(bot_ctrl, Mapping):
                        raise GameApiError(
                            f"Expected JSON object from /bot_status, got {type(bot_ctrl).__name__}"
                        )
                    if is_captcha_error(bot_ctrl.get("captcha_message"), bot_ctrl=bot_ctrl):
                        cap_msg = _safe_cmd_str(
                            bot_ctrl.get("captcha_message"),
                            "CAPTCHA / anti-bot challenge active",
                        )
                        print(
                            f"[CMD][STOP] CAPTCHA / anti-bot challenge detected ({redact_sensitive(cap_msg)}) — "
                            "stopping automation immediately. Complete verification in Chrome before restarting."
                        )
                        return 2
                    if run_loop and bot_ctrl.get("paused"):
                        print(f"[CMD tick {game_data.fmt_compact_int(tick)}] Paused via control UI; waiting.")
                        paused_via_ui = True
                        loop_sleep = max(3.0, min(8.0, eff_min_interval_s))
                    else:
                        override = bot_ctrl.get("mode_override")
                        if override in ("live", "dry-run"):
                            effective_live = override == "live"
                        raw_scans = bot_ctrl.get("scanned_systems")
                        if isinstance(raw_scans, Mapping):
                            parsed_scans: dict[str, float] = {}
                            for k, v in raw_scans.items():
                                fv = _safe_finite_float(v)
                                if fv is not None:
                                    parsed_scans[_safe_cmd_str(k)] = fv
                            scanned_systems = parsed_scans
                        raw_cd = bot_ctrl.get("throttle_cooldowns")
                        if isinstance(raw_cd, Mapping):
                            parsed_cd: dict[int, float] = {}
                            for k, v in raw_cd.items():
                                fv = _safe_finite_float(v)
                                if fv is None:
                                    continue
                                if isinstance(k, int) and not isinstance(k, bool):
                                    if abs(k).bit_length() <= 64:
                                        parsed_cd[k] = fv
                                else:
                                    ks = _safe_cmd_str(k).strip()
                                    if re.fullmatch(r"-?\d{1,18}", ks, re.ASCII):
                                        parsed_cd[int(ks)] = fv
                            throttle_cooldowns = parsed_cd
                        raw_es = bot_ctrl.get("economy_sync_timestamps")
                        if isinstance(raw_es, Mapping):
                            parsed_es: dict[int, float] = {}
                            for k, v in raw_es.items():
                                fv = _safe_finite_float(v)
                                if fv is None:
                                    continue
                                if isinstance(k, int) and not isinstance(k, bool):
                                    if abs(k).bit_length() <= 64:
                                        parsed_es[k] = fv
                                else:
                                    ks = _safe_cmd_str(k).strip()
                                    if re.fullmatch(r"-?\d{1,18}", ks, re.ASCII):
                                        parsed_es[int(ks)] = fv
                            economy_sync_timestamps = parsed_es
                        raw_fsc = bot_ctrl.get("fleet_slot_cooldowns")
                        if isinstance(raw_fsc, Mapping):
                            parsed_fsc: dict[int, float] = {}
                            for k, v in raw_fsc.items():
                                fv = _safe_finite_float(v)
                                if fv is None or fv <= 0.0:
                                    continue
                                if isinstance(k, int) and not isinstance(k, bool):
                                    if abs(k).bit_length() <= 64:
                                        parsed_fsc[k] = fv
                                else:
                                    ks = _safe_cmd_str(k).strip()
                                    if re.fullmatch(r"-?\d{1,18}", ks, re.ASCII):
                                        parsed_fsc[int(ks)] = fv
                            fleet_slot_cooldowns = parsed_fsc
                        raw_mq = bot_ctrl.get("manual_queue")
                        if isinstance(raw_mq, list):
                            manual_queue = [x for x in raw_mq if isinstance(x, Mapping)]
                except (requests.RequestException, GameApiError):
                    pass

                if not paused_via_ui:
                    now_mono = time.monotonic()
                    need_buildings = (
                        last_full_buildings_at <= 0.0
                        or (now_mono - last_full_buildings_at) >= 60.0
                    )

                    eff_label = "LIVE" if effective_live else "DRY-RUN"
                    effective_max_actions = max(
                        1,
                        args.max_actions if args.max_actions is not None else (10 if run_loop else 1),
                    )
                    state, actual_action, actual_result, next_action, outcomes = run_commander_tick(
                        api,
                        live=effective_live,
                        max_actions=effective_max_actions,
                        config=strategy_cfg,
                        include_buildings=need_buildings,
                        scanned_systems=scanned_systems,
                        throttle_cooldowns=throttle_cooldowns,
                        economy_sync_timestamps=economy_sync_timestamps,
                        fleet_slot_cooldowns=fleet_slot_cooldowns,
                        manual_queue=manual_queue,
                    )
                    refreshed_buildings = need_buildings or (
                        effective_live
                        and any(
                            isinstance(o, Mapping)
                            and bool(o.get("executed"))
                            and (
                                (
                                    isinstance(o.get("action"), Mapping)
                                    and _safe_cmd_str((o.get("action") or {}).get("kind")).upper()
                                    in ("BUILD", "CLAIM_TUTORIAL")
                                )
                                or _safe_cmd_str(o.get("command")).lower()
                                in ("build", "claim_tutorial")
                            )
                            for o in outcomes
                        )
                    )
                    if refreshed_buildings:
                        last_full_buildings_at = time.monotonic()
                    planet = state.active_planet()
                    res_str = planet.resources.format_short() if planet else "?"
                    raw_msg = (
                        actual_result.get("message", "no action")
                        if isinstance(actual_result, Mapping)
                        else "no action"
                    )
                    msg = redact_sensitive(_safe_cmd_str(raw_msg, "no action"))
                    coords_txt = (
                        redact_sensitive(_safe_cmd_str(planet.coords, "?"))
                        if (planet and planet.coords is not None)
                        else "?"
                    )
                    metal_tag = ""
                    if (
                        planet is not None
                        and normalize_planet_type(planet) not in ("asteroid", "raid")
                        and planet.building_level(2) >= 1
                    ):
                        raw_r2 = (planet.economy_rates or {}).get("resource2", 100)
                        if isinstance(raw_r2, str) and raw_r2.strip().endswith("%"):
                            raw_r2 = raw_r2.strip()[:-1].strip()
                        b2_pct = max(
                            0,
                            min(
                                100,
                                _safe_cmd_int(raw_r2, 100),
                            ),
                        )
                        metal_tag = f" | MetalProd={'ON' if b2_pct > 0 else 'OFF'}({b2_pct}%)"
                    executed_count = sum(
                        1 for o in outcomes if isinstance(o, Mapping) and bool(o.get("executed"))
                    )
                    batch_tag = f" | BatchExec={executed_count}" if executed_count > 1 else ""
                    print(f"[CMD tick {game_data.fmt_compact_int(tick)} | {eff_label}] {coords_txt} | {res_str}{metal_tag}{batch_tag}")
                    print(f"  Actual: {format_action_summary(actual_action)} => {msg}")
                    print(f"  Next:   {format_action_summary(next_action)}")
                    if run_loop:
                        wake_s, wake_reason = compute_next_wakeup_seconds(
                            state,
                            next_action,
                            outcomes,
                            min_interval_s=eff_min_interval_s,
                            max_idle_s=eff_max_idle_s,
                            fleet_slot_cooldowns=fleet_slot_cooldowns,
                        )
                        jitter_s = random.uniform(0.5, 2.5) if wake_s >= 5.0 else 0.0
                        loop_sleep = wake_s + jitter_s
                        if loop_sleep >= 5.0:
                            print(f"  Wake:   in {loop_sleep:.0f}s ({redact_sensitive(wake_reason)})")
                    if args.log_dir:
                        append_tick_logs(
                            args.log_dir,
                            source="commander",
                            tick=tick,
                            mode=eff_label,
                            state=state,
                            actual_action=actual_action,
                            actual_result=actual_result,
                            next_action=next_action,
                            outcomes=outcomes,
                        )
            except (requests.RequestException, GameApiError) as exc:
                exc_txt = _safe_cmd_str(exc)
                tick_s = game_data.fmt_compact_int(tick)
                if is_captcha_error(exc_txt):
                    try:
                        api.send_command({"command": "bot_control", "paused": True})
                    except Exception:
                        pass
                    print(
                        f"[CMD][STOP] CAPTCHA / anti-bot challenge detected ({redact_sensitive(exc_txt)}) — "
                        "stopping automation immediately. Complete verification in Chrome before restarting."
                    )
                    return 2
                print(f"[CMD tick {tick_s}] API unavailable ({redact_sensitive(exc_txt)}); retrying shortly.")
                if not run_loop:
                    return 1
                err_lower = exc_txt.lower()
                health_info: Optional[dict[str, Any]] = None
                if any(marker in err_lower for marker in _RATE_LIMIT_MARKERS):
                    try:
                        h_raw = api.health()
                        health_info = h_raw if isinstance(h_raw, dict) else (dict(h_raw) if isinstance(h_raw, Mapping) else None)
                    except Exception:
                        health_info = None
                rl_backoff = compute_rate_limit_backoff(exc_txt, health_payload=health_info)
                if rl_backoff is not None:
                    wait_s = rl_backoff + random.uniform(1.0, 3.0)
                    print(f"[CMD tick {tick_s}] Rate-limit cooldown active; backing off {wait_s:.1f}s.")
                    loop_sleep = wait_s
                elif (
                    isinstance(exc, GameApiError)
                    and any(
                        marker in err_lower
                        for marker in (
                            "not inside spaceinvasion game shell",
                            "timed out waiting for frame",
                            "session expired",
                            "missing #json-container",
                        )
                    )
                    and (time.monotonic() - last_recovery_at) >= 25.0
                ):
                    last_recovery_at = time.monotonic()
                    try:
                        api.send_command({"command": "ensure_session"})
                        print(f"[CMD tick {tick_s}] Recovered browser session via ensure_session.")
                        loop_sleep = 1.0
                    except (requests.RequestException, GameApiError) as rec_exc:
                        rec_txt = _safe_cmd_str(rec_exc)
                        if is_captcha_error(rec_txt):
                            try:
                                api.send_command({"command": "bot_control", "paused": True})
                            except Exception:
                                pass
                            print(
                                f"[CMD][STOP] CAPTCHA / anti-bot challenge detected ({redact_sensitive(rec_txt)}) — "
                                "stopping automation immediately. Complete verification in Chrome before restarting."
                            )
                            return 2
                        print(f"[CMD tick {tick_s}] Session recovery pending: {redact_sensitive(rec_txt)}")
                        if any(gate in rec_txt.lower() for gate in ("2-factor", "2fa")):
                            loop_sleep = 15.0
                        else:
                            loop_sleep = 3.0
                else:
                    loop_sleep = 3.0

            if not run_loop:
                break
            if args.max_ticks > 0 and tick >= args.max_ticks:
                break
            if loop_sleep is None:
                jitter_f = random.uniform(0.5, 2.5) if eff_min_interval_s >= 5.0 else 0.0
                loop_sleep = max(0.01, eff_min_interval_s + jitter_f)
            time.sleep(loop_sleep)
        return 0
    except KeyboardInterrupt:
        print("\n[CMD] Stopped.")
        return 0
    finally:
        close_fn = getattr(api, "close", None)
        if callable(close_fn):
            close_fn()
        if is_main and prev_sigterm is not None:
            signal.signal(signal.SIGTERM, prev_sigterm)


if __name__ == "__main__":
    raise SystemExit(main())
