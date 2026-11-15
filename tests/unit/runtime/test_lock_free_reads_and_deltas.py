"""Phase 4 tests: lock-free reads, delta dashboard, invariants, ui_dir, docs, and AST audit."""

from __future__ import annotations

import ast
from contextlib import ExitStack
import inspect
import json
from pathlib import Path
import threading
import time

import pytest
import requests

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import PlannedAction
from spaceinvasion.runtime.api.handlers import build_command_registry
from spaceinvasion.runtime.api.http_server import create_modular_api_server
from spaceinvasion.runtime.api.registry import CommandRegistry, generate_command_reference_markdown
from spaceinvasion.runtime.bridge.bridge import ModularGameApiBridge
from spaceinvasion.session.auth import AntiBotChallengeError
from spaceinvasion.session.executor import ExecutionResult
from tests.e2e.harness import E2ETestHarness
from tests.goldens.api_scenario import (
    API_STEPS,
    REMOVED_COMMANDS,
    FrozenClock,
    build_config,
    build_server,
)
from tests.goldens.scenarios import GOLDEN_EPOCH

REPO_ROOT = Path(__file__).resolve().parents[3]
PREEXISTING_PHASE4_FILES = frozenset(
    {
        "spaceinvasion/runtime/bridge/snapshot.py",
        "spaceinvasion/runtime/api/registry.py",
        "spaceinvasion/runtime/api/validation.py",
    }
)


def _boot_modular_harness(
    stack: ExitStack, *, config: StrategyConfig | None = None
) -> tuple[E2ETestHarness, requests.Session]:
    clock = FrozenClock(GOLDEN_EPOCH)
    server = build_server(clock)
    http = requests.Session()
    http.trust_env = False
    stack.callback(http.close)
    harness = E2ETestHarness(server=server, clock=clock, config=config or build_config())
    h = stack.enter_context(harness)
    return h, http


@pytest.mark.usefixtures("pg_dsn")
def test_lock_free_health_bot_status_and_db_commands_under_held_browser_lock() -> None:
    """GET /health responds < 50 ms and DB-only commands never block while browser_lock is held."""
    with ExitStack() as stack:
        h, http = _boot_modular_harness(stack)
        # Warm snapshot with one plan call.
        warm = http.post(f"{h.base_url}/command", json={"command": "plan", "dry_run": True}, timeout=15)
        assert warm.status_code == 200
        bridge = h.bridge
        assert isinstance(bridge, ModularGameApiBridge)

        held = threading.Event()
        release = threading.Event()

        def _hold_browser_lock() -> None:
            with bridge.ctx.lock:
                held.set()
                release.wait(timeout=3.0)

        holder = threading.Thread(target=_hold_browser_lock, daemon=True)
        holder.start()
        assert held.wait(timeout=2.0)
        try:
            t0 = time.perf_counter()
            r_health = http.get(f"{h.base_url}/health", timeout=2.0)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assert r_health.status_code == 200
            assert elapsed_ms < 50.0, f"/health took {elapsed_ms:.1f} ms while browser_lock held (limit 50 ms)"
            health_body = r_health.json()
            assert health_body["ok"] is True
            assert health_body.get("busy") is True

            r_bot = http.get(f"{h.base_url}/bot_status", timeout=2.0)
            assert r_bot.status_code == 200
            assert r_bot.json()["ok"] is True

            r_dash = http.get(f"{h.base_url}/dashboard?refresh=0", timeout=2.0)
            assert r_dash.status_code == 200
            assert r_dash.json()["ok"] is True

            cmd_payloads: tuple[dict[str, str | int | bool | dict[str, int]], ...] = (
                {"command": "config"},
                {"command": "notifications_list"},
                {"command": "espionage_intel"},
                {"command": "colony_candidates"},
                {"command": "planner_jobs_status"},
                {"command": "db_summary"},
                {"command": "bot_control", "paused": True},
                {"command": "bot_control", "paused": False},
                {"command": "queue_add", "kind": "BUILD", "planet_id": 20001, "target_id": 3},
                {"command": "queue_clear"},
                {"command": "update_config", "config": {"reserved_spy_slots": 2}},
            )
            for cmd_payload in cmd_payloads:
                t_cmd = time.perf_counter()
                r_cmd = http.post(f"{h.base_url}/command", json=cmd_payload, timeout=2.0)
                cmd_ms = (time.perf_counter() - t_cmd) * 1000.0
                assert r_cmd.status_code == 200, f"{cmd_payload} failed: {r_cmd.text}"
                assert r_cmd.json()["ok"] is True
                assert cmd_ms < 250.0, f"DB command {cmd_payload['command']} blocked for {cmd_ms:.1f} ms"
        finally:
            release.set()
            holder.join(timeout=2.0)


