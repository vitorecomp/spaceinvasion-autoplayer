"""Parity, behavioral, and Section 4 tests for ``runtime.logs`` and ``runtime.commander``."""

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

import bin.commander as legacy_cmd
from spaceinvasion.core import logging_utils as legacy_logs
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import (
    EnergyBalance,
    GameState,
    PlanetState,
    PlannedAction,
    QueueJob,
    Resources,
)
from spaceinvasion.core.state import (
    EnergyBalance as LegacyEnergy,
    GameState as LegacyGameState,
    PlanetState as LegacyPlanetState,
    Resources as LegacyResources,
)
from spaceinvasion.planner.strategy import PlannedAction as LegacyPlannedAction
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


def _make_legacy_state() -> LegacyGameState:
    planet = LegacyPlanetState(
        planet_id=101,
        name="Homeworld",
        coords="01:100:04",
        planet_type="normal",
        temperature=25,
        fields_used=40,
        fields_max=300,
        resources=LegacyResources(pig_iron=5000, metal=3000, kryptonite=2000, spice=1000),
        storage=LegacyResources(pig_iron=100_000, metal=100_000, kryptonite=100_000, spice=100_000),
        energy=LegacyEnergy(produced=500, used=400),
        buildings={"b1": 10, "b2": 8, "b3": 6, "b4": 5, "b5": 8, "b7": 4},
        economy_rates={"resource2": 100},
    )
    return LegacyGameState(
        universe_id=313,
        user_id=999,
        nickname="Tester",
        timestamp=1_700_000_000.0,
        active_planet_id=101,
        planets=[planet],
    )


