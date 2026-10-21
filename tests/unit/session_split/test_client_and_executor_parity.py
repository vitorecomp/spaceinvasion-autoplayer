"""Fake-game parity and Section 4 layering tests for `browser_client` and `exec_ops`."""

from __future__ import annotations

import ast
from dataclasses import asdict
from pathlib import Path
import typing

import pytest

from spaceinvasion.core.model import PlannedAction, Resources as ModelResources
from spaceinvasion.session import client as legacy_client_mod
from spaceinvasion.session import executor as legacy_executor_mod
from spaceinvasion.session.auth import AntiBotChallengeError
from spaceinvasion.session.browser_client import (
    GameBrowserClient,
    PageViewLimiter,
    RateLimitError,
    SpaceInvasionClient,
)
from spaceinvasion.session.exec_ops import ActionExecutor, ExecutionResult
from spaceinvasion.session.limiter import PageViewLimiter as LimiterClass
from tests.e2e.harness import FakeClock, FakeDriver, FakeSpaceInvasionServer

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLIENT_DIR = _REPO_ROOT / "spaceinvasion" / "session" / "browser_client"
_EXEC_DIR = _REPO_ROOT / "spaceinvasion" / "session" / "exec_ops"


def _make_pair(
    **server_kwargs: object,
) -> tuple[
    tuple[FakeSpaceInvasionServer, legacy_client_mod.GameBrowserClient],
    tuple[FakeSpaceInvasionServer, GameBrowserClient],
]:
    clk1 = FakeClock()
    clk2 = FakeClock()
    srv1 = FakeSpaceInvasionServer(clock=clk1, **server_kwargs)  # type: ignore[arg-type]
    srv2 = FakeSpaceInvasionServer(clock=clk2, **server_kwargs)  # type: ignore[arg-type]
    lim1 = legacy_client_mod.PageViewLimiter(
        min_delay_range=(0.0, 0.0),
        max_views_per_minute=500,
        reserved_interactive_views=0,
    )
    lim2 = LimiterClass(
        min_delay_range=(0.0, 0.0),
        max_views_per_minute=500,
        reserved_interactive_views=0,
        clock=clk2.monotonic,
        sleeper=clk2.sleep,
    )
    c1 = legacy_client_mod.GameBrowserClient(
        FakeDriver(srv1),
        detach_on_close=True,
        min_nav_delay=(0.0, 0.0),
        timeout=5.0,
        page_limiter=lim1,
    )
    c2 = GameBrowserClient(
        FakeDriver(srv2),
        detach_on_close=True,
        min_nav_delay=(0.0, 0.0),
        timeout=5.0,
        page_limiter=lim2,
    )
    return (srv1, c1), (srv2, c2)


def _norm_result(res: object) -> dict[str, object]:
    d = asdict(res)  # type: ignore[call-overload]
    act = d.get("action")
    if isinstance(act, dict):
        d["action"] = {
            "priority": act.get("priority"),
            "kind": act.get("kind"),
            "planet_id": act.get("planet_id"),
            "planet_coords": act.get("planet_coords"),
            "target_name": act.get("target_name"),
            "target_id": act.get("target_id"),
            "target_level": act.get("target_level"),
            "executable_now": act.get("executable_now"),
        }
    return d


def test_browser_client_exports_and_snapshot_navigation_parity() -> None:
    assert SpaceInvasionClient is GameBrowserClient
    assert PageViewLimiter is LimiterClass
    (srv1, c1), (srv2, c2) = _make_pair(
        galaxy_systems={
            (2, 380): [
                {"position": 5, "name": "Farm", "owner": "Inactive", "status_color": "yellow", "has_debris": True}
            ],
            (2, 381): [{"position": 3, "name": "Empty", "owner": "", "status_color": "free"}],
        }
    )

    c1.ensure_in_game()
    c2.ensure_in_game()

    ov1 = c1.read_planets_overview()
    ov2 = c2.read_planets_overview()
    assert asdict(ov2) == asdict(ov1)

    snap1 = c1.read_full_snapshot()
    snap2 = c2.read_full_snapshot()
    assert asdict(snap2) == asdict(snap1)

    for menu_key in ("internalHome", "internalBuildings", "internalResearch", "internalFleet", "internalSettings"):
        assert c2.navigate_menu(menu_key) == c1.navigate_menu(menu_key)

    g_html1 = c1.open_galaxy_with_coords(2, 380)
    g_html2 = c2.open_galaxy_with_coords(2, 380)
    assert g_html2 == g_html1
    assert c2.last_galaxy_coords == c1.last_galaxy_coords

    step1 = c1.step_galaxy_system(2, 381)
    step2 = c2.step_galaxy_system(2, 381)
    assert step2 == step1
    assert c2.last_galaxy_coords == c1.last_galaxy_coords

    spy1 = c1.dispatch_galaxy_spy(2, 380, 5)
    spy2 = c2.dispatch_galaxy_spy(2, 380, 5)
    assert spy2 == spy1

    assert c2.open_building_detail(2) == c1.open_building_detail(2)
    assert c2.open_messages() == c1.open_messages()
    assert c2.read_menu_status() == c1.read_menu_status()
    assert srv2.launched_fleets == srv1.launched_fleets


