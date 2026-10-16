"""JSON rank history loader and snapshot recorder for ``spaceinvasion.combat.points``."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import time

from spaceinvasion.combat._points_helpers import (
    MAX_JSON_SAFE,
    estimate_hourly_economy,
    safe_finite_float,
    safe_nonneg_int,
    safe_optional_finite_float,
    safe_optional_finite_int,
)
from spaceinvasion.core.catalog import sanitize_json_tree
from spaceinvasion.core.model import GameState


def load_rank_history(history_path: Path | None) -> list[dict[str, object]]:
    """Load persisted rank evolution samples safely from JSON."""
    if history_path is None or not history_path.exists():
        return []
    try:
        raw = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [
        sanitize_json_tree(item, max_int=MAX_JSON_SAFE, nonfinite_float=None)
        for item in raw
        if isinstance(item, Mapping)
    ]


def _should_append_rank_sample(
    samples: list[dict[str, object]],
    entry: Mapping[str, object],
    min_interval_seconds: float,
) -> bool:
    if not samples:
        return True
    last = samples[-1]
    last_ts = safe_optional_finite_float(last.get("ts"))
    last_est = safe_optional_finite_float(last.get("estimated_points"))
    if last_ts is None or last_est is None:
        return True
    elapsed = abs(safe_finite_float(entry.get("ts"), 0.0) - last_ts)
    changed_server = (
        entry.get("server_points") is not None and entry.get("server_points") != last.get("server_points")
    ) or (entry.get("server_rank") is not None and entry.get("server_rank") != last.get("server_rank"))
    changed_est = int(round(safe_finite_float(entry.get("estimated_points"), 0.0))) != int(round(last_est))
    return changed_server or changed_est or elapsed >= min_interval_seconds


def record_rank_snapshot(
    history_path: Path | None,
    state: GameState,
    estimate_account_points_fn: object,
    *,
    max_entries: int = 240,
    min_interval_seconds: float = 300.0,
) -> list[dict[str, object]]:
    """Append a deduplicated rank/points observation to ``history_path`` (JSON) and return samples."""
    samples = load_rank_history(history_path)
    assert callable(estimate_account_points_fn)
    est = estimate_account_points_fn(state)
    econ = estimate_hourly_economy(state)
    now_ts = safe_finite_float(state.timestamp, 0.0) or time.time()
    srv_pts = min(MAX_JSON_SAFE, safe_nonneg_int(state.account_points, 0)) if state.account_points is not None else None
    srv_rank = safe_optional_finite_int(state.account_rank)
    if srv_rank is not None:
        srv_rank = max(0, min(MAX_JSON_SAFE, srv_rank))
    entry: dict[str, object] = {
        "ts": round(now_ts, 3),
        "server_points": srv_pts,
        "server_rank": srv_rank,
        "estimated_points": est["estimated_points"],
        "pts_per_hr": econ["pts_per_hr_steady"],
        "spice_value": est["spice_value"],
        "planets": len(state.planets),
    }
    if _should_append_rank_sample(samples, entry, min_interval_seconds):
        samples.append(entry)
        if len(samples) > max_entries:
            samples = samples[-max_entries:]
        if history_path is not None:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            safe_samples = sanitize_json_tree(samples, max_int=MAX_JSON_SAFE, nonfinite_float=None)
            history_path.write_text(json.dumps(safe_samples, indent=2, allow_nan=False), encoding="utf-8")
    return samples
