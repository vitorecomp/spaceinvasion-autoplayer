"""Contract, parity, CLI, DSN, config_store, and standards tests for Phase 3b Step 6 cutover."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.db.config_store import load_strategy_config, save_strategy_config
from spaceinvasion.db.dsn import (
    DEFAULT_DB_BACKEND,
    DEFAULT_PG_PORT,
    build_default_postgres_url,
    get_unix_socket_host,
    is_container_env,
    is_postgres_dsn,
    is_unix_socket_dsn,
    normalize_backend_name,
    redact_db_url,
    resolve_database_config,
)
from spaceinvasion.db.migrations.cli import main as migrations_cli_main
from spaceinvasion.db.storage import GameDatabase as NotebookShimGameDatabase
from spaceinvasion.parsing.models import GalaxySlot, InboxMessage, Resources, SpyReport
from spaceinvasion.runtime.bridge._db_gateway import RuntimeDatabaseGateway
from spaceinvasion.runtime.postgres_ctl import (
    DEFAULT_COMPOSE_FILENAME,
    DEFAULT_PODMAN_CONTAINER_NAME,
    check_postgres_tcp_open,
    is_container_runtime,
)
from tests.pg_harness import DisposablePostgresServer
from tests.unit.db._workload_b_fixtures import T0, build_game_state

_REPO_ROOT: Path = Path(__file__).resolve().parents[3]

_REQUIRED_GATEWAY_ATTRS: tuple[str, ...] = tuple(
    (
        "backend backend_name postgres_url display_target storage change_token close summary verify_schema "
        "load_meta_json save_meta_json load_runtime_state save_runtime_state "
        "load_strategy_config_snapshot save_strategy_config_snapshot "
        "get_custom_planet_names set_custom_planet_name set_custom_planet_names "
        "get_galaxy_sweep_state save_galaxy_sweep_state get_transmitter_status "
        "note_daily_login_bonus_seen record_transmitter_state "
        "get_notifications get_notification unread_notification_count "
        "create_or_bump_notification acknowledge_notification acknowledge_all_notifications "
        "get_manual_queue enqueue_manual_command update_manual_queue_item "
        "cancel_manual_queue_item clear_manual_queue complete_manual_queue_item complete_matching_manual_command "
        "record_state_snapshot record_full_state_snapshot load_latest_game_state "
        "get_rank_samples get_all_economy_rates save_economy_rates "
        "get_hourly_production_history record_hourly_production get_loot_recycle_hourly "
        "get_last_throttle_timestamps record_action "
        "get_recent_transport_dispatches get_recent_transport_actions_fallback record_transport_dispatch "
        "get_raid_stats load_bash_tracker record_raid record_target_block "
        "get_battle_reports get_recycle_reports get_game_messages record_game_messages mark_game_messages_deleted "
        "get_spy_targets get_spy_target_by_coords get_spy_reports record_spy_report "
        "set_target_debris get_active_debris_targets get_galaxy_debris_amounts "
        "get_galaxy_scans get_galaxy_slots record_galaxy_scan record_galaxy_slots get_galaxy_sector_intel "
        "get_colony_candidates discard_colony_candidate get_discarded_colony_coords "
        "record_colony_attempt get_colony_attempts get_colony_attempts_summary "
        "get_universe_research_max refresh_universe_research_max "
        "get_active_config_optimizer list_config_optimizer_runs record_config_optimizer_run "
        "get_config_optimizer_specializations update_config_optimizer_specializations "
        "get_reserved_building_floors update_reserved_building_floor "
        "get_active_ai_planner list_ai_planner_runs record_ai_planner_run "
        "list_field_conflict_prompts upsert_field_conflict_prompt resolve_field_conflict_prompt"
    ).split()
)


def test_dsn_helpers_notebook_shim_and_postgres_ctl_pure_contracts() -> None:
    """Pure DSN parsing, redaction, notebook shim, and container helpers obey contracts."""
    assert len(_REQUIRED_GATEWAY_ATTRS) == 92
    gateway_members = frozenset(dir(RuntimeDatabaseGateway))
    for attr_name in _REQUIRED_GATEWAY_ATTRS:
        assert attr_name in gateway_members, f"Missing gateway attr: {attr_name}"

    assert DEFAULT_DB_BACKEND == "postgres"
    assert DEFAULT_PG_PORT == 5433
    assert DEFAULT_COMPOSE_FILENAME == "docker-compose.yml"
    assert DEFAULT_PODMAN_CONTAINER_NAME == "spaceinvasion_postgres"
    assert is_postgres_dsn("postgresql://user:secret@127.0.0.1:5433/spaceinvasion")
    assert not is_postgres_dsn("sqlite:///tmp/test.db")
    assert normalize_backend_name("pg") == "postgres"
    with pytest.raises(ValueError, match="SQLite backend has been removed"):
        normalize_backend_name("sqlite")

    sock_dsn = "postgresql:///si_test?host=/tmp/pgsock&user=postgres"
    assert is_unix_socket_dsn(sock_dsn)
    assert get_unix_socket_host(sock_dsn) == "/tmp/pgsock"
    assert redact_db_url("postgresql://alice:s3cr3t@127.0.0.1:5433/db") == "postgresql://alice:***@127.0.0.1:5433/db"
    assert is_container_env({"SPACEINVASION_CONTAINER": "1"}) is True
    assert is_container_runtime({"SPACEINVASION_CONTAINER": "0"}) is False
    assert check_postgres_tcp_open("127.0.0.1", 1, timeout=0.1) is False

    url = build_default_postgres_url({"SPACEINVASION_PG_HOST": "pg.local", "SPACEINVASION_PG_PORT": "5439"})
    assert url.startswith("postgresql://") and "@pg.local:5439/" in url
    resolved = resolve_database_config(explicit_db_url=sock_dsn, env={})
    assert resolved.backend == "postgres" and resolved.postgres_url == sock_dsn

    shim = NotebookShimGameDatabase("state/ignored.db", backup_on_upgrade=False)
    shim.close()


def _verify_meta_and_ops(gw: RuntimeDatabaseGateway) -> None:
    assert gw.backend == "postgres" and gw.backend_name == "postgres" and gw.storage is gw
    tok0 = gw.change_token()
    gw.save_meta_json("scalar_key", [1, 2, 3], ts=T0)
    assert gw.load_meta_json("scalar_key") == [1, 2, 3]
    assert gw.change_token() > tok0

    gw.save_runtime_state({"mode": "loop", "ticks": 4}, ts=T0)
    assert gw.load_runtime_state() == {"mode": "loop", "ticks": 4}
    gw.save_strategy_config_snapshot({"planner_mode": "ai_planner"}, ts=T0)
    assert gw.load_strategy_config_snapshot() == {"planner_mode": "ai_planner"}

    assert gw.set_custom_planet_name(101, "Alpha  Prime", ts=T0) == {"101": "Alpha Prime"}
    assert gw.set_custom_planet_names({102: "Beta"}, ts=T0) == {"101": "Alpha Prime", "102": "Beta"}
    assert gw.get_custom_planet_names() == {"101": "Alpha Prime", "102": "Beta"}

    sweep = gw.save_galaxy_sweep_state({"cursor_galaxy": 3, "cursor_system": 45, "next_sweep_due_ts": T0 + 3600}, ts=T0)
    assert sweep["cursor_galaxy"] == 3 and sweep["due"] is False

    gw.note_daily_login_bonus_seen(present=True, ts=T0)
    tx = gw.record_transmitter_state(activated=True, free_spins_remaining=0, reward="5000 Spice", ts=T0)
    assert tx["due"] is False and tx["total_activations"] == 1

    notif = gw.create_or_bump_notification(title="Alert", message="Check fleet", dedupe_key="k1", ts=T0)
    assert gw.unread_notification_count() == 1
    assert gw.get_notification(int(str(notif["id"]))) is not None
    assert gw.acknowledge_all_notifications(status="read", ts=T0 + 5.0) == 1

    q1 = gw.enqueue_manual_command(kind="BUILD", planet_id=101, target_id=1, target_level=20, note="mine", ts=T0)
    qid = int(str(q1["id"]))
    upd = gw.update_manual_queue_item(qid, note="mine-v2", ts=T0 + 1.0)
    assert upd is not None and upd["target_level"] == 20 and upd["note"] == "mine-v2"
    assert (
        gw.complete_matching_manual_command(kind="BUILD", planet_id=101, target_id=1, executed_level=19, ts=T0 + 2.0)
        == qid
    )
    assert len(gw.get_manual_queue(status="pending")) == 1
    assert (
        gw.complete_matching_manual_command(kind="BUILD", planet_id=101, target_id=1, executed_level=20, ts=T0 + 3.0)
        == qid
    )
    assert gw.get_manual_queue(status="pending") == []

    assert gw.get_active_config_optimizer() is None
    floors = gw.update_reserved_building_floor("101", "1", 18, reason="pin", ts=T0)
    assert floors == {"101": {"b1": 18}} and gw.get_reserved_building_floors() == {"101": {"b1": 18}}
    assert gw.get_active_config_optimizer() is None
    specs = gw.update_config_optimizer_specializations({"101": "spice_mine"}, ts=T0)
    assert specs == {"101": "spice"} and gw.get_config_optimizer_specializations() == {"101": "spice"}
    cfg_id = gw.record_config_optimizer_run({"summary": {"ships_per_month": 50.0}}, trigger_source="test", ts=T0)
    assert gw.get_active_config_optimizer() is not None and len(gw.list_config_optimizer_runs(limit=5)) == 1
    gw.record_ai_planner_run({"summary": {"discounted_reward_spice_eq": 1234.0}}, config_run_id=cfg_id, ts=T0)
    assert gw.get_active_ai_planner() is not None and len(gw.list_ai_planner_runs(limit=5)) == 1

    gw.upsert_field_conflict_prompt(
        {"conflict_key": "101:b1:20", "planet_id": 101, "blocked_building_key": "b1"}, ts=T0
    )
    assert len(gw.list_field_conflict_prompts(status="pending")) == 1
    assert gw.resolve_field_conflict_prompt("101:b1:20", "keep", ts=T0 + 5.0) is not None


def _verify_state_and_telemetry(gw: RuntimeDatabaseGateway) -> None:
    state = build_game_state()
    gw.record_full_state_snapshot(state, plan=[{"kind": "BUILD"}], source="commander")
    ranks = gw.record_state_snapshot(state)
    assert len(ranks) == 1 and set(ranks[0]) == {
        "ts",
        "server_points",
        "server_rank",
        "estimated_points",
        "pts_per_hr",
        "spice_value",
        "planets",
    }
    loaded = gw.load_latest_game_state()
    assert loaded is not None and loaded.nickname == state.nickname

    gw.save_economy_rates(101, "02:380:12", {"b1": 100, "b2": 90}, ts=T0)
    assert gw.get_all_economy_rates()[101]["b2"] == 90
    gw.record_action(
        source="commander",
        command="throttle",
        action={"kind": "THROTTLE_BUILDING", "planet_id": 101, "target_id": 2, "planet_coords": "02:380:12"},
        executed=True,
        dry_run=False,
        message="Set 90%",
        ts=T0 + 10.0,
    )
    assert gw.get_last_throttle_timestamps(building_id=2)[101] == T0 + 10.0
    gw.record_action(
        source="commander",
        command="transport",
        action={"kind": "TRANSPORT", "planet_id": 101, "planet_coords": "02:380:12", "target_name": "03:120:07"},
        executed=True,
        dry_run=False,
        message="Sent P:5000 M:2000 to 03:120:07",
        ts=T0 + 12.0,
    )
    fb = gw.get_recent_transport_actions_fallback(now_ts=T0 + 60.0)
    assert len(fb) == 1 and fb[0]["dest_coords"] == "03:120:07"
    ring = gw.record_transport_dispatch(
        origin_coords="2:380:12", dest_coords="3:120:7", cargo={"pig_iron": 5000}, ships={"s2": 1}, ts=T0 + 15.0
    )
    assert len(ring) == 1 and len(gw.get_recent_transport_dispatches(now_ts=T0 + 20.0)) == 1


def _verify_combat_intel_and_colonies(gw: RuntimeDatabaseGateway) -> None:
    slots = [
        GalaxySlot(
            coords="02:380:04",
            position=4,
            name="TargetA",
            owner="InactiveA",
            status_color="yellow",
            has_debris=True,
            debris_pig_iron=4000,
            debris_metal=2000,
        ),
        GalaxySlot(
            coords="02:380:08",
            position=8,
            status_color="free",
            colonize_min_fields=280,
            colonize_max_fields=340,
            temperature=20,
        ),
    ]
    gw.record_galaxy_slots(slots, ts=T0 + 20.0)
    gw.record_galaxy_scan(2, 380, slots, ts=T0 + 20.5)
    assert "02:380" in gw.get_galaxy_scans() and len(gw.get_galaxy_slots(galaxy=2, system=380)) == 2
    assert gw.get_galaxy_debris_amounts()["02:380:04"]["total"] == 6000
    debris = gw.get_active_debris_targets(origin_coords=("02:380:12",), limit=10)
    assert len(debris) == 1 and debris[0]["debris_total"] == 6000

    gw.record_spy_report(
        SpyReport(
            target_coords="2:380:4",
            owner="InactiveA",
            planet_name="TargetA",
            resources=Resources(pig_iron=9000, metal=4000, kryptonite=1000, spice=500),
            researches={"r1": 11},
        ),
        points=12000,
        is_inactive=True,
        status_color="yellow",
        ts=T0 + 25.0,
    )
    assert gw.get_spy_target_by_coords("2:380:4") is not None
    assert len(gw.get_spy_reports(coords="02:380:04")) == 1
    res_max = gw.refresh_universe_research_max(own_researches={"r1": 8}, own_nick="Commander", ts=T0 + 26.0)
    r1_row = next(r for r in res_max if r["key"] == "r1")
    assert r1_row["universe_max_level"] == 11 and r1_row["gap_to_max"] == 3
    assert len(gw.get_universe_research_max(own_researches={"r1": 8})) == len(res_max)

    msg = InboxMessage(
        msg_uid="m-bat-1",
        category="battle_report",
        coords="02:380:04",
        subject="Battle",
        body_text="Battle report",
        parsed_data={"defender": "InactiveA", "loot": {"pig_iron": 4000, "metal": 1500}},
    )
    uids, new_msgs = gw.record_game_messages([msg], ts=T0 + 30.0)
    assert uids == ["m-bat-1"] and len(new_msgs) == 1 and new_msgs[0].msg_uid == "m-bat-1"
    assert len(gw.get_battle_reports(coords="02:380:04")) == 1
    assert gw.mark_game_messages_deleted(["m-bat-1"], ts=T0 + 35.0) == 1

    gw.record_raid("02:380:04", attacked_ts=T0 + 40.0, outcome="win", loot={"pig_iron": 4000})
    gw.record_target_block("02:380:04", blocked_until_ts=T0 + 5000.0, reason="Bash", is_bash=True, ts=T0 + 45.0)
    tracker = gw.load_bash_tracker(now=T0 + 60.0)
    assert tracker.attacks_last_24h("02:380:04", timestamp=T0 + 60.0) == 1
    assert tracker.blocked_until("02:380:04", timestamp=T0 + 60.0) == T0 + 5000.0
    rstats = gw.get_raid_stats(now=T0 + 60.0)
    assert isinstance(rstats["overall"], dict) and rstats["overall"]["attempts"] == 1
    assert isinstance(rstats["telemetry_summary"], dict) and "rank_snapshots" in rstats["telemetry_summary"]

    intel = gw.get_galaxy_sector_intel(galaxies=[2], now_ts=T0 + 60.0)
    assert isinstance(intel["sectors"], dict) and "02:380" in intel["sectors"]

    cands = gw.get_colony_candidates(origin_coords="02:380:12", min_fields_target=300)
    assert len(cands) == 1 and cands[0]["coords"] == "02:380:08"
    disc = gw.discard_colony_candidate("02:380:08", reason="test", ts=T0 + 70.0)
    assert disc["discarded"] is True and "02:380:08" in gw.get_discarded_colony_coords()
    gw.record_colony_attempt(
        target_coords="02:380:08", event_type="dispatched", origin_coords="02:380:12", ts=T0 + 75.0
    )
    assert len(gw.get_colony_attempts(limit=10)) == 1
    assert gw.get_colony_attempts_summary()["dispatched_count"] == 1


def test_runtime_db_gateway_config_store_and_migrations_cli_end_to_end(
    pg_harness: DisposablePostgresServer,
) -> None:
    """Exercise all `RuntimeDatabaseGateway` families, `config_store`, and `migrations/cli.py` on PG16."""
    dsn = pg_harness.create_database(prefix="gw_e2e", from_template=False)

    # CLI migrate + status + verify + history on a fresh disposable PostgreSQL database.
    assert migrations_cli_main(["--db-url", dsn, "migrate", "--dry-run"], env={}) == 0
    assert migrations_cli_main(["--db-url", dsn, "migrate", "--all"], env={}) == 0
    assert migrations_cli_main(["--db-url", dsn, "status"], env={}) == 0
    assert migrations_cli_main(["--db-url", dsn, "verify", "--full-integrity"], env={}) == 0
    assert migrations_cli_main(["--db-url", dsn, "history", "--limit", "10"], env={}) == 0

    # `config_store` round-trip via ORM `DatabaseStore`.
    cfg = StrategyConfig(planner_mode="priority")
    save_strategy_config(cfg, dsn)
    loaded_cfg = load_strategy_config(dsn)
    assert loaded_cfg.planner_mode == "priority"

    gw = RuntimeDatabaseGateway.from_dsn(dsn, pool_size=2, max_overflow=0, ensure_ready=True)
    try:
        _verify_meta_and_ops(gw)
        _verify_state_and_telemetry(gw)
        _verify_combat_intel_and_colonies(gw)
        summ = gw.summary()
        ver = gw.verify_schema(full_integrity=True)
        assert ver["ok"] is True and ver["alembic_revision"] == "0001"
        assert isinstance(summ["counts"], dict) and len(summ["counts"]) == 39 and "meta" not in summ["counts"]
        recent = summ["recent_actions"]
        assert isinstance(recent, list) and len(recent) >= 2
        assert set(recent[0]) == {
            "id",
            "ts",
            "source",
            "command",
            "priority",
            "kind",
            "planet_coords",
            "target_name",
            "target_level",
            "executed",
            "dry_run",
            "message",
        }
        assert type(recent[0]["executed"]) is int and type(recent[0]["dry_run"]) is int
    finally:
        gw.close()


def test_runtime_gateway_and_postgres_ctl_obey_section_4_standards() -> None:
    """All `_db_gateway*.py` and `postgres_ctl.py` modules obey LOC, zero-SQLAlchemy, and typing rules."""
    files = [
        _REPO_ROOT / "spaceinvasion" / "runtime" / "postgres_ctl.py",
        *sorted((_REPO_ROOT / "spaceinvasion" / "runtime" / "bridge").glob("_db_gateway*.py")),
    ]
    assert len(files) >= 6
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 400, f"{path.name} exceeds 400 LOC ({len(lines)})"
        tree = ast.parse("\n".join(lines), filename=str(path))
        assert ast.get_docstring(tree), f"{path.name} missing docstring"
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = node.module if isinstance(node, ast.ImportFrom) else ""
                names = [a.name for a in node.names]
                for target in ([mod] if mod else []) + names:
                    if target:
                        assert target.split(".")[0] not in {"sqlalchemy", "alembic", "psycopg"}, (
                            f"{path.name} must not import DB driver `{target}`"
                        )
            elif isinstance(node, ast.Name) and node.id == "Any":
                raise AssertionError(f"{path.name} must not use `Any`")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"print", "getattr", "hasattr", "setattr"}, (
                    f"{path.name} must not call `{node.func.id}`"
                )
            elif isinstance(node, ast.ExceptHandler):
                exc = node.type
                ename = exc.id if isinstance(exc, ast.Name) else ""
                assert exc is not None and ename not in {"Exception", "BaseException"}, f"{path.name} broad except"
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                span = (node.end_lineno or node.lineno) - node.lineno + 1
                limit = 400 if isinstance(node, ast.ClassDef) else 100
                assert span <= limit, f"{path.name}:{node.name} exceeds {limit} LOC ({span})"
