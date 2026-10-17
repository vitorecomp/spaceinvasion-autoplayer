"""Parity, instant fake-clock limiter, and Section 4 architecture tests for planner/graph and session/limiter."""

from __future__ import annotations

import ast
from dataclasses import replace
import math
from pathlib import Path
import sys
import threading

import pytest

from spaceinvasion.core.model import PlannedAction, QueueJob, Resources
from spaceinvasion.planner.ai import run_ai_planner
from spaceinvasion.planner.graph import (
    EDGE_TYPE_PRIORITY,
    build_action_graph,
    build_ai_planner_graph,
    build_fleet_planner_graph,
    freeze_sig_tree,
    merge_strategy_config_overrides,
)
from spaceinvasion.planner.heuristics import plan_actions
from spaceinvasion.runtime.api_server import _freeze_sig_tree as legacy_freeze_sig_tree
from spaceinvasion.session import client as legacy_client
from spaceinvasion.session.limiter import (
    PageViewLimiter,
    RateLimitError,
    get_default_page_view_limiter,
    is_server_rate_limit_html,
    reset_default_page_view_limiter,
)
from tests.goldens.scenarios import AI_PLANNER_PARAMS, CONFIGS, STATES, ai_target_config

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GRAPH_DIR = _REPO_ROOT / "spaceinvasion" / "planner" / "graph"
_LIMITER_PATH = _REPO_ROOT / "spaceinvasion" / "session" / "limiter.py"


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        self.sleeps.append(dt)
        self.now += dt


@pytest.mark.parametrize("state_name", sorted(STATES.keys()))
@pytest.mark.parametrize("cfg_name", sorted(CONFIGS.keys()))
def test_build_action_graph_golden_parity(state_name: str, cfg_name: str) -> None:
    state = STATES[state_name]()
    cfg = CONFIGS[cfg_name]()
    actions = plan_actions(state, config=cfg)

    actual = build_action_graph(state, actions, effective_config=cfg, active_config=cfg)
    assert "nodes" in actual and "edges" in actual
    assert len(actual["nodes"]) == len(actions)


def test_build_action_graph_simulated_overrides_and_planet_filter_parity() -> None:
    state = STATES["midgame_three_colonies"]()
    base_cfg = CONFIGS["default"]()
    overrides: dict[str, object] = {
        "allow_raids": True,
        "allow_spying": True,
        "storage_warn_ratio": 0.72,
        "shipyard_targets": {"small_transporter": 12, "spy_probe": 15},
        "fleet_research_targets": {"r3": 6},
    }
    eff_new, ch_new = merge_strategy_config_overrides(base_cfg, overrides, planner_mode_override="ai_planner")
    assert eff_new.planner_mode == "ai_planner"
    assert eff_new.allow_raids is True
    assert ch_new

    actions = plan_actions(state, config=eff_new)
    pid = state.planets[0].planet_id
    actual = build_action_graph(
        state,
        actions,
        effective_config=eff_new,
        active_config=base_cfg,
        simulated=True,
        planet_filter=pid,
    )
    assert actual["is_dirty"] is True
    assert actual["changed_keys"] == ch_new


