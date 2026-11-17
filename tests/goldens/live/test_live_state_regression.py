"""Deterministic golden regression tests against the D10 sanitized live snapshot."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
import dataclasses
import enum
import json
import math
from pathlib import Path
import random
import re
import time
from typing import cast
from unittest import mock

import pytest

from backend.core.catalog import sanitize_json_tree
from backend.core.config import StrategyConfig, strategy_config_from_dict
from backend.core.model import GameState, PlannedAction, game_state_from_dict
from backend.core.redaction import redact_sensitive, redact_text
from backend.planner.eta import build_buildings_eta
from backend.planner.graph import build_action_graph
from backend.planner.heuristics import plan_actions, summarize_plan

LIVE_GOLDEN_DIR = Path(__file__).resolve().parent
LIVE_STATE_PATH = LIVE_GOLDEN_DIR / "sanitized_live_state.json"
LIVE_OUTPUTS_PATH = LIVE_GOLDEN_DIR / "sanitized_live_outputs.json"

FIXED_SEED = 20260417
_RAW_SID_RE = re.compile(r"\bsid=(?!REDACTED\b)[^\s&\"'<>]+", re.IGNORECASE)

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def _reject_nonfinite_json(token: str) -> JsonScalar:
    raise ValueError(f"Non-finite JSON constant {token!r} is forbidden in live golden fixtures")


def _canonical_float(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        raise ValueError(f"Non-finite float encountered in live golden output: {value!r}")
    rounded = float(f"{value:.10g}")
    return 0.0 if rounded == 0.0 else rounded


def _to_canonical_json(obj: object) -> JsonValue:
    """Convert planner, graph, and ETA payloads into finite canonical JSON data."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return _canonical_float(obj)
    if isinstance(obj, enum.Enum):
        return _to_canonical_json(obj.value)
    if isinstance(obj, PlannedAction):
        return _to_canonical_json(dataclasses.asdict(obj))
    if isinstance(obj, Mapping):
        return {str(k): _to_canonical_json(v) for k, v in obj.items()}
    if isinstance(obj, Sequence) and not isinstance(obj, (bytes, bytearray)):
        return [_to_canonical_json(item) for item in obj]
    raise TypeError(f"Unsupported type for live golden JSON serialization: {type(obj).__name__}")