def test_browser_client_antibot_and_rate_limit_guards() -> None:
    (_, c1), (_, c2) = _make_pair()
    captcha_html = "<html><title>Just a moment...</title><div class='cf-turnstile'>Verify you are human</div></html>"
    with pytest.raises(AntiBotChallengeError):
        c1._check_page_guards(captcha_html)
    with pytest.raises(AntiBotChallengeError):
        c2._check_page_guards(captcha_html)

    throttle_html = '<div>You have made more than <span id="pageview-amount">60</span> page views in 1 minute!</div>'
    with pytest.raises(legacy_client_mod.RateLimitError):
        c1._check_page_guards(throttle_html)
    with pytest.raises(RateLimitError) as exc_info:
        c2._check_page_guards(throttle_html)
    assert exc_info.value.server_throttle is True


def test_executor_dry_run_and_permission_guards_parity() -> None:
    (_, c1), (_, c2) = _make_pair()
    st1 = c1.read_full_snapshot()
    st2 = c2.read_full_snapshot()
    p0 = st1.planets[0]

    dry_ex1 = legacy_executor_mod.ActionExecutor(dry_run=True, action_delay_range=(0.0, 0.0))
    dry_ex2 = ActionExecutor(dry_run=True, action_delay_range=(0.0, 0.0))

    cases: list[PlannedAction] = [
        PlannedAction(
            "P2",
            "BUILD",
            p0.planet_id,
            p0.coords,
            "Pig iron mine",
            "Upgrade",
            target_id=1,
            target_level=6,
            executable_now=True,
        ),
        PlannedAction(
            "P1",
            "THROTTLE_BUILDING",
            p0.planet_id,
            p0.coords,
            "Blast furnace",
            "Throttle",
            target_id=2,
            target_level=0,
            executable_now=True,
        ),
        PlannedAction(
            "P2",
            "BUILD_SHIP",
            p0.planet_id,
            p0.coords,
            "Small transport",
            "Build",
            target_id=1,
            target_level=3,
            executable_now=True,
        ),
        PlannedAction(
            "P2", "SPY", p0.planet_id, p0.coords, "Spy 02:380:05", "Scout", target_level=1, executable_now=True
        ),
        PlannedAction(
            "P2",
            "TRANSPORT",
            p0.planet_id,
            p0.coords,
            "Transport -> 02:380:14",
            "Haul",
            cost=ModelResources(1000, 2000, 0, 0),
            executable_now=True,
        ),
        PlannedAction("P2", "ATTACK", p0.planet_id, p0.coords, "Attack 02:380:05", "Raid", executable_now=True),
        PlannedAction(
            "P2", "BUILD", p0.planet_id, p0.coords, "Blocked", "Not ready", target_id=1, executable_now=False
        ),
    ]
    for act in cases:
        r1 = dry_ex1.execute_action(c1, st1, act)  # type: ignore[arg-type]
        r2 = dry_ex2.execute_action(c2, st2, act)
        assert isinstance(r2, ExecutionResult)
        assert _norm_result(r2) == _norm_result(r1)

    guarded_ex1 = legacy_executor_mod.ActionExecutor(
        dry_run=False,
        action_delay_range=(0.0, 0.0),
        allow_fleetsave=False,
        allow_raids=False,
        allow_colonize=False,
        allow_transport=False,
        allow_transmitter=False,
    )
    guarded_ex2 = ActionExecutor(
        dry_run=False,
        action_delay_range=(0.0, 0.0),
        allow_fleetsave=False,
        allow_raids=False,
        allow_colonize=False,
        allow_transport=False,
        allow_transmitter=False,
    )
    for kind in (
        "ESCAPE_FLIGHT",
        "COLONIZE",
        "TRANSPORT",
        "STATION",
        "ABANDON_COLONY",
        "ATTACK",
        "ACTIVATE_TRANSMITTER",
    ):
        act = PlannedAction("P2", kind, p0.planet_id, p0.coords, f"Test {kind}", "Guard check", executable_now=True)
        r1 = guarded_ex1.execute_action(c1, st1, act)  # type: ignore[arg-type]
        r2 = guarded_ex2.execute_action(c2, st2, act)
        assert _norm_result(r2) == _norm_result(r1)
        assert r2.executed is False


