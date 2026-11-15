"""Modular HTTP-only SpaceInvasion Commander package (Phase 3 decomposition)."""

from __future__ import annotations

from backend.runtime.commander._types import (
    CAPTCHA_STOP_MARKERS,
    CONNECTION_REFUSED_MARKERS,
    FLEET_ACTION_KINDS,
    LOOP_BOUNDARY_ERRORS,
    PARALLEL_BUILD_KINDS,
    RATE_LIMIT_MARKERS,
    SESSION_RECOVERY_MARKERS,
    BotControlSnapshot,
    CommanderApiLike,
    CommanderConfigLike,
    CommanderOptions,
    LoopRuntimeState,
    TickResult,
)
from backend.runtime.commander.errors import (
    compute_rate_limit_backoff,
    is_captcha_error,
    is_connection_refused_error,
    is_galaxy_scan_busy_error,
    safe_cmd_int,
    safe_cmd_opt_int,
    safe_cmd_str,
    safe_finite_float,
    wait_for_api_reconnect,
)
from backend.runtime.commander.executor import (
    action_dedup_key,
    pick_next_ready_action,
    run_commander_tick,
)
from backend.runtime.commander.loop import (
    build_commander_parser,
    discover_commander_watch_paths,
    parse_commander_options,
    run_commander_loop,
    run_commander_main,
)
from backend.runtime.commander.payloads import (
    action_to_command_payload,
    hold_busy_fleet_slots,
    parse_station_ships_spec,
    planned_action_from_dict,
)
from backend.runtime.commander.summary import (
    format_metal_production_tag,
    format_tick_summary_lines,
    should_refresh_buildings_after_tick,
    summarize_and_log_tick,
)
from backend.runtime.commander.wakeup import (
    compute_next_wakeup_seconds,
    parse_bot_control_snapshot,
    parse_queue_timer_seconds,
)

__all__ = [
    "BotControlSnapshot",
    "CAPTCHA_STOP_MARKERS",
    "CONNECTION_REFUSED_MARKERS",
    "CommanderApiLike",
    "CommanderConfigLike",
    "CommanderOptions",
    "FLEET_ACTION_KINDS",
    "LOOP_BOUNDARY_ERRORS",
    "LoopRuntimeState",
    "PARALLEL_BUILD_KINDS",
    "RATE_LIMIT_MARKERS",
    "SESSION_RECOVERY_MARKERS",
    "TickResult",
    "action_dedup_key",
    "action_to_command_payload",
    "build_commander_parser",
    "compute_next_wakeup_seconds",
    "compute_rate_limit_backoff",
    "discover_commander_watch_paths",
    "format_metal_production_tag",
    "format_tick_summary_lines",
    "hold_busy_fleet_slots",
    "is_captcha_error",
    "is_connection_refused_error",
    "is_galaxy_scan_busy_error",
    "parse_bot_control_snapshot",
    "parse_commander_options",
    "parse_queue_timer_seconds",
    "parse_station_ships_spec",
    "pick_next_ready_action",
    "planned_action_from_dict",
    "run_commander_loop",
    "run_commander_main",
    "run_commander_tick",
    "safe_cmd_int",
    "safe_cmd_opt_int",
    "safe_cmd_str",
    "safe_finite_float",
    "should_refresh_buildings_after_tick",
    "summarize_and_log_tick",
    "wait_for_api_reconnect",
]