@pytest.mark.usefixtures("pg_dsn")
def test_versioned_dashboard_delta_payload_under_50kb_when_unchanged() -> None:
    """Phase 4.3 delta dashboard omits unchanged sections (incl. 8,000-system bot_control) and stays < 5 KB."""
    with ExitStack() as stack:
        h, http = _boot_modular_harness(stack)
        bridge = h.bridge
        assert isinstance(bridge, ModularGameApiBridge)
        scanned_8k = {f"{(i // 400) + 1}:{(i % 400) + 1}": 1_760_000_000.0 + i for i in range(8_000)}
        bridge.views.bind_hooks(on_get_scanned_systems=lambda: scanned_8k)
        bridge.publish_snapshot()
        http.post(f"{h.base_url}/command", json={"command": "plan", "dry_run": True}, timeout=15)

        r_full_compat = http.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0)
        assert r_full_compat.status_code == 200
        full_body = r_full_compat.json()
        assert full_body["ok"] is True
        assert "delta" not in full_body

        r_init = http.get(f"{h.base_url}/dashboard?refresh=0&versions=%7B%7D", timeout=5.0)
        assert r_init.status_code == 200
        init_body = r_init.json()
        assert len(init_body["bot_control"]["scanned_systems"]) == 8_000
        sec_versions = init_body["section_versions"]
        assert isinstance(sec_versions, dict) and "bot_control" in sec_versions and len(sec_versions) >= 5
        latest_min = init_body.get("latest_minute_bucket")
        latest_loot = init_body.get("latest_loot_hour")

        params: dict[str, str] = {"refresh": "0", "versions": json.dumps(sec_versions)}
        if latest_min is not None:
            params["since_minute_bucket"] = str(latest_min)
        if latest_loot is not None:
            params["since_loot_hour"] = str(latest_loot)

        r_delta = http.get(f"{h.base_url}/dashboard", params=params, timeout=5.0)
        assert r_delta.status_code == 200
        assert len(r_delta.content) < 5_000, f"Unchanged delta response too large: {len(r_delta.content)} bytes"
        delta_body = r_delta.json()
        assert delta_body["delta"] is True
        assert "bot_control" not in delta_body
        assert set(delta_body["unchanged_sections"]) == set(sec_versions.keys())
        assert delta_body["hourly_production_history"] == []
        assert delta_body["loot_recycle_hourly"] == []

        # Changing bot_control state ships only updated bot_control and refreshes its version token
        r_pause = http.post(f"{h.base_url}/command", json={"command": "bot_control", "paused": True}, timeout=5.0)
        assert r_pause.status_code == 200
        r_changed = http.get(f"{h.base_url}/dashboard", params=params, timeout=5.0)
        assert r_changed.status_code == 200
        changed_body = r_changed.json()
        assert "bot_control" in changed_body
        assert "bot_control" not in changed_body["unchanged_sections"]
        assert changed_body["bot_control"]["paused"] is True
        assert changed_body["section_versions"]["bot_control"] != sec_versions["bot_control"]


@pytest.mark.usefixtures("pg_dsn")
def test_allow_galaxy_scan_false_aborts_auto_sweep_not_manual_sweep() -> None:
    """Disabling allow_galaxy_scan via update_config immediately aborts automatic sweeps, not manual ones."""
    with ExitStack() as stack:
        h, http = _boot_modular_harness(stack)
        bridge = h.bridge
        assert isinstance(bridge, ModularGameApiBridge)

        # Simulate active automatic sweep.
        bridge.ctx.sweep_in_progress = True
        bridge.ctx.sweep_is_manual = False
        bridge.ctx.sweep_preempt_event.clear()

        resp = http.post(
            f"{h.base_url}/command",
            json={"command": "update_config", "config": {"allow_galaxy_scan": False}},
            timeout=5.0,
        )
        assert resp.status_code == 200
        assert bridge.ctx.strategy_config.allow_galaxy_scan is False
        assert bridge.ctx.sweep_preempt_event.is_set()

        # Re-enable and simulate explicit manual sweep: disabling allow_galaxy_scan must NOT abort manual sweep.
        http.post(
            f"{h.base_url}/command",
            json={"command": "update_config", "config": {"allow_galaxy_scan": True}},
            timeout=5.0,
        )
        bridge.ctx.sweep_in_progress = True
        bridge.ctx.sweep_is_manual = True
        bridge.ctx.sweep_preempt_event.clear()
        http.post(
            f"{h.base_url}/command",
            json={"command": "update_config", "config": {"allow_galaxy_scan": False}},
            timeout=5.0,
        )
        assert not bridge.ctx.sweep_preempt_event.is_set()
        bridge.ctx.sweep_in_progress = False
        bridge.ctx.sweep_is_manual = False


