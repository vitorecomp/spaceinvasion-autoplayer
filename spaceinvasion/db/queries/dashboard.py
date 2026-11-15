"""Set-based PostgreSQL queries for DB diagnostics, dashboard time series, and optimizer goals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from alembic.script import ScriptDirectory
from sqlalchemy import Engine, case, func, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from spaceinvasion.db.dto import (
    ConfigOptimizerGoalsRecord,
    DashboardTimeSeriesRecord,
    DbSummaryRecord,
    DbVerifyRecord,
    HourlyProductionRecord,
    PlanetBuildingGoalsRecord,
    TableTiersSummary,
)
from spaceinvasion.db.migrations import (
    BASELINE_REVISION,
    build_alembic_config,
    get_current_revision,
    verify_models_match_migrations,
)
from spaceinvasion.db.models import (
    Base,
    ConfigOptimizerActive,
    GalaxyScan,
    GameMessage,
    HourlyProductionHistory,
    MetaEntry,
    RaidHistory,
    SchemaMigration,
    SchemaMigrationEvent,
    StateSnapshot,
)
from spaceinvasion.db.queries.intel import query_spy_targets
from spaceinvasion.db.repos._helpers import (
    as_str_dict,
    loads_json_dict,
    normalize_coords,
    sanitize_text,
    to_float,
    to_int,
)
from spaceinvasion.db.repos._telemetry_rollups import read_loot_recycle_window
from spaceinvasion.db.repos.telemetry import TelemetryRepository
from spaceinvasion.db.session import DatabaseEngine

HOT_TABLES: tuple[str, ...] = (
    "meta",
    "latest_state_hot",
    "empire_planets_hot",
    "galaxy_sector_hot",
    "colony_candidates_hot",
    "strategy_config_store",
    "economy_rates_cache",
    "tutorial_progress",
    "spy_targets",
    "galaxy_scans",
    "galaxy_slots",
    "ui_notifications",
    "universe_research_max",
    "manual_queue",
    "config_optimizer_active",
    "ai_planner_active",
    "field_conflict_prompts",
    "loot_recycle_hourly",
    "schema_migrations",
)

HISTORY_TABLES: tuple[str, ...] = (
    "rank_snapshots",
    "planet_snapshots",
    "state_snapshots",
    "action_history",
    "config_history",
    "spy_reports",
    "galaxy_scan_history",
    "galaxy_slot_history",
    "raid_history",
    "bash_blocks",
    "game_messages",
    "battle_reports",
    "recycle_reports",
    "manual_queue_events",
    "universe_research_max_history",
    "colony_attempts",
    "config_optimizer_runs",
    "ai_planner_runs",
    "field_conflict_events",
    "hourly_production_history",
    "schema_migration_events",
)

GAME_REQUIRED_TABLES: tuple[str, ...] = (*HOT_TABLES, *HISTORY_TABLES)
TABLE_TIER_BY_NAME: dict[str, str] = {
    **{tbl: "hot" for tbl in HOT_TABLES},
    **{tbl: "history" for tbl in HISTORY_TABLES},
}
_BUILDING_GOAL_KEYS: tuple[str, ...] = tuple(f"b{bid}" for bid in range(1, 22))


def _build_tiers_summary(counts: Mapping[str, int]) -> TableTiersSummary:
    hot_counts = {tbl: int(counts.get(tbl, 0)) for tbl in HOT_TABLES}
    history_counts = {tbl: int(counts.get(tbl, 0)) for tbl in HISTORY_TABLES}
    return TableTiersSummary(
        hot_tables=HOT_TABLES,
        history_tables=HISTORY_TABLES,
        hot_counts=hot_counts,
        history_counts=history_counts,
        hot_total_rows=sum(hot_counts.values()),
        history_total_rows=sum(history_counts.values()),
    )


def _batched_table_counts(session: Session, present_tables: set[str]) -> dict[str, int]:
    """Fetch exact `COUNT(*)` across all existing required tables in 1 SQL round-trip."""
    ordered = [tbl for tbl in GAME_REQUIRED_TABLES if tbl in present_tables and tbl in Base.metadata.tables]
    counts: dict[str, int] = {tbl: 0 for tbl in GAME_REQUIRED_TABLES}
    if not ordered:
        return counts
    exprs: list[ColumnElement[int]] = [
        select(func.count()).select_from(Base.metadata.tables[tbl]).scalar_subquery().label(tbl) for tbl in ordered
    ]
    row = session.execute(select(*exprs)).one()
    for idx, tbl in enumerate(ordered):
        counts[tbl] = int(row[idx] or 0)
    return counts


def query_db_summary(
    session: Session,
    *,
    recent_actions_limit: int = 12,
    spy_targets_limit: int = 25,
) -> DbSummaryRecord:
    """Collect PostgreSQL size, 40-table row counts, tiers, latest timestamps, and dashboard previews."""
    count_exprs: list[ColumnElement[object]] = [
        select(func.count()).select_from(Base.metadata.tables[tbl]).scalar_subquery().label(tbl)
        for tbl in GAME_REQUIRED_TABLES
    ]
    extras: list[ColumnElement[object]] = [
        func.pg_database_size(func.current_database()).label("size_bytes"),
        select(func.max(StateSnapshot.ts)).scalar_subquery().label("latest_snapshot_ts"),
        select(func.max(GalaxyScan.last_scanned_ts)).scalar_subquery().label("latest_scan_ts"),
        select(func.max(RaidHistory.attacked_ts)).scalar_subquery().label("latest_raid_ts"),
        select(func.coalesce(func.max(SchemaMigration.version), 0)).scalar_subquery().label("legacy_ver"),
        func.to_regclass("public.alembic_version").label("alembic_tbl"),
    ]
    row = session.execute(select(*count_exprs, *extras)).one()
    n_tbl = len(GAME_REQUIRED_TABLES)
    counts = {tbl: to_int(row[idx], 0) for idx, tbl in enumerate(GAME_REQUIRED_TABLES)}
    size_bytes = to_int(row[n_tbl], 0)
    latest_snap = to_float(row[n_tbl + 1]) if row[n_tbl + 1] is not None else None
    latest_scan = to_float(row[n_tbl + 2]) if row[n_tbl + 2] is not None else None
    latest_raid = to_float(row[n_tbl + 3]) if row[n_tbl + 3] is not None else None
    legacy_ver = to_int(row[n_tbl + 4], 0)
    has_alembic_tbl = row[n_tbl + 5] is not None

    alembic_rev: str | None = None
    if has_alembic_tbl:
        rev_row = session.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).first()
        if rev_row is not None and rev_row[0]:
            alembic_rev = str(rev_row[0])

    meta_keys = tuple(
        sanitize_text(k)
        for k in session.execute(select(MetaEntry.key).order_by(MetaEntry.key.asc())).scalars().all()
        if sanitize_text(k)
    )
    msg_rows = session.execute(
        select(GameMessage.category, func.count())
        .where(
            ~GameMessage.msg_uid.like("id:msg-%"),
            GameMessage.body_text.op("!~")(r"^\s*\d[\d.]*\s*/\s*\d[\d.]*\s*$"),
        )
        .group_by(GameMessage.category)
    ).all()
    msg_cats = {sanitize_text(r[0] or "general"): int(r[1] or 0) for r in msg_rows}

    tel = TelemetryRepository(session)
    tutorial_history = tel.list_tutorial_progress()
    recent_actions = tel.list_recent_actions(limit=max(1, int(recent_actions_limit)))
    spy_targets = query_spy_targets(session, limit=max(1, int(spy_targets_limit)))

    return DbSummaryRecord(
        backend="postgres",
        size_bytes=size_bytes,
        alembic_revision=alembic_rev,
        legacy_schema_version=legacy_ver,
        counts=counts,
        table_tiers=_build_tiers_summary(counts),
        message_categories=msg_cats,
        persisted_meta_keys=meta_keys,
        latest_snapshot_ts=latest_snap,
        latest_scan_ts=latest_scan,
        latest_raid_ts=latest_raid,
        tutorial_history=tutorial_history,
        recent_actions=recent_actions,
        spy_targets=spy_targets,
    )


def query_db_verify(
    session: Session,
    engine: Engine | DatabaseEngine,
) -> DbVerifyRecord:
    """Verify PostgreSQL catalog tables, ORM zero-drift equivalence, Alembic revision, and ledger integrity."""
    del engine
    conn = session.connection()
    existing = set(inspect(conn).get_table_names(schema="public"))
    missing = tuple(tbl for tbl in GAME_REQUIRED_TABLES if tbl not in existing)
    counts = _batched_table_counts(session, existing)
    tiers = _build_tiers_summary(counts)

    alembic_rev = get_current_revision(conn)
    schema_diffs = tuple(str(d) for d in verify_models_match_migrations(conn)) if not missing else ()

    ledger_ok = False
    events_count = 0
    if "schema_migration_events" in existing:
        bad_event_cond = (
            (SchemaMigrationEvent.event_type == "")
            | (SchemaMigrationEvent.from_version < 0)
            | (SchemaMigrationEvent.to_version < 0)
        )
        ev_row = session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(case((bad_event_cond, 1), else_=0)), 0),
            )
        ).one()
        events_count = int(ev_row[0] or 0)
        bad_events = int(ev_row[1] or 0)
        ledger_ok = bad_events == 0 and (events_count > 0 or alembic_rev is not None)

    target_rev = ScriptDirectory.from_config(build_alembic_config()).get_current_head() or BASELINE_REVISION
    up_to_date = alembic_rev == target_rev
    ok = (not missing) and (not schema_diffs) and ledger_ok and up_to_date
    return DbVerifyRecord(
        ok=ok,
        backend="postgres",
        alembic_revision=alembic_rev,
        target_revision=target_rev,
        up_to_date=up_to_date,
        missing_tables=missing,
        schema_diffs=schema_diffs,
        ledger_ok=ledger_ok,
        ledger_events_count=events_count,
        table_tiers=tiers,
    )


def query_dashboard_time_series(
    session: Session,
    *,
    production_limit: int = 120,
    since_minute_bucket: int | None = None,
    loot_hours: int = 24,
    now_ts: float | None = None,
) -> DashboardTimeSeriesRecord:
    """Fetch production history (full or incremental delta) and hourly loot/recycle window in 2 SQL queries."""
    lim = max(1, min(1440, int(production_limit)))
    incremental = since_minute_bucket is not None
    if since_minute_bucket is not None:
        prod_rows = (
            session.execute(
                select(HourlyProductionHistory)
                .where(HourlyProductionHistory.minute_bucket > int(since_minute_bucket))
                .order_by(HourlyProductionHistory.minute_bucket.asc())
                .limit(lim)
            )
            .scalars()
            .all()
        )
        ordered_prod = list(prod_rows)
    else:
        prod_rows = (
            session.execute(
                select(HourlyProductionHistory).order_by(HourlyProductionHistory.minute_bucket.desc()).limit(lim)
            )
            .scalars()
            .all()
        )
        ordered_prod = list(reversed(prod_rows))

    hourly_prod = tuple(
        HourlyProductionRecord(
            id=int(r.id),
            minute_bucket=int(r.minute_bucket),
            recorded_at=round(float(r.recorded_at), 3),
            pig_iron_per_hour=round(float(r.pig_iron_per_hour), 1),
            metal_per_hour=round(float(r.metal_per_hour), 1),
            kryptonite_per_hour=round(float(r.kryptonite_per_hour), 1),
            spice_per_hour=round(float(r.spice_per_hour), 1),
            total_per_hour=round(float(r.total_per_hour), 1),
            speq_per_hour=round(float(r.speq_per_hour), 1),
            planet_count=max(0, int(r.planet_count)),
            active_builders=max(0, int(r.active_builders)),
        )
        for r in ordered_prod
    )
    latest_bucket = hourly_prod[-1].minute_bucket if hourly_prod else since_minute_bucket
    loot_hist, loot_sum = read_loot_recycle_window(session, hours=loot_hours, now_ts=now_ts)
    return DashboardTimeSeriesRecord(
        hourly_production=hourly_prod,
        latest_minute_bucket=latest_bucket,
        incremental=incremental,
        loot_recycle_hourly=loot_hist,
        loot_recycle_summary=loot_sum,
    )


def query_config_optimizer_goals(session: Session) -> ConfigOptimizerGoalsRecord | None:
    """Extract per-planet Config Optimizer building goals (`b1..b21`) from `config_optimizer_active` in 1 SQL query."""
    active = session.get(ConfigOptimizerActive, 1)
    if active is None:
        return None
    res_obj = loads_json_dict(active.result_json)
    planets_raw = res_obj.get("planets")
    if not isinstance(planets_raw, Sequence) or isinstance(planets_raw, (str, bytes)):
        return None

    by_planet_id: dict[str, PlanetBuildingGoalsRecord] = {}
    by_coords: dict[str, PlanetBuildingGoalsRecord] = {}
    for p_raw in planets_raw:
        p_rec = as_str_dict(p_raw)
        if not p_rec or bool(p_rec.get("is_placeholder", False)):
            continue
        pid_i = max(0, to_int(p_rec.get("planet_id"), 0))
        coords_s = normalize_coords(sanitize_text(p_rec.get("coords") or ""))
        spec_s = sanitize_text(
            p_rec.get("detected_specialization")
            or p_rec.get("locked_specialization")
            or p_rec.get("specialization")
            or ""
        ).strip()
        goals_map: dict[str, int] = {bk: 0 for bk in _BUILDING_GOAL_KEYS}
        raw_blds = p_rec.get("buildings")
        if isinstance(raw_blds, Sequence) and not isinstance(raw_blds, (str, bytes)):
            for b_raw in raw_blds:
                brow = as_str_dict(b_raw)
                bkey = sanitize_text(brow.get("key") or "").strip().lower()
                if bkey in goals_map:
                    goals_map[bkey] = max(0, to_int(brow.get("target_level"), 0))
        entry = PlanetBuildingGoalsRecord(
            planet_id=pid_i,
            coords=coords_s,
            specialization=spec_s,
            goals=goals_map,
        )
        if pid_i > 0:
            by_planet_id[str(pid_i)] = entry
        if coords_s:
            by_coords[coords_s] = entry

    if not by_planet_id and not by_coords:
        return None
    return ConfigOptimizerGoalsRecord(
        run_id=int(active.run_id) if active.run_id is not None else None,
        updated_ts=round(float(active.updated_ts), 3),
        by_planet_id=by_planet_id,
        by_coords=by_coords,
    )


__all__ = [
    "GAME_REQUIRED_TABLES",
    "HISTORY_TABLES",
    "HOT_TABLES",
    "TABLE_TIER_BY_NAME",
    "query_config_optimizer_goals",
    "query_dashboard_time_series",
    "query_db_summary",
    "query_db_verify",
]
