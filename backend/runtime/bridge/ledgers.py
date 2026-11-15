"""Cooldowns, transmitter state, transport/spend ledgers, and telemetry collaborator."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from backend.core.config import StrategyConfig
from backend.core.model import GameState, PlanetState, PlannedAction, Resources
from backend.runtime.bridge import (
    _ledgers_adaptive as _adaptive,
)
from backend.runtime.bridge import (
    _ledgers_budgets as _budgets,
)
from backend.runtime.bridge import (
    _ledgers_fleet as _fleet,
)
from backend.runtime.bridge import (
    _ledgers_production as _prod,
)
from backend.runtime.bridge import (
    _ledgers_queue_enrich as _qenrich,
)
from backend.runtime.bridge import (
    _ledgers_requests as _reqs,
)
from backend.runtime.bridge import (
    _ledgers_telemetry as _tel,
)
from backend.runtime.bridge import (
    _ledgers_transmitter as _tx,
)
from backend.runtime.bridge import (
    _ledgers_transport as _tr,
)
from backend.runtime.bridge import (
    _ledgers_transport_util as _tr_util,
)
from backend.runtime.bridge._context import BridgeContext


class LedgerTelemetryService:
    """Owns bridge cooldowns, transmitter state, rolling spend ledgers, and telemetry projections."""

    def __init__(self, ctx: BridgeContext, *, on_persist_runtime_state: Callable[[], None] | None = None) -> None:
        self._ctx = ctx
        self._on_persist_runtime_state = on_persist_runtime_state

    def bind_hooks(self, *, on_persist_runtime_state: Callable[[], None] | None = None) -> None:
        """Bind post-construction runtime persistence callback."""
        if on_persist_runtime_state is not None:
            self._on_persist_runtime_state = on_persist_runtime_state

    def get_scanned_systems(self) -> dict[str, float]:
        return dict(self._ctx.db.get_galaxy_scans()) if self._ctx.db is not None else dict(self._ctx.scanned_systems)

    def get_throttle_cooldowns(self) -> dict[int, float]:
        return (
            dict(self._ctx.db.get_last_throttle_timestamps())
            if self._ctx.db is not None
            else dict(self._ctx.throttle_timestamps)
        )

    def get_fleet_slot_cooldowns(self) -> dict[str, float]:
        return _fleet.get_fleet_slot_cooldowns(self._ctx)

    def apply_fleet_slot_cooldowns(
        self, actions: list[PlannedAction], *, re_sort_priority: bool = False
    ) -> list[PlannedAction]:
        return _fleet.apply_fleet_slot_cooldowns(self._ctx, actions, re_sort_priority=re_sort_priority)

    def apply_fleet_slot_holds(
        self,
        state: GameState,
        actions: list[PlannedAction],
        *,
        cfg: StrategyConfig | None = None,
        re_sort_priority: bool = False,
    ) -> list[PlannedAction]:
        return _fleet.apply_fleet_slot_holds(self._ctx, state, actions, cfg=cfg, re_sort_priority=re_sort_priority)

    def compute_fleet_slot_busy_cooldown_s(self, state: GameState, planet: PlanetState | None) -> float:
        return _fleet.compute_fleet_slot_busy_cooldown_s(state, planet)

    def record_observed_fleet_slots_locked(self, planet: PlanetState | None, available: int | None) -> None:
        _fleet.record_observed_fleet_slots(self._ctx, planet, available)

    def max_fleet_slots_for_planet_locked(self, planet: PlanetState | None) -> int:
        return _fleet.max_fleet_slots_for_planet(self._ctx, planet)

    def get_transmitter_status_locked(self, *, now_ts: float | None = None) -> dict[str, object]:
        return _tx.get_transmitter_status(self._ctx, now_ts=now_ts)

    def record_transmitter_state_locked(
        self,
        *,
        activated: bool,
        free_spins_remaining: int | None = None,
        reward: str = "",
        daily_bonus_checked: bool | None = None,
        daily_bonus_collected: bool | None = None,
        daily_bonus_available: bool | None = None,
        ts: float | None = None,
    ) -> dict[str, object]:
        return _tx.record_transmitter_state(
            self._ctx,
            activated=activated,
            free_spins_remaining=free_spins_remaining,
            reward=reward,
            daily_bonus_checked=daily_bonus_checked,
            daily_bonus_collected=daily_bonus_collected,
            daily_bonus_available=daily_bonus_available,
            ts=ts,
        )

    def note_daily_login_bonus_seen_locked(self, present: bool, *, ts: float | None = None) -> dict[str, object]:
        return _tx.note_daily_login_bonus_seen(self._ctx, present, ts=ts)

    def note_transmitter_activation_locked(
        self,
        *,
        reward: str = "",
        free_spins: int | None = None,
        daily_bonus_collected: bool = False,
        activated: bool = True,
        daily_bonus_checked: bool = True,
        ts: float | None = None,
    ) -> dict[str, object]:
        return _tx.record_transmitter_state(
            self._ctx,
            activated=activated,
            free_spins_remaining=free_spins,
            reward=reward,
            daily_bonus_checked=daily_bonus_checked,
            daily_bonus_collected=daily_bonus_collected,
            daily_bonus_available=False,
            ts=ts,
        )

    def record_transport_dispatch_locked(
        self,
        *,
        origin_coords: str,
        dest_coords: str,
        cargo: object,
        ships: object = "",
        source: str = "dispatch",
        ts: float | None = None,
    ) -> None:
        _tr.record_transport_dispatch(
            self._ctx,
            origin_coords=origin_coords,
            dest_coords=dest_coords,
            cargo=cargo,
            ships=ships,
            source=source,
            ts=ts,
        )

    def capture_observed_outbound_transports_locked(self, state: GameState) -> None:
        _tr.capture_observed_outbound_transports(self._ctx, state)

    def match_recent_transport_dispatch_locked(
        self,
        origin_coords: str,
        dest_coords: str,
        *,
        ships_detail: str = "",
        used_indices: set[int] | None = None,
        max_age_seconds: float = 86400.0,
    ) -> tuple[int, Resources] | None:
        return _tr.match_recent_transport_dispatch(
            self._ctx,
            origin_coords,
            dest_coords,
            ships_detail=ships_detail,
            used_indices=used_indices,
            max_age_seconds=max_age_seconds,
        )

    def enrich_active_queues_locked(self, state: GameState) -> GameState:
        return _qenrich.enrich_active_queues(self._ctx, state, on_persist_runtime_state=self._on_persist_runtime_state)

    def prune_delivered_transport_events_locked(self, now_ts: float | None = None) -> None:
        _tr_util.prune_delivered_transport_events(self._ctx, now_ts)

    def compute_delivered_transport_stats_locked(self, now_ts: float | None = None) -> dict[str, object]:
        return _tr_util.compute_delivered_transport_stats(self._ctx, now_ts)

    def recompute_transport_utilization_locked(
        self, now_ts: float | None = None, *, rt_per_hour: float = 2.5, fallback_fleet_cargo: int = 0
    ) -> float:
        return _tr_util.recompute_transport_utilization(
            self._ctx, now_ts, rt_per_hour=rt_per_hour, fallback_fleet_cargo=fallback_fleet_cargo
        )

    def observe_transport_utilization_locked(self, state: GameState | None, *, now_ts: float | None = None) -> bool:
        return _tr_util.observe_transport_utilization(self._ctx, state, now_ts=now_ts)

    def build_transport_fleet_summary_locked(
        self, state: GameState | None, transport_demand: Mapping[str, object] | None
    ) -> dict[str, object] | None:
        return _tr_util.build_transport_fleet_summary(
            self._ctx,
            state,
            transport_demand,
            ordered_cargo_last_hour=_budgets.get_infra_cargo_ordered_last_hour(self._ctx),
        )

    def prune_infra_transport_spend_locked(self, now_ts: float | None = None) -> None:
        _budgets.prune_infra_transport_spend(self._ctx, now_ts)

    def get_infra_transport_spend_last_hour_locked(self, now_ts: float | None = None) -> tuple[dict[str, float], float]:
        return _budgets.get_infra_transport_spend_last_hour(self._ctx, now_ts)

    def get_infra_cargo_ordered_last_hour_locked(self, now_ts: float | None = None) -> int:
        return _budgets.get_infra_cargo_ordered_last_hour(self._ctx, now_ts)

    def record_infra_transport_spend_locked(
        self, action: PlannedAction, *, now_ts: float | None = None, quantity: int | None = None
    ) -> None:
        _budgets.record_infra_transport_spend(self._ctx, action, now_ts=now_ts, quantity=quantity)

    def prune_spare_ship_spend_locked(self, now_ts: float | None = None) -> None:
        _budgets.prune_spare_ship_spend(self._ctx, now_ts)

    def get_spare_ship_spend_in_window_locked(self, now_ts: float | None = None) -> dict[str, float]:
        return _budgets.get_spare_ship_spend_in_window(self._ctx, now_ts)

    def record_spare_ship_spend_locked(
        self, action: PlannedAction, *, now_ts: float | None = None, quantity: int | None = None
    ) -> None:
        _budgets.record_spare_ship_spend(self._ctx, action, now_ts=now_ts, quantity=quantity)

    def prune_adaptive_idle_buckets_locked(self, now_ts: float | None = None) -> None:
        _adaptive.prune_adaptive_idle_buckets(self._ctx, now_ts)

    def prune_all_ledgers(self, now_ts: float | None = None) -> None:
        """Prune all rolling transport, infra-spend, spare-ship, and adaptive-idle ledgers."""
        _tr_util.prune_delivered_transport_events(self._ctx, now_ts)
        _budgets.prune_infra_transport_spend(self._ctx, now_ts)
        _budgets.prune_spare_ship_spend(self._ctx, now_ts)
        _adaptive.prune_adaptive_idle_buckets(self._ctx, now_ts)

    def record_adaptive_idle_bucket_locked(
        self,
        state: GameState | None,
        active_plan_result: Mapping[str, object] | None,
        *,
        ai_actions: Sequence[PlannedAction] = (),
        cfg: StrategyConfig | None = None,
        field_conflicts: Sequence[Mapping[str, object]] = (),
        inbound_cargo_by_planet: Mapping[int, Resources] | None = None,
        now_ts: float | None = None,
    ) -> bool:
        return _adaptive.record_adaptive_idle_bucket(
            self._ctx,
            state,
            active_plan_result,
            ai_actions=ai_actions,
            cfg=cfg,
            field_conflicts=field_conflicts,
            inbound_cargo_by_planet=inbound_cargo_by_planet,
            now_ts=now_ts,
        )

    def summarize_adaptive_idle_window_24h_locked(self, now_ts: float | None = None) -> dict[str, object]:
        return _adaptive.summarize_adaptive_idle_window_24h(self._ctx, now_ts)

    def build_adaptive_status_locked(
        self,
        state: GameState | None,
        active_result: Mapping[str, object] | None,
        *,
        cfg: StrategyConfig | None = None,
        field_conflicts: Sequence[Mapping[str, object]] = (),
        custom_names: Mapping[str, str] | None = None,
        transport_fleet: dict[str, object] | None = None,
        auto_replan: dict[str, object] | None = None,
        now_ts: float | None = None,
    ) -> dict[str, object]:
        return _adaptive.build_adaptive_status(
            self._ctx,
            state,
            active_result,
            cfg=cfg,
            field_conflicts=field_conflicts,
            custom_names=custom_names,
            transport_fleet=transport_fleet,
            auto_replan=auto_replan,
            now_ts=now_ts,
        )

    def compute_infra_transport_budget_locked(self, *, now_ts: float | None = None) -> dict[str, object]:
        return _budgets.compute_infra_transport_budget(self._ctx, now_ts=now_ts)

    def compute_spare_ship_budget_locked(
        self,
        state: GameState,
        spare_pct: Mapping[str, float] | float | None,
        *,
        attack_telemetry: Mapping[str, object] | None = None,
        reserved_cost: Resources | Mapping[str, object] | None = None,
        adaptive_execution: bool = False,
        config: StrategyConfig | None = None,
        now_ts: float | None = None,
    ) -> dict[str, object]:
        return _budgets.compute_spare_ship_budget_for_ctx(
            self._ctx,
            state,
            spare_pct,
            attack_telemetry=attack_telemetry,
            reserved_cost=reserved_cost,
            adaptive_execution=adaptive_execution,
            config=config,
            now_ts=now_ts,
        )

    @staticmethod
    def count_active_builders(state: GameState | None, *, now_ts: float | None = None) -> int:
        return _prod.count_active_builders(state, now_ts=now_ts)

    @staticmethod
    def compute_current_hourly_production(state: GameState | None, *, now_ts: float | None = None) -> dict[str, object]:
        return _prod.compute_current_hourly_production(state, now_ts=now_ts)

    def record_hourly_production_locked(
        self, state: GameState | None, *, now_ts: float | None = None
    ) -> dict[str, object]:
        return _prod.record_hourly_production(self._ctx, state, now_ts=now_ts)

    def get_hourly_production_history_locked(
        self, hours: int = 168, *, limit: int | None = None
    ) -> list[dict[str, object]]:
        return _prod.get_hourly_production_history(self._ctx, hours=hours, limit=limit)

    def get_loot_recycle_hourly_locked(self, hours: int = 168, *, now_ts: float | None = None) -> dict[str, object]:
        return _prod.get_loot_recycle_hourly(self._ctx, hours=hours, now_ts=now_ts)

    def get_realized_loot_hourly_locked(self) -> dict[str, object] | None:
        return _prod.get_realized_loot_hourly(self._ctx)

    def overlay_realized_loot_locked(self, atk_tel: Mapping[str, object] | None) -> dict[str, object]:
        return _prod.overlay_realized_loot(self._ctx, atk_tel)

    @staticmethod
    def telemetry_income_basis(tel: object) -> str:
        return _prod.telemetry_income_basis(tel)

    def get_db_summary_cached_locked(self) -> dict[str, object] | None:
        return _tel.get_db_summary_cached(self._ctx)

    def build_building_resource_requests_locked(
        self,
        state: GameState | None,
        actions: Sequence[PlannedAction],
        early_needs: Mapping[int, Mapping[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        return _reqs.build_building_resource_requests(state, actions, early_needs)

    def get_raid_stats_cached(self, *, include_telemetry_summary: bool = True) -> dict[str, object]:
        return _tel.get_raid_stats_cached(self._ctx, include_telemetry_summary=include_telemetry_summary)

    def merge_colony_candidates_if_needed(self, state: GameState, cfg: StrategyConfig) -> GameState:
        return _tel.merge_colony_candidates_if_needed(self._ctx, state, cfg)

    def attach_raid_stats(self, state: GameState) -> GameState:
        return _tel.attach_raid_stats(self._ctx, state, on_persist_runtime_state=self._on_persist_runtime_state)
