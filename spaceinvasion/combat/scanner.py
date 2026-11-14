"""Smart 2-phase galaxy scan system selection for the tactical planner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
import time
from typing import Any, Optional

from spaceinvasion.combat._scanner_batch import select_smart_galaxy_scan_batch as select_smart_galaxy_scan_batch
from spaceinvasion.combat._scanner_sectors import score_galaxy_scan_sectors as score_galaxy_scan_sectors
from spaceinvasion.combat._scanner_walk import order_sectors_for_smooth_walk as order_sectors_for_smooth_walk
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.coords import ARROW_NAV_MAX_DISTANCE as ARROW_NAV_MAX_DISTANCE

_PLANNER_SCAN_COOLDOWN_S: float = 1800.0
_GAL_SYS_RE = re.compile(r"(\d{1,2}):(\d{1,3})(?::(\d{1,2}))?", re.ASCII)


def _safe_float_any(val: Any, default: float = 0.0) -> float:
    if type(val) is float:
        return val if math.isfinite(val) else default
    if val is None or isinstance(val, bool):
        return default
    if type(val) is int:
        try:
            parsed = float(val)
        except OverflowError:
            return default
        return parsed if math.isfinite(parsed) else default
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")
    if isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii() or txt.lower() in ("null", "none", "~"):
            return default
        val = txt
    elif isinstance(val, (dict, list, tuple, set, frozenset)) or isinstance(val, Mapping):
        return default
    try:
        parsed = float(val)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def _parse_gal_sys(coords: str) -> tuple[int, int]:
    m = _GAL_SYS_RE.search(str(coords or ""))
    if not m:
        return 1, 1
    g1, g2 = m.group(1), m.group(2)
    if not (g1.isascii() and g1.isdigit() and g2.isascii() and g2.isdigit()):
        return 1, 1
    return int(g1), int(g2)


def select_planner_scan_system(
    origin_coords: Sequence[str] | str,
    *,
    scanned_systems: Optional[Mapping[Any, Any]] = None,
    recent: Optional[set[tuple[int, int]]] = None,
    raid_targets: Optional[Sequence[Mapping[str, Any]]] = None,
    raid_stats: Optional[Mapping[str, Any]] = None,
    cfg: Optional[StrategyConfig] = None,
    radius: int = 15,
    now_ts: Optional[float] = None,
    cooldown_s: float = _PLANNER_SCAN_COOLDOWN_S,
) -> Optional[dict[str, Any]]:
    """Pick ONE smart `SCAN_GALAXY` system for the planner (2-phase: explore outward -> weighted stale refresh)."""
    own_list: list[str] = [origin_coords] if isinstance(origin_coords, str) else [str(c) for c in origin_coords if c]
    if not own_list:
        return None
    eff_now = _safe_float_any(now_ts, 0.0)
    if eff_now <= 0.0:
        eff_now = time.time()
    explore_first = bool(getattr(cfg, "galaxy_scan_explore_first", True)) if cfg is not None else True
    near_w = _safe_float_any(getattr(cfg, "galaxy_scan_near_weight", 2.0), 2.0) if cfg is not None else 2.0
    attack_w = _safe_float_any(getattr(cfg, "galaxy_scan_attack_weight", 3.0), 3.0) if cfg is not None else 3.0
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
    except (TypeError, ValueError, KeyError, OverflowError):  # pragma: no cover - defensive fallback
        return None
    eff_radius = max(0, int(radius))

    def _usable(it: Mapping[str, Any]) -> bool:
        return (not bool(it.get("is_fresh"))) and (int(it["galaxy"]), int(it["system"])) not in recent_set

    local = [it for it in ranked if int(it.get("distance", 0)) <= eff_radius and _usable(it)]
    if explore_first:
        for it in local:
            if not it.get("scanned"):
                return dict(it, phase="exploratory")
    refresh_local = [it for it in local if it.get("scanned")] if explore_first else local
    if refresh_local:
        best = max(
            refresh_local,
            key=lambda it: (
                float(it.get("score", 0.0)),
                float(it.get("importance", 0.0)),
                -int(it.get("distance", 0)),
            ),
        )
        return dict(best, phase="exploratory" if not best.get("scanned") else "targeted_refresh")
    return None


def _next_unscanned_system(
    origin_coords: str,
    scanned: set[tuple[int, int]],
    *,
    radius: int = 15,
    scanned_ts: Optional[Mapping[Any, Any]] = None,
    raid_targets: Optional[Sequence[Mapping[str, Any]]] = None,
    raid_stats: Optional[Mapping[str, Any]] = None,
    cfg: Optional[StrategyConfig] = None,
    now_ts: Optional[float] = None,
) -> Optional[tuple[int, int]]:
    """Return next `(galaxy, system)` to scan; `scanned` = systems on scan cooldown."""
    if scanned_ts is not None:
        pick = select_planner_scan_system(
            origin_coords,
            scanned_systems=scanned_ts,
            recent=scanned,
            raid_targets=raid_targets,
            raid_stats=raid_stats,
            cfg=cfg,
            radius=radius,
            now_ts=now_ts,
        )
        if pick is not None:
            return (int(pick["galaxy"]), int(pick["system"]))
        return None
    gal, sys_num = _parse_gal_sys(origin_coords)
    if (gal, sys_num) not in scanned:
        return (gal, sys_num)
    for delta in range(1, max(1, radius) + 1):
        for cand_sys in (sys_num + delta, sys_num - delta):
            if 1 <= cand_sys <= 400 and (gal, cand_sys) not in scanned:
                return (gal, cand_sys)
    return None
