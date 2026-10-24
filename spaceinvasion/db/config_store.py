"""PostgreSQL ORM and JSON file persistence for ``StrategyConfig``."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

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
from spaceinvasion.db.dsn import build_default_postgres_url, is_postgres_dsn
from spaceinvasion.db.store import DatabaseStore


def load_strategy_config_from_postgres(dsn: str) -> StrategyConfig | None:
    """Load persisted ``StrategyConfig`` via `SettingsRepository` (`strategy_config_store` / `meta`)."""
    target_dsn = dsn.strip()
    if not is_postgres_dsn(target_dsn):
        return None
    raw_json: str | None = None
    try:
        with DatabaseStore.from_dsn(target_dsn, pool_size=1, max_overflow=0) as store:
            with store.repositories() as repos:
                rec = repos.settings.load_strategy_config()
                if rec is not None and rec.config_json.strip():
                    raw_json = rec.config_json
    except (SQLAlchemyError, OSError, ValueError):
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
    """Persist ``StrategyConfig`` into PostgreSQL via `SettingsRepository.save_strategy_config`."""
    target_dsn = dsn.strip()
    cfg_json = _serialize_strategy_config_json(cfg)
    raw_obj: object = json.loads(cfg_json)
    payload: dict[str, object] = {str(k): v for k, v in raw_obj.items()} if isinstance(raw_obj, Mapping) else {}
    with DatabaseStore.from_dsn(target_dsn, pool_size=1, max_overflow=0) as store:
        with store.repositories() as repos:
            repos.settings.save_strategy_config(payload, source=source)
    return target_dsn


def _resolve_default_config_target(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Resolve default ``StrategyConfig`` storage target (PostgreSQL exclusively)."""
    return ("postgres", build_default_postgres_url(env))


def load_strategy_config(
    path: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> StrategyConfig:
    """Load ``StrategyConfig`` from PostgreSQL (default or DSN) or a JSON file path."""
    if path is None:
        _backend, target = _resolve_default_config_target(env)
        from_pg = load_strategy_config_from_postgres(target)
        return from_pg if from_pg is not None else StrategyConfig()
    if isinstance(path, str) and is_postgres_dsn(path):
        from_pg = load_strategy_config_from_postgres(path)
        return from_pg if from_pg is not None else StrategyConfig()
    return load_strategy_config_json(path)


def save_strategy_config(
    cfg: StrategyConfig,
    path: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Path | str:
    """Persist ``StrategyConfig`` to PostgreSQL (default or DSN) or a JSON file path."""
    if path is None:
        _backend, target = _resolve_default_config_target(env)
        return save_strategy_config_to_postgres(cfg, target)
    if isinstance(path, str) and is_postgres_dsn(path):
        return save_strategy_config_to_postgres(cfg, path)
    if not isinstance(path, Path):
        ok_fsp, fsp_val = _safe_fspath(path)
        if not ok_fsp:
            raise TypeError("Expected Path, str, or os.PathLike for config path.")
        path = Path(os.fsdecode(fsp_val))
    return save_strategy_config_json(cfg, path)


__all__ = [
    "load_strategy_config",
    "load_strategy_config_from_postgres",
    "save_strategy_config",
    "save_strategy_config_to_postgres",
]
