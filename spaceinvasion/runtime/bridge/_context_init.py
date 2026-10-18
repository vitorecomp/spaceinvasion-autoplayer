"""Bridge constants, default state factories, and database/config bootstrap helpers."""

from __future__ import annotations

from pathlib import Path

from spaceinvasion.core.config import StrategyConfig, load_strategy_config
from spaceinvasion.db.backend import (
    ResolvedDatabaseConfig,
    build_default_postgres_url,
    ensure_postgres_container_ready,
    get_podman_postgres_status,
    is_container_runtime,
    is_postgres_dsn,
    normalize_backend_name,
    ping_postgres,
    redact_db_url,
    resolve_database_config,
)
from spaceinvasion.db.storage import GameDatabase
from spaceinvasion.runtime.bridge._helpers import REPO_ROOT_DIR, safe_hourly_budget
from spaceinvasion.session.client import PageViewLimiter
from spaceinvasion.session.executor import ActionExecutor


class BridgeConstants:
    """Immutable timing, budget, and detection constants shared across bridge collaborators."""

    SNAPSHOT_CACHE_TTL_S: float = 6.0
    RESEARCH_SYNC_TTL_S: float = 60.0
    SHIPYARD_SYNC_TTL_S: float = 120.0
    SPIO_SYNC_TTL_S: float = 30.0
    FLEET_SLOT_COOLDOWN_S: float = 180.0
    ADAPTIVE_IDLE_BUCKET_SECONDS: float = 600.0
    ADAPTIVE_IDLE_MAX_BUCKETS: int = 144
    AI_DRIFT_REPLAN_COOLDOWN_SECONDS: float = 1800.0
    AI_DRIFT_REPLAN_MAX_AGE_SECONDS: float = 14400.0
    AI_DRIFT_CHECK_MIN_INTERVAL_SECONDS: float = 60.0
    AI_DRIFT_LOOT_SHIFT_RATIO: float = 0.25
    TRANSPORT_UTIL_MIN: float = 0.2
    TRANSPORT_UTIL_MAX: float = 0.9
    TRANSPORT_UTIL_DEFAULT: float = 0.5
    TRANSPORT_UTIL_EWMA_ALPHA: float = 0.15
    DELIVERED_TRANSPORT_WINDOW_SECONDS: float = 86400.0
    DELIVERED_TRANSPORT_MAX_EVENTS: int = 500
    DELIVERED_TRANSPORT_MIN_SAMPLES: int = 3
    DELIVERED_TRANSPORT_MIN_SPAN_HOURS: float = 1.0
    INFRA_TRANSPORT_SPEND_WINDOW_SECONDS: float = 3600.0
    INFRA_TRANSPORT_MAX_CARRYOVER_HOURS: float = 2.0
    SPARE_SHIP_BUDGET_WINDOW_HOURS: float = 4.0
    REALIZED_LOOT_WINDOW_HOURS: int = 24
    REALIZED_INCOME_BASIS: str = "realized_24h"
    ADAPTIVE_IDLE_STATE_KEYS: tuple[str, ...] = (
        "building",
        "ready_not_started",
        "field_conflict",
        "storage_blocked",
        "transport_inbound",
        "waiting_metal",
        "waiting_pig_iron",
        "waiting_kryptonite",
        "waiting_spice",
        "idle_complete",
    )
    FLEET_SLOT_BUSY_MARKERS: tuple[str, ...] = (
        "no ship input fields found on internalfleet",
        "no free fleet slot",
        "too many fleets",
        "fleeterr.toomanyfleets",
        "fleeterr.nofreeslot",
    )


def new_idle_planner_job(kind: str) -> dict[str, object]:
    """Create a clean idle planner job record."""
    return {
        "job_id": None,
        "kind": kind,
        "status": "idle",
        "phase": "idle",
        "progress": 0.0,
        "progress_pct": 0.0,
        "started_ts": None,
        "finished_ts": None,
        "elapsed_ms": 0.0,
        "trigger_source": "",
        "run_id": None,
        "error": None,
        "auto_replan_ai": False,
    }


def default_bot_control_state() -> dict[str, object]:
    """Return initial bot-control flags."""
    return {
        "paused": False,
        "mode_override": None,
        "captcha_blocked": False,
        "captcha_message": None,
    }


def default_transmitter_state() -> dict[str, object]:
    """Return initial in-memory transmitter state."""
    return {
        "last_activated_day": "",
        "last_checked_day": "",
        "daily_bonus_checked_day": "",
        "daily_bonus_collected_day": "",
        "daily_bonus_available": False,
        "daily_bonus_available_day": "",
        "last_activated_ts": 0.0,
        "last_checked_ts": 0.0,
        "free_spins_remaining": None,
        "last_reward": "",
        "total_activations": 0,
    }


def default_galaxy_sweep_state() -> dict[str, object]:
    """Return initial in-memory galaxy sweep telemetry."""
    return {
        "cursor_galaxy": 1,
        "cursor_system": 1,
        "last_sweep_started_ts": 0.0,
        "last_sweep_completed_ts": 0.0,
        "next_sweep_due_ts": 0.0,
        "last_pages_scanned": 0,
        "last_players_found": 0,
        "last_colonies_found": 0,
        "last_debris_found": 0,
        "total_sweeps": 0,
        "total_pages_scanned": 0,
        "click_delay_seconds": 1.5,
        "click_jitter_seconds": 0.6,
    }


