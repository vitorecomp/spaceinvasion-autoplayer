#!/usr/bin/env python3
"""Autoplayer runner loop (M3): attaches to Chrome and runs advisor + safe executor."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import random
import sys
import time
from typing import Optional


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
from spaceinvasion.session.auth import SpaceInvasionAuthError, load_credentials  # noqa: E402
from spaceinvasion.session.browser import (  # noqa: E402
    DEFAULT_DEBUG_HOST,
    DEFAULT_DEBUG_PORT,
    DEFAULT_PROFILE_ROOT,
    BrowserConfig,
    is_debug_port_open,
    login_with_browser,
)
from spaceinvasion.session.client import GameBrowserClient, RateLimitError  # noqa: E402
from spaceinvasion.session.executor import ActionExecutor, ExecutionResult  # noqa: E402
from spaceinvasion.core.logging_utils import (  # noqa: E402
    append_tick_logs,
    format_action_summary,
    pick_next_action,
    redact_sensitive,
)
from spaceinvasion.planner.strategy import PlannedAction, plan_actions  # noqa: E402


def _safe_bot_str(
    val: object,
    default: str = "",
    *,
    _depth: int = 0,
    _seen: set[int] | None = None,
    _budget: list[int] | None = None,
) -> str:
    if val is None:
        return default
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except Exception:
            return default
        if val is None or isinstance(val, os.PathLike):
            return default
    if isinstance(val, str):
        txt = val
    elif isinstance(val, (bytes, bytearray)):
        txt = bytes(val).decode("utf-8", errors="replace")
    elif isinstance(val, int) and not isinstance(val, bool):
        txt = game_data.fmt_compact_int(val) if abs(val).bit_length() >= 12_000 else str(val)
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
                                txt = _safe_bot_str(
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
                                        _safe_bot_str(
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
    else:
        try:
            txt = game_data.safe_key(val)
        except Exception:
            return default
    return str(redact_sensitive(txt))


def _safe_interval(raw: object, default: float = 20.0) -> float:
    if raw is None or isinstance(raw, (bool, dict, list, tuple, set, frozenset)):
        return default
    if isinstance(raw, int):
        if raw.bit_length() > 128:
            return 86400.0 if raw > 0 else default
        iv = float(raw)
    else:
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8", errors="replace")
        if isinstance(raw, str):
            txt = raw.strip()
            if not txt or not txt.isascii():
                return default
            raw = txt
        try:
            iv = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            return default
    if not math.isfinite(iv) or iv <= 0.0:
        return default
    return max(0.01, min(86400.0, iv))


def _safe_retry_after(exc: BaseException, default: float = 45.0) -> float:
    raw = getattr(exc, "retry_after", default)
    if raw is None or isinstance(raw, (bool, dict, list, tuple, set, frozenset)):
        return default
    if isinstance(raw, int):
        if raw.bit_length() > 128:
            return 3600.0 if raw > 0 else default
        ra = float(raw)
    else:
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8", errors="replace")
        if isinstance(raw, str):
            txt = raw.strip()
            if not txt or not txt.isascii():
                return default
            raw = txt
        try:
            ra = float(raw)
        except (TypeError, ValueError, OverflowError):
            return default
    if not math.isfinite(ra) or ra <= 0.0:
        return default
    return min(3600.0, max(45.0, ra))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the SpaceInvasion Classic autoplayer against live Chrome on a debug port."
    )
    parser.add_argument(
        "--debug-port",
        type=int,
        default=DEFAULT_DEBUG_PORT,
        help=f"Chrome DevTools debug port kept open across ticks (default: {DEFAULT_DEBUG_PORT}).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Path to .env credentials for auto-launching/recovering Chrome on the debug port.",
    )
    parser.add_argument(
        "--account",
        default="default",
        help="Named account profile in .env (default: default).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually click and execute approved actions in Chrome (default is dry-run).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single planning/execution cycle and exit (default runs once unless --loop).",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep running periodically until --max-ticks or Ctrl+C.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=20.0,
        help="Base seconds between loop ticks (with jitter, default: 20s).",
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=0,
        help="Maximum ticks in --loop mode (0 = unlimited).",
    )
    parser.add_argument(
        "--max-actions",
        type=int,
        default=1,
        help="Max executable actions per tick (default: 1).",
    )
    parser.add_argument(
        "--max-per-hour",
        type=int,
        default=30,
        help="Hard cap on live actions per rolling hour (default: 30).",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs"),
        help="Directory for JSONL and `.log` files (default: logs).",
    )
    return parser


def run_tick(
    client: GameBrowserClient,
    executor: ActionExecutor,
    *,
    max_actions: int,
) -> tuple[object, Optional[PlannedAction], Optional[dict], Optional[PlannedAction], list[ExecutionResult]]:
    """Run one read -> plan -> execute cycle and return actual & next actions."""
    results: list[ExecutionResult] = []
    state = client.read_full_snapshot(include_buildings=True, return_home=False)
    actions = plan_actions(state)
    actual_action: Optional[PlannedAction] = None
    actual_result: Optional[dict] = None
    after_live = False

    p0_alerts = [a for a in actions if a.priority == "P0"]
    if p0_alerts:
        for alert in p0_alerts:
            print(f"[P0 ALERT] {redact_sensitive(_safe_bot_str(alert.reason))}")

    acted = 0
    while acted < max_actions:
        ready = next((a for a in actions if a.executable_now), None)
        if ready is None:
            break
        actual_action = ready
        res = executor.execute_action(client, state, ready)
        results.append(res)
        actual_result = {
            "executed": res.executed,
            "dry_run": res.dry_run,
            "message": res.message,
        }
        if res.dry_run:
            acted += 1
            break
        if not res.executed:
            break
        acted += 1
        after_live = True
        state = client.read_full_snapshot(include_buildings=True, return_home=False)
        actions = plan_actions(state)

    if actual_action is None and actions:
        actual_action = actions[0]
        res = executor.execute_action(client, state, actual_action)
        results.append(res)
        actual_result = {
            "executed": res.executed,
            "dry_run": res.dry_run,
            "message": res.message,
        }

    next_action = pick_next_action(actions, actual_action, after_live_execution=after_live)
    client.navigate_menu("internalHome")
    return state, actual_action, actual_result, next_action, results


def ensure_debug_browser_ready(
    *,
    debug_port: int,
    env_file: Path,
    account: str,
) -> None:
    """Ensure Chrome is running on `debug_port` and logged into the target universe.

    Always detaches leaving Chrome alive on `127.0.0.1:<debug_port>`.
    """
    port_open = (
        is_debug_port_open(debug_port)
        if DEFAULT_DEBUG_HOST == "127.0.0.1"
        else is_debug_port_open(debug_port, DEFAULT_DEBUG_HOST)
    )
    if not port_open:
        creds = load_credentials(env_path=env_file, account=account)
        cfg = BrowserConfig(
            profile_dir=DEFAULT_PROFILE_ROOT / creds.account_name,
            debug_port=debug_port,
            debug_host=DEFAULT_DEBUG_HOST,
            detach=True,
        )
        login_with_browser(creds=creds, config=cfg, require_in_game=True)
        return

    # Verify attached Chrome is still inside the game; recover automatically if kicked to portal
    try:
        attach_ctx = (
            GameBrowserClient.attach(port=debug_port)
            if DEFAULT_DEBUG_HOST == "127.0.0.1"
            else GameBrowserClient.attach(port=debug_port, host=DEFAULT_DEBUG_HOST)
        )
        with attach_ctx as client:
            client.ensure_in_game()
    except RateLimitError:
        raise
    except SpaceInvasionAuthError:
        creds = load_credentials(env_path=env_file, account=account)
        cfg = BrowserConfig(
            profile_dir=DEFAULT_PROFILE_ROOT / creds.account_name,
            debug_port=debug_port,
            debug_host=DEFAULT_DEBUG_HOST,
            attach=True,
            detach=True,
        )
        login_with_browser(creds=creds, config=cfg, require_in_game=True)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    executor = ActionExecutor(
        dry_run=not args.live,
        max_actions_per_hour=args.max_per_hour,
    )
    run_loop = args.loop and not args.once
    interval_s = _safe_interval(args.interval, 20.0)
    mode_label = "LIVE" if args.live else "DRY-RUN"

    try:
        tick = 0
        browser_ready = False
        while True:
            tick += 1
            try:
                if not browser_ready:
                    ensure_debug_browser_ready(
                        debug_port=args.debug_port,
                        env_file=args.env_file,
                        account=args.account,
                    )
                    browser_ready = True
                with GameBrowserClient.attach(port=args.debug_port) as client:
                    state, actual_action, actual_result, next_action, results = run_tick(
                        client, executor, max_actions=max(1, args.max_actions)
                    )
                planet = state.active_planet()
                res_str = planet.resources.format_short() if planet else "?"
                msg = redact_sensitive(
                    _safe_bot_str((actual_result or {}).get("message", "no action"), "no action")
                )
                coords_txt = (
                    redact_sensitive(_safe_bot_str(planet.coords, "?"))
                    if (planet and planet.coords is not None)
                    else "?"
                )
                tick_s = game_data.fmt_compact_int(tick)
                print(f"[tick {tick_s} | {mode_label}] {coords_txt} | {res_str}")
                print(f"  Actual: {format_action_summary(actual_action)} => {msg}")
                print(f"  Next:   {format_action_summary(next_action)}")
                if args.log_dir:
                    append_tick_logs(
                        args.log_dir,
                        source="bot",
                        tick=tick,
                        mode=mode_label,
                        state=state,
                        actual_action=actual_action,
                        actual_result=actual_result,
                        next_action=next_action,
                        outcomes=[
                            {"executed": r.executed, "dry_run": r.dry_run, "message": r.message}
                            for r in results
                        ],
                    )
            except RateLimitError as exc:
                backoff_s = _safe_retry_after(exc, 45.0)
                print(
                    f"[tick {game_data.fmt_compact_int(tick)}] Rate limit hit: {redact_sensitive(_safe_bot_str(exc))}; backing off {backoff_s:.1f}s."
                )
                if not run_loop:
                    return 1
                time.sleep(backoff_s)
            except SpaceInvasionAuthError as exc:
                if not run_loop:
                    raise
                browser_ready = False
                print(
                    f"[tick {game_data.fmt_compact_int(tick)}] Session interrupted ({redact_sensitive(_safe_bot_str(exc))}); re-ensuring browser on port {args.debug_port}."
                )
                try:
                    ensure_debug_browser_ready(
                        debug_port=args.debug_port,
                        env_file=args.env_file,
                        account=args.account,
                    )
                    browser_ready = True
                except RateLimitError as rl_exc:
                    backoff_s = _safe_retry_after(rl_exc, 45.0)
                    print(
                        f"[tick {game_data.fmt_compact_int(tick)}] Rate limit hit during recovery: {redact_sensitive(_safe_bot_str(rl_exc))}; backing off {backoff_s:.1f}s."
                    )
                    time.sleep(backoff_s)

            if not run_loop:
                break
            if args.max_ticks > 0 and tick >= args.max_ticks:
                break
            sleep_s = max(5.0, interval_s + random.uniform(-2.0, 4.0))
            time.sleep(sleep_s)
        return 0
    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 0
    except SpaceInvasionAuthError as exc:
        print(f"Error: {redact_sensitive(_safe_bot_str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