def test_build_action_graph_synthetic_all_edge_types_parity() -> None:
    state = STATES["lategame_five_colonies"]()
    p0 = state.planets[0]
    pid = p0.planet_id
    cfg = CONFIGS["full_auto"]()
    synthetic_actions = [
        PlannedAction("P0", "ALERT_HOSTILE", pid, p0.coords, "Incoming raid", "Hostile inbound", executable_now=False),
        PlannedAction("P0", "ESCAPE_FLIGHT", pid, p0.coords, "Escape flight", "Save fleet", executable_now=True),
        PlannedAction(
            "P1",
            "THROTTLE_BUILDING",
            pid,
            p0.coords,
            "Blast furnace",
            "Throttle pig iron deficit",
            target_id=2,
            executable_now=True,
        ),
        PlannedAction(
            "P1",
            "BUILD",
            pid,
            p0.coords,
            "Solar power plant",
            "Energy deficit",
            target_id=5,
            target_level=p0.building_level(5) + 1,
            executable_now=True,
        ),
        PlannedAction(
            "P1",
            "BUILD",
            pid,
            p0.coords,
            "Pig iron warehouse",
            "Storage cap",
            target_id=11,
            target_level=p0.building_level(11) + 1,
            executable_now=True,
        ),
        PlannedAction(
            "P1",
            "BUILD",
            pid,
            p0.coords,
            "Pig iron mine",
            "Mine up",
            target_id=1,
            target_level=p0.building_level(1) + 1,
            cost=Resources(5000, 2000, 500, 0),
            executable_now=True,
        ),
        PlannedAction(
            "P1",
            "BUILD",
            pid,
            p0.coords,
            "Pig iron mine",
            "Mine next",
            target_id=1,
            target_level=p0.building_level(1) + 2,
            cost=Resources(9000, 4000, 1000, 0),
            missing=Resources(2000, 0, 0, 0),
            executable_now=False,
        ),
        PlannedAction(
            "P2",
            "BUILD",
            pid,
            p0.coords,
            "Blast furnace",
            "Smelt",
            target_id=2,
            target_level=p0.building_level(2) + 1,
            cost=Resources(8000, 3000, 1000, 0),
            missing=Resources(3000, 0, 0, 0),
            executable_now=False,
        ),
        PlannedAction(
            "P2",
            "BUILD_SHIP",
            pid,
            p0.coords,
            "Spy probe (x3)",
            "Build probes",
            target_id=10,
            cost=Resources(0, 3000, 3000, 0),
            executable_now=True,
        ),
        PlannedAction("P2", "SCAN_GALAXY", pid, p0.coords, "Scan 01:020", "Scout 01:020", executable_now=True),
        PlannedAction("P2", "SPY", pid, p0.coords, "Spy 01:020:05", "Intel 01:020:05", executable_now=True),
        PlannedAction("P2", "ATTACK", pid, p0.coords, "Raid 01:020:05", "Loot raid 01:020:05", executable_now=True),
        PlannedAction(
            "P3",
            "RESEARCH",
            0,
            "",
            "Mining technology",
            "Research r1",
            target_id=1,
            target_level=state.researches.get("r1", 0) + 1,
            cost=Resources(10000, 5000, 5000, 1000),
            missing=Resources(1000, 0, 0, 0),
            executable_now=False,
        ),
    ]
    actual = build_action_graph(state, synthetic_actions, effective_config=cfg, active_config=cfg)
    assert len(actual["nodes"]) == len(synthetic_actions)
    assert actual["edges"]
    assert "ga_step_order" not in EDGE_TYPE_PRIORITY


def test_build_ai_planner_graph_real_and_synthetic_parity() -> None:
    state = STATES["midgame_three_colonies"]()
    target_cfg = ai_target_config(state)
    plan_res = run_ai_planner(state, target_cfg, params=AI_PLANNER_PARAMS)
    g_full = build_ai_planner_graph(state, plan_res, window_size=30)
    assert g_full["nodes"]
    p0 = state.planets[0]
    g_w1 = build_ai_planner_graph(state, plan_res, window_size=25, week_filter=1, planet_filter=p0.planet_id)
    assert isinstance(g_w1["nodes"], list)

    synthetic_plan: dict[str, object] = {
        "steps": [
            {
                "seq": 1,
                "kind": "BUILD",
                "week": 1,
                "planet_id": p0.planet_id,
                "entity_key": "b5",
                "target_level": p0.building_level(5) + 1,
                "start_sec": 0,
                "duration_sec": 300,
                "cost": {"pig_iron": 200, "metal": 100, "kryptonite": 50, "spice": 0},
            },
            {
                "seq": 2,
                "kind": "DEMOLISH",
                "week": 1,
                "planet_id": p0.planet_id,
                "entity_key": "b1",
                "target_level": max(0, p0.building_level(1) - 1),
                "start_sec": 300,
                "duration_sec": 120,
                "cost": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
            },
            {
                "seq": 3,
                "kind": "BUILD",
                "week": 1,
                "planet_id": p0.planet_id,
                "entity_key": "b2",
                "target_level": p0.building_level(2) + 1,
                "start_sec": 420,
                "duration_sec": 600,
                "cost": {"pig_iron": 900000, "metal": 400000, "kryptonite": 200000, "spice": 0},
                "needs_transport": True,
                "transport_request_at_sec": 300,
                "transport_sources": [{"from": p0.coords, "pig_iron": 5000}],
            },
            {
                "seq": 4,
                "kind": "RESEARCH",
                "week": 1,
                "entity_key": "r1",
                "target_level": state.researches.get("r1", 0) + 1,
                "start_sec": 100,
                "duration_sec": 900,
                "cost": {"pig_iron": 1000, "metal": 1000, "kryptonite": 500, "spice": 100},
            },
            {
                "seq": 5,
                "kind": "COLONIZE_PREVIEW",
                "week": 2,
                "planet_slot": 4,
                "planet_name": "New Frontier",
                "start_sec": 700000,
                "duration_sec": 3600,
                "cost": {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0},
            },
        ]
    }
    for wk in (None, 1, 2):
        for pf in (None, "global", p0.coords, p0.name):
            g_syn = build_ai_planner_graph(state, synthetic_plan, window_size=20, week_filter=wk, planet_filter=pf)
            assert isinstance(g_syn["nodes"], list) and isinstance(g_syn["edges"], list)


