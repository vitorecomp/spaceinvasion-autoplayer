"""Bridge persistence, shutdown, anti-bot CAPTCHA gate, and notification lifecycle collaborator."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
import logging
import math
import time

from backend.core.config import strategy_config_from_dict
from backend.core.model import GameState
from backend.core.redaction import redact_text
from backend.runtime.bridge._context import BridgeContext
from backend.runtime.bridge._context_init import BridgeConstants
from backend.runtime.bridge._helpers import (
    BRIDGE_RUNTIME_ERRORS,
    bounded_api_str,
    redact_api_payload,
    safe_api_int,
)
from backend.runtime.bridge._lifecycle_hydrate import (
    hydrate_adaptive_idle_buckets,
    hydrate_bot_control_and_scalars,
    hydrate_db_auxiliary_caches,
    hydrate_timers_and_slots,
    hydrate_transport_and_spend_ledgers,
)
from backend.session.executor import ExecutionResult

_LOGGER = logging.getLogger(__name__)

_PROTECTION_KEYWORDS: tuple[str, ...] = (
    "can't be attacked",
    "cannot be attacked",
    "nicht angegriffen",
    "vacation",
    "urlaubsmodus",
    "protection",
    "attack zone",
    "attackzone",
    "angriffszone",
    "forbidden",
    "newbie",
    "noob",
    "bash",
)


class BridgeLifecycleService:
    """Handles bridge boot hydration, runtime persistence, CAPTCHA pause, notifications, and close."""

    def __init__(
        self,
        ctx: BridgeContext,
        *,
        prune_ledgers_fn: Callable[[float], None] | None = None,
        recompute_transport_util_fn: Callable[[float], None] | None = None,
        merge_economy_fn: Callable[[GameState], GameState] | None = None,
        stop_sweep_fn: Callable[[], None] | None = None,
    ) -> None:
        self._ctx = ctx
        self._prune_ledgers_fn = prune_ledgers_fn
        self._recompute_transport_util_fn = recompute_transport_util_fn
        self._merge_economy_fn = merge_economy_fn
        self._stop_sweep_fn = stop_sweep_fn

    def bind_callbacks(
        self,
        *,
        prune_ledgers_fn: Callable[[float], None] | None = None,
        recompute_transport_util_fn: Callable[[float], None] | None = None,
        merge_economy_fn: Callable[[GameState], GameState] | None = None,
        stop_sweep_fn: Callable[[], None] | None = None,
    ) -> None:
        """Bind post-construction collaborator hooks explicitly without circular imports."""
        if prune_ledgers_fn is not None:
            self._prune_ledgers_fn = prune_ledgers_fn
        if recompute_transport_util_fn is not None:
            self._recompute_transport_util_fn = recompute_transport_util_fn
        if merge_economy_fn is not None:
            self._merge_economy_fn = merge_economy_fn
        if stop_sweep_fn is not None:
            self._stop_sweep_fn = stop_sweep_fn

    def hydrate_persisted_bridge_state(self) -> None:
        """Hydrate strategy config, runtime state, last game state, and auxiliary DB caches."""
        ctx = self._ctx
        db = ctx.db
        if db is None:
            return
        ctx.rank_samples = [dict(r) for r in db.get_rank_samples() if isinstance(r, Mapping)]
        ctx.economy_rates.update(db.get_all_economy_rates())
        ctx.scanned_systems.update(db.get_galaxy_scans())
        ctx.throttle_timestamps.update(db.get_last_throttle_timestamps())

        has_explicit_file = bool(ctx.config_file_explicit and ctx.config_path is not None and ctx.config_path.exists())
        if not has_explicit_file:
            saved_cfg = db.load_strategy_config_snapshot()
            if isinstance(saved_cfg, Mapping) and saved_cfg:
                try:
                    ctx.strategy_config = strategy_config_from_dict(dict(saved_cfg))
                except BRIDGE_RUNTIME_ERRORS:
                    _LOGGER.warning("Ignoring invalid persisted strategy config snapshot", exc_info=True)
        db.save_strategy_config_snapshot(asdict(ctx.strategy_config))
        if not ctx.custom_page_limiter_injected:
            ctx.sync_page_limiter_from_config(ctx.strategy_config)

        rt = db.load_runtime_state()
        if isinstance(rt, Mapping) and rt:
            now_wall = time.time()
            now_mono = time.monotonic()
            hydrate_bot_control_and_scalars(ctx, rt)
            hydrate_timers_and_slots(ctx, rt, now_wall=now_wall, now_mono=now_mono)
            hydrate_transport_and_spend_ledgers(ctx, rt)
            hydrate_adaptive_idle_buckets(ctx, rt)
            if self._prune_ledgers_fn is not None:
                self._prune_ledgers_fn(now_wall)
            if self._recompute_transport_util_fn is not None:
                self._recompute_transport_util_fn(now_wall)

        persisted_gs = db.load_latest_game_state()
        if isinstance(persisted_gs, GameState):
            ctx.last_state = (
                self._merge_economy_fn(persisted_gs) if self._merge_economy_fn is not None else persisted_gs
            )
            ctx.last_recorded_state_obj = ctx.last_state
            ctx.last_snapshot_at = -1e9
            ctx.active_planet_confirmed = False
            ctx.last_home_fetched = False
            ap0 = ctx.last_state.active_planet()
            ctx.last_buildings_fetched = bool(ap0 is not None and ap0.building_offers)

        hydrate_db_auxiliary_caches(ctx, db)

    def persist_runtime_state(self) -> None:
        """Persist in-memory runtime control flags, cooldowns, and adaptive ledgers to PostgreSQL."""
        ctx = self._ctx
        db = ctx.db
        if db is None:
            return
        now_wall = time.time()
        now_mono = time.monotonic()

        def _mono_to_wall(mono_val: float) -> float:
            if not math.isfinite(mono_val) or mono_val <= 0.0:
                return 0.0
            return round(now_wall - max(0.0, now_mono - float(mono_val)), 3)

        fsc_wall: dict[str, float] = {}
        for pid, until_m in list(ctx.fleet_slot_cooldown_until.items()):
            if math.isfinite(until_m):
                rem = float(until_m) - now_mono
                if rem > 0.0:
                    fsc_wall[str(int(pid))] = round(now_wall + rem, 3)

        if self._prune_ledgers_fn is not None:
            self._prune_ledgers_fn(now_wall)

        serialized_buckets: dict[str, dict[str, object]] = {
            str(bk): dict(ctx.adaptive_idle_buckets[bk]) for bk in sorted(ctx.adaptive_idle_buckets.keys())
        }
        payload: dict[str, object] = {
            "bot_control": dict(ctx.bot_control),
            "economy_sync_timestamps": {
                str(int(k)): round(float(v), 3)
                for k, v in ctx.economy_sync_timestamps.items()
                if math.isfinite(v) and v > 0.0
            },
            "metal_overflow_paused_planets": {
                str(int(k)): round(float(v), 3)
                for k, v in ctx.metal_overflow_paused_planets.items()
                if math.isfinite(v) and v > 0.0
            },
            "fleet_slot_cooldown_until_wall": fsc_wall,
            "observed_fleet_slot_base_offset": ctx.observed_fleet_slot_base_offset,
            "observed_fleet_slots_by_pid": {
                str(int(k)): [int(v[0]), int(v[1])] for k, v in ctx.observed_fleet_slots_by_pid.items()
            },
            "last_synced_unread_total": max(0, int(ctx.last_synced_unread_total)),
            "pending_spy_sync": bool(ctx.pending_spy_sync),
            "spio_sync_at_wall": _mono_to_wall(ctx.last_spio_sync_at),
            "research_sync_at_wall": _mono_to_wall(ctx.last_research_sync_at),
            "shipyard_sync_at_wall": _mono_to_wall(ctx.last_shipyard_sync_at),
            "spy_dispatched_at_wall": _mono_to_wall(ctx.last_spy_dispatched_at),
            "last_url": bounded_api_str(ctx.last_url, default="") if ctx.last_url else None,
            "adaptive_idle_buckets": serialized_buckets,
            "delivered_transport_events": [dict(e) for e in ctx.delivered_transport_events],
            "infra_transport_spend_ledger": [dict(e) for e in ctx.infra_transport_spend_ledger],
            "spare_ship_spend_ledger": [dict(e) for e in ctx.spare_ship_spend_ledger],
            "infra_transport_last_spend_ts": (
                round(float(ctx.infra_transport_last_spend_ts), 3)
                if math.isfinite(ctx.infra_transport_last_spend_ts) and ctx.infra_transport_last_spend_ts > 0.0
                else 0.0
            ),
            "transport_realized_utilization": round(
                min(
                    BridgeConstants.TRANSPORT_UTIL_MAX,
                    max(BridgeConstants.TRANSPORT_UTIL_MIN, float(ctx.transport_realized_utilization)),
                ),
                4,
            ),
            "last_auto_ai_replan_ts": (
                round(float(ctx.last_auto_ai_replan_ts), 3)
                if math.isfinite(ctx.last_auto_ai_replan_ts) and ctx.last_auto_ai_replan_ts > 0.0
                else 0.0
            ),
            "last_auto_ai_replan_reason": str(ctx.last_auto_ai_replan_reason or "")[:128],
        }
        db.save_runtime_state(payload, ts=now_wall)

    def record_antibot_pause(self, exc: BaseException) -> None:
        """Auto-pause automation on CAPTCHA/anti-bot detection and raise a persistent UI alert."""
        ctx = self._ctx
        already_blocked = bool(ctx.bot_control.get("captcha_blocked"))
        msg = bounded_api_str(exc, default="CAPTCHA / anti-bot challenge detected.")
        if not any(tok in msg.lower() for tok in ("captcha", "anti-bot", "antibot", "turnstile", "sicherheitspr")):
            msg = f"CAPTCHA / anti-bot challenge: {msg}" if msg else "CAPTCHA / anti-bot challenge detected."
        ctx.bot_control["paused"] = True
        ctx.bot_control["captcha_blocked"] = True
        ctx.bot_control["captcha_message"] = msg
        db = ctx.db
        if not already_blocked and db is not None:
            try:
                db.create_or_bump_notification(
                    title="CAPTCHA / Security Verification Required",
                    message=msg,
                    help_prompt=(
                        "Page refreshes and automation are completely frozen so the CAPTCHA is not interrupted. "
                        "Solve the verification check manually in Chrome (:9222), then click "
                        "'Solved in Chrome — Resume Bot' in the UI."
                    ),
                    severity="error",
                    category="captcha_gate",
                    action_kind="SECURITY_CHECK",
                    dedupe_key="captcha:gate",
                )
            except BRIDGE_RUNTIME_ERRORS:
                _LOGGER.warning("Failed to persist CAPTCHA gate notification", exc_info=True)
        if not already_blocked:
            self.persist_runtime_state()

    def ack_captcha_notifications_locked(self) -> None:
        """Acknowledge unread CAPTCHA/anti-bot notifications after operator resume."""
        db = self._ctx.db
        if db is None:
            return
        try:
            for item in db.get_notifications(status="unread", limit=50):
                if not isinstance(item, Mapping):
                    continue
                if item.get("dedupe_key") in ("captcha:gate", "antibot:captcha_challenge") or item.get("category") in (
                    "captcha_gate",
                    "antibot",
                ):
                    nid = safe_api_int(item.get("id"), 0)
                    if nid > 0:
                        db.acknowledge_notification(nid)
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.warning("Failed to acknowledge CAPTCHA notifications", exc_info=True)

    def get_notifications_summary_locked(self, *, limit: int = 30) -> dict[str, object]:
        """Return active notifications and unread count."""
        db = self._ctx.db
        if db is None:
            return {"unread_count": 0, "items": []}
        items = db.get_notifications(status="active", limit=limit)
        unread_cnt = db.unread_notification_count()
        return {
            "unread_count": int(unread_cnt),
            "items": redact_api_payload(items),
        }

    def record_action_validation_notification_locked(
        self,
        cmd: str,
        res: ExecutionResult,
    ) -> dict[str, object] | None:
        """Create a persistent notification on unexpected action failures (never on target protection)."""
        db = self._ctx.db
        if db is None or res.dry_run:
            return None
        if res.executed and res.validated:
            return None
        msg_s = bounded_api_str(res.message, default="")
        msg_l = msg_s.lower()
        if (
            msg_l.startswith("no-op:")
            or (res.validated and msg_l.startswith("skipped:"))
            or "hourly action rate cap" in msg_l
            or "no free fleet slot" in msg_l
            or "too many fleets" in msg_l
            or any(marker in msg_l for marker in BridgeConstants.FLEET_SLOT_BUSY_MARKERS)
        ):
            return None

        act = res.action
        kind_s = bounded_api_str(act.kind if act is not None else cmd, default=cmd).upper()
        pcoords_s = bounded_api_str(act.planet_coords if act is not None else "", default="")
        tname_s = bounded_api_str(act.target_name if act is not None else kind_s, default=kind_s)
        val_err = bounded_api_str(res.validation_error or "", default="")
        combined_l = f"{msg_l} {val_err.lower()}"
        raw_tcd = res.target_cooldown_seconds
        has_tcd = raw_tcd is not None and math.isfinite(raw_tcd) and raw_tcd > 0.0
        if has_tcd or any(kw in combined_l for kw in _PROTECTION_KEYWORDS):
            cd_hours = round(float(raw_tcd) / 3600.0, 2) if (has_tcd and raw_tcd is not None) else 6.0
            _LOGGER.info(
                "%s",
                redact_text(
                    f"[API] {kind_s} target protected ({tname_s}): {val_err or msg_s} (cooldown={cd_hours:g}h)"
                ),
            )
            return None

        if res.executed and not res.validated:
            severity = "error"
            category = "action_validation"
            title = f"Validation failed after {kind_s}: {tname_s}"
            help_prompt = (
                f"{val_err or msg_s} Please inspect [{pcoords_s or 'active colony'}] in Chrome (:9222) "
                "to verify whether the action took effect or needs manual intervention."
            )
            dkey = f"valfail:{kind_s}:{pcoords_s}:{tname_s}"
        else:
            severity = "warning"
            category = "action_aborted"
            title = f"{kind_s} could not execute: {tname_s}"
            help_prompt = (
                f"{msg_s} Please check [{pcoords_s or 'active colony'}] in Chrome (:9222) if operator action is needed, "
                "or acknowledge to dismiss."
            )
            dkey = f"abort:{kind_s}:{pcoords_s}:{tname_s}"
        try:
            created = db.create_or_bump_notification(
                title=title,
                message=msg_s or val_err or "Action did not pass post-execution validation.",
                help_prompt=help_prompt,
                severity=severity,
                category=category,
                action_kind=kind_s,
                planet_coords=pcoords_s,
                target_name=tname_s,
                dedupe_key=dkey,
                details={
                    "command": cmd,
                    "executed": res.executed,
                    "validated": res.validated,
                    "validation_error": val_err or None,
                },
            )
            return dict(created) if isinstance(created, Mapping) else None
        except BRIDGE_RUNTIME_ERRORS:
            _LOGGER.warning("Failed to record action validation notification", exc_info=True)
            return None

    def close(self) -> None:
        """Stop background sweep/planner workers and close the database idempotently."""
        ctx = self._ctx
        with ctx.planner_jobs_lock:
            ctx.planner_closed = True
        if self._stop_sweep_fn is not None:
            self._stop_sweep_fn()
        with ctx.lock:
            if not ctx.db_closed:
                ctx.db_closed = True
                if ctx.db is not None:
                    ctx.db.close()
