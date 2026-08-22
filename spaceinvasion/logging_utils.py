"""Structured JSONL and human-readable logging helpers for `logs/`."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Optional, Sequence

from spaceinvasion.state import GameState
from spaceinvasion.strategy import PlannedAction

_SID_RE = re.compile(r"(sid=)([0-9a-fA-F]+)")


def redact_sensitive(obj: Any) -> Any:
    """Recursively mask transient session tokens (`sid=`) before writing logs."""
    if isinstance(obj, str):
        return _SID_RE.sub(r"\1REDACTED", obj)
    if isinstance(obj, dict):
        return {k: redact_sensitive(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_sensitive(v) for v in obj]
    return obj


def _to_dict(item: Any) -> Optional[dict[str, Any]]:
    if item is None:
        return None
    if isinstance(item, dict):
        return redact_sensitive(item)
    if is_dataclass(item):
        return redact_sensitive(asdict(item))
    return {"value": redact_sensitive(str(item))}


def format_action_summary(action: PlannedAction | dict[str, Any] | None) -> str:
    """Format an action concisely for console and `.log` lines."""
    data = _to_dict(action)
    if not data:
        return "none"
    prio = data.get("priority", "?")
    ready = "READY" if data.get("executable_now") else "WAIT"
    kind = data.get("kind", "ACTION")
    name = data.get("target_name", "?")
    lvl = data.get("target_level")
    coords = data.get("planet_coords") or ""
    lvl_part = f" -> L{lvl}" if lvl else ""
    coord_part = f" @ [{coords}]" if coords else ""
    missing = data.get("missing") or {}
    miss_parts = [
        f"{k}={v:,}"
        for k, v in missing.items()
        if isinstance(v, int) and v > 0
    ]
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


def append_tick_logs(
    log_dir: Path,
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
    """Write structured JSONL and readable `.log` entries under `log_dir`."""
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / f"{source}.jsonl"
    text_path = log_dir / f"{source}.log"

    ts = datetime.now(timezone.utc).isoformat()
    planet = state.active_planet()
    actual_dict = _to_dict(actual_action)
    next_dict = _to_dict(next_action)

    record = redact_sensitive(
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
    )
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")

    result_msg = redact_sensitive((actual_result or {}).get("message", "no action"))
    res_str = planet.resources.format_short() if planet else "?"
    line = (
        f"[{ts}] [tick {tick} | {mode}] {planet.coords if planet else '?'} | {res_str}\n"
        f"  ACTUAL: {format_action_summary(actual_dict)} => {result_msg}\n"
        f"  NEXT:   {format_action_summary(next_dict)}\n"
    )
    with text_path.open("a", encoding="utf-8") as handle:
        handle.write(line)

    return jsonl_path, text_path


def append_api_log(
    log_dir: Path,
    *,
    method: str,
    path: str,
    status: int,
    payload: Optional[dict[str, Any]] = None,
    response: Optional[dict[str, Any]] = None,
) -> tuple[Path, Path]:
    """Append an HTTP API request/command audit event into `logs/game_api.*`."""
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / "game_api.jsonl"
    text_path = log_dir / "game_api.log"

    ts = datetime.now(timezone.utc).isoformat()
    actual_action = (response or {}).get("action")
    next_action = (response or {}).get("next_action")
    cmd = (payload or {}).get("command") or (response or {}).get("command")

    record = redact_sensitive(
        {
            "ts": ts,
            "method": method,
            "path": path,
            "status": status,
            "command": cmd,
            "payload": payload,
            "actual_action": actual_action,
            "next_action": next_action,
            "executed": (response or {}).get("executed"),
            "dry_run": (response or {}).get("dry_run"),
            "message": (response or {}).get("message") or (response or {}).get("error"),
        }
    )
    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")

    msg = record["message"] or ""
    actual_str = format_action_summary(actual_action) if actual_action else (cmd or path)
    next_str = format_action_summary(next_action) if next_action else "n/a"
    line = (
        f"[{ts}] {method} {path} -> {status} | actual={actual_str}"
        f" | next={next_str}" + (f" | {msg}" if msg else "") + "\n"
    )
    with text_path.open("a", encoding="utf-8") as handle:
        handle.write(line)

    return jsonl_path, text_path
