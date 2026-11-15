"""Golden reference outputs: the current code must reproduce every recorded file exactly.

Refactor plan, Phase 0.6. A failure here means behaviour changed. If the change is
intended, re-record with `python3 scripts/capture_goldens.py --write` and explain the
difference in the commit; otherwise fix the code.

Each group runs once per process and is compared case by case, so one run reports
every case that moved.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest

REPO_ROOT = Path(__file__).resolve().parents[2]
_CAPTURE_PATH = REPO_ROOT / "scripts" / "capture_goldens.py"


def _load_capture_module() -> ModuleType:
    cached = sys.modules.get("capture_goldens")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("capture_goldens", _CAPTURE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["capture_goldens"] = module
    spec.loader.exec_module(module)
    return module


capture = _load_capture_module()


class GoldenReferenceOutputs(unittest.TestCase):
    maxDiff = None

    def _assert_group_reproduced(self, group: str) -> dict[str, str]:
        produced = capture.capture_group(group)
        recorded = capture.recorded_cases(group)
        self.assertTrue(recorded, f"no reference files recorded for group {group!r}")
        self.assertEqual(
            sorted(produced),
            recorded,
            f"the set of {group!r} cases changed; re-record with scripts/capture_goldens.py --write",
        )
        for case in recorded:
            with self.subTest(case=f"{group}/{case}"):
                expected = capture.reference_path(group, case).read_text(encoding="utf-8")
                if expected != produced[case]:
                    self.fail(
                        f"{group}/{case} no longer matches its reference output\n"
                        + capture.diff_text(expected, produced[case], f"{group}/{case}.json")
                    )
        return produced

    def test_heuristic_planner_actions_and_summary(self) -> None:
        self._assert_group_reproduced("planner")

    def test_ai_planner_path_actions_and_graph(self) -> None:
        self._assert_group_reproduced("ai_planner")

    def test_heuristic_action_graph(self) -> None:
        self._assert_group_reproduced("action_graph")

    def test_html_and_json_parsers(self) -> None:
        self._assert_group_reproduced("parsers")

    def test_api_command_responses_against_fake_game(self) -> None:
        produced = self._assert_group_reproduced("api")
        effects = json.loads(produced["150_fake_game_side_effects"])
        # Only the three live steps may have reached the fake game: one build and one spy probe.
        self.assertEqual([entry["action"] for entry in effects["action_log"]], ["build"])
        self.assertEqual([fleet["mission"] for fleet in effects["launched_fleets"]], ["6"])
        self.assertEqual(effects["concurrent_render_collisions"], 0)


class GoldenSuiteContract(unittest.TestCase):
    """Properties of the suite itself, checked without running the code under test twice."""

    def test_pure_groups_repeat_within_one_process(self) -> None:
        for group in ("planner", "action_graph", "parsers"):
            with self.subTest(group=group):
                self.assertEqual(capture.capture_group(group), capture.capture_group(group))

    def test_api_session_skips_removed_and_ga_nn_commands(self) -> None:
        from backend.runtime.api.handlers import build_command_registry
        from tests.goldens.api_scenario import API_STEPS, REMOVED_COMMANDS

        sent = [step.command for step in API_STEPS if step.command is not None]
        names = {str(cmd["command"]) for cmd in sent}
        self.assertFalse(names & REMOVED_COMMANDS)
        for cmd in sent:
            self.assertNotEqual(cmd.get("planner_mode"), "ga_nn")
            self.assertNotEqual((cmd.get("config") or {}).get("planner_mode"), "ga_nn")
        # Every command that survives Decision D5 has a golden; the one unknown name is the error case.
        kept = {spec.name for spec in build_command_registry().specs()} - REMOVED_COMMANDS
        self.assertEqual(names - kept, {"no_such_command"})
        self.assertEqual(kept - names, set())

    def test_reference_files_are_canonical_json(self) -> None:
        files = sorted(capture.REFERENCE_DIR.glob("*/*.json"))
        self.assertTrue(files)
        self.assertEqual(sorted({p.parent.name for p in files}), sorted(capture.GROUPS))
        for path in files:
            with self.subTest(file=str(path.relative_to(capture.REFERENCE_DIR))):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text, json.dumps(json.loads(text), indent=1, sort_keys=True, ensure_ascii=True) + "\n")

    def test_api_references_hold_no_session_id_or_temporary_path(self) -> None:
        from tests.goldens.scenarios import SID

        for path in sorted((capture.REFERENCE_DIR / "api").glob("*.json")):
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(SID, text, "the API leaked a session id past redact_sensitive")
                self.assertNotIn("si_e2e_", text, "a temporary directory name was recorded")


if __name__ == "__main__":
    unittest.main()
