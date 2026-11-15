"""Set-based SQLAlchemy read queries for 3/24h Bash tracking and raid performance stats."""

from __future__ import annotations

from collections.abc import Sequence
import math

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.db.dto import ActiveTargetBlock, BashWindowEntry, RaidStatsSummary, TargetRaidStats
from backend.db.models import BashBlock, RaidHistory, SpyTarget
from backend.db.repos._helpers import (
    RESOURCE_KEYS,
    is_win_outcome,
    normalize_coords,
    normalize_loot_dict,
    round_ts,
    spice_equivalent_from_counts,
)


def query_bash_window(
    session: Session,
    *,
    now_ts: float | None = None,
    window_seconds: float = 86400.0,
    max_hits: int = 3,
) -> tuple[BashWindowEntry, ...]:
    """Compute per-target 3/24h Bash tracker status and active server blocks in 1 SQL query."""
    ref = round_ts(now_ts)
    eff_win = max(1.0, float(window_seconds))
    cutoff = ref - eff_win
    skew_max = ref + 60.0
    cap_hits = max(1, int(max_hits))

    hits_cte = (
        select(
            RaidHistory.target_coords.label("coords"),
            func.count().label("hits"),
            func.min(RaidHistory.attacked_ts).label("first_ts"),
            func.max(RaidHistory.attacked_ts).label("last_ts"),
        )
        .where(
            RaidHistory.is_exempt == 0,
            RaidHistory.attacked_ts >= cutoff,
            RaidHistory.attacked_ts <= skew_max,
        )
        .group_by(RaidHistory.target_coords)
        .cte("bash_window_hits")
    )
    blocks_cte = (
        select(
            SpyTarget.coords.label("coords"),
            SpyTarget.blocked_until_ts.label("blocked_until_ts"),
            SpyTarget.block_reason.label("block_reason"),
        )
        .where(
            SpyTarget.blocked_until_ts > ref,
            or_(SpyTarget.last_blocked_ts <= 0.0, SpyTarget.last_blocked_ts <= skew_max),
        )
        .cte("active_target_blocks")
    )
    coord_col = func.coalesce(hits_cte.c.coords, blocks_cte.c.coords).label("target_coords")
    hits_col = func.coalesce(hits_cte.c.hits, 0).label("hits")
    until_col = func.coalesce(blocks_cte.c.blocked_until_ts, 0.0).label("blocked_until_ts")

    stmt = (
        select(
            coord_col,
            hits_col,
            hits_cte.c.first_ts,
            hits_cte.c.last_ts,
            until_col,
            func.coalesce(blocks_cte.c.block_reason, "").label("block_reason"),
        )
        .select_from(hits_cte.outerjoin(blocks_cte, hits_cte.c.coords == blocks_cte.c.coords, full=True))
        .order_by(
            hits_col.desc(),
            until_col.desc(),
            hits_cte.c.last_ts.desc().nulls_last(),
            coord_col.asc(),
        )
    )
    rows = session.execute(stmt).all()
    entries: list[BashWindowEntry] = []
    for r in rows:
        coords = normalize_coords(str(r[0] or ""))
        if not coords:
            continue
        hits = max(0, int(r[1] or 0))
        first_ts = float(r[2]) if r[2] is not None else None
        last_ts = float(r[3]) if r[3] is not None else None
        b_until = float(r[4] or 0.0)
        b_reason = str(r[5] or "")
        rem = max(0, cap_hits - hits)
        blocked_by_hits = hits >= cap_hits
        is_blocked = blocked_by_hits or b_until > ref
        next_open: float | None = None
        if is_blocked:
            candidates: list[float] = []
            if blocked_by_hits and first_ts is not None:
                candidates.append(round(first_ts + eff_win, 3))
            if b_until > ref:
                candidates.append(round(b_until, 3))
            if candidates:
                next_open = max(candidates)
        entries.append(
            BashWindowEntry(
                target_coords=coords,
                hits_in_window=hits,
                remaining_attacks=0 if is_blocked else rem,
                is_bash_blocked=is_blocked,
                first_hit_ts=first_ts,
                last_hit_ts=last_ts,
                blocked_until_ts=b_until,
                block_reason=b_reason,
                next_slot_opens_ts=next_open,
            )
        )
    return tuple(entries)


