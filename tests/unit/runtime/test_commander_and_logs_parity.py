"""Behavioral, resilience, and Section 4 tests for ``runtime.logs`` and ``runtime.commander``."""

from __future__ import annotations

import ast
from collections.abc import Mapping
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest
import requests

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import (
    EnergyBalance,
    GameState,
    PlanetState,
    PlannedAction,
    QueueJob,
    Resources,
)
from spaceinvasion.runtime import logs as new_logs
from spaceinvasion.runtime.commander import (
    CommanderOptions,
    action_to_command_payload,
    compute_next_wakeup_seconds,
    compute_rate_limit_backoff,
    hold_busy_fleet_slots,
    is_captcha_error,
    is_connection_refused_error,
    is_galaxy_scan_busy_error,
    parse_bot_control_snapshot,
    parse_commander_options,
    parse_queue_timer_seconds,
    planned_action_from_dict,
    run_commander_loop,
    run_commander_tick,
    wait_for_api_reconnect,
)
from spaceinvasion.runtime.commander_client import GameApiError

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_model_state() -> GameState:
    planet = PlanetState(
        planet_id=101,
        name="Homeworld",
        coords="01:100:04",
        planet_type="normal",
        temperature=25,
        fields_used=40,
        fields_max=300,
        resources=Resources(pig_iron=5000, metal=3000, kryptonite=2000, spice=1000),
        storage=Resources(pig_iron=100_000, metal=100_000, kryptonite=100_000, spice=100_000),
        energy=EnergyBalance(produced=500, used=400),
        buildings={"b1": 10, "b2": 8, "b3": 6, "b4": 5, "b5": 8, "b7": 4},
        economy_rates={"resource2": 100},
    )
    return GameState(
        universe_id=313,
        user_id=999,
        nickname="Tester",
        timestamp=1_700_000_000.0,
        active_planet_id=101,
        planets=(planet,),
        active_queues=(QueueJob(category="research", name="Mining", remaining_text="00:01:30"),),
    )


class _FakeApi:
    """In-memory fake HTTP API implementing ``CommanderApiLike``."""

    def __init__(
        self,
        *,
        state: GameState | None = None,
        bot_controls: list[dict[str, object] | Exception] | None = None,
        command_results: list[dict[str, object] | Exception] | None = None,
        health_results: list[dict[str, object] | Exception] | None = None,
        state_errors: list[Exception] | None = None,
    ) -> None:
        self.state = state if state is not None else _make_model_state()
        self.bot_controls = list(bot_controls or [{"paused": False, "captcha_blocked": False}])
        self.command_results = list(command_results or [])
        self.health_results = list(health_results or [{"ok": True}])
        self.state_errors = list(state_errors or [])
        self.commands_sent: list[dict[str, object]] = []
        self.get_state_calls: list[bool] = []
        self.bot_ctrl_calls: int = 0
        self.health_calls: int = 0
        self.closed: bool = False

    def health(self) -> Mapping[str, object]:
        self.health_calls += 1
        item = self.health_results.pop(0) if len(self.health_results) > 1 else self.health_results[0]
        if isinstance(item, Exception):
            raise item
        return item

    def get_bot_control(self) -> Mapping[str, object]:
        self.bot_ctrl_calls += 1
        item = self.bot_controls.pop(0) if len(self.bot_controls) > 1 else self.bot_controls[0]
        if isinstance(item, Exception):
            raise item
        return item

    def get_state(self, *, include_buildings: bool = True) -> object:
        self.get_state_calls.append(include_buildings)
        if self.state_errors:
            raise self.state_errors.pop(0)
        return self.state

    def send_command(self, payload: dict[str, object]) -> Mapping[str, object]:
        self.commands_sent.append(dict(payload))
        if self.command_results:
            item = self.command_results.pop(0) if len(self.command_results) > 1 else self.command_results[0]
            if isinstance(item, Exception):
                raise item
            return item
        return {
            "ok": True,
            "executed": not payload.get("dry_run", True),
            "command": payload.get("command"),
            "message": "ok",
        }

    def close(self) -> None:
        self.closed = True


