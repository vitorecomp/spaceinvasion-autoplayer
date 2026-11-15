"""Fake-game integration and Section 4 layering tests for decomposed `client` and `executor` packages."""

from __future__ import annotations

import ast
from pathlib import Path
import typing

import pytest

from backend.core.model import PlannedAction, Resources as ModelResources
from backend.core.state import Resources as LegacyResources
from backend.session.auth import AntiBotChallengeError
from backend.session.client import (
    GameBrowserClient,
    PageViewLimiter,
    RateLimitError,
    SpaceInvasionClient,
    get_default_page_view_limiter,
    is_server_rate_limit_html,
)
from backend.session.executor import ActionExecutor, ExecutionResult, Select
from backend.session.limiter import PageViewLimiter as LimiterClass
from tests.e2e.harness import FakeClock, FakeDriver, FakeSpaceInvasionServer

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLIENT_DIR = _REPO_ROOT / "backend" / "session" / "client"
_EXEC_DIR = _REPO_ROOT / "backend" / "session" / "executor"


def _make_client(**server_kwargs: object) -> tuple[FakeSpaceInvasionServer, GameBrowserClient]:
    clk = FakeClock()
    srv = FakeSpaceInvasionServer(clock=clk, **server_kwargs)  # type: ignore[arg-type]
    lim = LimiterClass(
        min_delay_range=(0.0, 0.0),
        max_views_per_minute=500,
        reserved_interactive_views=0,
        clock=clk.monotonic,
        sleeper=clk.sleep,
    )
    cli = GameBrowserClient(
        typing.cast(object, FakeDriver(srv)),  # type: ignore[arg-type]
        detach_on_close=True,
        min_nav_delay=(0.0, 0.0),
        timeout=5.0,
        page_limiter=lim,
    )
    return srv, cli


def test_browser_client_exports_and_snapshot_navigation() -> None:
    assert SpaceInvasionClient is GameBrowserClient
    assert PageViewLimiter is LimiterClass
    assert isinstance(get_default_page_view_limiter(), LimiterClass)
    assert callable(is_server_rate_limit_html)
    assert Select is not None

    srv, cli = _make_client(
        galaxy_systems={
            (2, 380): [
                {"position": 5, "name": "Farm", "owner": "Inactive", "status_color": "yellow", "has_debris": True}
            ],
            (2, 381): [{"position": 3, "name": "Empty", "owner": "", "status_color": "free"}],
            (2, 385): [{"position": 4, "name": "Far", "owner": "", "status_color": "free"}],
        }
    )

    cli.ensure_in_game()
    ov = cli.read_planets_overview()
    assert len(ov.planets) >= 1

    snap = cli.read_full_snapshot()
    assert len(snap.planets) == len(ov.planets)
    assert snap.active_planet_id is not None

    for menu_key in (
        "internalHome",
        "internalBuildings",
        "internalResearch",
        "internalFleet",
        "internalEconomy",
        "internalWeaponFactory",
    ):
        assert cli.navigate_menu(menu_key)

    g_html, g_gal, g_sys = cli.open_galaxy_with_coords(galaxy=2, system=380)
    assert g_html and (g_gal, g_sys) == (2, 380)
    assert cli.last_galaxy_coords == (2, 380)

    step_html, step_g, step_s = cli.step_galaxy_system(galaxy=2, system=381)
    assert step_html and (step_g, step_s) == (2, 381)
    assert cli.last_galaxy_coords == (2, 381)

    # Distance == 5 (ARROW_NAV_MAX_DISTANCE == 5 -> NOT < 5) must jump directly without arrow stepping.
    cli.open_galaxy_with_coords(galaxy=2, system=380)
    jump_html, jump_g, jump_s = cli.step_galaxy_system(galaxy=2, system=385, pace=False)
    assert jump_html and (jump_g, jump_s) == (2, 385)
    assert cli.last_galaxy_coords == (2, 385)
    assert cli.last_galaxy_nav_method != "arrow"

    spy_res = cli.dispatch_galaxy_spy(galaxy=2, system=380, position=5)
    assert isinstance(spy_res, dict)
    assert spy_res.get("supported") is False
    assert "html" in spy_res

    assert cli.open_building_detail(2)
    assert cli.open_messages()
    unread, _pts, _rank = cli.read_menu_status()
    assert isinstance(unread, dict)


