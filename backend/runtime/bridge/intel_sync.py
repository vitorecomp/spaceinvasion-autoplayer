"""Intel sync collaborator: galaxy scans, message/spy ingestion, colonies, and scanned sectors."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import time

from backend.core.config import StrategyConfig
from backend.core.model import GameState
from backend.parsing import (
    GalaxySlot,
    InboxMessage,
    SpyReport,
    extract_galaxy_system_coords,
    parse_galaxy_system_html,
)
from backend.runtime.bridge._context import BridgeContext
from backend.runtime.bridge._helpers import safe_api_int, sanitize_json_dict
from backend.runtime.bridge._intel_colonies import (
    build_colony_candidates_payload,
    merge_colony_candidates_for_ctx,
)
from backend.runtime.bridge._intel_colony_ledger import (
    build_colony_attempts_payload,
    hydrate_colony_attempt_seen_for_ctx,
    record_colony_dispatch_outcome_for_ctx,
    record_new_colonies_for_ctx,
)
from backend.runtime.bridge._intel_messages import (
    ingest_page_messages_for_ctx,
    own_planet_coords_for_ctx,
    persist_spy_reports_for_ctx,
)
from backend.runtime.bridge._intel_sectors import (
    max_galaxies_for_ctx,
    observe_galaxy_universe_bounds_for_ctx,
    persist_galaxy_slots_for_ctx,
)
from backend.runtime.bridge._intel_sectors_view import build_scanned_sectors_payload
from backend.runtime.bridge._intel_spio_sync import sync_spio_if_unread_for_ctx
from backend.runtime.bridge._intel_sync_ops import ingest_messages_for_ctx
from backend.runtime.bridge.state_cache import StateCacheService
from backend.session.client import GameBrowserClient
from backend.session.executor import ExecutionResult


class IntelSyncService:
    """Owns galaxy slot persistence, inbox/spy message sync, colony ledger, and sector analytics."""

    def __init__(self, ctx: BridgeContext, state_cache: StateCacheService) -> None:
        self._ctx = ctx
        self._state_cache = state_cache
        self._on_transport_delivery_fn: Callable[[InboxMessage], None] | None = None
        self._on_publish_snapshot: Callable[[], None] | None = None
        self._on_persist_runtime_state: Callable[[], None] | None = None
        self._on_plan_smart_sweep: Callable[..., dict[str, object] | None] | None = None
        self._on_store_smart_summary: Callable[..., dict[str, object]] | None = None
        self._on_get_sweep_state: Callable[..., dict[str, object]] | None = None

    def bind_hooks(
        self,
        *,
        on_transport_delivery_fn: Callable[[InboxMessage], None] | None = None,
        on_publish_snapshot: Callable[[], None] | None = None,
        on_persist_runtime_state: Callable[[], None] | None = None,
        on_plan_smart_sweep: Callable[..., dict[str, object] | None] | None = None,
        on_store_smart_summary: Callable[..., dict[str, object]] | None = None,
        on_get_sweep_state: Callable[..., dict[str, object]] | None = None,
    ) -> None:
        """Bind optional post-construction collaborator callbacks explicitly."""
        if on_transport_delivery_fn is not None:
            self._on_transport_delivery_fn = on_transport_delivery_fn
        if on_publish_snapshot is not None:
            self._on_publish_snapshot = on_publish_snapshot
        if on_persist_runtime_state is not None:
            self._on_persist_runtime_state = on_persist_runtime_state
        if on_plan_smart_sweep is not None:
            self._on_plan_smart_sweep = on_plan_smart_sweep
        if on_store_smart_summary is not None:
            self._on_store_smart_summary = on_store_smart_summary
        if on_get_sweep_state is not None:
            self._on_get_sweep_state = on_get_sweep_state

    def own_planet_coords(self, state: GameState | None = None) -> set[str]:
        """Return normalized coordinates of all owned planets."""
        return own_planet_coords_for_ctx(self._ctx, state)

    def max_galaxies_locked(self) -> int:
        """Return observed universe max galaxy count (1..14)."""
        return max_galaxies_for_ctx(self._ctx)

    def observe_galaxy_universe_bounds_locked(
        self,
        client: GameBrowserClient | None = None,
        html: str | None = None,
    ) -> int:
        """Observe `maxGalaxies` from client or HTML and update cached universe bounds."""
        return observe_galaxy_universe_bounds_for_ctx(self._ctx, client=client, html=html)

    def persist_galaxy_scan(self, galaxy: int, system: int, slots: Sequence[GalaxySlot] = ()) -> None:
        """Record a solar-system galaxy scan timestamp and slot list."""
        now_ts = round(time.time(), 3)
        skey = f"{int(galaxy):02d}:{int(system):03d}"
        self._ctx.scanned_systems[skey] = now_ts
        if self._ctx.db is not None:
            self._ctx.db.record_galaxy_scan(int(galaxy), int(system), slots, ts=now_ts)

    def persist_galaxy_slots(self, slots: Sequence[GalaxySlot], *, refresh_targets: bool = True) -> int:
        """Persist parsed galaxy slots and debris amounts to DB or in-memory raid targets."""
        return persist_galaxy_slots_for_ctx(self._ctx, self._state_cache, slots, refresh_targets=refresh_targets)

    def record_galaxy_scan_html_locked(
        self,
        state: GameState,
        html: str,
        *,
        galaxy: int | None = None,
        system: int | None = None,
    ) -> dict[str, object]:
        """Parse and persist a live `internalGalaxy` HTML document into scan/slot caches."""
        self.observe_galaxy_universe_bounds_locked(html=html)
        default_gal = max(1, safe_api_int(galaxy, 1)) if galaxy is not None else 1
        default_sys = max(1, min(400, safe_api_int(system, 1))) if system is not None else 1
        eff_gal, eff_sys = extract_galaxy_system_coords(html, default=(default_gal, default_sys))
        slots = parse_galaxy_system_html(html, galaxy=eff_gal, system=eff_sys)
        self.persist_galaxy_scan(eff_gal, eff_sys, slots)
        saved = self.persist_galaxy_slots(slots, refresh_targets=True)
        self._state_cache.refresh_cached_economy(state)
        self._ctx.invalidate_dashboard_cache()
        if self._on_publish_snapshot is not None:
            self._on_publish_snapshot()
        return sanitize_json_dict(
            {
                "galaxy": eff_gal,
                "system": eff_sys,
                "sector": f"{eff_gal:02d}:{eff_sys:03d}",
                "slots_found": len(slots),
                "saved_slots": saved,
                "debris_found": sum(1 for s in slots if s.has_debris),
                "free_slots": sum(1 for s in slots if s.status_color == "free"),
            }
        )

    def persist_spy_reports(self, reports: Sequence[SpyReport], *, exclude_coords: set[str] | None = None) -> int:
        """Persist spy reports excluding own planets."""
        return persist_spy_reports_for_ctx(self._ctx, self._state_cache, reports, exclude_coords=exclude_coords)

    def ingest_and_delete_page_messages(
        self,
        client: GameBrowserClient | None,
        html: str,
        *,
        folder_type: str = "",
        own_coords: set[str] | None = None,
        priority: str = "normal",
        always_persist_spy: bool = False,
        delete_after_persist: bool = True,
    ) -> tuple[list[SpyReport], int, bool, str]:
        """Parse, archive, and optionally delete messages from a single messages page."""
        return ingest_page_messages_for_ctx(
            self._ctx,
            self._state_cache,
            client,
            html,
            folder_type=folder_type,
            own_coords=own_coords,
            priority=priority,
            always_persist_spy=always_persist_spy,
            delete_after_persist=delete_after_persist,
            on_transport_delivery_fn=self._on_transport_delivery_fn,
        )

    def sync_spio_if_unread(
        self,
        client: GameBrowserClient | None,
        state: GameState,
        *,
        force: bool = False,
        priority: str = "normal",
        get_client: Callable[[], GameBrowserClient] | None = None,
        ignore_auth_error: bool = False,
    ) -> int:
        """Archive unread spy and combat messages when badges or pending spy dispatches indicate work."""
        return sync_spio_if_unread_for_ctx(
            self._ctx,
            self._state_cache,
            client,
            state,
            force=force,
            priority=priority,
            get_client=get_client,
            ignore_auth_error=ignore_auth_error,
            on_transport_delivery_fn=self._on_transport_delivery_fn,
            on_persist_runtime_state=self._on_persist_runtime_state,
        )

    def ingest_messages_locked(
        self,
        client: GameBrowserClient,
        state: GameState,
        *,
        include_folders: bool = True,
        delete_processed: bool = False,
        priority: str = "interactive",
    ) -> dict[str, object]:
        """Run interactive multi-folder message archival (`sync_messages` command implementation)."""
        return ingest_messages_for_ctx(
            self._ctx,
            self._state_cache,
            client,
            state,
            include_folders=include_folders,
            delete_processed=delete_processed,
            priority=priority,
            on_transport_delivery_fn=self._on_transport_delivery_fn,
            on_publish_snapshot=self._on_publish_snapshot,
        )

    def merge_colony_candidates_if_needed(self, state: GameState, cfg: StrategyConfig) -> GameState:
        """Merge uninhabited colony candidates into `state.raid_targets` when expansion is enabled."""
        return merge_colony_candidates_for_ctx(self._ctx, state, cfg)

    def hydrate_colony_attempt_seen(self) -> None:
        """Hydrate seen colony attempt cache from DB."""
        hydrate_colony_attempt_seen_for_ctx(self._ctx)

    def record_new_colonies_locked(self, prev: GameState | None, cur: GameState) -> None:
        """Record newly settled/kept secondary colonies into the colony trial ledger."""
        record_new_colonies_for_ctx(self._ctx, prev, cur)

    def record_colony_dispatch_outcome_locked(self, state: GameState, res: ExecutionResult, cmd: str) -> None:
        """Record `COLONIZE` or `ABANDON_COLONY` outcomes into the colony trial ledger."""
        record_colony_dispatch_outcome_for_ctx(self._ctx, state, res, cmd)

    def colony_attempts_payload(self) -> dict[str, object]:
        """Return colony trial ledger summary mapping."""
        return build_colony_attempts_payload(self._ctx)

    def get_colony_candidates_payload(
        self,
        *,
        limit: int = 50,
        min_fields: int | None = None,
        include_discarded: bool = False,
    ) -> dict[str, object]:
        """Build the `colony_candidates` API response payload."""
        return build_colony_candidates_payload(
            self._ctx, limit=limit, min_fields=min_fields, include_discarded=include_discarded
        )

    def get_scanned_sectors_payload(
        self,
        *,
        galaxy: int | None = None,
        limit: int = 200,
        include_unscanned: bool | None = None,
        planet_id: int | None = None,
        preview_refresh_pct: int | None = None,
        preview_sweep_pages: int | None = None,
        preview_all_galaxies: bool | None = None,
    ) -> dict[str, object]:
        """Build the `scanned_sectors` API response payload."""
        return build_scanned_sectors_payload(
            self._ctx,
            galaxy=galaxy,
            limit=limit,
            include_unscanned=include_unscanned,
            planet_id=planet_id,
            preview_refresh_pct=preview_refresh_pct,
            preview_sweep_pages=preview_sweep_pages,
            preview_all_galaxies=preview_all_galaxies,
            on_plan_smart_sweep=self._on_plan_smart_sweep,
            on_store_smart_summary=self._on_store_smart_summary,
            on_get_sweep_state=self._on_get_sweep_state,
        )
