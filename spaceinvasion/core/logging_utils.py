"""Structured JSONL and human-readable logging helpers for `logs/`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Optional, Sequence, cast

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.model import GameState, PlannedAction
from spaceinvasion.core.redaction import (
    _clip as _clip_log_str,
)
from spaceinvasion.core.redaction import (
    _redact_key as _redaction_redact_key,
)
from spaceinvasion.core.redaction import (
    _safe_text as _redaction_safe_text,
)
from spaceinvasion.core.redaction import (
    format_clean_error as format_clean_error,
)
from spaceinvasion.core.redaction import (
    is_secret_dict_key as is_secret_dict_key,
)
from spaceinvasion.core.redaction import (
    redact_sensitive as redact_sensitive,
)
from spaceinvasion.core.redaction import (
    strip_webdriver_stacktrace as strip_webdriver_stacktrace,
)


def _decode_bytes(raw: bytes | bytearray) -> str:
    return bytes(raw).decode("utf-8", errors="replace")


_MAX_LOG_KEY_NODES = 256
_MAX_LOG_KEY_CHARS = 4096
_MAX_LOG_TREE_NODES = 32_768
_MAX_LOG_TREE_DEPTH = 64


def _safe_fspath_text(obj: os.PathLike[Any]) -> str:
    return _redaction_safe_text(obj, f"<{type(obj).__name__}>", 0, set(), [_MAX_LOG_KEY_NODES])


def _safe_exc_text(
    exc: BaseException,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> str:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_LOG_KEY_NODES]
    return _clip_log_str(
        _redaction_safe_text(exc, f"<{type(exc).__name__}>", _depth, seen, budget),
        16_384,
    )


def _redact_mapping_key(
    key: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_LOG_KEY_NODES]
    return _redaction_redact_key(key, False, _depth, seen, budget)


def _safe_summary_text(val: Any, default: str = "") -> str:
    if val is None:
        return default
    if isinstance(val, os.PathLike):
        return _safe_fspath_text(val)
    if isinstance(val, (bytes, bytearray)):
        return _decode_bytes(val)
    if isinstance(val, str):
        return val
    if isinstance(val, BaseException):
        return _safe_exc_text(val)
    if isinstance(val, int) and not isinstance(val, bool):
        return game_data.fmt_compact_int(val)
    if isinstance(val, (tuple, list, set, frozenset, Mapping)):
        return game_data.safe_key(val)
    try:
        return str(val)
    except Exception:
        return f"<{type(val).__name__}>"


def _coerce_json_scalars(
    obj: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_LOG_TREE_NODES]

    def _walk(val: Any, d: int) -> Any:
        tv = type(val)
        if val is None or tv is bool or tv is int or tv is float or tv is str:
            return val
        is_dict = tv is dict
        is_seq = tv is list or tv is tuple
        if not (is_dict or is_seq) and isinstance(val, (bool, int, float, str)):
            return val
        if is_dict or is_seq or isinstance(val, (Mapping, list, tuple, set, frozenset)):
            vid = id(val)
            if vid in seen:
                return "[Cycle]"
            if d >= _MAX_LOG_TREE_DEPTH or budget[0] <= 0:
                return "[Truncated]"
            budget[0] -= 1
            seen.add(vid)
            try:
                if is_dict or isinstance(val, Mapping):
                    out: dict[Any, Any] = {}
                    for k, v in val.items():
                        if budget[0] <= 0:
                            out["[Truncated]"] = "[Truncated]"
                            break
                        out[k] = _walk(v, d + 1)
                    return out
                out_list: list[Any] = []
                for v in val:
                    if budget[0] <= 0:
                        out_list.append("[Truncated]")
                        break
                    out_list.append(_walk(v, d + 1))
                return out_list
            finally:
                seen.discard(vid)
        return _safe_summary_text(val)

    return _walk(obj, _depth)


def _to_dict(item: Any) -> Optional[dict[str, Any]]:
    if item is None:
        return None
    if isinstance(item, Mapping):
        return cast(dict[str, Any], redact_sensitive(dict(item)))
    if is_dataclass(item) and not isinstance(item, type):
        return cast(dict[str, Any], redact_sensitive(asdict(item)))
    if isinstance(item, os.PathLike):
        return {"value": redact_sensitive(item)}
    if isinstance(item, (bytes, bytearray)):
        return {"value": redact_sensitive(_decode_bytes(item))}
    if isinstance(item, BaseException):
        return {"value": redact_sensitive(_safe_exc_text(item))}
    if isinstance(item, int) and not isinstance(item, bool):
        return {"value": game_data.fmt_compact_int(item)}
    if isinstance(item, (tuple, list, set, frozenset)):
        return {"value": redact_sensitive(game_data.safe_key(item))}
    try:
        raw = str(item)
    except Exception:
        raw = f"<{type(item).__name__}>"
    return {"value": redact_sensitive(raw)}


def format_action_summary(action: PlannedAction | dict[str, Any] | None) -> str:
    """Format an action concisely for console and `.log` lines."""
    data = _to_dict(action)
    if not data:
        return "none"
    prio = redact_sensitive(_safe_summary_text(data.get("priority"), "?"))
    ready = "READY" if data.get("executable_now") else "WAIT"
    kind = redact_sensitive(_safe_summary_text(data.get("kind"), "ACTION"))
    name = redact_sensitive(_safe_summary_text(data.get("target_name"), "?"))
    lvl = data.get("target_level")
    coords = redact_sensitive(_safe_summary_text(data.get("planet_coords"), ""))
    if isinstance(lvl, int) and not isinstance(lvl, bool) and lvl:
        lvl_part = f" -> L{game_data.fmt_compact_int(lvl)}"
    else:
        lvl_part = f" -> L{redact_sensitive(_safe_summary_text(lvl, ''))}" if lvl else ""
    coord_part = f" @ [{coords}]" if coords else ""
    missing = data.get("missing")
    miss_parts = (
        [
            f"{redact_sensitive(_safe_summary_text(k, '?'))}={game_data.fmt_compact_int(v, grouped=True)}"
            for k, v in missing.items()
            if isinstance(v, int) and not isinstance(v, bool) and v > 0
        ]
        if isinstance(missing, Mapping)
        else []
    )
    miss_part = f" (missing: {', '.join(miss_parts)})" if miss_parts else ""
    return f"[{prio}][{ready}] {kind} {name}{lvl_part}{coord_part}{miss_part}"


def pick_next_action(
    actions: Sequence[PlannedAction],
    actual_action: Optional[PlannedAction],
    *,
    after_live_execution: bool = False,
) -> Optional[PlannedAction]:
    """Select the logical next action after `actual_action`.

    When `actions` comes from a post-execution refresh (`after_live_execution=True`),
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


