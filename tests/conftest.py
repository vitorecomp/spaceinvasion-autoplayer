"""Pytest configuration: hermetic network/filesystem guards and disposable Unix-socket PostgreSQL."""

from __future__ import annotations

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


def pytest_configure(config: pytest.Config) -> None:
    install_hermetic_guards()
    redirector = get_pg_redirector()
    redirector.install()
    redirector.enable()


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
    try:
        yield
    finally:
        redirector.reset_test_scope()
        assert_repo_root_clean(baseline, context=f"test teardown ({node_id})")
