"""Tests for Phase 4 CommandRegistry, strict validation, and lock-free SnapshotPublisher."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
import time

import pytest

from spaceinvasion.runtime.api import (
    ApiRequestError,
    CommandRegistry,
    FieldBounds,
    generate_command_reference_markdown,
)
from spaceinvasion.runtime.bridge import (
    PublishedBridgeSnapshot,
    SnapshotPublisher,
    build_dashboard_delta_response,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class QueueAddParams:
    planet_id: int = field(metadata={"bounds": FieldBounds(min_val=1, max_val=999_999)})
    building_id: int = field(metadata={"bounds": FieldBounds(min_val=1, max_val=21)})
    priority: str = field(
        default="P1",
        metadata={"bounds": FieldBounds(choices=("P0", "P1", "P2", "P3"), allow_empty=False)},
    )
    ratio: float = field(default=1.0, metadata={"bounds": FieldBounds(min_val=0.0, max_val=10.0)})
    dry_run: bool = False
    note: str | None = field(default=None, metadata={"bounds": FieldBounds(max_len=64)})


@dataclass(frozen=True)
class BrowserActionParams:
    planet_id: int = field(metadata={"bounds": FieldBounds(min_val=1)})


class _FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []


def _build_registry() -> CommandRegistry[_FakeBridge]:
    reg: CommandRegistry[_FakeBridge] = CommandRegistry()

    @reg.command(
        "queue_add",
        needs_browser=False,
        mutates=True,
        summary="Pin a manual command to the priority queue.",
        params=QueueAddParams,
    )
    def handle_queue_add(bridge: _FakeBridge, params: QueueAddParams) -> dict[str, object]:
        bridge.calls.append(("queue_add", params))
        return {
            "planet_id": params.planet_id,
            "building_id": params.building_id,
            "priority": params.priority,
            "ratio": params.ratio,
            "dry_run": params.dry_run,
            "note": params.note,
        }

    @reg.command(
        "build_live",
        needs_browser=True,
        mutates=True,
        summary="Dispatch a live building click through Chrome.",
        params=BrowserActionParams,
    )
    def handle_build_live(bridge: _FakeBridge, params: BrowserActionParams) -> dict[str, object]:
        bridge.calls.append(("build_live", params))
        return {"clicked_planet_id": params.planet_id}

    return reg


def test_registry_validates_params_and_rejects_bad_inputs_before_locking() -> None:
    reg = _build_registry()
    bridge = _FakeBridge()
    lock_acquired = False

    class _RecordingLock:
        def __enter__(self) -> None:
            nonlocal lock_acquired
            lock_acquired = True

        def __exit__(self, *args: object) -> None:
            return None

    # Unknown command rejected before locking
    with pytest.raises(ApiRequestError) as exc_info:
        reg.dispatch(bridge, {"command": "does_not_exist"}, browser_lock=_RecordingLock())
    assert exc_info.value.status_code == 400
    assert exc_info.value.error_code == "unknown_command"
    assert exc_info.value.to_error_payload()["ok"] is False
    assert lock_acquired is False

    # Unicode digits, booleans, NaN, Infinity, underscores, out-of-range, bad choices
    bad_payloads: list[dict[str, object]] = [
        {"command": "queue_add", "planet_id": "١٢٣", "building_id": 1},
        {"command": "queue_add", "planet_id": "５", "building_id": 1},
        {"command": "queue_add", "planet_id": True, "building_id": 1},
        {"command": "queue_add", "planet_id": 10, "building_id": 22},
        {"command": "queue_add", "planet_id": 10, "building_id": 1, "ratio": float("nan")},
        {"command": "queue_add", "planet_id": 10, "building_id": 1, "ratio": float("inf")},
        {"command": "queue_add", "planet_id": 10, "building_id": 1, "ratio": "1_0.5"},
        {"command": "queue_add", "planet_id": 10, "building_id": 1, "priority": "P9"},
        {"command": "queue_add", "planet_id": 10, "building_id": 1, "dry_run": "true"},
        {"command": "queue_add", "planet_id": 10},  # missing required building_id
        {"command": "build_live", "planet_id": "١٠"},  # fails validation before browser_lock!
    ]
    for bad in bad_payloads:
        with pytest.raises(ApiRequestError) as err:
            reg.dispatch(bridge, bad, browser_lock=_RecordingLock())
        assert err.value.status_code == 400
        assert lock_acquired is False

    # Valid non-browser command succeeds without touching browser_lock
    resp = reg.dispatch(
        bridge,
        {"command": "QUEUE_ADD", "planet_id": "101", "building_id": 3, "ratio": "2.5", "note": "  go  "},
        browser_lock=_RecordingLock(),
    )
    assert resp == {
        "ok": True,
        "command": "queue_add",
        "planet_id": 101,
        "building_id": 3,
        "priority": "P1",
        "ratio": 2.5,
        "dry_run": False,
        "note": "go",
    }
    assert lock_acquired is False

    # Valid browser command DOES acquire browser_lock
    live_resp = reg.dispatch(
        bridge,
        {"command": "build_live", "planet_id": 101},
        browser_lock=_RecordingLock(),
    )
    assert live_resp == {"ok": True, "command": "build_live", "clicked_planet_id": 101}
    assert lock_acquired is True


def test_non_browser_command_and_snapshot_reads_bypass_contended_browser_lock() -> None:
    reg = _build_registry()
    bridge = _FakeBridge()
    publisher = SnapshotPublisher()
    publisher.publish(
        health={"ok": True, "in_game": True, "debug_port": 9222},
        bot_control={"paused": False, "mode_override": "auto", "captcha_blocked": False},
        state={"universe_id": 31, "planets": [{"planet_id": 101, "coords": "02:380:08"}]},
        sections={"plan": [{"kind": "BUILD", "target": "b1"}]},
        published_at=1_760_000_000.0,
    )

    browser_lock = threading.Lock()
    lock_held = threading.Event()
    release_lock = threading.Event()

    def _hold_browser_lock() -> None:
        with browser_lock:
            lock_held.set()
            release_lock.wait(timeout=2.0)

    holder = threading.Thread(target=_hold_browser_lock, daemon=True)
    holder.start()
    assert lock_held.wait(timeout=1.0)

    try:
        # 1. Lock-free /health, /bot_status, and /dashboard delta read complete in < 5ms
        t0 = time.perf_counter()
        health = publisher.health_payload()
        bot_status = publisher.bot_status_payload()
        snap = publisher.current()
        assert snap is not None
        delta = build_dashboard_delta_response(snap)
        elapsed_read_ms = (time.perf_counter() - t0) * 1000.0

        assert health["ok"] is True
        assert bot_status["paused"] is False
        assert delta["ok"] is True
        assert elapsed_read_ms < 5.0, f"Lock-free snapshot reads took {elapsed_read_ms:.2f} ms"

        # 2. needs_browser=False command also completes in < 5ms while browser_lock is held
        t1 = time.perf_counter()
        resp = reg.dispatch(
            bridge,
            {"command": "queue_add", "planet_id": 101, "building_id": 2},
            browser_lock=browser_lock,
        )
        elapsed_cmd_ms = (time.perf_counter() - t1) * 1000.0
        assert resp["ok"] is True
        assert elapsed_cmd_ms < 5.0, f"Non-browser command took {elapsed_cmd_ms:.2f} ms"
    finally:
        release_lock.set()
        holder.join(timeout=2.0)


def _make_eleven_colony_snapshot(publisher: SnapshotPublisher) -> PublishedBridgeSnapshot:
    planets = [
        {
            "planet_id": 100 + idx,
            "name": f"Colony-{idx + 1}",
            "coords": f"02:38{idx // 2}:{(idx % 12) + 1:02d}",
            "planet_type": "normal",
            "temperature": -30 + idx * 7,
            "fields_used": 180 + idx,
            "fields_max": 310 + idx,
            "resources": {"pig_iron": 350_000 + idx * 1000, "metal": 220_000, "kryptonite": 160_000, "spice": 90_000},
            "storage": {"pig_iron": 640_000, "metal": 640_000, "kryptonite": 320_000, "spice": 320_000},
            "buildings": {f"b{b}": 10 + (b % 15) for b in range(1, 22)},
            "ships": {f"s{s}": 25 * s for s in (1, 2, 4, 6, 8, 10, 11, 13, 15)},
        }
        for idx in range(11)
    ]
    plan_items = [
        {
            "priority": "P2",
            "kind": "BUILD",
            "planet_id": 100 + (i % 11),
            "target_id": (i % 17) + 1,
            "target_name": f"Building-{i}",
            "reason": f"Compound payback upgrade #{i} across 11-colony empire",
            "cost": {"pig_iron": 100_000, "metal": 50_000, "kryptonite": 40_000, "spice": 10_000},
        }
        for i in range(60)
    ]
    prod_series = [
        {
            "minute_bucket": 29_000_000 + i * 60,
            "pig_iron": 400_000 + i * 500,
            "metal": 210_000 + i * 250,
            "kryptonite": 195_000 + i * 200,
            "spice": 120_000 + i * 150,
            "spice_eq": 350_000 + i * 400,
        }
        for i in range(168)
    ]
    loot_series = [
        {"hour_bucket": 485_000 + i, "raid_loot": 180_000 + i * 1000, "recycle_loot": 60_000 + i * 500}
        for i in range(168)
    ]
    return publisher.publish(
        health={"ok": True, "in_game": True, "debug_port": 9222, "remaining_hourly_actions": 55},
        bot_control={"paused": False, "mode_override": "auto", "captcha_blocked": False},
        state={"universe_id": 31, "user_id": 103603, "active_planet_id": 100, "planets": planets},
        sections={
            "plan": plan_items,
            "manual_queue": [{"id": i, "command": "build", "planet_id": 100} for i in range(10)],
            "ranking": {"current": {"server_rank": 42, "server_points": 125_000}},
            "config": {"planner_mode": "ai_planner", "max_planets": 11, "allow_raids": True},
            "notifications": {"unread_count": 0, "items": []},
        },
        hourly_production_history=prod_series,
        loot_recycle_hourly=loot_series,
        published_at=1_760_000_100.0,
    )


def test_dashboard_delta_omits_unchanged_sections_and_stays_under_5kb() -> None:
    publisher = SnapshotPublisher()
    snap = _make_eleven_colony_snapshot(publisher)

    full = build_dashboard_delta_response(snap)
    full_bytes = len(json.dumps(full, separators=(",", ":")).encode("utf-8"))
    assert full["delta"] is False
    assert full["hourly_production_mode"] == "full"
    assert full["loot_recycle_mode"] == "full"
    assert len(full["hourly_production_history"]) == 168  # type: ignore[arg-type]
    assert len(full["loot_recycle_hourly"]) == 168  # type: ignore[arg-type]
    assert full_bytes > 40_000

    # Unchanged poll sends all client section versions + latest series cursors
    unchanged = build_dashboard_delta_response(
        snap,
        client_section_versions=snap.section_versions,
        since_minute_bucket=snap.latest_minute_bucket,
        since_loot_hour=snap.latest_loot_hour,
    )
    unchanged_bytes = len(json.dumps(unchanged, separators=(",", ":")).encode("utf-8"))
    assert unchanged["delta"] is True
    assert unchanged["hourly_production_history"] == []
    assert unchanged["loot_recycle_hourly"] == []
    unchanged_sections = unchanged["unchanged_sections"]
    assert isinstance(unchanged_sections, list)
    assert set(unchanged_sections) == set(snap.section_versions.keys())
    assert "state" not in unchanged
    assert "plan" not in unchanged
    assert unchanged_bytes < 5_000, f"Unchanged delta payload too large: {unchanged_bytes} bytes"

    # Updating only one section sends ONLY that section while keeping all others omitted
    snap2 = publisher.publish_section("notifications", {"unread_count": 1, "items": [{"id": 99}]})
    partial = build_dashboard_delta_response(
        snap2,
        client_section_versions=snap.section_versions,
        since_minute_bucket=snap.latest_minute_bucket,
        since_loot_hour=snap.latest_loot_hour,
    )
    assert "notifications" in partial
    assert "state" not in partial
    assert "plan" not in partial
    assert "notifications" not in partial["unchanged_sections"]  # type: ignore[operator]

    # Incremental time-series cursor returns only newer buckets
    assert snap.latest_minute_bucket is not None
    assert snap.latest_loot_hour is not None
    inc = build_dashboard_delta_response(
        snap,
        client_section_versions=snap.section_versions,
        since_minute_bucket=snap.latest_minute_bucket - 120,
        since_loot_hour=snap.latest_loot_hour - 2,
    )
    assert len(inc["hourly_production_history"]) == 2  # type: ignore[arg-type]
    assert len(inc["loot_recycle_hourly"]) == 2  # type: ignore[arg-type]


def test_dashboard_delta_versions_bot_control_with_8000_scanned_systems_under_5kb() -> None:
    """Omitting unchanged bot_control (8,000 scanned systems) keeps delta < 5 KB and reflects paused/busy updates."""
    publisher = SnapshotPublisher()
    scanned_systems = {f"{(i // 400) + 1}:{(i % 400) + 1}": 1_760_000_000.0 + i for i in range(8_000)}
    snap = publisher.publish(
        health={"ok": True, "in_game": True, "debug_port": 9222, "bot_control": {"paused": False}},
        bot_control={
            "ok": True,
            "paused": False,
            "busy": False,
            "mode_override": "auto",
            "captcha_blocked": False,
            "scanned_systems": scanned_systems,
        },
        state={"universe_id": 31, "user_id": 103603, "active_planet_id": 100, "planets": []},
        sections={"plan": [], "notifications": {"unread_count": 0, "items": []}},
        published_at=1_760_008_000.0,
    )
    assert "bot_control" in snap.section_versions

    initial = build_dashboard_delta_response(snap)
    initial_bytes = len(json.dumps(initial, separators=(",", ":")).encode("utf-8"))
    assert initial_bytes > 100_000
    assert len(initial["bot_control"]["scanned_systems"]) == 8_000  # type: ignore[index]

    unchanged = build_dashboard_delta_response(snap, client_section_versions=snap.section_versions)
    unchanged_bytes = len(json.dumps(unchanged, separators=(",", ":")).encode("utf-8"))
    assert unchanged_bytes < 5_000, f"Unchanged delta with 8,000 scanned_systems too large: {unchanged_bytes} bytes"
    assert "bot_control" not in unchanged
    assert "bot_control" in unchanged["unchanged_sections"]  # type: ignore[operator]

    snap_paused = publisher.publish_section(
        "bot_control",
        {
            "ok": True,
            "paused": True,
            "busy": True,
            "mode_override": "dry-run",
            "captcha_blocked": False,
            "scanned_systems": scanned_systems,
        },
    )
    assert snap_paused.section_versions["bot_control"] != snap.section_versions["bot_control"]
    changed = build_dashboard_delta_response(snap_paused, client_section_versions=snap.section_versions)
    assert "bot_control" in changed
    assert "bot_control" not in changed["unchanged_sections"]  # type: ignore[operator]
    assert changed["bot_control"]["paused"] is True  # type: ignore[index]
    assert changed["bot_control"]["busy"] is True  # type: ignore[index]
    assert changed["bot_control"]["mode_override"] == "dry-run"  # type: ignore[index]
    assert len(changed["bot_control"]["scanned_systems"]) == 8_000  # type: ignore[index]


def test_command_reference_markdown_is_deterministic() -> None:
    reg = _build_registry()
    md1 = generate_command_reference_markdown(reg)
    md2 = generate_command_reference_markdown(reg)
    assert md1 == md2
    assert "| `build_live` | Yes | Yes | `BrowserActionParams` |" in md1
    assert "| `queue_add` | No | Yes | `QueueAddParams` |" in md1
    assert "choices=P0/P1/P2/P3" in md1
    assert "## `queue_add`" in md1


def test_phase4_runtime_modules_obey_layering_and_section4_standards() -> None:
    dirs = (
        REPO_ROOT / "spaceinvasion" / "runtime" / "api",
        REPO_ROOT / "spaceinvasion" / "runtime" / "bridge",
    )
    for folder in dirs:
        for path in sorted(folder.glob("*.py")):
            src = path.read_text(encoding="utf-8")
            assert len(src.splitlines()) <= 400, f"{path} exceeds 400 LOC"
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("spaceinvasion.ui"), f"{path} imports ui/"
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.end_lineno:
                    assert (node.end_lineno - node.lineno + 1) <= 100, f"{path}:{node.name} >100 LOC"
                if isinstance(node, ast.ClassDef) and node.end_lineno:
                    assert (node.end_lineno - node.lineno + 1) <= 400, f"{path}:{node.name} >400 LOC"
                if isinstance(node, ast.ExceptHandler) and node.type is not None:
                    if isinstance(node.type, ast.Name):
                        assert node.type.id != "Exception", f"Broad except Exception in {path}"
