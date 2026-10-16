"""PostgreSQL and default storage persistence for ``StrategyConfig`` (moved from ``core.config``)."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import time
from typing import Optional

import psycopg

from spaceinvasion.core.config import (
    StrategyConfig,
    _safe_fspath,
    _sanitize_cfg_json_tree,
    _serialize_strategy_config_json,
    strategy_config_from_dict,
)
from spaceinvasion.core.config import (
    load_strategy_config as load_strategy_config_json,
)
from spaceinvasion.core.config import (
    save_strategy_config as save_strategy_config_json,
)
from spaceinvasion.db.backend import build_default_postgres_url


def load_strategy_config_from_postgres(dsn: str) -> Optional[StrategyConfig]:
    """Load persisted ``StrategyConfig`` from PostgreSQL (``strategy_config_store`` or ``meta['strategy_config']``)."""
    raw_json: Optional[str] = None
    try:
        with psycopg.connect(str(dsn).strip(), connect_timeout=3) as conn:
            has_store = conn.execute(
                "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = 'public' AND tablename = 'strategy_config_store' LIMIT 1"
            ).fetchone()
            if has_store is not None:
                row = conn.execute("SELECT config_json FROM strategy_config_store WHERE id = 1").fetchone()
                if row is not None and isinstance(row[0], str) and row[0].strip():
                    raw_json = row[0]
            if raw_json is None:
                has_meta = conn.execute(
                    "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = 'public' AND tablename = 'meta' LIMIT 1"
                ).fetchone()
                if has_meta is not None:
                    mrow = conn.execute("SELECT value FROM meta WHERE key = 'strategy_config'").fetchone()
                    if mrow is not None and isinstance(mrow[0], str) and mrow[0].strip():
                        raw_json = mrow[0]
    except (psycopg.Error, OSError, ValueError):
        return None
    if not raw_json:
        return None
    try:
        parsed = _sanitize_cfg_json_tree(json.loads(raw_json))
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, Mapping):
        return None
    return strategy_config_from_dict(parsed)


def save_strategy_config_to_postgres(
    cfg: StrategyConfig,
    dsn: str,
    *,
    source: str = "postgres",
) -> str:
    """Persist ``StrategyConfig`` into PostgreSQL (``strategy_config_store``, ``meta``, and ``config_history``)."""
    cfg_json = _serialize_strategy_config_json(cfg)
    now_ts = round(time.time(), 3)
    target_dsn = str(dsn).strip()
    with psycopg.connect(target_dsn, connect_timeout=5) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_config_store (
                id BIGINT PRIMARY KEY CHECK (id = 1),
                config_json TEXT NOT NULL,
                updated_ts DOUBLE PRECISION NOT NULL,
                source TEXT NOT NULL DEFAULT 'postgres'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO strategy_config_store(id, config_json, updated_ts, source)
            VALUES (1, %s, %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                config_json = EXCLUDED.config_json,
                updated_ts = EXCLUDED.updated_ts,
                source = EXCLUDED.source
            """,
            (cfg_json, now_ts, str(source or "postgres")[:32]),
        )
        has_meta = conn.execute(
            "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = 'public' AND tablename = 'meta' LIMIT 1"
        ).fetchone()
        if has_meta is not None:
            conn.execute(
                """
                INSERT INTO meta(key, value, updated_ts) VALUES ('strategy_config', %s, %s)
                ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value, updated_ts = EXCLUDED.updated_ts
                """,
                (cfg_json, now_ts),
            )
        has_hist = conn.execute(
            "SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname = 'public' AND tablename = 'config_history' LIMIT 1"
        ).fetchone()
        if has_hist is not None:
            last_row = conn.execute("SELECT config_json FROM config_history ORDER BY id DESC LIMIT 1").fetchone()
            if last_row is None or last_row[0] != cfg_json:
                conn.execute("INSERT INTO config_history(ts, config_json) VALUES (%s, %s)", (now_ts, cfg_json))
        conn.commit()
    return target_dsn


def _resolve_default_config_target() -> tuple[str, str]:
    """Resolve default ``StrategyConfig`` storage target (PostgreSQL exclusively)."""
    return ("postgres", build_default_postgres_url())


def load_strategy_config(path: Optional[Path | str] = None) -> StrategyConfig:
    """Load ``StrategyConfig`` from PostgreSQL (default or DSN) or a JSON file path."""
    if path is None:
        _backend, target = _resolve_default_config_target()
        from_pg = load_strategy_config_from_postgres(str(target))
        return from_pg if from_pg is not None else StrategyConfig()
    if isinstance(path, str) and path.strip().lower().startswith(("postgresql://", "postgres://")):
        from_pg = load_strategy_config_from_postgres(path)
        return from_pg if from_pg is not None else StrategyConfig()
    return load_strategy_config_json(path)


def save_strategy_config(cfg: StrategyConfig, path: Optional[Path | str] = None) -> Path | str:
    """Persist ``StrategyConfig`` to PostgreSQL (default or DSN) or a JSON file path."""
    if path is None:
        _backend, target = _resolve_default_config_target()
        return save_strategy_config_to_postgres(cfg, str(target))
    if isinstance(path, str) and path.strip().lower().startswith(("postgresql://", "postgres://")):
        return save_strategy_config_to_postgres(cfg, path)
    if not isinstance(path, Path):
        ok_fsp, fsp_val = _safe_fspath(path)
        if not ok_fsp:
            raise TypeError("Expected Path, str, or os.PathLike for config path.")
        path = Path(os.fsdecode(fsp_val))
    return save_strategy_config_json(cfg, path)