def test_build_fleet_planner_graph_golden_and_saturated_slots_parity() -> None:
    cfg = CONFIGS["full_auto"]()
    for name in ("midgame_three_colonies", "lategame_five_colonies"):
        st = STATES[name]()
        actions = plan_actions(st, config=cfg)
        actual = build_fleet_planner_graph(
            st, actions, cfg, attack_telemetry={"raids_24h": 4}, spare_budget={"spice": 1200}
        )
        assert "nodes" in actual and "edges" in actual

    base = STATES["midgame_three_colonies"]()
    p0 = base.planets[0]
    max_slots = 1 + p0.building_level(10)
    inflight_jobs = tuple(
        QueueJob(
            category="fleet",
            name=f"Attack 01:030:0{i + 1}",
            remaining_seconds=float(300 * (i + 1)),
            remaining_text=f"{5 * (i + 1)}m",
            detail=f"Outbound raid to 01:030:0{i + 1}",
            origin_coords=p0.coords,
        )
        for i in range(max_slots)
    )
    saturated = replace(base, active_queues=(*base.active_queues, *inflight_jobs))
    tactical_actions = [
        PlannedAction("P0", "ALERT_HOSTILE", p0.planet_id, p0.coords, "Hostile alert", "Inbound", executable_now=False),
        PlannedAction(
            "P0", "ESCAPE_FLIGHT", p0.planet_id, p0.coords, "Escape flight", "Save ships", executable_now=True
        ),
        PlannedAction(
            "P1", "RESEARCH", 0, "", "Rocket drive", "Fleet research", target_id=3, target_level=5, executable_now=True
        ),
        PlannedAction(
            "P1",
            "BUILD_SHIP",
            p0.planet_id,
            p0.coords,
            "Spy probe (x5)",
            "Build probes",
            target_id=10,
            executable_now=True,
        ),
        PlannedAction("P2", "SCAN_GALAXY", p0.planet_id, p0.coords, "Scan 01:030", "Scan 01:030", executable_now=True),
        PlannedAction("P2", "SPY", p0.planet_id, p0.coords, "Spy 01:030:09", "Spy 01:030:09", executable_now=True),
        PlannedAction(
            "P2", "ATTACK", p0.planet_id, p0.coords, "Attack 01:030:09", "Raid 01:030:09", executable_now=False
        ),
    ]
    sat_graph = build_fleet_planner_graph(saturated, tactical_actions, cfg)
    assert sat_graph["nodes"] and sat_graph["edges"]


def test_freeze_sig_tree_parity() -> None:
    cyclic: dict[str, object] = {"a": [1, True, False, 0, float("nan"), float("inf"), float("-inf"), {3, 1, 2}]}
    cyclic["self"] = cyclic
    deep: object = "leaf"
    for _ in range(36):
        deep = [deep]
    sample: dict[object, object] = {"cyclic": cyclic, "deep": deep, 1: "int_key", "1": "str_key", "none": None}
    assert freeze_sig_tree(sample) == legacy_freeze_sig_tree(sample)
    assert hash(freeze_sig_tree(sample)) == hash(legacy_freeze_sig_tree(sample))


