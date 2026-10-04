#!/usr/bin/env python3
"""Record or check the golden reference outputs (refactor plan, Phase 0.6).

The suite freezes what the code does today so that every refactoring step can prove
it changed nothing: the heuristic plan, the AI-Planner path and its live actions, both
action graphs, the HTML/JSON parsers, and one response per kept `POST /command`
handler against the fake game.

    python3 scripts/capture_goldens.py            # compare, exit 1 on any difference
    python3 scripts/capture_goldens.py --write    # record tests/goldens/reference/
    python3 scripts/capture_goldens.py --only planner,parsers --write
    python3 scripts/capture_goldens.py --list
    python3 scripts/capture_goldens.py --write --reference-dir /tmp/trial   # capture elsewhere

Record only when behaviour is meant to change, and say why in the commit message.

Determinism rules applied before a value is written:

* Clocks are frozen at `GOLDEN_EPOCH`; random seeds are fixed (`GOLDEN_SEED`).
* JSON is written with sorted keys, so dict insertion order never shows up in a diff.
  List order is kept: it is behaviour.
* Floats are rounded to 10 significant digits, enough to hide last-bit noise from a
  reordered sum and nothing else.
* The keys in `VOLATILE_KEYS` hold wall-clock durations, wall-clock dates, random
  tokens or file-system paths. Their values are replaced by `"<volatile>"`; `null`
  stays `null` so presence is still checked.
* The paths in `BACKEND_SPECIFIC_PATHS` describe the storage engine rather than the
  behaviour (SQLite today, PostgreSQL after phase 0.7) and are dropped.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import dataclasses
import difflib
import enum
import json
import math
from pathlib import Path
import random
import re
import sys
import time as _real_time
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(REPO_ROOT), str(REPO_ROOT / "bin")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REFERENCE_DIR = REPO_ROOT / "tests" / "goldens" / "reference"
PAGES_DIR = REPO_ROOT / "tests" / "goldens" / "pages"

VOLATILE = "<volatile>"

# Wall-clock durations, wall-clock dates, per-process random tokens, temporary paths and
# hashes of source files. None of them is behaviour a refactoring has to preserve.
VOLATILE_KEYS = frozenset(
    {
        "elapsed_ms",
        "elapsed_seconds",
        "generated_at",
        "config_generated_at",
        "server_boot_id",
        "code_version",
        "css_version",
        "ui_version",
        "db_path",
        "size_bytes",
    }
)

# (case glob, dotted path) pairs removed from the named API cases: storage-engine detail.
BACKEND_SPECIFIC_PATHS: tuple[tuple[str, str], ...] = (
    ("*", "response.db_backend"),
    ("130_db_summary", "response.db.backend"),
    ("130_db_summary", "response.db.schema"),
    ("131_db_verify", "response.verification"),
    ("141_get_dashboard", "response.db_summary.backend"),
    ("141_get_dashboard", "response.db_summary.schema"),
)


class _FrozenTime:
    """Stand-in for the `time` module: a fixed wall clock, everything else untouched."""

    def __init__(self, now: float) -> None:
        self._now = float(now)

    def time(self) -> float:
        return self._now

    def monotonic(self) -> float:
        return self._now

    def sleep(self, _seconds: float) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(_real_time, name)


@contextmanager
def frozen_time(now: float) -> Iterator[None]:
    """Freeze `time.time()` in the pure planner, combat and state modules and seed `random`."""
    from spaceinvasion.combat import fleet, ranking
    from spaceinvasion.core import config, state
    from spaceinvasion.planner import strategy
    from tests.goldens.scenarios import GOLDEN_SEED

    proxy = _FrozenTime(now)
    rng_state = random.getstate()
    with ExitStack() as stack:
        for module in (fleet, ranking, config, state, strategy):
            stack.enter_context(mock.patch.object(module, "time", proxy))
        random.seed(GOLDEN_SEED)
        try:
            yield
        finally:
            random.setstate(rng_state)


def _canonical_float(value: float) -> Any:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    rounded = float(f"{value:.10g}")
    return 0.0 if rounded == 0.0 else rounded


def canonical(obj: Any) -> Any:
    """Turn any captured value into plain JSON data, applying the determinism rules."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return _canonical_float(obj)
    if isinstance(obj, enum.Enum):
        return canonical(obj.value)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return canonical({f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)})
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            skey = key if isinstance(key, str) else json.dumps(canonical(key), allow_nan=False)
            out[skey] = VOLATILE if (skey in VOLATILE_KEYS and value is not None) else canonical(value)
        return out
    if isinstance(obj, (list, tuple)):
        return [canonical(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted((canonical(v) for v in obj), key=lambda v: json.dumps(v, sort_keys=True, allow_nan=False))
    if isinstance(obj, Path):
        return str(obj)
    item = getattr(obj, "item", None)  # numpy scalar
    if callable(item) and getattr(obj, "shape", None) == ():
        return canonical(item())
    tolist = getattr(obj, "tolist", None)  # numpy array
    if callable(tolist):
        return canonical(tolist())
    raise TypeError(f"golden capture cannot serialise {type(obj).__name__}: {obj!r:.200}")


def render(obj: Any) -> str:
    """The exact text of a reference file."""
    return json.dumps(canonical(obj), indent=1, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"


def _drop_path(node: Any, parts: Sequence[str]) -> None:
    if not isinstance(node, dict) or not parts:
        return
    if len(parts) == 1:
        node.pop(parts[0], None)
        return
    _drop_path(node.get(parts[0]), parts[1:])


def summarize_actions(actions: Sequence[Any]) -> dict[str, Any]:
    """Counts that make a changed plan readable at a glance."""
    by_priority: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_planet: dict[str, int] = {}
    for act in actions:
        by_priority[act.priority] = by_priority.get(act.priority, 0) + 1
        by_kind[act.kind] = by_kind.get(act.kind, 0) + 1
        by_planet[str(act.planet_id)] = by_planet.get(str(act.planet_id), 0) + 1
    first = next((a for a in actions if a.executable_now), None)
    return {
        "total": len(actions),
        "executable_now": sum(1 for a in actions if a.executable_now),
        "by_priority": by_priority,
        "by_kind": by_kind,
        "by_planet_id": by_planet,
        "first_executable": (
            None
            if first is None
            else {
                "priority": first.priority,
                "kind": first.kind,
                "planet_id": first.planet_id,
                "target_name": first.target_name,
                "target_level": first.target_level,
            }
        ),
    }


def _plan(state: Any, cfg: Any) -> list[Any]:
    from spaceinvasion.planner.strategy import plan_actions
    from tests.goldens.scenarios import GOLDEN_EPOCH

    return plan_actions(state, config=cfg, scanned_systems={}, throttle_cooldowns={}, now_ts=GOLDEN_EPOCH)


def capture_planner() -> dict[str, Any]:
    """Heuristic planner (`priority` mode): planned actions and a summary, per state and config."""
    from tests.goldens.scenarios import CONFIGS, GOLDEN_EPOCH, STATES

    out: dict[str, Any] = {}
    with frozen_time(GOLDEN_EPOCH):
        for state_name, make_state in STATES.items():
            for cfg_name, make_cfg in CONFIGS.items():
                actions = _plan(make_state(), make_cfg())
                out[f"priority__{state_name}__{cfg_name}"] = {
                    "summary": summarize_actions(actions),
                    "actions": actions,
                }
    return out


def capture_action_graph() -> dict[str, Any]:
    """Heuristic action graph (`build_action_graph`) and the fleet-planner graph built from the same plan."""
    from spaceinvasion.planner.action_graph import build_action_graph, build_fleet_planner_graph
    from tests.goldens.scenarios import CONFIGS, GOLDEN_EPOCH, STATES, default_config, full_auto_config

    out: dict[str, Any] = {}
    with frozen_time(GOLDEN_EPOCH):
        for state_name, make_state in STATES.items():
            for cfg_name, make_cfg in CONFIGS.items():
                state, cfg = make_state(), make_cfg()
                out[f"heuristic__{state_name}__{cfg_name}"] = build_action_graph(
                    state, _plan(state, cfg), effective_config=cfg
                )
        state = STATES["midgame_three_colonies"]()
        sim_cfg = full_auto_config()
        sim_actions = _plan(state, sim_cfg)
        out["heuristic_simulated_planet_filter__midgame_three_colonies"] = build_action_graph(
            state,
            sim_actions,
            effective_config=sim_cfg,
            active_config=default_config(),
            simulated=True,
            planet_filter=state.planets[1].planet_id,
        )
        for state_name in ("midgame_three_colonies", "lategame_five_colonies"):
            state = STATES[state_name]()
            out[f"fleet_planner__{state_name}__full_auto"] = build_fleet_planner_graph(
                state, _plan(state, sim_cfg), sim_cfg
            )
    return out


def capture_ai_planner() -> dict[str, Any]:
    """AI-Planner: the planned path, the live actions reconciled from it, and `build_ai_planner_graph`."""
    from spaceinvasion.core.config import StrategyConfig
    from spaceinvasion.planner import ai_planner
    from spaceinvasion.planner.action_graph import build_ai_planner_graph
    from tests.goldens.scenarios import AI_PLANNER_PARAMS, GOLDEN_EPOCH, STATES, ai_target_config

    out: dict[str, Any] = {}
    cfg = StrategyConfig(planner_mode="ai_planner")
    with frozen_time(GOLDEN_EPOCH):
        for state_name in ("midgame_three_colonies", "lategame_five_colonies"):
            state = STATES[state_name]()
            plan = ai_planner.run_ai_planner(state, ai_target_config(state), params=AI_PLANNER_PARAMS)
            actions, conflicts = ai_planner.reconcile_live_ai_actions(state, plan, cfg)
            fills = ai_planner.plan_live_fill_actions(state, plan, cfg, actions)
            out[f"path__{state_name}"] = plan
            out[f"actions__{state_name}"] = {
                "summary": summarize_actions(actions),
                "actions": actions,
                "field_conflicts": conflicts,
                "fill_actions": fills,
            }
            out[f"graph__{state_name}"] = build_ai_planner_graph(state, plan)
            out[f"graph_week1_colony__{state_name}"] = build_ai_planner_graph(
                state, plan, window_size=12, week_filter=1, planet_filter=state.planets[1].planet_id
            )
    return out


def capture_parsers() -> dict[str, Any]:
    """HTML/JSON parsers over the static synthetic pages in `tests/goldens/pages/`."""
    from tests.goldens.parser_cases import parser_cases
    from tests.goldens.scenarios import GOLDEN_EPOCH

    with frozen_time(GOLDEN_EPOCH):
        return {name: run(PAGES_DIR) for name, run in parser_cases().items()}


def capture_api() -> dict[str, Any]:
    """One response per kept `POST /command` handler (dry run where the command has one) against the fake game."""
    from fnmatch import fnmatch

    from tests.goldens.api_scenario import run_api_session
    from tests.goldens.scenarios import GOLDEN_SEED

    rng_state = random.getstate()
    random.seed(GOLDEN_SEED)
    try:
        raw = run_api_session()
    finally:
        random.setstate(rng_state)
    out: dict[str, Any] = {}
    for case, payload in raw.items():
        data = canonical(payload)
        for pattern, dotted in BACKEND_SPECIFIC_PATHS:
            if fnmatch(case, pattern):
                _drop_path(data, dotted.split("."))
        out[case] = data
    return out


GROUPS: dict[str, Callable[[], dict[str, Any]]] = {
    "planner": capture_planner,
    "ai_planner": capture_ai_planner,
    "action_graph": capture_action_graph,
    "parsers": capture_parsers,
    "api": capture_api,
}

_CASE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def capture_group(group: str) -> dict[str, str]:
    """Run one group and return `{case: reference file text}`."""
    rendered: dict[str, str] = {}
    for case, payload in GROUPS[group]().items():
        if not _CASE_NAME_RE.match(case):
            raise ValueError(f"bad golden case name {case!r}")
        rendered[case] = render(payload)
    return rendered


def reference_path(group: str, case: str) -> Path:
    return REFERENCE_DIR / group / f"{case}.json"


def recorded_cases(group: str) -> list[str]:
    group_dir = REFERENCE_DIR / group
    return sorted(p.stem for p in group_dir.glob("*.json")) if group_dir.is_dir() else []


def diff_text(expected: str, actual: str, label: str, *, max_lines: int = 60) -> str:
    lines = list(
        difflib.unified_diff(
            expected.splitlines(), actual.splitlines(), f"recorded/{label}", f"current/{label}", lineterm="", n=2
        )
    )
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... {len(lines) - max_lines} more diff lines"]
    return "\n".join(lines)


def compare_group(group: str, produced: Optional[Mapping[str, str]] = None) -> list[str]:
    """Return one problem description per case that differs from its reference file."""
    current = dict(produced) if produced is not None else capture_group(group)
    problems: list[str] = []
    recorded = set(recorded_cases(group))
    for case in sorted(recorded - set(current)):
        problems.append(f"{group}/{case}: recorded but no longer produced")
    for case in sorted(set(current) - recorded):
        problems.append(f"{group}/{case}: produced but not recorded (run --write)")
    for case in sorted(recorded & set(current)):
        expected = reference_path(group, case).read_text(encoding="utf-8")
        if expected != current[case]:
            problems.append(f"{group}/{case}: differs\n" + diff_text(expected, current[case], f"{group}/{case}.json"))
    return problems


def write_group(group: str) -> tuple[int, int]:
    """Record one group. Returns `(files written, stale files removed)`."""
    current = capture_group(group)
    group_dir = REFERENCE_DIR / group
    group_dir.mkdir(parents=True, exist_ok=True)
    removed = 0
    for case in recorded_cases(group):
        if case not in current:
            reference_path(group, case).unlink()
            removed += 1
    for case, text in current.items():
        reference_path(group, case).write_text(text, encoding="utf-8")
    return len(current), removed


def _emit(line: str) -> None:
    """Write one line of the result to stdout (the result of this tool is its text output)."""
    sys.stdout.write(f"{line}\n")


def _list_groups(groups: Sequence[str]) -> int:
    for group in groups:
        cases = recorded_cases(group)
        _emit(f"{group}: {len(cases)} recorded case(s)")
        for case in cases:
            _emit(f"  {case}")
    return 0


def _run_group(group: str, *, write: bool) -> bool:
    """Record or compare one group. Returns True when the group is clean."""
    started = _real_time.perf_counter()
    if write:
        written, removed = write_group(group)
        _emit(f"{group}: wrote {written} file(s), removed {removed} stale, {_real_time.perf_counter() - started:.1f}s")
        return True
    problems = compare_group(group)
    took = _real_time.perf_counter() - started
    if not problems:
        _emit(f"{group}: {len(recorded_cases(group))} case(s) reproduced exactly, {took:.1f}s")
        return True
    _emit(f"{group}: {len(problems)} difference(s), {took:.1f}s")
    for problem in problems:
        _emit(problem)
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--write", action="store_true", help="record the reference files instead of comparing")
    parser.add_argument("--only", default="", help="comma-separated groups (default: all)")
    parser.add_argument("--list", action="store_true", help="list groups and recorded cases, then exit")
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=None,
        help="read or write reference files here instead of tests/goldens/reference (for trial captures)",
    )
    args = parser.parse_args(argv)
    if args.reference_dir is not None:
        global REFERENCE_DIR
        REFERENCE_DIR = args.reference_dir.resolve()

    groups = [g.strip() for g in args.only.split(",") if g.strip()] or list(GROUPS)
    unknown = [g for g in groups if g not in GROUPS]
    if unknown:
        parser.error(f"unknown group(s) {unknown}; choose from {list(GROUPS)}")
    if args.list:
        return _list_groups(groups)
    results = [_run_group(group, write=args.write) for group in groups]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
