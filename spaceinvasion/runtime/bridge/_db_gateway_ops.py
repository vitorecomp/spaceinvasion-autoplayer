"""Manual command queue, config optimizer, AI planner, and field-conflict gateway mixin."""

from __future__ import annotations

from collections.abc import Mapping

from spaceinvasion.core.catalog import cap_json_int
from spaceinvasion.db.dto import (
    AiPlannerRunRecord,
    ConfigOptimizerRunRecord,
    FieldConflictPromptRecord,
    ManualQueueRecord,
)
from spaceinvasion.db.repos import complete_matching_queue_item, normalize_floors
from spaceinvasion.runtime.bridge._db_gateway_common import _GatewayCore, _loads_dict


def _queue_item_to_dict(rec: ManualQueueRecord) -> dict[str, object]:
    return {
        "id": cap_json_int(rec.id),
        "created_ts": rec.created_ts,
        "updated_ts": rec.updated_ts,
        "status": rec.status,
        "priority": rec.priority,
        "sort_rank": cap_json_int(rec.sort_rank),
        "kind": rec.kind,
        "planet_id": cap_json_int(rec.planet_id),
        "planet_coords": rec.planet_coords,
        "target_id": cap_json_int(rec.target_id) if rec.target_id is not None else None,
        "target_name": rec.target_name,
        "target_coords": rec.target_coords,
        "target_level": cap_json_int(rec.target_level) if rec.target_level is not None else None,
        "note": rec.note,
        "completed_ts": rec.completed_ts,
        "canceled_ts": rec.canceled_ts,
        "result_message": rec.result_message,
    }


def _cfg_run_to_dict(rec: ConfigOptimizerRunRecord) -> dict[str, object]:
    return {
        "id": cap_json_int(rec.id),
        "created_ts": rec.created_ts,
        "trigger_source": rec.trigger_source,
        "elapsed_ms": rec.elapsed_ms,
        "target_ship_key": rec.target_ship_key,
        "ships_per_month": rec.ships_per_month,
        "limiting_factor": rec.limiting_factor,
        "params": _loads_dict(rec.params_json),
        "reserved_floors": normalize_floors(_loads_dict(rec.reserved_floors_json)),
    }


def _ai_run_to_dict(rec: AiPlannerRunRecord) -> dict[str, object]:
    return {
        "id": cap_json_int(rec.id),
        "created_ts": rec.created_ts,
        "config_run_id": cap_json_int(rec.config_run_id) if rec.config_run_id is not None else None,
        "trigger_source": rec.trigger_source,
        "elapsed_ms": rec.elapsed_ms,
        "reward_weeks": cap_json_int(rec.reward_weeks),
        "weekly_discount": rec.weekly_discount,
        "discounted_reward_spice_eq": rec.discounted_reward_spice_eq,
        "total_net_spice_eq": rec.total_net_spice_eq,
        "actions_count": cap_json_int(rec.actions_count),
        "params": _loads_dict(rec.params_json),
        "summary": _loads_dict(rec.summary_json),
    }


def _conflict_to_dict(rec: FieldConflictPromptRecord) -> dict[str, object]:
    return {
        "conflict_key": rec.conflict_key,
        "planet_id": cap_json_int(rec.planet_id),
        "planet_coords": rec.planet_coords,
        "blocked_building_key": rec.blocked_building_key,
        "blocked_target_level": cap_json_int(rec.blocked_target_level),
        "candidate_building_key": rec.candidate_building_key,
        "candidate_level": cap_json_int(rec.candidate_level),
        "status": rec.status,
        "decision": rec.decision,
        "created_ts": rec.created_ts,
        "resolved_ts": rec.resolved_ts,
        "detail": _loads_dict(rec.detail_json),
    }