@pytest.mark.usefixtures("pg_dsn")
def test_target_protection_rejection_creates_zero_notifications_and_captcha_pauses() -> None:
    """Target protection rejections emit 0 notifications; CAPTCHA auto-pauses and clears on resume."""
    with ExitStack() as stack:
        h, http = _boot_modular_harness(stack)
        bridge = h.bridge
        assert isinstance(bridge, ModularGameApiBridge)

        act = PlannedAction(
            priority="P2",
            kind="ATTACK",
            planet_id=20001,
            planet_coords="03:120:07",
            target_name="03:120:14",
            reason="Test attack",
            executable_now=True,
        )
        for reason in (
            "This user can't be attacked!",
            "Target is in Vacation mode",
            "Newbie protection active",
            "Attack Zone mismatch",
            "Bash protection triggered",
        ):
            res = ExecutionResult(
                executed=False,
                dry_run=False,
                action=act,
                message=reason,
                validated=False,
                validation_error=reason,
                target_cooldown_seconds=21600.0,
            )
            created = bridge.lifecycle.record_action_validation_notification_locked("attack", res)
            assert created is None

        notif_resp = http.post(f"{h.base_url}/command", json={"command": "notifications_list"}, timeout=5.0)
        assert notif_resp.json()["unread_count"] == 0

        # CAPTCHA gate auto-pause & passive resume.
        bridge.record_antibot_pause(AntiBotChallengeError("Turnstile verification required"))
        bot_status = http.get(f"{h.base_url}/bot_status", timeout=5.0).json()
        assert bot_status["paused"] is True
        assert bot_status["captcha_blocked"] is True
        notifs_after = http.post(f"{h.base_url}/command", json={"command": "notifications_list"}, timeout=5.0).json()
        assert notifs_after["unread_count"] >= 1
        assert any(item["category"] == "captcha_gate" for item in notifs_after["items"])

        resumed = http.post(f"{h.base_url}/command", json={"command": "bot_control", "action": "resume"}, timeout=5.0)
        assert resumed.status_code == 200
        assert resumed.json()["bot_control"]["paused"] is False
        assert resumed.json()["bot_control"]["captcha_blocked"] is False
        notifs_cleared = http.post(f"{h.base_url}/command", json={"command": "notifications_list"}, timeout=5.0).json()
        assert notifs_cleared["unread_count"] == 0


