"""Detailed raid statistics and full telemetry history summary queries over SQLAlchemy ORM."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from spaceinvasion.core.catalog import cap_json_int, safe_big_ratio
from spaceinvasion.db.models import BashBlock, GalaxyScan, RaidHistory, SpyReportRecord, SpyTarget
from spaceinvasion.db.queries._telemetry_summary_parts import (
    summarize_actions,
    summarize_planet_snapshots,
    summarize_rank_snapshots,
    summarize_spy_targets,
)
from spaceinvasion.db.repos._helpers import (
    RESOURCE_KEYS,
    is_win_outcome,
    normalize_coords,
    normalize_loot_dict,
    sanitize_text,
    spice_equivalent_from_counts,
    to_float,
    to_int,
)


@dataclass(frozen=True)
class _NormRaidRow:
    row_id: int
    target_coords: str
    attacked_ts: float
    is_exempt: bool
    outcome: str
    loot: dict[str, int]
    loot_spice_eq: float
    won: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.row_id,
            "target_coords": self.target_coords,
            "attacked_ts": self.attacked_ts,
            "is_exempt": self.is_exempt,
            "outcome": self.outcome,
            "loot": dict(self.loot),
            "loot_spice_eq": self.loot_spice_eq,
            "won": self.won,
        }


def _load_norm_raid_rows(
    session: Session,
    *,
    window_seconds: float | None = None,
    now: float | None = None,
) -> list[_NormRaidRow]:
    stmt = select(
        RaidHistory.id,
        RaidHistory.target_coords,
        RaidHistory.attacked_ts,
        RaidHistory.is_exempt,
        RaidHistory.outcome,
        RaidHistory.loot_json,
    )
    win_f = to_float(window_seconds, -1.0) if window_seconds is not None else -1.0
    if win_f >= 0.0:
        ref = to_float(now, -1.0) if now is not None else -1.0
        if ref < 0.0:
            ref = time.time()
        stmt = stmt.where(RaidHistory.attacked_ts >= ref - win_f)
    rows = session.execute(stmt.order_by(RaidHistory.attacked_ts.asc(), RaidHistory.id.asc())).all()
    out: list[_NormRaidRow] = []
    for r in rows:
        loot = normalize_loot_dict(str(r[5] or "{}"))
        sp = round(spice_equivalent_from_counts(loot["pig_iron"], loot["metal"], loot["kryptonite"], loot["spice"]), 3)
        outcome = sanitize_text(r[4] or "")
        out.append(
            _NormRaidRow(
                row_id=cap_json_int(to_int(r[0], 0)),
                target_coords=normalize_coords(str(r[1] or "")),
                attacked_ts=to_float(r[2], 0.0),
                is_exempt=bool(r[3]),
                outcome=outcome,
                loot=loot,
                loot_spice_eq=sp,
                won=is_win_outcome(outcome, sp),
            )
        )
    return out


def _aggregate_raid_rows(rows: Sequence[_NormRaidRow], *, ref_ts: float) -> dict[str, object]:
    n = len(rows)
    if n == 0:
        return {
            "attempts": 0,
            "wins": 0,
            "losses": 0,
            "success_rate": 0.0,
            "total_loot": {rk: 0 for rk in RESOURCE_KEYS},
            "avg_loot": {rk: 0.0 for rk in RESOURCE_KEYS},
            "total_loot_spice_eq": 0.0,
            "avg_loot_spice_eq": 0.0,
            "recency_weighted_loot_spice_eq": 0.0,
        }
    wins = sum(1 for r in rows if r.won)
    tot_loot: dict[str, int] = {rk: 0 for rk in RESOURCE_KEYS}
    tot_sp = 0.0
    order_w_list: list[float] = []
    comb_w_list: list[float] = []
    sp_list: list[float] = []
    for idx, r in enumerate(rows):
        for rk in RESOURCE_KEYS:
            tot_loot[rk] += max(0, int(r.loot[rk]))
        sp_list.append(r.loot_spice_eq)
        tot_sp += r.loot_spice_eq
        ow = 0.65 ** ((n - 1) - idx)
        tw = (
            math.exp(-max(0.0, ref_ts - r.attacked_ts) / (3.0 * 86400.0))
            if (r.attacked_ts > 0.0 and ref_ts > 0.0)
            else 0.75
        )
        order_w_list.append(ow)
        comb_w_list.append(ow * tw)
    w_tot = sum(comb_w_list)
    weights = order_w_list if w_tot <= 1e-12 else comb_w_list
    w_tot = sum(weights)
    w_sum = sum(w * sp for w, sp in zip(weights, sp_list, strict=True))
    return {
        "attempts": n,
        "wins": wins,
        "losses": n - wins,
        "success_rate": round(wins / n, 6),
        "total_loot": tot_loot,
        "avg_loot": {rk: round(safe_big_ratio(tot_loot[rk], n, cap=1e200), 3) for rk in RESOURCE_KEYS},
        "total_loot_spice_eq": round(tot_sp, 3),
        "avg_loot_spice_eq": round(tot_sp / n, 3),
        "recency_weighted_loot_spice_eq": round(w_sum / max(1e-12, w_tot), 3),
    }


def query_full_history_summary(session: Session) -> dict[str, object]:
    """Compute the full longitudinal telemetry summary embedded inside `get_raid_stats()`."""
    norm_raids = _load_norm_raid_rows(session)
    max_raid_ts = max((r.attacked_ts for r in norm_raids), default=0.0)
    raid_agg = _aggregate_raid_rows(norm_raids, ref_ts=max_raid_ts)
    last_atk: dict[str, object] | None = (
        {
            "target_coords": norm_raids[-1].target_coords,
            "attacked_ts": norm_raids[-1].attacked_ts,
            "outcome": norm_raids[-1].outcome,
            "loot": dict(norm_raids[-1].loot),
            "loot_spice_eq": norm_raids[-1].loot_spice_eq,
        }
        if norm_raids
        else None
    )
    raid_summary: dict[str, object] = {
        "count": len(norm_raids),
        **raid_agg,
        "targets_count": len({r.target_coords for r in norm_raids if r.target_coords}),
        "last_attack": last_atk,
    }
    spy_count, spy_summary = summarize_spy_targets(session)
    gal_scans_cnt = int(session.execute(select(func.count()).select_from(GalaxyScan)).scalar_one() or 0)
    spy_rep_cnt = int(session.execute(select(func.count()).select_from(SpyReportRecord)).scalar_one() or 0)
    p_count, planet_summary = summarize_planet_snapshots(session)
    rk_count, srv_pts_delta, est_delta, latest_pph, pph_delta, rank_summary = summarize_rank_snapshots(session)
    act_total, act_exec, act_sr, action_summary = summarize_actions(session)
    pts_delta = float(srv_pts_delta) if srv_pts_delta != 0 else est_delta
    return {
        "raid_history": raid_summary,
        "spy_targets": spy_summary,
        "planet_snapshots": planet_summary,
        "rank_snapshots": rank_summary,
        "action_history": action_summary,
        "rank_snapshots_count": rk_count,
        "planet_snapshots_count": p_count,
        "action_history_count": act_total,
        "spy_targets_count": spy_count,
        "spy_reports_count": spy_rep_cnt,
        "galaxy_scans_count": gal_scans_cnt,
        "points_delta": round(pts_delta, 3),
        "points_trend": round(pts_delta, 3),
        "pts_per_hr": latest_pph,
        "economy_trend": pph_delta,
        "action_count": act_total,
        "action_successes": act_exec,
        "action_success_rate": act_sr,
    }


def query_raid_stats_detailed(
    session: Session,
    *,
    window_seconds: float | None = None,
    now: float | None = None,
    include_telemetry_summary: bool = True,
) -> dict[str, object]:
    """Compute the full `get_raid_stats` payload matching the API golden contract."""
    now_f = to_float(now, -1.0) if now is not None else -1.0
    active_now = now_f if now_f >= 0.0 else time.time()
    norm_rows = _load_norm_raid_rows(session, window_seconds=window_seconds, now=now)
    max_row_ts = max((r.attacked_ts for r in norm_rows), default=0.0)
    ref_ts = now_f if now_f > 0.0 else max_row_ts

    overall = _aggregate_raid_rows(norm_rows, ref_ts=ref_ts)
    if norm_rows:
        lr = norm_rows[-1]
        overall["last_attack"] = {
            "target_coords": lr.target_coords,
            "attacked_ts": lr.attacked_ts,
            "outcome": lr.outcome,
            "loot": dict(lr.loot),
            "loot_spice_eq": lr.loot_spice_eq,
        }
    else:
        overall["last_attack"] = None

    grouped: dict[str, list[_NormRaidRow]] = {}
    for r in norm_rows:
        if r.target_coords:
            grouped.setdefault(r.target_coords, []).append(r)

    per_target: dict[str, dict[str, object]] = {}
    for coords, t_rows in grouped.items():
        t_agg = _aggregate_raid_rows(t_rows, ref_ts=ref_ts)
        tl = t_rows[-1]
        t_agg["last_outcome"] = tl.outcome
        t_agg["last_attacked_ts"] = tl.attacked_ts
        t_agg["last_loot"] = dict(tl.loot)
        t_agg["last_loot_spice_eq"] = tl.loot_spice_eq
        per_target[coords] = t_agg

    bash_rows = (
        session.execute(select(BashBlock).order_by(BashBlock.blocked_ts.asc(), BashBlock.id.asc())).scalars().all()
    )
    bash_blocks = [
        {
            "id": cap_json_int(to_int(br.id, 0)),
            "target_coords": normalize_coords(br.target_coords),
            "blocked_ts": to_float(br.blocked_ts, 0.0),
            "blocked_until_ts": to_float(br.blocked_until_ts, 0.0),
            "cooldown_seconds": to_float(br.cooldown_seconds, 0.0),
            "is_bash": bool(br.is_bash),
            "reason": sanitize_text(br.reason),
        }
        for br in bash_rows
    ]
    blocked_rows = session.execute(
        select(
            SpyTarget.coords,
            SpyTarget.blocked_until_ts,
            SpyTarget.block_reason,
            SpyTarget.bash_hits,
            SpyTarget.last_blocked_ts,
        )
        .where(SpyTarget.blocked_until_ts > active_now)
        .order_by(SpyTarget.blocked_until_ts.desc(), SpyTarget.coords.asc())
    ).all()
    blocked_targets: dict[str, dict[str, object]] = {}
    for sr in blocked_rows:
        bc = normalize_coords(str(sr[0] or ""))
        if not bc:
            continue
        b_until = to_float(sr[1], 0.0)
        blocked_targets[bc] = {
            "blocked_until_ts": b_until,
            "remaining_seconds": round(max(0.0, b_until - active_now), 1),
            "reason": sanitize_text(sr[2] or ""),
            "bash_hits": cap_json_int(max(0, to_int(sr[3], 0))),
            "last_blocked_ts": to_float(sr[4], 0.0),
        }

    return {
        "overall": overall,
        "per_target": per_target,
        "history": [r.to_dict() for r in norm_rows],
        "bash_blocks": bash_blocks,
        "telemetry_summary": query_full_history_summary(session) if include_telemetry_summary else {},
        "blocked_targets": blocked_targets,
    }


__all__ = ["query_full_history_summary", "query_raid_stats_detailed"]