class _OpsGatewayMixin(_GatewayCore):
    """Manual queue, config optimizer, AI planner, and field-conflict persistence methods."""

    def get_manual_queue(self, *, status: str | None = "pending", limit: int = 200) -> list[dict[str, object]]:
        """List manual command queue items ordered by priority and `sort_rank`."""
        with self._store.repositories() as repos:
            rows = repos.queue.list_items(status=status, limit=limit)
        return [_queue_item_to_dict(r) for r in rows]

    def enqueue_manual_command(
        self,
        *,
        kind: str,
        priority: str = "P1",
        position: str = "top",
        planet_id: int = 0,
        planet_coords: str = "",
        target_id: int | None = None,
        target_name: str = "",
        target_coords: str = "",
        target_level: int | None = None,
        note: str = "",
        deduplicate_pending: bool = False,
        ts: float | None = None,
    ) -> dict[str, object]:
        """Enqueue a pending manual command (or reprioritize a duplicate) and record an event."""
        with self._store.repositories() as repos:
            rec = repos.queue.enqueue(
                kind=kind,
                priority=priority,
                position=position,
                planet_id=planet_id,
                planet_coords=planet_coords,
                target_id=target_id,
                target_name=target_name,
                target_coords=target_coords,
                target_level=target_level,
                note=note,
                deduplicate_pending=deduplicate_pending,
                ts=ts,
            )
        self._bump_change()
        return _queue_item_to_dict(rec)

    def update_manual_queue_item(
        self,
        queue_id: int,
        *,
        priority: str | None = None,
        move: str | None = None,
        sort_rank: int | None = None,
        target_level: int | None | object = ...,
        note: str | None = None,
        ts: float | None = None,
    ) -> dict[str, object] | None:
        """Update fields or reorder a manual queue item (`target_level=...` keeps existing value)."""
        with self._store.repositories() as repos:
            if target_level is ...:
                rec = repos.queue.update_item(
                    queue_id, priority=priority, move=move, sort_rank=sort_rank, note=note, ts=ts
                )
            else:
                rec = repos.queue.update_item(
                    queue_id,
                    priority=priority,
                    move=move,
                    sort_rank=sort_rank,
                    target_level=target_level,
                    note=note,
                    ts=ts,
                )
        if rec is not None:
            self._bump_change()
            return _queue_item_to_dict(rec)
        return None

    def cancel_manual_queue_item(
        self, queue_id: int, *, reason: str = "Canceled by operator", ts: float | None = None
    ) -> bool:
        """Cancel one pending manual queue item."""
        with self._store.repositories() as repos:
            ok = repos.queue.cancel_item(queue_id, reason=reason, ts=ts)
        if ok:
            self._bump_change()
        return ok

    def clear_manual_queue(self, *, reason: str = "Cleared by operator", ts: float | None = None) -> int:
        """Cancel all pending manual queue items and return the canceled count."""
        with self._store.repositories() as repos:
            cnt = repos.queue.clear_pending(reason=reason, ts=ts)
        if cnt > 0:
            self._bump_change()
        return cnt

    def complete_manual_queue_item(self, queue_id: int, *, message: str = "", ts: float | None = None) -> bool:
        """Mark one pending manual queue item completed."""
        with self._store.repositories() as repos:
            ok = repos.queue.complete_item(queue_id, message=message, ts=ts)
        if ok:
            self._bump_change()
        return ok

    def complete_matching_manual_command(
        self,
        *,
        kind: str,
        planet_id: int | None = None,
        target_id: int | None = None,
        target_coords: str = "",
        executed_level: int | None = None,
        executed_qty: int | None = None,
        message: str = "",
        ts: float | None = None,
    ) -> int | None:
        """Progress or complete the highest-priority pending queue item matching an executed action."""
        with self._store.session() as session:
            qid = complete_matching_queue_item(
                session,
                kind=kind,
                planet_id=planet_id,
                target_id=target_id,
                target_coords=target_coords,
                executed_level=executed_level,
                executed_qty=executed_qty,
                message=message,
                ts=ts,
            )
        if qid is not None:
            self._bump_change()
        return qid

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
        """Persist a Config Optimizer run and optionally promote it to active."""
        with self._store.repositories() as repos:
            run_id = repos.planner_runs.record_config_optimizer_run(
                result,
                params=params,
                reserved_floors=reserved_floors,
                trigger_source=trigger_source,
                elapsed_ms=elapsed_ms,
                set_active=set_active,
                ts=ts,
            )
        self._bump_change()
        return run_id

    def get_active_config_optimizer(self) -> dict[str, object] | None:
        """Read the active Config Optimizer snapshot (returns `None` if uninitialized)."""
        with self._store.repositories() as repos:
            rec = repos.planner_runs.get_active_config_optimizer()
        if rec is None:
            return None
        res_dict = _loads_dict(rec.result_json)
        if rec.run_id is None and not res_dict:
            return None
        return {
            "run_id": cap_json_int(rec.run_id) if rec.run_id is not None else None,
            "updated_ts": rec.updated_ts,
            "params": _loads_dict(rec.params_json),
            "reserved_floors": normalize_floors(_loads_dict(rec.reserved_floors_json)),
            "result": res_dict,
        }

    def list_config_optimizer_runs(self, limit: int = 20) -> list[dict[str, object]]:
        """List append-only Config Optimizer runs ordered newest first."""
        with self._store.repositories() as repos:
            rows = repos.planner_runs.list_config_optimizer_runs(limit=limit)
        return [_cfg_run_to_dict(r) for r in rows]

    def get_reserved_building_floors(self) -> dict[str, dict[str, int]]:
        """Return per-planet reserved minimum building floors."""
        with self._store.repositories() as repos:
            return repos.planner_runs.get_reserved_building_floors()

    def update_reserved_building_floor(
        self,
        planet_key: str,
        building_key: str,
        min_level: int,
        *,
        reason: str = "",
        ts: float | None = None,
    ) -> dict[str, dict[str, int]]:
        """Upsert or clear a per-planet minimum building floor."""
        del reason
        with self._store.repositories() as repos:
            floors = repos.planner_runs.update_reserved_building_floor(planet_key, building_key, min_level, ts=ts)
        self._bump_change()
        return floors

    def get_config_optimizer_specializations(self) -> dict[str, str]:
        """Return per-planet specialization locks."""
        with self._store.repositories() as repos:
            return repos.planner_runs.get_config_optimizer_specializations()

    def update_config_optimizer_specializations(
        self,
        specializations: Mapping[str, object],
        *,
        replace: bool = False,
        ts: float | None = None,
    ) -> dict[str, str]:
        """Upsert or replace per-planet specialization locks."""
        with self._store.repositories() as repos:
            specs = repos.planner_runs.update_config_optimizer_specializations(specializations, replace=replace, ts=ts)
        self._bump_change()
        return specs

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
        """Persist an AI-Planner run and optionally promote it to active."""
        with self._store.repositories() as repos:
            run_id = repos.planner_runs.record_ai_planner_run(
                result,
                config_run_id=config_run_id,
                params=params,
                trigger_source=trigger_source,
                elapsed_ms=elapsed_ms,
                set_active=set_active,
                ts=ts,
            )
        self._bump_change()
        return run_id

    def get_active_ai_planner(self) -> dict[str, object] | None:
        """Read the active AI-Planner snapshot."""
        with self._store.repositories() as repos:
            rec = repos.planner_runs.get_active_ai_planner()
        if rec is None:
            return None
        return {
            "run_id": cap_json_int(rec.run_id) if rec.run_id is not None else None,
            "config_run_id": cap_json_int(rec.config_run_id) if rec.config_run_id is not None else None,
            "updated_ts": rec.updated_ts,
            "params": _loads_dict(rec.params_json),
            "summary": _loads_dict(rec.summary_json),
            "result": _loads_dict(rec.result_json),
        }

    def list_ai_planner_runs(self, limit: int = 20) -> list[dict[str, object]]:
        """List append-only AI-Planner history runs ordered newest first."""
        with self._store.repositories() as repos:
            rows = repos.planner_runs.list_ai_planner_runs(limit=limit)
        return [_ai_run_to_dict(r) for r in rows]

    def upsert_field_conflict_prompt(
        self, conflict: Mapping[str, object], *, ts: float | None = None
    ) -> dict[str, object]:
        """Upsert a field-limit conflict prompt and append an audit event."""
        with self._store.repositories() as repos:
            rec = repos.planner_runs.upsert_field_conflict_prompt(conflict, ts=ts)
        self._bump_change()
        return _conflict_to_dict(rec)

    def resolve_field_conflict_prompt(
        self,
        conflict_key: str,
        decision: str,
        *,
        detail: Mapping[str, object] | None = None,
        ts: float | None = None,
    ) -> dict[str, object] | None:
        """Resolve a field-limit conflict prompt and append a `resolved` audit event."""
        with self._store.repositories() as repos:
            rec = repos.planner_runs.resolve_field_conflict_prompt(conflict_key, decision, detail=detail, ts=ts)
        if rec is not None:
            self._bump_change()
            return _conflict_to_dict(rec)
        return None

    def list_field_conflict_prompts(self, *, status: str | None = "pending") -> list[dict[str, object]]:
        """List field-limit conflict prompts filtered by status."""
        with self._store.repositories() as repos:
            rows = repos.planner_runs.list_field_conflict_prompts(status=status)
        return [_conflict_to_dict(r) for r in rows]


__all__ = ["_OpsGatewayMixin"]
