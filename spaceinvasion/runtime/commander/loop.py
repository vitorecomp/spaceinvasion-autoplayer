"""Commander CLI parsing, tick cycle orchestration, and main process loop."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import logging
from pathlib import Path
import random
import signal
import sys
import threading
import time

import requests

from spaceinvasion.core.config import StrategyConfig, load_strategy_config, strategy_config_from_dict
from spaceinvasion.core.redaction import format_clean_error
from spaceinvasion.runtime.commander._loop_recovery import (
    evaluate_control_gates,
    fetch_bot_control,
    handle_api_tick_error,
    handle_unexpected_tick_error,
)
from spaceinvasion.runtime.commander._types import (
    LOOP_BOUNDARY_ERRORS,
    BotControlSnapshot,
    CommanderApiLike,
    CommanderOptions,
    LoopRuntimeState,
)
from spaceinvasion.runtime.commander.errors import (
    is_connection_refused_error,
    safe_cmd_int,
    safe_finite_float,
)
from spaceinvasion.runtime.commander.executor import run_commander_tick
from spaceinvasion.runtime.commander.summary import (
    should_refresh_buildings_after_tick,
    summarize_and_log_tick,
)
from spaceinvasion.runtime.commander.wakeup import parse_bot_control_snapshot
from spaceinvasion.runtime.commander_client import GameApiClient, GameApiError
from spaceinvasion.runtime.reloader import (
    current_python_cmd,
    discover_watch_paths,
    should_spawn_reloader,
    watch_and_run,
)

LOGGER = logging.getLogger("spaceinvasion.runtime.commander")


def discover_commander_watch_paths(repo_root: Path, entrypoint: Path) -> tuple[Path, ...]:
    """Return all watched files and package directories for commander hot-reload."""
    discovered = discover_watch_paths(
        repo_root,
        entrypoints=(entrypoint.resolve(),),
        packages=("core", "combat", "planner"),
        extra_files=(
            repo_root / "spaceinvasion/__init__.py",
            repo_root / "spaceinvasion/runtime/commander_client.py",
            repo_root / "spaceinvasion/runtime/logs.py",
            repo_root / "spaceinvasion/runtime/reloader.py",
        ),
    )
    watch_dirs = (
        repo_root / "spaceinvasion/core",
        repo_root / "spaceinvasion/combat",
        repo_root / "spaceinvasion/planner",
        repo_root / "spaceinvasion/runtime/commander",
    )
    return (*discovered, *watch_dirs)


def build_commander_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser for the SpaceInvasion Commander."""
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
        default=None,
        help="Optional strategy config JSON path (defaults to active PostgreSQL database).",
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


def parse_commander_options(argv: Sequence[str] | None = None) -> CommanderOptions:
    """Parse CLI arguments into a frozen ``CommanderOptions`` dataclass."""
    ns = build_commander_parser().parse_args(list(argv) if argv is not None else None)
    return CommanderOptions(
        api_url=str(ns.api_url),
        live=bool(ns.live),
        once=bool(ns.once),
        loop=bool(ns.loop),
        interval=float(ns.interval),
        max_ticks=int(ns.max_ticks),
        max_actions=int(ns.max_actions) if ns.max_actions is not None else None,
        config=ns.config,
        log_dir=ns.log_dir,
        reload=bool(ns.reload),
    )


