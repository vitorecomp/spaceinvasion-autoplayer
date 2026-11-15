"""Set-based PostgreSQL read queries for colony candidates and attempt summaries."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Double, Select, case, cast, func, literal, select, union_all
from sqlalchemy.orm import Session

from backend.db.dto import ColonyCandidateRecord
from backend.db.models import ColonyAttempt, ColonyCandidateHot
from backend.db.repos._helpers import clamp_limit, normalize_coords, parse_coords_gsp, to_float, to_int

_DELETED_EVENTS: frozenset[str] = frozenset({"deleted_undersized", "deleted", "abandoned", "undersized"})
_KEPT_EVENTS: frozenset[str] = frozenset({"kept", "settled_kept"})
_FAILED_EVENTS: frozenset[str] = frozenset({"failed", "aborted", "blocked", "error"})


@dataclass(frozen=True)
class ColonyAttemptCoordSummary:
    """Per-coordinate colonization attempt rollout counters."""

    coords: str
    attempts_count: int
    events_count: int
    dispatched_count: int
    deleted_count: int
    kept_count: int
    failed_count: int
    last_event_type: str
    last_fields_rolled: int | None
    last_temperature: int | None
    last_origin_coords: str
    last_status_message: str
    last_ts: float


@dataclass(frozen=True)
class ColonyAttemptsSummary:
    """Aggregated KPI summary across all `colony_attempts` rows."""

    total_events: int
    unique_coords_tried: int
    dispatched_count: int
    deleted_undersized_count: int
    kept_count: int
    failed_count: int
    by_coords: dict[str, ColonyAttemptCoordSummary]


@dataclass
class _CoordAcc:
    """Mutable per-coordinate accumulator for `query_colony_attempts_summary`."""

    attempts: int = 0
    events: int = 0
    dispatched: int = 0
    deleted: int = 0
    kept: int = 0
    failed: int = 0
    pending_dispatch: bool = False
    last_ev: str = ""
    last_fields: int | None = None
    last_temp: int | None = None
    last_origin: str = ""
    last_msg: str = ""
    last_ts: float = 0.0


def _distance_expr(origin_coords: str) -> ColumnElement[float]:
    parsed = parse_coords_gsp(origin_coords) if origin_coords.strip() else None
    if parsed is None:
        return literal(100.0 if origin_coords.strip() else 1000.0, type_=Double())
    og, osys, opos = parsed
    zero_coord = (
        (ColonyCandidateHot.galaxy == 0) & (ColonyCandidateHot.system == 0) & (ColonyCandidateHot.position == 0)
    )
    return cast(
        case(
            (zero_coord, 100.0),
            (ColonyCandidateHot.galaxy != og, 20000.0 * func.abs(ColonyCandidateHot.galaxy - og)),
            (ColonyCandidateHot.system != osys, 2700.0 + 95.0 * func.abs(ColonyCandidateHot.system - osys)),
            else_=1000.0 + 5.0 * func.abs(ColonyCandidateHot.position - opos),
        ),
        Double(),
    )


def _unprobed_score_expr(dist_col: ColumnElement[float]) -> ColumnElement[float]:
    pos = ColonyCandidateHot.position
    pot_expr = case(
        ((pos >= 4) & (pos <= 6), 56 - func.abs(pos - 5) * 3),
        ((pos >= 13) & (pos <= 16), 48 - func.abs(pos - 14) * 2),
        else_=30,
    )
    d_pen = func.least(5, func.floor(func.greatest(0.0, dist_col - 1000.0) / 800.0))
    return cast(func.greatest(5, func.least(85, pot_expr - d_pen)), Double())


def _build_tiered_candidate_rows(
    session: Session,
    *,
    origin_coords: str,
    min_tgt: int,
    excl: list[str],
    include_discarded: bool,
    eff_limit: int,
) -> list[tuple[object, ...]]:
    dist_col = _distance_expr(origin_coords)
    pos = ColonyCandidateHot.position
    eff_fields = ColonyCandidateHot.max_fields_est
    unprobed_score = _unprobed_score_expr(dist_col)

    base_conds: list[ColumnElement[bool]] = [ColonyCandidateHot.is_active == 1]
    if not include_discarded:
        base_conds.append(ColonyCandidateHot.is_discarded == 0)
    if excl:
        base_conds.append(ColonyCandidateHot.coords.notin_(excl))

    def _branch(
        tier_val: int,
        meets_val: bool,
        extra: ColumnElement[bool],
        primary_col: ColumnElement[float],
    ) -> Select[str, float, bool, int, float]:
        return (
            select(
                ColonyCandidateHot.coords.label("coords"),
                dist_col.label("distance"),
                literal(meets_val).label("meets_target"),
                literal(tier_val).label("tier"),
                primary_col.label("primary_sort"),
            )
            .where(*base_conds, extra)
            .order_by(primary_col.asc(), dist_col.asc(), ColonyCandidateHot.coords.asc())
            .limit(eff_limit)
        )

    neg_fields: ColumnElement[float] = cast(-eff_fields, Double[float]())
    t0 = _branch(0, True, (ColonyCandidateHot.is_probed == 1) & (eff_fields >= min_tgt), neg_fields)
    t1 = _branch(1, False, (ColonyCandidateHot.is_probed == 0) & (pos >= 7) & (pos <= 12), dist_col)
    t2 = _branch(2, False, (ColonyCandidateHot.is_probed == 0) & ((pos < 7) | (pos > 12)), -unprobed_score)
    t3 = _branch(3, False, (ColonyCandidateHot.is_probed == 1) & (eff_fields < min_tgt), neg_fields)

    ranked = union_all(t0, t1, t2, t3).subquery("tiered_colonies")
    top_ranked = (
        select(ranked)
        .order_by(ranked.c.tier.asc(), ranked.c.primary_sort.asc(), ranked.c.distance.asc(), ranked.c.coords.asc())
        .limit(eff_limit)
        .subquery("top_colonies")
    )
    stmt = (
        select(
            ColonyCandidateHot,
            top_ranked.c.distance,
            top_ranked.c.meets_target,
            top_ranked.c.tier,
        )
        .join(top_ranked, ColonyCandidateHot.coords == top_ranked.c.coords)
        .order_by(
            top_ranked.c.tier.asc(),
            top_ranked.c.primary_sort.asc(),
            top_ranked.c.distance.asc(),
            top_ranked.c.coords.asc(),
        )
    )
    return [tuple(r) for r in session.execute(stmt).all()]


def query_colony_candidates(
    session: Session,
    *,
    origin_coords: str = "",
    min_fields_target: int = 300,
    exclude_coords: Iterable[str] = (),
    include_discarded: bool = False,
    limit: int = 200,
) -> tuple[ColonyCandidateRecord, ...]:
    """Rank active colony candidates in a single index-backed tiered PostgreSQL `SELECT`."""
    min_tgt = max(50, min(600, to_int(min_fields_target, 300)))
    eff_limit = clamp_limit(limit, 200)
    excl = sorted({nc for raw in exclude_coords if (nc := normalize_coords(raw))})

    raw_rows = _build_tiered_candidate_rows(
        session,
        origin_coords=origin_coords,
        min_tgt=min_tgt,
        excl=excl,
        include_discarded=include_discarded,
        eff_limit=eff_limit,
    )
    out: list[ColonyCandidateRecord] = []
    for raw_row in raw_rows:
        cand = raw_row[0]
        if not isinstance(cand, ColonyCandidateHot):
            continue
        dist_raw = raw_row[1]
        tier_raw = raw_row[3]
        dist_f = to_float(dist_raw, 0.0)
        tier_i = to_int(tier_raw, 3)
        out.append(
            ColonyCandidateRecord(
                coords=cand.coords,
                galaxy=to_int(cand.galaxy, 0),
                system=to_int(cand.system, 0),
                position=to_int(cand.position, 0),
                uninhabited_fields=to_int(cand.uninhabited_fields, 0) if cand.uninhabited_fields is not None else None,
                colonize_min_fields=to_int(cand.colonize_min_fields, 0)
                if cand.colonize_min_fields is not None
                else None,
                colonize_max_fields=to_int(cand.colonize_max_fields, 0)
                if cand.colonize_max_fields is not None
                else None,
                max_fields_est=to_int(cand.max_fields_est, 0),
                temperature=to_int(cand.temperature, 0) if cand.temperature is not None else None,
                is_probed=bool(cand.is_probed),
                last_spied_ts=to_float(cand.last_spied_ts, 0.0),
                last_scanned_ts=to_float(cand.last_scanned_ts, 0.0),
                spy_count=to_int(cand.spy_count, 0),
                blocked_until_ts=to_float(cand.blocked_until_ts, 0.0),
                block_reason=cand.block_reason,
                is_discarded=bool(cand.is_discarded),
                is_active=bool(cand.is_active),
                updated_ts=to_float(cand.updated_ts, 0.0),
                distance=round(dist_f, 1),
                meets_target=bool(raw_row[2]),
                tier=tier_i,
            )
        )
    return tuple(out)


def query_colony_attempts_summary(session: Session) -> ColonyAttemptsSummary:
    """Summarize all `colony_attempts` in one single ordered `SELECT` statement."""
    rows = (
        session.execute(select(ColonyAttempt).order_by(ColonyAttempt.ts.asc(), ColonyAttempt.id.asc())).scalars().all()
    )
    total_events = dispatched_count = deleted_count = kept_count = failed_count = 0
    acc_by_coords: dict[str, _CoordAcc] = {}

    for r in rows:
        nc = normalize_coords(r.target_coords)
        if not nc:
            continue
        total_events += 1
        ev = r.event_type.strip().lower()
        acc = acc_by_coords.setdefault(nc, _CoordAcc())
        acc.events += 1
        if ev == "dispatched":
            dispatched_count += 1
            acc.dispatched += 1
            acc.attempts += 1
            acc.pending_dispatch = True
        else:
            if ev in _DELETED_EVENTS:
                deleted_count += 1
                acc.deleted += 1
            elif ev in _KEPT_EVENTS:
                kept_count += 1
                acc.kept += 1
            elif ev == "settled":
                eff_min = max(50, int(r.min_fields_target)) if int(r.min_fields_target) >= 50 else 300
                if r.fields_rolled is not None and int(r.fields_rolled) >= eff_min:
                    kept_count += 1
                    acc.kept += 1
            elif ev in _FAILED_EVENTS:
                failed_count += 1
                acc.failed += 1
            if acc.pending_dispatch:
                acc.pending_dispatch = False
            else:
                acc.attempts += 1

        acc.last_ev = ev
        if r.fields_rolled is not None:
            acc.last_fields = int(r.fields_rolled)
        if r.temperature is not None:
            acc.last_temp = int(r.temperature)
        if r.origin_coords.strip():
            acc.last_origin = normalize_coords(r.origin_coords)
        if r.status_message:
            acc.last_msg = r.status_message
        acc.last_ts = max(acc.last_ts, to_float(r.ts, 0.0))

    by_coords = {
        nc: ColonyAttemptCoordSummary(
            coords=nc,
            attempts_count=a.attempts,
            events_count=a.events,
            dispatched_count=a.dispatched,
            deleted_count=a.deleted,
            kept_count=a.kept,
            failed_count=a.failed,
            last_event_type=a.last_ev,
            last_fields_rolled=a.last_fields,
            last_temperature=a.last_temp,
            last_origin_coords=a.last_origin,
            last_status_message=a.last_msg,
            last_ts=a.last_ts,
        )
        for nc, a in acc_by_coords.items()
    }
    return ColonyAttemptsSummary(
        total_events=total_events,
        unique_coords_tried=len(by_coords),
        dispatched_count=dispatched_count,
        deleted_undersized_count=deleted_count,
        kept_count=kept_count,
        failed_count=failed_count,
        by_coords=by_coords,
    )


__all__ = [
    "ColonyAttemptCoordSummary",
    "ColonyAttemptsSummary",
    "query_colony_attempts_summary",
    "query_colony_candidates",
]
