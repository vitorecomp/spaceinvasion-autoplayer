"""Set-based SQLAlchemy read queries for espionage target filtering and debris ranking."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Integer, and_, case, cast, func, not_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from spaceinvasion.db.dto import SpyTargetRecord
from spaceinvasion.db.models import SpyTarget
from spaceinvasion.db.repos._helpers import clamp_limit, normalize_coords, parse_coords_gsp, to_float, to_int
from spaceinvasion.db.repos.intel import spy_target_to_dto


def _coords_part_int(part_index: int) -> ColumnElement[int]:
    return cast(func.nullif(func.split_part(SpyTarget.coords, ":", part_index), ""), Integer)


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
    empty_unprobed_free = and_(
        SpyTarget.status_color == "free",
        SpyTarget.owner == "",
        SpyTarget.is_spacia == 0,
        SpyTarget.has_debris == 0,
        SpyTarget.last_spied_ts <= 0.0,
        SpyTarget.uninhabited_fields.is_(None),
        SpyTarget.colonize_max_fields.is_(None),
    )
    stmt = select(SpyTarget).where(SpyTarget.merged_into == "", not_(empty_unprobed_free))
    if not include_own:
        stmt = stmt.where(SpyTarget.is_own_planet == 0)
    if only_spied:
        stmt = stmt.where(SpyTarget.last_spied_ts > 0.0)
    if only_inactive_or_spacia:
        stmt = stmt.where((SpyTarget.is_inactive == 1) | (SpyTarget.is_spacia == 1))
    if only_debris:
        stmt = stmt.where(SpyTarget.has_debris == 1)
    if exclude_blocked_at_ts is not None:
        stmt = stmt.where(SpyTarget.blocked_until_ts <= to_float(exclude_blocked_at_ts, 0.0))
    if galaxy is not None and to_int(galaxy, 0) > 0:
        stmt = stmt.where(SpyTarget.coords.like(f"{to_int(galaxy, 0):02d}:%"))

    priority_score: ColumnElement[int] = SpyTarget.is_inactive + SpyTarget.is_spacia + SpyTarget.has_debris
    has_spy_flag: ColumnElement[int] = case((SpyTarget.last_spied_ts > 0.0, 1), else_=0)

    rows = (
        session.execute(
            stmt.order_by(
                priority_score.desc(),
                has_spy_flag.desc(),
                SpyTarget.last_scanned_ts.desc(),
                SpyTarget.points.desc().nulls_last(),
                SpyTarget.coords.asc(),
            ).limit(clamp_limit(limit, 50))
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
