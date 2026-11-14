"""Scanned-system normalization and 3/24h Bash tracker hydration for fleet mission planning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

from spaceinvasion.combat.flight import BashTracker, normalize_coords
from spaceinvasion.core import catalog
from spaceinvasion.core.model import GameState
from spaceinvasion.planner.heuristics._common import (
    _parse_gal_sys_pos,
    _safe_strategy_float,
    _safe_strategy_str,
)


def _count_qualified_colony_coords(
    raid_targets: Sequence[object],
    own_coords: set[str],
    min_colony_fields: int,
) -> int:
    """Count distinct uninhabited coordinates meeting `min_colony_fields`."""
    qualified: set[str] = set()
    for raw_pre in raid_targets:
        if not isinstance(raw_pre, Mapping) or bool(raw_pre.get("discarded", False)):
            continue
        c_pre = catalog.safe_key(raw_pre.get("coords") or raw_pre.get("target_coords") or "").strip()
        if not c_pre:
            continue
        nc_pre = normalize_coords(c_pre)
        if not nc_pre or nc_pre in own_coords or bool(raw_pre.get("is_spacia", False)):
            continue
        sc_pre = catalog.safe_key(raw_pre.get("status_color") or "").strip().lower()
        ow_pre = catalog.safe_key(raw_pre.get("owner") or "").strip()
        raw_uf = raw_pre.get("uninhabited_fields")
        uf_pre = catalog.safe_optional_int(raw_uf, None) if raw_uf is not None else None
        raw_cmax = raw_pre.get("colonize_max_fields")
        cmax_pre = catalog.safe_optional_int(raw_cmax, None) if raw_cmax is not None else None
        eff_qual_f = (
            uf_pre
            if (uf_pre is not None and uf_pre > 0)
            else (cmax_pre if (cmax_pre is not None and cmax_pre > 0) else None)
        )
        if (sc_pre == "free" or (not ow_pre and eff_qual_f is not None)) and eff_qual_f is not None:
            if eff_qual_f >= min_colony_fields:
                qualified.add(nc_pre)
    return len(qualified)


def _parse_scanned_systems(scanned_systems: Mapping[str, float] | None) -> dict[str, float]:
    """Normalize `GG:SSS` keys from `scanned_systems` to latest scan timestamp."""
    scanned_map: dict[str, float] = {}
    if not isinstance(scanned_systems, Mapping):
        return scanned_map
    for raw_sys, raw_ts in scanned_systems.items():
        m_sys = re.search(r"(\d{1,2})\s*:\s*(\d{1,3})", catalog.safe_key(raw_sys), re.ASCII)
        if m_sys:
            skey = f"{int(m_sys.group(1)):02d}:{int(m_sys.group(2)):03d}"
            scanned_map[skey] = max(scanned_map.get(skey, 0.0), _safe_strategy_float(raw_ts, 0.0))
    return scanned_map


def _record_bash_blocks_from_source(
    rs_src: Mapping[str, object],
    bash_tracker: BashTracker,
    ref_now: float,
    skew_max: float,
    max_until_ts: float,
) -> None:
    raw_bt = rs_src.get("blocked_targets")
    if isinstance(raw_bt, Mapping):
        for bk, bv in raw_bt.items():
            bk_s = catalog.safe_key(bk).strip()
            if not bk_s:
                continue
            if isinstance(bv, Mapping):
                b_until = _safe_strategy_float(bv.get("blocked_until_ts"), 0.0)
                b_last_ts = _safe_strategy_float(
                    bv.get("last_blocked_ts") if bv.get("last_blocked_ts") is not None else bv.get("blocked_ts"),
                    0.0,
                )
                b_reason = _safe_strategy_str(bv.get("reason") or bv.get("block_reason"))
            else:
                b_until = _safe_strategy_float(bv, 0.0)
                b_last_ts = 0.0
                b_reason = ""
            if (b_last_ts > 0.0 and b_last_ts > skew_max) or (b_last_ts <= 0.0 and b_until > max_until_ts):
                continue
            if b_until > ref_now:
                bash_tracker.record_block(normalize_coords(bk_s), b_until, reason=b_reason)
    raw_bb = rs_src.get("bash_blocks")
    if isinstance(raw_bb, Sequence) and not isinstance(raw_bb, (str, bytes)):
        for brow in raw_bb:
            if not isinstance(brow, Mapping):
                continue
            bc_s = catalog.safe_key(brow.get("target_coords") or brow.get("coords") or "").strip()
            b_until = _safe_strategy_float(brow.get("blocked_until_ts"), 0.0)
            b_ts = _safe_strategy_float(
                brow.get("blocked_ts") if brow.get("blocked_ts") is not None else brow.get("last_blocked_ts"),
                0.0,
            )
            if (b_ts > 0.0 and b_ts > skew_max) or (b_ts <= 0.0 and b_until > max_until_ts):
                continue
            if bc_s and b_until > ref_now:
                bash_tracker.record_block(
                    normalize_coords(bc_s), b_until, reason=_safe_strategy_str(brow.get("reason"))
                )


def _hydrate_bash_tracker(
    state: GameState,
    raid_stats: Mapping[str, object] | None,
    ref_now: float,
) -> tuple[BashTracker, dict[str, float], list[Mapping[str, object]]]:
    """Hydrate `BashTracker` and `latest_battle_by_sys` from state and external `raid_stats`."""
    bash_tracker = BashTracker()
    latest_battle_by_sys: dict[str, float] = {}
    stats_sources: list[Mapping[str, object]] = []
    state_rs = getattr(state, "raid_stats", None)
    if isinstance(state_rs, Mapping):
        stats_sources.append(state_rs)
    if isinstance(raid_stats, Mapping) and raid_stats is not state_rs:
        stats_sources.append(raid_stats)
    hist_entries: list[tuple[float, int, str, bool]] = []
    seen_hist_keys: set[tuple[str, float]] = set()
    skew_max = (ref_now + 60.0) if ref_now > 0.0 else float("inf")
    max_until_ts = (ref_now + 86400.0 * 2.0) if ref_now > 0.0 else float("inf")
    for rs_src in stats_sources:
        raw_hist = rs_src.get("history")
        if isinstance(raw_hist, Sequence) and not isinstance(raw_hist, (str, bytes)):
            for idx_h, hrow in enumerate(raw_hist):
                if not isinstance(hrow, Mapping):
                    continue
                raw_hc = catalog.safe_key(hrow.get("target_coords") or hrow.get("coords") or "").strip()
                if not raw_hc:
                    continue
                norm_hc = normalize_coords(raw_hc)
                raw_hts = (
                    hrow.get("attacked_ts")
                    if hrow.get("attacked_ts") is not None
                    else (hrow.get("ts") if hrow.get("ts") is not None else hrow.get("timestamp"))
                )
                hts = _safe_strategy_float(raw_hts, 0.0)
                if hts <= 0.0 or hts > skew_max:
                    continue
                h_gal, h_sys, _ = _parse_gal_sys_pos(norm_hc)
                h_skey = f"{h_gal:02d}:{h_sys:03d}"
                latest_battle_by_sys[h_skey] = max(latest_battle_by_sys.get(h_skey, 0.0), hts)
                dedup_k = (norm_hc, round(hts, 3))
                if dedup_k in seen_hist_keys:
                    continue
                seen_hist_keys.add(dedup_k)
                if (ref_now - 86400.0) <= hts <= skew_max:
                    hist_entries.append((min(hts, ref_now), idx_h, norm_hc, bool(hrow.get("is_exempt", False))))
        _record_bash_blocks_from_source(rs_src, bash_tracker, ref_now, skew_max, max_until_ts)
    hist_entries.sort(key=lambda item: (item[0], item[1]))
    for hts, _, norm_hc, is_ex in hist_entries:
        if not is_ex:
            bash_tracker.record_attack(norm_hc, timestamp=hts)
    return bash_tracker, latest_battle_by_sys, stats_sources