@pytest.mark.usefixtures("pg_dsn")
def test_http_server_explicit_ui_dir_override_and_traversal_guard(tmp_path: Path) -> None:
    """Phase 5.6: explicit ui_dir serves custom static assets and blocks path traversal."""
    custom_ui = tmp_path / "custom_ui"
    (custom_ui / "assets").mkdir(parents=True)
    (custom_ui / "index.html").write_text("<html><body>CUSTOM_UI_ROOT</body></html>", encoding="utf-8")
    (custom_ui / "styles.css").write_text("body { color: cyan; }", encoding="utf-8")
    (custom_ui / "app.js").write_text("console.log('custom');", encoding="utf-8")
    (custom_ui / "assets" / "chunk.js").write_text("export const x = 42;", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")

    with ExitStack() as stack:
        h, http = _boot_modular_harness(stack)
        assert isinstance(h.bridge, ModularGameApiBridge)
        srv = create_modular_api_server(h.bridge, host="127.0.0.1", port=0, ui_dir=custom_ui)
        port = int(srv.server_address[1])
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{port}"
            assert "CUSTOM_UI_ROOT" in http.get(f"{base}/", timeout=2.0).text
            assert "export const x = 42;" in http.get(f"{base}/assets/chunk.js", timeout=2.0).text
            ver = http.get(f"{base}/ui-version", timeout=2.0).json()
            assert ver["ok"] is True and ver["css_version"] != "missing"
            trav = http.get(f"{base}/assets/%2e%2e/secret.txt", timeout=2.0)
            assert trav.status_code in (400, 404)
            assert "TOP_SECRET" not in trav.text
        finally:
            srv.shutdown()
            srv.server_close()
            t.join(timeout=2.0)


@pytest.mark.usefixtures("pg_dsn")
def test_custom_planet_name_preserves_game_name() -> None:
    """Renaming a planet overrides display `name` while preserving canonical `game_name`."""
    with ExitStack() as stack:
        h, http = _boot_modular_harness(stack)
        http.post(f"{h.base_url}/command", json={"command": "plan", "dry_run": True}, timeout=15)
        r_set = http.post(
            f"{h.base_url}/command",
            json={"command": "rename_planet", "planet_id": 20001, "name": "Forge Prime"},
            timeout=5.0,
        )
        assert r_set.status_code == 200 and r_set.json()["ok"] is True
        assert r_set.json()["name"] == "Forge Prime"
        assert r_set.json()["game_name"] and r_set.json()["game_name"] != "Forge Prime"
        dash = http.get(f"{h.base_url}/dashboard?refresh=0", timeout=5.0).json()
        planets = dash["state"]["planets"]
        home = next(p for p in planets if p["planet_id"] == 20001)
        assert home["name"] == "Forge Prime"
        assert home["game_name"] == r_set.json()["game_name"]


def test_command_registry_60_commands_docs_dispatcher_loc_and_section4_ast() -> None:
    """Verify 60/60 kept commands, generated Markdown docs, dispatcher < 100 LOC, and Section 4 AST rules."""
    registry = build_command_registry()
    registered_names = {spec.name for spec in registry.specs()}
    expected_names = {
        str(step.command["command"])
        for step in API_STEPS
        if step.command is not None and step.command.get("command") != "no_such_command"
    }
    assert len(registered_names) == 60
    assert registered_names == expected_names
    assert registered_names.isdisjoint(REMOVED_COMMANDS)

    # Confirm non-browser commands never declare needs_browser=True.
    non_browser_names = {spec.name for spec in registry.specs() if not spec.needs_browser}
    assert len(non_browser_names) >= 33

    # Generated Markdown reference documents all 60 commands.
    docs_md = generate_command_reference_markdown(registry)
    for cmd_name in sorted(expected_names):
        assert f"| `{cmd_name}` |" in docs_md, f"Missing {cmd_name} in command reference Markdown"

    # Dispatcher < 100 LOC.
    dispatch_loc = len(inspect.getsource(CommandRegistry.dispatch).splitlines())
    handle_loc = len(inspect.getsource(ModularGameApiBridge.handle_command).splitlines())
    assert dispatch_loc < 100, f"CommandRegistry.dispatch is {dispatch_loc} LOC"
    assert handle_loc < 100, f"ModularGameApiBridge.handle_command is {handle_loc} LOC"

    # Section 4 AST check across every new Phase 4 production file.
    prod_files = sorted(
        list((REPO_ROOT / "spaceinvasion/runtime/bridge").rglob("*.py"))
        + list((REPO_ROOT / "spaceinvasion/runtime/api").rglob("*.py"))
    )
    prod_files = [p for p in prod_files if "/payloads/" not in p.as_posix()]
    assert len(prod_files) >= 25
    for path in prod_files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        src = path.read_text(encoding="utf-8")
        loc = len(src.splitlines())
        assert loc <= 400, f"{rel} exceeds 400 LOC ({loc})"
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                cloc = (node.end_lineno or node.lineno) - node.lineno + 1
                assert cloc <= 400, f"{rel}:{node.name} exceeds 400 LOC ({cloc})"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                floc = (node.end_lineno or node.lineno) - node.lineno + 1
                assert floc <= 100, f"{rel}:{node.name} exceeds 100 LOC ({floc})"
            elif isinstance(node, ast.ExceptHandler):
                t = node.type
                assert t is not None and not (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException")), (
                    f"Broad except in {rel}:{node.lineno}"
                )
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", f"print() in {rel}:{node.lineno}"
                if rel not in PREEXISTING_PHASE4_FILES:
                    assert node.func.id not in (
                        "getattr",
                        "hasattr",
                        "setattr",
                    ), f"{node.func.id}() in {rel}:{node.lineno}"
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert not (node.value.id == "os" and node.attr in ("environ", "getenv")), (
                    f"os.{node.attr} in {rel}:{node.lineno}"
                )
            elif isinstance(node, ast.Name) and rel not in PREEXISTING_PHASE4_FILES:
                assert node.id != "Any", f"Any used in {rel}:{node.lineno}"