def _poll_control_and_resolve_config(
    api: CommanderApiLike,
    opts: CommanderOptions,
    strategy_cfg: StrategyConfig,
    rt_state: LoopRuntimeState,
    *,
    run_loop: bool,
    effective_live: bool,
    eff_min_interval_s: float,
    logger: logging.Logger,
    sleep_fn: Callable[[float], None],
    monotonic_fn: Callable[[], float],
) -> tuple[BotControlSnapshot, StrategyConfig, bool, bool, float | None]:
    snapshot = BotControlSnapshot()
    try:
        bot_ctrl = fetch_bot_control(api, run_loop=run_loop, sleep_fn=sleep_fn, monotonic_fn=monotonic_fn)
        snapshot = parse_bot_control_snapshot(bot_ctrl, strategy_cfg, run_loop=run_loop, effective_live=effective_live)
        paused_via_ui, gate_sleep = evaluate_control_gates(
            snapshot, rt_state, run_loop=run_loop, eff_min_interval_s=eff_min_interval_s, logger=logger
        )
        if paused_via_ui:
            return snapshot, strategy_cfg, effective_live, True, gate_sleep
        if snapshot.mode_override in ("live", "dry-run"):
            effective_live = snapshot.mode_override == "live"
        if opts.config is None and snapshot.strategy_config_raw is not None:
            try:
                strategy_cfg = strategy_config_from_dict(dict(snapshot.strategy_config_raw))
            except (TypeError, ValueError, KeyError) as cfg_exc:
                logger.debug("Ignored invalid remote strategy_config: %s", format_clean_error(cfg_exc))
    except (requests.RequestException, GameApiError) as bc_outer_exc:
        if run_loop and is_connection_refused_error(bc_outer_exc):
            raise
    return snapshot, strategy_cfg, effective_live, False, None


def _run_tick_cycle(
    api: CommanderApiLike,
    opts: CommanderOptions,
    rt_state: LoopRuntimeState,
    *,
    run_loop: bool,
    has_cli_interval: bool,
    cli_interval_s: float,
    config_loader: Callable[[Path | None], StrategyConfig],
    logger: logging.Logger,
    jitter_fn: Callable[[float, float], float],
    sleep_fn: Callable[[float], None],
    monotonic_fn: Callable[[], float],
) -> tuple[int | None, float | None, float]:
    effective_live = opts.live
    eff_min_interval_s = cli_interval_s if has_cli_interval else 120.0
    try:
        strategy_cfg = config_loader(opts.config)
        cfg_batch_s = max(5.0, min(86400.0, float(safe_cmd_int(strategy_cfg.action_batch_interval_seconds, 120))))
        eff_min_interval_s = cli_interval_s if has_cli_interval else cfg_batch_s
        eff_max_idle_s = (
            eff_min_interval_s if (has_cli_interval and cli_interval_s < 5.0) else max(eff_min_interval_s, 900.0)
        )
        snapshot, strategy_cfg, effective_live, paused_via_ui, gate_sleep = _poll_control_and_resolve_config(
            api,
            opts,
            strategy_cfg,
            rt_state,
            run_loop=run_loop,
            effective_live=effective_live,
            eff_min_interval_s=eff_min_interval_s,
            logger=logger,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
        )
        if paused_via_ui:
            return None, gate_sleep, eff_min_interval_s

        now_mono = monotonic_fn()
        need_buildings = rt_state.last_full_buildings_at <= 0.0 or (now_mono - rt_state.last_full_buildings_at) >= 60.0
        eff_label = "LIVE" if effective_live else "DRY-RUN"
        effective_max_actions = max(1, opts.max_actions if opts.max_actions is not None else (10 if run_loop else 1))
        state, actual_action, actual_result, next_action, outcomes = run_commander_tick(
            api,
            live=effective_live,
            max_actions=effective_max_actions,
            config=strategy_cfg,
            include_buildings=need_buildings,
            scanned_systems=snapshot.scanned_systems,
            throttle_cooldowns=snapshot.throttle_cooldowns,
            economy_sync_timestamps=snapshot.economy_sync_timestamps,
            fleet_slot_cooldowns=snapshot.fleet_slot_cooldowns,
            manual_queue=snapshot.manual_queue,
            transmitter_status=snapshot.transmitter_status,
            logger=logger,
        )
        if should_refresh_buildings_after_tick(need_buildings, effective_live, outcomes):
            rt_state.last_full_buildings_at = monotonic_fn()
        loop_sleep = summarize_and_log_tick(
            tick=rt_state.tick,
            mode_label=eff_label,
            state=state,
            actual_action=actual_action,
            actual_result=actual_result,
            next_action=next_action,
            outcomes=outcomes,
            run_loop=run_loop,
            eff_min_interval_s=eff_min_interval_s,
            eff_max_idle_s=eff_max_idle_s,
            fleet_slot_cooldowns=snapshot.fleet_slot_cooldowns,
            log_dir=opts.log_dir,
            logger=logger,
            jitter_fn=jitter_fn,
        )
        return None, loop_sleep, eff_min_interval_s
    except (requests.RequestException, GameApiError) as exc:
        rc, sleep_s = handle_api_tick_error(
            exc,
            api,
            opts,
            rt_state,
            eff_min_interval_s=eff_min_interval_s,
            run_loop=run_loop,
            logger=logger,
            jitter_fn=jitter_fn,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
        )
        return rc, sleep_s, eff_min_interval_s
    except LOOP_BOUNDARY_ERRORS as exc:
        rc, sleep_s = handle_unexpected_tick_error(
            exc, rt_state, effective_live=effective_live, run_loop=run_loop, logger=logger
        )
        return rc, sleep_s, eff_min_interval_s


