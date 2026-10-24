"""Private section summarizers for `query_full_history_summary`."""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from spaceinvasion.core.catalog import cap_json_int, safe_key
from spaceinvasion.core.model import normalize_planet_type
from spaceinvasion.db.models import ActionHistory, PlanetSnapshot, RankSnapshot, SpyTarget
from spaceinvasion.db.repos._helpers import (
    loads_json_dict,
    sanitize_text,
    spice_equivalent_from_counts,
    to_int,
)


def summarize_spy_targets(session: Session) -> tuple[int, dict[str, object]]:
    """Aggregate spy target counts and known stored resources in Spice equivalents."""
    agg = session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(case((SpyTarget.is_inactive != 0, 1), else_=0)), 0),
            func.coalesce(func.sum(case((SpyTarget.is_spacia != 0, 1), else_=0)), 0),
        )
    ).one()
    spy_count, inact_cnt, spacia_cnt = int(agg[0] or 0), int(agg[1] or 0), int(agg[2] or 0)
    res_rows = session.execute(
        select(
            SpyTarget.last_spied_ts,
            SpyTarget.pig_iron,
            SpyTarget.metal,
            SpyTarget.kryptonite,
            SpyTarget.spice,
        ).where(
            (SpyTarget.last_spied_ts > 0.0)
            | (SpyTarget.pig_iron != 0)
            | (SpyTarget.metal != 0)
            | (SpyTarget.kryptonite != 0)
            | (SpyTarget.spice != 0)
        )
    ).all()
    spied_cnt = 0
    known_sp = 0.0
    for r in res_rows:
        if float(r[0] or 0.0) > 0.0:
            spied_cnt += 1
        known_sp += spice_equivalent_from_counts(
            cap_json_int(int(r[1] or 0)),
            cap_json_int(int(r[2] or 0)),
            cap_json_int(int(r[3] or 0)),
            cap_json_int(int(r[4] or 0)),
        )
    return spy_count, {
        "count": spy_count,
        "inactive_count": inact_cnt,
        "spacia_count": spacia_cnt,
        "spied_count": spied_cnt,
        "total_known_resources_spice_eq": round(known_sp, 3),
    }


