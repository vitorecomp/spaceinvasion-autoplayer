"""Golden reference suite (refactor plan, Phase 0.6).

Deterministic reference outputs for planner, action-graph, parser and API behaviour.
`scripts/capture_goldens.py` records them; `test_reference_outputs.py` asserts that
the current code reproduces them exactly.
"""

from __future__ import annotations

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
for _p in (str(_REPO_ROOT), str(_BIN_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