class _FakeApi:
    """In-memory fake HTTP API implementing ``CommanderApiLike``."""

    def __init__(
        self,
        *,
        state: GameState | LegacyGameState | None = None,
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


def test_runtime_logs_parity_with_legacy_logging_utils(tmp_path: Path) -> None:
    wd_err = (
        "Message: element click intercepted sid=SUPERSECRET123\n"
        "  (Session info: chrome=124.0)\n"
        "Stacktrace:\n#0 0x1234 base::debug\n"
    )
    assert new_logs.strip_webdriver_stacktrace(wd_err) == legacy_logs.strip_webdriver_stacktrace(wd_err)
    assert new_logs.format_clean_error(wd_err) == legacy_logs.format_clean_error(wd_err)
    assert "SUPERSECRET123" not in new_logs.format_clean_error(wd_err)

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
    act_leg = LegacyPlannedAction(
        priority="P1",
        kind="BUILD",
        planet_id=101,
        planet_coords="01:100:04",
        target_name="Pig iron mine",
        reason="grow",
        target_id=1,
        target_level=11,
        missing=LegacyResources(pig_iron=250, metal=0, kryptonite=100, spice=0),
        executable_now=False,
    )
    assert new_logs.format_action_summary(act_new) == legacy_logs.format_action_summary(act_leg)
    assert new_logs.format_action_summary(None) == legacy_logs.format_action_summary(None)

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

    # Default repo logs dir is skipped unless explicitly opted in.
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

    # API log & crash log stdout/stderr stream behavior + explicit dir writing
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


def test_commander_error_and_payload_parity_against_legacy_bin() -> None:
    rl_cases = [
        ("normal error", None),
        ("HTTP 429 Too Many Requests retry-after 120s", None),
        ("page-view throttle cooldown active 65s", {"rate_limited": True, "retry_after": 95}),
        ("rate limit hit", {"rate_limited": True, "retry_after": 99999}),
    ]
    for text, health in rl_cases:
        assert compute_rate_limit_backoff(text, health) == legacy_cmd.compute_rate_limit_backoff(text, health)

    for text in ("ok", "Cloudflare Turnstile active", "Sicherheitsprüfung required", "anti-bot challenge"):
        assert is_captcha_error(text) == legacy_cmd.is_captcha_error(text)
    assert is_captcha_error("", bot_ctrl={"captcha_blocked": True}) is True

    assert is_galaxy_scan_busy_error("Galaxy sweep in progress") == legacy_cmd.is_galaxy_scan_busy_error(
        "Galaxy sweep in progress"
    )
    assert (
        is_galaxy_scan_busy_error("", bot_ctrl={"galaxy_scan_blocking": True, "p0_escape_flight_ready": True}) is False
    )
    assert is_connection_refused_error(requests.exceptions.ConnectionError("refused")) is True

    for t in ("", "01:02:03", "2h 5m 10s", "45s", "none"):
        assert parse_queue_timer_seconds(t) == legacy_cmd._parse_queue_timer_seconds(t)

    # All 16 command payload kinds match legacy bin/commander.py 1:1
    action_specs = [
        dict(
            priority="P1",
            kind="CLAIM_TUTORIAL",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Tutorial",
            reason="claim",
        ),
        dict(
            priority="P1",
            kind="BUILD",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Mine",
            reason="grow",
            target_id=1,
            target_level=11,
        ),
        dict(
            priority="P2",
            kind="DEMOLISH_BUILDING",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Thermo",
            reason="free",
            target_id=6,
            target_level=0,
        ),
        dict(
            priority="P1",
            kind="RESEARCH",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Mining",
            reason="tech",
            target_id=1,
            target_level=4,
        ),
        dict(
            priority="P2",
            kind="BUILD_SHIP",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Transmitter",
            reason="cargo",
            target_id=11,
            target_level=5,
        ),
        dict(
            priority="P1",
            kind="THROTTLE_BUILDING",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Blast furnace",
            reason="metal storage full",
            target_id=2,
            target_level=0,
        ),
        dict(
            priority="P0",
            kind="ESCAPE_FLIGHT",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Escape 01:100:05",
            reason="hostile incoming",
        ),
        dict(
            priority="P2",
            kind="SCAN_GALAXY",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Scan 02:150",
            reason="explore",
        ),
        dict(
            priority="P1",
            kind="SPY",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Spy 01:101:09",
            reason="intel",
            target_level=3,
        ),
        dict(
            priority="P2",
            kind="RECYCLE",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="DF 01:101:09",
            reason="debris",
            target_level=4,
        ),
        dict(
            priority="P1",
            kind="TRANSPORT",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="To 01:102:06",
            reason="feed",
            target_id=102,
            cost={"pig_iron": 100, "metal": 200, "kryptonite": 50, "spice": 10},
        ),
        dict(
            priority="P2",
            kind="STATION",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="01:102:06 [10x s11, 2x s4]",
            reason="rebalance",
            target_id=102,
        ),
        dict(
            priority="P1",
            kind="COLONIZE",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Colony 01:105:08",
            reason="expand",
        ),
        dict(
            priority="P1",
            kind="ABANDON_COLONY",
            planet_id=102,
            planet_coords="01:102:06",
            target_name="Small",
            reason="too small",
            target_level=300,
        ),
        dict(
            priority="P2",
            kind="ACTIVATE_TRANSMITTER",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Transmitter",
            reason="spin",
        ),
        dict(
            priority="P1",
            kind="ATTACK",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Raid 01:101:09",
            reason="loot",
        ),
        dict(
            priority="P3",
            kind="WAIT_RESOURCES",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="Wait",
            reason="saving",
        ),
    ]
    for spec in action_specs:
        new_act = planned_action_from_dict(spec)
        leg_act = legacy_cmd._planned_action_from_dict(spec)
        assert new_act is not None and leg_act is not None
        for dry_run in (True, False):
            assert action_to_command_payload(new_act, dry_run=dry_run) == legacy_cmd._action_to_command_payload(
                leg_act, dry_run=dry_run
            )

    # Busy fleet slot holding parity
    a_fleet = planned_action_from_dict(
        dict(
            priority="P1",
            kind="SPY",
            planet_id=101,
            planet_coords="01:100:04",
            target_name="01:101:09",
            reason="probe",
            executable_now=True,
        )
    )
    a_build = planned_action_from_dict(
        dict(
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
    )
    assert a_fleet is not None and a_build is not None
    held_new = hold_busy_fleet_slots([a_fleet, a_build], {101: 45.0}, re_sort_priority=True)
    assert held_new[0].kind == "BUILD" and held_new[1].kind == "SPY" and not held_new[1].executable_now
    assert "[Hold: fleet slots busy / returning]" in held_new[1].reason

    # Wakeup computation parity
    st_model = _make_model_state()
    st_legacy = _make_legacy_state()
    w_new = compute_next_wakeup_seconds(st_model, a_build, [], min_interval_s=30.0, max_idle_s=600.0)
    w_leg = legacy_cmd.compute_next_wakeup_seconds(
        st_legacy,
        legacy_cmd._planned_action_from_dict(
            dict(
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
        ),
        [],
        min_interval_s=30.0,
        max_idle_s=600.0,
    )
    assert w_new == w_leg


def test_commander_loop_captcha_passive_wait_reconnect_and_rate_limit(tmp_path: Path) -> None:
    # 1. CAPTCHA passive wait: ZERO get_state / send_command calls while captcha_blocked=True!
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

    # 2. ConnectionRefused reload window: reconnect succeeds via health() poll!
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

    # 3. Rate-limit backoff queries health() retry_after and backs off!
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

    # 4. Direct tick execution in AI planner mode via /command plan
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
