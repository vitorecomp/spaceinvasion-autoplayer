"""Set-based SQLAlchemy read queries for espionage target filtering and debris ranking."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Integer, Select, and_, case, cast, func, literal, not_, select, union_all
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from backend.db.dto import SpyTargetRecord
from backend.db.models import SpyTarget
from backend.db.repos._helpers import clamp_limit, normalize_coords, parse_coords_gsp, to_float, to_int
from backend.db.repos.intel import spy_target_to_dto


def _coords_part_int(part_index: int) -> ColumnElement[int]:
    return cast(func.nullif(func.split_part(SpyTarget.coords, ":", part_index), ""), Integer)


def _empty_unprobed_free_expr() -> ColumnElement[bool]:
    return and_(
        SpyTarget.status_color == "free",
        SpyTarget.owner == "",
        SpyTarget.is_spacia == 0,
        SpyTarget.has_debris == 0,
        SpyTarget.last_spied_ts <= 0.0,
        SpyTarget.uninhabited_fields.is_(None),
        SpyTarget.colonize_max_fields.is_(None),
    )


def _query_spy_targets_bucketed(
    session: Session,
    *,
    extra_conds: list[ColumnElement[bool]],
    only_spied: bool,
    only_inactive_or_spacia: bool,
    only_debris: bool,
    eff_limit: int,
) -> list[SpyTarget]:
    """Execute 1 index-backed `UNION ALL` across discrete `(inactive, spacia, debris, spied)` buckets."""
    spied_modes: tuple[tuple[ColumnElement[bool], int], ...] = (
        ((SpyTarget.last_spied_ts > 0.0, 1),)
        if only_spied
        else ((SpyTarget.last_spied_ts > 0.0, 1), (SpyTarget.last_spied_ts <= 0.0, 0))
    )
    branches: list[Select[str, int, int, float, int | None]] = []
    for a in (1, 0):
        for b in (1, 0):
            if only_inactive_or_spacia and (a == 0 and b == 0):
                continue
            for c in (1, 0):
                if only_debris and c == 0:
                    continue
                score_val = a + b + c
                for spied_cond, spied_flag in spied_modes:
                    branches.append(
                        select(
                            SpyTarget.coords.label("coords"),
                            literal(score_val).label("priority_score"),
                            literal(spied_flag).label("has_spy_flag"),
                            SpyTarget.last_scanned_ts.label("last_scanned_ts"),
                            SpyTarget.points.label("points"),
                        )
                        .where(
                            SpyTarget.merged_into == "",
                            SpyTarget.is_own_planet == 0,
                            SpyTarget.is_inactive == a,
                            SpyTarget.is_spacia == b,
                            SpyTarget.has_debris == c,
                            spied_cond,
                            *extra_conds,
                        )
                        .order_by(
                            SpyTarget.last_scanned_ts.desc(),
                            SpyTarget.points.desc().nulls_last(),
                            SpyTarget.coords.asc(),
                        )
                        .limit(eff_limit)
                    )
    ranked = union_all(*branches).subquery("spy_target_buckets")
    top = (
        select(ranked)
        .order_by(
            ranked.c.priority_score.desc(),
            ranked.c.has_spy_flag.desc(),
            ranked.c.last_scanned_ts.desc(),
            ranked.c.points.desc().nulls_last(),
            ranked.c.coords.asc(),
        )
        .limit(eff_limit)
        .subquery("top_spy_targets")
    )
    stmt = (
        select(SpyTarget)
        .join(top, SpyTarget.coords == top.c.coords)
        .order_by(
            top.c.priority_score.desc(),
            top.c.has_spy_flag.desc(),
            top.c.last_scanned_ts.desc(),
            top.c.points.desc().nulls_last(),
            top.c.coords.asc(),
        )
    )
    return list(session.execute(stmt).scalars().all())


def query_spy_targets(
    session: Session,
    *,
    limit: int = 50,
    include_own: bool = False,
    only_spied: bool = False,
    only_inactive_or_spacia: bool = False,
    only_debris: bool = False,
    exclude_blocked_at_ts: float | None = None,
    galaxy: int | None = None,
) -> tuple[SpyTargetRecord, ...]:
    """Filter and rank `spy_targets` in 1 SQL query with explicit `NULLS LAST` ordering."""
    eff_limit = clamp_limit(limit, 50)
    extra_conds: list[ColumnElement[bool]] = [not_(_empty_unprobed_free_expr())]
    if exclude_blocked_at_ts is not None:
        extra_conds.append(SpyTarget.blocked_until_ts <= to_float(exclude_blocked_at_ts, 0.0))
    if galaxy is not None and to_int(galaxy, 0) > 0:
        extra_conds.append(SpyTarget.coords.like(f"{to_int(galaxy, 0):02d}:%"))

    if not include_own:
        rows = _query_spy_targets_bucketed(
            session,
            extra_conds=extra_conds,
            only_spied=only_spied,
            only_inactive_or_spacia=only_inactive_or_spacia,
            only_debris=only_debris,
            eff_limit=eff_limit,
        )
        return tuple(spy_target_to_dto(r) for r in rows)

    stmt = select(SpyTarget).where(SpyTarget.merged_into == "", *extra_conds)
    if only_spied:
        stmt = stmt.where(SpyTarget.last_spied_ts > 0.0)
    if only_inactive_or_spacia:
        stmt = stmt.where((SpyTarget.is_inactive == 1) | (SpyTarget.is_spacia == 1))
    if only_debris:
        stmt = stmt.where(SpyTarget.has_debris == 1)

    priority_score: ColumnElement[int] = SpyTarget.is_inactive + SpyTarget.is_spacia + SpyTarget.has_debris
    has_spy_flag: ColumnElement[int] = case((SpyTarget.last_spied_ts > 0.0, 1), else_=0)
    rows = list(
        session.execute(
            stmt.order_by(
                priority_score.desc(),
                has_spy_flag.desc(),
                SpyTarget.last_scanned_ts.desc(),
                SpyTarget.points.desc().nulls_last(),
                SpyTarget.coords.asc(),
            ).limit(eff_limit)
        )
        .scalars()
        .all()
    )
    return tuple(spy_target_to_dto(r) for r in rows)


def query_active_debris_targets(
    session: Session,
    *,
    origin_coords: Sequence[str] = (),
    limit: int = 100,
    include_own: bool = False,
) -> tuple[SpyTargetRecord, ...]:
    """Rank active debris fields (`has_debris = 1`) by proximity to `origin_coords` in 1 SQL query."""
    parsed_origins: list[tuple[int, int, int]] = []
    seen: set[str] = set()
    for raw in origin_coords:
        nc = normalize_coords(raw)
        gsp = parse_coords_gsp(nc)
        if gsp is not None and nc not in seen:
            seen.add(nc)
            parsed_origins.append(gsp)

    stmt = select(SpyTarget).where(SpyTarget.merged_into == "", SpyTarget.has_debris == 1)
    if not include_own:
        stmt = stmt.where(SpyTarget.is_own_planet == 0)

    if parsed_origins:
        g_col = func.coalesce(_coords_part_int(1), 0)
        s_col = func.coalesce(_coords_part_int(2), 0)
        p_col = func.coalesce(_coords_part_int(3), 0)
        dist_terms: list[ColumnElement[int]] = [
            case((g_col == og, 0), else_=1_000_000)
            + func.abs(g_col - og) * 100_000
            + func.abs(s_col - osys) * 100
            + func.abs(p_col - opos)
            for og, osys, opos in parsed_origins
        ]
        best_dist: ColumnElement[int] = dist_terms[0] if len(dist_terms) == 1 else func.least(*dist_terms)
        ordered = stmt.order_by(
            best_dist.asc(),
            SpyTarget.last_scanned_ts.desc(),
            SpyTarget.points.desc().nulls_last(),
            SpyTarget.coords.asc(),
        )
    else:
        ordered = stmt.order_by(
            SpyTarget.last_scanned_ts.desc(),
            SpyTarget.points.desc().nulls_last(),
            SpyTarget.coords.asc(),
        )

    rows = session.execute(ordered.limit(clamp_limit(limit, 100))).scalars().all()
    return tuple(spy_target_to_dto(r) for r in rows)


__all__ = ["query_active_debris_targets", "query_spy_targets"]
