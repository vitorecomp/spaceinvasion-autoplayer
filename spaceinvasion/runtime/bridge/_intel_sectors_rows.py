"""Scanned-sectors queue, per-galaxy aggregation, and sortable sector row builders."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

from spaceinvasion.combat.fleet import normalize_coords
from spaceinvasion.core.state import GameState
from spaceinvasion.runtime.bridge._helpers import safe_api_float, safe_api_int

_ORIGIN_FULL_RE = re.compile(r"(\d+):(\d+):(\d+)", re.ASCII)


def resolve_origin_sector(state_snap: GameState | None, req_pid: int | None) -> tuple[str, tuple[int, int] | None]:
    """Resolve normalized origin coordinates and `(galaxy, system)` pair for distance metrics."""
    origin_coords = ""
    if state_snap is not None:
        orig_p = next((p for p in state_snap.planets if p.planet_id == req_pid), None) if req_pid is not None else None
        if orig_p is None:
            orig_p = state_snap.active_planet()
        if orig_p is None and state_snap.planets:
            orig_p = state_snap.planets[0]
        if orig_p is not None and orig_p.coords:
            origin_coords = normalize_coords(orig_p.coords)
    match = _ORIGIN_FULL_RE.fullmatch(origin_coords) if origin_coords else None
    orig_gs = (int(match.group(1)), int(match.group(2))) if match else None
    return origin_coords, orig_gs


def calc_sector_dist(
    orig_gs: tuple[int, int] | None,
    gal_i: int,
    sys_i: int,
    fallback_dist: object = None,
) -> tuple[int | None, int | None, int | None, int | None]:
    """Return `(galaxy_delta, system_delta, system_distance, circular_distance)`."""
    if orig_gs is not None:
        gd = abs(orig_gs[0] - gal_i)
        sd = abs(orig_gs[1] - sys_i)
        sdist = int(gd * 400 + sd)
        cdist = int(min(sd, 400 - sd)) if gd == 0 else sdist
        return gd, sd, sdist, cdist
    fb = safe_api_int(fallback_dist, -1) if fallback_dist is not None else -1
    sdist_fb = fb if fb >= 0 else None
    return None, None, sdist_fb, sdist_fb


def extract_freq_owners(raw_freq: object) -> list[str]:
    """Format up to 3 frequent raid owners for a sector row."""
    out_names: list[str] = []
    if isinstance(raw_freq, Sequence) and not isinstance(raw_freq, (str, bytes)):
        for fo in raw_freq[:3]:
            if isinstance(fo, Mapping) and fo.get("owner"):
                out_names.append(f"{fo['owner']} ({safe_api_int(fo.get('raid_count'), 0)})")
            elif isinstance(fo, str) and fo.strip():
                out_names.append(fo.strip())
    return out_names


def build_scan_queue(
    smart_plan: Mapping[str, object] | None,
    sectors_map: Mapping[str, object],
    all_galaxies: Sequence[int],
    *,
    max_g: int,
    now_ts: float,
    orig_gs: tuple[int, int] | None,
) -> tuple[list[dict[str, object]], dict[str, dict[str, object]], dict[int, int]]:
    """Build ordered `scan_queue` list, lookup map, and per-galaxy queued counts."""
    scan_queue: list[dict[str, object]] = []
    queue_by_sector: dict[str, dict[str, object]] = {}
    queued_count_by_gal: dict[int, int] = {g: 0 for g in all_galaxies}
    raw_sel = smart_plan.get("selected_sectors") if isinstance(smart_plan, Mapping) else None
    selected = raw_sel if isinstance(raw_sel, Sequence) and not isinstance(raw_sel, (str, bytes)) else ()
    for q_idx, q_item in enumerate(selected, start=1):
        if not isinstance(q_item, Mapping):
            continue
        qg = safe_api_int(q_item.get("galaxy"), 0)
        qs = safe_api_int(q_item.get("system"), 0)
        if not (1 <= qg <= max_g and 1 <= qs <= 400):
            continue
        q_key = f"{qg:02d}:{qs:03d}"
        q_sec_raw = sectors_map.get(q_key)
        q_sec_map: Mapping[str, object] = q_sec_raw if isinstance(q_sec_raw, Mapping) else {}
        q_scanned = bool(q_item.get("scanned", False)) or safe_api_float(q_sec_map.get("last_scanned_ts"), 0.0) > 0.0
        q_last_ts = max(
            safe_api_float(q_item.get("last_scanned_ts"), 0.0),
            safe_api_float(q_sec_map.get("last_scanned_ts"), 0.0),
        )
        q_age_s: float | None = round(max(0.0, now_ts - q_last_ts), 1) if q_scanned and q_last_ts > 0.0 else None
        q_gd, q_sd, q_sdist, q_cdist = calc_sector_dist(orig_gs, qg, qs, q_item.get("distance"))
        q_row: dict[str, object] = {
            "queue_pos": q_idx,
            "sector": q_key,
            "galaxy": qg,
            "system": qs,
            "phase": str(q_item.get("phase") or ("exploratory" if not q_scanned else "targeted_refresh")),
            "scanned": q_scanned,
            "last_scanned_ts": round(q_last_ts, 3),
            "age_seconds": q_age_s,
            "scan_count": max(1 if q_scanned else 0, safe_api_int(q_sec_map.get("scan_count"), 0)),
            "occupied_count": max(0, safe_api_int(q_sec_map.get("occupied_count"), 0)),
            "debris_count": max(
                safe_api_int(q_item.get("debris_count"), 0), safe_api_int(q_sec_map.get("debris_count"), 0)
            ),
            "target_count": max(
                safe_api_int(q_item.get("target_count"), 0), safe_api_int(q_sec_map.get("target_count"), 0)
            ),
            "free_count": max(safe_api_int(q_item.get("free_count"), 0), safe_api_int(q_sec_map.get("free_count"), 0)),
            "prime_colony_free_count": max(0, safe_api_int(q_sec_map.get("prime_colony_free_count"), 0)),
            "direct_raid_count": max(
                safe_api_int(q_item.get("direct_raid_count"), 0), safe_api_int(q_sec_map.get("direct_raid_count"), 0)
            ),
            "owner_affinity_raid_count": max(
                safe_api_int(q_item.get("owner_affinity_raid_count"), 0),
                safe_api_int(q_sec_map.get("owner_affinity_raid_count"), 0),
            ),
            "frequent_owners": extract_freq_owners(q_item.get("frequent_owners") or q_sec_map.get("frequent_owners")),
            "system_distance": q_sdist,
            "circular_distance": q_cdist,
            "galaxy_delta": q_gd,
            "system_delta": q_sd,
            "score": round(safe_api_float(q_item.get("score"), 0.0), 2),
            "importance": round(safe_api_float(q_item.get("importance"), 0.0), 2),
            "reason": str(q_item.get("reason") or ""),
        }
        scan_queue.append(q_row)
        queue_by_sector[q_key] = q_row
        queued_count_by_gal[qg] = queued_count_by_gal.get(qg, 0) + 1
    return scan_queue, queue_by_sector, queued_count_by_gal


def aggregate_galaxy_stats(
    sectors_map: Mapping[str, object],
    all_galaxies: Sequence[int],
    owned_gal_set: set[int],
    queue_gal_set: set[int],
    queued_count_by_gal: Mapping[int, int],
    *,
    max_g: int,
) -> tuple[list[dict[str, object]], set[int], dict[str, Mapping[str, object]]]:
    """Aggregate per-galaxy coverage, debris, targets, and prime-colony counts."""
    gal_agg: dict[int, dict[str, int]] = {
        g: {"scanned_count": 0, "occupied_count": 0, "debris_count": 0, "target_count": 0, "prime_free_count": 0}
        for g in all_galaxies
    }
    scanned_gals: set[int] = set()
    scanned_by_key: dict[str, Mapping[str, object]] = {}
    for sec_v in sectors_map.values():
        if not isinstance(sec_v, Mapping):
            continue
        if safe_api_float(sec_v.get("last_scanned_ts"), 0.0) <= 0.0 and not bool(sec_v.get("scanned")):
            continue
        gi = safe_api_int(sec_v.get("galaxy"), 0)
        si = safe_api_int(sec_v.get("system"), 0)
        if not (1 <= gi <= max_g and 1 <= si <= 400):
            continue
        scanned_by_key[f"{gi:02d}:{si:03d}"] = sec_v
        scanned_gals.add(gi)
        agg = gal_agg[gi]
        agg["scanned_count"] += 1
        agg["occupied_count"] += max(0, safe_api_int(sec_v.get("occupied_count"), 0))
        agg["debris_count"] += max(0, safe_api_int(sec_v.get("debris_count"), 0))
        inact_v = max(0, safe_api_int(sec_v.get("inactive_count"), 0))
        spacia_v = max(0, safe_api_int(sec_v.get("spacia_count"), 0))
        agg["target_count"] += max(0, safe_api_int(sec_v.get("target_count"), inact_v + spacia_v))
        agg["prime_free_count"] += max(0, safe_api_int(sec_v.get("prime_colony_free_count"), 0))

    galaxy_stats: list[dict[str, object]] = []
    for g in all_galaxies:
        agg = gal_agg[g]
        sc_cnt = agg["scanned_count"]
        galaxy_stats.append(
            {
                "galaxy": g,
                "is_owned": g in owned_gal_set,
                "in_sweep_scope": g in queue_gal_set,
                "scanned_count": sc_cnt,
                "unscanned_count": max(0, 400 - sc_cnt),
                "total_systems": 400,
                "coverage_pct": round((sc_cnt / 400.0) * 100.0, 2),
                "occupied_count": agg["occupied_count"],
                "debris_count": agg["debris_count"],
                "target_count": agg["target_count"],
                "prime_free_count": agg["prime_free_count"],
                "queued_count": queued_count_by_gal.get(g, 0),
            }
        )
    return galaxy_stats, scanned_gals, scanned_by_key


def _build_sector_row(
    gal_i: int,
    sys_i: int,
    sec_raw: Mapping[str, object] | None,
    *,
    now_ts: float,
    orig_gs: tuple[int, int] | None,
    score_by_sector: Mapping[str, Mapping[str, object]],
    queue_by_sector: Mapping[str, dict[str, object]],
) -> dict[str, object]:
    skey_row = f"{gal_i:02d}:{sys_i:03d}"
    sc_info = score_by_sector.get(skey_row, {})
    q_info = queue_by_sector.get(skey_row)
    is_scanned = sec_raw is not None
    last_ts = safe_api_float(sec_raw.get("last_scanned_ts"), 0.0) if sec_raw else 0.0
    scan_cnt = max(0, safe_api_int(sec_raw.get("scan_count"), 0)) if sec_raw else 0
    age_s: float | None = (
        (round(max(0.0, now_ts - last_ts), 1) if last_ts > 0.0 else safe_api_float(sec_raw.get("age_seconds"), 0.0))
        if is_scanned and sec_raw is not None
        else None
    )
    occ_cnt = max(0, safe_api_int(sec_raw.get("occupied_count"), 0)) if sec_raw else 0
    inact_cnt = max(0, safe_api_int(sec_raw.get("inactive_count"), 0)) if sec_raw else 0
    spacia_cnt = max(0, safe_api_int(sec_raw.get("spacia_count"), 0)) if sec_raw else 0
    g_diff, s_diff, sys_dist, circ_dist = calc_sector_dist(
        orig_gs, gal_i, sys_i, sc_info.get("distance") if sc_info else None
    )
    reason_txt = (
        str(q_info.get("reason") or "")
        if q_info and not is_scanned
        else str(sc_info.get("reason") or ("unscanned sector" if not is_scanned else ""))
    )
    return {
        "sector": skey_row,
        "galaxy": gal_i,
        "system": sys_i,
        "scanned": is_scanned,
        "queue_pos": safe_api_int(q_info.get("queue_pos"), 0) if q_info else None,
        "queue_phase": str(q_info.get("phase") or "") if q_info else None,
        "occupied_count": occ_cnt,
        "max_slots": 16,
        "active_occupied_count": max(0, occ_cnt - inact_cnt - spacia_cnt),
        "free_count": max(0, safe_api_int(sec_raw.get("free_count"), 0)) if sec_raw else 0,
        "prime_colony_free_count": max(0, safe_api_int(sec_raw.get("prime_colony_free_count"), 0)) if sec_raw else 0,
        "debris_count": max(0, safe_api_int(sec_raw.get("debris_count"), 0)) if sec_raw else 0,
        "target_count": max(0, safe_api_int(sec_raw.get("target_count"), inact_cnt + spacia_cnt)) if sec_raw else 0,
        "inactive_count": inact_cnt,
        "spacia_count": spacia_cnt,
        "scan_count": max(1, scan_cnt) if is_scanned else 0,
        "last_scanned_ts": round(last_ts, 3),
        "age_seconds": age_s,
        "system_distance": sys_dist,
        "circular_distance": circ_dist,
        "galaxy_delta": g_diff,
        "system_delta": s_diff,
        "direct_raid_count": max(0, safe_api_int(sec_raw.get("direct_raid_count"), 0)) if sec_raw else 0,
        "direct_raid_24h_count": max(0, safe_api_int(sec_raw.get("direct_raid_24h_count"), 0)) if sec_raw else 0,
        "direct_loot_spice_eq": (
            round(max(0.0, safe_api_float(sec_raw.get("direct_loot_spice_eq"), 0.0)), 1) if sec_raw else 0.0
        ),
        "owner_affinity_raid_count": (
            max(0, safe_api_int(sec_raw.get("owner_affinity_raid_count"), 0)) if sec_raw else 0
        ),
        "frequent_owners": extract_freq_owners(sec_raw.get("frequent_owners")) if sec_raw else [],
        "score": round(safe_api_float(sc_info.get("score"), 0.0), 2),
        "importance": round(safe_api_float(sc_info.get("importance"), 0.0), 2),
        "reason": reason_txt,
    }


def collect_and_sort_sector_rows(
    scanned_by_key: Mapping[str, Mapping[str, object]],
    all_galaxies: Sequence[int],
    *,
    gal_filter: int | None,
    include_unscanned: bool,
    limit: int,
    max_g: int,
    now_ts: float,
    orig_gs: tuple[int, int] | None,
    score_by_sector: Mapping[str, Mapping[str, object]],
    queue_by_sector: Mapping[str, dict[str, object]],
) -> list[dict[str, object]]:
    """Collect scanned (and optionally unscanned) sector rows and sort in canonical UI order."""
    rows_out: list[dict[str, object]] = []
    if gal_filter and gal_filter > 0:
        for sys_i in range(1, 401):
            sec_m = scanned_by_key.get(f"{gal_filter:02d}:{sys_i:03d}")
            if sec_m is not None or include_unscanned:
                rows_out.append(
                    _build_sector_row(
                        gal_filter,
                        sys_i,
                        sec_m,
                        now_ts=now_ts,
                        orig_gs=orig_gs,
                        score_by_sector=score_by_sector,
                        queue_by_sector=queue_by_sector,
                    )
                )
    else:
        for sec_m in scanned_by_key.values():
            gi = safe_api_int(sec_m.get("galaxy"), 0)
            si = safe_api_int(sec_m.get("system"), 0)
            if 1 <= gi <= max_g and 1 <= si <= 400:
                rows_out.append(
                    _build_sector_row(
                        gi,
                        si,
                        sec_m,
                        now_ts=now_ts,
                        orig_gs=orig_gs,
                        score_by_sector=score_by_sector,
                        queue_by_sector=queue_by_sector,
                    )
                )
        if include_unscanned:
            unscanned_cands: list[tuple[int, int, int, int]] = []
            for g_i in all_galaxies:
                for s_i in range(1, 401):
                    sk_u = f"{g_i:02d}:{s_i:03d}"
                    if sk_u in scanned_by_key:
                        continue
                    q_ent = queue_by_sector.get(sk_u)
                    q_rank = safe_api_int(q_ent.get("queue_pos"), 10**6) if q_ent else 10**6
                    _, _, sdist_u, _ = calc_sector_dist(orig_gs, g_i, s_i, None)
                    unscanned_cands.append((q_rank, sdist_u if sdist_u is not None else 10**9, g_i, s_i))
            unscanned_cands.sort()
            for _, _, g_u, s_u in unscanned_cands[: min(400, max(1, int(limit)))]:
                rows_out.append(
                    _build_sector_row(
                        g_u,
                        s_u,
                        None,
                        now_ts=now_ts,
                        orig_gs=orig_gs,
                        score_by_sector=score_by_sector,
                        queue_by_sector=queue_by_sector,
                    )
                )
    rows_out.sort(
        key=lambda r: (
            0 if r["scanned"] else 1,
            -safe_api_float(r["last_scanned_ts"], 0.0)
            if r["scanned"]
            else (safe_api_int(r["queue_pos"], 10**6) if r["queue_pos"] is not None else 10**6),
            safe_api_int(r["system_distance"], 10**9) if r["system_distance"] is not None else 10**9,
            safe_api_int(r["galaxy"], 0),
            safe_api_int(r["system"], 0),
        )
    )
    return rows_out
