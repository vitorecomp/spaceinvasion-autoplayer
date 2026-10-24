"""Phase 4 94/94 API golden parity suite against ModularGameApiBridge + create_modular_api_server."""

from __future__ import annotations

from contextlib import ExitStack
import difflib
import fnmatch
import importlib
import pkgutil
import sys
from types import ModuleType
from unittest import mock

import pytest
import requests

from scripts.capture_goldens import BACKEND_SPECIFIC_PATHS, REFERENCE_DIR, _drop_path, render
from spaceinvasion.core import config as config_mod
from spaceinvasion.core import model as state_mod
import spaceinvasion.runtime.api as api_pkg
import spaceinvasion.runtime.bridge as bridge_pkg
from tests.e2e.harness import E2ETestHarness, _ClockTimeProxy
from tests.goldens.api_scenario import (
    API_STEPS,
    REMOVED_COMMANDS,
    FrozenClock,
    _fake_game_side_effects,
    _send,
    build_config,
    build_server,
)
from tests.goldens.scenarios import GOLDEN_EPOCH


def _iter_submodules(pkg: ModuleType) -> list[ModuleType]:
    mods: list[ModuleType] = [pkg]
    pkg_path = pkg.__path__
    for mod_info in pkgutil.walk_packages(pkg_path, prefix=f"{pkg.__name__}."):
        mod = sys.modules.get(mod_info.name)
        if mod is None:
            mod = importlib.import_module(mod_info.name)
        mods.append(mod)
    return mods


def _strip_backend_paths(case_name: str, data: object) -> object:
    if not isinstance(data, dict):
        return data
    for pattern, dotted in BACKEND_SPECIFIC_PATHS:
        if fnmatch.fnmatchcase(case_name, pattern):
            _drop_path(data, dotted.split("."))
    return data


def _run_modular_api_session(*, timeout: float = 600.0) -> dict[str, object]:
    for step in API_STEPS:
        if step.command is not None and step.command.get("command") in REMOVED_COMMANDS:
            raise AssertionError(f"{step.case} uses a command removed by Decision D5")

    clock = FrozenClock(GOLDEN_EPOCH)
    server = build_server(clock)
    out: dict[str, object] = {}
    http = requests.Session()
    http.trust_env = False
    proxy = _ClockTimeProxy(clock)

    harness = E2ETestHarness(server=server, clock=clock, config=build_config(), http_timeout=timeout)
    with ExitStack() as stack:
        stack.callback(http.close)
        # Freeze `time` across core modules (E2ETestHarness freezes all bridge & api submodules).
        for module in (state_mod, config_mod, *_iter_submodules(bridge_pkg), *_iter_submodules(api_pkg)):
            if "time" in module.__dict__:
                stack.enter_context(mock.patch.object(module, "time", proxy))

        h = stack.enter_context(harness)
        for step in API_STEPS:
            out[step.case] = _send(http, h.base_url, step, server, clock, timeout)
        out["150_fake_game_side_effects"] = _fake_game_side_effects(server)
    return out


@pytest.mark.usefixtures("pg_dsn")
def test_modular_bridge_matches_all_94_api_goldens() -> None:
    """Prove ModularGameApiBridge + create_modular_api_server match 94/94 reference API goldens."""
    api_ref_dir = REFERENCE_DIR / "api"
    expected_files = sorted(api_ref_dir.glob("*.json"))
    assert len(expected_files) == 94, f"Expected 94 reference API goldens, found {len(expected_files)}"

    captured = _run_modular_api_session()
    assert len(captured) == 94

    mismatches: list[str] = []
    for ref_path in expected_files:
        case_name = ref_path.stem
        assert case_name in captured, f"Missing captured case {case_name}"
        actual_obj = _strip_backend_paths(case_name, captured[case_name])
        actual_txt = render(actual_obj)
        expected_txt = ref_path.read_text(encoding="utf-8")
        if actual_txt != expected_txt:
            diff_lines = list(
                difflib.unified_diff(
                    expected_txt.splitlines(),
                    actual_txt.splitlines(),
                    fromfile=f"reference/{case_name}.json",
                    tofile=f"modular/{case_name}.json",
                    n=2,
                )
            )
            preview = "\n".join(diff_lines[:40])
            mismatches.append(f"=== {case_name} ===\n{preview}")

    assert not mismatches, f"{len(mismatches)}/94 API golden mismatch(es):\n" + "\n\n".join(mismatches[:8])