def summarize_planet_snapshots(session: Session) -> tuple[int, dict[str, object]]:
    """Summarize first and latest planet snapshot trends via PostgreSQL window functions."""
    p_count = int(session.execute(select(func.count()).select_from(PlanetSnapshot)).scalar_one() or 0)
    first_ts: float | None = None
    latest_ts: float | None = None
    first_by_pid: dict[int, dict[str, object]] = {}
    latest_by_pid: dict[int, dict[str, object]] = {}
    first_res_sp = 0.0
    latest_res_sp = 0.0

    if p_count > 0:
        first_ts_raw = session.execute(
            select(PlanetSnapshot.ts).order_by(PlanetSnapshot.ts.asc(), PlanetSnapshot.id.asc()).limit(1)
        ).scalar_one_or_none()
        latest_ts_raw = session.execute(
            select(PlanetSnapshot.ts).order_by(PlanetSnapshot.ts.desc(), PlanetSnapshot.id.desc()).limit(1)
        ).scalar_one_or_none()
        first_ts = float(first_ts_raw) if first_ts_raw is not None else None
        latest_ts = float(latest_ts_raw) if latest_ts_raw is not None else None

        rn_first = func.row_number().over(
            partition_by=PlanetSnapshot.planet_id, order_by=(PlanetSnapshot.ts.asc(), PlanetSnapshot.id.asc())
        )
        rn_last = func.row_number().over(
            partition_by=PlanetSnapshot.planet_id, order_by=(PlanetSnapshot.ts.desc(), PlanetSnapshot.id.desc())
        )
        ranked = select(PlanetSnapshot, rn_first.label("rn_first"), rn_last.label("rn_last")).subquery()
        p_rows = session.execute(
            select(ranked)
            .where((ranked.c.rn_first == 1) | (ranked.c.rn_last == 1))
            .order_by(ranked.c.ts.asc(), ranked.c.id.asc())
        ).all()
        first_sp_map: dict[int, float] = {}
        latest_sp_map: dict[int, float] = {}
        for row in p_rows:
            pid = cap_json_int(int(row.planet_id or 0))
            bld_map = loads_json_dict(str(row.buildings_json or "{}"))
            shp_map = loads_json_dict(str(row.ships_json or "{}"))
            econ_map = loads_json_dict(str(row.economy_rates_json or "{}"))
            pig, met, kry, spi = (
                cap_json_int(int(row.pig_iron or 0)),
                cap_json_int(int(row.metal or 0)),
                cap_json_int(int(row.kryptonite or 0)),
                cap_json_int(int(row.spice or 0)),
            )
            res_sp = round(spice_equivalent_from_counts(pig, met, kry, spi), 3)
            snap_info: dict[str, object] = {
                "planet_id": pid,
                "coords": sanitize_text(row.coords or ""),
                "planet_type": sanitize_text(normalize_planet_type(str(row.planet_type or "normal") or "normal")),
                "fields_used": cap_json_int(int(row.fields_used or 0)),
                "fields_max": cap_json_int(int(row.fields_max or 0)),
                "resources": {"pig_iron": pig, "metal": met, "kryptonite": kry, "spice": spi},
                "energy_produced": cap_json_int(int(row.energy_produced or 0)),
                "energy_used": cap_json_int(int(row.energy_used or 0)),
                "buildings_total_levels": cap_json_int(sum(to_int(v, 0) for v in bld_map.values())),
                "ships_total": cap_json_int(sum(to_int(v, 0) for v in shp_map.values())),
                "economy_rates": {sanitize_text(safe_key(k)): cap_json_int(to_int(v, 0)) for k, v in econ_map.items()},
            }
            if pid not in first_by_pid:
                first_by_pid[pid] = snap_info
                first_sp_map[pid] = res_sp
            latest_by_pid[pid] = snap_info
            latest_sp_map[pid] = res_sp
        first_res_sp = round(sum(first_sp_map.values()), 3)
        latest_res_sp = round(sum(latest_sp_map.values()), 3)

    first_bld = sum(to_int(v["buildings_total_levels"], 0) for v in first_by_pid.values())
    latest_bld = sum(to_int(v["buildings_total_levels"], 0) for v in latest_by_pid.values())
    first_fld = sum(to_int(v["fields_used"], 0) for v in first_by_pid.values())
    latest_fld = sum(to_int(v["fields_used"], 0) for v in latest_by_pid.values())
    latest_enet = sum(to_int(v["energy_produced"], 0) - to_int(v["energy_used"], 0) for v in latest_by_pid.values())
    return p_count, {
        "count": p_count,
        "distinct_planets": len(latest_by_pid),
        "first_ts": first_ts,
        "latest_ts": latest_ts,
        "latest_by_planet": {str(pid): info for pid, info in latest_by_pid.items()},
        "trend": {
            "first_total_buildings_levels": first_bld,
            "latest_total_buildings_levels": latest_bld,
            "building_levels_delta": latest_bld - first_bld,
            "first_fields_used": first_fld,
            "latest_fields_used": latest_fld,
            "fields_used_delta": latest_fld - first_fld,
            "latest_energy_net": latest_enet,
            "first_resources_spice_eq": first_res_sp,
            "latest_resources_spice_eq": latest_res_sp,
            "resources_spice_eq_delta": round(latest_res_sp - first_res_sp, 3),
        },
    }


