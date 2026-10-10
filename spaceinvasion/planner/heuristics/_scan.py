"""Scan-system selection adapter (delegates to `combat.scanner` when merged; zero `planner.optimizer` import)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import importlib
import time

from spaceinvasion.combat.fleet import score_galaxy_scan_sectors
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.planner.heuristics._common import _safe_strategy_float, _safe_strategy_int

_PLANNER_SCAN_COOLDOWN_S: float = 1800.0


def select_planner_scan_system(
    origin_coords: Sequence[str] | str,
    *,
    scanned_systems: Mapping[object, object] | None = None,
    recent: set[tuple[int, int]] | None = None,
    raid_targets: Sequence[Mapping[str, object]] | None = None,
    raid_stats: Mapping[str, object] | None = None,
    cfg: StrategyConfig | None = None,
    radius: int = 15,
    now_ts: float | None = None,
    cooldown_s: float = _PLANNER_SCAN_COOLDOWN_S,
) -> dict[str, object] | None:
    """Pick ONE smart `SCAN_GALAXY` system for the planner (2-phase: explore outward -> weighted refresh)."""
    try:
        scanner_mod = importlib.import_module("spaceinvasion.combat.scanner")
        scanner_fn = getattr(scanner_mod, "select_planner_scan_system", None)
        if callable(scanner_fn):
            picked = scanner_fn(
                origin_coords,
                scanned_systems=scanned_systems,
                recent=recent,
                raid_targets=raid_targets,
                raid_stats=raid_stats,
                cfg=cfg,
                radius=radius,
                now_ts=now_ts,
                cooldown_s=cooldown_s,
            )
            return dict(picked) if isinstance(picked, Mapping) else None
    except ModuleNotFoundError:
        pass

    own_list = [origin_coords] if isinstance(origin_coords, str) else [str(c) for c in origin_coords if c]
    if not own_list:
        return None
    eff_now = _safe_strategy_float(now_ts, 0.0)
    if eff_now <= 0.0:
        eff_now = time.time()
    explore_first = bool(getattr(cfg, "galaxy_scan_explore_first", True)) if cfg is not None else True
    near_w = _safe_strategy_float(getattr(cfg, "galaxy_scan_near_weight", 2.0), 2.0) if cfg is not None else 2.0
    attack_w = _safe_strategy_float(getattr(cfg, "galaxy_scan_attack_weight", 3.0), 3.0) if cfg is not None else 3.0
    recent_set = recent if isinstance(recent, set) else set()
    try:
        ranked = score_galaxy_scan_sectors(
            own_coords=own_list,
            scanned_systems=scanned_systems if isinstance(scanned_systems, Mapping) else None,
            raid_targets=[r for r in (raid_targets or ()) if isinstance(r, Mapping)],
            raid_stats=raid_stats if isinstance(raid_stats, Mapping) else None,
            explore_first=explore_first,
            near_weight=near_w,
            attack_weight=attack_w,
            min_freshness_seconds=max(0.0, float(cooldown_s)),
            now_ts=eff_now,
        )
    except (TypeError, ValueError, KeyError, OverflowError):
        return None
    eff_radius = max(0, int(radius))

    def _usable(it: Mapping[str, object]) -> bool:
        return (not bool(it.get("is_fresh"))) and (
            _safe_strategy_int(it.get("galaxy"), 0),
            _safe_strategy_int(it.get("system"), 0),
        ) not in recent_set

    local = [it for it in ranked if _safe_strategy_int(it.get("distance"), 0) <= eff_radius and _usable(it)]
    if explore_first:
        for it in local:
            if not it.get("scanned"):
                return {**it, "phase": "exploratory"}
    refresh_local = [it for it in local if it.get("scanned")] if explore_first else local
    if refresh_local:
        best = max(
            refresh_local,
            key=lambda it: (
                _safe_strategy_float(it.get("score"), 0.0),
                _safe_strategy_float(it.get("importance"), 0.0),
                -_safe_strategy_int(it.get("distance"), 0),
            ),
        )
        return {**best, "phase": "exploratory" if not best.get("scanned") else "targeted_refresh"}
    return None
