#!/usr/bin/env python3
"""Hot-reloadable Commander client communicating exclusively over HTTP with `game_api.py`."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import os
from pathlib import Path
import random
import signal
import sys
import time
from typing import Optional

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

from spaceinvasion.commander_client import GameApiClient, GameApiError  # noqa: E402
from spaceinvasion.config import StrategyConfig, load_strategy_config  # noqa: E402
from spaceinvasion.logging_utils import (  # noqa: E402
    append_tick_logs,
    format_action_summary,
    pick_next_action,
)
from spaceinvasion.reloader import (  # noqa: E402
    current_python_cmd,
    should_spawn_reloader,
    watch_and_run,
)
from spaceinvasion.state import GameState  # noqa: E402
from spaceinvasion.strategy import PlannedAction, plan_actions  # noqa: E402

WATCH_PATHS = (
    Path("commander.py"),
    Path("config.yaml"),
    Path("spaceinvasion/config.py"),
    Path("spaceinvasion/fleet.py"),
    Path("spaceinvasion/game_data.py"),
    Path("spaceinvasion/strategy.py"),
    Path("spaceinvasion/commander_client.py"),
    Path("spaceinvasion/state.py"),
    Path("spaceinvasion/logging_utils.py"),
)


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


def _action_to_command_payload(action: PlannedAction, *, dry_run: bool) -> dict:
    if action.kind == "CLAIM_TUTORIAL":
        return {"command": "claim_tutorial", "dry_run": dry_run}
    if action.kind == "BUILD" and action.target_id is not None:
        return {
            "command": "build",
            "building_id": action.target_id,
            "planet_id": action.planet_id,
            "dry_run": dry_run,
        }
    if action.kind == "RESEARCH" and action.target_id is not None:
        return {
            "command": "research",
            "research_id": action.target_id,
            "planet_id": action.planet_id,
            "dry_run": dry_run,
        }
    if action.kind == "BUILD_SHIP" and action.target_id is not None:
        return {
            "command": "build_ship",
            "ship_id": action.target_id,
            "quantity": max(1, int(action.target_level or 1)),
            "planet_id": action.planet_id,
            "dry_run": dry_run,
        }
    if action.kind == "ESCAPE_FLIGHT":
        return {"command": "fleetsave_plan", "planet_id": action.planet_id}
    return {"command": "plan", "dry_run": True}


def run_commander_tick(
    api: GameApiClient,
    *,
    live: bool,
    max_actions: int,
    config: Optional[StrategyConfig] = None,
) -> tuple[GameState, Optional[PlannedAction], Optional[dict], Optional[PlannedAction], list[dict]]:
    """Fetch state via HTTP, compute `plan_actions`, and send commands.

    Returns `(state, actual_action, actual_result, next_action, outcomes)`.
    """
    cfg = config or StrategyConfig()
    state = api.get_state(include_buildings=True)
    actions = plan_actions(state, config=cfg)
    outcomes: list[dict] = []
    actual_action: Optional[PlannedAction] = None
    actual_result: Optional[dict] = None
    after_live = False

    for alert in [a for a in actions if a.priority == "P0"]:
        print(f"[CMD][P0 ALERT] {alert.reason}")

    acted = 0
    while acted < max_actions:
        ready = next((a for a in actions if a.executable_now), None)
        if ready is None:
            break
        actual_action = ready
        payload = _action_to_command_payload(ready, dry_run=not live)
        res = api.send_command(payload)
        actual_result = res
        outcomes.append(res)
        acted += 1
        if not live or not res.get("executed"):
            break
        after_live = True
        state = api.get_state(include_buildings=True)
        actions = plan_actions(state, config=cfg)

    if actual_action is None and actions:
        actual_action = actions[0]
        actual_result = {
            "ok": True,
            "executed": False,
            "dry_run": not live,
            "message": f"Waiting: {actual_action.target_name} ({actual_action.reason})",
            "action": asdict(actual_action),
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

    def _shutdown_handler(_signum, _frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _shutdown_handler)
    strategy_cfg = load_strategy_config(args.config)
    api = GameApiClient(base_url=args.api_url)
    run_loop = args.loop and not args.once
    mode_label = "LIVE" if args.live else "DRY-RUN"

    try:
        tick = 0
        while True:
            tick += 1
            try:
                state, actual_action, actual_result, next_action, outcomes = run_commander_tick(
                    api,
                    live=args.live,
                    max_actions=max(1, args.max_actions),
                    config=strategy_cfg,
                )
                planet = state.active_planet()
                res_str = planet.resources.format_short() if planet else "?"
                msg = (actual_result or {}).get("message", "no action")
                print(f"[CMD tick {tick} | {mode_label}] {planet.coords if planet else '?'} | {res_str}")
                print(f"  Actual: {format_action_summary(actual_action)} => {msg}")
                print(f"  Next:   {format_action_summary(next_action)}")
                if args.log_dir:
                    append_tick_logs(
                        args.log_dir,
                        source="commander",
                        tick=tick,
                        mode=mode_label,
                        state=state,
                        actual_action=actual_action,
                        actual_result=actual_result,
                        next_action=next_action,
                        outcomes=outcomes,
                    )
            except (requests.RequestException, GameApiError) as exc:
                print(f"[CMD tick {tick}] API unavailable ({exc}); retrying shortly.")
                if not run_loop:
                    return 1
                time.sleep(3.0)
                continue

            if not run_loop:
                break
            if args.max_ticks > 0 and tick >= args.max_ticks:
                break
            time.sleep(max(5.0, args.interval + random.uniform(-2.0, 4.0)))
        return 0
    except KeyboardInterrupt:
        print("\n[CMD] Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
