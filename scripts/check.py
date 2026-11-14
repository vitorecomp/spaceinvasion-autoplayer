#!/usr/bin/env python3
"""Unified repository quality gate and Section 4 engineering-standards ratchet.

Runs:
  1. `ruff check` (ratcheted per-file/rule against `scripts/quality_baseline.json`)
  2. `mypy` (ratcheted per-file against `scripts/quality_baseline.json`)
  3. AST engineering-standards ratchet on production Python (excluding
     `spaceinvasion/core/game_data.py` and `spaceinvasion/ui/`):
       - module <= 800 LOC
       - class <= 400 LOC
       - function/method <= 100 LOC
       - broad `except` handlers <= baseline
       - `print()` calls <= baseline
       - `os.environ` / `os.getenv` reads <= baseline
  4. `pytest` (default `-m 'not notebooks'` from `pyproject.toml`)
  5. `npm --prefix frontend test`
  6. `npm --prefix frontend exec tsc -- --noEmit`
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from spaceinvasion.core.env import get_process_env  # noqa: E402

BASELINE_PATH = REPO_ROOT / "scripts" / "quality_baseline.json"
COVERAGE_BASELINE_PATH = REPO_ROOT / "scripts" / "coverage_baseline.json"
PROD_ROOTS = ("spaceinvasion", "bin", "scripts")
EXCLUDED_AST_FILES = frozenset({"spaceinvasion/core/game_data.py"})

MAX_MODULE_LOC = 800
MAX_CLASS_LOC = 400
MAX_FUNCTION_LOC = 100

_MYPY_ERR_RE = re.compile(r"^(?P<path>[^:\n]+):\d+(?::\d+)?: error: (?P<msg>.*)$")


@dataclass(frozen=True)
class AstInventory:
    """Measured AST metrics across production Python files."""

    module_loc: dict[str, int]
    class_loc: dict[str, int]
    function_loc: dict[str, int]
    broad_except: dict[str, int]
    print_calls: dict[str, int]
    environ_reads: dict[str, int]


def _emit(line: str, *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    stream.write(f"{line}\n")


def _resolve_python_tool(tool_name: str) -> list[str]:
    exe_dir = Path(sys.executable).resolve().parent
    candidate = exe_dir / tool_name
    if candidate.is_file():
        return [str(candidate)]
    venv_candidate = REPO_ROOT / ".venv" / "bin" / tool_name
    if venv_candidate.is_file():
        return [str(venv_candidate)]
    found = shutil.which(tool_name)
    if found:
        return [found]
    return [sys.executable, "-m", tool_name]


def _resolve_npm() -> str:
    found = shutil.which("npm")
    if not found:
        raise FileNotFoundError("npm executable not found on PATH")
    return found


def _iter_prod_python_files() -> list[Path]:
    files: list[Path] = []
    for root_name in PROD_ROOTS:
        root_dir = REPO_ROOT / root_name
        if not root_dir.is_dir():
            continue
        for path in sorted(root_dir.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith("spaceinvasion/ui/") or "/__pycache__/" in f"/{rel}/":
                continue
            if rel in EXCLUDED_AST_FILES:
                continue
            files.append(path)
    return files


def _is_broad_except(handler: ast.ExceptHandler) -> bool:
    typ = handler.type
    if typ is None:
        return True
    if isinstance(typ, ast.Name):
        return typ.id in ("Exception", "BaseException")
    if isinstance(typ, ast.Tuple):
        return any(isinstance(elt, ast.Name) and elt.id in ("Exception", "BaseException") for elt in typ.elts)
    return False


class _AstMetricVisitor(ast.NodeVisitor):
    """Collect class/function LOC and smell counters for a single module."""

    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        self._scope: list[str] = []
        self.class_loc: dict[str, int] = {}
        self.function_loc: dict[str, int] = {}
        self.broad_except = 0
        self.print_calls = 0
        self.environ_reads = 0

    def _qualname(self, name: str) -> str:
        return ".".join([*self._scope, name]) if self._scope else name

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qual = self._qualname(node.name)
        end = node.end_lineno or node.lineno
        lines = end - node.lineno + 1
        if lines > MAX_CLASS_LOC:
            self.class_loc[f"{self.rel_path}:{qual}"] = lines
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qual = self._qualname(node.name)
        end = node.end_lineno or node.lineno
        lines = end - node.lineno + 1
        if lines > MAX_FUNCTION_LOC:
            self.function_loc[f"{self.rel_path}:{qual}"] = lines
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if _is_broad_except(node):
            self.broad_except += 1
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            self.print_calls += 1
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr in ("environ", "getenv"):
            self.environ_reads += 1
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "os":
            for alias in node.names:
                if alias.name in ("environ", "getenv"):
                    self.environ_reads += 1
        self.generic_visit(node)


def collect_ast_inventory() -> AstInventory:
    """Measure Section 4 AST metrics across all non-excluded production Python files."""
    module_loc: dict[str, int] = {}
    class_loc: dict[str, int] = {}
    function_loc: dict[str, int] = {}
    broad_except: dict[str, int] = {}
    print_calls: dict[str, int] = {}
    environ_reads: dict[str, int] = {}

    for path in _iter_prod_python_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        loc = len(source.splitlines())
        if loc > MAX_MODULE_LOC:
            module_loc[rel] = loc

        tree = ast.parse(source, filename=rel)
        visitor = _AstMetricVisitor(rel)
        visitor.visit(tree)

        class_loc.update(visitor.class_loc)
        function_loc.update(visitor.function_loc)
        if visitor.broad_except:
            broad_except[rel] = visitor.broad_except
        if visitor.print_calls:
            print_calls[rel] = visitor.print_calls
        if visitor.environ_reads:
            environ_reads[rel] = visitor.environ_reads

    return AstInventory(
        module_loc=dict(sorted(module_loc.items())),
        class_loc=dict(sorted(class_loc.items())),
        function_loc=dict(sorted(function_loc.items())),
        broad_except=dict(sorted(broad_except.items())),
        print_calls=dict(sorted(print_calls.items())),
        environ_reads=dict(sorted(environ_reads.items())),
    )


def collect_ruff_counts() -> tuple[dict[str, dict[str, int]], list[str]]:
    """Run `ruff check` and return `(counts_by_file_and_code, sample_violations)`."""
    cmd = [*_resolve_python_tool("ruff"), "check", "--output-format=json", *PROD_ROOTS]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"ruff failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    payload = json.loads(proc.stdout or "[]")
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    samples: list[str] = []
    for item in payload:
        raw_file = str(item.get("filename") or "")
        try:
            rel = Path(raw_file).resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = raw_file
        code = str(item.get("code") or "UNKNOWN")
        counts[rel][code] += 1
        loc = item.get("location") or {}
        samples.append(f"{rel}:{loc.get('row', '?')}: {code} {item.get('message', '')}")
    normalized = {path: dict(sorted(counter.items())) for path, counter in sorted(counts.items())}
    return normalized, samples


def collect_mypy_counts() -> tuple[dict[str, int], dict[str, list[str]]]:
    """Run `mypy` and return `(error_counts_by_file, error_lines_by_file)`."""
    cmd = [*_resolve_python_tool("mypy"), "--no-error-summary", "--show-column-numbers", *PROD_ROOTS]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    counts: Counter[str] = Counter()
    lines_by_file: dict[str, list[str]] = defaultdict(list)
    for line in (proc.stdout or "").splitlines():
        match = _MYPY_ERR_RE.match(line)
        if not match:
            continue
        raw_path = match.group("path")
        path_obj = Path(raw_path)
        if path_obj.is_absolute():
            try:
                rel = path_obj.resolve().relative_to(REPO_ROOT).as_posix()
            except ValueError:
                rel = raw_path
        else:
            rel = path_obj.as_posix()
        counts[rel] += 1
        lines_by_file[rel].append(line)
    if proc.returncode not in (0, 1) and not counts:
        raise RuntimeError(f"mypy failed ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    return dict(sorted(counts.items())), dict(lines_by_file)


def _check_flat_ratchet(
    label: str,
    current: dict[str, int],
    baseline: dict[str, int],
    *,
    limit: int = 0,
) -> list[str]:
    failures: list[str] = []
    for key, val in sorted(current.items()):
        allowed = baseline.get(key, limit)
        if val > allowed:
            failures.append(f"[{label}] {key}: {val} > baseline {allowed}")
    return failures


def _check_ruff_ratchet(
    current: dict[str, dict[str, int]],
    baseline: dict[str, dict[str, int]],
    samples: Sequence[str],
) -> list[str]:
    failures: list[str] = []
    bad_files: set[str] = set()
    for path, codes in sorted(current.items()):
        allowed_codes = baseline.get(path, {})
        for code, count in sorted(codes.items()):
            allowed = allowed_codes.get(code, 0)
            if count > allowed:
                bad_files.add(path)
                failures.append(f"[ruff] {path} ({code}): {count} > baseline {allowed}")
    if bad_files:
        for sample in samples:
            sample_path = sample.split(":", 1)[0]
            if sample_path in bad_files:
                failures.append(f"  sample: {sample}")
                if len(failures) > 40:
                    break
    return failures


def _check_mypy_ratchet(
    current: dict[str, int],
    baseline: dict[str, int],
    lines_by_file: dict[str, list[str]],
) -> list[str]:
    failures: list[str] = []
    for path, count in sorted(current.items()):
        allowed = baseline.get(path, 0)
        if count > allowed:
            failures.append(f"[mypy] {path}: {count} > baseline {allowed}")
            for line in lines_by_file.get(path, [])[:5]:
                failures.append(f"  {line}")
    return failures


def _build_baseline_payload() -> dict[str, Any]:
    ast_inv = collect_ast_inventory()
    ruff_counts, _ = collect_ruff_counts()
    mypy_counts, _ = collect_mypy_counts()
    return {
        "schema_version": 1,
        "limits": {
            "module_loc": MAX_MODULE_LOC,
            "class_loc": MAX_CLASS_LOC,
            "function_loc": MAX_FUNCTION_LOC,
        },
        "ast": {
            "module_loc": ast_inv.module_loc,
            "class_loc": ast_inv.class_loc,
            "function_loc": ast_inv.function_loc,
            "broad_except": ast_inv.broad_except,
            "print_calls": ast_inv.print_calls,
            "environ_reads": ast_inv.environ_reads,
        },
        "ruff": ruff_counts,
        "mypy": mypy_counts,
    }


def _run_subprocess_step(name: str, cmd: Sequence[str]) -> bool:
    _emit(f"==> {name}: {' '.join(cmd)}")
    proc = subprocess.run(list(cmd), cwd=REPO_ROOT, check=False)
    if proc.returncode != 0:
        _emit(f"[FAIL] {name} exited with {proc.returncode}", err=True)
        return False
    _emit(f"[PASS] {name}")
    return True


def _run_static_gates() -> bool:
    if not BASELINE_PATH.is_file():
        _emit(f"[FAIL] Missing baseline file: {BASELINE_PATH}", err=True)
        return False

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    ast_base = baseline.get("ast") or {}

    _emit("==> ruff check (ratchet)")
    ruff_counts, ruff_samples = collect_ruff_counts()
    ruff_failures = _check_ruff_ratchet(ruff_counts, baseline.get("ruff") or {}, ruff_samples)

    _emit("==> mypy (ratchet)")
    mypy_counts, mypy_lines = collect_mypy_counts()
    mypy_failures = _check_mypy_ratchet(mypy_counts, baseline.get("mypy") or {}, mypy_lines)

    _emit("==> AST engineering-standards ratchet")
    ast_inv = collect_ast_inventory()
    ast_failures = [
        *_check_flat_ratchet("module_loc", ast_inv.module_loc, ast_base.get("module_loc") or {}, limit=MAX_MODULE_LOC),
        *_check_flat_ratchet("class_loc", ast_inv.class_loc, ast_base.get("class_loc") or {}, limit=MAX_CLASS_LOC),
        *_check_flat_ratchet(
            "function_loc", ast_inv.function_loc, ast_base.get("function_loc") or {}, limit=MAX_FUNCTION_LOC
        ),
        *_check_flat_ratchet("broad_except", ast_inv.broad_except, ast_base.get("broad_except") or {}, limit=0),
        *_check_flat_ratchet("print_calls", ast_inv.print_calls, ast_base.get("print_calls") or {}, limit=0),
        *_check_flat_ratchet("environ_reads", ast_inv.environ_reads, ast_base.get("environ_reads") or {}, limit=0),
    ]

    all_failures = [*ruff_failures, *mypy_failures, *ast_failures]
    if all_failures:
        for item in all_failures:
            _emit(item, err=True)
        _emit(f"[FAIL] Static gates failed with {len(all_failures)} regression(s).", err=True)
        return False

    _emit(
        "[PASS] Static gates OK "
        f"(ruff files={len(ruff_counts)}, mypy files={len(mypy_counts)}, "
        f"big modules={len(ast_inv.module_loc)}, big classes={len(ast_inv.class_loc)}, "
        f"big funcs={len(ast_inv.function_loc)})"
    )
    return True


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run quality gates, ratchets, and test suites.")
    parser.add_argument(
        "--lint-only",
        action="store_true",
        help="Run only ruff, mypy, and AST ratchet checks (skip pytest, frontend tests, and tsc).",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip pytest and vitest while keeping static checks and frontend tsc.",
    )
    parser.add_argument(
        "--format-check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run `ruff format --check` across production Python and tests (default: enabled).",
    )
    parser.add_argument(
        "--record-coverage",
        action="store_true",
        help="Run pytest with coverage and record per-module coverage to scripts/coverage_baseline.json.",
    )
    parser.add_argument(
        "--check-coverage",
        action="store_true",
        help="Run pytest with coverage and verify >= Phase 0 baseline via scripts/check_test_hygiene.py.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, max(1, os.cpu_count() or 1)),
        help="Number of parallel file-sharded pytest worker processes (default: min(8, cpu_count)).",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Regenerate scripts/quality_baseline.json from the current workspace.",
    )
    return parser


def _write_coverage_baseline(raw_cov_json: Path) -> None:
    payload = json.loads(raw_cov_json.read_text(encoding="utf-8"))
    files_raw = payload.get("files") or {}
    files_out: dict[str, dict[str, Any]] = {}
    for file_key, file_info in sorted(files_raw.items()):
        summary = (file_info or {}).get("summary") or {}
        files_out[str(file_key)] = {
            "covered_lines": int(summary.get("covered_lines", 0)),
            "num_statements": int(summary.get("num_statements", 0)),
            "missing_lines": int(summary.get("missing_lines", 0)),
            "excluded_lines": int(summary.get("excluded_lines", 0)),
            "percent_covered": round(float(summary.get("percent_covered", 0.0)), 2),
        }
    totals_raw = payload.get("totals") or {}
    baseline = {
        "schema_version": 1,
        "totals": {
            "covered_lines": int(totals_raw.get("covered_lines", 0)),
            "num_statements": int(totals_raw.get("num_statements", 0)),
            "missing_lines": int(totals_raw.get("missing_lines", 0)),
            "excluded_lines": int(totals_raw.get("excluded_lines", 0)),
            "num_branches": int(totals_raw.get("num_branches", 0)),
            "covered_branches": int(totals_raw.get("covered_branches", 0)),
            "percent_covered": round(float(totals_raw.get("percent_covered", 0.0)), 2),
        },
        "files": files_out,
    }
    COVERAGE_BASELINE_PATH.write_text(
        json.dumps(baseline, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _emit(f"Wrote coverage baseline to {COVERAGE_BASELINE_PATH.relative_to(REPO_ROOT)} ({len(files_out)} files)")


_HEAVY_TEST_FILES: frozenset[str] = frozenset(
    {
        "tests/goldens/test_reference_outputs.py",
        "tests/unit/runtime/test_api_goldens_parity.py",
        "tests/unit/runtime/test_api_and_commander.py",
        "tests/unit/runtime/test_r2_throttle.py",
        "tests/e2e/test_tier5_adversarial.py",
        "tests/e2e/test_tier2_r2.py",
        "tests/e2e/test_tier4_workflows.py",
        "tests/unit/combat/test_smart_galaxy_scan.py",
        "tests/unit/runtime/test_settings_and_ts_codegen.py",
    }
)


def _collect_test_shards(pytest_base: Sequence[str], workers: int) -> list[list[str]]:
    proc = subprocess.run(
        [*pytest_base, "-p", "no:cacheprovider", "--collect-only", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pytest --collect-only failed ({proc.returncode}): {proc.stdout}\n{proc.stderr}")
    files: list[str] = []
    seen: set[str] = set()
    for line in proc.stdout.splitlines():
        if "::" not in line:
            continue
        rel = line.split("::", 1)[0].strip()
        if rel not in seen and (REPO_ROOT / rel).is_file():
            seen.add(rel)
            files.append(rel)
    if workers <= 1 or len(files) <= 1:
        return [files] if files else []
    n_shards = min(workers, len(files))
    weighted = sorted(
        files,
        key=lambda rel: ((REPO_ROOT / rel).stat().st_size + (400_000 if rel in _HEAVY_TEST_FILES else 0), rel),
        reverse=True,
    )
    shards: list[list[str]] = [[] for _ in range(n_shards)]
    loads = [0] * n_shards
    for rel in weighted:
        w = (REPO_ROOT / rel).stat().st_size + (400_000 if rel in _HEAVY_TEST_FILES else 0)
        idx = min(range(n_shards), key=lambda i: loads[i])
        shards[idx].append(rel)
        loads[idx] += w
    return [s for s in shards if s]


def _run_pytest_shard(
    idx: int,
    total: int,
    shard_files: Sequence[str],
    pytest_base: Sequence[str],
    cov_file: Path | None,
) -> tuple[int, int, float, str]:
    t0 = time.monotonic()
    cmd = [*pytest_base, "-p", "no:cacheprovider", "-q"]
    env = get_process_env()
    if cov_file is not None:
        cmd.extend(["--cov=spaceinvasion", "--cov=bin", "--cov=scripts", "--cov-report="])
        env["COVERAGE_FILE"] = str(cov_file)
    cmd.extend(shard_files)
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False)
    elapsed = time.monotonic() - t0
    out = (proc.stdout or "") + (proc.stderr or "")
    summary = out.strip().splitlines()[-1] if out.strip() else f"rc={proc.returncode}"
    _emit(f"  [shard {idx + 1}/{total}] ({elapsed:.1f}s) {summary}")
    return idx, proc.returncode, elapsed, out


def _finalize_coverage(cov_dir: Path, cov_files: Sequence[Path], cov_json: Path) -> bool:
    env = get_process_env()
    env["COVERAGE_FILE"] = str(cov_dir / ".coverage")
    cov_tool = _resolve_python_tool("coverage")
    if len(cov_files) > 1:
        comb = subprocess.run(
            [*cov_tool, "combine", "--keep", *[str(p) for p in cov_files]],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if comb.returncode != 0:
            _emit(f"[FAIL] coverage combine failed:\n{comb.stdout}\n{comb.stderr}", err=True)
            return False
    elif cov_files and cov_files[0].is_file():
        shutil.copyfile(cov_files[0], cov_dir / ".coverage")
    rep = subprocess.run(
        [*cov_tool, "json", "-o", str(cov_json)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if rep.returncode != 0 or not cov_json.is_file():
        _emit(f"[FAIL] coverage json failed:\n{rep.stdout}\n{rep.stderr}", err=True)
        return False
    return True


def _run_pytest_step(*, workers: int, record_coverage: bool, check_coverage: bool) -> bool:
    pytest_base = _resolve_python_tool("pytest")
    collect_cov = record_coverage or check_coverage
    if workers <= 1 and not collect_cov:
        return _run_subprocess_step("pytest", [*pytest_base, "-p", "no:cacheprovider"])

    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="si_cov_") as tmp_dir:
        cov_dir = Path(tmp_dir)
        cov_json = cov_dir / "coverage.json"
        if workers <= 1:
            cmd = [
                *pytest_base,
                "-p",
                "no:cacheprovider",
                "--cov=spaceinvasion",
                "--cov=bin",
                "--cov=scripts",
                f"--cov-report=json:{cov_json}",
            ]
            env = {**get_process_env(), "COVERAGE_FILE": str(cov_dir / ".coverage")}
            _emit(f"==> pytest (with coverage): {' '.join(cmd)}")
            ok = subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=False).returncode == 0
        else:
            shards = _collect_test_shards(pytest_base, workers)
            _emit(f"==> pytest ({len(shards)} parallel shards, cov={collect_cov})")
            cov_files = [cov_dir / f".coverage.{i}" for i in range(len(shards))] if collect_cov else []
            with ThreadPoolExecutor(max_workers=len(shards)) as pool:
                futs = [
                    pool.submit(
                        _run_pytest_shard, i, len(shards), s, pytest_base, cov_files[i] if collect_cov else None
                    )
                    for i, s in enumerate(shards)
                ]
                results = [f.result() for f in futs]
            failed = [r for r in results if r[1] != 0]
            for idx, rc, _elapsed, out in failed:
                _emit(f"[FAIL] pytest shard {idx + 1} exited with {rc}:\n{out}", err=True)
            ok = not failed
            if ok and collect_cov:
                ok = _finalize_coverage(cov_dir, cov_files, cov_json)
            _emit(f"[{'PASS' if ok else 'FAIL'}] pytest ({len(shards)} shards in {time.monotonic() - t0:.2f}s)")
        if ok and record_coverage and cov_json.is_file():
            _write_coverage_baseline(cov_json)
        if ok and check_coverage:
            ok = _run_subprocess_step(
                "coverage baseline check",
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "check_test_hygiene.py"),
                    "--check-coverage",
                    "--baseline",
                    str(COVERAGE_BASELINE_PATH),
                    "--coverage-json",
                    str(cov_json),
                ],
            )
        return ok


def _run_contract_and_hygiene_gates() -> bool:
    steps = (
        (
            "ts types sync",
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "generate_ts_types.py"),
                "--check",
                str(REPO_ROOT / "frontend" / "src" / "types" / "generated.ts"),
            ],
        ),
        ("docs sync", [sys.executable, str(REPO_ROOT / "scripts" / "generate_docs.py"), "--check"]),
        (
            "test hygiene & invariants",
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "check_test_hygiene.py"),
                "--check-private-imports",
                "--max-private-imports",
                "20",
                "--check-invariants",
            ],
        ),
    )
    ok = True
    for label, cmd in steps:
        ok = _run_subprocess_step(label, cmd) and ok
    return ok


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.write_baseline:
        payload = _build_baseline_payload()
        BASELINE_PATH.write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
        )
        _emit(f"Wrote baseline to {BASELINE_PATH.relative_to(REPO_ROOT)}")
        return 0

    if not _run_static_gates():
        return 1

    if args.format_check:
        fmt_cmd = [*_resolve_python_tool("ruff"), "format", "--check", *PROD_ROOTS, "tests"]
        if not _run_subprocess_step("ruff format --check", fmt_cmd):
            return 1

    if args.lint_only:
        return 0

    ok = _run_contract_and_hygiene_gates()
    npm = _resolve_npm()
    if not args.skip_tests:
        ok = (
            _run_pytest_step(
                workers=max(1, int(args.workers)),
                record_coverage=args.record_coverage,
                check_coverage=args.check_coverage,
            )
            and ok
        )
        ok = _run_subprocess_step("frontend test", [npm, "--prefix", "frontend", "test"]) and ok

    ok = _run_subprocess_step("frontend typecheck", [npm, "--prefix", "frontend", "run", "typecheck"]) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
