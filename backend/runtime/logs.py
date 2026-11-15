"""Structured JSONL and human-readable runtime logging helpers (Phase 3)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import traceback
from typing import TextIO, TypeVar

from backend.core.coercion import MAX_JSON_SAFE_INT, fmt_compact_int, sanitize_json_tree
from backend.core.model import GameState, PlannedAction
from backend.core.redaction import (
    format_clean_error,
    is_secret_dict_key,
    redact_sensitive,
    redact_text,
    strip_webdriver_stacktrace,
)
from backend.runtime._logs_helpers import (
    SAFE_LOG_ERRORS,
    GameStateLogLike,
    PlannedActionLike,
    clip_log_str,
    coerce_json_scalars,
    resources_to_dict,
    safe_exc_text,
    safe_summary_text,
    to_redacted_dict,
)

_TAction = TypeVar("_TAction", bound=PlannedActionLike)

_REPO_ROOT_DIR: Path = Path(__file__).resolve().parents[2]
_REPO_LOGS_DIR: Path = (_REPO_ROOT_DIR / "logs").resolve()
DEFAULT_CRASH_LOG_DIR: Path = _REPO_LOGS_DIR

_NOISY_POLL_COMMANDS: frozenset[str] = frozenset(
    {
        "planner_jobs_status",
        "notifications_list",
        "db_summary",
        "config_optimizer_get",
        "ai_planner_get",
        "fleet_planner_get",
        "buildings_eta",
        "combat_overview",
        "espionage_intel",
        "colony_candidates",
        "attack_results",
        "recycler_results",
        "universe_research_max",
    }
)


def _sanitize_log_record(raw_record: Mapping[str, object]) -> dict[str, object]:
    red1 = redact_sensitive(dict(raw_record))
    sanitized = sanitize_json_tree(red1, max_int=MAX_JSON_SAFE_INT, nonfinite_float=None)
    coerced = coerce_json_scalars(sanitized)
    red2 = redact_sensitive(coerced)
    return {str(k): v for k, v in red2.items()} if isinstance(red2, Mapping) else {}


def _should_skip_disk_log(target_dir: Path | None, *, allow_default_disk_logs: bool = False) -> bool:
    if target_dir is None:
        return True
    if allow_default_disk_logs:
        return False
    try:
        resolved = Path(target_dir).resolve()
    except OSError:
        return True
    return resolved in (_REPO_LOGS_DIR, DEFAULT_CRASH_LOG_DIR)


def format_action_summary(action: PlannedActionLike | Mapping[str, object] | None) -> str:
    """Format an action concisely for console and ``.log`` lines."""
    data = to_redacted_dict(action)
    if not data:
        return "none"
    prio = redact_sensitive(safe_summary_text(data.get("priority"), "?"))
    ready = "READY" if data.get("executable_now") else "WAIT"
    kind = redact_sensitive(safe_summary_text(data.get("kind"), "ACTION"))
    name = redact_sensitive(safe_summary_text(data.get("target_name"), "?"))
    lvl = data.get("target_level")
    coords = redact_sensitive(safe_summary_text(data.get("planet_coords"), ""))
    if isinstance(lvl, int) and not isinstance(lvl, bool) and lvl:
        lvl_part = f" -> L{fmt_compact_int(lvl)}"
    else:
        lvl_part = f" -> L{redact_sensitive(safe_summary_text(lvl, ''))}" if lvl else ""
    coord_part = f" @ [{coords}]" if coords else ""
    missing = data.get("missing")
    miss_parts = (
        [
            f"{redact_sensitive(safe_summary_text(k, '?'))}={fmt_compact_int(v, grouped=True)}"
            for k, v in missing.items()
            if isinstance(v, int) and not isinstance(v, bool) and v > 0
        ]
        if isinstance(missing, Mapping)
        else []
    )
    miss_part = f" (missing: {', '.join(miss_parts)})" if miss_parts else ""
    return f"[{prio}][{ready}] {kind} {name}{lvl_part}{coord_part}{miss_part}"


def pick_next_action(
    actions: Sequence[_TAction],
    actual_action: _TAction | None,
    *,
    after_live_execution: bool = False,
) -> _TAction | None:
    """Select the logical next action after ``actual_action``.

    When ``actions`` comes from a post-execution refresh (``after_live_execution=True``),
    the top planned action is already the next step. Otherwise, return the second
    distinct planned action (or the top action if waiting).
    """
    if not actions:
        return None
    if after_live_execution or actual_action is None or not actual_action.executable_now:
        return actions[0]
    for candidate in actions:
        if (
            candidate.kind != actual_action.kind
            or candidate.planet_id != actual_action.planet_id
            or candidate.target_id != actual_action.target_id
            or candidate.target_name != actual_action.target_name
            or candidate.target_level != actual_action.target_level
        ):
            return candidate
    return actions[1] if len(actions) > 1 else None


def append_tick_logs(
    log_dir: Path | None,
    *,
    source: str,
    tick: int,
    mode: str,
    state: GameStateLogLike,
    actual_action: PlannedActionLike | Mapping[str, object] | None,
    actual_result: Mapping[str, object] | None,
    next_action: PlannedActionLike | Mapping[str, object] | None,
    outcomes: Sequence[Mapping[str, object]] = (),
    allow_default_disk_logs: bool = False,
) -> tuple[Path, Path]:
    """Write structured JSONL and readable ``.log`` entries when ``log_dir`` is enabled."""
    if _should_skip_disk_log(log_dir, allow_default_disk_logs=allow_default_disk_logs) or log_dir is None:
        return Path("/dev/null"), Path("/dev/null")
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / f"{source}.jsonl"
    text_path = log_dir / f"{source}.log"

    ts = datetime.now(timezone.utc).isoformat()
    planet = state.active_planet()
    actual_dict = to_redacted_dict(actual_action)
    next_dict = to_redacted_dict(next_action)

    record = _sanitize_log_record(
        {
            "ts": ts,
            "tick": tick,
            "mode": mode,
            "universe_id": state.universe_id,
            "planet": planet.coords if planet is not None else None,
            "resources": resources_to_dict(planet.resources) if planet is not None else None,
            "energy": (
                {"produced": planet.energy.produced, "used": planet.energy.used} if planet is not None else None
            ),
            "actual_action": actual_dict,
            "actual_result": dict(actual_result) if isinstance(actual_result, Mapping) else actual_result,
            "next_action": next_dict,
            "outcomes": list(outcomes),
        }
    )
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, allow_nan=False) + "\n")

    sanitized_result = record.get("actual_result")
    raw_msg = sanitized_result.get("message", "no action") if isinstance(sanitized_result, Mapping) else "no action"
    result_msg = safe_summary_text(raw_msg, "no action") if raw_msg is not None else "no action"
    planet_label = safe_summary_text(record.get("planet"), "?") or "?"
    mode_label = safe_summary_text(record.get("mode"), "") or safe_summary_text(mode, "")
    if isinstance(tick, int) and not isinstance(tick, bool):
        tick_label = fmt_compact_int(tick)
    else:
        safe_tick = record.get("tick")
        if isinstance(safe_tick, int) and not isinstance(safe_tick, bool):
            tick_label = fmt_compact_int(safe_tick)
        else:
            tick_label = safe_summary_text(safe_tick if safe_tick is not None else tick, "")
    res_str = planet.resources.format_short() if planet is not None else "?"
    line = str(
        redact_sensitive(
            f"[{ts}] [tick {tick_label} | {mode_label}] {planet_label} | {res_str}\n"
            f"  ACTUAL: {format_action_summary(actual_dict)} => {result_msg}\n"
            f"  NEXT:   {format_action_summary(next_dict)}\n"
        )
    )
    with text_path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    return jsonl_path, text_path


def append_api_log(
    log_dir: Path | None,
    *,
    method: str,
    path: str,
    status: int,
    payload: Mapping[str, object] | None = None,
    response: Mapping[str, object] | None = None,
    allow_default_disk_logs: bool = False,
    stream: TextIO | None = None,
) -> tuple[Path, Path]:
    """Emit an HTTP API request/command audit event to container stdout or explicit ``log_dir``."""
    skip_disk = _should_skip_disk_log(log_dir, allow_default_disk_logs=allow_default_disk_logs)
    jsonl_path = (log_dir / "game_api.jsonl") if (not skip_disk and log_dir is not None) else Path("/dev/null")
    text_path = (log_dir / "game_api.log") if (not skip_disk and log_dir is not None) else Path("/dev/null")

    ts = datetime.now(timezone.utc).isoformat()
    payload_map: Mapping[str, object] = payload if isinstance(payload, Mapping) else {}
    resp_map: Mapping[str, object] = response if isinstance(response, Mapping) else {}
    cmd = payload_map.get("command") or resp_map.get("command")

    record = _sanitize_log_record(
        {
            "ts": ts,
            "method": method,
            "path": path,
            "status": status,
            "command": cmd,
            "payload": dict(payload) if isinstance(payload, Mapping) else payload,
            "actual_action": resp_map.get("action"),
            "next_action": resp_map.get("next_action"),
            "executed": resp_map.get("executed"),
            "dry_run": resp_map.get("dry_run"),
            "message": resp_map.get("message") or resp_map.get("error"),
        }
    )

    method_label = safe_summary_text(record.get("method"), "")
    redacted_path = safe_summary_text(record.get("path"), "")
    msg = safe_summary_text(record.get("message"), "")
    cmd_label = safe_summary_text(record.get("command"), "")
    raw_actual = record.get("actual_action")
    actual_str = (
        format_action_summary(raw_actual if isinstance(raw_actual, Mapping) else None)
        if raw_actual
        else (cmd_label or redacted_path)
    )
    raw_next = record.get("next_action")
    next_str = format_action_summary(raw_next if isinstance(raw_next, Mapping) else None) if raw_next else "n/a"
    status_label = (
        fmt_compact_int(status)
        if isinstance(status, int) and not isinstance(status, bool)
        else safe_summary_text(record.get("status"), "")
    )
    line = str(
        redact_sensitive(
            f"[{ts}] {method_label} {redacted_path} -> {status_label} | actual={actual_str}"
            f" | next={next_str}" + (f" | {msg}" if msg else "") + "\n"
        )
    )
    if skip_disk or log_dir is None:
        if int(status) >= 400 or cmd_label not in _NOISY_POLL_COMMANDS:
            out_stream = stream if stream is not None else sys.stdout
            out_stream.write(f"[API] {line}")
            out_stream.flush()
        return jsonl_path, text_path

    log_dir.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, allow_nan=False) + "\n")
    with text_path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    return jsonl_path, text_path


def _write_crash_text(
    handle: TextIO,
    header: str,
    det_json: str,
    tb_redacted: str,
) -> None:
    handle.write(header)
    if det_json and det_json != "{}":
        handle.write(f"details: {det_json}\n")
    if tb_redacted:
        handle.write(f"traceback:\n{tb_redacted}\n")


def append_crash_debug_log(
    *,
    component: str,
    event: str,
    error: BaseException | None = None,
    details: Mapping[str, object] | None = None,
    crash_dir: Path | None = None,
    allow_default_disk_logs: bool = False,
    stream: TextIO | None = None,
) -> tuple[Path, Path] | None:
    """Emit a ``sid=``-redacted diagnostic/crash record to stderr or explicit ``crash_dir``."""
    try:
        target_dir = crash_dir if crash_dir is not None else DEFAULT_CRASH_LOG_DIR
        skip_disk = _should_skip_disk_log(target_dir, allow_default_disk_logs=allow_default_disk_logs)

        ts = datetime.now(timezone.utc).isoformat()
        exc_type = type(error).__name__ if error is not None else None
        exc_msg = safe_exc_text(error) if error is not None else None
        tb_str = (
            "".join(traceback.format_exception(type(error), error, error.__traceback__))
            if error is not None and error.__traceback__ is not None
            else None
        )
        record = _sanitize_log_record(
            {
                "ts": ts,
                "component": component,
                "event": event,
                "exc_type": exc_type,
                "exc_message": exc_msg,
                "traceback": clip_log_str(tb_str, 16_384) if tb_str else None,
                "details": dict(details) if isinstance(details, Mapping) else {},
            }
        )
        comp_s = safe_summary_text(record.get("component"), "")
        evt_s = safe_summary_text(record.get("event"), "")
        rec_exc_type = safe_summary_text(record.get("exc_type"), "")
        rec_exc_msg = safe_summary_text(record.get("exc_message"), "")
        header = str(
            redact_sensitive(
                f"=== [{ts}] {comp_s} :: {evt_s}"
                + (f" ({rec_exc_type}: {rec_exc_msg})" if rec_exc_type else "")
                + " ===\n"
            )
        )
        det_json = str(redact_sensitive(json.dumps(record.get("details") or {}, indent=2, allow_nan=False)))
        tb_redacted = str(redact_sensitive(str(record.get("traceback") or "")))
        if skip_disk:
            err_stream = stream if stream is not None else sys.stderr
            _write_crash_text(err_stream, header, det_json, tb_redacted)
            err_stream.flush()
            return None

        target_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = target_dir / "crash_debug.jsonl"
        text_path = target_dir / "crash_debug.log"
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, allow_nan=False) + "\n")
        with text_path.open("a", encoding="utf-8") as handle:
            _write_crash_text(handle, header, det_json, tb_redacted)
        return jsonl_path, text_path
    except SAFE_LOG_ERRORS:
        return None


__all__ = [
    "DEFAULT_CRASH_LOG_DIR",
    "GameState",
    "PlannedAction",
    "append_api_log",
    "append_crash_debug_log",
    "append_tick_logs",
    "format_action_summary",
    "format_clean_error",
    "is_secret_dict_key",
    "pick_next_action",
    "redact_sensitive",
    "redact_text",
    "strip_webdriver_stacktrace",
]
