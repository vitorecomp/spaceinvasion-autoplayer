"""Deterministic multi-galaxy, multi-colony Wave B workload for `GameDatabase` vs ORM parity."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
import time

from sqlalchemy.orm import Session

from spaceinvasion.combat.ranking import estimate_account_points, estimate_hourly_economy
from spaceinvasion.db.repos import (
    GalaxyRepository,
    IntelRepository,
    MessagesRepository,
    RaidsRepository,
    RankSnapshotInput,
    SettingsRepository,
    SnapshotsRepository,
    StateSnapshotInput,
    TelemetryRepository,
)
from spaceinvasion.db.repos._hot_refresh import refresh_colony_candidates_hot, refresh_galaxy_sectors_hot
from spaceinvasion.db.session import DatabaseEngine, db_uow
from spaceinvasion.db.storage import GameDatabase
from tests.unit.db._workload_b_fixtures import (
    T0,
    build_game_state,
    galaxy_slot_specs,
    inbox_message_batches,
    spy_inputs,
    to_combat_slots,
    to_legacy_messages,
    to_legacy_spy,
    to_planet_inputs,
)

WAVE_B_TABLES: tuple[str, ...] = tuple(
    (
        "raid_history battle_reports bash_blocks game_messages recycle_reports "
        "state_snapshots planet_snapshots rank_snapshots latest_state_hot empire_planets_hot "
        "spy_targets spy_reports galaxy_scans galaxy_slots galaxy_scan_history "
        "galaxy_slot_history galaxy_sector_hot "
        "colony_candidates_hot economy_rates_cache loot_recycle_hourly meta tutorial_progress"
    ).split()
)


@contextmanager
def _frozen_time(ts: float) -> Iterator[None]:
    orig = time.time
    time.time = lambda: ts
    try:
        yield
    finally:
        time.time = orig


def run_legacy_workload_b(dsn: str) -> None:
    """Execute the deterministic Wave B multi-galaxy workload via legacy `GameDatabase`."""
    state = build_game_state()
    plan = [{"kind": "BUILD", "planet_id": 101, "target_id": 1}]
    g2, g3 = galaxy_slot_specs()
    rep_inact, rep_col = spy_inputs()
    batch_1, batch_2 = inbox_message_batches()
    msgs_1, msgs_2 = to_legacy_messages(batch_1), to_legacy_messages(batch_2)
    gdb = GameDatabase(backend="postgres", postgres_url=dsn)
    try:
        gdb.record_full_state_snapshot(state, plan=plan, source="commander")
        gdb.record_state_snapshot(state)
        gdb.record_galaxy_slots(to_combat_slots(g2), ts=T0)
        gdb.record_galaxy_scan(2, 380, to_combat_slots(g2), ts=T0 + 2.0)
        gdb.record_galaxy_slots(to_combat_slots(g3), ts=T0 + 5.0)
        gdb.record_galaxy_scan(3, 120, to_combat_slots(g3), ts=T0 + 7.0)
        gdb.record_spy_report(
            to_legacy_spy(rep_inact), points=42000, is_inactive=True, status_color="yellow", ts=T0 + 10.0
        )
        gdb.record_spy_report(to_legacy_spy(rep_col), ts=T0 + 12.0)
        with _frozen_time(T0 + 15.0):
            gdb.set_target_debris("02:380:05", False)
        gdb.record_game_messages(msgs_1, ts=T0 + 20.0)
        gdb.record_game_messages(msgs_2, ts=T0 + 22.0)
        gdb.mark_game_messages_deleted(["msg-gen-1"], ts=T0 + 25.0)
        gdb.record_raid("02:380:04", attacked_ts=T0 + 30.0, outcome="win", loot={"pig_iron": 10000, "metal": 5000})
        gdb.record_raid("02:380:04", attacked_ts=T0 + 40.0, outcome="win", loot={"pig_iron": 8000, "spice": 500})
        gdb.record_raid("02:380:06", attacked_ts=T0 + 45.0, is_exempt=True, outcome="win", loot={"metal": 6000})
        gdb.record_target_block(
            "02:380:04", blocked_until_ts=T0 + 86400.0, reason="Bash limit reached", is_bash=True, blocked_ts=T0 + 50.0
        )
        gdb.record_target_block(
            "03:120:09", blocked_until_ts=T0 + 120.0, reason="Cooldown", is_bash=False, blocked_ts=T0 + 55.0
        )
        gdb.record_raid("03:120:09", attacked_ts=T0 + 200.0, outcome="loss")
    finally:
        gdb.close()


def _run_orm_snapshots(session: Session) -> None:
    state = build_game_state()
    plan = [{"kind": "BUILD", "planet_id": 101, "target_id": 1}]
    est = estimate_account_points(state)
    econ = estimate_hourly_economy(state)
    planet_inputs = to_planet_inputs(state)
    state_dict = asdict(state)
    latest_meta = dict(state_dict)
    latest_meta.pop("raid_stats", None)
    latest_meta.pop("raid_targets", None)

    snaps = SnapshotsRepository(session)
    snaps.record_full_state_snapshot(
        StateSnapshotInput(
            state_payload=state_dict,
            plan_payload=plan,
            source="commander",
            universe_id=state.universe_id,
            user_id=state.user_id,
            nickname=state.nickname,
            active_planet_id=state.active_planet_id or 0,
            planet_count=len(state.planets),
            ts=T0,
        )
    )
    snaps.record_rank_snapshot(
        RankSnapshotInput(
            universe_id=state.universe_id,
            nickname=state.nickname,
            server_points=state.account_points,
            server_rank=state.account_rank,
            estimated_points=float(est["estimated_points"]),
            pts_per_hr=float(econ["pts_per_hr_steady"]),
            spice_value=float(est["spice_value"]),
            planets=len(state.planets),
            ts=T0,
        )
    )
    snaps.record_planet_snapshots(planet_inputs, researches=state.researches, universe_id=state.universe_id, ts=T0)
    assert state.tutorial is not None
    TelemetryRepository(session).record_tutorial_progress(
        step=state.tutorial.step,
        total_steps=state.tutorial.total_steps,
        tasks=[
            {
                "description": t.description,
                "category": t.category,
                "current": t.current,
                "target": t.target,
                "completed": t.completed,
            }
            for t in state.tutorial.tasks
        ],
        reward_text=state.tutorial.reward_text,
        can_complete=state.tutorial.can_complete,
        ts=T0,
    )
    own_coords = [p.coords for p in state.planets]
    IntelRepository(session).mark_own_planets(own_coords)
    touched = snaps.sync_empire_planets_hot(planet_inputs, universe_id=state.universe_id, ts=T0)
    refresh_colony_candidates_hot(session, own_coords, ts=T0)
    if touched:
        refresh_galaxy_sectors_hot(session, touched, ts=T0)
    SettingsRepository(session).upsert_meta_json("latest_game_state", latest_meta, ts=T0)


def _run_orm_world_and_combat(session: Session) -> None:
    g2, g3 = galaxy_slot_specs()
    rep_inact, rep_col = spy_inputs()
    gal = GalaxyRepository(session)
    intel = IntelRepository(session)
    msgs = MessagesRepository(session)
    raids = RaidsRepository(session)

    gal.record_galaxy_slots(g2, ts=T0)
    gal.record_system_scan(2, 380, g2, ts=T0 + 2.0)
    gal.record_galaxy_slots(g3, ts=T0 + 5.0)
    gal.record_system_scan(3, 120, g3, ts=T0 + 7.0)

    intel.record_spy_report(rep_inact, points=42000, is_inactive=True, status_color="yellow", ts=T0 + 10.0)
    intel.record_spy_report(rep_col, ts=T0 + 12.0)
    intel.set_target_debris("02:380:05", has_debris=False, ts=T0 + 15.0)

    batch_1, batch_2 = inbox_message_batches()
    msgs.ingest_messages(batch_1, ts=T0 + 20.0)
    msgs.ingest_messages(batch_2, ts=T0 + 22.0)
    msgs.mark_deleted_in_game(["msg-gen-1"], ts=T0 + 25.0)

    raids.record_raid("02:380:04", attacked_ts=T0 + 30.0, outcome="win", loot={"pig_iron": 10000, "metal": 5000})
    raids.record_raid("02:380:04", attacked_ts=T0 + 40.0, outcome="win", loot={"pig_iron": 8000, "spice": 500})
    raids.record_raid("02:380:06", attacked_ts=T0 + 45.0, is_exempt=True, outcome="win", loot={"metal": 6000})
    raids.record_target_block(
        "02:380:04", blocked_until_ts=T0 + 86400.0, reason="Bash limit reached", is_bash=True, blocked_ts=T0 + 50.0
    )
    raids.record_target_block(
        "03:120:09", blocked_until_ts=T0 + 120.0, reason="Cooldown", is_bash=False, blocked_ts=T0 + 55.0
    )
    raids.record_raid("03:120:09", attacked_ts=T0 + 200.0, outcome="loss")


def run_orm_workload_b(dsn: str) -> None:
    """Execute the exact same Wave B multi-galaxy workload via the 5 Wave B ORM repositories."""
    with DatabaseEngine.from_dsn(dsn, pool_size=2, max_overflow=0) as db:
        with db_uow(db) as session:
            _run_orm_snapshots(session)
            _run_orm_world_and_combat(session)


__all__ = ["WAVE_B_TABLES", "run_legacy_workload_b", "run_orm_workload_b"]
