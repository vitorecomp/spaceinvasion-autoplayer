#!/usr/bin/env python3
"""Phase 6 test-suite hygiene gate, invariant completeness checker, coverage diff, and sleep auditor."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE_PATH = REPO_ROOT / "scripts" / "coverage_baseline.json"
DEFAULT_TESTS_ROOT = REPO_ROOT / "tests"

SACRED_INVARIANT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rapidfire", ("rapidfire", "1_to_1")),
    ("blast_furnace", ("blast_furnace", "2:1")),
    ("escape", ("escape_flight", "plan_escape_flight")),
    ("bash", ("bash_limit", "bashtracker")),
    ("fusion_tutorial", ("fusion", "tutorial")),
    ("executable_now", ("executable_now",)),
    ("redact_sid", ("redact_sensitive", "sid=")),
)

RETIRED_BASELINE_MODULES: frozenset[str] = frozenset(
    "bin/advisor.py bin/run_bot.py scripts/sync_db.py spaceinvasion/db/postgres.py spaceinvasion/planner/optimizer.py".split()
)


def _g(pkg: str, csv: str, *extra: str) -> tuple[str, ...]:
    return tuple(f"spaceinvasion/{pkg}/{p}" for p in csv.split(",")) + tuple(f"spaceinvasion/{e}" for e in extra)


SPLIT_PACKAGE_PREFIXES: dict[str, tuple[str, ...]] = {
    "bin/commander.py": ("bin/commander.py", "spaceinvasion/runtime/commander/"),
    "spaceinvasion/combat/fleet.py": _g(
        "combat", "fleet.py,cargo.py,flight.py,simulator.py,colonies.py,scanner.py,_scanner_", "parsing/"
    ),
    "spaceinvasion/combat/ranking.py": _g(
        "combat", "ranking.py,points.py,_points_helpers.py,_combat_overview.py,_rank_history.py"
    )
    + _g("planner", "eta.py,_eta_"),
    "spaceinvasion/core/config.py": _g("core", "config.py,settings/", "db/config_store.py"),
    "spaceinvasion/core/game_data.py": _g("core", "game_data.py,catalog/"),
    "spaceinvasion/core/logging_utils.py": _g("core", "logging_utils.py,redaction.py", "runtime/logs/"),
    "spaceinvasion/core/state.py": _g(
        "core", "state.py,model.py,_model_,_inflight.py,coercion.py,coords.py", "parsing/"
    ),
    "spaceinvasion/db/backend.py": _g("db", "backend.py,dsn.py,session.py", "runtime/postgres_ctl.py"),
    "spaceinvasion/db/migrations.py": _g("db", "migrations,alembic_migrations"),
    "spaceinvasion/db/storage.py": _g("db", "storage.py,store.py,models/,repos/,queries/,dto/,session.py"),
    "spaceinvasion/planner/action_graph.py": ("spaceinvasion/planner/graph/",),
    "spaceinvasion/planner/ai_planner.py": ("spaceinvasion/planner/ai/",),
    "spaceinvasion/planner/config_optimizer.py": _g("planner", "config_optimizer,config_opt/"),
    "spaceinvasion/planner/strategy.py": ("spaceinvasion/planner/heuristics/",),
    "spaceinvasion/runtime/api_server.py": _g("runtime", "api_server.py,bridge/,api/"),
    "spaceinvasion/session/client.py": _g("session", "client,browser_client/,limiter.py"),
    "spaceinvasion/session/executor.py": _g("session", "executor,exec_ops/"),
}


@dataclass(frozen=True)
class PrivateImportHit:
    rel_path: str
    lineno: int
    module: str
    symbol: str
    kind: str


@dataclass(frozen=True)
class SleepSite:
    rel_path: str
    lineno: int
    call_name: str
    seconds: float | None
    expr: str


def _emit(line: str, *, err: bool = False) -> None:
    (sys.stderr if err else sys.stdout).write(f"{line}\n")


def _iter_test_files(tests_root: Path) -> list[Path]:
    return (
        [p for p in sorted(tests_root.rglob("*.py")) if "/__pycache__/" not in f"/{p.as_posix()}/"]
        if tests_root.is_dir()
        else []
    )


def _is_priv(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def scan_private_imports(tests_root: Path = DEFAULT_TESTS_ROOT, repo_root: Path = REPO_ROOT) -> list[PrivateImportHit]:
    """AST-scan tests for ``from spaceinvasion... import _sym`` and ``mod._sym`` accesses."""
    hits: list[PrivateImportHit] = []
    for path in _iter_test_files(tests_root):
        rel = path.relative_to(repo_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        si_mod_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "spaceinvasion" or alias.name.startswith("spaceinvasion."):
                        si_mod_aliases[alias.asname or alias.name.split(".")[0]] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("spaceinvasion"):
                for alias in node.names:
                    if _is_priv(alias.name):
                        hits.append(PrivateImportHit(rel, node.lineno, node.module, alias.name, "import"))
                    else:
                        si_mod_aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and _is_priv(node.attr):
                if isinstance(node.value, ast.Name) and node.value.id in si_mod_aliases:
                    hits.append(PrivateImportHit(rel, node.lineno, si_mod_aliases[node.value.id], node.attr, "attr"))
    return hits


def _has_skip_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any("skip" in ast.unparse(dec).lower() for dec in node.decorator_list)


def _is_trivial_body(body: Sequence[ast.stmt]) -> bool:
    stmts = [
        st
        for st in body
        if not (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) and isinstance(st.value.value, str))
    ]
    return not stmts or all(isinstance(st, ast.Pass) for st in stmts)


def check_invariants_suite(tests_root: Path = DEFAULT_TESTS_ROOT) -> tuple[bool, list[str]]:
    """Verify ``tests/invariants/`` contains non-empty unskipped tests for all 7 invariants + layers."""
    inv_dir = tests_root / "invariants"
    if not inv_dir.is_dir():
        return False, [f"Missing invariants directory: {inv_dir}"]
    combined_chunks: list[str] = []
    has_layers_test = False
    for path in sorted(inv_dir.glob("test_*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=path.name)
        active_tests: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                if not _has_skip_decorator(node) and not _is_trivial_body(node.body):
                    seg = ast.get_source_segment(src, node) or node.name
                    active_tests.append(seg.lower())
        if path.name == "test_architecture_layers.py" and active_tests:
            has_layers_test = True
        combined_chunks.extend(active_tests)

    corpus = "\n".join(combined_chunks)
    missing: list[str] = [] if has_layers_test else ["test_architecture_layers.py"]
    for label, markers in SACRED_INVARIANT_RULES:
        if not any(m in corpus for m in markers):
            missing.append(label)
    return not missing, missing


def _numeric_const(node: ast.expr) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    return None


def scan_sleep_sites(tests_root: Path = DEFAULT_TESTS_ROOT, repo_root: Path = REPO_ROOT) -> list[SleepSite]:
    """AST-scan tests for ``time.sleep(...)`` and ``<event>.wait(...)`` calls."""
    sites: list[SleepSite] = []
    for path in _iter_test_files(tests_root):
        rel = path.relative_to(repo_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            attr = node.func.attr
            is_sleep = attr == "sleep" and isinstance(node.func.value, ast.Name) and node.func.value.id == "time"
            is_wait = attr == "wait" and bool(node.args or node.keywords)
            if not (is_sleep or is_wait):
                continue
            call_name = f"{ast.unparse(node.func.value)}.{attr}"
            if "mock" in call_name.lower() or "patch" in call_name.lower() or attr == "wait" and "proc" in call_name:
                continue
            arg_node = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "timeout"), None)
            secs = _numeric_const(arg_node) if arg_node is not None else None
            expr = ast.unparse(arg_node) if arg_node is not None else ""
            sites.append(SleepSite(rel, node.lineno, call_name, secs, expr))
    sites.sort(key=lambda s: (-(s.seconds if s.seconds is not None else -1.0), s.rel_path, s.lineno))
    return sites


def _sum_group_coverage(files_map: Mapping[str, Mapping[str, object]], prefixes: Sequence[str]) -> tuple[int, int]:
    cov, stmts = 0, 0
    for fpath, info in files_map.items():
        if any(fpath == p or fpath.startswith(p) for p in prefixes):
            stats = summary if isinstance(summary := info.get("summary"), Mapping) else info
            raw_c, raw_s = stats.get("covered_lines", 0), stats.get("num_statements", 0)
            cov += int(raw_c) if isinstance(raw_c, (int, float)) else 0
            stmts += int(raw_s) if isinstance(raw_s, (int, float)) else 0
    return cov, stmts


def evaluate_coverage_baseline(
    baseline_path: Path,
    coverage_json_path: Path,
    *,
    tolerance_pct: float = 0.0,
) -> tuple[bool, float, float, list[str]]:
    """Compare ``coverage_json_path`` overall & per-group line coverage against Phase 0 baseline."""
    base = json.loads(baseline_path.read_text(encoding="utf-8"))
    cur = json.loads(coverage_json_path.read_text(encoding="utf-8"))
    base_pct = float((base.get("totals") or {}).get("percent_covered", 0.0))
    cur_pct = float((cur.get("totals") or {}).get("percent_covered", 0.0))
    failures: list[str] = []
    if cur_pct + tolerance_pct < base_pct:
        failures.append(f"Overall coverage {cur_pct:.2f}% < Phase 0 baseline {base_pct:.2f}%")
    cur_files = cur.get("files") or {}
    for rel_path, info in sorted((base.get("files") or {}).items()):
        if rel_path in RETIRED_BASELINE_MODULES or int(info.get("num_statements", 0)) == 0:
            continue
        b_pct = float(info.get("percent_covered", 0.0))
        prefixes = SPLIT_PACKAGE_PREFIXES.get(rel_path, (rel_path,))
        cov, stmts = _sum_group_coverage(cur_files, prefixes)
        if stmts == 0:
            continue
        g_pct = round((cov / stmts) * 100.0, 2)
        if g_pct + tolerance_pct < b_pct:
            failures.append(f"{rel_path} group coverage {g_pct:.2f}% < baseline {b_pct:.2f}% ({cov}/{stmts})")
    return not failures, base_pct, cur_pct, failures


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Phase 6 test hygiene gate, invariants, coverage diff, sleep audit.")
    p.add_argument("--check-private-imports", action="store_true", help="Fail if private imports >= max threshold.")
    p.add_argument("--max-private-imports", type=int, default=20, help="Exclusive max private imports.")
    p.add_argument("--check-invariants", action="store_true", help="Verify 7 sacred invariants + DAG test.")
    p.add_argument("--check-coverage", action="store_true", help="Compare coverage JSON to Phase 0 baseline.")
    p.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH, help="Phase 0 baseline JSON path.")
    p.add_argument("--coverage-json", type=Path, default=None, help="Current pytest-cov JSON report path.")
    p.add_argument("--coverage-tolerance", type=float, default=0.0, help="Allowed percentage-point tolerance.")
    p.add_argument("--audit-sleeps", action="store_true", help="List top time.sleep / Event.wait sites.")
    p.add_argument("--top", type=int, default=25, help="Max top offenders/sites to display.")
    p.add_argument("--tests-root", type=Path, default=DEFAULT_TESTS_ROOT, help="Tests root directory.")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    run_all = not (args.check_private_imports or args.check_invariants or args.check_coverage or args.audit_sleeps)
    repo_root = REPO_ROOT if args.tests_root == DEFAULT_TESTS_ROOT else args.tests_root.parent
    ok = True

    if run_all or args.check_private_imports:
        hits = scan_private_imports(args.tests_root, repo_root)
        by_file = Counter(h.rel_path for h in hits)
        _emit(f"[private-imports] total={len(hits)} files={len(by_file)} threshold<{args.max_private_imports}")
        for fpath, cnt in by_file.most_common(args.top):
            _emit(f"  {cnt:3d}  {fpath}")
        if args.check_private_imports and len(hits) >= args.max_private_imports:
            _emit(f"[FAIL] Private imports {len(hits)} >= {args.max_private_imports}", err=True)
            ok = False

    if run_all or args.check_invariants:
        inv_ok, missing = check_invariants_suite(args.tests_root)
        _emit(f"[invariants] ok={inv_ok} missing={missing}")
        if not inv_ok:
            _emit(f"[FAIL] Missing invariant coverage: {', '.join(missing)}", err=True)
            ok = False

    if args.check_coverage:
        if args.coverage_json is None or not args.coverage_json.is_file():
            _emit("[FAIL] --check-coverage requires a valid --coverage-json path", err=True)
            return 2
        cov_ok, base_pct, cur_pct, cov_failures = evaluate_coverage_baseline(
            args.baseline, args.coverage_json, tolerance_pct=args.coverage_tolerance
        )
        _emit(f"[coverage] current={cur_pct:.2f}% baseline={base_pct:.2f}% ok={cov_ok}")
        for item in cov_failures[: args.top]:
            _emit(f"  [FAIL] {item}", err=True)
        ok = ok and cov_ok

    if run_all or args.audit_sleeps:
        sites = scan_sleep_sites(args.tests_root, repo_root)
        _emit(f"[sleeps] total={len(sites)}")
        for site in sites[: args.top]:
            dur = f"{site.seconds:g}s" if site.seconds is not None else site.expr
            _emit(f"  {site.rel_path}:{site.lineno}: {site.call_name}({dur})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
