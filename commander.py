#!/usr/bin/env python3
"""Hot-reloadable Commander client communicating exclusively over HTTP with `game_api.py`."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import math
import os
from pathlib import Path
import random
import re
import signal
import sys
import threading
import time
from typing import Any, Mapping, Optional

import requests


def _maybe_reexec_venv() -> None:
    if os.environ.get("SPACEINVASION_NO_REEXEC") == "1":
        return
    if sys.prefix != sys.base_prefix:
        return
    venv_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python3"
    if venv_python.exists() and os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


_maybe_reexec_venv()

from spaceinvasion import game_data  # noqa: E402
from spaceinvasion.commander_client import GameApiClient, GameApiError  # noqa: E402
from spaceinvasion.config import StrategyConfig, load_strategy_config  # noqa: E402
from spaceinvasion.logging_utils import (  # noqa: E402
    append_tick_logs,
    format_action_summary,
    pick_next_action,
    redact_sensitive,
)
from spaceinvasion.reloader import (  # noqa: E402
    current_python_cmd,
    should_spawn_reloader,
    watch_and_run,
)
from spaceinvasion.state import GameState, game_state_from_dict, is_asteroid_planet  # noqa: E402
from spaceinvasion.strategy import PlannedAction, plan_actions  # noqa: E402

WATCH_PATHS = (
    Path("commander.py"),
    Path("config.yaml"),
    Path("spaceinvasion/config.py"),
    Path("spaceinvasion/fleet.py"),
    Path("spaceinvasion/game_data.py"),
    Path("spaceinvasion/optimizer.py"),
    Path("spaceinvasion/ranking.py"),
    Path("spaceinvasion/strategy.py"),
    Path("spaceinvasion/commander_client.py"),
    Path("spaceinvasion/state.py"),
    Path("spaceinvasion/logging_utils.py"),
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
    parser.add_argument("--interval", type=float, default=20.0, help="Seconds between ticks.")
    parser.add_argument("--max-ticks", type=int, default=0, help="Max ticks in loop mode (0=unlimited).")
    parser.add_argument("--max-actions", type=int, default=1, help="Max commands per tick.")
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
        return {"command": "fleetsave_plan", "planet_id": pid}
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


def run_commander_tick(
    api: GameApiClient,
    *,
    live: bool,
    max_actions: int,
    config: Optional[StrategyConfig] = None,
    include_buildings: bool = True,
    scanned_systems: Optional[Mapping[str, float]] = None,
    throttle_cooldowns: Optional[Mapping[int, float]] = None,
) -> tuple[GameState, Optional[PlannedAction], Optional[dict], Optional[PlannedAction], list[dict]]:
    """Fetch state via HTTP, compute `plan_actions`, and send commands.

    Returns `(state, actual_action, actual_result, next_action, outcomes)`.
    """
    cfg = config or StrategyConfig()
    state = api.get_state(include_buildings=include_buildings)
    actions = plan_actions(
        state,
        config=cfg,
        scanned_systems=scanned_systems,
        throttle_cooldowns=throttle_cooldowns,
    )
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
            if act.kind != "WAIT_RESOURCES" or act.target_id is None or act.cost.is_zero():
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
            actions = plan_actions(
                state,
                config=cfg,
                scanned_systems=scanned_systems,
                throttle_cooldowns=throttle_cooldowns,
            )
            break

    outcomes: list[dict] = []
    actual_action: Optional[PlannedAction] = None
    actual_result: Optional[dict] = None
    after_live = False

    for alert in [a for a in actions if a.priority == "P0"]:
        print(f"[CMD][P0 ALERT] {redact_sensitive(_safe_cmd_str(alert.reason))}")

    acted = 0
    while acted < max_actions:
        ready = next((a for a in actions if a.executable_now), None)
        if ready is None:
            break
        actual_action = ready
        payload = _action_to_command_payload(ready, dry_run=not live)
        res = api.send_command(payload)
        if not isinstance(res, Mapping):
            raise GameApiError(
                f"Expected JSON object from send_command, got {type(res).__name__}"
            )
        res_dict = dict(res) if not isinstance(res, dict) else res
        actual_result = res_dict
        outcomes.append(res_dict)
        acted += 1
        if not live or not res_dict.get("executed"):
            break
        after_live = True
        if isinstance(res_dict.get("state"), Mapping):
            state = game_state_from_dict(res_dict["state"])
        elif acted < max_actions:
            state = api.get_state(include_buildings=True)
        actions = plan_actions(
            state,
            config=cfg,
            scanned_systems=scanned_systems,
            throttle_cooldowns=throttle_cooldowns,
        )

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
    return state, actual_action, actual_result, next_action, outcomes


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
    interval_s = max(0.01, min(86400.0, iv_f if (iv_f is not None and iv_f > 0.0) else 20.0))

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
                effective_live = args.live
                scanned_systems: Optional[dict[str, float]] = None
                throttle_cooldowns: Optional[dict[int, float]] = None
                paused_via_ui = False
                try:
                    bot_ctrl = api.get_bot_control()
                    if not isinstance(bot_ctrl, Mapping):
                        raise GameApiError(
                            f"Expected JSON object from /bot_status, got {type(bot_ctrl).__name__}"
                        )
                    if run_loop and bot_ctrl.get("paused"):
                        print(f"[CMD tick {game_data.fmt_compact_int(tick)}] Paused via control UI; waiting.")
                        paused_via_ui = True
                        loop_sleep = max(3.0, min(8.0, interval_s))
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
                except (requests.RequestException, GameApiError):
                    pass

                if not paused_via_ui:
                    now_mono = time.monotonic()
                    need_buildings = (
                        last_full_buildings_at <= 0.0
                        or (now_mono - last_full_buildings_at) >= 60.0
                    )

                    eff_label = "LIVE" if effective_live else "DRY-RUN"
                    state, actual_action, actual_result, next_action, outcomes = run_commander_tick(
                        api,
                        live=effective_live,
                        max_actions=max(1, args.max_actions),
                        config=strategy_cfg,
                        include_buildings=need_buildings,
                        scanned_systems=scanned_systems,
                        throttle_cooldowns=throttle_cooldowns,
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
                    print(f"[CMD tick {game_data.fmt_compact_int(tick)} | {eff_label}] {coords_txt} | {res_str}")
                    print(f"  Actual: {format_action_summary(actual_action)} => {msg}")
                    print(f"  Next:   {format_action_summary(next_action)}")
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
                        print(f"[CMD tick {tick_s}] Session recovery pending: {redact_sensitive(rec_txt)}")
                        if any(
                            gate in rec_txt.lower()
                            for gate in ("captcha", "turnstile", "anti-bot", "2-factor", "2fa")
                        ):
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
                loop_sleep = max(5.0, interval_s + random.uniform(-2.0, 4.0))
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