def open_bridge_database(
    *,
    db_path: Path | str | None,
    db_backend: str | None,
    db_url: str | None,
    log_dir: Path | None,
) -> tuple[GameDatabase | None, str, str]:
    """Open the configured PostgreSQL `GameDatabase` (or None when disabled)."""
    selection_source = "explicit"
    resolved_url = build_default_postgres_url()
    if db_backend is not None and str(db_backend).strip().lower() not in ("", "auto"):
        normalize_backend_name(db_backend)

    is_live_repo_default_db = False
    if db_path is None and (db_backend is not None or db_url is not None or is_container_runtime()):
        is_live_repo_default_db = True
    elif db_path is None and log_dir is not None:
        try:
            is_live_repo_default_db = Path(log_dir).resolve() == (REPO_ROOT_DIR / "logs").resolve()
        except OSError:
            is_live_repo_default_db = False

    if db_path is not None and is_postgres_dsn(str(db_path)):
        selection_source = "explicit_dsn"
        resolved_url = str(db_path).strip()
        return GameDatabase(resolved_url, backend="postgres"), selection_source, resolved_url
    if is_live_repo_default_db:
        resolved_cfg: ResolvedDatabaseConfig = resolve_database_config(
            explicit_backend=db_backend,
            explicit_db_url=db_url,
            root_dir=REPO_ROOT_DIR,
        )
        ensure_postgres_container_ready(
            resolved_cfg.postgres_url,
            root_dir=REPO_ROOT_DIR,
            auto_start=resolved_cfg.auto_start_podman,
        )
        db = GameDatabase(resolved_cfg.postgres_url, backend="postgres")
        return db, resolved_cfg.selection_source, resolved_cfg.postgres_url
    if db_path is not None or log_dir is not None:
        eff_path: Path | str = db_path if db_path is not None else (Path(log_dir).parent / "state" / "postgres")  # type: ignore[arg-type]
        db = GameDatabase(eff_path)
        pg_dsn = str(db.postgres_url or "")
        if is_postgres_dsn(pg_dsn):
            resolved_url = pg_dsn
        return db, selection_source, resolved_url
    return None, selection_source, resolved_url


def resolve_initial_strategy_config(
    *,
    config_path: Path | None,
    log_dir: Path | None,
    db_backend: str | None,
    db_url: str | None,
) -> StrategyConfig:
    """Resolve the initial `StrategyConfig` matching legacy `GameApiBridge` startup precedence."""
    if config_path is not None:
        return load_strategy_config(config_path)
    if log_dir is not None or db_backend is not None or db_url is not None or is_container_runtime():
        return load_strategy_config(None)
    return StrategyConfig()


def build_action_executors(
    cfg: StrategyConfig,
    *,
    max_actions_per_hour_override: int | None,
    page_limiter: PageViewLimiter,
    executor_max_limiter_wait: float | None,
) -> tuple[ActionExecutor, ActionExecutor]:
    """Construct paired live and dry-run `ActionExecutor` instances."""
    enforce_hourly = max_actions_per_hour_override is not None
    raw_budget = (
        max_actions_per_hour_override if max_actions_per_hour_override is not None else cfg.max_actions_per_hour
    )
    budget = safe_hourly_budget(raw_budget, 60)
    live_exec = ActionExecutor(
        dry_run=False,
        max_actions_per_hour=budget,
        enforce_hourly_cap=enforce_hourly,
        allow_fleetsave=cfg.allow_fleetsave,
        allow_raids=cfg.allow_raids,
        allow_colonize=cfg.allow_colonize,
        allow_transport=cfg.allow_transport,
        allow_transmitter=cfg.allow_transmitter,
        page_limiter=page_limiter,
        max_limiter_wait=executor_max_limiter_wait,
    )
    dry_exec = ActionExecutor(
        dry_run=True,
        max_actions_per_hour=budget,
        enforce_hourly_cap=enforce_hourly,
        allow_fleetsave=cfg.allow_fleetsave,
        allow_raids=cfg.allow_raids,
        allow_colonize=cfg.allow_colonize,
        allow_transport=cfg.allow_transport,
        allow_transmitter=cfg.allow_transmitter,
        page_limiter=page_limiter,
        max_limiter_wait=executor_max_limiter_wait,
    )
    return live_exec, dry_exec


def build_db_backend_status_dict(
    db: GameDatabase | None,
    *,
    selection_source: str,
    resolved_postgres_url: str,
    include_ping: bool = False,
) -> dict[str, object]:
    """Build the `db_backend` diagnostic mapping for health and DB endpoints."""
    out: dict[str, object] = {
        "backend": str(db.backend_name) if db is not None else "none",
        "target": str(db.display_target) if db is not None else "",
        "selection_source": selection_source,
        "container_mode": is_container_runtime(),
        "postgres_target": redact_db_url(resolved_postgres_url),
    }
    if include_ping:
        out["podman"] = get_podman_postgres_status(force=False)
        pg_ok, pg_ms, pg_msg = ping_postgres(resolved_postgres_url, timeout=1.5)
        out["postgres_reachable"] = pg_ok
        out["postgres_latency_ms"] = pg_ms
        out["postgres_server"] = pg_msg if pg_ok else ""
        out["postgres_error"] = "" if pg_ok else pg_msg
    return out
