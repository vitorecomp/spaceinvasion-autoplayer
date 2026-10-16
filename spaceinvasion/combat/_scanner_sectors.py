"""Galaxy sector intel aggregation and priority scoring helpers for smart scanning."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
import math
import re
import time
from typing import Any, Optional

from spaceinvasion.core.catalog import safe_finite_float, safe_key, spice_equivalent
from spaceinvasion.core.coercion import coerce_int
from spaceinvasion.core.coords import normalize_coords
from spaceinvasion.core.model import Resources


def parse_gal_sys_pair(raw: Any) -> Optional[tuple[int, int]]:
    if isinstance(raw, (tuple, list)) and len(raw) >= 2:
        g, s = coerce_int(raw[0], 0), coerce_int(raw[1], 0)
        return (g, s) if (1 <= g <= 14 and 1 <= s <= 400) else None
    txt = safe_key(raw).strip() if raw is not None else ""
    if not txt:
        return None
    m = re.search(r"(\d{1,2})\s*[:/\-]\s*(\d{1,3})", txt, re.ASCII)
    if not m:
        return None
    g, s = int(m.group(1)), int(m.group(2))
    return (g, s) if (1 <= g <= 14 and 1 <= s <= 400) else None


def extract_own_colonies_and_coords(
    own_coords: Sequence[str] | str, *, systems_per_galaxy: int = 400
) -> tuple[list[tuple[int, int]], set[str]]:
    if isinstance(own_coords, (str, bytes, bytearray)):
        raw_items: Sequence[Any] = [own_coords]
    elif isinstance(own_coords, Sequence):
        raw_items = own_coords
    elif isinstance(own_coords, Iterable):
        raw_items = list(own_coords)
    else:
        raw_items = ()
    colonies: list[tuple[int, int]] = []
    seen_gs: set[tuple[int, int]] = set()
    norm_set: set[str] = set()
    for raw in raw_items:
        txt = safe_key(raw).strip() if raw is not None else ""
        if not txt:
            continue
        nc = normalize_coords(txt)
        if re.fullmatch(r"\d{2}:\d{3}:\d{2}", nc, re.ASCII):
            norm_set.add(nc)
        pair = parse_gal_sys_pair(nc or txt)
        if pair is not None and 1 <= pair[1] <= systems_per_galaxy and pair not in seen_gs:
            seen_gs.add(pair)
            colonies.append(pair)
    return colonies, norm_set


def format_age_short(age_seconds: Optional[float]) -> str:
    if age_seconds is None or not math.isfinite(age_seconds):
        return "unscanned"
    sec = max(0, int(round(age_seconds)))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    return f"{sec / 3600.0:.1f}h" if sec < 86400 else f"{sec / 86400.0:.1f}d"


def _resolve_active_galaxies(
    galaxies: Optional[Sequence[int]],
    own_colonies: Sequence[tuple[int, int]],
    sector_intel: Optional[Mapping[str, Any]],
) -> list[int]:
    active: list[int] = []
    if galaxies is not None and not isinstance(galaxies, (str, bytes, bytearray)):
        for g_raw in galaxies:
            gi = coerce_int(g_raw, 0)
            if 1 <= gi <= 14 and gi not in active:
                active.append(gi)
    if not active and own_colonies:
        for gi, _ in own_colonies:
            if 1 <= gi <= 14 and gi not in active:
                active.append(gi)
    if not active and isinstance(sector_intel, Mapping):
        raw_gals = sector_intel.get("galaxies")
        if isinstance(raw_gals, Sequence) and not isinstance(raw_gals, (str, bytes, bytearray)):
            for g_raw in raw_gals:
                gi = coerce_int(g_raw, 0)
                if 1 <= gi <= 14 and gi not in active:
                    active.append(gi)
    return active or [1]


def _ensure_sector_entry(sectors_data: dict[str, dict[str, Any]], gal: int, sys_num: int) -> dict[str, Any]:
    skey = f"{gal:02d}:{sys_num:03d}"
    entry = sectors_data.get(skey)
    if entry is None:
        entry = {
            "galaxy": gal, "system": sys_num, "last_scanned_ts": 0.0, "scan_count": 0,
            "occupied_count": 0, "target_count": 0, "inactive_count": 0, "spacia_count": 0,
            "debris_count": 0, "free_count": 0, "prime_colony_free_count": 0,
            "direct_raid_count": 0, "direct_recency_raid_score": 0.0, "direct_loot_spice_eq": 0.0,
            "last_attacked_ts": 0.0, "owner_planet_counts": {}, "owner_display": {},
        }
        sectors_data[skey] = entry
    return entry


def _ingest_sector_intel_mapping(
    sector_intel: Mapping[str, Any],
    sectors_data: dict[str, dict[str, Any]],
    owner_stats: dict[str, dict[str, Any]],
    n_sys: int,
) -> None:
    raw_owner_stats = sector_intel.get("owner_raid_stats")
    if isinstance(raw_owner_stats, Mapping):
        for ok_raw, ov in raw_owner_stats.items():
            if isinstance(ov, Mapping) and (disp := safe_key(ov.get("owner") or ok_raw).strip()) and disp.lower() != "spacia":
                owner_stats[disp.lower()] = {
                    "owner": disp,
                    "raid_count": max(0, coerce_int(ov.get("raid_count"), 0)),
                    "recency_raid_score": max(0.0, safe_finite_float(ov.get("recency_raid_score"), 0.0)),
                    "loot_spice_eq": max(0.0, safe_finite_float(ov.get("loot_spice_eq"), 0.0)),
                }
    raw_owner_counts = sector_intel.get("owner_attack_counts")
    if isinstance(raw_owner_counts, Mapping):
        for ok_raw, cnt_raw in raw_owner_counts.items():
            disp, cnt = safe_key(ok_raw).strip(), max(0, coerce_int(cnt_raw, 0))
            if disp and disp.lower() != "spacia" and cnt > 0:
                cur_os = owner_stats.setdefault(disp.lower(), {"owner": disp, "raid_count": 0, "recency_raid_score": float(cnt), "loot_spice_eq": 0.0})
                cur_os["raid_count"] = max(int(cur_os["raid_count"]), cnt)
    raw_sectors = sector_intel.get("sectors")
    if not isinstance(raw_sectors, Mapping):
        return
    for skey_raw, sv in raw_sectors.items():
        if not isinstance(sv, Mapping):
            continue
        pair = parse_gal_sys_pair(skey_raw)
        gal, sys_num = coerce_int(sv.get("galaxy"), pair[0] if pair else 0), coerce_int(sv.get("system"), pair[1] if pair else 0)
        if not (1 <= gal <= 14 and 1 <= sys_num <= n_sys):
            continue
        sec = _ensure_sector_entry(sectors_data, gal, sys_num)
        for fk in ("last_scanned_ts", "direct_recency_raid_score", "direct_loot_spice_eq", "last_attacked_ts"):
            sec[fk] = max(sec[fk], safe_finite_float(sv.get(fk), 0.0))
        for ik in ("scan_count", "occupied_count", "target_count", "inactive_count", "spacia_count", "debris_count", "free_count", "prime_colony_free_count", "direct_raid_count"):
            sec[ik] = max(sec[ik], coerce_int(sv.get(ik), 0))
        if isinstance(raw_op := sv.get("owner_planet_counts"), Mapping):
            for oname_raw, pcnt_raw in raw_op.items():
                disp, pcnt = safe_key(oname_raw).strip(), max(0, coerce_int(pcnt_raw, 0))
                if disp and disp.lower() != "spacia" and pcnt > 0:
                    okey = disp.lower()
                    sec["owner_planet_counts"][okey] = max(sec["owner_planet_counts"].get(okey, 0), pcnt)
                    sec["owner_display"].setdefault(okey, disp)
        if isinstance(raw_ol := sv.get("owners"), Sequence) and not isinstance(raw_ol, (str, bytes, bytearray)):
            for oname_raw in raw_ol:
                if (disp := safe_key(oname_raw).strip()) and disp.lower() != "spacia":
                    sec["owner_planet_counts"].setdefault(disp.lower(), 1)
                    sec["owner_display"].setdefault(disp.lower(), disp)


def _ingest_scanned_systems(
    scanned_systems: Optional[Mapping[Any, Any] | Iterable[Any]],
    sectors_data: dict[str, dict[str, Any]],
    n_sys: int,
    eff_now: float,
) -> None:
    if isinstance(scanned_systems, Mapping):
        for raw_k, raw_ts in scanned_systems.items():
            pair = parse_gal_sys_pair(raw_k)
            if pair is not None and 1 <= pair[1] <= n_sys and (ts_val := safe_finite_float(raw_ts, 0.0)) > 0.0:
                sec = _ensure_sector_entry(sectors_data, pair[0], pair[1])
                sec["last_scanned_ts"] = max(sec["last_scanned_ts"], ts_val)
                sec["scan_count"] = max(sec["scan_count"], 1)
    elif isinstance(scanned_systems, Iterable) and not isinstance(scanned_systems, (str, bytes, bytearray)):
        fallback_ts = max(1.0, eff_now - 3600.0)
        for raw_k in scanned_systems:
            pair = parse_gal_sys_pair(raw_k)
            if pair is not None and 1 <= pair[1] <= n_sys:
                sec = _ensure_sector_entry(sectors_data, pair[0], pair[1])
                if sec["last_scanned_ts"] <= 0.0:
                    sec["last_scanned_ts"] = fallback_ts
                sec["scan_count"] = max(sec["scan_count"], 1)


def _ingest_raid_targets(
    raid_targets: Optional[Sequence[Mapping[str, Any]]],
    own_norm_coords: set[str],
    sectors_data: dict[str, dict[str, Any]],
    n_sys: int,
) -> tuple[dict[str, str], set[str]]:
    coord_owner: dict[str, str] = {}
    coord_spacia: set[str] = set()
    if not (isinstance(raid_targets, Sequence) and not isinstance(raid_targets, (str, bytes, bytearray))):
        return coord_owner, coord_spacia
    per_sec_owner: dict[tuple[int, int], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    buckets: dict[str, dict[tuple[int, int], set[str]]] = {
        k: defaultdict(set) for k in ("debris_count", "inactive_count", "spacia_count", "free_count", "prime_colony_free_count")
    }
    for trow in raid_targets:
        if not isinstance(trow, Mapping) or bool(trow.get("is_own_planet", False)) or str(trow.get("merged_into") or "").strip():
            continue
        nc = normalize_coords(trow.get("coords") or trow.get("target_coords") or "")
        if not nc or nc in own_norm_coords or not (m_c := re.fullmatch(r"(\d{2}):(\d{3}):(\d{2})", nc, re.ASCII)):
            continue
        tg, ts_i, tp = int(m_c.group(1)), int(m_c.group(2)), int(m_c.group(3))
        if not (1 <= tg <= 14 and 1 <= ts_i <= n_sys):
            continue
        sec = _ensure_sector_entry(sectors_data, tg, ts_i)
        scolor = safe_key(trow.get("status_color") or "").strip().lower()
        is_sp = bool(trow.get("is_spacia", False))
        owner_disp = safe_key(trow.get("owner") or "").strip()
        if is_sp or owner_disp.lower() == "spacia":
            coord_spacia.add(nc)
            buckets["spacia_count"][(tg, ts_i)].add(nc)
        elif owner_disp and scolor != "free":
            coord_owner[nc] = owner_disp
            okey = owner_disp.lower()
            per_sec_owner[(tg, ts_i)][okey].add(nc)
            sec["owner_display"].setdefault(okey, owner_disp)
        if bool(trow.get("has_debris", False)):
            buckets["debris_count"][(tg, ts_i)].add(nc)
        if scolor == "yellow" or (bool(trow.get("is_inactive", False)) and not is_sp and scolor != "free"):
            buckets["inactive_count"][(tg, ts_i)].add(nc)
        if scolor == "free":
            buckets["free_count"][(tg, ts_i)].add(nc)
            uf, cmax = coerce_int(trow.get("uninhabited_fields"), 0), coerce_int(trow.get("colonize_max_fields"), 0)
            if uf >= 300 or cmax >= 300 or (uf == 0 and cmax == 0 and 7 <= tp <= 12):
                buckets["prime_colony_free_count"][(tg, ts_i)].add(nc)
    for (tg, ts_i), omap in per_sec_owner.items():
        sec = _ensure_sector_entry(sectors_data, tg, ts_i)
        for okey, cset in omap.items():
            sec["owner_planet_counts"][okey] = max(sec["owner_planet_counts"].get(okey, 0), len(cset))
    for bkey, bmap in buckets.items():
        for (tg, ts_i), cset in bmap.items():
            sec = _ensure_sector_entry(sectors_data, tg, ts_i)
            sec[bkey] = max(sec[bkey], len(cset))
    for sec in sectors_data.values():
        sec["target_count"] = max(sec["target_count"], sec["inactive_count"] + sec["spacia_count"])
    return coord_owner, coord_spacia


def _record_owner_raid(
    owner_stats: dict[str, dict[str, Any]],
    coord_owner: Mapping[str, str],
    coord_spacia: set[str],
    nc: str,
    raids: int,
    decay: float,
    loot_sp: float,
) -> None:
    owner_disp = coord_owner.get(nc, "")
    if owner_disp and nc not in coord_spacia and owner_disp.lower() != "spacia":
        os_entry = owner_stats.setdefault(
            owner_disp.lower(), {"owner": owner_disp, "raid_count": 0, "recency_raid_score": 0.0, "loot_spice_eq": 0.0}
        )
        os_entry["raid_count"] += raids
        os_entry["recency_raid_score"] += decay
        os_entry["loot_spice_eq"] += loot_sp


def _ingest_raid_history_fallback(
    raid_stats: Optional[Mapping[str, Any]],
    raid_history: Optional[Sequence[Mapping[str, Any]]],
    own_norm_coords: set[str],
    coord_owner: Mapping[str, str],
    coord_spacia: set[str],
    sectors_data: dict[str, dict[str, Any]],
    owner_stats: dict[str, dict[str, Any]],
    n_sys: int,
    eff_now: float,
) -> None:
    raw_hist_rows = [r for r in (raid_history or ()) if isinstance(r, Mapping)] if isinstance(raid_history, Sequence) and not isinstance(raid_history, (str, bytes, bytearray)) else []
    if not raw_hist_rows and isinstance(raid_stats, Mapping) and isinstance(rs_hist := raid_stats.get("history"), Sequence) and not isinstance(rs_hist, (str, bytes, bytearray)):
        raw_hist_rows = [r for r in rs_hist if isinstance(r, Mapping)]
    if raw_hist_rows:
        for r in raw_hist_rows:
            nc = normalize_coords(r.get("target_coords") or r.get("coords") or "")
            if not nc or nc in own_norm_coords or (pair := parse_gal_sys_pair(nc)) is None or not (1 <= pair[1] <= n_sys):
                continue
            rts = safe_finite_float(r.get("attacked_ts") if r.get("attacked_ts") is not None else (r.get("ts") if r.get("ts") is not None else r.get("timestamp")), eff_now)
            loot_sp = safe_finite_float(r.get("loot_spice_eq"), 0.0)
            if loot_sp <= 0.0 and isinstance(lmap := r.get("loot"), Mapping):
                loot_sp = float(spice_equivalent(Resources(*(max(0, coerce_int(lmap.get(k), 0)) for k in ("pig_iron", "metal", "kryptonite", "spice")))))
            decay = 0.25 + 0.75 * math.exp(-max(0.0, eff_now - rts) / (7.0 * 86400.0))
            sec = _ensure_sector_entry(sectors_data, pair[0], pair[1])
            sec["direct_raid_count"] += 1
            sec["direct_recency_raid_score"] += decay
            sec["direct_loot_spice_eq"] += max(0.0, loot_sp)
            sec["last_attacked_ts"] = max(sec["last_attacked_ts"], rts)
            _record_owner_raid(owner_stats, coord_owner, coord_spacia, nc, 1, decay, max(0.0, loot_sp))
    elif isinstance(raid_stats, Mapping) and isinstance(per_tgt := raid_stats.get("per_target"), Mapping):
        for tc_raw, pt in per_tgt.items():
            if not isinstance(pt, Mapping):
                continue
            nc = normalize_coords(tc_raw)
            pair = parse_gal_sys_pair(nc)
            attempts = max(0, coerce_int(pt.get("attempts"), 0))
            if pair is None or not (1 <= pair[1] <= n_sys) or attempts <= 0:
                continue
            loot_sp = max(0.0, safe_finite_float(pt.get("total_loot_spice_eq"), 0.0))
            sec = _ensure_sector_entry(sectors_data, pair[0], pair[1])
            sec["direct_raid_count"] += attempts
            sec["direct_recency_raid_score"] += float(attempts) * 0.75
            sec["direct_loot_spice_eq"] += loot_sp
            _record_owner_raid(owner_stats, coord_owner, coord_spacia, nc, attempts, float(attempts) * 0.75, loot_sp)


def _sector_distance(sys_num: int, same_gal_cols: tuple[int, ...], other_gal_cols: tuple[tuple[int, int], ...], n_sys: int) -> int:
    best_d = 10**9
    for cs in same_gal_cols:
        delta = abs(sys_num - cs)
        best_d = min(best_d, delta, n_sys - delta)
    for g_pen, cs in other_gal_cols:
        delta = abs(sys_num - cs)
        best_d = min(best_d, g_pen + min(delta, n_sys - delta))
    return best_d if best_d < 10**9 else 0


def _score_scanned_sector(
    skey: str, gal: int, sys_num: int, dist: int, same_gal: bool, ring_offset: int,
    sec: dict[str, Any], owner_stats: Mapping[str, Mapping[str, Any]],
    near_score: float, eff_near_w: float, eff_attack_w: float, eff_fresh_s: float,
    explore_first: bool, collect_colonies: bool, eff_now: float,
) -> dict[str, Any]:
    last_ts = safe_finite_float(sec["last_scanned_ts"], 0.0)
    scanned = last_ts > 0.0
    age_s = round(max(0.0, eff_now - last_ts), 3) if scanned else None
    is_fresh = bool(scanned and age_s is not None and age_s < eff_fresh_s)
    direct_raids, direct_recency, direct_loot = int(sec["direct_raid_count"]), float(sec["direct_recency_raid_score"]), float(sec["direct_loot_spice_eq"])
    debris_cnt, inact_cnt, spacia_cnt = int(sec["debris_count"]), int(sec["inactive_count"]), int(sec["spacia_count"])
    target_cnt, free_cnt, prime_free = max(int(sec["target_count"]), inact_cnt + spacia_cnt), int(sec["free_count"]), int(sec["prime_colony_free_count"])
    affinity_sig, owner_aff_raids, freq_owners = 0.0, 0, []
    if sec["owner_planet_counts"]:
        entries = sorted(
            ((int(os_i["raid_count"]), float(os_i["loot_spice_eq"]), str(os_i["owner"] or sec["owner_display"].get(ok, ok)), int(pcnt))
             for ok, pcnt in sec["owner_planet_counts"].items() if (os_i := owner_stats.get(ok)) is not None and int(os_i["raid_count"]) > 0),
            key=lambda t: (-t[0], -t[1], t[2]),
        )
        for o_raids, o_loot, o_disp, pcnt in entries:
            owner_aff_raids += o_raids
            affinity_sig += (math.log1p(float(o_raids)) + 0.25 * math.log1p(max(0.0, o_loot) / 10000.0)) * (1.0 + 0.35 * max(0, pcnt - 1))
            freq_owners.append(f"{o_disp}({o_raids})")
    attack_score = eff_attack_w * (1.6 * math.log1p(max(0.0, max(direct_recency, 0.6 * direct_raids))) + 0.35 * math.log1p(max(0.0, direct_loot) / 5000.0) + 1.4 * affinity_sig)
    secondary_score = min(3.0, 1.25 * debris_cnt) + min(2.5, 0.55 * inact_cnt + 0.75 * spacia_cnt) + (min(1.8, 0.45 * prime_free + 0.10 * min(6, free_cnt)) if collect_colonies else 0.0)
    importance = 1.0 + near_score + attack_score + secondary_score
    staleness_factor = max(60.0, age_s if (scanned and age_s is not None) else (7.0 * 86400.0)) / 1800.0
    phase = "exploratory" if (explore_first and not scanned) else "targeted_refresh"
    if phase == "exploratory":
        reason = f"unscanned sector (dist {dist})"
    else:
        tags = ["near d=" + str(dist) if (same_gal and dist <= 25 and eff_near_w > 0.0) else (f"d={dist}" if same_gal else f"cross-gal d={dist}")]
        if direct_raids > 0:
            tags.append(f"raided {direct_raids}x")
        if freq_owners:
            tags.append("freq-target " + ",".join(freq_owners[:2]))
        if debris_cnt > 0:
            tags.append(f"debris x{debris_cnt}")
        elif target_cnt > 0:
            tags.append(f"targets x{target_cnt}")
        elif collect_colonies and prime_free > 0:
            tags.append(f"colony-slots x{prime_free}")
        tags.append(f"age {format_age_short(age_s)}")
        reason = ", ".join(tags)
    return {
        "sector": skey, "galaxy": gal, "system": sys_num, "phase": phase, "scanned": scanned, "is_fresh": is_fresh,
        "score": round(importance * staleness_factor, 4), "importance": round(importance, 4),
        "near_score": round(near_score, 4), "attack_score": round(attack_score, 4), "secondary_score": round(secondary_score, 4),
        "staleness_factor": round(staleness_factor, 4), "distance": int(dist), "last_scanned_ts": round(last_ts, 3),
        "age_seconds": age_s, "direct_raid_count": direct_raids, "direct_loot_spice_eq": round(direct_loot, 2),
        "owner_affinity_raid_count": owner_aff_raids, "frequent_owners": freq_owners, "debris_count": debris_cnt,
        "target_count": target_cnt, "free_count": free_cnt, "reason": reason,
        "_same_gal": 0 if same_gal else 1, "_ring_offset": ring_offset,
    }


def score_galaxy_scan_sectors(
    *,
    own_coords: Sequence[str] | str = (),
    sector_intel: Optional[Mapping[str, Any]] = None,
    galaxies: Optional[Sequence[int]] = None,
    scanned_systems: Optional[Mapping[Any, Any] | Iterable[Any]] = None,
    raid_targets: Optional[Sequence[Mapping[str, Any]]] = None,
    raid_stats: Optional[Mapping[str, Any]] = None,
    raid_history: Optional[Sequence[Mapping[str, Any]]] = None,
    explore_first: bool = True,
    near_weight: float = 2.0,
    attack_weight: float = 3.0,
    collect_colonies: bool = True,
    min_freshness_seconds: float = 120.0,
    max_radius: Optional[int] = None,
    systems_per_galaxy: int = 400,
    now_ts: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Score and rank galaxy solar systems ``(1..400)`` for 2-phase smart scanning."""
    eff_now = safe_finite_float(now_ts, 0.0) or (safe_finite_float(sector_intel.get("now_ts"), 0.0) if isinstance(sector_intel, Mapping) else 0.0) or time.time()
    n_sys = max(1, coerce_int(systems_per_galaxy, 400))
    eff_near_w = max(0.0, min(20.0, safe_finite_float(near_weight, 2.0)))
    eff_attack_w = max(0.0, min(20.0, safe_finite_float(attack_weight, 3.0)))
    eff_fresh_s = max(0.0, safe_finite_float(min_freshness_seconds, 120.0))
    rad_cap = coerce_int(max_radius, -1) if max_radius is not None else -1
    own_colonies, own_norm_coords = extract_own_colonies_and_coords(own_coords, systems_per_galaxy=n_sys)
    active_galaxies = _resolve_active_galaxies(galaxies, own_colonies, sector_intel)
    sectors_data: dict[str, dict[str, Any]] = {}
    owner_stats: dict[str, dict[str, Any]] = {}
    if isinstance(sector_intel, Mapping):
        _ingest_sector_intel_mapping(sector_intel, sectors_data, owner_stats, n_sys)
    if scanned_systems is not None:
        _ingest_scanned_systems(scanned_systems, sectors_data, n_sys, eff_now)
    coord_owner, coord_spacia = _ingest_raid_targets(raid_targets, own_norm_coords, sectors_data, n_sys)
    if sector_intel is None:
        _ingest_raid_history_fallback(raid_stats, raid_history, own_norm_coords, coord_owner, coord_spacia, sectors_data, owner_stats, n_sys, eff_now)
    eff_colonies = own_colonies or [(active_galaxies[0], 1)]
    colony_gal_set = {cg for cg, _ in eff_colonies}
    primary_sys_by_gal: dict[int, int] = {}
    for cg, cs in eff_colonies:
        primary_sys_by_gal.setdefault(cg, cs)
    scored: list[dict[str, Any]] = []
    near_scale, default_staleness = eff_near_w * 2.5, (7.0 * 86400.0) / 1800.0
    col_int = [(int(cg), int(cs)) for cg, cs in eff_colonies]
    for gal in active_galaxies:
        anchor_sys, same_gal = primary_sys_by_gal.get(gal, eff_colonies[0][1]), gal in colony_gal_set
        same_gal_cols = tuple(cs for cg, cs in col_int if cg == gal)
        other_gal_cols = tuple((250 * abs(gal - cg), cs) for cg, cs in col_int if cg != gal)
        for sys_num in range(1, n_sys + 1):
            dist = _sector_distance(sys_num, same_gal_cols, other_gal_cols, n_sys)
            if rad_cap >= 0 and dist > rad_cap:
                continue
            skey = f"{gal:02d}:{sys_num:03d}"
            near_score = near_scale * ((1.0 / (1.0 + dist / 20.0)) if same_gal else (0.15 / (1.0 + dist / 200.0)))
            ring_offset = (sys_num - anchor_sys) % n_sys
            if (sec := sectors_data.get(skey)) is None:
                importance = 1.0 + near_score
                reason = f"unscanned sector (dist {dist})" if explore_first else (f"near d={dist}, age never" if same_gal and dist <= 25 and eff_near_w > 0.0 else (f"d={dist}, age never" if same_gal else f"cross-gal d={dist}, age never"))
                scored.append({
                    "sector": skey, "galaxy": gal, "system": sys_num, "phase": "exploratory" if explore_first else "targeted_refresh",
                    "scanned": False, "is_fresh": False, "score": round(importance * default_staleness, 4), "importance": round(importance, 4),
                    "near_score": round(near_score, 4), "attack_score": 0.0, "secondary_score": 0.0, "staleness_factor": round(default_staleness, 4),
                    "distance": dist, "last_scanned_ts": 0.0, "age_seconds": None, "direct_raid_count": 0, "direct_loot_spice_eq": 0.0,
                    "owner_affinity_raid_count": 0, "frequent_owners": [], "debris_count": 0, "target_count": 0, "free_count": 0,
                    "reason": reason, "_same_gal": 0 if same_gal else 1, "_ring_offset": ring_offset,
                })
            else:
                scored.append(_score_scanned_sector(skey, gal, sys_num, dist, same_gal, ring_offset, sec, owner_stats, near_score, eff_near_w, eff_attack_w, eff_fresh_s, explore_first, collect_colonies, eff_now))
    scored.sort(
        key=(lambda it: (0 if not it["scanned"] else (1 if not it["is_fresh"] else 2), it["_same_gal"] if not it["scanned"] else 0, it["distance"] if not it["scanned"] else -it["score"], it["_ring_offset"] if not it["scanned"] else -it["importance"], it["distance"], it["galaxy"], it["system"]))
        if explore_first else
        (lambda it: (0 if not it["is_fresh"] else 1, -it["score"], -it["importance"], it["distance"], it["galaxy"], it["system"]))
    )
    for it in scored:
        it.pop("_same_gal", None)
        it.pop("_ring_offset", None)
    return scored