_REPO_ROOT_DIR: Path = Path(__file__).resolve().parents[2]
_REPO_LOGS_DIR: Path = (_REPO_ROOT_DIR / "logs").resolve()
DEFAULT_CRASH_LOG_DIR: Path = (_REPO_ROOT_DIR / "state").resolve()


def _should_skip_disk_log(target_dir: Optional[Path]) -> bool:
    if target_dir is None:
        return True
    if os.environ.get("SPACEINVASION_DISK_LOGS", "0").strip() == "1":
        return False
    try:
        resolved = Path(target_dir).resolve()
    except OSError:
        return True
    return resolved in (_REPO_LOGS_DIR, DEFAULT_CRASH_LOG_DIR)


def append_tick_logs(
    log_dir: Optional[Path],
    *,
    source: str,
    tick: int,
    mode: str,
    state: GameState,
    actual_action: PlannedAction | dict[str, Any] | None,
    actual_result: Optional[dict[str, Any]],
    next_action: PlannedAction | dict[str, Any] | None,
    outcomes: Sequence[dict[str, Any]] = (),
) -> tuple[Path, Path]:
    """Write structured JSONL and readable `.log` entries when `log_dir` is an explicit path, or no-op on disk in Podman/stdout mode."""
    skip_disk = _should_skip_disk_log(log_dir)
    if skip_disk:
        return Path("/dev/null"), Path("/dev/null")
    assert log_dir is not None
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / f"{source}.jsonl"
    text_path = log_dir / f"{source}.log"

    ts = datetime.now(timezone.utc).isoformat()
    planet = state.active_planet()
    actual_dict = _to_dict(actual_action)
    next_dict = _to_dict(next_action)

    record = cast(
        dict[str, Any],
        redact_sensitive(
            _coerce_json_scalars(
                game_data.sanitize_json_tree(
                    redact_sensitive(
                        {
                            "ts": ts,
                            "tick": tick,
                            "mode": mode,
                            "universe_id": state.universe_id,
                            "planet": planet.coords if planet else None,
                            "resources": asdict(planet.resources) if planet else None,
                            "energy": (
                                {"produced": planet.energy.produced, "used": planet.energy.used}
                                if planet
                                else None
                            ),
                            "actual_action": actual_dict,
                            "actual_result": actual_result,
                            "next_action": next_dict,
                            "outcomes": list(outcomes),
                        }
                    ),
                    max_int=game_data.MAX_JSON_SAFE_INT,
                    nonfinite_float=None,
                )
            )
        ),
    )
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, allow_nan=False) + "\n")

    sanitized_result = record.get("actual_result")
    raw_msg = (
        sanitized_result.get("message", "no action")
        if isinstance(sanitized_result, Mapping)
        else "no action"
    )
    result_msg = _safe_summary_text(raw_msg, "no action") if raw_msg is not None else "no action"
    planet_label = _safe_summary_text(record.get("planet"), "?") or "?"
    mode_label = _safe_summary_text(record.get("mode"), "") or _safe_summary_text(mode, "")
    if isinstance(tick, int) and not isinstance(tick, bool):
        tick_label = game_data.fmt_compact_int(tick)
    else:
        safe_tick = record.get("tick")
        if isinstance(safe_tick, int) and not isinstance(safe_tick, bool):
            tick_label = game_data.fmt_compact_int(safe_tick)
        else:
            tick_label = _safe_summary_text(safe_tick if safe_tick is not None else tick, "")
    res_str = planet.resources.format_short() if planet else "?"
    line = redact_sensitive(
        f"[{ts}] [tick {tick_label} | {mode_label}] {planet_label} | {res_str}\n"
        f"  ACTUAL: {format_action_summary(actual_dict)} => {result_msg}\n"
        f"  NEXT:   {format_action_summary(next_dict)}\n"
    )
    with text_path.open("a", encoding="utf-8") as handle:
        handle.write(line)

    return jsonl_path, text_path