def summarize_rank_snapshots(session: Session) -> tuple[int, int, float, float, float, dict[str, object]]:
    """Summarize rank progression and return `(count, srv_delta, est_delta, latest_pph, pph_delta, payload)`."""
    rows = session.execute(select(RankSnapshot).order_by(RankSnapshot.ts.asc(), RankSnapshot.id.asc())).scalars().all()
    rk_count = len(rows)
    est_vals = [float(r.estimated_points or 0.0) for r in rows]
    sp_vals = [cap_json_int(int(r.server_points)) for r in rows if r.server_points is not None]
    sr_vals = [cap_json_int(int(r.server_rank)) for r in rows if r.server_rank is not None]
    pph_vals = [float(r.pts_per_hr or 0.0) for r in rows]
    spice_vals = [float(r.spice_value or 0.0) for r in rows]

    est_delta = round(est_vals[-1] - est_vals[0], 3) if rk_count >= 1 else 0.0
    srv_pts_delta = (sp_vals[-1] - sp_vals[0]) if sp_vals else 0
    rank_climbed = (sr_vals[0] - sr_vals[-1]) if sr_vals else 0
    pph_delta = round(pph_vals[-1] - pph_vals[0], 3) if rk_count >= 1 else 0.0
    latest_pph = round(pph_vals[-1], 3) if rk_count >= 1 else 0.0
    trajectory = [
        {
            "ts": float(r.ts or 0.0),
            "estimated_points": float(r.estimated_points or 0.0),
            "server_points": cap_json_int(int(r.server_points)) if r.server_points is not None else None,
            "server_rank": cap_json_int(int(r.server_rank)) if r.server_rank is not None else None,
            "pts_per_hr": float(r.pts_per_hr or 0.0),
            "spice_value": float(r.spice_value or 0.0),
            "planets": cap_json_int(max(1, int(r.planets or 1))),
        }
        for r in rows[-240:]
    ]
    return (
        rk_count,
        srv_pts_delta,
        est_delta,
        latest_pph,
        pph_delta,
        {
            "count": rk_count,
            "first_ts": float(rows[0].ts) if rows else None,
            "latest_ts": float(rows[-1].ts) if rows else None,
            "first_estimated_points": round(est_vals[0], 3) if est_vals else 0.0,
            "latest_estimated_points": round(est_vals[-1], 3) if est_vals else 0.0,
            "min_estimated_points": round(min(est_vals), 3) if est_vals else 0.0,
            "max_estimated_points": round(max(est_vals), 3) if est_vals else 0.0,
            "estimated_points_delta": est_delta,
            "first_server_points": sp_vals[0] if sp_vals else None,
            "latest_server_points": sp_vals[-1] if sp_vals else None,
            "min_server_points": min(sp_vals) if sp_vals else None,
            "max_server_points": max(sp_vals) if sp_vals else None,
            "server_points_delta": srv_pts_delta,
            "first_server_rank": sr_vals[0] if sr_vals else None,
            "latest_server_rank": sr_vals[-1] if sr_vals else None,
            "best_server_rank": min(sr_vals) if sr_vals else None,
            "worst_server_rank": max(sr_vals) if sr_vals else None,
            "rank_climbed": rank_climbed,
            "first_pts_per_hr": round(pph_vals[0], 3) if pph_vals else 0.0,
            "latest_pts_per_hr": latest_pph,
            "min_pts_per_hr": round(min(pph_vals), 3) if pph_vals else 0.0,
            "max_pts_per_hr": round(max(pph_vals), 3) if pph_vals else 0.0,
            "avg_pts_per_hr": round(sum(pph_vals) / len(pph_vals), 3) if pph_vals else 0.0,
            "pts_per_hr_delta": pph_delta,
            "latest_spice_value": round(spice_vals[-1], 3) if spice_vals else 0.0,
            "spice_value_delta": round(spice_vals[-1] - spice_vals[0], 3) if spice_vals else 0.0,
            "trajectory": trajectory,
        },
    )


def summarize_actions(session: Session) -> tuple[int, int, float, dict[str, object]]:
    """Aggregate commander action success rates grouped by action kind."""
    rows = session.execute(
        select(ActionHistory.kind, ActionHistory.executed, ActionHistory.dry_run, func.count()).group_by(
            ActionHistory.kind, ActionHistory.executed, ActionHistory.dry_run
        )
    ).all()
    total = executed = failed = dry = 0
    by_kind: dict[str, dict[str, int]] = {}
    for r in rows:
        cnt = int(r[3] or 0)
        kind = sanitize_text(r[0] or "UNKNOWN") or "UNKNOWN"
        ex, is_dry = bool(r[1]), bool(r[2])
        total += cnt
        bucket = by_kind.setdefault(kind, {"total": 0, "executed": 0, "failed": 0, "dry_run": 0})
        bucket["total"] += cnt
        if ex:
            executed += cnt
            bucket["executed"] += cnt
        elif is_dry:
            dry += cnt
            bucket["dry_run"] += cnt
        else:
            failed += cnt
            bucket["failed"] += cnt
    live_attempts = total - dry
    sr = round(executed / max(1, live_attempts), 4) if live_attempts > 0 else 0.0
    return (
        total,
        executed,
        sr,
        {
            "total": total,
            "executed": executed,
            "failed": failed,
            "dry_run": dry,
            "success_rate": sr,
            "by_kind": by_kind,
        },
    )


__all__ = [
    "summarize_actions",
    "summarize_planet_snapshots",
    "summarize_rank_snapshots",
    "summarize_spy_targets",
]
