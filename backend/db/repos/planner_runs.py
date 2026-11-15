"""SQLAlchemy 2.0 ORM repository for config-optimizer runs, AI-planner runs, and field conflicts."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.db.dto import (
    AiPlannerActiveRecord,
    AiPlannerRunRecord,
    ConfigOptimizerActiveRecord,
    ConfigOptimizerRunRecord,
    FieldConflictEventRecord,
    FieldConflictPromptRecord,
)
from backend.db.models import AiPlannerActive, AiPlannerRun, ConfigOptimizerActive, ConfigOptimizerRun
from backend.db.repos._field_conflicts import (
    list_conflict_events,
    list_conflict_prompts,
    resolve_conflict_prompt,
    upsert_conflict_prompt,
)
from backend.db.repos._helpers import (
    as_str_dict,
    clamp_int64,
    dumps_json,
    loads_json_dict,
    round_ts,
    sanitize_text,
    to_float,
)
from backend.db.repos._planner_helpers import (
    apply_floor_update,
    apply_spec_updates,
    extract_ai_kpis,
    merge_floors,
    normalize_floors,
    normalize_specs,
)


class PlannerRunsRepository:
    """SQLAlchemy ORM repository over optimizer runs, AI planner runs, and field conflicts."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_config_optimizer_run(
        self,
        result: Mapping[str, object],
        *,
        params: Mapping[str, object] | None = None,
        reserved_floors: Mapping[str, Mapping[str, int]] | None = None,
        trigger_source: str = "manual",
        elapsed_ms: float = 0.0,
        set_active: bool = True,
        ts: float | None = None,
    ) -> int:
        """Append a config-optimizer run and optionally promote it to `config_optimizer_active`."""
        now_ts = round_ts(ts)
        meta_obj, summary_obj = as_str_dict(result.get("meta_parameters")), as_str_dict(result.get("summary"))
        clean_params = as_str_dict(params) if params is not None else dict(meta_obj)
        clean_trigger = sanitize_text(trigger_source or "manual", 64).strip() or "manual"
        eff_elapsed = max(0.0, elapsed_ms if elapsed_ms > 0.0 else to_float(result.get("elapsed_ms"), 0.0))
        target_ship_key = (
            sanitize_text(
                clean_params.get("target_ship_key")
                or meta_obj.get("target_ship_key")
                or summary_obj.get("target_ship_key")
                or "s16",
                32,
            ).strip()
            or "s16"
        )
        ships_per_month = to_float(
            summary_obj.get(
                "ships_per_month",
                summary_obj.get(
                    "target_ships_per_month",
                    summary_obj.get("best_ships_per_month", result.get("ships_per_month", 0.0)),
                ),
            ),
            0.0,
        )
        limiting = sanitize_text(summary_obj.get("limiting_factor", result.get("limiting_factor", "")), 128).strip()

        active_row = self._session.get(ConfigOptimizerActive, 1)
        eff_floors = merge_floors(active_row, reserved_floors, clean_params)
        if "planet_specializations" in clean_params:
            clean_params["planet_specializations"] = normalize_specs(clean_params["planet_specializations"])
        elif active_row is not None:
            existing_specs = normalize_specs(loads_json_dict(active_row.params_json).get("planet_specializations"))
            if existing_specs:
                clean_params["planet_specializations"] = existing_specs

        params_json, floors_json, result_json = (
            dumps_json(clean_params),
            dumps_json(eff_floors),
            dumps_json(dict(result)),
        )
        run = ConfigOptimizerRun(
            created_ts=now_ts,
            trigger_source=clean_trigger,
            elapsed_ms=round(eff_elapsed, 3),
            target_ship_key=target_ship_key,
            ships_per_month=ships_per_month,
            limiting_factor=limiting,
            params_json=params_json,
            reserved_floors_json=floors_json,
            result_json=result_json,
        )
        self._session.add(run)
        self._session.flush()
        if set_active:
            stmt = pg_insert(ConfigOptimizerActive).values(
                singleton_id=1,
                run_id=run.id,
                updated_ts=now_ts,
                params_json=params_json,
                reserved_floors_json=floors_json,
                result_json=result_json,
            )
            self._session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[ConfigOptimizerActive.singleton_id],
                    set_={
                        "run_id": stmt.excluded.run_id,
                        "updated_ts": stmt.excluded.updated_ts,
                        "params_json": stmt.excluded.params_json,
                        "reserved_floors_json": stmt.excluded.reserved_floors_json,
                        "result_json": stmt.excluded.result_json,
                    },
                )
            )
        return int(run.id)

    def get_active_config_optimizer(self) -> ConfigOptimizerActiveRecord | None:
        """Read the active config-optimizer singleton row."""
        row = self._session.get(ConfigOptimizerActive, 1)
        if row is None:
            return None
        return ConfigOptimizerActiveRecord(
            singleton_id=int(row.singleton_id),
            run_id=int(row.run_id) if row.run_id is not None else None,
            updated_ts=float(row.updated_ts),
            params_json=row.params_json,
            reserved_floors_json=row.reserved_floors_json,
            result_json=row.result_json,
        )

    def list_config_optimizer_runs(self, *, limit: int = 20) -> tuple[ConfigOptimizerRunRecord, ...]:
        """List config-optimizer runs ordered newest first."""
        lim = max(1, min(500, int(limit)))
        rows = (
            self._session.execute(
                select(ConfigOptimizerRun)
                .order_by(ConfigOptimizerRun.created_ts.desc(), ConfigOptimizerRun.id.desc())
                .limit(lim)
            )
            .scalars()
            .all()
        )
        return tuple(
            ConfigOptimizerRunRecord(
                r.id,
                float(r.created_ts),
                r.trigger_source,
                float(r.elapsed_ms),
                r.target_ship_key,
                float(r.ships_per_month),
                r.limiting_factor,
                r.params_json,
                r.reserved_floors_json,
                r.result_json,
            )
            for r in rows
        )

    def record_ai_planner_run(
        self,
        result: Mapping[str, object],
        *,
        config_run_id: int | None = None,
        params: Mapping[str, object] | None = None,
        trigger_source: str = "manual",
        elapsed_ms: float = 0.0,
        set_active: bool = True,
        ts: float | None = None,
    ) -> int:
        """Append an AI-planner run and optionally promote it to `ai_planner_active`."""
        now_ts = round_ts(ts)
        meta_obj, summary_obj = as_str_dict(result.get("meta_parameters")), as_str_dict(result.get("summary"))
        clean_params = as_str_dict(params) if params is not None else dict(meta_obj)
        clean_trigger = sanitize_text(trigger_source or "manual", 64).strip() or "manual"
        eff_elapsed = max(0.0, elapsed_ms if elapsed_ms > 0.0 else to_float(result.get("elapsed_ms"), 0.0))
        reward_weeks, weekly_discount, discounted_reward, total_net, actions_count = extract_ai_kpis(
            result, clean_params, meta_obj, summary_obj
        )
        params_json, summary_json, result_json = (
            dumps_json(clean_params),
            dumps_json(summary_obj),
            dumps_json(dict(result)),
        )
        cfg_rid = clamp_int64(int(config_run_id)) if config_run_id is not None else None
        run = AiPlannerRun(
            created_ts=now_ts,
            config_run_id=cfg_rid,
            trigger_source=clean_trigger,
            elapsed_ms=round(eff_elapsed, 3),
            reward_weeks=reward_weeks,
            weekly_discount=weekly_discount,
            discounted_reward_spice_eq=discounted_reward,
            total_net_spice_eq=total_net,
            actions_count=actions_count,
            params_json=params_json,
            summary_json=summary_json,
            result_json=result_json,
        )
        self._session.add(run)
        self._session.flush()
        if set_active:
            stmt = pg_insert(AiPlannerActive).values(
                singleton_id=1,
                run_id=run.id,
                config_run_id=cfg_rid,
                updated_ts=now_ts,
                params_json=params_json,
                summary_json=summary_json,
                result_json=result_json,
            )
            self._session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[AiPlannerActive.singleton_id],
                    set_={
                        "run_id": stmt.excluded.run_id,
                        "config_run_id": stmt.excluded.config_run_id,
                        "updated_ts": stmt.excluded.updated_ts,
                        "params_json": stmt.excluded.params_json,
                        "summary_json": stmt.excluded.summary_json,
                        "result_json": stmt.excluded.result_json,
                    },
                )
            )
        return int(run.id)

    def get_active_ai_planner(self) -> AiPlannerActiveRecord | None:
        """Read the active AI-planner singleton row."""
        row = self._session.get(AiPlannerActive, 1)
        if row is None:
            return None
        return AiPlannerActiveRecord(
            singleton_id=int(row.singleton_id),
            run_id=int(row.run_id) if row.run_id is not None else None,
            config_run_id=int(row.config_run_id) if row.config_run_id is not None else None,
            updated_ts=float(row.updated_ts),
            params_json=row.params_json,
            summary_json=row.summary_json,
            result_json=row.result_json,
        )

    def list_ai_planner_runs(self, *, limit: int = 20) -> tuple[AiPlannerRunRecord, ...]:
        """List AI-planner history runs ordered newest first."""
        lim = max(1, min(500, int(limit)))
        rows = (
            self._session.execute(
                select(AiPlannerRun).order_by(AiPlannerRun.created_ts.desc(), AiPlannerRun.id.desc()).limit(lim)
            )
            .scalars()
            .all()
        )
        return tuple(
            AiPlannerRunRecord(
                r.id,
                float(r.created_ts),
                int(r.config_run_id) if r.config_run_id is not None else None,
                r.trigger_source,
                float(r.elapsed_ms),
                int(r.reward_weeks),
                float(r.weekly_discount),
                float(r.discounted_reward_spice_eq),
                float(r.total_net_spice_eq),
                int(r.actions_count),
                r.params_json,
                r.summary_json,
                r.result_json,
            )
            for r in rows
        )

    def upsert_field_conflict_prompt(
        self, conflict: Mapping[str, object], *, ts: float | None = None
    ) -> FieldConflictPromptRecord:
        """Upsert a field-limit conflict prompt and append an audit event."""
        return upsert_conflict_prompt(self._session, conflict, ts=ts)

    def resolve_field_conflict_prompt(
        self, conflict_key: str, decision: str, *, detail: Mapping[str, object] | None = None, ts: float | None = None
    ) -> FieldConflictPromptRecord | None:
        """Resolve a field-limit conflict prompt and append a `resolved` event."""
        return resolve_conflict_prompt(self._session, conflict_key, decision, detail=detail, ts=ts)

    def list_field_conflict_prompts(self, *, status: str | None = "pending") -> tuple[FieldConflictPromptRecord, ...]:
        """List field-limit conflict prompts filtered by status."""
        return list_conflict_prompts(self._session, status=status)

    def get_reserved_building_floors(self) -> dict[str, dict[str, int]]:
        """Read normalized per-planet reserved building floors from `config_optimizer_active`."""
        row = self._session.get(ConfigOptimizerActive, 1)
        if row is None:
            return {}
        return normalize_floors(loads_json_dict(row.reserved_floors_json))

    def update_reserved_building_floor(
        self,
        planet_key: str,
        building_key: str,
        min_level: int,
        *,
        ts: float | None = None,
    ) -> dict[str, dict[str, int]]:
        """Upsert one planet building floor into `config_optimizer_active.reserved_floors_json`."""
        now_ts = round_ts(ts)
        row = self._session.get(ConfigOptimizerActive, 1)
        cur_floors = normalize_floors(loads_json_dict(row.reserved_floors_json)) if row is not None else {}
        floors = apply_floor_update(cur_floors, planet_key, building_key, min_level)
        floors_json = dumps_json(floors)
        stmt = pg_insert(ConfigOptimizerActive).values(
            singleton_id=1,
            run_id=row.run_id if row is not None else None,
            updated_ts=now_ts,
            params_json=row.params_json if row is not None else "{}",
            reserved_floors_json=floors_json,
            result_json=row.result_json if row is not None else "{}",
        )
        self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[ConfigOptimizerActive.singleton_id],
                set_={
                    "updated_ts": stmt.excluded.updated_ts,
                    "reserved_floors_json": stmt.excluded.reserved_floors_json,
                },
            )
        )
        return floors

    def get_config_optimizer_specializations(self) -> dict[str, str]:
        """Read per-planet specialization locks from `config_optimizer_active.params_json`."""
        row = self._session.get(ConfigOptimizerActive, 1)
        if row is None:
            return {}
        return normalize_specs(loads_json_dict(row.params_json).get("planet_specializations"))

    def update_config_optimizer_specializations(
        self,
        specializations: Mapping[str, object],
        *,
        replace: bool = False,
        ts: float | None = None,
    ) -> dict[str, str]:
        """Upsert or replace per-planet specialization locks in `config_optimizer_active.params_json`."""
        now_ts = round_ts(ts)
        row = self._session.get(ConfigOptimizerActive, 1)
        params_dict = loads_json_dict(row.params_json) if row is not None else {}
        cur_specs = normalize_specs(params_dict.get("planet_specializations"))
        next_specs = apply_spec_updates(cur_specs, specializations, replace=replace)
        params_dict["planet_specializations"] = next_specs
        params_json = dumps_json(params_dict)
        stmt = pg_insert(ConfigOptimizerActive).values(
            singleton_id=1,
            run_id=row.run_id if row is not None else None,
            updated_ts=now_ts,
            params_json=params_json,
            reserved_floors_json=row.reserved_floors_json if row is not None else "{}",
            result_json=row.result_json if row is not None else "{}",
        )
        self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[ConfigOptimizerActive.singleton_id],
                set_={"updated_ts": stmt.excluded.updated_ts, "params_json": stmt.excluded.params_json},
            )
        )
        return next_specs

    def list_field_conflict_events(
        self, *, conflict_key: str | None = None, limit: int = 100
    ) -> tuple[FieldConflictEventRecord, ...]:
        """List append-only field-limit conflict audit events."""
        return list_conflict_events(self._session, conflict_key=conflict_key, limit=limit)


__all__ = ["PlannerRunsRepository"]
