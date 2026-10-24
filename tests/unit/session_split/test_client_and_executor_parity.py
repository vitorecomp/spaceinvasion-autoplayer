"""Fake-game parity and Section 4 layering tests for `browser_client` and `exec_ops`."""

from __future__ import annotations

import ast
from dataclasses import asdict
from pathlib import Path
import typing

import pytest

from spaceinvasion.core.model import PlannedAction, Resources as ModelResources
from spaceinvasion.core.state import Resources as LegacyResources
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
        typing.cast(object, FakeDriver(srv2)),  # type: ignore[arg-type]
        detach_on_close=True,
        min_nav_delay=(0.0, 0.0),
        timeout=5.0,
        page_limiter=lim2,
    )
    return (srv1, c1), (srv2, c2)


def _norm_state(obj: object) -> object:
    if obj is None:
        return None
    raw = obj if isinstance(obj, dict) else asdict(obj)  # type: ignore[call-overload]
    if isinstance(raw, dict):
        cleaned: dict[str, object] = {k: v for k, v in raw.items() if k != "timestamp"}
        queues = cleaned.get("active_queues")
        if isinstance(queues, (list, tuple)):
            cleaned["active_queues"] = tuple(
                {qk: qv for qk, qv in q.items() if qk != "finish_ts"} if isinstance(q, dict) else q for q in queues
            )
        return cleaned
    return raw


def _norm_fleets(fleets: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{k: v for k, v in item.items() if k != "ts"} for item in fleets]