def append_api_log(
    log_dir: Optional[Path],
    *,
    method: str,
    path: str,
    status: int,
    payload: Optional[dict[str, Any]] = None,
    response: Optional[dict[str, Any]] = None,
) -> tuple[Path, Path]:
    """Emit an HTTP API request/command audit event to container stdout (or explicit test `log_dir`)."""
    skip_disk = _should_skip_disk_log(log_dir)
    jsonl_path = (log_dir / "game_api.jsonl") if (not skip_disk and log_dir is not None) else Path("/dev/null")
    text_path = (log_dir / "game_api.log") if (not skip_disk and log_dir is not None) else Path("/dev/null")

    ts = datetime.now(timezone.utc).isoformat()
    payload_map = payload if isinstance(payload, Mapping) else {}
    resp_map = response if isinstance(response, Mapping) else {}
    actual_action = resp_map.get("action")
    next_action = resp_map.get("next_action")
    cmd = payload_map.get("command") or resp_map.get("command")

    record = redact_sensitive(
        _coerce_json_scalars(
            game_data.sanitize_json_tree(
                redact_sensitive(
                    {
                        "ts": ts,
                        "method": method,
                        "path": path,
                        "status": status,
                        "command": cmd,
                        "payload": payload,
                        "actual_action": actual_action,
                        "next_action": next_action,
                        "executed": resp_map.get("executed"),
                        "dry_run": resp_map.get("dry_run"),
                        "message": resp_map.get("message") or resp_map.get("error"),
                    }
                ),
                max_int=game_data.MAX_JSON_SAFE_INT,
                nonfinite_float=None,
            )
        )
    )

    method_label = _safe_summary_text(record.get("method"), "")
    redacted_path = _safe_summary_text(record.get("path"), "")
    msg = _safe_summary_text(record.get("message"), "")
    cmd_label = _safe_summary_text(record.get("command"), "")
    actual_str = (
        format_action_summary(record["actual_action"])
        if record.get("actual_action")
        else (cmd_label or redacted_path)
    )
    next_str = format_action_summary(record["next_action"]) if record["next_action"] else "n/a"
    status_label = (
        game_data.fmt_compact_int(status)
        if isinstance(status, int) and not isinstance(status, bool)
        else _safe_summary_text(record.get("status"), "")
    )
    line = redact_sensitive(
        f"[{ts}] {method_label} {redacted_path} -> {status_label} | actual={actual_str}"
        f" | next={next_str}" + (f" | {msg}" if msg else "") + "\n"
    )
    if skip_disk:
        # Avoid spamming read-only UI polling commands in pod logs; log mutating/action/error commands cleanly
        noisy_poll_cmds = {
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
        if int(status) >= 400 or cmd_label not in noisy_poll_cmds:
            sys.stdout.write(f"[API] {line}")
            sys.stdout.flush()
        return jsonl_path, text_path

    assert log_dir is not None
    log_dir.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, allow_nan=False) + "\n")
    with text_path.open("a", encoding="utf-8") as handle:
        handle.write(line)

    return jsonl_path, text_path


