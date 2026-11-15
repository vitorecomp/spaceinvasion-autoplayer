"""Section 4 engineering-standards and architectural layering audit for Phase 2 & 3 modules."""

from __future__ import annotations

import ast
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
FOUNDATION_MODULES = (
    "backend/core/redaction.py",
    "backend/core/coercion.py",
    "backend/core/coords.py",
)
FORBIDDEN_PARSING_PACKAGES = frozenset({"combat", "db", "planner", "session", "runtime", "bin"})


def _iter_imported_roots(tree: ast.AST) -> set[tuple[str, str]]:
    roots: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                roots.add((parts[0], parts[1] if len(parts) > 1 else ""))
        elif isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            roots.add((parts[0], parts[1] if len(parts) > 1 else ""))
    return roots


def _check_size_limits(path: Path, rel: str, tree: ast.AST, src: str) -> None:
    loc = len(src.splitlines())
    assert loc <= 400, f"{rel} exceeds 400 LOC ({loc})"
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            cloc = (node.end_lineno or node.lineno) - node.lineno + 1
            assert cloc <= 400, f"{rel}:{node.name} class exceeds 400 LOC ({cloc})"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            floc = (node.end_lineno or node.lineno) - node.lineno + 1
            assert floc <= 100, f"{rel}:{node.name} exceeds 100 LOC ({floc})"


def _check_prod_smells(rel: str, tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            typ = node.type
            assert typ is not None, f"Bare except in {rel}:{node.lineno}"
            if isinstance(typ, ast.Name):
                assert typ.id not in ("Exception", "BaseException"), f"Broad except in {rel}:{node.lineno}"
            elif isinstance(typ, ast.Tuple):
                for elt in typ.elts:
                    assert not (isinstance(elt, ast.Name) and elt.id in ("Exception", "BaseException")), (
                        f"Broad except in {rel}:{node.lineno}"
                    )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "print", f"print() call in {rel}:{node.lineno}"
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert not (node.value.id == "os" and node.attr in ("environ", "getenv")), (
                f"os.{node.attr} in {rel}:{node.lineno}"
            )
        elif isinstance(node, ast.Name):
            assert node.id != "Any", f"typing.Any usage in {rel}:{node.lineno}"


def test_section4_engineering_standards_and_layering_rules() -> None:
    """Enforce <=400 LOC modules/classes, <=100 LOC funcs, stdlib-only core, and parsing layering."""
    stdlib_names = set(sys.stdlib_module_names) | {"__future__"}
    parsing_files = sorted((REPO_ROOT / "backend" / "parsing").rglob("*.py"))
    assert parsing_files, "Expected backend/parsing modules to exist"

    prod_files = [REPO_ROOT / rel for rel in FOUNDATION_MODULES] + parsing_files
    test_files = [
        REPO_ROOT / "tests" / "unit" / "core" / "test_layering_and_standards.py",
        REPO_ROOT / "tests" / "unit" / "core" / "test_redaction_coercion_coords.py",
        REPO_ROOT / "tests" / "unit" / "parsing" / "test_parsing_parity.py",
    ]

    for path in prod_files + test_files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=rel)
        _check_size_limits(path, rel, tree, src)
        if path not in prod_files:
            continue
        _check_prod_smells(rel, tree)
        imports = _iter_imported_roots(tree)
        if rel in FOUNDATION_MODULES:
            for top, _ in imports:
                assert top in stdlib_names, f"{rel} imports non-stdlib module {top!r}"
        else:
            for top, second in imports:
                if top == "backend":
                    assert second in ("core", "parsing"), f"{rel} violates parsing layering: spaceinvasion.{second}"
                    assert second not in FORBIDDEN_PARSING_PACKAGES
                else:
                    assert top in stdlib_names or top == "bs4", f"{rel} imports forbidden third-party {top!r}"
