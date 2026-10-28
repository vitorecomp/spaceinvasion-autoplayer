"""Snapshot reading, state caching, economy rate merging, and raid-target enrichment collaborator."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import time

from spaceinvasion.core.model import GameState
from spaceinvasion.runtime.api.validation import ApiRequestError
from spaceinvasion.runtime.bridge._context import BridgeContext
from spaceinvasion.runtime.bridge._context_init import BridgeConstants
from spaceinvasion.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    GalaxyScanInProgressError,
    sanitize_json_dict,
)
from spaceinvasion.runtime.bridge._helpers_sig import call_with_optional_priority, supports_kwarg
from spaceinvasion.runtime.bridge._state_helpers import (
    build_universe_research_max,
    commit_recorded_state_metadata,
    compute_snapshot_sync_flags,
    merge_economy_rates_for_ctx,
    refresh_raid_targets_for_ctx,
    sync_observed_active_planet_for_ctx,
)
from spaceinvasion.runtime.bridge._state_offers import (
    carry_forward_prev_queues_and_metadata,
    enrich_raid_target_rows,
    merge_cached_offers_into_state,
)
from spaceinvasion.runtime.bridge.lifecycle import BridgeLifecycleService
from spaceinvasion.runtime.bridge.planet_names import PlanetNameService
from spaceinvasion.runtime.logs import append_crash_debug_log
from spaceinvasion.session.auth import AntiBotChallengeError, SpaceInvasionAuthError
from spaceinvasion.session.client import GameBrowserClient, RateLimitError

_STATE_FETCH_ERRORS: tuple[type[Exception], ...] = (SpaceInvasionAuthError, *BRIDGE_RUNTIME_ERRORS)


class StateCacheService:
    """Owns live browser snapshot reads, state merging, raid target enrichment, and state recording."""

    def __init__(self, ctx: BridgeContext, names: PlanetNameService, lifecycle: BridgeLifecycleService) -> None:
        self._ctx = ctx
        self._names = names
        self._lifecycle = lifecycle
        self._on_note_daily_bonus: Callable[[bool], None] | None = None
        self._on_capture_outbound_transports: Callable[[GameState], None] | None = None
        self._on_record_hourly_production: Callable[[GameState], None] | None = None
        self._on_record_new_colonies: Callable[[GameState | None, GameState], None] | None = None
        self._on_record_adaptive_idle_if_due: Callable[[GameState, float], None] | None = None
        self._on_sync_spio_if_unread: Callable[[GameBrowserClient, GameState, str], int] | None = None
        self._on_attach_raid_stats: Callable[[GameState], GameState] | None = None
        self._on_is_galaxy_scan_blocking: Callable[[], bool] | None = None
        self._on_galaxy_scan_busy_msg: Callable[[], str] | None = None
        self._on_maybe_trigger_ai_drift_replan: Callable[[], str | None] | None = None
        self._on_publish_snapshot: Callable[[], None] | None = None

    def bind_hooks(
        self,
        *,
        on_note_daily_bonus: Callable[[bool], None] | None = None,
        on_capture_outbound_transports: Callable[[GameState], None] | None = None,
        on_record_hourly_production: Callable[[GameState], None] | None = None,
        on_record_new_colonies: Callable[[GameState | None, GameState], None] | None = None,
        on_record_adaptive_idle_if_due: Callable[[GameState, float], None] | None = None,
        on_sync_spio_if_unread: Callable[[GameBrowserClient, GameState, str], int] | None = None,
        on_attach_raid_stats: Callable[[GameState], GameState] | None = None,
        on_is_galaxy_scan_blocking: Callable[[], bool] | None = None,
        on_galaxy_scan_busy_msg: Callable[[], str] | None = None,
        on_maybe_trigger_ai_drift_replan: Callable[[], str | None] | None = None,
        on_publish_snapshot: Callable[[], None] | None = None,
    ) -> None:
        """Wire post-record and read-time collaborator hooks explicitly."""
        if on_note_daily_bonus is not None:
            self._on_note_daily_bonus = on_note_daily_bonus
        if on_capture_outbound_transports is not None:
            self._on_capture_outbound_transports = on_capture_outbound_transports
        if on_record_hourly_production is not None:
            self._on_record_hourly_production = on_record_hourly_production
        if on_record_new_colonies is not None:
            self._on_record_new_colonies = on_record_new_colonies
        if on_record_adaptive_idle_if_due is not None:
            self._on_record_adaptive_idle_if_due = on_record_adaptive_idle_if_due
        if on_sync_spio_if_unread is not None:
            self._on_sync_spio_if_unread = on_sync_spio_if_unread
        if on_attach_raid_stats is not None:
            self._on_attach_raid_stats = on_attach_raid_stats
        if on_is_galaxy_scan_blocking is not None:
            self._on_is_galaxy_scan_blocking = on_is_galaxy_scan_blocking
        if on_galaxy_scan_busy_msg is not None:
            self._on_galaxy_scan_busy_msg = on_galaxy_scan_busy_msg
        if on_maybe_trigger_ai_drift_replan is not None:
            self._on_maybe_trigger_ai_drift_replan = on_maybe_trigger_ai_drift_replan
        if on_publish_snapshot is not None:
            self._on_publish_snapshot = on_publish_snapshot

    def read_snapshot(
        self,
        client: GameBrowserClient,
        *,
        include_buildings: bool = True,
        include_research: bool = True,
        include_shipyard: bool = True,
        include_home: bool = True,
        return_home: bool = False,
        priority: str = "normal",
    ) -> GameState:
        """Read a live game snapshot via `client.read_full_snapshot` and track daily bonus sight."""
        fn = client.read_full_snapshot
        extra: dict[str, object] = {}
        if not include_home and supports_kwarg(fn, "include_home"):
            extra["include_home"] = False
        raw_snap = call_with_optional_priority(
            fn,
            include_buildings=include_buildings,
            include_research=include_research,
            include_shipyard=include_shipyard,
            return_home=return_home,
            priority=priority,
            **extra,
        )
        if not isinstance(raw_snap, GameState):
            raise TypeError(f"Expected GameState from read_full_snapshot, got {type(raw_snap).__name__}")
        try:
            drv = client.driver
        except AttributeError:
            drv = None
        if drv is not None:
            cur_u = str(drv.current_url or "")
            if cur_u:
                self._ctx.last_url = cur_u
        try:
            bonus_seen: object = client.last_home_daily_bonus_present
        except AttributeError:
            bonus_seen = None
        if isinstance(bonus_seen, bool) and self._on_note_daily_bonus is not None:
            self._on_note_daily_bonus(bonus_seen)
        return raw_snap

    @staticmethod
    def open_galaxy(
        client: GameBrowserClient,
        *,
        galaxy: int | None = None,
        system: int | None = None,
        priority: str = "interactive",
    ) -> str:
        """Open a galaxy page through `client.open_galaxy` with optional priority forwarding."""
        res = call_with_optional_priority(client.open_galaxy, galaxy=galaxy, system=system, priority=priority)
        return str(res or "")

    def merge_cached_offers(self, state: GameState) -> GameState:
        """Carry forward cached offers from the previous state into `state`."""
        return merge_cached_offers_into_state(self._ctx.last_state, state)

    def enrich_raid_targets(self, rows: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
        """Enrich raid target rows with combat stats and active target blocks."""
        return enrich_raid_target_rows(self._ctx, rows)

    def refresh_raid_targets_locked(
        self,
        *,
        limit: int = 100,
        debris_limit: int = 60,
        state_override: GameState | None = None,
    ) -> tuple[dict[str, object], ...]:
        """Merge top spy targets with proximity-ranked active debris fields."""
        return refresh_raid_targets_for_ctx(
            self._ctx, limit=limit, debris_limit=debris_limit, state_override=state_override
        )

    def merge_economy_rates(self, state: GameState) -> GameState:
        """Merge cached and executor-observed economy rates and raid targets onto `state`."""
        return merge_economy_rates_for_ctx(self._ctx, state)

    def sync_observed_active_planet_state(
        self,
        fallback_state: GameState,
        *,
        obs_pid: int | None,
        obs_confirmed: bool | None,
    ) -> None:
        """Sync `active_planet_id` and confirmation flags after an action observes the browser planet."""
        sync_observed_active_planet_for_ctx(self._ctx, fallback_state, obs_pid=obs_pid, obs_confirmed=obs_confirmed)

    def refresh_cached_economy(self, state: GameState) -> GameState:
        """Refresh economy rates onto a cached state while preserving `last_recorded_state_obj` identity."""
        ctx = self._ctx
        was_recorded = state is ctx.last_recorded_state_obj
        merged = self.merge_economy_rates(state)
        ctx.last_state = merged
        if was_recorded:
            ctx.last_recorded_state_obj = merged
        return merged

    def is_home_fresh(self, now_mono: float) -> bool:
        """Return True when the cached home snapshot is within its 15-second freshness window."""
        ctx = self._ctx
        if ctx.last_state is None:
            return False
        if ctx.last_home_fetched and ctx.last_home_at is not None and (now_mono - ctx.last_home_at) <= 15.0:
            return True
        return ctx.last_state is not ctx.last_recorded_state_obj and ctx.last_state.tutorial is not None

    def record_state(
        self,
        state: GameState,
        *,
        fetched_buildings: bool = False,
        fetched_research: bool = False,
        fetched_shipyard: bool = False,
        fetched_home: bool = False,
        observed_active_planet: bool = False,
        update_snapshot_ts: bool = True,
    ) -> GameState:
        """Merge, stamp, persist, and publish a newly read `GameState`."""
        ctx = self._ctx
        prev = ctx.last_state
        if prev is None:
            ctx.last_home_fetched = False
            ctx.last_home_at = None
            ctx.active_planet_confirmed = False
        state = carry_forward_prev_queues_and_metadata(
            prev,
            state,
            fetched_buildings=fetched_buildings,
            fetched_research=fetched_research,
            fetched_home=fetched_home,
            observed_active_planet=observed_active_planet,
        )
        now = time.monotonic()
        if fetched_home:
            ctx.last_home_fetched = True
            ctx.last_home_at = now
        elif update_snapshot_ts and (
            prev is None or not ctx.last_home_fetched or ctx.last_home_at is None or (now - ctx.last_home_at) > 15.0
        ):
            ctx.last_home_fetched = False
        if observed_active_planet or len(state.planets) <= 1:
            ctx.active_planet_confirmed = True

        merged = self.merge_economy_rates(self.merge_cached_offers(state))
        if self._on_capture_outbound_transports is not None:
            self._on_capture_outbound_transports(merged)
        if self._on_record_hourly_production is not None:
            self._on_record_hourly_production(merged)
        if self._on_record_new_colonies is not None:
            self._on_record_new_colonies(prev, merged)
        if self._on_record_adaptive_idle_if_due is not None:
            self._on_record_adaptive_idle_if_due(merged, time.time())

        commit_recorded_state_metadata(
            ctx,
            prev,
            merged,
            now=now,
            fetched_buildings=fetched_buildings,
            fetched_research=fetched_research,
            fetched_shipyard=fetched_shipyard,
            update_snapshot_ts=update_snapshot_ts,
            persist_runtime_fn=self._lifecycle.persist_runtime_state,
        )
        ctx.snapshot_dirty = True
        return merged

    def _serialize_enriched_state(self, state: GameState, *, sweep_in_progress: bool = False) -> dict[str, object]:
        custom_names = self._names.get_custom_planet_names_locked()
        enriched = self._on_attach_raid_stats(state) if self._on_attach_raid_stats is not None else state
        serialized = self._names.serialize_state_with_custom_names(enriched, custom_names=custom_names)
        serialized["custom_planet_names"] = custom_names
        if sweep_in_progress:
            serialized["sweep_in_progress"] = True
        return sanitize_json_dict(serialized)

    def get_state(self, *, include_buildings: bool = True) -> dict[str, object]:
        """Return fresh or cached game state (acquiring browser lock only when a live refresh is needed)."""
        ctx = self._ctx
        with ctx.lock:
            if ctx.bot_control.get("captcha_blocked"):
                raise AntiBotChallengeError(
                    str(
                        ctx.bot_control.get("captcha_message") or "Paused for CAPTCHA — confirm solved in UI to resume."
                    )
                )
            ctx.raise_if_rate_limited()
            if self._on_is_galaxy_scan_blocking is not None and self._on_is_galaxy_scan_blocking():
                if ctx.last_state is not None:
                    return self._serialize_enriched_state(
                        self.refresh_cached_economy(ctx.last_state), sweep_in_progress=True
                    )
                busy_msg = (
                    self._on_galaxy_scan_busy_msg()
                    if self._on_galaxy_scan_busy_msg is not None
                    else "Galaxy scan in progress."
                )
                raise GalaxyScanInProgressError(busy_msg)
            if ctx.last_state is None:
                ctx.active_planet_confirmed = False
                ctx.last_home_fetched = False
                ctx.last_home_at = None
            now = time.monotonic()
            if (
                ctx.last_state is not None
                and (now - ctx.last_snapshot_at) <= BridgeConstants.SNAPSHOT_CACHE_TTL_S
                and (not include_buildings or ctx.last_buildings_fetched)
                and (self.is_home_fresh(now) or ctx.last_state is not ctx.last_recorded_state_obj)
            ):
                return self._serialize_enriched_state(self.refresh_cached_economy(ctx.last_state))

            need_research, need_shipyard = compute_snapshot_sync_flags(
                ctx, include_buildings=include_buildings, now=now
            )
            merged = self._fetch_live_state_locked(
                include_buildings=include_buildings, need_research=need_research, need_shipyard=need_shipyard
            )
            state_resp = self._serialize_enriched_state(merged)
        if self._on_maybe_trigger_ai_drift_replan is not None:
            self._on_maybe_trigger_ai_drift_replan()
        return state_resp

    def _fetch_live_state_locked(
        self, *, include_buildings: bool, need_research: bool, need_shipyard: bool
    ) -> GameState:
        ctx = self._ctx
        try:
            with ctx.client_factory(ctx.debug_port) as client:
                state = self.read_snapshot(
                    client,
                    include_buildings=include_buildings,
                    include_research=need_research,
                    include_shipyard=need_shipyard,
                    return_home=False,
                    priority="normal",
                )
                if not include_buildings:
                    ctx.last_buildings_fetched = False
                try:
                    obs_active = bool(client.last_snapshot_observed_active_planet)
                except AttributeError:
                    obs_active = False
                merged = self.record_state(
                    state,
                    fetched_buildings=include_buildings,
                    fetched_research=need_research,
                    fetched_shipyard=need_shipyard,
                    fetched_home=True,
                    observed_active_planet=obs_active,
                )
                if (
                    self._on_sync_spio_if_unread is not None
                    and self._on_sync_spio_if_unread(client, merged, "normal") > 0
                ):
                    merged = self.record_state(
                        merged,
                        fetched_buildings=include_buildings,
                        fetched_research=need_research,
                        fetched_shipyard=need_shipyard,
                        fetched_home=False,
                        observed_active_planet=False,
                        update_snapshot_ts=False,
                    )
                return merged
        except RateLimitError as exc:
            ctx.record_server_rate_limit(exc)
            if ctx.log_dir is not None:
                append_crash_debug_log(component="api_server.get_state", event="rate_limit", error=exc)
            raise
        except AntiBotChallengeError as exc:
            self._lifecycle.record_antibot_pause(exc)
            if self._on_publish_snapshot is not None:
                self._on_publish_snapshot()
            if ctx.log_dir is not None:
                append_crash_debug_log(component="api_server.get_state", event="antibot_challenge", error=exc)
            raise
        except _STATE_FETCH_ERRORS as exc:
            if ctx.log_dir is not None and not isinstance(exc, (ApiRequestError, GalaxyScanInProgressError)):
                append_crash_debug_log(component="api_server.get_state", event="state_error", error=exc)
            raise

    def get_universe_research_max_locked(self, state: GameState | None = None) -> list[dict[str, object]]:
        """Return universe-wide max observed research levels merged with own levels."""
        return build_universe_research_max(self._ctx, state=state)