def test_browser_client_antibot_and_rate_limit_guards() -> None:
    _, cli = _make_client()
    captcha_html = "<html><title>Just a moment...</title><div class='cf-turnstile'>Verify you are human</div></html>"
    with pytest.raises(AntiBotChallengeError):
        cli._check_page_guards(captcha_html)

    throttle_html = '<div>You have made more than <span id="pageview-amount">60</span> page views in 1 minute!</div>'
    with pytest.raises(RateLimitError) as exc_info:
        cli._check_page_guards(throttle_html)
    assert exc_info.value.server_throttle is True


def test_executor_dry_run_and_permission_guards() -> None:
    _, cli = _make_client()
    st = cli.read_full_snapshot()
    p0 = st.planets[0]

    dry_ex = ActionExecutor(dry_run=True, action_delay_range=(0.0, 0.0))
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
        PlannedAction(
            "P2",
            "STATION",
            p0.planet_id,
            p0.coords,
            "Station 3x Small transport -> 02:380:14",
            "Transfer",
            executable_now=True,
        ),
        PlannedAction("P2", "COLONIZE", p0.planet_id, p0.coords, "Colonize 02:380:09", "Expand", executable_now=True),
        PlannedAction(
            "P2",
            "RECYCLE",
            p0.planet_id,
            p0.coords,
            "Recycle 02:380:05",
            "Harvest",
            target_level=2,
            executable_now=True,
        ),
        PlannedAction(
            "P2",
            "DEMOLISH_BUILDING",
            p0.planet_id,
            p0.coords,
            "Solar plant",
            "Free fields",
            target_id=7,
            target_level=1,
            executable_now=True,
        ),
        PlannedAction(
            "P2",
            "ABANDON_COLONY",
            p0.planet_id,
            p0.coords,
            "Small colony",
            "Too few fields",
            target_level=300,
            executable_now=True,
        ),
        PlannedAction("P2", "ATTACK", p0.planet_id, p0.coords, "Attack 02:380:05", "Raid", executable_now=True),
        PlannedAction(
            "P2", "BUILD", p0.planet_id, p0.coords, "Blocked", "Not ready", target_id=1, executable_now=False
        ),
    ]
    for act in cases:
        res = dry_ex.execute_action(cli, st, act)
        assert isinstance(res, ExecutionResult)
        assert res.executed is False
        assert res.dry_run is True
        if act.executable_now:
            assert "[DRY-RUN]" in res.message
        if act.kind == "TRANSPORT":
            legacy_act = PlannedAction(
                act.priority,
                act.kind,
                act.planet_id,
                act.planet_coords,
                act.target_name,
                act.reason,
                cost=LegacyResources(1000, 2000, 0, 0),  # type: ignore[arg-type]
                executable_now=act.executable_now,
            )
            res_legacy = dry_ex.execute_action(cli, st, legacy_act)
            assert res_legacy.message == res.message

    guarded_ex = ActionExecutor(
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
        res = guarded_ex.execute_action(cli, st, act)
        assert res.executed is False
        assert "Guarded:" in res.message


def test_executor_live_build_throttle_spy_attack_and_rename_safety() -> None:
    srv, cli = _make_client(
        galaxy_systems={
            (2, 380): [
                {"position": 5, "name": "Farm", "owner": "Inactive", "status_color": "yellow", "has_debris": True}
            ]
        }
    )
    st = cli.read_full_snapshot()
    p0 = st.planets[0]

    ex = ActionExecutor(
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
        res = ex.execute_action(cli, st, act)
        assert isinstance(res, ExecutionResult)
        assert res.executed is True, f"{act.kind} failed: {res.message}"
        if res.updated_state is not None:
            st = res.updated_state

    assert len(srv.launched_fleets) >= 2

    # Verify planet rename safety (never touches confirmPlanetDeletion / deletePlanet).
    ren = ex.sync_planet_names_in_game(cli, st, {p0.planet_id: "HomeAlpha"})
    assert ren.get("ok") is True
    assert p0.planet_id in srv.planets
    assert not any("delete" in str(entry).lower() for entry in srv.action_log)


def test_executor_extended_missions_and_multiplanet() -> None:
    srv, cli = _make_client(
        galaxy_systems={
            (2, 380): [
                {"position": 5, "name": "Farm", "owner": "Inactive", "status_color": "yellow", "has_debris": True},
                {"position": 9, "name": "Empty", "owner": "", "status_color": "free"},
            ]
        }
    )
    pid0 = next(iter(srv.planets))
    srv.planets[pid0]["ships"].update({"s1": 20, "s2": 10, "s4": 15, "s8": 2, "s10": 5, "s11": 4})
    srv.planets[pid0]["resources"] = [50000, 50000, 50000, 50000]

    st = cli.read_full_snapshot()
    p0 = st.planets[0]

    ex = ActionExecutor(
        dry_run=False,
        action_delay_range=(0.0, 0.0),
        allow_fleetsave=True,
        allow_raids=True,
        allow_colonize=True,
        allow_transport=True,
        allow_transmitter=True,
    )
    actions: list[PlannedAction] = [
        PlannedAction(
            "P2",
            "RESEARCH",
            p0.planet_id,
            p0.coords,
            "Energy technology",
            "Tech",
            target_id=2,
            target_level=3,
            executable_now=True,
        ),
        PlannedAction(
            "P2",
            "RECYCLE",
            p0.planet_id,
            p0.coords,
            "Recycle 02:380:05",
            "Harvest",
            target_level=2,
            executable_now=True,
        ),
        PlannedAction("P2", "COLONIZE", p0.planet_id, p0.coords, "Colonize 02:380:09", "Expand", executable_now=True),
        PlannedAction(
            "P2",
            "TRANSPORT",
            p0.planet_id,
            p0.coords,
            "Transport -> 02:380:05",
            "Haul",
            cost=ModelResources(1000, 2000, 500, 0),
            executable_now=True,
        ),
        PlannedAction(
            "P2",
            "STATION",
            p0.planet_id,
            p0.coords,
            "Station 2x Small transport -> 02:380:05",
            "Transfer",
            executable_now=True,
        ),
        PlannedAction(
            "P0", "ESCAPE_FLIGHT", p0.planet_id, p0.coords, "Escape -> 02:380:05", "Save", executable_now=True
        ),
    ]
    for act in actions:
        res = ex.execute_action(cli, st, act)
        assert isinstance(res, ExecutionResult)
        if res.updated_state is not None:
            st = res.updated_state
    assert len(srv.launched_fleets) >= 1


def test_section4_ast_loc_and_strict_session_layering() -> None:
    client_files = sorted(_CLIENT_DIR.glob("*.py"))
    exec_files = sorted(_EXEC_DIR.glob("*.py"))
    assert len(client_files) >= 5
    assert len(exec_files) >= 7
    all_files = [*client_files, *exec_files]

    common_forbidden = (
        "backend.planner",
        "backend.combat",
        "backend.db",
        "backend.runtime",
        "backend.ui",
        "backend.core.game_data",
        "backend.core.state",
        "backend.core.cli_support",
        "backend.core.logging_utils",
        "backend.session.browser_client",
        "backend.session.exec_ops",
    )

    for path in all_files:
        forbidden = (*common_forbidden, "backend.session.executor") if path.parent == _CLIENT_DIR else common_forbidden
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
                assert node.func.id not in ("getattr", "hasattr", "setattr"), (
                    f"Forbidden {node.func.id}() in {path.name}"
                )
            elif isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    pytest.fail(f"Bare except in {path.name}")
                if isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException"):
                    pytest.fail(f"Broad except {node.type.id} in {path.name}")
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert not (node.value.id == "os" and node.attr == "environ"), f"os.environ in {path.name}"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden), f"Forbidden import {alias.name} in {path.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden), f"Forbidden import {node.module} in {path.name}"
    _ = typing.cast(object, None)