def _render_canonical_json(payload: Mapping[str, object]) -> str:
    canonical = _to_canonical_json(payload)
    return json.dumps(canonical, indent=1, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"


def _load_fixture_document() -> tuple[str, dict[str, object], GameState, StrategyConfig, float]:
    raw_text = LIVE_STATE_PATH.read_text(encoding="utf-8")
    loaded = cast(object, json.loads(raw_text, parse_constant=_reject_nonfinite_json))
    if not isinstance(loaded, dict):
        raise TypeError("sanitized_live_state.json root must be a JSON object")
    doc = cast(dict[str, object], loaded)
    raw_state = doc["state"]
    raw_config = doc["config"]
    raw_epoch = doc["clock_epoch"]
    if (
        not isinstance(raw_state, Mapping)
        or not isinstance(raw_config, Mapping)
        or not isinstance(raw_epoch, (int, float))
    ):
        raise TypeError("sanitized_live_state.json is missing valid state/config/clock_epoch fields")
    state = game_state_from_dict(cast(Mapping[str, object], raw_state))
    cfg = strategy_config_from_dict(cast(Mapping[str, object], raw_config))
    return raw_text, doc, state, cfg, float(raw_epoch)


@contextmanager
def _frozen_clock_and_seed(epoch: float) -> Iterator[None]:
    rng_state = random.getstate()
    random.seed(FIXED_SEED)
    with (
        mock.patch.object(time, "time", return_value=epoch),
        mock.patch.object(time, "monotonic", return_value=epoch),
    ):
        try:
            yield
        finally:
            random.setstate(rng_state)


def compute_live_outputs(state: GameState, cfg: StrategyConfig, epoch: float) -> dict[str, object]:
    """Run plan_actions, build_action_graph, and build_buildings_eta under a frozen clock and seed."""
    with _frozen_clock_and_seed(epoch):
        actions = plan_actions(state, config=cfg, scanned_systems={}, throttle_cooldowns={}, now_ts=epoch)
        action_graph = build_action_graph(state, actions, effective_config=cfg)
        buildings_eta = build_buildings_eta(state)
    return {
        "schema": "spaceinvasion-sanitized-live-outputs/1",
        "clock_epoch": epoch,
        "plan_summary": summarize_plan(actions),
        "actions": actions,
        "action_graph": action_graph,
        "buildings_eta": buildings_eta,
    }


def test_sanitized_fixture_has_no_secrets_or_real_identifiers() -> None:
    """Verify the sanitized live snapshot and golden outputs contain zero secrets, real IDs, or non-finite numbers."""
    raw_state_text, doc, state, _cfg, _epoch = _load_fixture_document()
    assert redact_text(raw_state_text) == raw_state_text
    assert redact_sensitive(doc) == doc
    assert _RAW_SID_RE.search(raw_state_text) is None
    assert "bitmeup.com" not in raw_state_text.lower()
    assert state.nickname == "Commander"
    assert state.user_id == 100001
    assert state.planets
    assert all(p.name.startswith("Colony-") for p in state.planets)

    raw_outputs_text = LIVE_OUTPUTS_PATH.read_text(encoding="utf-8")
    loaded_outputs = cast(object, json.loads(raw_outputs_text, parse_constant=_reject_nonfinite_json))
    assert isinstance(loaded_outputs, dict)
    assert redact_text(raw_outputs_text) == raw_outputs_text
    assert redact_sensitive(cast(Mapping[str, object], loaded_outputs)) == loaded_outputs
    assert _RAW_SID_RE.search(raw_outputs_text) is None


def test_sanitized_live_plan_action_graph_and_etas_match_golden() -> None:
    """Verify deterministic parity of heuristic plan, action graph DAG, and building/research ETAs."""
    _raw_text, _doc, state, cfg, epoch = _load_fixture_document()
    first = _render_canonical_json(compute_live_outputs(state, cfg, epoch))
    second = _render_canonical_json(compute_live_outputs(state, cfg, epoch))
    assert first == second, "compute_live_outputs is non-deterministic within the same process"

    expected = LIVE_OUTPUTS_PATH.read_text(encoding="utf-8")
    assert first == expected


def test_live_outputs_are_finite_canonical_json() -> None:
    """Verify live outputs serialize cleanly with allow_nan=False and match sanitize_json_tree."""
    _raw_text, _doc, state, cfg, epoch = _load_fixture_document()
    outputs = compute_live_outputs(state, cfg, epoch)
    canonical_tree = _to_canonical_json(outputs)
    assert sanitize_json_tree(canonical_tree) == canonical_tree

    action_graph = outputs["action_graph"]
    assert isinstance(action_graph, dict)
    summary = action_graph.get("summary")
    assert isinstance(summary, dict)
    assert isinstance(summary.get("total_nodes"), int) and summary["total_nodes"] > 0

    buildings_eta = outputs["buildings_eta"]
    assert isinstance(buildings_eta, dict)
    buildings = buildings_eta.get("buildings")
    researches = buildings_eta.get("researches")
    assert isinstance(buildings, list) and len(buildings) == 21
    assert isinstance(researches, list) and len(researches) == 15


def main() -> None:
    """Regenerate `sanitized_live_outputs.json` explicitly when intentional planner behaviour changes."""
    _raw_text, _doc, state, cfg, epoch = _load_fixture_document()
    rendered = _render_canonical_json(compute_live_outputs(state, cfg, epoch))
    LIVE_OUTPUTS_PATH.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
    pytest.main([__file__, "-v"])
