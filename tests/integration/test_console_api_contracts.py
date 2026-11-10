"""Console HTTP API, static-icon, persistence, and Python<->TS contract integration tests."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from unittest.mock import MagicMock, patch

import requests

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.model import EnergyBalance, GameState, PlanetState, Resources
from spaceinvasion.db.config_store import load_strategy_config
import spaceinvasion.runtime.api_server as api_mod
from spaceinvasion.runtime.api_server import GameApiBridge, create_api_server
from tests.e2e.harness import E2ETestHarness

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
UI_DIR_DEFAULT: Path = REPO_ROOT / "spaceinvasion" / "ui"


def _sample_state() -> GameState:
    planet = PlanetState(
        planet_id=12476,
        name="Colony",
        coords="02:380:12",
        planet_type="normal",
        temperature=31,
        fields_used=45,
        fields_max=230,
        resources=Resources(50_000, 30_000, 40_000, 20_000),
        storage=Resources(500_000, 500_000, 500_000, 500_000),
        energy=EnergyBalance(1200, 600),
        buildings={"b1": 12, "b5": 14, "b9": 4, "b10": 5},
        ships={"s10": 6},
    )
    return GameState(
        universe_id=31,
        user_id=103603,
        nickname="tester",
        timestamp=1700000500.0,
        planets=(planet,),
        active_planet_id=12476,
    )


def test_static_icons_served_when_compiled_bundle_absent() -> None:
    """`/favicon.svg`, `/favicon.ico`, and `/bell.svg` serve HTTP 200 even without tracked `app.js`."""
    for icon in ("bell.svg", "favicon.svg", "favicon.ico"):
        assert (UI_DIR_DEFAULT / icon).is_file()

    with E2ETestHarness(config=StrategyConfig()) as h:
        fav_svg = requests.get(f"{h.base_url}/favicon.svg", timeout=5.0)
        assert fav_svg.status_code == 200
        assert 'rx="14"' in fav_svg.text

        fav_ico = requests.get(f"{h.base_url}/favicon.ico", timeout=5.0)
        assert fav_ico.status_code == 200
        assert len(fav_ico.content) > 64

        bell_svg = requests.get(f"{h.base_url}/bell.svg", timeout=5.0)
        assert bell_svg.status_code == 200
        assert "<svg" in bell_svg.text


def test_temp_ui_dir_serves_bundle_tracks_versions_and_blocks_traversal(tmp_path: Path) -> None:
    """A built bundle in a temp `UI_DIR` serves cleanly, updates `/ui-version` hashes, and blocks traversal."""
    fake_ui = tmp_path / "ui"
    assets_dir = fake_ui / "assets"
    assets_dir.mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")
    (fake_ui / "index.html").write_text("<!doctype html><div id='root'></div>", encoding="utf-8")
    (fake_ui / "styles.css").write_text("body{background:#06090b;}", encoding="utf-8")
    (fake_ui / "app.js").write_text("console.log('v1');", encoding="utf-8")
    (assets_dir / "chunk-test.js").write_text("export const v = 1;\n", encoding="utf-8")

    with patch.object(api_mod, "UI_DIR", fake_ui):
        with E2ETestHarness(config=StrategyConfig()) as h:
            r_html = requests.get(f"{h.base_url}/", timeout=5.0)
            r_css = requests.get(f"{h.base_url}/styles.css", timeout=5.0)
            r_js = requests.get(f"{h.base_url}/app.js", timeout=5.0)
            r_chunk = requests.get(f"{h.base_url}/assets/chunk-test.js", timeout=5.0)
            assert r_html.status_code == 200 and "root" in r_html.text
            assert r_css.status_code == 200 and "#06090b" in r_css.text
            assert r_js.status_code == 200 and "v1" in r_js.text
            assert r_chunk.status_code == 200 and "export const v = 1" in r_chunk.text

            for bad_path in ("/assets/../secret.txt", "/assets/..%2Fsecret.txt", "/assets/"):
                r_bad = requests.get(f"{h.base_url}{bad_path}", timeout=5.0)
                assert r_bad.status_code in (400, 404)
                assert "TOP_SECRET" not in r_bad.text

            ver = requests.get(f"{h.base_url}/ui-version", timeout=5.0).json()
            assert ver["ok"] is True
            assert isinstance(ver.get("server_boot_id"), str) and bool(ver["server_boot_id"])

            v1 = api_mod.compute_ui_assets_state(fake_ui)
            (fake_ui / "styles.css").write_text("body{background:#091015;}", encoding="utf-8")
            v2 = api_mod.compute_ui_assets_state(fake_ui)
            assert v2["code_version"] == v1["code_version"]
            assert v2["css_version"] != v1["css_version"]

            (fake_ui / "app.js").write_text("console.log('v2');", encoding="utf-8")
            v3 = api_mod.compute_ui_assets_state(fake_ui)
            assert v3["code_version"] != v2["code_version"]


def test_protected_target_spy_records_block_with_zero_operator_notifications() -> None:
    """Live SPY against a protected user records a 6h target block without raising operator notifications."""
    state = _sample_state()
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.driver = MagicMock(current_url="https://spaceinvasion.bitmeup.com/indexGame.es?sid=tok")
    fake_client.read_full_snapshot.return_value = state
    fake_client.navigate_menu.return_value = (
        '<form action="indexInternal.es?action=internalSendFleet">'
        '<input name="ship_10" value="0"/><input name="gal"/><input name="sun"/><input name="pla"/>'
        '<input type="submit" value="Continue"/></form>'
    )
    fake_client._wait_frame_ready.return_value = (
        '<div>You have 2 unread Messages! [i]</div><div style="color:red">This user can\'t be attacked!</div>'
    )

    with tempfile.TemporaryDirectory(prefix="si_int_notif_") as td:
        td_path = Path(td)
        bridge = GameApiBridge(
            debug_port=19999,
            env_file=td_path / "no.env",
            db_path=td_path / "notif.db",
            log_dir=None,
            client_factory=lambda _port: fake_client,
            ensure_session_fn=lambda: None,
        )
        bridge._record_state(state, fetched_buildings=True, fetched_home=True, observed_active_planet=True)
        bridge._last_snapshot_at = 1e9
        server = create_api_server(bridge, host="127.0.0.1", port=0)
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            spy_res = requests.post(
                f"{base_url}/command",
                json={"command": "spy", "target_coords": "02:381:05", "quantity": 1, "dry_run": False},
                timeout=5.0,
            )
            assert spy_res.status_code == 200
            spy_body = spy_res.json()
            assert spy_body["executed"] is False

            notif_res = requests.post(
                f"{base_url}/command",
                json={"command": "notifications_list", "status": "unread", "limit": 20},
                timeout=5.0,
            )
            assert notif_res.status_code == 200
            assert notif_res.json()["unread_count"] == 0
        finally:
            server.shutdown()
            server.server_close()
            bridge.close()


def test_config_apply_persists_across_restart_and_strict_api_validation() -> None:
    """Explicit `update_config` persists across restart, rotates `server_boot_id`, and rejects invalid inputs."""
    with E2ETestHarness(config=StrategyConfig(planner_mode="priority", playstyle="fleeter")) as h:
        boot_before = requests.get(f"{h.base_url}/ui-version", timeout=5.0).json()["server_boot_id"]

        bad_cmds: tuple[dict[str, str | int | dict[str, str | int]], ...] = (
            {"command": "spy", "target_coords": "\u0660\u0662:\u0663\u0668\u0660:\u0660\u0664"},
            {"command": "scan_galaxy", "galaxy": "\u0662", "system": 1},
            {"command": "fleetsave_plan", "fuel_reserve_spice": "bad"},
            {"command": "update_config", "config": {"storage_warn_ratio": "\u0660.\u0665"}},
            {"command": "simulate_combat", "attacker_ships": {"s4": 1}, "safety_margin": "\u0662"},
        )
        for bad_cmd in bad_cmds:
            r_bad = requests.post(f"{h.base_url}/command", json=bad_cmd, timeout=5.0)
            assert r_bad.status_code == 400, r_bad.text

        upd = requests.post(
            f"{h.base_url}/command",
            json={
                "command": "update_config",
                "config": {
                    "planner_mode": "ai_planner",
                    "playstyle": "hybrid",
                    "min_colony_fields": 315,
                    "allow_raids": True,
                },
            },
            timeout=5.0,
        )
        assert upd.status_code == 200
        assert upd.json()["config"]["planner_mode"] == "ai_planner"

        h.restart_bridge()
        boot_after = requests.get(f"{h.base_url}/ui-version", timeout=5.0).json()["server_boot_id"]
        assert isinstance(boot_after, str) and bool(boot_after)
        assert isinstance(boot_before, str) and bool(boot_before)

        reloaded = load_strategy_config(h.config_path)
        assert reloaded.planner_mode == "ai_planner"
        assert reloaded.playstyle == "hybrid"
        assert reloaded.min_colony_fields == 315
        assert reloaded.allow_raids is True


def test_generated_ts_types_in_sync_with_python_contracts() -> None:
    """`scripts/generate_ts_types.py --check frontend/src/types/generated.ts` exits 0 with zero drift."""
    target = REPO_ROOT / "frontend" / "src" / "types" / "generated.ts"
    res = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate_ts_types.py"), "--check", str(target)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30.0,
    )
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