def test_page_view_limiter_fake_clock_and_recovery_protocol() -> None:
    clk = _FakeClock(start=500.0)
    lim = PageViewLimiter(
        min_delay_range=(1.0, 1.0),
        max_views_per_minute=6,
        reserved_interactive_views=2,
        default_cooldown_seconds=30.0,
        window_seconds=60.0,
        clock=clk,
        sleeper=clk.sleep,
        rng=lambda lo, _hi: lo,
    )
    assert lim.acquire(priority="normal") == 0.0
    assert lim.acquire(priority="normal") == 1.0
    assert lim.acquire(priority="normal") == 1.0
    assert lim.acquire(priority="normal") == 1.0
    assert lim.available_budget("normal") == 0
    assert lim.available_budget("interactive") == 2

    with pytest.raises(RateLimitError) as exc_info:
        lim.acquire(priority="normal", max_wait=5.0)
    assert exc_info.value.server_throttle is False

    assert lim.acquire(priority="interactive") == 1.0
    assert lim.views_last_60s() == 5

    clk.now += 65.0
    assert lim.views_last_60s() == 0
    assert lim.available_budget("normal") == 4

    gen0 = lim.recovery_generation()
    ok, rem = lim.record_rate_limit_if_generation(gen0, retry_after=20.0)
    assert ok is True and math.isclose(rem, 20.0)
    ok_dup, _ = lim.record_rate_limit_if_generation(gen0, retry_after=20.0)
    assert ok_dup is False
    with pytest.raises(RateLimitError) as srv_exc:
        lim.acquire(priority="interactive")
    assert srv_exc.value.server_throttle is True
    assert srv_exc.value.recorded_limiter_id == id(lim)

    legacy_lim = legacy_client.PageViewLimiter(max_views_per_minute=6, reserved_interactive_views=2)
    assert set(lim.stats().keys()) == set(legacy_lim.stats().keys())

    clk.now += 25.0
    role, token = lim.begin_or_wait_throttle_recovery(wait_timeout=5.0)
    assert role == "leader"
    follower_result: list[str] = []
    started = threading.Event()

    def _follower() -> None:
        started.set()
        follower_result.append(lim.wait_if_throttle_recovery_in_flight(wait_timeout=5.0))

    th = threading.Thread(target=_follower, daemon=True)
    th.start()
    started.wait(timeout=2.0)
    lim.finish_throttle_recovery(token, recovered=True)
    th.join(timeout=2.0)
    assert follower_result in (["waited"], ["idle"])
    assert lim.begin_or_wait_throttle_recovery(wait_timeout=1.0)[0] == "none"

    reset_default_page_view_limiter()
    assert get_default_page_view_limiter().views_last_60s() == 0


def test_is_server_rate_limit_html_parity() -> None:
    cases = (
        "",
        "<html><body>Normal Buildings Page</body></html>",
        '<div id="modal">You have made more than <span id="pageview-amount"></span> page views in the last minute.</div>',
        '<div id="modal">You have made more than <span id="pageview-amount">0</span> page views!</div>',
        '<div id="modal">You have made more than <span id="pageview-amount">60</span> page views in 1 minute!</div>',
        "You have made more than 45 page views recently.",
    )
    for html in cases:
        assert is_server_rate_limit_html(html) == legacy_client.is_server_rate_limit_html(html)


def test_section4_ast_loc_and_layering_constraints() -> None:
    graph_files = sorted(_GRAPH_DIR.glob("*.py"))
    assert len(graph_files) >= 5
    all_prod = [*graph_files, _LIMITER_PATH]

    forbidden_graph_prefixes = (
        "spaceinvasion.db",
        "spaceinvasion.session",
        "spaceinvasion.runtime",
        "spaceinvasion.ui",
        "spaceinvasion.combat.fleet",
        "spaceinvasion.combat.ranking",
        "spaceinvasion.planner.optimizer",
        "spaceinvasion.planner.action_graph",
    )

    for path in all_prod:
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
                if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                    pytest.fail(f"Broad except Exception in {path.name}")
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert not (node.value.id == "os" and node.attr == "environ"), f"os.environ in {path.name}"

        if path.parent == _GRAPH_DIR:
            assert "ga_nn" not in src, f"Legacy ga_nn reference found in {path.name}"
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith(forbidden_graph_prefixes), (
                            f"Forbidden import {alias.name} in {path.name}"
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith(forbidden_graph_prefixes), (
                        f"Forbidden import {node.module} in {path.name}"
                    )
        else:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".", 1)[0]
                        assert top in sys.stdlib_module_names, f"Non-stdlib import {alias.name} in limiter.py"
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".", 1)[0]
                    assert top in sys.stdlib_module_names or node.module.startswith("spaceinvasion.core"), (
                        f"Forbidden import {node.module} in limiter.py"
                    )
