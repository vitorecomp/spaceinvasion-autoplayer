#!/usr/bin/env python3
"""Thin CLI entrypoint for the HTTP-only SpaceInvasion Commander (zero Selenium imports)."""

from __future__ import annotations

from collections.abc import Sequence
import logging
from pathlib import Path
import sys
import time
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from spaceinvasion.core.model import PlannedAction  # noqa: E402, F401
from spaceinvasion.planner.heuristics import plan_actions as _DEFAULT_PLAN_ACTIONS  # noqa: E402
from spaceinvasion.runtime import commander as _cmd  # noqa: E402
from spaceinvasion.runtime.commander import _executor_plan  # noqa: E402
from spaceinvasion.runtime.commander_client import GameApiClient  # noqa: E402
from spaceinvasion.runtime.reloader import should_spawn_reloader  # noqa: E402

WATCH_PATHS: tuple[Path, ...] = _cmd.discover_commander_watch_paths(_REPO_ROOT, Path(__file__))
plan_actions = _DEFAULT_PLAN_ACTIONS
_action_to_command_payload, _hold_busy_fleet_slots = _cmd.action_to_command_payload, _cmd.hold_busy_fleet_slots
_parse_queue_timer_seconds, _planned_action_from_dict = _cmd.parse_queue_timer_seconds, _cmd.planned_action_from_dict
_safe_cmd_str, compute_next_wakeup_seconds = _cmd.safe_cmd_str, _cmd.compute_next_wakeup_seconds
compute_rate_limit_backoff, is_captcha_error = _cmd.compute_rate_limit_backoff, _cmd.is_captcha_error
is_galaxy_scan_busy_error = _cmd.is_galaxy_scan_busy_error


def run_commander_tick(*args: Any, **kwargs: Any) -> Any:
    """Delegate to ``spaceinvasion.runtime.commander.run_commander_tick``, honoring patched ``plan_actions``."""
    if plan_actions is _DEFAULT_PLAN_ACTIONS:
        return _cmd.run_commander_tick(*args, **kwargs)
    plan_mod: Any = _executor_plan
    orig, plan_mod.plan_actions = plan_mod.plan_actions, plan_actions
    try:
        return _cmd.run_commander_tick(*args, **kwargs)
    finally:
        plan_mod.plan_actions = orig


def main(argv: Sequence[str] | None = None) -> int:
    """Configure stdout logging and run the SpaceInvasion Commander CLI."""
    logging.basicConfig(format="%(message)s", level=logging.INFO, stream=sys.stdout, force=True)
    opts = _cmd.parse_commander_options(argv)
    if should_spawn_reloader(opts.reload):
        return _cmd.run_commander_main(argv, entrypoint=Path(__file__), repo_root=_REPO_ROOT)
    client = GameApiClient(base_url=opts.api_url, timeout=60.0)
    return _cmd.run_commander_loop(opts, api=client, sleep_fn=time.sleep, monotonic_fn=time.monotonic)


if __name__ == "__main__":
    raise SystemExit(main())