def test_zero_selenium_imports_in_runtime_logs_and_commander() -> None:
    code = (
        "import sys; "
        "import spaceinvasion.runtime.logs, spaceinvasion.runtime.commander; "
        "bad = [m for m in sys.modules if m == 'selenium' or m.startswith('selenium.')]; "
        "assert not bad, f'Unexpected selenium imports: {bad}'"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def test_runtime_logs_formatting_redaction_and_disk_guards(tmp_path: Path) -> None:
    wd_err = (
        "Message: element click intercepted sid=SUPERSECRET123\n"
        "  (Session info: chrome=124.0)\n"
        "Stacktrace:\n#0 0x1234 base::debug\n"
    )
    stripped = new_logs.strip_webdriver_stacktrace(wd_err)
    assert "Stacktrace:" not in stripped and "Session info:" not in stripped
    cleaned_err = new_logs.format_clean_error(wd_err)
    assert "SUPERSECRET123" not in cleaned_err and "REDACTED" in cleaned_err

    act_new = PlannedAction(
        priority="P1",
        kind="BUILD",
        planet_id=101,
        planet_coords="01:100:04",
        target_name="Pig iron mine",
        reason="grow",
        target_id=1,
        target_level=11,
        missing=Resources(pig_iron=250, metal=0, kryptonite=100, spice=0),
        executable_now=False,
    )
    assert "BUILD" in new_logs.format_action_summary(act_new)
    assert new_logs.format_action_summary(None) == "none"

    act2 = PlannedAction(
        priority="P2",
        kind="RESEARCH",
        planet_id=101,
        planet_coords="01:100:04",
        target_name="Mining",
        reason="tech",
        target_id=1,
        target_level=5,
        executable_now=True,
    )
    act1_ready = PlannedAction(
        priority="P1",
        kind="BUILD",
        planet_id=101,
        planet_coords="01:100:04",
        target_name="Mine",
        reason="grow",
        target_id=1,
        target_level=11,
        executable_now=True,
    )
    assert new_logs.pick_next_action([act1_ready, act2], act1_ready, after_live_execution=False) == act2
    assert new_logs.pick_next_action([act1_ready, act2], act1_ready, after_live_execution=True) == act1_ready

    st = _make_model_state()
    jsonl_p, log_p = new_logs.append_tick_logs(
        tmp_path,
        source="commander",
        tick=3,
        mode="DRY-RUN",
        state=st,
        actual_action=act1_ready,
        actual_result={"message": "ok sid=TOKEN999"},
        next_action=act2,
    )
    assert jsonl_p.is_file() and log_p.is_file()
    row = json.loads(jsonl_p.read_text(encoding="utf-8").strip())
    assert row["tick"] == 3 and row["universe_id"] == 313
    assert "TOKEN999" not in jsonl_p.read_text(encoding="utf-8")
    assert "TOKEN999" not in log_p.read_text(encoding="utf-8")

    skipped_j, skipped_t = new_logs.append_tick_logs(
        REPO_ROOT / "logs",
        source="commander",
        tick=1,
        mode="DRY-RUN",
        state=st,
        actual_action=None,
        actual_result=None,
        next_action=None,
    )
    assert skipped_j == Path("/dev/null") and skipped_t == Path("/dev/null")

    buf = io.StringIO()
    new_logs.append_api_log(
        None, method="POST", path="/command?sid=ABC123", status=200, payload={"command": "db_summary"}, stream=buf
    )
    assert buf.getvalue() == ""
    new_logs.append_api_log(
        None,
        method="POST",
        path="/command?sid=ABC123",
        status=500,
        payload={"command": "db_summary"},
        response={"error": "boom sid=XYZ789"},
        stream=buf,
    )
    assert "[API]" in buf.getvalue() and "ABC123" not in buf.getvalue() and "XYZ789" not in buf.getvalue()

    crash_j, crash_t = new_logs.append_crash_debug_log(
        component="test",
        event="boom",
        error=RuntimeError("fail sid=SECRET01"),
        details={"password": "pw123"},
        crash_dir=tmp_path,
    ) or (Path("/dev/null"), Path("/dev/null"))
    assert crash_j.is_file() and crash_t.is_file()
    assert "SECRET01" not in crash_j.read_text(encoding="utf-8")
    assert "pw123" not in crash_j.read_text(encoding="utf-8")


def test_commander_error_classification_timers_and_all_16_payload_kinds() -> None:
    assert compute_rate_limit_backoff("normal error", None) is None
    assert compute_rate_limit_backoff("HTTP 429 Too Many Requests retry-after 120s", None) is not None
    assert compute_rate_limit_backoff(
        "page-view throttle cooldown active 65s", {"rate_limited": True, "retry_after": 95}
    ) == pytest.approx(95.0)
    assert is_captcha_error("ok") is False
    for text in ("Cloudflare Turnstile active", "Sicherheitsprüfung required", "anti-bot challenge"):
        assert is_captcha_error(text) is True
    assert is_captcha_error("", bot_ctrl={"captcha_blocked": True}) is True
    assert is_galaxy_scan_busy_error("Galaxy sweep in progress") is True
    assert (
        is_galaxy_scan_busy_error("", bot_ctrl={"galaxy_scan_blocking": True, "p0_escape_flight_ready": True}) is False
    )
    assert is_connection_refused_error(requests.exceptions.ConnectionError("refused")) is True

    assert parse_queue_timer_seconds("") is None
    assert parse_queue_timer_seconds("01:02:03") == 3723
    assert parse_queue_timer_seconds("2h 5m 10s") == 7510
    assert parse_queue_timer_seconds("45s") == 45

    expected_cmd_by_kind = [
        ("CLAIM_TUTORIAL", "claim_tutorial", {}),
        ("BUILD", "build", {"target_id": 1, "target_level": 11}),
        ("DEMOLISH_BUILDING", "demolish_building", {"target_id": 6, "target_level": 0}),
        ("RESEARCH", "research", {"target_id": 1, "target_level": 4}),
        ("BUILD_SHIP", "build_ship", {"target_id": 11, "target_level": 5}),
        ("THROTTLE_BUILDING", "throttle_building", {"target_id": 2, "target_level": 0}),
        ("ESCAPE_FLIGHT", "escape_flight", {"target_name": "Escape 01:100:05"}),
        ("SCAN_GALAXY", "scan_galaxy", {"target_name": "Scan 02:150"}),
        ("SPY", "spy", {"target_name": "Spy 01:101:09", "target_level": 3}),
        ("RECYCLE", "recycle", {"target_name": "DF 01:101:09", "target_level": 4}),
        (
            "TRANSPORT",
            "transport",
            {
                "target_name": "To 01:102:06",
                "target_id": 102,
                "cost": {"pig_iron": 100, "metal": 200, "kryptonite": 50, "spice": 10},
            },
        ),
        ("STATION", "station", {"target_name": "01:102:06 [10x s11, 2x s4]", "target_id": 102}),
        ("COLONIZE", "colonize", {"target_name": "Colony 01:105:08"}),
        ("ABANDON_COLONY", "abandon_colony", {"planet_id": 102, "planet_coords": "01:102:06", "target_level": 300}),
        ("ACTIVATE_TRANSMITTER", "activate_transmitter", {}),
        ("ATTACK", "attack", {"target_name": "Raid 01:101:09"}),
    ]
    for kind, expected_cmd, extra in expected_cmd_by_kind:
        spec: dict[str, object] = {
            "priority": "P1",
            "kind": kind,
            "planet_id": 101,
            "planet_coords": "01:100:04",
            "target_name": "Target",
            "reason": "test",
            **extra,
        }
        act = planned_action_from_dict(spec)
        assert act is not None
        payload = action_to_command_payload(act, dry_run=True)
        assert payload is not None and payload["command"] == expected_cmd and payload["dry_run"] is True

    wait_act = planned_action_from_dict(
        {
            "priority": "P3",
            "kind": "WAIT_RESOURCES",
            "planet_id": 101,
            "planet_coords": "01:100:04",
            "target_name": "Wait",
            "reason": "saving",
        }
    )
    assert wait_act is not None and action_to_command_payload(wait_act, dry_run=True) == {
        "command": "plan",
        "dry_run": True,
    }

    a_fleet = planned_action_from_dict(
        {
            "priority": "P1",
            "kind": "SPY",
            "planet_id": 101,
            "planet_coords": "01:100:04",
            "target_name": "01:101:09",
            "reason": "probe",
            "executable_now": True,
        }
    )
    a_build = planned_action_from_dict(
        {
            "priority": "P1",
            "kind": "BUILD",
            "planet_id": 101,
            "planet_coords": "01:100:04",
            "target_name": "Mine",
            "reason": "grow",
            "target_id": 1,
            "target_level": 11,
            "executable_now": True,
        }
    )
    assert a_fleet is not None and a_build is not None
    held_new = hold_busy_fleet_slots([a_fleet, a_build], {101: 45.0}, re_sort_priority=True)
    assert held_new[0].kind == "BUILD" and held_new[1].kind == "SPY" and not held_new[1].executable_now
    assert "[Hold: fleet slots busy / returning]" in held_new[1].reason

    st_model = _make_model_state()
    w_new, w_reason = compute_next_wakeup_seconds(st_model, a_build, [], min_interval_s=30.0, max_idle_s=600.0)
    assert 30.0 <= w_new <= 600.0
    assert isinstance(w_reason, str) and w_reason


def test_commander_loop_captcha_passive_wait_reconnect_and_rate_limit(tmp_path: Path) -> None:
    sleeps: list[float] = []
    api_cap = _FakeApi(
        bot_controls=[
            {"paused": True, "captcha_blocked": True, "captcha_message": "Turnstile gate sid=HIDE1"},
            {"paused": True, "captcha_blocked": True, "captcha_message": "Turnstile gate sid=HIDE1"},
        ]
    )
    opts = CommanderOptions(loop=True, max_ticks=2, interval=10.0, log_dir=tmp_path)
    rc = run_commander_loop(
        opts,
        api=api_cap,
        config_loader=lambda _p: StrategyConfig(),
        jitter_fn=lambda a, _b: a,
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: 100.0,
    )
    assert rc == 0
    assert api_cap.bot_ctrl_calls == 2
    assert api_cap.get_state_calls == []
    assert api_cap.commands_sent == []
    assert sleeps == [5.0]
    assert api_cap.closed

    api_reconn = _FakeApi(
        bot_controls=[
            requests.exceptions.ConnectionError("Connection refused"),
            {"paused": True, "captcha_blocked": False},
        ],
        health_results=[requests.exceptions.ConnectionError("starting"), {"ok": True}],
    )
    sleeps2: list[float] = []
    rc2 = run_commander_loop(
        CommanderOptions(loop=True, max_ticks=1, interval=10.0, log_dir=tmp_path),
        api=api_reconn,
        config_loader=lambda _p: StrategyConfig(),
        jitter_fn=lambda a, _b: a,
        sleep_fn=sleeps2.append,
        monotonic_fn=lambda: 100.0,
    )
    assert rc2 == 0 and api_reconn.health_calls == 2 and api_reconn.bot_ctrl_calls == 2

    api_rl = _FakeApi(
        bot_controls=[{"paused": False, "captcha_blocked": False}],
        state_errors=[GameApiError("HTTP 429 rate-limit cooldown active")],
        health_results=[{"ok": True, "rate_limited": True, "retry_after": 75.0}],
    )
    sleeps3: list[float] = []
    rc3 = run_commander_loop(
        CommanderOptions(loop=True, max_ticks=2, interval=10.0, log_dir=tmp_path),
        api=api_rl,
        config_loader=lambda _p: StrategyConfig(),
        jitter_fn=lambda a, _b: a,
        sleep_fn=sleeps3.append,
        monotonic_fn=lambda: 100.0,
    )
    assert rc3 == 0 and sleeps3 and sleeps3[0] == pytest.approx(76.0)

    api_ai = _FakeApi(
        command_results=[
            {
                "ok": True,
                "effective_planner_mode": "ai_planner",
                "actions": [
                    {
                        "priority": "P1",
                        "kind": "BUILD",
                        "planet_id": 101,
                        "planet_coords": "01:100:04",
                        "target_name": "Pig iron mine",
                        "reason": "ai step",
                        "target_id": 1,
                        "target_level": 11,
                        "executable_now": True,
                    }
                ],
            },
            {"ok": True, "executed": False, "dry_run": True, "command": "build", "message": "dry-run build"},
        ]
    )
    st_out, actual, res, _nxt, outcomes = run_commander_tick(
        api_ai,
        live=False,
        max_actions=1,
        config=StrategyConfig(planner_mode="ai_planner", max_planets=1),
    )
    assert st_out.universe_id == 313
    assert actual is not None and actual.kind == "BUILD"
    assert res is not None and res["command"] == "build"
    assert len(outcomes) == 1
    assert parse_commander_options(["--live", "--loop", "--max-ticks", "3"]).live is True
    assert (
        parse_bot_control_snapshot(
            {"mode_override": "live"}, StrategyConfig(), run_loop=True, effective_live=False
        ).mode_override
        == "live"
    )
    assert wait_for_api_reconnect(_FakeApi(), sleep_fn=lambda _s: None, monotonic_fn=lambda: 0.0) is True


def test_section4_standards_and_layering_for_runtime_logs_and_commander() -> None:
    runtime_files = [
        REPO_ROOT / "spaceinvasion/runtime/_logs_helpers.py",
        REPO_ROOT / "spaceinvasion/runtime/logs.py",
        *sorted((REPO_ROOT / "spaceinvasion/runtime/commander").glob("*.py")),
    ]
    for path in runtime_files:
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        assert len(lines) <= 400, f"{path}: {len(lines)} > 400 LOC"
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                floc = (node.end_lineno or node.lineno) - node.lineno + 1
                assert floc <= 100, f"{path}:{node.name}: {floc} > 100 LOC"
            if isinstance(node, ast.ClassDef):
                cloc = (node.end_lineno or node.lineno) - node.lineno + 1
                assert cloc <= 400, f"{path}:{node.name}: {cloc} > 400 LOC"
            if isinstance(node, ast.ExceptHandler):
                t = node.type
                assert t is not None, f"{path}: bare except"
                if isinstance(t, ast.Name):
                    assert t.id not in ("Exception", "BaseException"), f"{path}: broad except {t.id}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("print", "getattr", "hasattr"), f"{path}: forbidden call {node.func.id}"
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
                assert node.attr not in ("environ", "getenv"), f"{path}: forbidden os.{node.attr}"
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else ([node.module] if node.module else [])
                )
                for mod in mods:
                    assert not mod.startswith(("selenium", "spaceinvasion.db", "spaceinvasion.session")), (
                        f"{path}: bad import {mod}"
                    )
                    if path.name in ("logs.py", "_logs_helpers.py"):
                        assert not mod.startswith(("spaceinvasion.planner", "spaceinvasion.combat")), (
                            f"{path}: logs must not import {mod}"
                        )
                    else:
                        assert not mod.startswith("spaceinvasion.combat"), f"{path}: commander must not import {mod}"
            if isinstance(node, ast.Name) and node.id == "Any":
                raise AssertionError(f"{path}: forbidden Any")