def _default_load_strategy_config(path: Path | None) -> StrategyConfig:
    if path is None:
        return StrategyConfig()
    return load_strategy_config(path)


def run_commander_loop(
    opts: CommanderOptions,
    *,
    api: CommanderApiLike | None = None,
    config_loader: Callable[[Path | None], StrategyConfig] = _default_load_strategy_config,
    logger: logging.Logger = LOGGER,
    jitter_fn: Callable[[float, float], float] = random.uniform,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> int:
    """Run the commander tick loop until completion, error exit, or signal shutdown."""
    is_main = threading.current_thread() is threading.main_thread()
    prev_sigterm = signal.getsignal(signal.SIGTERM) if is_main else None

    def _shutdown_handler(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    if is_main:
        signal.signal(signal.SIGTERM, _shutdown_handler)
    config_loader(opts.config)
    client: CommanderApiLike = api if api is not None else GameApiClient(base_url=opts.api_url, timeout=60.0)
    run_loop = opts.loop and not opts.once
    iv_f = safe_finite_float(opts.interval)
    has_cli_interval = iv_f is not None and iv_f > 0.0
    cli_interval_s = max(0.01, min(86400.0, iv_f)) if (has_cli_interval and iv_f is not None) else 0.0
    rt_state = LoopRuntimeState()

    try:
        while True:
            if opts.max_ticks > 0 and rt_state.tick >= opts.max_ticks:
                break
            rt_state.tick += 1
            rc, loop_sleep, eff_min = _run_tick_cycle(
                client,
                opts,
                rt_state,
                run_loop=run_loop,
                has_cli_interval=has_cli_interval,
                cli_interval_s=cli_interval_s,
                config_loader=config_loader,
                logger=logger,
                jitter_fn=jitter_fn,
                sleep_fn=sleep_fn,
                monotonic_fn=monotonic_fn,
            )
            if rc is not None:
                return rc
            if not run_loop or (opts.max_ticks > 0 and rt_state.tick >= opts.max_ticks):
                break
            if loop_sleep is None:
                jitter_f = jitter_fn(0.5, 2.5) if eff_min >= 5.0 else 0.0
                loop_sleep = max(0.01, eff_min + jitter_f)
            sleep_fn(loop_sleep)
        return 0
    except KeyboardInterrupt:
        logger.info("[CMD] Stopped.")
        return 0
    finally:
        client.close()
        if is_main and prev_sigterm is not None:
            signal.signal(signal.SIGTERM, prev_sigterm)


def run_commander_main(
    argv: Sequence[str] | None = None,
    *,
    entrypoint: Path | None = None,
    repo_root: Path | None = None,
    api: CommanderApiLike | None = None,
    config_loader: Callable[[Path | None], StrategyConfig] = _default_load_strategy_config,
) -> int:
    """Top-level CLI entrypoint supporting ``--reload`` and delegating to ``run_commander_loop``."""
    opts = parse_commander_options(argv)
    if should_spawn_reloader(opts.reload):
        resolved_entry = (entrypoint or Path(__file__)).resolve()
        resolved_root = (repo_root or Path(__file__).resolve().parents[3]).resolve()
        raw_argv = list(sys.argv[1:]) if argv is None else list(argv)
        return int(
            watch_and_run(
                label="CMD",
                cmd=current_python_cmd(resolved_entry, raw_argv),
                watch_paths=discover_commander_watch_paths(resolved_root, resolved_entry),
            )
        )
    return run_commander_loop(opts, api=api, config_loader=config_loader)
