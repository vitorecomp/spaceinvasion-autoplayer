"""Action planning, action-graph assembly, manual queue review, and action finder collaborator."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from backend.core.config import StrategyConfig
from backend.core.model import GameState, PlannedAction
from backend.planner.graph import build_action_graph
from backend.planner.heuristics import plan_actions
from backend.runtime.bridge._context import BridgeContext
from backend.runtime.bridge._helpers import sanitize_json_dict
from backend.runtime.bridge._helpers_sig import redact_action_graph_strings
from backend.runtime.bridge._plan_finders import find_build_action, find_demolish_action
from backend.runtime.bridge._plan_finders_research import find_research_action
from backend.runtime.bridge._plan_finders_ships import find_ship_action
from backend.runtime.bridge._plan_helpers import (
    has_urgent_p0_escape_flight,
    serialize_planned_action,
)
from backend.runtime.bridge._plan_hybrid import (
    apply_manual_queue_to_plan,
    plan_hybrid_ai_actions,
    record_manual_queue_metal_review,
    review_queue_metal_status,
)
from backend.runtime.bridge._planner_jobs_telemetry import evaluate_ai_planner_gate
from backend.runtime.bridge.ledgers import LedgerTelemetryService
from backend.runtime.bridge.planet_names import PlanetNameService
from backend.runtime.bridge.planner_jobs import PlannerJobService


class PlanAssemblyService:
    """Assembles heuristic and hybrid AI action plans, action graphs, manual queue reviews, and direct action finders."""

    def __init__(
        self,
        ctx: BridgeContext,
        names: PlanetNameService,
        ledgers: LedgerTelemetryService,
        *,
        planner_jobs: PlannerJobService | None = None,
        on_persist_runtime_state: Callable[[], None] | None = None,
    ) -> None:
        self._ctx = ctx
        self._names = names
        self._ledgers = ledgers
        self._planner_jobs = planner_jobs
        self._on_persist_runtime_state = on_persist_runtime_state
        self._evaluate_gate_cb: Callable[[GameState, StrategyConfig], Mapping[str, object]] | None = None
        self._upsert_conflict_cb: Callable[[Mapping[str, object]], dict[str, object]] | None = None
        self._live_telemetry_cb: Callable[[GameState], dict[str, object]] | None = None

    def bind_planner_callbacks(
        self,
        *,
        planner_jobs: PlannerJobService | None = None,
        evaluate_gate_fn: Callable[[GameState, StrategyConfig], Mapping[str, object]] | None = None,
        upsert_field_conflict_fn: Callable[[Mapping[str, object]], dict[str, object]] | None = None,
        compute_live_attack_telemetry_fn: Callable[[GameState], dict[str, object]] | None = None,
        on_persist_runtime_state: Callable[[], None] | None = None,
    ) -> None:
        """Bind `PlannerJobService` or explicit planner callbacks post-construction."""
        if planner_jobs is not None:
            self._planner_jobs = planner_jobs
        if evaluate_gate_fn is not None:
            self._evaluate_gate_cb = evaluate_gate_fn
        if upsert_field_conflict_fn is not None:
            self._upsert_conflict_cb = upsert_field_conflict_fn
        if compute_live_attack_telemetry_fn is not None:
            self._live_telemetry_cb = compute_live_attack_telemetry_fn
        if on_persist_runtime_state is not None:
            self._on_persist_runtime_state = on_persist_runtime_state

    def _evaluate_gate(self, state: GameState, cfg: StrategyConfig) -> Mapping[str, object]:
        if self._evaluate_gate_cb is not None:
            return self._evaluate_gate_cb(state, cfg)
        if self._planner_jobs is not None:
            return self._planner_jobs.evaluate_ai_planner_gate_locked(state, cfg=cfg)
        return evaluate_ai_planner_gate(self._ctx, state, cfg=cfg)

    def _upsert_conflict(self, conflict: Mapping[str, object]) -> dict[str, object]:
        if self._upsert_conflict_cb is not None:
            return self._upsert_conflict_cb(conflict)
        if self._planner_jobs is not None:
            return self._planner_jobs.upsert_field_conflict_locked(conflict)
        return dict(conflict)

    def _compute_live_telemetry(self, state: GameState) -> dict[str, object]:
        if self._live_telemetry_cb is not None:
            return self._live_telemetry_cb(state)
        if self._planner_jobs is not None:
            return self._planner_jobs.compute_live_attack_telemetry_locked(state)
        return {}

    def serialize_action(
        self,
        action: PlannedAction,
        state: GameState | None = None,
    ) -> dict[str, object]:
        """Serialize a `PlannedAction` with ETAs, display planet name, and finite JSON sanitization."""
        return serialize_planned_action(self._ctx, action, state=state)

    def plan_hybrid_ai_actions_locked(
        self,
        enriched_state: GameState,
        active_result: Mapping[str, object],
        cfg: StrategyConfig,
        *,
        record_conflicts: bool = False,
    ) -> list[PlannedAction]:
        """Synthesize prioritized hybrid AI-Planner actions for `enriched_state`."""
        return plan_hybrid_ai_actions(
            self._ctx,
            self._ledgers,
            enriched_state,
            active_result,
            cfg,
            record_conflicts=record_conflicts,
            on_upsert_field_conflict=self._upsert_conflict,
            on_compute_live_attack_telemetry=self._compute_live_telemetry,
            on_persist_runtime_state=self._on_persist_runtime_state,
        )

    def plan_with_summary(
        self,
        state: GameState,
        *,
        config: StrategyConfig | None = None,
        include_manual_queue: bool = True,
        record_summary: bool = True,
        already_enriched: bool = False,
    ) -> tuple[list[PlannedAction], dict[str, object] | None]:
        """Produce the prioritized action plan (hybrid AI-Planner or heuristic) and optional summary."""
        cfg = config or self._ctx.strategy_config
        enriched_state = state if already_enriched else self._ledgers.attach_raid_stats(state)
        if config is not None:
            enriched_state = self._ledgers.merge_colony_candidates_if_needed(enriched_state, cfg)
        gate = self._evaluate_gate(enriched_state, cfg)
        eff_mode = gate.get("effective_planner_mode")
        active_result = gate.get("active_result")
        persist_auto = bool(record_summary and config is None)

        if eff_mode == "ai_planner" and isinstance(active_result, Mapping):
            actions = self.plan_hybrid_ai_actions_locked(
                enriched_state,
                active_result,
                cfg,
                record_conflicts=persist_auto,
            )
            actions = self._ledgers.apply_fleet_slot_cooldowns(actions, re_sort_priority=False)
            actions = self._ledgers.apply_fleet_slot_holds(enriched_state, actions, cfg=cfg, re_sort_priority=False)
            if include_manual_queue:
                actions = apply_manual_queue_to_plan(
                    self._ctx,
                    enriched_state,
                    actions,
                    cfg=cfg,
                    persist_auto_satisfied=persist_auto,
                    reconcile_metal_production=False,
                )
            return actions, None

        actions = plan_actions(
            enriched_state,
            config=cfg,
            scanned_systems=self._ledgers.get_scanned_systems(),
            throttle_cooldowns=self._ledgers.get_throttle_cooldowns(),
        )
        actions = self._ledgers.apply_fleet_slot_cooldowns(actions, re_sort_priority=True)
        actions = self._ledgers.apply_fleet_slot_holds(enriched_state, actions, cfg=cfg, re_sort_priority=True)
        if include_manual_queue:
            actions = apply_manual_queue_to_plan(
                self._ctx,
                enriched_state,
                actions,
                cfg=cfg,
                persist_auto_satisfied=persist_auto,
                reconcile_metal_production=True,
            )
        return actions, None

    def plan_with_context(
        self,
        state: GameState,
        *,
        config: StrategyConfig | None = None,
    ) -> list[PlannedAction]:
        """Return the prioritized `PlannedAction` list for `state`."""
        actions, _ = self.plan_with_summary(state, config=config)
        return actions

    def build_action_graph_payload(
        self,
        state: GameState | None,
        *,
        config: StrategyConfig | None = None,
        include_manual_queue: bool = True,
        max_nodes: int = 36,
        planet_id: int | None = None,
        simulated: bool = False,
        record_summary: bool = True,
    ) -> dict[str, object]:
        """Build the sanitized, secret-redacted Action Graph DAG dict for `state`."""
        _ = max_nodes
        effective_cfg = config or self._ctx.strategy_config
        custom_names = self._names.get_custom_planet_names_locked()
        ag_raw_state = self._ledgers.attach_raid_stats(state) if state is not None else None
        ag_state = (
            self._names.apply_custom_planet_names_to_state(ag_raw_state, custom_names)
            if ag_raw_state is not None
            else None
        )
        if ag_state is not None and planet_id is not None:
            self._ctx.resolve_planet(ag_state, planet_id)
        if ag_state is not None:
            ag_actions, ag_opt_summary = self.plan_with_summary(
                ag_state,
                config=effective_cfg,
                include_manual_queue=include_manual_queue,
                record_summary=record_summary,
                already_enriched=True,
            )
        else:
            ag_actions, ag_opt_summary = [], None
        graph_obj = build_action_graph(
            ag_state,
            ag_actions,
            effective_config=effective_cfg,
            active_config=self._ctx.strategy_config,
            simulated=simulated,
            optimizer_summary=ag_opt_summary,
            planet_filter=planet_id,
            sanitize=False,
        )
        redacted = redact_action_graph_strings(dict(graph_obj))
        return sanitize_json_dict(redacted)

    def review_queue_metal_status_locked(
        self,
        planet_id: int,
        pending_mq: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        """Inspect Blast furnace Metal production status vs pinned manual queue position on `planet_id`."""
        return review_queue_metal_status(
            self._ctx,
            self._ledgers,
            planet_id,
            pending_mq,
            evaluate_gate_fn=self._evaluate_gate,
        )

    def record_manual_queue_metal_review(
        self,
        queue_items: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        """Compute per-planet Blast furnace Metal production reviews across `queue_items`."""
        return record_manual_queue_metal_review(
            self._ctx,
            self._ledgers,
            queue_items,
            evaluate_gate_fn=self._evaluate_gate,
        )

    def has_urgent_p0_escape_flight_locked(self) -> tuple[bool, str]:
        """Return `(True, reason)` if an urgent P0 escape flight should preempt galaxy sweeps."""
        return has_urgent_p0_escape_flight(self._ctx)

    def find_build_action(
        self,
        state: GameState,
        building_id: int,
        planet_id: int | None,
        *,
        allow_live_verify: bool = False,
    ) -> PlannedAction:
        """Resolve an explicit `BUILD` `PlannedAction` for `building_id` on `planet_id`."""
        return find_build_action(state, building_id, planet_id, allow_live_verify=allow_live_verify)

    def find_demolish_action(
        self,
        state: GameState,
        building_id: int,
        planet_id: int | None,
    ) -> PlannedAction:
        """Resolve an explicit `DEMOLISH_BUILDING` `PlannedAction` for `building_id` on `planet_id`."""
        return find_demolish_action(state, building_id, planet_id)

    def find_research_action(
        self,
        state: GameState,
        research_key: str | int,
        planet_id: int | None = None,
        *,
        allow_live_verify: bool = False,
    ) -> PlannedAction:
        """Resolve an explicit `RESEARCH` `PlannedAction` for `research_key` on `planet_id`."""
        return find_research_action(state, research_key, planet_id, allow_live_verify=allow_live_verify)

    def find_ship_action(
        self,
        state: GameState,
        ship_key: str | int,
        count: int,
        planet_id: int | None = None,
        *,
        allow_live_verify: bool = False,
    ) -> PlannedAction:
        """Resolve an explicit `BUILD_SHIP` `PlannedAction` for `ship_key` x `count` on `planet_id`."""
        return find_ship_action(state, ship_key, count, planet_id, allow_live_verify=allow_live_verify)