def append_crash_debug_log(
    *,
    component: str,
    event: str,
    error: Optional[BaseException] = None,
    details: Optional[Mapping[str, Any]] = None,
    crash_dir: Optional[Path] = None,
) -> Optional[tuple[Path, Path]]:
    """Emit a `sid=`-redacted diagnostic/crash record to stderr (or explicit `crash_dir` in tests).

    Never raises so instrumentation cannot mask or alter runtime behavior.
    """
    import traceback as _tb

    try:
        target_dir = crash_dir if crash_dir is not None else DEFAULT_CRASH_LOG_DIR
        skip_disk = _should_skip_disk_log(target_dir)

        ts = datetime.now(timezone.utc).isoformat()
        exc_type = type(error).__name__ if error is not None else None
        exc_msg = _safe_exc_text(error) if error is not None else None
        tb_str = (
            "".join(_tb.format_exception(type(error), error, error.__traceback__))
            if error is not None and getattr(error, "__traceback__", None) is not None
            else None
        )
        raw_rec = {
            "ts": ts,
            "component": component,
            "event": event,
            "exc_type": exc_type,
            "exc_message": exc_msg,
            "traceback": _clip_log_str(tb_str, 16_384) if tb_str else None,
            "details": dict(details) if isinstance(details, Mapping) else {},
        }
        record = redact_sensitive(
            _coerce_json_scalars(
                game_data.sanitize_json_tree(
                    redact_sensitive(raw_rec),
                    max_int=game_data.MAX_JSON_SAFE_INT,
                    nonfinite_float=None,
                )
            )
        )

        comp_s = _safe_summary_text(record.get("component"), "")
        evt_s = _safe_summary_text(record.get("event"), "")
        rec_exc_type = _safe_summary_text(record.get("exc_type"), "")
        rec_exc_msg = _safe_summary_text(record.get("exc_message"), "")
        header = redact_sensitive(
            f"=== [{ts}] {comp_s} :: {evt_s}"
            + (f" ({rec_exc_type}: {rec_exc_msg})" if rec_exc_type else "")
            + " ===\n"
        )
        det_json = redact_sensitive(
            json.dumps(record.get("details") or {}, indent=2, allow_nan=False)
        )
        tb_redacted = redact_sensitive(str(record.get("traceback") or ""))
        if skip_disk:
            sys.stderr.write(header)
            if det_json and det_json != "{}":
                sys.stderr.write(f"details: {det_json}\n")
            if tb_redacted:
                sys.stderr.write(f"traceback:\n{tb_redacted}\n")
            sys.stderr.flush()
            return None

        target_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = target_dir / "crash_debug.jsonl"
        text_path = target_dir / "crash_debug.log"
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, allow_nan=False) + "\n")
        with text_path.open("a", encoding="utf-8") as handle:
            handle.write(header)
            if det_json and det_json != "{}":
                handle.write(f"details: {det_json}\n")
            if tb_redacted:
                handle.write(f"traceback:\n{tb_redacted}\n")
        return jsonl_path, text_path
    except Exception:
        return None
