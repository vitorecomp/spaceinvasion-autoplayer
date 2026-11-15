"""SQLAlchemy ORM models for optimizer runs, AI planner runs, and field conflicts."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, desc, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.models.base import Base, JsonbText, identity_pk


class ConfigOptimizerRun(Base):
    """Append-only history of config-optimizer simulation runs."""

    __tablename__ = "config_optimizer_runs"
    __table_args__ = (Index("idx_config_optimizer_runs_ts", "created_ts"),)

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    created_ts: Mapped[float] = mapped_column()
    trigger_source: Mapped[str] = mapped_column()
    elapsed_ms: Mapped[float] = mapped_column(server_default=text("0"))
    target_ship_key: Mapped[str] = mapped_column(server_default=text("'s16'"))
    ships_per_month: Mapped[float] = mapped_column(server_default=text("0"))
    limiting_factor: Mapped[str] = mapped_column(server_default=text("''"))
    params_json: Mapped[str] = mapped_column(JsonbText())
    reserved_floors_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))
    result_json: Mapped[str] = mapped_column(JsonbText())


class ConfigOptimizerActive(Base):
    """Singleton active config-optimizer result and reserved building floors."""

    __tablename__ = "config_optimizer_active"
    __table_args__ = (
        CheckConstraint(
            "singleton_id = 1",
            name="config_optimizer_active_singleton_id_check",
        ),
    )

    singleton_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("config_optimizer_runs.id", name="fk_config_optimizer_active_run_id")
    )
    updated_ts: Mapped[float] = mapped_column()
    params_json: Mapped[str] = mapped_column(JsonbText())
    reserved_floors_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))
    result_json: Mapped[str] = mapped_column(JsonbText())


class AiPlannerRun(Base):
    """Append-only history of AI-planner optimization runs."""

    __tablename__ = "ai_planner_runs"
    __table_args__ = (
        Index("idx_ai_planner_runs_cfg", "config_run_id", "created_ts"),
        Index("idx_ai_planner_runs_ts", "created_ts"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    created_ts: Mapped[float] = mapped_column()
    config_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("config_optimizer_runs.id", name="fk_ai_planner_runs_config_run_id")
    )
    trigger_source: Mapped[str] = mapped_column()
    elapsed_ms: Mapped[float] = mapped_column(server_default=text("0"))
    reward_weeks: Mapped[int] = mapped_column(server_default=text("26"))
    weekly_discount: Mapped[float] = mapped_column(server_default=text("0.9"))
    discounted_reward_spice_eq: Mapped[float] = mapped_column(server_default=text("0"))
    total_net_spice_eq: Mapped[float] = mapped_column(server_default=text("0"))
    actions_count: Mapped[int] = mapped_column(server_default=text("0"))
    params_json: Mapped[str] = mapped_column(JsonbText())
    summary_json: Mapped[str] = mapped_column(JsonbText())
    result_json: Mapped[str] = mapped_column(JsonbText())


class AiPlannerActive(Base):
    """Singleton active AI-planner plan snapshot."""

    __tablename__ = "ai_planner_active"
    __table_args__ = (
        CheckConstraint(
            "singleton_id = 1",
            name="ai_planner_active_singleton_id_check",
        ),
    )

    singleton_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("ai_planner_runs.id", name="fk_ai_planner_active_run_id"))
    config_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("config_optimizer_runs.id", name="fk_ai_planner_active_config_run_id")
    )
    updated_ts: Mapped[float] = mapped_column()
    params_json: Mapped[str] = mapped_column(JsonbText())
    summary_json: Mapped[str] = mapped_column(JsonbText())
    result_json: Mapped[str] = mapped_column(JsonbText())


class FieldConflictPrompt(Base):
    """Active or resolved planet field-limit conflict prompts."""

    __tablename__ = "field_conflict_prompts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'resolved', 'dismissed', 'expired')",
            name="ck_field_conflict_prompts_status",
        ),
        Index("idx_field_conflict_prompts_planet", "planet_id", "status"),
        Index("idx_field_conflict_prompts_status_ts", "status", "created_ts"),
        Index(
            "idx_field_conflict_prompts_pending",
            desc("created_ts"),
            "conflict_key",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    conflict_key: Mapped[str] = mapped_column(primary_key=True)
    planet_id: Mapped[int] = mapped_column()
    planet_coords: Mapped[str] = mapped_column()
    blocked_building_key: Mapped[str] = mapped_column()
    blocked_target_level: Mapped[int] = mapped_column()
    candidate_building_key: Mapped[str] = mapped_column()
    candidate_level: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    decision: Mapped[str] = mapped_column(server_default=text("''"))
    created_ts: Mapped[float] = mapped_column()
    resolved_ts: Mapped[float | None] = mapped_column()
    detail_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))


class FieldConflictEvent(Base):
    """Append-only history of field-conflict prompt creation and decisions."""

    __tablename__ = "field_conflict_events"
    __table_args__ = (
        Index("idx_field_conflict_events_key_ts", "conflict_key", "created_ts"),
        Index("idx_field_conflict_events_ts", "created_ts"),
    )

    id: Mapped[int] = mapped_column(identity_pk(), primary_key=True)
    created_ts: Mapped[float] = mapped_column()
    conflict_key: Mapped[str] = mapped_column(
        ForeignKey("field_conflict_prompts.conflict_key", name="fk_field_conflict_events_conflict_key")
    )
    event_type: Mapped[str] = mapped_column()
    decision: Mapped[str] = mapped_column(server_default=text("''"))
    detail_json: Mapped[str] = mapped_column(JsonbText(), server_default=text("'{}'::jsonb"))


__all__ = [
    "AiPlannerActive",
    "AiPlannerRun",
    "ConfigOptimizerActive",
    "ConfigOptimizerRun",
    "FieldConflictEvent",
    "FieldConflictPrompt",
]
