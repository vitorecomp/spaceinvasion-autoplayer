"""Galaxy sector intel, scanned-system, and raid-history ingestion helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
import math
import re
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


def resolve_active_galaxies(
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


def ensure_sector_entry(sectors_data: dict[str, dict[str, Any]], gal: int, sys_num: int) -> dict[str, Any]:
    skey = f"{gal:02d}:{sys_num:03d}"
    entry = sectors_data.get(skey)
    if entry is None:
        entry = {
            "galaxy": gal,
            "system": sys_num,
            "last_scanned_ts": 0.0,
            "scan_count": 0,
            "occupied_count": 0,
            "target_count": 0,
            "inactive_count": 0,
            "spacia_count": 0,
            "debris_count": 0,
            "free_count": 0,
            "prime_colony_free_count": 0,
            "direct_raid_count": 0,
            "direct_recency_raid_score": 0.0,
            "direct_loot_spice_eq": 0.0,
            "last_attacked_ts": 0.0,
            "owner_planet_counts": {},
            "owner_display": {},
        }
        sectors_data[skey] = entry
    return entry


def ingest_sector_intel_mapping(
    sector_intel: Mapping[str, Any],
    sectors_data: dict[str, dict[str, Any]],
    owner_stats: dict[str, dict[str, Any]],
    n_sys: int,
) -> None:
    raw_owner_stats = sector_intel.get("owner_raid_stats")
    if isinstance(raw_owner_stats, Mapping):
        for ok_raw, ov in raw_owner_stats.items():
            if (
                isinstance(ov, Mapping)
                and (disp := safe_key(ov.get("owner") or ok_raw).strip())
                and disp.lower() != "spacia"
            ):
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
                cur_os = owner_stats.setdefault(
                    disp.lower(),
                    {"owner": disp, "raid_count": 0, "recency_raid_score": float(cnt), "loot_spice_eq": 0.0},
                )
                cur_os["raid_count"] = max(int(cur_os["raid_count"]), cnt)
    raw_sectors = sector_intel.get("sectors")
    if not isinstance(raw_sectors, Mapping):
        return
    for skey_raw, sv in raw_sectors.items():
        if not isinstance(sv, Mapping):
            continue
        pair = parse_gal_sys_pair(skey_raw)
        gal, sys_num = (
            coerce_int(sv.get("galaxy"), pair[0] if pair else 0),
            coerce_int(sv.get("system"), pair[1] if pair else 0),
        )
        if not (1 <= gal <= 14 and 1 <= sys_num <= n_sys):
            continue
        sec = ensure_sector_entry(sectors_data, gal, sys_num)
        for fk in ("last_scanned_ts", "direct_recency_raid_score", "direct_loot_spice_eq", "last_attacked_ts"):
            sec[fk] = max(sec[fk], safe_finite_float(sv.get(fk), 0.0))
        for ik in (
            "scan_count",
            "occupied_count",
            "target_count",
            "inactive_count",
            "spacia_count",
            "debris_count",
            "free_count",
            "prime_colony_free_count",
            "direct_raid_count",
        ):
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


def ingest_scanned_systems(
    scanned_systems: Optional[Mapping[Any, Any] | Iterable[Any]],
    sectors_data: dict[str, dict[str, Any]],
    n_sys: int,
    eff_now: float,
) -> None:
    if isinstance(scanned_systems, Mapping):
        for raw_k, raw_ts in scanned_systems.items():
            pair = parse_gal_sys_pair(raw_k)
            if pair is not None and 1 <= pair[1] <= n_sys and (ts_val := safe_finite_float(raw_ts, 0.0)) > 0.0:
                sec = ensure_sector_entry(sectors_data, pair[0], pair[1])
                sec["last_scanned_ts"] = max(sec["last_scanned_ts"], ts_val)
                sec["scan_count"] = max(sec["scan_count"], 1)
    elif isinstance(scanned_systems, Iterable) and not isinstance(scanned_systems, (str, bytes, bytearray)):
        fallback_ts = max(1.0, eff_now - 3600.0)
        for raw_k in scanned_systems:
            pair = parse_gal_sys_pair(raw_k)
            if pair is not None and 1 <= pair[1] <= n_sys:
                sec = ensure_sector_entry(sectors_data, pair[0], pair[1])
                if sec["last_scanned_ts"] <= 0.0:
                    sec["last_scanned_ts"] = fallback_ts
                sec["scan_count"] = max(sec["scan_count"], 1)


def ingest_raid_targets(
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
        k: defaultdict(set)
        for k in ("debris_count", "inactive_count", "spacia_count", "free_count", "prime_colony_free_count")
    }
    for trow in raid_targets:
        if (
            not isinstance(trow, Mapping)
            or bool(trow.get("is_own_planet", False))
            or str(trow.get("merged_into") or "").strip()
        ):
            continue
        nc = normalize_coords(trow.get("coords") or trow.get("target_coords") or "")
        if not nc or nc in own_norm_coords or not (m_c := re.fullmatch(r"(\d{2}):(\d{3}):(\d{2})", nc, re.ASCII)):
            continue
        tg, ts_i, tp = int(m_c.group(1)), int(m_c.group(2)), int(m_c.group(3))
        if not (1 <= tg <= 14 and 1 <= ts_i <= n_sys):
            continue
        sec = ensure_sector_entry(sectors_data, tg, ts_i)
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
        sec = ensure_sector_entry(sectors_data, tg, ts_i)
        for okey, cset in omap.items():
            sec["owner_planet_counts"][okey] = max(sec["owner_planet_counts"].get(okey, 0), len(cset))
    for bkey, bmap in buckets.items():
        for (tg, ts_i), cset in bmap.items():
            sec = ensure_sector_entry(sectors_data, tg, ts_i)
            sec[bkey] = max(sec[bkey], len(cset))
    for sec in sectors_data.values():
        sec["target_count"] = max(sec["target_count"], sec["inactive_count"] + sec["spacia_count"])
    return coord_owner, coord_spacia


def record_owner_raid(
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


def ingest_raid_history_fallback(
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
    raw_hist_rows = (
        [r for r in (raid_history or ()) if isinstance(r, Mapping)]
        if isinstance(raid_history, Sequence) and not isinstance(raid_history, (str, bytes, bytearray))
        else []
    )
    if (
        not raw_hist_rows
        and isinstance(raid_stats, Mapping)
        and isinstance(rs_hist := raid_stats.get("history"), Sequence)
        and not isinstance(rs_hist, (str, bytes, bytearray))
    ):
        raw_hist_rows = [r for r in rs_hist if isinstance(r, Mapping)]
    if raw_hist_rows:
        for r in raw_hist_rows:
            nc = normalize_coords(r.get("target_coords") or r.get("coords") or "")
            if (
                not nc
                or nc in own_norm_coords
                or (pair := parse_gal_sys_pair(nc)) is None
                or not (1 <= pair[1] <= n_sys)
            ):
                continue
            rts = safe_finite_float(
                r.get("attacked_ts")
                if r.get("attacked_ts") is not None
                else (r.get("ts") if r.get("ts") is not None else r.get("timestamp")),
                eff_now,
            )
            loot_sp = safe_finite_float(r.get("loot_spice_eq"), 0.0)
            if loot_sp <= 0.0 and isinstance(lmap := r.get("loot"), Mapping):
                loot_sp = float(
                    spice_equivalent(
                        Resources(
                            *(max(0, coerce_int(lmap.get(k), 0)) for k in ("pig_iron", "metal", "kryptonite", "spice"))
                        )
                    )
                )
            decay = 0.25 + 0.75 * math.exp(-max(0.0, eff_now - rts) / (7.0 * 86400.0))
            sec = ensure_sector_entry(sectors_data, pair[0], pair[1])
            sec["direct_raid_count"] += 1
            sec["direct_recency_raid_score"] += decay
            sec["direct_loot_spice_eq"] += max(0.0, loot_sp)
            sec["last_attacked_ts"] = max(sec["last_attacked_ts"], rts)
            record_owner_raid(owner_stats, coord_owner, coord_spacia, nc, 1, decay, max(0.0, loot_sp))
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
            sec = ensure_sector_entry(sectors_data, pair[0], pair[1])
            sec["direct_raid_count"] += attempts
            sec["direct_recency_raid_score"] += float(attempts) * 0.75
            sec["direct_loot_spice_eq"] += loot_sp
            record_owner_raid(owner_stats, coord_owner, coord_spacia, nc, attempts, float(attempts) * 0.75, loot_sp)
