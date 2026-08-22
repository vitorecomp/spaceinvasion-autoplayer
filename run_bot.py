#!/usr/bin/env python3
"""Autoplayer runner loop (M3): attaches to Chrome and runs advisor + safe executor."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import random
import sys
import time
from typing import Optional


def _maybe_reexec_venv() -> None:
    if os.environ.get("SPACEINVASION_NO_REEXEC") == "1":
        return
    if sys.prefix != sys.base_prefix:
        return
    venv_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python3"
    if venv_python.exists() and os.path.abspath(sys.executable) != os.path.abspath(venv_python):
        os.execv(str(venv_python), [str(venv_python), *sys.argv])


_maybe_reexec_venv()

from spaceinvasion.auth import SpaceInvasionAuthError, load_credentials  # noqa: E402
from spaceinvasion.browser import (  # noqa: E402
    DEFAULT_DEBUG_PORT,
    BrowserConfig,
    is_debug_port_open,
    login_with_browser,
)
from spaceinvasion.client import GameBrowserClient, RateLimitError  # noqa: E402
from spaceinvasion.executor import ActionExecutor, ExecutionResult  # noqa: E402
from spaceinvasion.logging_utils import (  # noqa: E402
    append_tick_logs,
    format_action_summary,
    pick_next_action,
)
from spaceinvasion.strategy import PlannedAction, plan_actions  # noqa: E402


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
            print(f"[P0 ALERT] {alert.reason}")

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
    if not is_debug_port_open(debug_port):
        creds = load_credentials(env_path=env_file, account=account)
        cfg = BrowserConfig(
            profile_dir=Path(".browser_profile") / creds.account_name,
            debug_port=debug_port,
            detach=True,
        )
        login_with_browser(creds=creds, config=cfg, require_in_game=True)
        return

    # Verify attached Chrome is still inside the game; recover automatically if kicked to portal
    try:
        with GameBrowserClient.attach(port=debug_port) as client:
            client.ensure_in_game()
    except SpaceInvasionAuthError:
        creds = load_credentials(env_path=env_file, account=account)
        cfg = BrowserConfig(
            profile_dir=Path(".browser_profile") / creds.account_name,
            debug_port=debug_port,
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
    mode_label = "LIVE" if args.live else "DRY-RUN"

    try:
        ensure_debug_browser_ready(
            debug_port=args.debug_port,
            env_file=args.env_file,
            account=args.account,
        )
        tick = 0
        while True:
            tick += 1
            try:
                with GameBrowserClient.attach(port=args.debug_port) as client:
                    state, actual_action, actual_result, next_action, results = run_tick(
                        client, executor, max_actions=max(1, args.max_actions)
                    )
                planet = state.active_planet()
                res_str = planet.resources.format_short() if planet else "?"
                msg = (actual_result or {}).get("message", "no action")
                print(f"[tick {tick} | {mode_label}] {planet.coords if planet else '?'} | {res_str}")
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
                print(f"[tick {tick}] Rate limit hit: {exc}; backing off 45s.")
                if not run_loop:
                    return 1
                time.sleep(45.0)
            except SpaceInvasionAuthError as exc:
                if not run_loop:
                    raise
                print(f"[tick {tick}] Session interrupted ({exc}); re-ensuring browser on port {args.debug_port}.")
                ensure_debug_browser_ready(
                    debug_port=args.debug_port,
                    env_file=args.env_file,
                    account=args.account,
                )

            if not run_loop:
                break
            if args.max_ticks > 0 and tick >= args.max_ticks:
                break
            sleep_s = max(5.0, args.interval + random.uniform(-2.0, 4.0))
            time.sleep(sleep_s)
        return 0
    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 0
    except SpaceInvasionAuthError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
