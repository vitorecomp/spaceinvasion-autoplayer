"""Batched SQLAlchemy refresh helper for `colony_candidates_hot`."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from spaceinvasion.db.models import ColonyCandidateHot, GalaxySlot, MetaEntry, SpyReportRecord, SpyTarget
from spaceinvasion.db.repos._helpers import (
    loads_json_dict,
    normalize_coords,
    parse_coords_gsp,
    round_ts,
    sanitize_text,
)


def _load_discarded_coords(session: Session) -> set[str]:
    raw = session.execute(
        select(MetaEntry.value).where(MetaEntry.key == "discarded_colony_coords").limit(1)
    ).scalar_one_or_none()
    if not raw:
        return set()
    parsed = loads_json_dict(str(raw))
    return {nc for k in parsed if (nc := normalize_coords(str(k)))}


def _empty_colony_entry() -> list[object]:
    return [None, None, None, None, 0.0, 0.0, 0, 0.0, ""]


def _merge_spy_targets_into(
    session: Session,
    norm_coords: Sequence[str],
    merged: dict[str, list[object]],
) -> None:
    spy_rows = session.execute(
        select(
            SpyTarget.coords,
            SpyTarget.uninhabited_fields,
            SpyTarget.colonize_min_fields,
            SpyTarget.colonize_max_fields,
            SpyTarget.temperature,
            SpyTarget.last_spied_ts,
            SpyTarget.last_scanned_ts,
            SpyTarget.spy_count,
            SpyTarget.blocked_until_ts,
            SpyTarget.block_reason,
        ).where(
            SpyTarget.coords.in_(norm_coords),
            SpyTarget.merged_into == "",
            SpyTarget.is_own_planet == 0,
            or_(
                SpyTarget.status_color == "free",
                (
                    or_(SpyTarget.uninhabited_fields.is_not(None), SpyTarget.colonize_max_fields.is_not(None))
                    & (SpyTarget.owner == "")
                    & (SpyTarget.is_spacia == 0)
                ),
            ),
        )
    ).all()
    for sr in spy_rows:
        nc = normalize_coords(str(sr[0] or ""))
        if not nc:
            continue
        entry = merged.setdefault(nc, _empty_colony_entry())
        if sr[1] is not None:
            entry[0] = int(sr[1])
        if sr[2] is not None and entry[1] is None:
            entry[1] = int(sr[2])
        if sr[3] is not None and entry[2] is None:
            entry[2] = int(sr[3])
        if sr[4] is not None and entry[3] is None:
            entry[3] = int(sr[4])
        entry[4] = max(float(entry[4]) if isinstance(entry[4], (int, float)) else 0.0, float(sr[5] or 0.0))
        entry[5] = max(float(entry[5]) if isinstance(entry[5], (int, float)) else 0.0, float(sr[6] or 0.0))
        entry[6] = max(int(entry[6]) if isinstance(entry[6], int) else 0, int(sr[7] or 0))
        b_until = float(sr[8] or 0.0)
        b_rsn = sanitize_text(sr[9] or "")
        b_low = b_rsn.lower()
        cur_b = float(entry[7]) if isinstance(entry[7], (int, float)) else 0.0
        if "can't be attacked" not in b_low and "cannot be attacked" not in b_low and b_until > cur_b:
            entry[7] = b_until
            entry[8] = b_rsn


def _merge_colony_sources(session: Session, norm_coords: Sequence[str]) -> dict[str, list[object]]:
    merged: dict[str, list[object]] = {}
    slot_rows = session.execute(
        select(
            GalaxySlot.coords,
            GalaxySlot.colonize_min_fields,
            GalaxySlot.colonize_max_fields,
            GalaxySlot.temperature,
            GalaxySlot.last_scanned_ts,
        ).where(GalaxySlot.status_color == "free", GalaxySlot.coords.in_(norm_coords))
    ).all()
    for r in slot_rows:
        nc = normalize_coords(str(r[0] or ""))
        if nc:
            merged[nc] = [None, r[1], r[2], r[3], 0.0, float(r[4] or 0.0), 0, 0.0, ""]

    _merge_spy_targets_into(session, norm_coords, merged)

    rep_rows = session.execute(
        select(
            SpyReportRecord.target_coords,
            func.max(SpyReportRecord.uninhabited_fields),
            func.max(SpyReportRecord.ts),
            func.count(),
        )
        .where(
            SpyReportRecord.target_coords.in_(norm_coords),
            SpyReportRecord.uninhabited_fields.is_not(None),
            SpyReportRecord.owner == "",
            SpyReportRecord.is_spacia == 0,
        )
        .group_by(SpyReportRecord.target_coords)
    ).all()
    for rr in rep_rows:
        nc = normalize_coords(str(rr[0] or ""))
        if not nc:
            continue
        entry = merged.setdefault(nc, _empty_colony_entry())
        if rr[1] is not None:
            entry[0] = int(rr[1])
        entry[4] = max(float(entry[4]) if isinstance(entry[4], (int, float)) else 0.0, float(rr[2] or 0.0))
        entry[6] = max(int(entry[6]) if isinstance(entry[6], int) else 0, int(rr[3] or 0))
    return merged


def refresh_colony_candidates_hot(
    session: Session,
    coords_iter: Sequence[str],
    *,
    ts: float | None = None,
) -> int:
    """Recompute and bulk-upsert `colony_candidates_hot` for `coords_iter` in O(1) SQL."""
    norm_coords = sorted({nc for c in coords_iter if (nc := normalize_coords(c))})
    if not norm_coords:
        return 0
    ts_r = round_ts(ts)
    discarded = _load_discarded_coords(session)
    merged = _merge_colony_sources(session, norm_coords)

    payload: list[dict[str, object]] = []
    inactive_coords: list[str] = []
    for nc in norm_coords:
        entry = merged.get(nc)
        if entry is None:
            inactive_coords.append(nc)
            continue
        gsp = parse_coords_gsp(nc)
        gal_i, sys_i, pos_i = gsp if gsp is not None else (0, 0, 0)
        uf = int(entry[0]) if isinstance(entry[0], int) else None
        cmin = int(entry[1]) if isinstance(entry[1], int) else None
        cmax = int(entry[2]) if isinstance(entry[2], int) else None
        temp = int(entry[3]) if isinstance(entry[3], int) else None
        uf_pos = uf if (uf is not None and uf > 0) else None
        cmin_pos = cmin if (cmin is not None and cmin > 0) else None
        cmax_pos = cmax if (cmax is not None and cmax > 0) else None
        eff_cmax = cmin_pos if (cmin_pos is not None and cmax_pos is not None and cmin_pos > cmax_pos) else cmax_pos
        max_est = uf_pos if uf_pos is not None else (eff_cmax if eff_cmax is not None else 0)
        payload.append(
            {
                "coords": nc,
                "galaxy": gal_i,
                "system": sys_i,
                "position": pos_i,
                "uninhabited_fields": uf,
                "colonize_min_fields": cmin,
                "colonize_max_fields": cmax,
                "max_fields_est": max_est,
                "temperature": temp,
                "is_probed": 1 if (uf_pos is not None or eff_cmax is not None) else 0,
                "last_spied_ts": float(entry[4]) if isinstance(entry[4], (int, float)) else 0.0,
                "last_scanned_ts": float(entry[5]) if isinstance(entry[5], (int, float)) else 0.0,
                "spy_count": int(entry[6]) if isinstance(entry[6], int) else 0,
                "blocked_until_ts": float(entry[7]) if isinstance(entry[7], (int, float)) else 0.0,
                "block_reason": str(entry[8]),
                "is_discarded": 1 if nc in discarded else 0,
                "is_active": 1,
                "updated_ts": ts_r,
            }
        )
    if payload:
        stmt = pg_insert(ColonyCandidateHot).values(payload)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[ColonyCandidateHot.coords],
                set_={k: getattr(stmt.excluded, k) for k in payload[0] if k != "coords"},
            )
        )
    if inactive_coords:
        session.execute(
            update(ColonyCandidateHot)
            .where(ColonyCandidateHot.coords.in_(inactive_coords), ColonyCandidateHot.is_active == 1)
            .values(is_active=0, updated_ts=ts_r)
        )
    return len(payload)


__all__ = ["refresh_colony_candidates_hot"]