def _aggregate_raid_slice(
    target_coords: str,
    rows: Sequence[tuple[str, float, str, dict[str, int], float, bool]],
    *,
    ref_ts: float,
) -> TargetRaidStats:
    n = len(rows)
    if n == 0:
        return TargetRaidStats(target_coords, 0, 0, 0, 0.0, 0, 0, 0, 0, 0.0, 0.0, 0.0, None, "")
    wins = sum(1 for r in rows if r[5])
    losses = n - wins
    tot_loot: dict[str, int] = {rk: 0 for rk in RESOURCE_KEYS}
    tot_sp = 0.0
    order_weights: list[float] = []
    combined_weights: list[float] = []
    sp_values: list[float] = []
    for idx, r in enumerate(rows):
        loot = r[3]
        for rk in RESOURCE_KEYS:
            tot_loot[rk] += max(0, int(loot.get(rk, 0)))
        sp = float(r[4])
        sp_values.append(sp)
        tot_sp += sp
        order_w = 0.65 ** ((n - 1) - idx)
        ts_i = float(r[1])
        time_w = math.exp(-max(0.0, ref_ts - ts_i) / (3.0 * 86400.0)) if (ts_i > 0.0 and ref_ts > 0.0) else 0.75
        order_weights.append(order_w)
        combined_weights.append(order_w * time_w)
    weight_tot = sum(combined_weights)
    if weight_tot <= 1e-12:
        weights = order_weights
        weight_tot = sum(weights)
    else:
        weights = combined_weights
    weighted_sum = sum(w * sp for w, sp in zip(weights, sp_values, strict=True))
    last = rows[-1]
    return TargetRaidStats(
        target_coords=target_coords,
        attempts=n,
        wins=wins,
        losses=losses,
        success_rate=round(wins / n, 6),
        pig_iron=tot_loot["pig_iron"],
        metal=tot_loot["metal"],
        kryptonite=tot_loot["kryptonite"],
        spice=tot_loot["spice"],
        total_loot_spice_eq=round(tot_sp, 3),
        avg_loot_spice_eq=round(tot_sp / n, 3),
        recency_weighted_loot_spice_eq=round(weighted_sum / max(1e-12, weight_tot), 3),
        last_attacked_ts=float(last[1]),
        last_outcome=last[2],
    )


def query_raid_stats(
    session: Session,
    *,
    window_seconds: float | None = None,
    now_ts: float | None = None,
) -> RaidStatsSummary:
    """Compute overall and per-target raid statistics plus active target blocks in 3 SQL queries."""
    active_now = round_ts(now_ts)
    raid_stmt = select(
        RaidHistory.target_coords,
        RaidHistory.attacked_ts,
        RaidHistory.outcome,
        RaidHistory.loot_json,
    )
    if window_seconds is not None:
        cutoff = active_now - max(0.0, float(window_seconds))
        raid_stmt = raid_stmt.where(RaidHistory.attacked_ts >= cutoff)
    raw_raids = session.execute(raid_stmt.order_by(RaidHistory.attacked_ts.asc(), RaidHistory.id.asc())).all()

    norm_rows: list[tuple[str, float, str, dict[str, int], float, bool]] = []
    grouped: dict[str, list[tuple[str, float, str, dict[str, int], float, bool]]] = {}
    for r in raw_raids:
        coords = normalize_coords(str(r[0] or ""))
        atk_ts = float(r[1] or 0.0)
        outcome = str(r[2] or "")
        loot = normalize_loot_dict(str(r[3] or "{}"))
        sp = round(spice_equivalent_from_counts(loot["pig_iron"], loot["metal"], loot["kryptonite"], loot["spice"]), 3)
        tup = (coords, atk_ts, outcome, loot, sp, is_win_outcome(outcome, sp))
        norm_rows.append(tup)
        if coords:
            grouped.setdefault(coords, []).append(tup)

    max_row_ts = max((r[1] for r in norm_rows), default=0.0)
    ref_ts = active_now if (now_ts is not None and active_now > 0.0) else max_row_ts
    overall = _aggregate_raid_slice("*", norm_rows, ref_ts=ref_ts)
    per_target = tuple(_aggregate_raid_slice(coords, grouped[coords], ref_ts=ref_ts) for coords in sorted(grouped))

    block_rows = session.execute(
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
    active_blocks = tuple(
        ActiveTargetBlock(
            target_coords=normalize_coords(str(br[0] or "")),
            blocked_until_ts=float(br[1] or 0.0),
            remaining_seconds=round(max(0.0, float(br[1] or 0.0) - active_now), 1),
            reason=str(br[2] or ""),
            bash_hits=max(0, int(br[3] or 0)),
            last_blocked_ts=float(br[4] or 0.0),
        )
        for br in block_rows
        if normalize_coords(str(br[0] or ""))
    )
    bash_blocks_count = int(session.execute(select(func.count()).select_from(BashBlock)).scalar_one() or 0)
    return RaidStatsSummary(
        overall=overall,
        per_target=per_target,
        active_blocks=active_blocks,
        bash_blocks_count=bash_blocks_count,
    )


__all__ = ["query_bash_window", "query_raid_stats"]
