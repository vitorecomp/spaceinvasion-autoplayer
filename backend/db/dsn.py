"""Pure PostgreSQL DSN parsing, URL building, normalization, and redaction helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import parse_qs, quote, urlparse, urlunparse

from backend.core.redaction import redact_sensitive

VALID_DB_BACKENDS: frozenset[str] = frozenset({"postgres"})
DEFAULT_DB_BACKEND: str = "postgres"
DEFAULT_PG_HOST: str = "127.0.0.1"
DEFAULT_PG_PORT: int = 5433
DEFAULT_PG_DB: str = "spaceinvasion"
DEFAULT_PG_USER: str = "spaceinvasion"
DEFAULT_PG_PASSWORD: str = "spaceinvasion"

_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})
_FALSY: frozenset[str] = frozenset({"0", "false", "no", "off"})
_PG_SCHEMES: tuple[str, ...] = ("postgresql://", "postgres://", "postgresql+psycopg://")
_PW_MASK_RE = re.compile(r"(postgres(?:ql)?(?:\+psycopg)?://[^:/@\s]+:)[^@\s]+(@)")


def is_postgres_dsn(value: object) -> bool:
    """Return True when `value` is a PostgreSQL connection URL."""
    if not isinstance(value, str):
        return False
    low = value.strip().lower()
    return low.startswith(_PG_SCHEMES)


def get_unix_socket_host(dsn: object) -> str | None:
    """Return the Unix-socket directory from a PostgreSQL DSN (`?host=/path/...`), or None."""
    if not isinstance(dsn, str) or not is_postgres_dsn(dsn):
        return None
    try:
        hosts = parse_qs(urlparse(dsn.strip()).query).get("host", ())
    except ValueError:
        return None
    for host in hosts:
        cleaned = str(host).strip()
        if cleaned.startswith("/"):
            return cleaned
    return None


def is_unix_socket_dsn(dsn: object) -> bool:
    """Return True when `dsn` targets a local PostgreSQL Unix domain socket."""
    return get_unix_socket_host(dsn) is not None


def normalize_backend_name(raw: object, default: str = DEFAULT_DB_BACKEND) -> str:
    """Normalize a database backend name, rejecting retired SQLite backends."""
    if raw is None:
        return default
    txt = str(raw).strip().lower()
    if txt in ("", "auto"):
        return default
    if txt in ("pg", "postgres", "postgresql"):
        return "postgres"
    if txt in ("sqlite", "sqlite3", "file"):
        raise ValueError("SQLite backend has been removed; PostgreSQL is the only supported backend.")
    return default


def is_container_env(env: Mapping[str, str] | None = None) -> bool:
    """Return True when `SPACEINVASION_CONTAINER` is truthy in `env`."""
    if env is None:
        return False
    flag = str(env.get("SPACEINVASION_CONTAINER") or "").strip().lower()
    return flag in _TRUTHY


def build_default_postgres_url(env: Mapping[str, str] | None = None) -> str:
    """Build the default PostgreSQL DSN from an explicit `env` mapping and defaults."""
    src: Mapping[str, str] = env if env is not None else {}
    explicit = str(src.get("SPACEINVASION_DB_URL") or "").strip()
    if is_postgres_dsn(explicit):
        return explicit
    host = str(src.get("SPACEINVASION_PG_HOST") or DEFAULT_PG_HOST).strip() or DEFAULT_PG_HOST
    raw_port = str(src.get("SPACEINVASION_PG_PORT") or DEFAULT_PG_PORT).strip()
    try:
        port = max(1, min(65535, int(raw_port)))
    except ValueError:
        port = DEFAULT_PG_PORT
    dbname = str(src.get("SPACEINVASION_PG_DB") or DEFAULT_PG_DB).strip() or DEFAULT_PG_DB
    user = str(src.get("SPACEINVASION_PG_USER") or DEFAULT_PG_USER).strip() or DEFAULT_PG_USER
    pw = str(src.get("SPACEINVASION_PG_PASSWORD") or DEFAULT_PG_PASSWORD)
    return f"postgresql://{quote(user, safe='')}:{quote(pw, safe='')}@{host}:{port}/{quote(dbname, safe='')}"


def redact_db_url(url_or_path: object) -> str:
    """Mask passwords in PostgreSQL DSNs and redact sensitive tokens in arbitrary strings."""
    txt = str(url_or_path or "").strip()
    if not is_postgres_dsn(txt):
        return str(redact_sensitive(txt))
    try:
        parsed = urlparse(txt)
        if parsed.password:
            user_part = f"{parsed.username}:***" if parsed.username else "***"
            host_part = (
                parsed.hostname if parsed.hostname is not None else ("" if is_unix_socket_dsn(txt) else DEFAULT_PG_HOST)
            )
            if parsed.port is not None:
                host_part = f"{host_part}:{parsed.port}"
            netloc = f"{user_part}@{host_part}"
            return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
        return txt
    except (ValueError, TypeError):
        return _PW_MASK_RE.sub(r"\1***\2", txt)


@dataclass(frozen=True)
class ResolvedDatabaseConfig:
    """Resolved PostgreSQL connection parameters and provenance."""

    backend: str
    postgres_url: str
    auto_start_podman: bool = True
    selection_source: str = "default"

    @property
    def active_target(self) -> str:
        """Return the active PostgreSQL DSN."""
        return self.postgres_url

    @property
    def display_target(self) -> str:
        """Return a password-redacted display string for logs and UI."""
        return redact_db_url(self.postgres_url)


def resolve_database_config(
    *,
    explicit_backend: str | None = None,
    explicit_db_url: str | None = None,
    root_dir: Path | str = Path("."),
    env: Mapping[str, str] | None = None,
) -> ResolvedDatabaseConfig:
    """Resolve PostgreSQL database configuration from explicit flags and `env`."""
    del root_dir
    src_env: Mapping[str, str] = env if env is not None else {}
    in_container = is_container_env(src_env)

    env_url = str(src_env.get("SPACEINVASION_DB_URL") or "").strip()
    if explicit_db_url and is_postgres_dsn(explicit_db_url):
        pg_url = explicit_db_url.strip()
    elif is_postgres_dsn(env_url):
        pg_url = env_url
    else:
        pg_url = build_default_postgres_url(src_env)

    auto_podman = not in_container
    env_auto = str(src_env.get("SPACEINVASION_AUTO_PODMAN") or "").strip().lower()
    if env_auto in _FALSY:
        auto_podman = False
    elif env_auto in _TRUTHY:
        auto_podman = True

    if explicit_backend is not None and explicit_backend.strip().lower() not in ("", "auto"):
        norm_b = normalize_backend_name(explicit_backend, DEFAULT_DB_BACKEND)
        return ResolvedDatabaseConfig(
            backend=norm_b,
            postgres_url=pg_url,
            auto_start_podman=auto_podman,
            selection_source="container_postgres_only" if in_container else "cli_flag",
        )

    if in_container:
        return ResolvedDatabaseConfig(
            backend="postgres",
            postgres_url=pg_url,
            auto_start_podman=False,
            selection_source="container_postgres_only",
        )

    if explicit_db_url and is_postgres_dsn(explicit_db_url):
        return ResolvedDatabaseConfig(
            backend="postgres",
            postgres_url=pg_url,
            auto_start_podman=auto_podman,
            selection_source="cli_db_url",
        )

    env_backend = str(src_env.get("SPACEINVASION_DB_BACKEND") or "").strip().lower()
    if env_backend and env_backend != "auto":
        return ResolvedDatabaseConfig(
            backend=normalize_backend_name(env_backend, DEFAULT_DB_BACKEND),
            postgres_url=pg_url,
            auto_start_podman=auto_podman,
            selection_source="env_var",
        )
    if is_postgres_dsn(env_url):
        return ResolvedDatabaseConfig(
            backend="postgres",
            postgres_url=pg_url,
            auto_start_podman=auto_podman,
            selection_source="env_db_url",
        )

    return ResolvedDatabaseConfig(
        backend="postgres",
        postgres_url=pg_url,
        auto_start_podman=auto_podman,
        selection_source="default",
    )


__all__ = [
    "DEFAULT_DB_BACKEND",
    "DEFAULT_PG_DB",
    "DEFAULT_PG_HOST",
    "DEFAULT_PG_PASSWORD",
    "DEFAULT_PG_PORT",
    "DEFAULT_PG_USER",
    "VALID_DB_BACKENDS",
    "ResolvedDatabaseConfig",
    "build_default_postgres_url",
    "get_unix_socket_host",
    "is_container_env",
    "is_postgres_dsn",
    "is_unix_socket_dsn",
    "normalize_backend_name",
    "redact_db_url",
    "resolve_database_config",
]