def test_executor_live_build_throttle_spy_attack_and_rename_safety_parity() -> None:
    (srv1, c1), (srv2, c2) = _make_pair(
        galaxy_systems={
            (2, 380): [
                {"position": 5, "name": "Farm", "owner": "Inactive", "status_color": "yellow", "has_debris": True}
            ]
        }
    )
    st1 = c1.read_full_snapshot()
    st2 = c2.read_full_snapshot()
    p0 = st1.planets[0]

    ex1 = legacy_executor_mod.ActionExecutor(
        dry_run=False,
        action_delay_range=(0.0, 0.0),
        allow_fleetsave=True,
        allow_raids=True,
        allow_colonize=True,
        allow_transport=True,
        allow_transmitter=True,
    )
    ex2 = ActionExecutor(
        dry_run=False,
        action_delay_range=(0.0, 0.0),
        allow_fleetsave=True,
        allow_raids=True,
        allow_colonize=True,
        allow_transport=True,
        allow_transmitter=True,
    )

    live_actions: list[PlannedAction] = [
        PlannedAction(
            "P2",
            "BUILD",
            p0.planet_id,
            p0.coords,
            "Pig iron mine",
            "Upgrade",
            target_id=1,
            target_level=6,
            executable_now=True,
        ),
        PlannedAction(
            "P1",
            "THROTTLE_BUILDING",
            p0.planet_id,
            p0.coords,
            "Blast furnace",
            "Pause",
            target_id=2,
            target_level=0,
            executable_now=True,
        ),
        PlannedAction(
            "P2",
            "BUILD_SHIP",
            p0.planet_id,
            p0.coords,
            "Small transport",
            "Build",
            target_id=1,
            target_level=1,
            executable_now=True,
        ),
        PlannedAction("P2", "SCAN_GALAXY", p0.planet_id, p0.coords, "Scan 02:380", "Scan 02:380", executable_now=True),
        PlannedAction(
            "P2",
            "SPY",
            p0.planet_id,
            p0.coords,
            "Spy 02:380:05",
            "Scout 02:380:05",
            target_level=1,
            executable_now=True,
        ),
        PlannedAction(
            "P2", "ATTACK", p0.planet_id, p0.coords, "Attack 02:380:05", "Raid 02:380:05", executable_now=True
        ),
    ]
    for act in live_actions:
        r1 = ex1.execute_action(c1, st1, act)  # type: ignore[arg-type]
        r2 = ex2.execute_action(c2, st2, act)
        assert _norm_result(r2) == _norm_result(r1)
        if r1.updated_state is not None:
            st1 = r1.updated_state
        if r2.updated_state is not None:
            st2 = r2.updated_state

    assert srv2.action_log == srv1.action_log
    assert srv2.launched_fleets == srv1.launched_fleets

    # Verify planet rename safety & parity (never touches confirmPlanetDeletion / deletePlanet).
    ren1 = ex1.sync_planet_names_in_game(c1, st1, {p0.planet_id: "HomeAlpha"}, dry_run=False)
    ren2 = ex2.sync_planet_names_in_game(c2, st2, {p0.planet_id: "HomeAlpha"}, dry_run=False)
    assert ren2 == ren1
    assert p0.planet_id in srv2.planets
    assert srv2.planets[p0.planet_id]["name"] == srv1.planets[p0.planet_id]["name"]


def test_section4_ast_loc_and_strict_session_layering() -> None:
    client_files = sorted(_CLIENT_DIR.glob("*.py"))
    exec_files = sorted(_EXEC_DIR.glob("*.py"))
    assert len(client_files) >= 5
    assert len(exec_files) >= 7
    all_files = [*client_files, *exec_files]

    forbidden_prefixes = (
        "spaceinvasion.planner",
        "spaceinvasion.combat",
        "spaceinvasion.db",
        "spaceinvasion.runtime",
        "spaceinvasion.ui",
        "spaceinvasion.core.game_data",
        "spaceinvasion.core.state",
        "spaceinvasion.core.cli_support",
        "spaceinvasion.core.logging_utils",
        "spaceinvasion.session.client",
        "spaceinvasion.session.executor",
    )

    for path in all_files:
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        assert len(lines) <= 400, f"{path.name} has {len(lines)} lines (>400)"
        tree = ast.parse(src, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_len = (node.end_lineno or node.lineno) - node.lineno + 1
                assert fn_len <= 100, f"{path.name}:{node.name} has {fn_len} lines (>100)"
            elif isinstance(node, ast.ClassDef):
                cls_len = (node.end_lineno or node.lineno) - node.lineno + 1
                assert cls_len <= 400, f"{path.name}:{node.name} has {cls_len} lines (>400)"
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print", f"Forbidden print() in {path.name}"
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    pytest.fail(f"Bare except in {path.name}")
                if isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException"):
                    pytest.fail(f"Broad except {node.type.id} in {path.name}")
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert not (node.value.id == "os" and node.attr == "environ"), f"os.environ in {path.name}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_prefixes), (
                        f"Forbidden import {alias.name} in {path.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden_prefixes), f"Forbidden import {node.module} in {path.name}"
    _ = typing.cast(object, None)
