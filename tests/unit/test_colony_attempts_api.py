"""Unit tests for the `colony_attempts` (V011) API wiring: payload keys, dispatch/failed/abandon ledger rows, kept/settled dedupe."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spaceinvasion.combat.fleet import GalaxySlot
from spaceinvasion.core.state import EnergyBalance, GameState, PlanetState, Resources
from spaceinvasion.planner.strategy import PlannedAction
from spaceinvasion.runtime.api_server import GameApiBridge
from spaceinvasion.session.executor import ExecutionResult


def _planet(
    pid: int,
    coords: str,
    *,
    fields_max: int = 320,
    fields_used: int = 20,
    temperature: int = 25,
    name: str = "",
    ships: dict[str, int] | None = None,
    buildings: dict[str, int] | None = None,
) -> PlanetState:
    return PlanetState(
        planet_id=pid,
        name=name or f"Colony {pid}",
        coords=coords,
        planet_type="normal",
        fields_used=fields_used,
        fields_max=fields_max,
        temperature=temperature,
        resources=Resources(50_000, 30_000, 40_000, 25_000),
        storage=Resources(500_000, 500_000, 500_000, 500_000),
        energy=EnergyBalance(1000, 500),
        buildings=dict(buildings if buildings is not None else {"b1": 10, "b2": 8}),
        ships=dict(ships if ships is not None else {"s8": 1, "s10": 5}),
    )


def _state(*planets: PlanetState) -> GameState:
    return GameState(31, 7, "cmdr", 1_700_000_000.0, tuple(planets), active_planet_id=planets[0].planet_id)


class TestColonyAttemptsApi(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tp = Path(self._tmp.name)
        self.cfg_path = self.tp / "cfg.yaml"
        self.cfg_path.write_text(
            "allow_colonize: true\nallow_galaxy_scan: true\nmin_colony_fields: 300\n",
            encoding="utf-8",
        )
        self.db_path = self.tp / "col.db"
        self.bridge = self._make_bridge()

    def _make_bridge(self) -> GameApiBridge:
        bridge = GameApiBridge(
            debug_port=19995,
            config_path=self.cfg_path,
            db_path=self.db_path,
            log_dir=None,
            ensure_session_fn=lambda: None,
        )
        self.addCleanup(bridge.close)
        assert bridge._db is not None
        return bridge

    def _exec(self, state: GameState, planned: PlannedAction, *, executed: bool, message: str, cmd: str = "colonize") -> dict:
        def _fake_exec(_client, _st, act, **_kw):
            return ExecutionResult(
                executed=executed,
                dry_run=False,
                action=act,
                message=message,
                updated_state=state if executed else None,
            )

        with patch.object(self.bridge._live_executor, "execute_action", side_effect=_fake_exec):
            return self.bridge._execute_and_summarize(None, state, cmd, planned, False)

    @staticmethod
    def _colonize_action(origin: PlanetState, target: str) -> PlannedAction:
        return PlannedAction(
            priority="P2",
            kind="COLONIZE",
            planet_id=origin.planet_id,
            planet_coords=origin.coords,
            target_id=8,
            target_level=1,
            target_name=f"Colonize -> {target}",
            reason="API colonize command",
            executable_now=True,
        )

    # ------------------------------------------------------------------
    def test_colony_candidates_payload_includes_attempt_keys(self) -> None:
        db = self.bridge._db
        assert db is not None
        self.bridge._last_state = _state(_planet(101, "02:380:12"))
        db.record_colony_attempt(target_coords="02:381:08", event_type="dispatched", origin_coords="02:380:12")
        db.record_colony_attempt(target_coords="02:381:08", event_type="deleted_undersized", fields_rolled=215)

        resp = self.bridge.handle_command({"command": "colony_candidates"})
        for key in ("colony_attempts", "colony_attempts_summary", "attempts_by_coords", "candidates"):
            self.assertIn(key, resp)
        self.assertEqual(len(resp["colony_attempts"]), 2)
        self.assertEqual(resp["colony_attempts"][0]["event_type"], "deleted_undersized")
        self.assertEqual(resp["colony_attempts_summary"]["deleted_undersized_count"], 1)
        self.assertIn("02:381:08", resp["attempts_by_coords"])
        self.assertEqual(resp["attempts_by_coords"], resp["colony_attempts_summary"]["by_coords"])
        self.assertEqual(resp["attempts_by_coords"]["02:381:08"]["last_fields_rolled"], 215)
        json.dumps(resp, allow_nan=False)

    def test_colony_candidates_payload_survives_ledger_errors(self) -> None:
        db = self.bridge._db
        assert db is not None
        self.bridge._last_state = _state(_planet(101, "02:380:12"))
        with (
            patch.object(db, "get_colony_attempts", side_effect=RuntimeError("no such table")),
            patch.object(db, "get_colony_attempts_summary", side_effect=RuntimeError("no such table")),
        ):
            resp = self.bridge.handle_command({"command": "colony_candidates"})
        self.assertEqual(resp["colony_attempts"], [])
        self.assertEqual(resp["colony_attempts_summary"]["total_events"], 0)
        self.assertEqual(resp["attempts_by_coords"], {})

    def test_colonize_success_records_dispatched_with_candidate_meta(self) -> None:
        db = self.bridge._db
        assert db is not None
        db.record_galaxy_slots(
            [
                GalaxySlot(
                    coords="02:381:08",
                    position=8,
                    status_color="free",
                    colonize_min_fields=250,
                    colonize_max_fields=380,
                    temperature=30,
                )
            ]
        )
        home = _planet(101, "02:380:12")
        st = _state(home)
        self.bridge._last_state = st
        self._exec(st, self._colonize_action(home, "02:381:08"), executed=True, message="Colony ship dispatched")

        rows = db.get_colony_attempts(limit=10)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["event_type"], "dispatched")
        self.assertEqual(row["target_coords"], "02:381:08")
        self.assertEqual(row["origin_coords"], "02:380:12")
        self.assertEqual(row["min_fields_target"], 300)
        self.assertEqual(row["expected_min_fields"], 250)
        self.assertEqual(row["expected_max_fields"], 380)
        self.assertEqual(row["temperature"], 30)
        self.assertTrue(row["grade"])
        self.assertIsNotNone(row["score"])
        self.assertEqual(row["details"].get("origin_planet_id"), 101)

    def test_colonize_failure_records_failed_deduped_and_skips_guard(self) -> None:
        db = self.bridge._db
        assert db is not None
        home = _planet(101, "02:380:12")
        st = _state(home)
        self.bridge._last_state = st
        act = self._colonize_action(home, "02:381:09")
        self._exec(st, act, executed=False, message="Aborted: no Colony ship available")
        self._exec(st, act, executed=False, message="Aborted: no Colony ship available")
        rows = db.get_colony_attempts(limit=10)
        self.assertEqual([r["event_type"] for r in rows], ["failed"])
        self.assertEqual(rows[0]["status_message"], "Aborted: no Colony ship available")
        self.assertEqual(db.get_colony_attempts_summary()["failed_count"], 1)

        self._exec(st, act, executed=False, message="Aborted: target occupied")
        self.assertEqual(len(db.get_colony_attempts(limit=10)), 2)

        self._exec(
            st,
            self._colonize_action(home, "02:381:10"),
            executed=False,
            message="Guarded: live COLONIZE requires allow_colonize=true in config.",
        )
        self.assertEqual(len(db.get_colony_attempts(limit=10)), 2)

    def test_abandon_colony_records_deleted_undersized(self) -> None:
        db = self.bridge._db
        assert db is not None
        home = _planet(101, "02:380:12")
        col = _planet(202, "02:381:08", fields_max=180, fields_used=0, temperature=-12, buildings={}, ships={})
        st = _state(home, col)
        self.bridge._last_state = st
        planned = PlannedAction(
            priority="P1",
            kind="ABANDON_COLONY",
            planet_id=202,
            planet_coords="02:381:08",
            target_level=300,
            target_name="Delete colony 02:381:08 (180/300 fields)",
            reason="Delete undersized colony",
            executable_now=True,
        )
        self._exec(st, planned, executed=True, message="Colony deleted", cmd="abandon_colony")
        rows = db.get_colony_attempts(limit=10)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["event_type"], "deleted_undersized")
        self.assertEqual(row["target_coords"], "02:381:08")
        self.assertEqual(row["planet_id"], 202)
        self.assertEqual(row["fields_rolled"], 180)
        self.assertEqual(row["min_fields_target"], 300)
        self.assertEqual(row["temperature"], -12)

    def test_new_colonies_record_kept_and_settled_once(self) -> None:
        db = self.bridge._db
        assert db is not None
        home = _planet(101, "02:380:12")
        self.bridge._record_state(_state(home))
        self.assertEqual(db.get_colony_attempts(limit=10), [])  # first snapshot never records

        big = _planet(202, "02:381:08", fields_max=342, fields_used=0)
        self.bridge._record_state(_state(home, big))
        self.bridge._record_state(_state(home, big))
        small = _planet(303, "02:382:04", fields_max=0, fields_used=0)
        self.bridge._record_state(_state(home, big, small))  # fields not parsed yet -> pending
        self.assertEqual([r["event_type"] for r in db.get_colony_attempts(limit=10)], ["kept"])
        small_parsed = _planet(303, "02:382:04", fields_max=164, fields_used=0, temperature=70)
        self.bridge._record_state(_state(home, big, small_parsed))
        self.bridge._record_state(_state(home, big, small_parsed))

        rows = db.get_colony_attempts(limit=10)
        self.assertEqual([r["event_type"] for r in rows], ["settled", "kept"])
        self.assertEqual(rows[0]["planet_id"], 303)
        self.assertEqual(rows[0]["fields_rolled"], 164)
        self.assertEqual(rows[1]["planet_id"], 202)
        self.assertEqual(rows[1]["fields_rolled"], 342)
        summary = db.get_colony_attempts_summary()
        self.assertEqual(summary["kept_count"], 1)
        self.assertEqual(summary["by_coords"]["02:382:04"]["kept_count"], 0)
        self.assertEqual(summary["by_coords"]["02:381:08"]["kept_count"], 1)

        # A restarted bridge hydrates the dedupe set and does not re-record on reappearance.
        bridge2 = self._make_bridge()
        self.assertIn((202, "02:381:08"), bridge2._colony_attempt_seen)
        self.assertIn((303, "02:382:04"), bridge2._colony_attempt_seen)
        bridge2._record_state(_state(home))
        bridge2._record_state(_state(home, big, small_parsed))
        self.assertEqual(len(bridge2._db.get_colony_attempts(limit=10)), 2)


if __name__ == "__main__":
    unittest.main()
