"""Pytest configuration: hermetic network/filesystem guards and disposable Unix-socket PostgreSQL."""

from __future__ import annotations

import os
from typing import Iterator

import pytest

from tests.pg_harness import (
    DisposablePostgresServer,
    assert_repo_root_clean,
    get_pg_redirector,
    get_shared_pg_harness,
    install_hermetic_guards,
    shutdown_shared_pg_harness,
    snapshot_repo_root,
)

# Install hermetic guards immediately when pytest loads conftest.py
install_hermetic_guards()
_SESSION_ROOT_BASELINE = snapshot_repo_root()

# Tests that explicitly test SQLite dynamic-affinity corruption, SQLite file copying,
# or SQLite multi-file CLI migration mechanics (retired in Phase 1.9).
_EXPLICIT_SQLITE_NODE_SUBSTRINGS: tuple[str, ...] = (
    "test_corrupt_numeric_cells_in_readers_and_spy_report_coord_normalization",
    "test_spy_target_direct_lookup_galaxy_coalescing_and_corrupt_snapshot_guard",
    "test_r10_blob_sid_redaction_literal_sid_json_and_planet_history",
    "test_unnormalized_coords_api_guards_external_research_cancel_and_deep_sid_redaction",
    "test_savepoint_rollback_on_failed_migration_and_checksum_drift_and_future_version_guard",
    "test_multi_database_registry_cli_json_sidecar_and_http_api_commands",
    "test_v013_staged_upgrade_from_v012_checksum_tiers_and_cli_verify",
    "test_v014_hourly_production_and_loot_recycle_hourly_upgrade_and_backfill",
)


def _pg_redirection_requested() -> bool:
    raw = os.environ.get("SPACEINVASION_TEST_DB_BACKEND", "postgres").strip().lower()
    return raw not in ("sqlite", "sqlite3", "0", "false", "off")


def pytest_configure(config: pytest.Config) -> None:
    install_hermetic_guards()
    redirector = get_pg_redirector()
    redirector.install()
    if _pg_redirection_requested():
        redirector.enable()
    else:
        redirector.disable()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    redirector = get_pg_redirector()
    redirector.reset_test_scope()
    shutdown_shared_pg_harness()
    assert_repo_root_clean(_SESSION_ROOT_BASELINE, context="pytest session teardown")


@pytest.fixture(scope="session")
def pg_harness() -> Iterator[DisposablePostgresServer]:
    """Session-scoped disposable Unix-socket PostgreSQL 16 server."""
    harness = get_shared_pg_harness()
    yield harness
    get_pg_redirector().reset_test_scope()
    shutdown_shared_pg_harness()


@pytest.fixture()
def pg_dsn(pg_harness: DisposablePostgresServer) -> str:
    """Function-scoped fresh cloned PostgreSQL database DSN (`TEMPLATE si_template`)."""
    return pg_harness.create_database(prefix="test", from_template=True)


@pytest.fixture(autouse=True)
def _hermetic_per_test_isolation(request: pytest.FixtureRequest) -> Iterator[None]:
    """Enforce per-test repo-root cleanliness and isolate disposable PostgreSQL DB mappings."""
    baseline = snapshot_repo_root()
    redirector = get_pg_redirector()
    redirector.reset_test_scope()

    node_id = str(getattr(request.node, "nodeid", ""))
    explicit_sqlite = any(tok in node_id for tok in _EXPLICIT_SQLITE_NODE_SUBSTRINGS)
    if explicit_sqlite:
        with redirector.allow_sqlite():
            try:
                yield
            finally:
                redirector.reset_test_scope()
                assert_repo_root_clean(baseline, context=f"test teardown ({node_id})")
    else:
        try:
            yield
        finally:
            redirector.reset_test_scope()
            assert_repo_root_clean(baseline, context=f"test teardown ({node_id})")
