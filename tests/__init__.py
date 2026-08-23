"""Unified SpaceInvasion Autoplayer test suite (`tests/unit` + `tests/e2e`)."""

from __future__ import annotations

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BIN_DIR = _REPO_ROOT / "bin"
for _p in (str(_REPO_ROOT), str(_BIN_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
