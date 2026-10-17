"""JSON rank history loader and snapshot recorder for ``spaceinvasion.combat.points``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
from pathlib import Path
import time

from spaceinvasion.combat._points_helpers import (
    MAX_JSON_SAFE,
    cap_eta_float,
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


def compute_observed_trend(history: Sequence[Mapping[str, object]] | None) -> dict[str, object]:
    """Compute observed points/rank deltas and hourly climb rate from recorded rank snapshots."""
    samples: list[dict[str, object]] = [
        sanitize_json_tree(s, max_int=MAX_JSON_SAFE, nonfinite_float=None)
        for s in (history or ())
        if isinstance(s, Mapping)
    ]
    observed_delta_pts: float | None = None
    observed_delta_rank: int | None = None
    observed_rate_hr: float | None = None
    elapsed_hours = 0.0
    if len(samples) >= 2:
        first, last = samples[0], samples[-1]
        dt_sec = cap_eta_float(
            safe_finite_float(last.get("ts", 0.0), 0.0) - safe_finite_float(first.get("ts", 0.0), 0.0),
            allow_negative=True,
        )
        elapsed_hours = cap_eta_float(max(0.0, dt_sec / 3600.0))
        p0, p1 = (
            safe_optional_finite_float(first.get("server_points")),
            safe_optional_finite_float(last.get("server_points")),
        )
        if p0 is not None and p1 is not None and p1 != p0:
            raw_delta = p1 - p0
        else:
            raw_delta = safe_finite_float(last.get("estimated_points", 0.0), 0.0) - safe_finite_float(
                first.get("estimated_points", 0.0), 0.0
            )
        observed_delta_pts = round(cap_eta_float(raw_delta, allow_negative=True), 1)
        if elapsed_hours >= 0.05 and math.isfinite(observed_delta_pts):
            rate_val = observed_delta_pts / elapsed_hours
            observed_rate_hr = (
                round(cap_eta_float(rate_val, allow_negative=True), 2) if math.isfinite(rate_val) else None
            )
        r0, r1 = safe_optional_finite_int(first.get("server_rank")), safe_optional_finite_int(last.get("server_rank"))
        if r0 is not None and r1 is not None:
            observed_delta_rank = max(-MAX_JSON_SAFE, min(MAX_JSON_SAFE, r0 - r1))
    return {
        "sample_count": len(samples),
        "elapsed_hours": round(cap_eta_float(elapsed_hours), 2),
        "delta_points": observed_delta_pts,
        "rank_climbed": observed_delta_rank,
        "observed_pts_per_hr": observed_rate_hr,
        "samples": samples[-20:],
    }
