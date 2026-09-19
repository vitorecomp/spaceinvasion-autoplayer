"""Unit tests for the Smart 2-Phase Galaxy Scanner (`config`, `storage`, and `fleet`)."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from spaceinvasion.combat.fleet import (
    GalaxySlot,
    SpyReport,
    galaxy_sector_distance,
    order_sectors_for_smooth_walk,
    score_galaxy_scan_sectors,
    select_smart_galaxy_scan_batch,
)
from spaceinvasion.core.config import (
    StrategyConfig,
    load_strategy_config,
    save_strategy_config,
    strategy_config_from_dict,
)
from spaceinvasion.core.state import EnergyBalance, GameState, PlanetState, Resources
from spaceinvasion.db.storage import GameDatabase


class TestSmartGalaxyScanConfig(unittest.TestCase):
    def test_defaults_clamping_and_yaml_sqlite_roundtrip(self) -> None:
        defaults = StrategyConfig()
        self.assertTrue(defaults.galaxy_scan_explore_first)
        self.assertAlmostEqual(defaults.galaxy_scan_near_weight, 2.0)
        self.assertAlmostEqual(defaults.galaxy_scan_attack_weight, 3.0)

        low_cfg = strategy_config_from_dict(
            {
                "galaxy_scan_explore_first": "false",
                "galaxy_scan_near_weight": -5.0,
                "galaxy_scan_attack_weight": -0.1,
            }
        )
        self.assertFalse(low_cfg.galaxy_scan_explore_first)
        self.assertAlmostEqual(low_cfg.galaxy_scan_near_weight, 0.0)
        self.assertAlmostEqual(low_cfg.galaxy_scan_attack_weight, 0.0)

        high_cfg = strategy_config_from_dict(
            {
                "galaxy_scan_explore_first": 1,
                "galaxy_scan_near_weight": 99.5,
                "galaxy_scan_attack_weight": 42.0,
            }
        )
        self.assertTrue(high_cfg.galaxy_scan_explore_first)
        self.assertAlmostEqual(high_cfg.galaxy_scan_near_weight, 20.0)
        self.assertAlmostEqual(high_cfg.galaxy_scan_attack_weight, 20.0)

        nan_cfg = strategy_config_from_dict(
            {
                "galaxy_scan_explore_first": float("nan"),
                "galaxy_scan_near_weight": float("nan"),
                "galaxy_scan_attack_weight": float("inf"),
            }
        )
        self.assertTrue(nan_cfg.galaxy_scan_explore_first)
        self.assertAlmostEqual(nan_cfg.galaxy_scan_near_weight, 2.0)
        self.assertAlmostEqual(nan_cfg.galaxy_scan_attack_weight, 3.0)

        with tempfile.TemporaryDirectory() as tmp:
            tp = Path(tmp)
            custom = StrategyConfig(
                galaxy_scan_explore_first=False,
                galaxy_scan_near_weight=4.5,
                galaxy_scan_attack_weight=7.25,
            )
            yaml_path = tp / "smart_scan.yaml"
            save_strategy_config(custom, yaml_path)
            loaded_yaml = load_strategy_config(yaml_path)
            self.assertFalse(loaded_yaml.galaxy_scan_explore_first)
            self.assertAlmostEqual(loaded_yaml.galaxy_scan_near_weight, 4.5)
            self.assertAlmostEqual(loaded_yaml.galaxy_scan_attack_weight, 7.25)

            db_path = tp / "smart_scan.db"
            db = GameDatabase(db_path, attack_history_path="")
            db.close()
            save_strategy_config(custom, db_path)
            loaded_db = load_strategy_config(db_path)
            self.assertFalse(loaded_db.galaxy_scan_explore_first)
            self.assertAlmostEqual(loaded_db.galaxy_scan_near_weight, 4.5)
            self.assertAlmostEqual(loaded_db.galaxy_scan_attack_weight, 7.25)


class TestSmartGalaxySectorIntelStorage(unittest.TestCase):
    def test_sector_intel_coverage_direct_raids_and_player_affinity(self) -> None:
        now = 1_750_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "intel.db", attack_history_path="")
            try:
                empty_intel = db.get_galaxy_sector_intel(galaxies=[2], now_ts=now)
                self.assertEqual(empty_intel["galaxies"], [2])
                self.assertEqual(empty_intel["explored_systems"], 0)
                self.assertEqual(empty_intel["unscanned_systems"], 400)
                self.assertEqual(empty_intel["total_systems"], 400)
                self.assertAlmostEqual(empty_intel["coverage_pct"], 0.0)

                # Record galaxy scans in 02:150, 02:152, 02:290, and 02:291
                db.record_galaxy_scan(
                    2,
                    150,
                    [
                        GalaxySlot(
                            coords="02:150:05",
                            position=5,
                            name="Homeworld",
                            owner="Me",
                            status_color="green",
                        ),
                        GalaxySlot(
                            coords="02:150:08",
                            position=8,
                            status_color="free",
                            colonize_min_fields=280,
                            colonize_max_fields=335,
                        ),
                    ],
                    ts=now - 900.0,
                )
                db.record_state_snapshot(
                    GameState(
                        universe_id=31,
                        user_id=101,
                        nickname="Me",
                        timestamp=now - 900.0,
                        planets=(
                            PlanetState(
                                planet_id=101,
                                name="Homeworld",
                                coords="02:150:05",
                                planet_type="normal",
                                temperature=30,
                                fields_used=20,
                                fields_max=300,
                                resources=Resources(),
                                storage=Resources(),
                                energy=EnergyBalance(100, 50),
                            ),
                        ),
                        active_planet_id=101,
                        researches={},
                    )
                )

                # FarmKing has a planet at 02:152:04 (raided 6x) AND a second planet at 02:290:09 (unraided)
                db.record_galaxy_scan(
                    2,
                    152,
                    [
                        GalaxySlot(
                            coords="02:152:04",
                            position=4,
                            name="LootHub",
                            owner="FarmKing",
                            status_color="yellow",
                            has_debris=True,
                        ),
                        GalaxySlot(
                            coords="02:152:12",
                            position=12,
                            name="Spacia Outpost",
                            owner="Spacia",
                            status_color="yellow",
                            is_spacia=True,
                        ),
                    ],
                    ts=now - 1200.0,
                )
                db.record_galaxy_scan(
                    2,
                    290,
                    [
                        GalaxySlot(
                            coords="02:290:09",
                            position=9,
                            name="RemoteColony",
                            owner="farmking",  # case-insensitive match
                            status_color="light_red",
                        ),
                    ],
                    ts=now - 1200.0,
                )
                db.record_galaxy_scan(
                    2,
                    291,
                    [
                        GalaxySlot(
                            coords="02:291:06",
                            position=6,
                            name="QuietWorld",
                            owner="PeacefulMiner",
                            status_color="white",
                        ),
                        GalaxySlot(
                            coords="02:291:14",
                            position=14,
                            name="Spacia Far",
                            owner="Spacia",
                            status_color="yellow",
                            is_spacia=True,
                        ),
                    ],
                    ts=now - 1200.0,
                )

                # Also test spy-report owner link from Galaxy 1 carrying affinity into Galaxy 2
                db.record_spy_report(
                    SpyReport(
                        target_coords="01:040:07",
                        owner="CrossGalRaider",
                        resources=Resources(spice=20000),
                    ),
                    ts=now - 2000.0,
                )
                db.record_galaxy_scan(
                    2,
                    310,
                    [
                        GalaxySlot(
                            coords="02:310:03",
                            position=3,
                            name="Outpost2",
                            owner="CrossGalRaider",
                            status_color="yellow",
                        ),
                    ],
                    ts=now - 1200.0,
                )

                for i in range(6):
                    db.record_raid(
                        "02:152:04",
                        attacked_ts=now - 300.0 * (i + 1),
                        is_exempt=True,
                        outcome="win",
                        loot={"spice": 5000, "metal": 4000},
                    )
                for i in range(3):
                    db.record_raid(
                        "02:152:12",
                        attacked_ts=now - 400.0 * (i + 1),
                        is_exempt=True,
                        outcome="Dispatched attack",
                    )
                for i in range(4):
                    db.record_raid(
                        "01:040:07",
                        attacked_ts=now - 500.0 * (i + 1),
                        is_exempt=False,
                        outcome="win",
                        loot={"spice": 10000},
                    )

                intel = db.get_galaxy_sector_intel(galaxies=[2], now_ts=now)
                serialized = json.dumps(intel, allow_nan=False)
                self.assertTrue(serialized)

                self.assertEqual(intel["explored_systems"], 5)
                self.assertEqual(intel["unscanned_systems"], 395)
                self.assertEqual(intel["total_systems"], 400)
                self.assertAlmostEqual(intel["coverage_pct"], 1.25)

                # FarmKing (6 raids) & CrossGalRaider (4 raids) tracked; NPC Spacia excluded from cross-sector owner map
                self.assertEqual(intel["owner_attack_counts"].get("FarmKing"), 6)
                self.assertEqual(intel["owner_attack_counts"].get("CrossGalRaider"), 4)
                self.assertNotIn("Spacia", intel["owner_attack_counts"])
                self.assertNotIn("Me", intel["owner_attack_counts"])

                s152 = intel["sectors"]["02:152"]
                self.assertEqual(s152["direct_raid_count"], 9)  # 6 on FarmKing + 3 on Spacia
                self.assertEqual(s152["owner_affinity_raid_count"], 6)
                self.assertEqual(s152["debris_count"], 1)
                self.assertEqual(s152["spacia_count"], 1)
                self.assertGreater(s152["direct_loot_spice_eq"], 30000.0)

                # 02:290 has 0 direct raids, but inherits FarmKing's 6-raid player affinity!
                s290 = intel["sectors"]["02:290"]
                self.assertEqual(s290["direct_raid_count"], 0)
                self.assertEqual(s290["owner_affinity_raid_count"], 6)

                # 02:291 has Spacia (which must NOT inherit 02:152:12's Spacia raids as player affinity!)
                s291 = intel["sectors"]["02:291"]
                self.assertEqual(s291["direct_raid_count"], 0)
                self.assertEqual(s291["owner_affinity_raid_count"], 0)
                self.assertEqual(s291["spacia_count"], 1)

                # 02:310 inherits CrossGalRaider's 4 raids from Galaxy 1!
                s310 = intel["sectors"]["02:310"]
                self.assertEqual(s310["owner_affinity_raid_count"], 4)
            finally:
                db.close()


class TestSmartGalaxyScanTwoPhaseEngine(unittest.TestCase):
    def test_phase1_exploratory_completes_all_400_systems_smoothly_without_duplicates(self) -> None:
        now = 1_750_000_000.0
        scanned: dict[str, float] = {}
        own_coords = ["02:390:05"]  # Near 400->1 wraparound boundary!

        for sweep_idx in range(4):
            batch = select_smart_galaxy_scan_batch(
                own_coords=own_coords,
                galaxies=[2],
                scanned_systems=scanned,
                batch_size=100,
                explore_first=True,
                now_ts=now + sweep_idx * 300.0,
            )
            self.assertEqual(batch["phase"], "exploratory")
            self.assertEqual(batch["explored_systems"], sweep_idx * 100)
            self.assertEqual(batch["total_systems"], 400)
            self.assertEqual(len(batch["selected_sectors"]), 100)

            # First sweep around 02:390 must include 02:390 and wrap cleanly across 400 -> 1!
            sys_seq = [it["system"] for it in batch["selected_sectors"]]
            self.assertEqual(len(set(sys_seq)), 100)
            if sweep_idx == 0:
                self.assertIn(390, sys_seq)
                self.assertIn(400, sys_seq)
                self.assertIn(1, sys_seq)
                # Every adjacent step in sweep 0 is a +1 mod 400 contiguous step!
                steps = [((sys_seq[i + 1] - sys_seq[i]) % 400) for i in range(len(sys_seq) - 1)]
                self.assertTrue(all(st == 1 for st in steps), f"Non-contiguous steps: {steps[:15]}")

            for it in batch["selected_sectors"]:
                skey = it["sector"]
                self.assertNotIn(skey, scanned, f"Duplicate exploratory scan on {skey}")
                scanned[skey] = now + sweep_idx * 300.0 + 1.0

        self.assertEqual(len(scanned), 400)

        # 5th sweep: 100% coverage achieved -> transitions to Phase 2 ("targeted_refresh")!
        refresh_batch = select_smart_galaxy_scan_batch(
            own_coords=own_coords,
            galaxies=[2],
            scanned_systems=scanned,
            batch_size=50,
            explore_first=True,
            now_ts=now + 2500.0,
        )
        self.assertEqual(refresh_batch["phase"], "targeted_refresh")
        self.assertEqual(refresh_batch["explored_systems"], 400)
        self.assertEqual(refresh_batch["unscanned_systems"], 0)
        self.assertAlmostEqual(refresh_batch["coverage_pct"], 100.0)
        self.assertEqual(len(refresh_batch["selected_sectors"]), 50)

    def test_exploratory_spillover_finishes_remaining_unscanned_first_then_refreshes_best(self) -> None:
        now = 1_750_000_000.0
        scanned = {f"02:{s:03d}": now - 1800.0 for s in range(1, 391)}  # 390 scanned, 10 unscanned (391..400)
        raid_targets = [
            {"coords": "02:120:04", "owner": "FrequentVictim", "status_color": "yellow"},
        ]
        raid_history = [
            {"target_coords": "02:120:04", "attacked_ts": now - 600.0, "loot": {"spice": 8000}}
            for _ in range(7)
        ]
        batch = select_smart_galaxy_scan_batch(
            own_coords=["02:100:05"],
            galaxies=[2],
            scanned_systems=scanned,
            raid_targets=raid_targets,
            raid_history=raid_history,
            batch_size=25,
            explore_first=True,
            now_ts=now,
        )
        self.assertEqual(batch["phase"], "exploratory")
        self.assertEqual(batch["explored_systems"], 390)
        self.assertEqual(batch["unscanned_systems"], 10)
        self.assertEqual(batch["exploratory_count"], 10)
        self.assertEqual(batch["refresh_count"], 15)
        self.assertEqual(len(batch["selected_sectors"]), 25)

        first_ten = batch["selected_sectors"][:10]
        last_fifteen = batch["selected_sectors"][10:]
        self.assertEqual({it["system"] for it in first_ten}, set(range(391, 401)))
        self.assertTrue(all(it["phase"] == "exploratory" for it in first_ten))
        self.assertTrue(all(it["phase"] == "targeted_refresh" for it in last_fifteen))
        self.assertIn("02:120", {it["sector"] for it in last_fifteen})

    def test_phase2_weights_near_sectors_and_frequent_attacked_players_and_sectors(self) -> None:
        now = 1_750_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            db = GameDatabase(Path(tmp) / "phase2.db", attack_history_path="")
            try:
                # Mark all 400 systems scanned equally 45 minutes ago
                for s in range(1, 401):
                    slots = []
                    if s == 230:
                        # Unraided planet owned by FavoriteFarm (whom we raided 10x at 02:155:04!)
                        slots.append(
                            GalaxySlot(
                                coords="02:230:08",
                                position=8,
                                name="FarmColony2",
                                owner="FavoriteFarm",
                                status_color="yellow",
                            )
                        )
                    elif s == 155:
                        slots.append(
                            GalaxySlot(
                                coords="02:155:04",
                                position=4,
                                name="FarmMain",
                                owner="FavoriteFarm",
                                status_color="yellow",
                            )
                        )
                    elif s == 231:
                        slots.append(
                            GalaxySlot(
                                coords="02:231:08",
                                position=8,
                                name="NeverAttacked",
                                owner="Stranger",
                                status_color="white",
                            )
                        )
                    elif s == 300:
                        slots.append(
                            GalaxySlot(
                                coords="02:300:06",
                                position=6,
                                name="BattleDebris",
                                owner="Someone",
                                status_color="light_red",
                                has_debris=True,
                            )
                        )
                    db.record_galaxy_scan(2, s, slots, ts=now - 2700.0)

                for i in range(10):
                    db.record_raid(
                        "02:155:04",
                        attacked_ts=now - 600.0 * (i + 1),
                        is_exempt=True,
                        outcome="win",
                        loot={"spice": 12000},
                    )

                intel = db.get_galaxy_sector_intel(galaxies=[2], now_ts=now)
                self.assertEqual(intel["explored_systems"], 400)
                self.assertAlmostEqual(intel["coverage_pct"], 100.0)

                ranked = score_galaxy_scan_sectors(
                    own_coords="02:150:05",
                    sector_intel=intel,
                    galaxies=[2],
                    explore_first=True,
                    near_weight=2.0,
                    attack_weight=3.0,
                    now_ts=now,
                )
                by_sec = {it["sector"]: it for it in ranked}

                # 1. Direct farm + near colony (02:155) ranks #1 overall!
                self.assertEqual(ranked[0]["sector"], "02:155")
                # 2. Player-affinity sector (02:230, FavoriteFarm's unraided 2nd planet) beats neighbor 02:231!
                self.assertGreater(by_sec["02:230"]["score"], by_sec["02:231"]["score"] * 2.5)
                self.assertGreater(by_sec["02:230"]["owner_affinity_raid_count"], 0)
                # 3. Near sector (02:151, dist=1) beats distant empty sector (02:350, dist=200)!
                self.assertGreater(by_sec["02:151"]["score"], by_sec["02:350"]["score"] * 2.0)
                # 4. Debris sector (02:300) beats adjacent empty sector (02:301)!
                self.assertGreater(by_sec["02:300"]["score"], by_sec["02:301"]["score"])

                # 5. Freshness window (< 120s) skips just-scanned 02:155 unless all sectors are fresh
                db.record_galaxy_scan(2, 155, [], ts=now - 20.0)
                intel_fresh = db.get_galaxy_sector_intel(galaxies=[2], now_ts=now)
                batch = select_smart_galaxy_scan_batch(
                    own_coords=["02:150:05"],
                    sector_intel=intel_fresh,
                    galaxies=[2],
                    batch_size=15,
                    min_freshness_seconds=120.0,
                    now_ts=now,
                )
                self.assertEqual(batch["phase"], "targeted_refresh")
                chosen_set = {it["sector"] for it in batch["selected_sectors"]}
                self.assertNotIn("02:155", chosen_set)
                self.assertIn("02:230", chosen_set)
                self.assertIn("02:150", chosen_set)
                self.assertTrue(len(batch["sample_targets"]) <= 5)
            finally:
                db.close()

    def test_circular_distance_smooth_walk_and_in_memory_set_inputs(self) -> None:
        self.assertEqual(galaxy_sector_distance(2, 399, [(2, 2)]), 3)
        self.assertEqual(galaxy_sector_distance(2, 150, [(2, 150)]), 0)
        self.assertGreater(galaxy_sector_distance(3, 150, [(2, 150)]), 200)

        walked = order_sectors_for_smooth_walk(
            [
                {"galaxy": 2, "system": 3},
                {"galaxy": 2, "system": 398},
                {"galaxy": 2, "system": 400},
                {"galaxy": 2, "system": 1},
            ],
            primary_colony_by_galaxy={2: 399},
        )
        self.assertEqual([w["system"] for w in walked], [398, 400, 1, 3])

        # Verify optimizer-style `set[(gal, sys)]` scanned_systems works seamlessly
        scanned_set = {(2, s) for s in range(1, 400)}  # Only (2, 400) unscanned!
        res = select_smart_galaxy_scan_batch(
            own_coords="02:005:04",
            scanned_systems=scanned_set,
            batch_size=1,
            explore_first=True,
            now_ts=1_750_000_000.0,
        )
        self.assertEqual(res["phase"], "exploratory")
        self.assertEqual(res["explored_systems"], 399)
        self.assertEqual(res["selected_sectors"][0]["sector"], "02:400")

    def test_refresh_vs_discovery_split_and_spillover(self) -> None:
        now = 1_750_000_000.0
        # 20 already-scanned sectors around 02:140..159 (stale by 45 min), 380 unscanned
        scanned_map = {f"02:{s:03d}": now - 2700.0 for s in range(140, 160)}

        # 30% Refresh / 70% Discovery on a 10-page batch -> 3 refresh + 7 discovery (mixed)
        mixed = select_smart_galaxy_scan_batch(
            own_coords="02:150:05",
            scanned_systems=scanned_map,
            galaxies=[2],
            batch_size=10,
            explore_first=True,
            refresh_pct=30,
            now_ts=now,
        )
        self.assertEqual(mixed["phase"], "mixed")
        self.assertEqual(mixed["refresh_count"], 3)
        self.assertEqual(mixed["exploratory_count"], 7)
        self.assertAlmostEqual(mixed["refresh_pct"], 30.0)
        self.assertAlmostEqual(mixed["discovery_pct"], 70.0)
        self.assertEqual(len(mixed["selected_sectors"]), 10)

        # 100% Refresh -> all 10 from already-scanned pool
        ref_only = select_smart_galaxy_scan_batch(
            own_coords="02:150:05",
            scanned_systems=scanned_map,
            galaxies=[2],
            batch_size=10,
            explore_first=True,
            refresh_pct=100,
            now_ts=now,
        )
        self.assertEqual(ref_only["phase"], "targeted_refresh")
        self.assertEqual(ref_only["refresh_count"], 10)
        self.assertEqual(ref_only["exploratory_count"], 0)
        self.assertTrue(all(it["scanned"] for it in ref_only["selected_sectors"]))

        # Spillover: 80% Refresh requested on 10-page batch when only 2 sectors have been scanned -> 2 refresh + 8 discovery
        sparse_scanned = {"02:150": now - 2700.0, "02:151": now - 2700.0}
        spilled = select_smart_galaxy_scan_batch(
            own_coords="02:150:05",
            scanned_systems=sparse_scanned,
            galaxies=[2],
            batch_size=10,
            explore_first=True,
            refresh_pct=80,
            now_ts=now,
        )
        self.assertEqual(spilled["phase"], "mixed")
        self.assertEqual(spilled["refresh_count"], 2)
        self.assertEqual(spilled["exploratory_count"], 8)
        self.assertEqual(len(spilled["selected_sectors"]), 10)

    def test_scanned_sectors_command_and_refresh_pct_config_roundtrip(self) -> None:
        from spaceinvasion.api_server import GameApiBridge
        from spaceinvasion.config import load_strategy_config, save_strategy_config, StrategyConfig
        from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources

        now = 1_750_000_000.0
        with tempfile.TemporaryDirectory(prefix="si_sec_api_") as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            save_strategy_config(
                StrategyConfig(
                    galaxy_scan_refresh_pct=25,
                    galaxy_sweep_pages=80,
                    galaxy_scan_all_galaxies=True,
                ),
                cfg_path,
            )
            reloaded = load_strategy_config(cfg_path)
            self.assertEqual(reloaded.galaxy_scan_refresh_pct, 25)
            self.assertEqual(reloaded.galaxy_sweep_pages, 80)
            self.assertTrue(reloaded.galaxy_scan_all_galaxies)

            db = GameDatabase(tmp_path / "state.db")
            bridge: GameApiBridge | None = None
            try:
                db.record_galaxy_scan(
                    2,
                    152,
                    [
                        GalaxySlot(
                            coords="02:152:04",
                            position=4,
                            name="FarmPlanet",
                            owner="IdleGuy",
                            status_color="yellow",
                            has_debris=True,
                        ),
                        GalaxySlot(
                            coords="02:152:08",
                            position=8,
                            name="",
                            owner="",
                            status_color="free",
                            colonize_max_fields=340,
                        ),
                    ],
                    ts=now - 1800.0,
                )
                db.record_galaxy_scan(
                    2,
                    155,
                    [
                        GalaxySlot(
                            coords="02:155:02",
                            position=2,
                            name="SpaciaOutpost",
                            owner="Spacia",
                            status_color="yellow",
                            is_spacia=True,
                        ),
                    ],
                    ts=now - 600.0,
                )
                bridge = GameApiBridge(
                    config_path=cfg_path,
                    db_path=tmp_path / "state.db",
                )
                bridge._last_state = GameState(
                    universe_id=31,
                    user_id=1,
                    nickname="tester",
                    timestamp=now,
                    planets=(
                        PlanetState(
                            planet_id=101,
                            name="Home",
                            coords="02:150:05",
                            planet_type="normal",
                            temperature=28,
                            fields_used=20,
                            fields_max=300,
                            resources=Resources(1000, 1000, 1000, 500),
                            storage=Resources(50000, 50000, 50000, 50000),
                            energy=EnergyBalance(100, 50),
                        ),
                    ),
                    active_planet_id=101,
                )

                res = bridge.handle_command({"command": "scanned_sectors"})
                self.assertTrue(res["ok"])
                self.assertEqual(res["origin_coords"], "02:150:05")
                self.assertEqual(res["galaxies_available"], list(range(1, 15)))
                self.assertEqual(res["galaxies_with_scans"], [2])
                self.assertEqual(len(res["galaxy_stats"]), 14)
                g2_stat = next(gs for gs in res["galaxy_stats"] if gs["galaxy"] == 2)
                g3_stat = next(gs for gs in res["galaxy_stats"] if gs["galaxy"] == 3)
                self.assertTrue(g2_stat["is_owned"])
                self.assertEqual(g2_stat["scanned_count"], 2)
                self.assertEqual(g2_stat["unscanned_count"], 398)
                self.assertEqual(g3_stat["scanned_count"], 0)
                self.assertEqual(g3_stat["unscanned_count"], 400)
                self.assertEqual(len(res["scan_queue"]), 80)
                self.assertEqual(res["scan_queue"][0]["queue_pos"], 1)
                self.assertTrue(any(q["phase"] == "exploratory" for q in res["scan_queue"]))
                self.assertTrue(any(q["phase"] == "targeted_refresh" for q in res["scan_queue"]))
                self.assertEqual(len(res["sectors"]), 2)
                by_sec = {s["sector"]: s for s in res["sectors"]}
                self.assertTrue(by_sec["02:152"]["scanned"])
                self.assertEqual(by_sec["02:152"]["occupied_count"], 1)
                self.assertEqual(by_sec["02:152"]["debris_count"], 1)
                self.assertEqual(by_sec["02:152"]["prime_colony_free_count"], 1)
                self.assertEqual(by_sec["02:152"]["system_distance"], 2)
                self.assertEqual(by_sec["02:155"]["spacia_count"], 1)
                self.assertEqual(by_sec["02:155"]["system_distance"], 5)
                self.assertEqual(res["summary"]["refresh_pct"], 25)
                self.assertEqual(res["summary"]["discovery_pct"], 75)
                self.assertEqual(res["summary"]["planned_refresh_pages"], 20)
                self.assertEqual(res["summary"]["planned_discovery_pages"], 60)
                self.assertTrue(res["summary"]["scan_all_galaxies"])
                self.assertEqual(res["summary"]["queue_count"], 80)

                # Single-galaxy view on unscanned G3 returns all 400 systems with distances
                res_g3 = bridge.handle_command({"command": "scanned_sectors", "galaxy": 3})
                self.assertTrue(res_g3["ok"])
                self.assertEqual(res_g3["galaxy_filter"], 3)
                self.assertEqual(len(res_g3["sectors"]), 400)
                self.assertTrue(all(s["galaxy"] == 3 and not s["scanned"] for s in res_g3["sectors"]))
                s3_150 = next(s for s in res_g3["sectors"] if s["sector"] == "03:150")
                self.assertEqual(s3_150["system_distance"], 400)

                # All-galaxy view with include_unscanned=True appends unscanned sectors across G1..G14
                res_inc = bridge.handle_command({"command": "scanned_sectors", "include_unscanned": True})
                self.assertTrue(res_inc["ok"])
                self.assertGreater(len(res_inc["sectors"]), 2)
                self.assertTrue(any(not s["scanned"] for s in res_inc["sectors"]))

                upd = bridge.handle_command(
                    {
                        "command": "update_config",
                        "config": {
                            "galaxy_scan_refresh_pct": 40,
                            "galaxy_sweep_pages": 50,
                            "galaxy_scan_all_galaxies": False,
                        },
                    }
                )
                self.assertTrue(upd["ok"])
                self.assertEqual(upd["config"]["galaxy_scan_refresh_pct"], 40)
                self.assertFalse(upd["config"]["galaxy_scan_all_galaxies"])
                res2 = bridge.handle_command({"command": "scanned_sectors"})
                self.assertEqual(res2["summary"]["refresh_pct"], 40)
                self.assertEqual(res2["summary"]["planned_refresh_pages"], 20)
                self.assertEqual(res2["summary"]["planned_discovery_pages"], 30)
                self.assertFalse(res2["summary"]["scan_all_galaxies"])
                self.assertEqual(len(res2["scan_queue"]), 50)
            finally:
                if bridge is not None and bridge._db is not None:
                    bridge._db.close()
                db.close()

    def test_mixed_split_spills_discovery_to_all_14_galaxies_when_owned_galaxy_is_full(self) -> None:
        from spaceinvasion.api_server import GameApiBridge
        from spaceinvasion.config import save_strategy_config, StrategyConfig
        from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources

        now = 1_750_000_000.0
        with tempfile.TemporaryDirectory(prefix="si_sec_spill_") as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            # galaxy_scan_all_galaxies=False, but mixed 30% Refresh / 70% Discovery on a 100% scanned G2
            save_strategy_config(
                StrategyConfig(
                    galaxy_scan_refresh_pct=30,
                    galaxy_sweep_pages=40,
                    galaxy_scan_all_galaxies=False,
                ),
                cfg_path,
            )
            db = GameDatabase(tmp_path / "state.db")
            bridge: GameApiBridge | None = None
            try:
                for sys_i in range(1, 401):
                    db.record_galaxy_scan(2, sys_i, [], ts=now - 1800.0 - sys_i)
                bridge = GameApiBridge(
                    config_path=cfg_path,
                    db_path=tmp_path / "state.db",
                )
                bridge._last_state = GameState(
                    universe_id=31,
                    user_id=1,
                    nickname="tester",
                    timestamp=now,
                    planets=(
                        PlanetState(
                            planet_id=101,
                            name="Home",
                            coords="02:380:12",
                            planet_type="normal",
                            temperature=28,
                            fields_used=20,
                            fields_max=300,
                            resources=Resources(1000, 1000, 1000, 500),
                            storage=Resources(50000, 50000, 50000, 50000),
                            energy=EnergyBalance(100, 50),
                        ),
                    ),
                    active_planet_id=101,
                )

                res = bridge.handle_command({"command": "scanned_sectors"})
                self.assertTrue(res["ok"])
                self.assertEqual(res["summary"]["queue_galaxies"], list(range(1, 15)))
                q = res["scan_queue"]
                self.assertEqual(len(q), 40)
                disc_items = [it for it in q if it["phase"] == "exploratory"]
                ref_items = [it for it in q if it["phase"] == "targeted_refresh"]
                self.assertEqual(len(ref_items), 12)  # 30% of 40
                self.assertEqual(len(disc_items), 28)  # 70% of 40
                # Discovery expands into nearest neighbor galaxies G1 and G3!
                self.assertTrue({it["galaxy"] for it in disc_items}.issubset({1, 3}))
                self.assertTrue(all(it["galaxy"] == 2 for it in ref_items))
            finally:
                if bridge is not None and bridge._db is not None:
                    bridge._db.close()
                db.close()

    def test_scanned_sectors_live_preview_rebuilds_queue_without_mutating_saved_config(self) -> None:
        from spaceinvasion.api_server import GameApiBridge
        from spaceinvasion.config import save_strategy_config, StrategyConfig
        from spaceinvasion.state import EnergyBalance, GameState, PlanetState, Resources

        now = 1_750_000_000.0
        with tempfile.TemporaryDirectory(prefix="si_sec_prev_") as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "config.yaml"
            save_strategy_config(
                StrategyConfig(
                    galaxy_scan_refresh_pct=0,
                    galaxy_sweep_pages=100,
                    galaxy_scan_all_galaxies=False,
                ),
                cfg_path,
            )
            db = GameDatabase(tmp_path / "state.db")
            bridge: GameApiBridge | None = None
            try:
                for sys_i in range(1, 401):
                    db.record_galaxy_scan(2, sys_i, [], ts=now - 1800.0 - sys_i)
                bridge = GameApiBridge(
                    config_path=cfg_path,
                    db_path=tmp_path / "state.db",
                )
                bridge._last_state = GameState(
                    universe_id=31,
                    user_id=1,
                    nickname="tester",
                    timestamp=now,
                    planets=(
                        PlanetState(
                            planet_id=101,
                            name="Home",
                            coords="02:380:12",
                            planet_type="normal",
                            temperature=28,
                            fields_used=20,
                            fields_max=300,
                            resources=Resources(1000, 1000, 1000, 500),
                            storage=Resources(50000, 50000, 50000, 50000),
                            energy=EnergyBalance(100, 50),
                        ),
                    ),
                    active_planet_id=101,
                )

                # Baseline saved config: 0% refresh, all_galaxies=False, G2 100% scanned -> 100 refresh in G2
                base = bridge.handle_command({"command": "scanned_sectors"})
                self.assertFalse(base["preview"])
                self.assertEqual(base["summary"]["queue_count"], 100)
                self.assertEqual(base["summary"]["queue_refresh_count"], 100)
                self.assertEqual(base["summary"]["queue_discovery_count"], 0)
                self.assertEqual(base["summary"]["queue_galaxies"], [2])

                # Preview toggling All 14 Galaxies ON -> 100 discovery in G1/G3 without mutating saved config
                prev_all = bridge.handle_command(
                    {
                        "command": "scanned_sectors",
                        "preview_refresh_pct": 0,
                        "preview_sweep_pages": 100,
                        "preview_all_galaxies": True,
                    }
                )
                self.assertTrue(prev_all["preview"])
                self.assertEqual(prev_all["summary"]["queue_count"], 100)
                self.assertEqual(prev_all["summary"]["queue_discovery_count"], 100)
                self.assertEqual(prev_all["summary"]["queue_refresh_count"], 0)
                self.assertEqual(prev_all["summary"]["queue_galaxies"], list(range(1, 15)))
                self.assertTrue(prev_all["summary"]["scan_all_galaxies"])
                self.assertFalse(prev_all["summary"]["saved_scan_all_galaxies"])
                self.assertFalse(bridge.strategy_config.galaxy_scan_all_galaxies)

                # Preview mixed 30% Refresh / 70% Discovery with 40 req/sweep -> 12 refresh + 28 discovery
                prev_mix = bridge.handle_command(
                    {
                        "command": "scanned_sectors",
                        "preview_refresh_pct": 30,
                        "preview_sweep_pages": 40,
                        "preview_all_galaxies": False,
                    }
                )
                self.assertTrue(prev_mix["preview"])
                self.assertEqual(prev_mix["summary"]["queue_count"], 40)
                self.assertEqual(prev_mix["summary"]["queue_refresh_count"], 12)
                self.assertEqual(prev_mix["summary"]["queue_discovery_count"], 28)
                self.assertEqual(prev_mix["summary"]["saved_refresh_pct"], 0)
                self.assertEqual(prev_mix["summary"]["saved_sweep_pages"], 100)
                self.assertEqual(bridge.strategy_config.galaxy_scan_refresh_pct, 0)
                self.assertEqual(bridge.strategy_config.galaxy_sweep_pages, 100)

                # Subsequent non-preview read still returns original saved queue
                after = bridge.handle_command({"command": "scanned_sectors"})
                self.assertFalse(after["preview"])
                self.assertEqual(after["summary"]["queue_refresh_count"], 100)
                self.assertEqual(after["summary"]["queue_discovery_count"], 0)
            finally:
                if bridge is not None and bridge._db is not None:
                    bridge._db.close()
                db.close()

    def test_exploratory_contiguous_arrow_chain_and_cursor_anchoring(self) -> None:
        now = 1_750_000_000.0
        # 1. After Sweep 0 scanned 340..400 + 1..39 around 02:390, Sweep 1 MUST pick ONE contiguous 100-system run
        # (every adjacent step distance == 1 for arrow clicks) instead of splitting left + right wings!
        scanned_sw0 = {f"02:{s:03d}": now - 100.0 for s in list(range(340, 401)) + list(range(1, 40))}
        sw1 = select_smart_galaxy_scan_batch(
            own_coords=["02:390:05"],
            galaxies=[2],
            scanned_systems=scanned_sw0,
            batch_size=100,
            explore_first=True,
            now_ts=now,
        )
        seq1 = [it["system"] for it in sw1["selected_sectors"]]
        self.assertEqual(len(seq1), 100)
        steps1 = [min((seq1[i + 1] - seq1[i]) % 400, (seq1[i] - seq1[i + 1]) % 400) for i in range(len(seq1) - 1)]
        self.assertTrue(all(st == 1 for st in steps1), f"Expected all dist=1 steps, got {steps1[:10]}")

        # 2. Outer-galaxy exploration anchored at current_cursor=(9, 333) continues 09:334 -> 09:335 -> ...
        scanned_outer = {f"02:{s:03d}": now - 600.0 for s in range(1, 401)}
        for s in range(1, 334):
            scanned_outer[f"09:{s:03d}"] = now - 300.0
        batch_cur = select_smart_galaxy_scan_batch(
            own_coords=["02:380:12"],
            galaxies=list(range(1, 15)),
            scanned_systems=scanned_outer,
            batch_size=25,
            explore_first=True,
            current_cursor=(9, 333),
            now_ts=now,
        )
        sel = batch_cur["selected_sectors"]
        self.assertEqual(len(sel), 25)
        self.assertTrue(all(it["galaxy"] == 9 for it in sel))
        self.assertEqual([it["system"] for it in sel[:5]], [334, 335, 336, 337, 338])

        # 3. order_sectors_for_smooth_walk reverses arc when cursor is right next to the arc's trailing edge
        walk_bwd = order_sectors_for_smooth_walk(
            [{"galaxy": 2, "system": s} for s in (329, 330, 331, 332)],
            primary_colony_by_galaxy={2: 100},
            current_cursor=(2, 333),
        )
        self.assertEqual([w["system"] for w in walk_bwd], [332, 331, 330, 329])


if __name__ == "__main__":
    unittest.main()
