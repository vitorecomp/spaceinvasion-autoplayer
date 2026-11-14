"""Unit tests for ``scripts/check_test_hygiene.py`` (private imports, invariants, coverage, sleeps)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts import check_test_hygiene as cth

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_private_import_scanner_counts_imports_and_module_attrs(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "\n".join(
            [
                "from spaceinvasion.core import catalog as gd",
                "from spaceinvasion.core.coercion import _helper_fn, __all__, coerce_int",
                "import spaceinvasion.combat.flight as flt",
                "def test_x():",
                "    _ = gd._internal_dict",
                "    _ = flt._private_helper()",
                "    _ = coerce_int('1')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    hits = cth.scan_private_imports(tests_dir, tmp_path)
    assert [(h.symbol, h.kind) for h in hits] == [
        ("_helper_fn", "import"),
        ("_internal_dict", "attr"),
        ("_private_helper", "attr"),
    ]
    rc_fail = cth.main(["--tests-root", str(tests_dir), "--check-private-imports", "--max-private-imports", "3"])
    rc_ok = cth.main(["--tests-root", str(tests_dir), "--check-private-imports", "--max-private-imports", "4"])
    assert rc_fail == 1
    assert rc_ok == 0


def test_invariants_check_passes_on_repo_and_flags_skipped_or_empty(tmp_path: Path) -> None:
    ok_repo, missing_repo = cth.check_invariants_suite(REPO_ROOT / "tests")
    assert ok_repo and missing_repo == []

    bad_tests = tmp_path / "tests" / "invariants"
    bad_tests.mkdir(parents=True)
    (bad_tests / "test_economy_invariants.py").write_text(
        "import pytest\n\n@pytest.mark.skip(reason='no')\ndef test_blast_furnace_fusion_tutorial_executable_now():\n    assert True\n",
        encoding="utf-8",
    )
    ok_bad, missing_bad = cth.check_invariants_suite(tmp_path / "tests")
    assert not ok_bad
    assert "test_architecture_layers.py" in missing_bad
    assert "blast_furnace" in missing_bad


def test_coverage_evaluator_handles_split_packages_and_regressions(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "totals": {"percent_covered": 82.24},
                "files": {
                    "spaceinvasion/planner/strategy.py": {
                        "covered_lines": 80,
                        "num_statements": 100,
                        "percent_covered": 80.0,
                    },
                    "bin/advisor.py": {"covered_lines": 10, "num_statements": 100, "percent_covered": 10.0},
                },
            },
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    cov_good = tmp_path / "cov_good.json"
    cov_good.write_text(
        json.dumps(
            {
                "totals": {"percent_covered": 83.1},
                "files": {
                    "spaceinvasion/planner/heuristics/assembly.py": {
                        "executed_lines": [1],
                        "summary": {"covered_lines": 45, "num_statements": 50},
                    },
                    "spaceinvasion/planner/heuristics/economy.py": {
                        "executed_lines": [1],
                        "summary": {"covered_lines": 40, "num_statements": 50},
                    },
                },
            },
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    ok, base_pct, cur_pct, fails = cth.evaluate_coverage_baseline(baseline, cov_good)
    assert ok and fails == []
    assert base_pct == 82.24 and cur_pct == 83.1

    cov_bad = tmp_path / "cov_bad.json"
    cov_bad.write_text(
        json.dumps(
            {
                "totals": {"percent_covered": 82.5},
                "files": {
                    "spaceinvasion/planner/heuristics/assembly.py": {
                        "executed_lines": [1],
                        "summary": {"covered_lines": 30, "num_statements": 50},
                    },
                },
            },
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    ok_bad, _, _, fails_bad = cth.evaluate_coverage_baseline(baseline, cov_bad)
    assert not ok_bad
    assert any("strategy.py group coverage 60.00% < baseline 80.00%" in msg for msg in fails_bad)


def test_sleep_auditor_finds_literal_sleeps_and_waits(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_slow.py").write_text(
        "import threading, time\ndef test_wait():\n    ev = threading.Event()\n    time.sleep(0.25)\n    ev.wait(timeout=1.5)\n",
        encoding="utf-8",
    )
    sites = cth.scan_sleep_sites(tests_dir, tmp_path)
    assert [(s.call_name, s.seconds) for s in sites] == [("ev.wait", 1.5), ("time.sleep", 0.25)]


def test_lazy_package_exports_and_capture_goldens_list() -> None:
    import pytest

    from scripts import capture_goldens
    import spaceinvasion.db as db_pkg
    import spaceinvasion.planner as planner_pkg

    assert planner_pkg.__getattr__("heuristics") is not None
    with pytest.raises(AttributeError):
        _ = getattr(planner_pkg, "nonexistent_attr")

    assert db_pkg.__getattr__("migrations") is not None
    assert db_pkg.__getattr__("DatabaseStore") is not None
    with pytest.raises(AttributeError):
        _ = getattr(db_pkg, "nonexistent_attr")

    assert capture_goldens.main(["--list"]) == 0