def _norm_result(res: object) -> dict[str, object]:
    d: dict[str, object] = dict(asdict(res))  # type: ignore[call-overload]
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
    if "updated_state" in d and d["updated_state"] is not None:
        d["updated_state"] = _norm_state(d["updated_state"])
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
            (2, 385): [{"position": 4, "name": "Far", "owner": "", "status_color": "free"}],
        }
    )

    c1.ensure_in_game()
    c2.ensure_in_game()

    ov1 = c1.read_planets_overview()
    ov2 = c2.read_planets_overview()
    assert _norm_state(ov2) == _norm_state(ov1)

    snap1 = c1.read_full_snapshot()
    snap2 = c2.read_full_snapshot()
    assert _norm_state(snap2) == _norm_state(snap1)

    for menu_key in (
        "internalHome",
        "internalBuildings",
        "internalResearch",
        "internalFleet",
        "internalEconomy",
        "internalWeaponFactory",
    ):
        assert c2.navigate_menu(menu_key) == c1.navigate_menu(menu_key)

    g_html1 = c1.open_galaxy_with_coords(galaxy=2, system=380)
    g_html2 = c2.open_galaxy_with_coords(galaxy=2, system=380)
    assert g_html2 == g_html1
    assert c2.last_galaxy_coords == c1.last_galaxy_coords

    step1 = c1.step_galaxy_system(galaxy=2, system=381)
    step2 = c2.step_galaxy_system(galaxy=2, system=381)
    assert step2 == step1
    assert c2.last_galaxy_coords == c1.last_galaxy_coords

    # Distance == 5 (ARROW_NAV_MAX_DISTANCE == 5 -> NOT < 5) must jump directly without arrow stepping.
    c1.open_galaxy_with_coords(galaxy=2, system=380)
    c2.open_galaxy_with_coords(galaxy=2, system=380)
    jump1 = c1.step_galaxy_system(galaxy=2, system=385, pace=False)
    jump2 = c2.step_galaxy_system(galaxy=2, system=385, pace=False)
    assert jump2 == jump1
    assert c2.last_galaxy_coords == c1.last_galaxy_coords == (2, 385)
    assert c2.last_galaxy_nav_method == c1.last_galaxy_nav_method
    assert c2.last_galaxy_nav_method != "arrow"

    spy1 = c1.dispatch_galaxy_spy(galaxy=2, system=380, position=5)
    spy2 = c2.dispatch_galaxy_spy(galaxy=2, system=380, position=5)
    assert spy2 == spy1

    assert c2.open_building_detail(2) == c1.open_building_detail(2)
    assert c2.open_messages() == c1.open_messages()
    assert c2.read_menu_status() == c1.read_menu_status()
    assert _norm_fleets(srv2.launched_fleets) == _norm_fleets(srv1.launched_fleets)


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
        PlannedAction(
            "P2",
            "STATION",
            p0.planet_id,
            p0.coords,
            "Station 3x Small transport -> 02:380:14",
            "Transfer",
            executable_now=True,
        ),
        PlannedAction(
            "P2",
            "COLONIZE",
            p0.planet_id,
            p0.coords,
            "Colonize 02:380:09",
            "Expand",
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
        legacy_act = act
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
        r1 = dry_ex1.execute_action(c1, st1, legacy_act)  # type: ignore[arg-type]
        r2 = dry_ex2.execute_action(c2, st2, act)
        assert isinstance(r2, ExecutionResult)
        assert _norm_result(r2) == _norm_result(r1)
        if act.kind == "TRANSPORT":
            r2_legacy = dry_ex2.execute_action(c2, st2, legacy_act)
            assert _norm_result(r2_legacy) == _norm_result(r1)

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
    assert _norm_fleets(srv2.launched_fleets) == _norm_fleets(srv1.launched_fleets)

    # Verify planet rename safety & parity (never touches confirmPlanetDeletion / deletePlanet).
    ren1 = ex1.sync_planet_names_in_game(c1, st1, {p0.planet_id: "HomeAlpha"})
    ren2 = ex2.sync_planet_names_in_game(c2, st2, {p0.planet_id: "HomeAlpha"})
    assert {k: v for k, v in ren2.items() if k != "updated_state"} == {
        k: v for k, v in ren1.items() if k != "updated_state"
    }
    assert _norm_state(ren2["updated_state"]) == _norm_state(ren1["updated_state"])
    assert p0.planet_id in srv2.planets
    assert srv2.planets[p0.planet_id]["name"] == srv1.planets[p0.planet_id]["name"]


def test_executor_extended_missions_and_multiplanet_parity() -> None:
    (srv1, c1), (srv2, c2) = _make_pair(
        galaxy_systems={
            (2, 380): [
                {"position": 5, "name": "Farm", "owner": "Inactive", "status_color": "yellow", "has_debris": True},
                {"position": 9, "name": "Empty", "owner": "", "status_color": "free"},
            ]
        }
    )
    for srv in (srv1, srv2):
        pid0 = next(iter(srv.planets))
        srv.planets[pid0]["ships"].update({"s1": 20, "s2": 10, "s4": 15, "s8": 2, "s10": 5, "s11": 4})
        srv.planets[pid0]["resources"] = [50000, 50000, 50000, 50000]

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

    actions: list[tuple[PlannedAction, PlannedAction]] = [
        (
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
                "RESEARCH",
                p0.planet_id,
                p0.coords,
                "Energy technology",
                "Tech",
                target_id=2,
                target_level=3,
                executable_now=True,
            ),
        ),
        (
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
                "RECYCLE",
                p0.planet_id,
                p0.coords,
                "Recycle 02:380:05",
                "Harvest",
                target_level=2,
                executable_now=True,
            ),
        ),
        (
            PlannedAction(
                "P2",
                "COLONIZE",
                p0.planet_id,
                p0.coords,
                "Colonize 02:380:09",
                "Expand",
                executable_now=True,
            ),
            PlannedAction(
                "P2",
                "COLONIZE",
                p0.planet_id,
                p0.coords,
                "Colonize 02:380:09",
                "Expand",
                executable_now=True,
            ),
        ),
        (
            PlannedAction(
                "P2",
                "TRANSPORT",
                p0.planet_id,
                p0.coords,
                "Transport -> 02:380:05",
                "Haul",
                cost=LegacyResources(1000, 2000, 500, 0),  # type: ignore[arg-type]
                executable_now=True,
            ),
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
        ),
        (
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
                "P2",
                "STATION",
                p0.planet_id,
                p0.coords,
                "Station 2x Small transport -> 02:380:05",
                "Transfer",
                executable_now=True,
            ),
        ),
        (
            PlannedAction(
                "P0",
                "ESCAPE_FLIGHT",
                p0.planet_id,
                p0.coords,
                "Escape -> 02:380:05",
                "Save",
                executable_now=True,
            ),
            PlannedAction(
                "P0",
                "ESCAPE_FLIGHT",
                p0.planet_id,
                p0.coords,
                "Escape -> 02:380:05",
                "Save",
                executable_now=True,
            ),
        ),
    ]
    for act1, act2 in actions:
        r1 = ex1.execute_action(c1, st1, act1)  # type: ignore[arg-type]
        r2 = ex2.execute_action(c2, st2, act2)
        assert _norm_result(r2) == _norm_result(r1)
        if r1.updated_state is not None:
            st1 = r1.updated_state
        if r2.updated_state is not None:
            st2 = r2.updated_state

    assert srv2.action_log == srv1.action_log
    assert _norm_fleets(srv2.launched_fleets) == _norm_fleets(srv1.launched_fleets)


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
                    assert not alias.name.startswith(forbidden_prefixes), (
                        f"Forbidden import {alias.name} in {path.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith(forbidden_prefixes), f"Forbidden import {node.module} in {path.name}"
    _ = typing.cast(object, None)
