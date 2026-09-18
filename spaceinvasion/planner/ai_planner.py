"""Pure-Python W-Week Discounted AI-Planner (Path Simulator + 8-Gene GA + Live Reconciler).

Direct, zero-pandas/Jupyter/Selenium port of `notebooks/full_planner.ipynb`
(Sections 2-5 & 11), adapted for live empire execution:
- Initializes directly from live `GameState` (built levels, researches, resources).
- Computes live median attack telemetry from SQLite raid/battle rows.
- Never generates or executes building demolition steps.
- Reconciles saved sequential plans into causally unblocked live `PlannedAction`s
  and structured max-field conflict prompts.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import copy
import datetime
import heapq
import json
import math
import re
import time
from typing import Any, Optional

import numpy as np

from spaceinvasion.combat.fleet import (
    SHIP_SLUG_BY_KEY,
    _parse_coords_distance,
    normalize_coords,
)
from spaceinvasion.combat.ranking import attach_action_etas
from spaceinvasion.core import game_data
from spaceinvasion.core.config import StrategyConfig
from spaceinvasion.core.state import (
    BUILDING_ID_BY_NAME,
    GameState,
    PlanetState,
    QueueJob,
    ResearchOffer,
    Resources,
    is_building_allowed_on_planet_type,
    normalize_planet_type,
)
from spaceinvasion.planner.config_optimizer import (
    PRODUCTION_FLOW_KEYS,
    RESOURCE_KEYS,
    TECH_GRAPH,
    WAREHOUSE_KEYS,
    _json_ready,
    _safe_speedup,
    build_monotonic_base_times,
    building_level_flows,
    building_time_divisor,
    fmt_duration,
    full_cost,
    research_catalog,
    research_time_divisor,
    shipyard_time_divisor,
    solar_cell_energy_per_unit,
    technology_multiplier,
    warehouse_level_for,
)
from spaceinvasion.planner.strategy import PlannedAction

PATH_FLOW_BUILDING_KEYS: tuple[str, ...] = ("b1", "b2", "b3", "b4", "b5", "b6")
PATH_MINE_KEYS: tuple[str, ...] = ("b1", "b2", "b3", "b4")
PATH_ACCELERATOR_KEYS: tuple[str, ...] = ("b7", "b17")
PATH_HOURS_PER_WEEK: float = 168.0
PATH_NEVER: float = float("inf")
DEFAULT_MEDIAN_ROUND_TRIP_SECONDS: float = 1800.0

DEFAULT_AI_PLANNER_PARAMS: dict[str, Any] = {
    "reward_weeks": 26,
    "weekly_discount": 0.90,
    "resource_spare_percent": {
        "pig_iron": 10.0,
        "metal": 10.0,
        "kryptonite": 10.0,
        "spice": 10.0,
    },
    "fleet_command_center_level": None,
    "attacks_per_day": None,
    "scarcity_focus": 1.0,
    "critical_queue_share": 0.9,
    "patience": 0.25,
    "speed_weight": 1.5,
    "revalue_every_steps": 25,
    "search_tolerance": 0.01,
    "ga_population_size": 10,
    "ga_generations": 6,
    "ga_tournament_size": 3,
    "ga_crossover_rate": 0.9,
    "ga_mutation_rate": 0.3,
    "ga_elite_count": 2,
    "ga_random_seed": 20261117,
    "pad_generic_colonies": False,
}

PATH_GENE_RANGES: dict[str, tuple[float, float]] = {
    "focus": (0.0, 3.0),
    "critical_share": (0.2, 1.0),
    "patience": (0.0, 1.0),
    "speed_weight": (0.0, 3.0),
    "value_pig_iron": (-2.0, 2.0),
    "value_metal": (-2.0, 2.0),
    "value_kryptonite": (-2.0, 2.0),
    "value_spice": (-2.0, 2.0),
}

_UPPER_AI_PARAM_ALIASES: dict[str, str] = {
    "PATH_REWARD_WEEKS": "reward_weeks",
    "PATH_WEEKLY_DISCOUNT": "weekly_discount",
    "PATH_RESOURCE_SPARE_PERCENT": "resource_spare_percent",
    "PATH_FLEET_COMMAND_CENTER_LEVEL": "fleet_command_center_level",
    "PATH_ATTACKS_PER_DAY": "attacks_per_day",
    "PATH_SCARCITY_FOCUS": "scarcity_focus",
    "PATH_CRITICAL_QUEUE_SHARE": "critical_queue_share",
    "PATH_PATIENCE": "patience",
    "PATH_SPEED_WEIGHT": "speed_weight",
    "PATH_REVALUE_EVERY_STEPS": "revalue_every_steps",
    "PATH_SEARCH_TOLERANCE": "search_tolerance",
    "PATH_GA_POPULATION_SIZE": "ga_population_size",
    "PATH_GA_GENERATIONS": "ga_generations",
    "PATH_GA_TOURNAMENT_SIZE": "ga_tournament_size",
    "PATH_GA_CROSSOVER_RATE": "ga_crossover_rate",
    "PATH_GA_MUTATION_RATE": "ga_mutation_rate",
    "PATH_GA_ELITE_COUNT": "ga_elite_count",
    "PATH_GA_RANDOM_SEED": "ga_random_seed",
}

_COORDS_RE = re.compile(r"^\d{1,2}:\d{1,3}:\d{1,2}$", re.ASCII)
_TIMER_RE = re.compile(r"(\d+):(\d{2}):(\d{2})", re.ASCII)
_LEVEL_RE = re.compile(r"\(\s*(?:Level|Lvl|L)?\s*(\d+)\s*\)", re.IGNORECASE | re.ASCII)


# ---------------------------------------------------------------------------
# Formatting & attack telemetry helpers
# ---------------------------------------------------------------------------


def fmt_path_time(hours: float) -> str:
    """Format hours as text: `fmt_duration` below 2 years, then years of 365 days."""
    if not math.isfinite(hours):
        return "never"
    if hours >= 2.0 * 8760.0:
        return f"{hours / 8760.0:,.1f} years"
    return fmt_duration(hours * 3600.0)


def _parse_json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _is_valid_coords(raw: Any) -> bool:
    txt = str(raw or "").strip()
    return bool(_COORDS_RE.match(txt))


def _estimate_physics_one_way_seconds(
    origin_coords: str,
    target_coords: str,
    *,
    min_ship_speed: float = 12000.0,
    fleet_speed_factor: float = 5.0,
) -> float:
    distance = max(1.0, float(_parse_coords_distance(origin_coords, target_coords)))
    speed = max(100.0, float(min_ship_speed))
    factor = min(100.0, max(1.0, float(fleet_speed_factor)))
    return float(round(10.0 + (3500.0 / factor) * math.sqrt((10.0 * distance) / speed)))


def _parse_server_clock(text: Any) -> Optional[float]:
    try:
        parsed = datetime.datetime.strptime(str(text or "").strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.timezone.utc).timestamp()


def _infer_server_tz_offset_sec(
    battle_rows: Sequence[Mapping[str, Any]],
    server_clock_ts: Sequence[Optional[float]],
    raid_rows: Sequence[Mapping[str, Any]],
) -> float:
    votes: list[float] = []
    for battle, clock_ts in zip(battle_rows, server_clock_ts):
        if clock_ts is None:
            continue
        b_target = str(battle.get("target_coords") or "")
        for raid in raid_rows:
            if str(raid.get("target_coords") or "") != b_target:
                continue
            dispatch_ts = game_data.safe_finite_float(raid.get("attacked_ts"), 0.0)
            offset_hours = round((dispatch_ts - clock_ts) / 3600.0)
            if abs(offset_hours) <= 14 and 30.0 <= (clock_ts + offset_hours * 3600.0 - dispatch_ts) <= 3600.0:
                votes.append(offset_hours * 3600.0)
    return float(np.median(np.asarray(votes, dtype=np.float64))) if votes else 0.0


def _extract_battle_rows_from_messages(message_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    battles: list[dict[str, Any]] = []
    for row in message_rows:
        if not isinstance(row, Mapping):
            continue
        # Direct `battle_reports` row shape
        if any(k in row for k in ("loot_spice_eq", "battle_id", "attacker_lost_spice")) or (
            all(k in row for k in RESOURCE_KEYS) and "outcome" in row
        ):
            battles.append(dict(row))
            continue
        # `game_messages` row shape (`category == "battle"` + `parsed_json`)
        cat = str(row.get("category") or "").strip().lower()
        parsed = _parse_json_dict(row.get("parsed_json"))
        if cat == "battle" or (parsed and any(k in parsed for k in ("loot", "pig_iron", "loot_spice_eq", "outcome"))):
            loot_map = parsed.get("loot") if isinstance(parsed.get("loot"), Mapping) else parsed
            loot_vals = {r: max(0.0, game_data.safe_finite_float(loot_map.get(r), 0.0)) for r in RESOURCE_KEYS}
            sp_eq = game_data.safe_finite_float(
                parsed.get("loot_spice_eq"),
                float(game_data.spice_equivalent(loot_vals)),
            )
            battles.append(
                {
                    "ts": game_data.safe_finite_float(row.get("ingested_ts", row.get("ts")), 0.0),
                    "msg_date_text": str(row.get("msg_date_text") or parsed.get("msg_date_text") or ""),
                    "target_coords": str(row.get("coords") or parsed.get("target_coords") or parsed.get("coords") or ""),
                    "outcome": str(parsed.get("outcome") or row.get("outcome") or "win"),
                    **loot_vals,
                    "loot_spice_eq": sp_eq,
                    "attacker_lost_spice": game_data.safe_finite_float(parsed.get("attacker_lost_spice"), 0.0),
                    "one_way_sec": parsed.get("one_way_sec", row.get("one_way_sec")),
                    "round_trip_sec": parsed.get("round_trip_sec", row.get("round_trip_sec")),
                }
            )
    return battles


def compute_attack_telemetry(
    raid_rows: Sequence[Mapping[str, Any]],
    message_rows: Sequence[Mapping[str, Any]] | None = None,
    *,
    state: GameState | None = None,
) -> dict[str, Any]:
    """Compute live median attack loot, round-trip flight time, fleet slots, and hourly loot."""
    normal_planets = [
        p for p in (getattr(state, "planets", ()) or ()) if normalize_planet_type(p) == "normal"
    ]
    current_fleet_slots = int(sum(1 + p.building_level(10) for p in normal_planets))
    default_origin = str(normal_planets[0].coords) if normal_planets and _is_valid_coords(normal_planets[0].coords) else ""
    fleet_speed = _safe_speedup(state, {}) if state is not None else 5.0
    if fleet_speed <= 0.0:
        fleet_speed = 5.0

    norm_raids = [dict(r) for r in (raid_rows or ()) if isinstance(r, Mapping)]
    battle_rows = _extract_battle_rows_from_messages(message_rows or ())

    loot_samples: dict[str, list[float]] = {r: [] for r in RESOURCE_KEYS}
    spice_eq_samples: list[float] = []
    rt_samples: list[float] = []
    wins = 0
    losses = 0
    source = "cold_start"

    if battle_rows:
        source = "battle_reports"
        server_clock_ts = [_parse_server_clock(b.get("msg_date_text")) for b in battle_rows]
        tz_offset = _infer_server_tz_offset_sec(battle_rows, server_clock_ts, norm_raids)

        used_raids: set[int] = set()
        seconds_per_sqrt_dist: list[float] = []
        paired: list[tuple[dict[str, Any], Optional[float], str]] = []
        for battle, clock_ts in zip(battle_rows, server_clock_ts):
            target = str(battle.get("target_coords") or "").strip()
            impact_ts = (clock_ts + tz_offset) if clock_ts is not None else game_data.safe_finite_float(battle.get("ts"), 0.0)
            explicit_rt = game_data.safe_finite_float(battle.get("round_trip_sec"), 0.0)
            explicit_ow = game_data.safe_finite_float(battle.get("one_way_sec"), 0.0)
            flight_ow: Optional[float] = None
            origin = str(battle.get("origin_coords") or default_origin).strip()
            if explicit_rt > 0.0:
                flight_ow = explicit_rt / 2.0
            elif explicit_ow > 0.0:
                flight_ow = explicit_ow
            else:
                paired_idx: Optional[int] = None
                for idx, raid in enumerate(norm_raids):
                    if idx in used_raids or str(raid.get("target_coords") or "").strip() != target:
                        continue
                    gap = impact_ts - game_data.safe_finite_float(raid.get("attacked_ts"), 0.0)
                    if 30.0 <= gap <= 7200.0 and (flight_ow is None or gap < flight_ow):
                        flight_ow = gap
                        origin = str(raid.get("origin_coords") or default_origin).strip()
                        paired_idx = idx
                if paired_idx is not None and flight_ow is not None:
                    used_raids.add(paired_idx)
                    if _is_valid_coords(origin) and _is_valid_coords(target):
                        dist = max(1.0, float(_parse_coords_distance(origin, target)))
                        seconds_per_sqrt_dist.append(max(1.0, flight_ow - 10.0) / math.sqrt(dist))
            paired.append((battle, flight_ow, origin))

        calibration = (
            float(np.median(np.asarray(seconds_per_sqrt_dist, dtype=np.float64)))
            if seconds_per_sqrt_dist
            else None
        )
        for battle, flight_ow, origin in paired:
            target = str(battle.get("target_coords") or "").strip()
            if flight_ow is not None and flight_ow > 0.0:
                rt_samples.append(2.0 * flight_ow)
            elif calibration is not None and _is_valid_coords(origin) and _is_valid_coords(target):
                dist = max(1.0, float(_parse_coords_distance(origin, target)))
                rt_samples.append(2.0 * (10.0 + calibration * math.sqrt(dist)))
            elif _is_valid_coords(origin) and _is_valid_coords(target):
                rt_samples.append(
                    2.0 * _estimate_physics_one_way_seconds(origin, target, fleet_speed_factor=fleet_speed)
                )

            outcome = str(battle.get("outcome") or "").strip().lower()
            won = outcome in ("win", "victory") or ("loss" not in outcome and "defeat" not in outcome and outcome != "")
            if won:
                wins += 1
            else:
                losses += 1
            loot = {r: max(0.0, game_data.safe_finite_float(battle.get(r), 0.0)) for r in RESOURCE_KEYS}
            for r in RESOURCE_KEYS:
                loot_samples[r].append(loot[r])
            sp_eq = game_data.safe_finite_float(
                battle.get("loot_spice_eq"),
                float(game_data.spice_equivalent(loot)),
            )
            spice_eq_samples.append(max(0.0, sp_eq))
    elif norm_raids:
        source = "raid_history"
        for raid in norm_raids:
            loot_obj = (
                raid.get("loot")
                if isinstance(raid.get("loot"), Mapping)
                else _parse_json_dict(raid.get("loot_json"))
            )
            if not loot_obj and any(r in raid for r in RESOURCE_KEYS):
                loot_obj = raid
            loot = {r: max(0.0, game_data.safe_finite_float(loot_obj.get(r), 0.0)) for r in RESOURCE_KEYS}
            for r in RESOURCE_KEYS:
                loot_samples[r].append(loot[r])
            sp_eq = game_data.safe_finite_float(
                raid.get("loot_spice_eq"),
                float(game_data.spice_equivalent(loot)),
            )
            spice_eq_samples.append(max(0.0, sp_eq))

            outcome = str(raid.get("outcome") or "").strip().lower()
            won = ("defeat" not in outcome) and ("loss" not in outcome)
            if won:
                wins += 1
            else:
                losses += 1

            explicit_rt = game_data.safe_finite_float(
                raid.get("round_trip_sec", raid.get("round_trip_seconds")), 0.0
            )
            explicit_ow = game_data.safe_finite_float(
                raid.get("one_way_sec", raid.get("one_way_seconds")), 0.0
            )
            origin = str(raid.get("origin_coords") or default_origin).strip()
            target = str(raid.get("target_coords") or "").strip()
            if explicit_rt > 0.0:
                rt_samples.append(explicit_rt)
            elif explicit_ow > 0.0:
                rt_samples.append(2.0 * explicit_ow)
            elif _is_valid_coords(origin) and _is_valid_coords(target):
                rt_samples.append(
                    2.0 * _estimate_physics_one_way_seconds(origin, target, fleet_speed_factor=fleet_speed)
                )

    sample_count = len(spice_eq_samples)
    median_rt = (
        float(np.median(np.asarray(rt_samples, dtype=np.float64)))
        if rt_samples
        else DEFAULT_MEDIAN_ROUND_TRIP_SECONDS
    )
    if not math.isfinite(median_rt) or median_rt <= 0.0:
        median_rt = DEFAULT_MEDIAN_ROUND_TRIP_SECONDS

    median_loot: dict[str, float] = {
        r: float(np.median(np.asarray(loot_samples[r], dtype=np.float64))) if sample_count > 0 else 0.0
        for r in RESOURCE_KEYS
    }
    median_loot["spice_eq"] = (
        float(np.median(np.asarray(spice_eq_samples, dtype=np.float64))) if sample_count > 0 else 0.0
    )
    mean_loot: dict[str, float] = {
        r: float(np.mean(np.asarray(loot_samples[r], dtype=np.float64))) if sample_count > 0 else 0.0
        for r in RESOURCE_KEYS
    }
    mean_loot["spice_eq"] = (
        float(np.mean(np.asarray(spice_eq_samples, dtype=np.float64))) if sample_count > 0 else 0.0
    )

    attacks_per_slot_per_hour = (3600.0 / median_rt) if (sample_count > 0 and median_rt > 0.0) else 0.0
    estimated_attacks_per_hour = float(current_fleet_slots) * attacks_per_slot_per_hour
    income_per_slot_per_hour = {r: median_loot[r] * attacks_per_slot_per_hour for r in RESOURCE_KEYS}
    hourly_loot: dict[str, float] = {
        r: income_per_slot_per_hour[r] * float(current_fleet_slots) for r in RESOURCE_KEYS
    }
    hourly_loot["spice_eq"] = float(game_data.spice_equivalent({r: hourly_loot[r] for r in RESOURCE_KEYS}))

    return _json_ready(
        {
            "source": source,
            "sample_count": int(sample_count),
            "wins": int(wins),
            "losses": int(losses),
            "success_rate": round(float(wins) / float(sample_count), 4) if sample_count > 0 else 0.0,
            "median_loot": {k: round(v, 2) for k, v in median_loot.items()},
            "mean_loot": {k: round(v, 2) for k, v in mean_loot.items()},
            "median_round_trip_seconds": round(median_rt, 2),
            "current_fleet_slots": int(current_fleet_slots),
            "attacks_per_slot_per_hour": round(attacks_per_slot_per_hour, 4),
            "estimated_attacks_per_hour": round(estimated_attacks_per_hour, 4),
            "income_per_slot_per_hour": {r: round(income_per_slot_per_hour[r], 2) for r in RESOURCE_KEYS},
            "hourly_loot": {k: round(v, 2) for k, v in hourly_loot.items()},
        }
    )


# ---------------------------------------------------------------------------
# Path problem & event-driven simulator (ported from full_planner.ipynb §11)
# ---------------------------------------------------------------------------


def planet_hourly_output(totals: Sequence[float]) -> tuple[float, float, float, float, float, float, float]:
    """`(pig_iron_mined, pig_iron_left, metal, kryptonite, spice, power_ratio, smelting_ratio)`."""
    pig_iron, metal, kryptonite, spice, pig_iron_used, spice_used, energy_produced, energy_used = totals
    power_ratio = min(1.0, energy_produced / energy_used) if energy_used > 0.0 else 1.0
    mined = pig_iron * power_ratio
    wanted = pig_iron_used * power_ratio
    smelting_ratio = min(1.0, mined / wanted) if wanted > 0.0 else 1.0
    return (
        mined,
        mined - wanted * smelting_ratio,
        metal * power_ratio * smelting_ratio,
        kryptonite * power_ratio,
        spice * power_ratio - spice_used,
        power_ratio,
        smelting_ratio,
    )


def _path_requirements(key: str, building_owner: int) -> list[tuple[int, str, int]]:
    return [
        (building_owner if TECH_GRAPH.nodes[source]["category"] == "buildings" else -1, source, int(level))
        for source, level in TECH_GRAPH.prereqs_of[key]
    ]


def _extract_target_buildings(entry: Mapping[str, Any]) -> dict[str, int]:
    raw_buildings = entry.get("buildings")
    out: dict[str, int] = {}
    if isinstance(raw_buildings, Mapping):
        for k, v in raw_buildings.items():
            lvl = max(0, game_data.safe_int(v, 0))
            if lvl > 0:
                out[str(k)] = lvl
    elif isinstance(raw_buildings, Sequence):
        for row in raw_buildings:
            if not isinstance(row, Mapping):
                continue
            k = str(row.get("key") or "")
            lvl = max(0, game_data.safe_int(row.get("target_level"), 0))
            if k and lvl > 0:
                out[k] = lvl
    return out


def _match_live_planet(
    entry: Mapping[str, Any],
    owned_normal: Sequence[PlanetState],
    used_ids: set[int],
) -> Optional[PlanetState]:
    entry_pid = game_data.safe_optional_int(entry.get("planet_id"), None)
    if entry_pid is not None and entry_pid > 0:
        for p in owned_normal:
            if int(p.planet_id) == entry_pid and int(p.planet_id) not in used_ids:
                return p
    entry_coords = str(entry.get("coords") or "").strip()
    if entry_coords and not entry_coords.startswith("EMPTY:"):
        norm_entry = normalize_coords(entry_coords)
        for p in owned_normal:
            if int(p.planet_id) in used_ids:
                continue
            p_coords = str(p.coords or "").strip()
            if p_coords == entry_coords or (p_coords and normalize_coords(p_coords) == norm_entry):
                return p
    slot = game_data.safe_optional_int(entry.get("slot", entry.get("slot_index")), None)
    if slot is not None and 1 <= slot <= len(owned_normal):
        cand = owned_normal[slot - 1]
        if int(cand.planet_id) not in used_ids:
            return cand
    return None


def build_path_problem(
    state: GameState,
    target_config: Mapping[str, Any],
    *,
    pad_generic_colonies: bool = False,
    colony_founding_hours: float = 0.0,
) -> dict[str, Any]:
    """Build the causal step problem from live `state` to `target_config` (ZERO demolition steps)."""
    if not isinstance(target_config, Mapping) or target_config.get("schema_version") != 1:
        found = target_config.get("schema_version") if isinstance(target_config, Mapping) else type(target_config).__name__
        raise ValueError(f"target_config must be a schema_version=1 dict, got {found!r}")
    raw_planets = target_config.get("planets")
    if not isinstance(raw_planets, Sequence) or not raw_planets:
        raise ValueError("target_config['planets'] must be a non-empty sequence")

    speedup = _safe_speedup(
        state,
        {"speedup_factor": (target_config.get("inputs") or {}).get("universe_speedup_factor")},
    )
    owned_normal = [p for p in (state.planets or ()) if normalize_planet_type(p) == "normal"]

    research_now = {
        key: max(0, int((state.researches or {}).get(key, 0) or 0)) for key, _ in research_catalog()
    }
    raw_planned_res = target_config.get("planned_research_levels") or {}
    research_target = {
        key: max(research_now[key], max(0, int(raw_planned_res.get(key, 0) or 0)))
        for key in research_now
    }

    problem_planets: list[dict[str, Any]] = []
    used_live_ids: set[int] = set()
    for raw_entry in raw_planets:
        if not isinstance(raw_entry, Mapping):
            continue
        is_placeholder_entry = bool(
            raw_entry.get("is_placeholder", False)
            or str(raw_entry.get("planet_status") or "") == "empty_colony_slot"
            or game_data.safe_int(raw_entry.get("planet_id"), 0) < 0
        )
        live_planet = None if is_placeholder_entry else _match_live_planet(raw_entry, owned_normal, used_live_ids)
        if live_planet is None and is_placeholder_entry and not pad_generic_colonies:
            continue
        if live_planet is not None:
            used_live_ids.add(int(live_planet.planet_id))
            levels = {
                str(k): max(0, int(v or 0))
                for k, v in (live_planet.buildings or {}).items()
                if max(0, int(v or 0)) > 0
            }
            stationed_s9 = max(0, int((live_planet.ships or {}).get("s9", 0) or 0))
            pid = int(live_planet.planet_id)
            p_coords = str(live_planet.coords)
            p_name = str(live_planet.name or live_planet.coords)
            temp_c = int(live_planet.temperature)
            fields_max = max(int(live_planet.fields_max), sum(levels.values()))
            founded = True
        else:
            if not pad_generic_colonies and owned_normal:
                continue
            levels = {}
            stationed_s9 = 0
            pid = game_data.safe_int(raw_entry.get("planet_id"), -(len(problem_planets) + 1))
            p_coords = str(raw_entry.get("coords") or f"EMPTY:{len(problem_planets) + 1:02d}")
            p_name = str(raw_entry.get("name") or f"Empty Colony Slot {len(problem_planets) + 1}")
            temp_c = game_data.safe_int(raw_entry.get("temperature", raw_entry.get("temperature_c")), 0)
            fields_max = max(1, game_data.safe_int(raw_entry.get("fields_max"), 300))
            founded = False

        raw_target = _extract_target_buildings(raw_entry)
        # HARD REQUIREMENT: never demolish — target level on every building is >= live built level
        target_levels = dict(raw_target)
        for bk, blvl in levels.items():
            if blvl > target_levels.get(bk, 0):
                target_levels[bk] = blvl

        slot_idx = game_data.safe_int(raw_entry.get("slot", raw_entry.get("slot_index")), len(problem_planets) + 1)
        problem_planets.append(
            {
                "index": len(problem_planets),
                "slot_index": slot_idx,
                "planet_id": pid,
                "coords": p_coords,
                "name": p_name,
                "planet_status": "colonized" if founded else "empty_colony_slot",
                "is_placeholder": not founded,
                "temperature_c": temp_c,
                "fields_max": fields_max,
                "founded": founded,
                "is_central_planet": bool(raw_entry.get("is_central_planet", False)),
                "pays_for_research": bool(raw_entry.get("pays_for_research", False)),
                "levels": levels,
                "target_levels": target_levels,
                "solar_cells": stationed_s9,
                "saved_production": dict(raw_entry.get("production") or {}),
                "missing_ships": {},
            }
        )

    if not problem_planets:
        raise ValueError("No matching planets between GameState and target_config")

    central_planet = next((p["index"] for p in problem_planets if p["is_central_planet"]), 0)
    problem_planets[central_planet]["is_central_planet"] = True
    lab_planet = next((p["index"] for p in problem_planets if p["pays_for_research"]), -1)
    if lab_planet < 0:
        best_lab = max(problem_planets, key=lambda p: (p["target_levels"].get("b8", 0), p["levels"].get("b8", 0)))
        if best_lab["target_levels"].get("b8", 0) > 0:
            lab_planet = int(best_lab["index"])
            problem_planets[lab_planet]["pays_for_research"] = True

    base_times: dict[str, dict[int, float]] = {}

    def base_hours(key: str, level: int) -> float:
        if key not in base_times:
            info = game_data.BUILDING_BY_ID[int(key[1:])] if key.startswith("b") else game_data.RESEARCH_BY_ID[key]
            levels_map = info.get("levels") or {}
            top_level = max(int(info.get("max_table_level") or len(levels_map)), level)
            base_times[key] = build_monotonic_base_times(levels_map, top_level)
        return float(base_times[key].get(level, 0.0)) / 3600.0

    def cost_tuple(raw_cost: Any, count: int = 1) -> tuple[float, float, float, float]:
        cost = full_cost(raw_cost)
        return (
            float(count) * float(cost["pig_iron"]),
            float(count) * float(cost["metal"]),
            float(count) * float(cost["kryptonite"]),
            float(count) * float(cost["spice"]),
        )

    steps: list[dict[str, Any]] = []

    def add_step(**step: Any) -> None:
        step["id"] = len(steps)
        step["cost_spice_eq"] = float(game_data.spice_equivalent(dict(zip(RESOURCE_KEYS, step["cost"]))))
        step["warehouse_levels"] = tuple(warehouse_level_for(amount) for amount in step["cost"])
        steps.append(step)

    planet_count = len(problem_planets)
    for planet in problem_planets:
        index = planet["index"]
        for building_id in sorted(game_data.BUILDING_BY_ID):
            key = f"b{building_id}"
            name = str(game_data.BUILDING_BY_ID[building_id]["name"])
            built = planet["levels"].get(key, 0)
            wanted = planet["target_levels"].get(key, 0)
            for level in range(built + 1, wanted + 1):
                add_step(
                    kind="build",
                    planet=index,
                    key=key,
                    name=name,
                    level_from=level - 1,
                    level_to=level,
                    cost=cost_tuple(game_data.building_cost(building_id, level)),
                    base_hours=base_hours(key, level),
                    queue=index,
                    pay_planet=index,
                    requires=_path_requirements(key, index),
                    chain=("build", index, key),
                )

    for key, info in research_catalog():
        for level in range(research_now[key] + 1, research_target[key] + 1):
            if lab_planet < 0:
                raise ValueError(
                    f"{info['name']} {level} is planned, but no planet in target_config has a Research lab (b8)"
                )
            add_step(
                kind="research",
                planet=-1,
                key=key,
                name=str(info["name"]),
                level_from=level - 1,
                level_to=level,
                cost=cost_tuple(((info.get("levels") or {}).get(level) or {}).get("cost")),
                base_hours=base_hours(key, level),
                queue=planet_count,
                pay_planet=lab_planet,
                requires=_path_requirements(key, lab_planet),
                chain=("research", -1, key),
            )

    colony_ship = game_data.SHIPS[SHIP_SLUG_BY_KEY["s8"]]
    for planet in problem_planets:
        if not planet["founded"]:
            add_step(
                kind="colonize",
                planet=planet["index"],
                key="s8",
                name=str(colony_ship["name"]),
                level_from=0,
                level_to=1,
                cost=cost_tuple(colony_ship.get("base_cost")),
                base_hours=max(0.0, float(colony_founding_hours)),
                queue=-1,
                pay_planet=central_planet,
                requires=[],
                chain=("colonize", planet["index"], "s8"),
            )

    # Validate reachability of prerequisites & warehouses (auto-raising warehouse target only if live state grew past stale config)
    for step in steps:
        label = f"{step['name']} {step['level_to']}" + (
            f" on {problem_planets[step['planet']]['coords']}" if step["planet"] >= 0 else ""
        )
        for owner, req_key, req_lvl in step["requires"]:
            final_level = (
                research_target.get(req_key, 0)
                if owner < 0
                else problem_planets[owner]["target_levels"].get(req_key, 0)
            )
            if final_level < req_lvl:
                where = "planned research" if owner < 0 else f"plan of {problem_planets[owner]['coords']}"
                raise ValueError(
                    f"{label} needs {TECH_GRAPH.nodes[req_key]['name']} {req_lvl}, but {where} ends at {final_level}"
                )
        pay_levels = problem_planets[step["pay_planet"]]["target_levels"]
        for resource, wh_key, needed in zip(RESOURCE_KEYS, WAREHOUSE_KEYS, step["warehouse_levels"]):
            if needed > pay_levels.get(wh_key, 0):
                raise ValueError(
                    f"{label} costs more {resource} than {TECH_GRAPH.nodes[wh_key]['name']} on "
                    f"{problem_planets[step['pay_planet']]['coords']} holds at target level "
                    f"{pay_levels.get(wh_key, 0)} (level {needed} needed)"
                )

    # HARD REQUIREMENT: sum live planet resources across owned planets into start_stock
    stock = [0.0, 0.0, 0.0, 0.0]
    for planet in state.planets or ():
        for pos, resource in enumerate(RESOURCE_KEYS):
            stock[pos] += max(0.0, float(getattr(planet.resources, resource, 0) or 0))

    return {
        "speedup_factor": speedup,
        "planets": problem_planets,
        "research_levels": research_now,
        "research_target": research_target,
        "lab_planet": lab_planet,
        "central_planet": central_planet,
        "start_stock": tuple(stock),
        "steps": steps,
    }


class EmpirePathSimulator:
    """Event-driven W-week empire rollout simulator matching `full_planner.ipynb` §11.4."""

    def __init__(
        self,
        problem: Mapping[str, Any],
        attack_income: Mapping[str, float],
        *,
        slot_attack_income: Mapping[str, float] | None = None,
        fleet_command_center_level: Optional[int] = None,
        spare_percent: Mapping[str, float] | None = None,
        reward_weeks: int = 26,
        weekly_discount: float = 0.90,
    ) -> None:
        if int(reward_weeks) < 1:
            raise ValueError(f"reward_weeks must be at least 1, got {reward_weeks!r}")
        if not (0.0 < float(weekly_discount) <= 1.0):
            raise ValueError(f"weekly_discount must be in (0, 1], got {weekly_discount!r}")
        if fleet_command_center_level is not None and int(fleet_command_center_level) < 0:
            raise ValueError(
                f"fleet_command_center_level must be >= 0 or None, got {fleet_command_center_level!r}"
            )
        if spare_percent is not None:
            if set(spare_percent) != set(RESOURCE_KEYS):
                raise ValueError(f"spare_percent must set {RESOURCE_KEYS}, got {sorted(spare_percent)}")
            for resource, pct in spare_percent.items():
                if not (0.0 <= float(pct) < 100.0):
                    raise ValueError(f"spare_percent[{resource!r}] must be in [0, 100), got {pct!r}")

        self.spare_percent = tuple(
            float(spare_percent[resource]) if spare_percent is not None else 0.0 for resource in RESOURCE_KEYS
        )
        self.keep = tuple(1.0 - pct / 100.0 for pct in self.spare_percent)
        self.fleet_command_center_level = (
            None if fleet_command_center_level is None else int(fleet_command_center_level)
        )
        self.reward_weeks = int(reward_weeks)
        self.weekly_discount = float(weekly_discount)
        self.horizon_hours = PATH_HOURS_PER_WEEK * self.reward_weeks
        self.week_weights = tuple(self.weekly_discount**week for week in range(self.reward_weeks))
        suffix = [0.0] * (self.reward_weeks + 1)
        for week in range(self.reward_weeks - 1, -1, -1):
            suffix[week] = suffix[week + 1] + PATH_HOURS_PER_WEEK * self.week_weights[week]
        self.suffix_discounted_hours = tuple(suffix)
        self.merchant_rates = tuple(
            1.0 / float(game_data.MERCHANT_RATES_IN_SPICE[resource]) for resource in RESOURCE_KEYS
        )
        self.problem = problem
        self.planets = problem["planets"]
        self.steps = problem["steps"]
        self.speedup = float(problem["speedup_factor"])
        self.fixed_attack = tuple(float(attack_income.get(resource, 0.0)) for resource in RESOURCE_KEYS)
        self.slot_attack = tuple(
            float(slot_attack_income.get(resource, 0.0)) if slot_attack_income is not None else 0.0
            for resource in RESOURCE_KEYS
        )
        start_slots = sum(
            1
            + (
                planet["levels"].get("b10", 0)
                if self.fleet_command_center_level is None
                else self.fleet_command_center_level
            )
            for planet in self.planets
            if planet["founded"]
        )
        self.attack = tuple(self.fixed_attack[pos] + start_slots * self.slot_attack[pos] for pos in range(4))
        self.lab_planet = int(problem["lab_planet"])
        self.central_planet = int(problem["central_planet"])
        self.planet_count = len(self.planets)
        self.research_queue = self.planet_count
        self.queue_count = 2 * self.planet_count + 1
        self.temperature = [int(planet["temperature_c"]) for planet in self.planets]
        self.fields_max = [int(planet["fields_max"]) for planet in self.planets]

        self.kind = [step["kind"] for step in self.steps]
        self.step_planet = [int(step["planet"]) for step in self.steps]
        self.key = [step["key"] for step in self.steps]
        self.level_to = [int(step["level_to"]) for step in self.steps]
        self.cost = [tuple(step["cost"]) for step in self.steps]
        self.cost_floor = [tuple(amount - (1e-9 * amount + 1e-6) for amount in step["cost"]) for step in self.steps]
        self.cost_spice_eq = [float(step["cost_spice_eq"]) for step in self.steps]
        self.base_hours = [float(step["base_hours"]) for step in self.steps]
        self.queue = [int(step["queue"]) for step in self.steps]
        self.pay_planet = [int(step["pay_planet"]) for step in self.steps]
        self.requires = [tuple(step["requires"]) for step in self.steps]
        self.warehouse_levels = [tuple(step["warehouse_levels"]) for step in self.steps]

        chains: dict[tuple[str, int, str], list[int]] = {}
        for step in self.steps:
            chains.setdefault(tuple(step["chain"]), []).append(int(step["id"]))
        self.chain_steps = list(chains.values())
        self.step_chain = [0] * len(self.steps)
        self.build_chains: list[dict[str, int]] = [{} for _ in self.planets]
        self.demolish_chains: list[list[int]] = [[] for _ in self.planets]
        self.ship_chains: list[list[int]] = [[] for _ in self.planets]
        self.colonize_step = [-1] * self.planet_count
        self.research_chains: dict[str, int] = {}
        for chain_index, ((kind, planet, key), members) in enumerate(chains.items()):
            for member in members:
                self.step_chain[member] = chain_index
            if kind == "build":
                self.build_chains[planet][key] = chain_index
            elif kind == "demolish":
                self.demolish_chains[planet].append(chain_index)
            elif kind == "ships":
                self.ship_chains[planet].append(chain_index)
            elif kind == "colonize":
                self.colonize_step[planet] = members[0]
            else:
                self.research_chains[key] = chain_index

        self.research_dependents: dict[str, set[int]] = {}
        for step in self.steps:
            owner = self.research_queue if step["planet"] < 0 else int(step["planet"])
            for required_owner, key, _level in step["requires"]:
                if required_owner < 0:
                    self.research_dependents.setdefault(key, set()).add(owner)

        self._flow_cache: dict[tuple[str, int, int, int], tuple[float, ...]] = {}
        self._solar_cache: dict[tuple[int, int], float] = {}
        self._accelerator_cache: dict[tuple[int, int, int], float] = {}

        final_research = problem["research_target"]
        self.final_levels = [dict(planet["target_levels"]) for planet in self.planets]
        self.final_solar = [
            int(planet["solar_cells"]) + int(planet["missing_ships"].get("s9", 0)) for planet in self.planets
        ]
        self.final_building_divisor = [
            building_time_divisor(levels.get("b7", 0), levels.get("b17", 0), self.speedup)
            for levels in self.final_levels
        ]
        self.final_shipyard_divisor = [
            shipyard_time_divisor(levels.get("b9", 0), levels.get("b17", 0), self.speedup)
            for levels in self.final_levels
        ]
        self.final_research_divisor = research_time_divisor(
            self.final_levels[self.lab_planet].get("b8", 0) if self.lab_planet >= 0 else 0, self.speedup
        )
        self.final_output = [
            planet_hourly_output(
                self.planet_totals(planet, levels, self.final_solar[planet], final_research["r1"], final_research["r2"])
            )
            for planet, levels in enumerate(self.final_levels)
        ]
        self.final_fleet_slots = self.fleet_slots_of(self.final_levels, [True] * self.planet_count)
        self.final_supply = self.supply_of(self.final_output, [True] * self.planet_count, self.final_fleet_slots)
        self.final_output_total = tuple(
            sum(output[position] for output in self.final_output) for position in (1, 2, 3, 4)
        )
        self.final_net_output_total = tuple(self.keep[pos] * self.final_output_total[pos] for pos in range(4))

        self.in_horizon = [False] * len(self.steps)
        horizon_need = [0.0, 0.0, 0.0, 0.0]
        for step in range(len(self.steps)):
            planet, kind = self.step_planet[step], self.kind[step]
            divisor = (
                1.0
                if kind == "colonize"
                else self.final_research_divisor
                if kind == "research"
                else self.final_shipyard_divisor[planet]
                if kind == "ships"
                else self.final_building_divisor[planet]
            )
            if self.base_hours[step] / divisor <= self.horizon_hours:
                self.in_horizon[step] = True
                for position in range(4):
                    horizon_need[position] += self.cost[step][position]
        self.horizon_need_init = tuple(horizon_need)
        self.reward_ceiling_spice_eq_per_hour = self._reward_ceiling_per_hour()
        self.reward_upper_bound = self.reward_ceiling_spice_eq_per_hour * self.suffix_discounted_hours[0]

    def _reward_ceiling_per_hour(self) -> float:
        mining = max(self.problem["research_levels"].get("r1", 0), self.problem["research_target"].get("r1", 0))
        energy = max(self.problem["research_levels"].get("r2", 0), self.problem["research_target"].get("r2", 0))
        rates = self.merchant_rates
        total = 0.0
        for planet in range(self.planet_count):
            top = {
                key: max(self.planets[planet]["levels"].get(key, 0), self.final_levels[planet].get(key, 0))
                for key in PATH_MINE_KEYS
            }
            flows = {
                key: self.level_flows(key, level, self.temperature[planet], mining, energy)
                for key, level in top.items()
                if level > 0
            }
            mined = flows["b1"][0] if "b1" in flows else 0.0
            smelted = min(flows["b2"][1], mined / 2.0) if "b2" in flows else 0.0
            kryptonite = flows["b3"][2] if "b3" in flows else 0.0
            spice = flows["b4"][3] if "b4" in flows else 0.0
            keep = self.keep
            smelt_bonus = max(0.0, smelted * (keep[1] * rates[1] - 2.0 * keep[0] * rates[0]))
            total += (
                mined * keep[0] * rates[0]
                + smelt_bonus
                + kryptonite * keep[2] * rates[2]
                + spice * keep[3] * rates[3]
            )
        return total

    def remaining_discounted_hours(self, hour: float) -> float:
        if hour >= self.horizon_hours:
            return 0.0
        if hour <= 0.0:
            return self.suffix_discounted_hours[0]
        week = int(hour // PATH_HOURS_PER_WEEK)
        if week >= self.reward_weeks:
            return 0.0
        in_week = (week + 1) * PATH_HOURS_PER_WEEK - hour
        return self.week_weights[week] * in_week + self.suffix_discounted_hours[week + 1]

    def level_flows(self, key: str, level: int, temperature: int, mining: int, energy: int) -> tuple[float, ...]:
        token = (key, level, temperature, mining if key in PATH_MINE_KEYS else energy)
        flows = self._flow_cache.get(token)
        if flows is None:
            context = {
                "speedup_factor": self.speedup,
                "planet_temperature_c": temperature,
                "r1_mining_tech": mining,
                "r2_energy_tech": energy,
            }
            flows = self._flow_cache[token] = tuple(building_level_flows(key, level, context))
        return flows

    def planet_totals(
        self, planet: int, levels: Mapping[str, int], solar_cells: int, mining: int, energy: int
    ) -> list[float]:
        temperature = self.temperature[planet]
        totals = [0.0] * len(PRODUCTION_FLOW_KEYS)
        for key in PATH_FLOW_BUILDING_KEYS:
            level = levels.get(key, 0)
            if level > 0:
                for position, flow in enumerate(self.level_flows(key, level, temperature, mining, energy)):
                    totals[position] += flow
        if solar_cells > 0:
            token = (temperature, energy)
            per_cell = self._solar_cache.get(token)
            if per_cell is None:
                per_cell = self._solar_cache[token] = solar_cell_energy_per_unit(temperature) * technology_multiplier(
                    "r2", energy
                )
            totals[6] += solar_cells * per_cell
        return totals

    def changed_totals(self, planet: int, changes: Mapping[str, int]) -> list[float]:
        totals = list(self.totals[planet])
        levels = self.levels[planet]
        temperature = self.temperature[planet]
        mining, energy = self.research["r1"], self.research["r2"]
        for key, new_level in changes.items():
            if key not in PATH_FLOW_BUILDING_KEYS:
                continue
            old_level = levels.get(key, 0)
            if old_level > 0:
                for position, flow in enumerate(self.level_flows(key, old_level, temperature, mining, energy)):
                    totals[position] -= flow
            if new_level > 0:
                for position, flow in enumerate(self.level_flows(key, new_level, temperature, mining, energy)):
                    totals[position] += flow
        return totals

    def fleet_slots_of(self, levels_list: Sequence[Mapping[str, int]], founded: Sequence[bool]) -> int:
        fixed = self.fleet_command_center_level
        return sum(
            1 + (levels_list[planet].get("b10", 0) if fixed is None else fixed)
            for planet in range(self.planet_count)
            if founded[planet]
        )

    def gross_attack_of(self, slots: int) -> tuple[float, float, float, float]:
        return (
            self.fixed_attack[0] + slots * self.slot_attack[0],
            self.fixed_attack[1] + slots * self.slot_attack[1],
            self.fixed_attack[2] + slots * self.slot_attack[2],
            self.fixed_attack[3] + slots * self.slot_attack[3],
        )

    def supply_of(
        self, outputs: Sequence[tuple[float, ...]], founded: Sequence[bool], slots: Optional[int] = None
    ) -> tuple[float, float, float, float]:
        active_slots = self.fleet_slots_of(self.levels, founded) if slots is None else int(slots)
        gross = list(self.gross_attack_of(active_slots))
        for output, is_founded in zip(outputs, founded):
            if is_founded:
                gross[0] += output[0]
                gross[1] += output[2]
                gross[2] += output[3]
                gross[3] += output[4]
        return (
            self.keep[0] * gross[0],
            self.keep[1] * gross[1],
            self.keep[2] * gross[2],
            self.keep[3] * gross[3],
        )

    def _refresh_planet(self, planet: int) -> None:
        self.totals[planet] = self.planet_totals(
            planet, self.levels[planet], self.solar[planet], self.research["r1"], self.research["r2"]
        )
        self.output[planet] = planet_hourly_output(self.totals[planet])

    def _refresh_rates(self) -> None:
        mined = left = metal = kry = spice = 0.0
        for planet in range(self.planet_count):
            if self.founded[planet]:
                out = self.output[planet]
                mined += out[0]
                left += out[1]
                metal += out[2]
                kry += out[3]
                spice += out[4]
        self.fleet_slots = self.fleet_slots_of(self.levels, self.founded)
        attack = self.gross_attack_of(self.fleet_slots)
        self.attack_now = attack
        keep = self.keep
        self.supply = (
            keep[0] * (mined + attack[0]),
            keep[1] * (metal + attack[1]),
            keep[2] * (kry + attack[2]),
            keep[3] * (spice + attack[3]),
        )
        self.supply_version += 1
        self.furnace_metal = metal
        self.rate_off = (
            keep[0] * (mined + attack[0]),
            keep[1] * attack[1],
            keep[2] * (kry + attack[2]),
            keep[3] * (spice + attack[3]),
        )
        self.rate_on = (
            keep[0] * (left + attack[0]),
            keep[1] * (metal + attack[1]),
            keep[2] * (kry + attack[2]),
            keep[3] * (spice + attack[3]),
        )

    def lower_bounds(self) -> list[tuple[str, float]]:
        self._load_start()
        count = self.planet_count
        least, _ = self.queue_work()
        founding = [
            self.base_hours[self.colonize_step[planet]] if self.colonize_step[planet] >= 0 else 0.0
            for planet in range(count)
        ]
        rows = [
            (f"building queue of {self.planets[planet]['coords']}", founding[planet] + least[planet])
            for planet in range(count)
        ]
        rows.append(("research queue", least[count]))
        rows.append(("shipyards", max((founding[planet] + least[count + 1 + planet] for planet in range(count)), default=0.0)))

        mining = max(self.problem["research_levels"].get("r1", 0), self.problem["research_target"].get("r1", 0))
        energy = max(self.problem["research_levels"].get("r2", 0), self.problem["research_target"].get("r2", 0))
        top_levels = [
            {
                key: max(self.levels[planet].get(key, 0), self.final_levels[planet].get(key, 0))
                for key in (*PATH_MINE_KEYS, "b10")
            }
            for planet in range(count)
        ]
        top_slots = self.fleet_slots_of(top_levels, [True] * count)
        top_attack = self.gross_attack_of(top_slots)
        ceiling = [0.0, 0.0, 0.0, 0.0]
        for planet in range(count):
            flows = {
                key: self.level_flows(key, level, self.temperature[planet], mining, energy)
                for key, level in top_levels[planet].items()
                if key in PATH_MINE_KEYS and level > 0
            }
            pig_iron = flows["b1"][0] if "b1" in flows else 0.0
            ceiling[0] += pig_iron
            ceiling[1] += min(flows["b2"][1], pig_iron / 2.0) if "b2" in flows else 0.0
            ceiling[2] += flows["b3"][2] if "b3" in flows else 0.0
            ceiling[3] += flows["b4"][3] if "b4" in flows else 0.0
        keep = self.keep
        metal_missing = max(0.0, self.need[1] - self.stock[1])
        missing = (
            max(0.0, self.need[0] - self.stock[0] + 2.0 * metal_missing),
            metal_missing,
            max(0.0, self.need[2] - self.stock[2]),
            max(0.0, self.need[3] - self.stock[3]),
        )
        smelt_extra = max(0.0, 2.0 * (keep[1] - keep[0]) * ceiling[1])
        supply = (
            keep[0] * (ceiling[0] + top_attack[0]) + 2.0 * keep[1] * top_attack[1] + smelt_extra,
            keep[1] * (ceiling[1] + top_attack[1]),
            keep[2] * (ceiling[2] + top_attack[2]),
            keep[3] * (ceiling[3] + top_attack[3]),
        )
        labels = ("Pig iron, with the Metal to smelt", "Metal", "Kryptonite", "Spice")
        for position in range(4):
            if missing[position] <= 0.0:
                hours = 0.0
            else:
                hours = missing[position] / supply[position] if supply[position] > 0.0 else PATH_NEVER
            rows.append((f"supply of {labels[position]}", hours))
        return rows

    def run(self, strategy: Any, *, horizon_hours: Optional[float] = None) -> dict[str, Any]:
        limit = self.horizon_hours if horizon_hours is None else float(horizon_hours)
        self._reset(strategy, limit_hours=limit)
        events = 0
        event_limit = 80 * len(self.steps) + 10_000
        status = "reached"
        while True:
            if self.clock < limit:
                self._decide()
            if self.unstarted == 0 and not self.heap:
                status = "reached"
                break
            if self.clock >= limit:
                status = "horizon"
                break
            until_done = self.heap[0][0] - self.clock if self.heap else PATH_NEVER
            until_stock = self._hours_to_stock_event()
            until_review = PATH_NEVER
            if self.unstarted > 0 and (self.heap or until_stock < PATH_NEVER) and self.review_at < PATH_NEVER:
                until_review = max(1e-9, self.review_at - self.clock)
            elapsed = min(until_done, until_stock, until_review)
            if elapsed == PATH_NEVER:
                if self.reserved < 0 and limit < PATH_NEVER:
                    self._advance(max(0.0, limit - self.clock), ends_a_step=False)
                    status = "reached" if (self.unstarted == 0 and not self.heap) else "horizon"
                else:
                    status = "stuck"
                break
            until_limit = limit - self.clock
            if until_limit < elapsed:
                self._advance(max(0.0, until_limit), ends_a_step=False)
                status = "reached" if (self.unstarted == 0 and not self.heap) else "horizon"
                break
            events += 1
            if events > event_limit:
                raise RuntimeError(f"Path simulation did not finish within {event_limit} events")
            self._advance(max(0.0, elapsed), ends_a_step=elapsed == until_done)

        if self.clock < self.horizon_hours:
            self._accrue_weekly(self.clock, self.horizon_hours - self.clock)
        rates = self.merchant_rates
        weekly_resources = [tuple(row) for row in self.weekly_resources]
        weekly_spice_eq = tuple(
            row[0] * rates[0] + row[1] * rates[1] + row[2] * rates[2] + row[3] * rates[3]
            for row in weekly_resources
        )
        discounted_reward = float(
            sum(weight * amount for weight, amount in zip(self.week_weights, weekly_spice_eq))
        )
        completed = sum(1 for end in self.end if end is not None and end <= self.clock)
        started_count = len(self.steps) - self.unstarted
        return {
            "status": status,
            "hours": self.clock if status == "reached" else PATH_NEVER,
            "stopped_at_hours": self.clock,
            "order": list(self.order),
            "start": list(self.start),
            "end": list(self.end),
            "steps_started": started_count,
            "steps_completed": completed,
            "steps_running": started_count - completed,
            "waiting_for_step": self.reserved if status == "stuck" else -1,
            "metal_smelted": self.metal_smelted,
            "smelting_spans": [tuple(span) for span in self.smelting_spans],
            "events": events,
            "reviews": self.reviews,
            "final_stock": tuple(self.stock),
            "final_levels": [dict(levels) for levels in self.levels],
            "final_research": dict(self.research),
            "final_solar": list(self.solar),
            "final_founded": list(self.founded),
            "start_fleet_slots": int(self.start_fleet_slots),
            "final_fleet_slots": int(self.fleet_slots),
            "loot_collected": tuple(self.loot_collected),
            "spare_diverted": tuple(self.spare_diverted),
            "weekly_resources": weekly_resources,
            "weekly_spice_eq": weekly_spice_eq,
            "discounted_reward": discounted_reward,
            "total_spice_eq_produced": float(sum(weekly_spice_eq)),
        }

    def _reset(self, strategy: Any, *, limit_hours: Optional[float] = None) -> None:
        self._load_start()
        self.run_limit_hours = self.horizon_hours if limit_hours is None else float(limit_hours)
        self.strategy = strategy
        strategy.begin(self)
        self._review()

    def _load_start(self) -> None:
        count = self.planet_count
        self.clock = 0.0
        self.stock = [float(amount) for amount in self.problem["start_stock"]]
        self.levels = [dict(planet["levels"]) for planet in self.planets]
        self.research = dict(self.problem["research_levels"])
        self.solar = [int(planet["solar_cells"]) for planet in self.planets]
        self.founded = [bool(planet["founded"]) for planet in self.planets]
        self.fields_used = [sum(levels.values()) for levels in self.levels]
        self.next_step = [0] * len(self.chain_steps)
        self.queue_step = [-1] * self.queue_count
        self.queue_end = [0.0] * self.queue_count
        self.heap: list[tuple[float, int]] = []
        self.started = [False] * len(self.steps)
        self.order: list[int] = []
        self.start: list[Optional[float]] = [None] * len(self.steps)
        self.end: list[Optional[float]] = [None] * len(self.steps)
        self.unstarted = len(self.steps)
        self.need = [sum(cost[position] for cost in self.cost) for position in range(4)]
        self.work_accelerators = [0.0] * count
        self.work_buildings = [0.0] * count
        self.work_ships = [0.0] * count
        self.work_research = 0.0
        for step in range(len(self.steps)):
            self._count_work(step, 1.0)
        self.supply_version = 0
        self.totals = [[0.0] * len(PRODUCTION_FLOW_KEYS) for _ in range(count)]
        self.output: list[tuple[float, ...]] = [(0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0)] * count
        for planet in range(count):
            self._refresh_planet(planet)
        self._refresh_rates()
        self.reserved = -1
        self.smelting = False
        self.metal_smelted = 0.0
        self.smelting_spans: list[list[float]] = []
        self.reviews = 0
        self.completions_since_review = 0
        self.review_at = PATH_NEVER
        self.review_every = 1
        self.dirty = set(range(count + 1))
        self.run_limit_hours = self.horizon_hours
        self.horizon_need = list(self.horizon_need_init)
        self.weekly_resources = [[0.0, 0.0, 0.0, 0.0] for _ in range(self.reward_weeks)]
        self.loot_collected = [0.0, 0.0, 0.0, 0.0]
        self.spare_diverted = [0.0, 0.0, 0.0, 0.0]
        self.start_fleet_slots = self.fleet_slots

    def _count_work(self, step: int, sign: float) -> None:
        kind, planet = self.kind[step], self.step_planet[step]
        if kind == "research":
            self.work_research += sign * self.base_hours[step]
        elif kind == "ships":
            self.work_ships[planet] += sign * self.base_hours[step]
        elif kind == "build" and self.key[step] in PATH_ACCELERATOR_KEYS:
            self.work_accelerators[planet] += sign * self.base_hours[step]
        elif kind in ("build", "demolish"):
            self.work_buildings[planet] += sign * self.base_hours[step]

    def accelerator_hours(self, planet: int) -> float:
        pending: list[list[int]] = []
        for key in PATH_ACCELERATOR_KEYS:
            chain = self.build_chains[planet].get(key)
            pending.append(self.chain_steps[chain][self.next_step[chain] :] if chain is not None else [])
        centers, microsystems = pending
        if not centers and not microsystems:
            return 0.0
        token = (planet, len(centers), len(microsystems))
        cached = self._accelerator_cache.get(token)
        if cached is not None:
            return cached
        center_level = self.level_to[centers[0]] - 1 if centers else self.final_levels[planet].get("b7", 0)
        microsystem_level = (
            self.level_to[microsystems[0]] - 1 if microsystems else self.final_levels[planet].get("b17", 0)
        )
        best = [[PATH_NEVER] * (len(microsystems) + 1) for _ in range(len(centers) + 1)]
        best[0][0] = 0.0
        for done_centers in range(len(centers) + 1):
            for done_microsystems in range(len(microsystems) + 1):
                hours = best[done_centers][done_microsystems]
                divisor = building_time_divisor(
                    center_level + done_centers, microsystem_level + done_microsystems, self.speedup
                )
                if done_centers < len(centers):
                    longer = hours + self.base_hours[centers[done_centers]] / divisor
                    if longer < best[done_centers + 1][done_microsystems]:
                        best[done_centers + 1][done_microsystems] = longer
                if done_microsystems < len(microsystems):
                    longer = hours + self.base_hours[microsystems[done_microsystems]] / divisor
                    if longer < best[done_centers][done_microsystems + 1]:
                        best[done_centers][done_microsystems + 1] = longer
        self._accelerator_cache[token] = best[-1][-1]
        return best[-1][-1]

    def queue_work(self) -> tuple[list[float], list[float]]:
        count = self.planet_count
        least = [0.0] * self.queue_count
        for queue in range(self.queue_count):
            if self.queue_step[queue] >= 0:
                least[queue] = max(0.0, self.queue_end[queue] - self.clock)
        slow = list(least)
        for planet in range(count):
            levels = self.levels[planet]
            divisor = building_time_divisor(levels.get("b7", 0), levels.get("b17", 0), self.speedup)
            least[planet] += (
                self.accelerator_hours(planet) + self.work_buildings[planet] / self.final_building_divisor[planet]
            )
            slow[planet] += (self.work_accelerators[planet] + self.work_buildings[planet]) / divisor
            yard = count + 1 + planet
            least[yard] += self.work_ships[planet] / self.final_shipyard_divisor[planet]
            slow[yard] += self.work_ships[planet] / shipyard_time_divisor(
                levels.get("b9", 0), levels.get("b17", 0), self.speedup
            )
        least[count] += self.work_research / self.final_research_divisor
        lab_level = self.levels[self.lab_planet].get("b8", 0) if self.lab_planet >= 0 else 0
        slow[count] += self.work_research / research_time_divisor(lab_level, self.speedup)
        return least, slow

    def missing_resources(self) -> tuple[float, float, float, float]:
        metal = max(0.0, self.need[1] - self.stock[1])
        return (
            max(0.0, self.need[0] + 2.0 * metal - self.stock[0]),
            metal,
            max(0.0, self.need[2] - self.stock[2]),
            max(0.0, self.need[3] - self.stock[3]),
        )

    def horizon_missing_resources(self) -> tuple[float, float, float, float]:
        metal = max(0.0, self.horizon_need[1] - self.stock[1])
        return (
            max(0.0, self.horizon_need[0] + 2.0 * metal - self.stock[0]),
            metal,
            max(0.0, self.horizon_need[2] - self.stock[2]),
            max(0.0, self.horizon_need[3] - self.stock[3]),
        )

    def _accrue_weekly(self, start_hour: float, elapsed: float) -> None:
        if elapsed <= 0.0 or start_hour >= self.horizon_hours:
            return
        end_hour = min(self.horizon_hours, start_hour + elapsed)
        g0 = g1 = g2 = g3 = 0.0
        for planet in range(self.planet_count):
            if self.founded[planet]:
                made = self.output[planet]
                g0 += made[1]
                g1 += made[2]
                g2 += made[3]
                g3 += made[4]
        keep = self.keep
        r0, r1, r2, r3 = keep[0] * g0, keep[1] * g1, keep[2] * g2, keep[3] * g3
        week = int(start_hour // PATH_HOURS_PER_WEEK)
        cursor = start_hour
        while cursor < end_hour and week < self.reward_weeks:
            boundary = min(end_hour, (week + 1) * PATH_HOURS_PER_WEEK)
            span = boundary - cursor
            if span > 0.0:
                bucket = self.weekly_resources[week]
                bucket[0] += r0 * span
                bucket[1] += r1 * span
                bucket[2] += r2 * span
                bucket[3] += r3 * span
            cursor = boundary
            week += 1

    def duration(self, step: int) -> float:
        kind, planet = self.kind[step], self.step_planet[step]
        if kind == "colonize":
            return self.base_hours[step]
        if kind == "research":
            return self.base_hours[step] / research_time_divisor(
                self.levels[self.lab_planet].get("b8", 0) if self.lab_planet >= 0 else 0,
                self.speedup,
            )
        levels = self.levels[planet]
        if kind == "ships":
            return self.base_hours[step] / shipyard_time_divisor(
                levels.get("b9", 0), levels.get("b17", 0), self.speedup
            )
        return self.base_hours[step] / building_time_divisor(
            levels.get("b7", 0), levels.get("b17", 0), self.speedup
        )

    def blockers(self, step: int) -> list[tuple]:
        blockers: list[tuple] = []
        kind, planet = self.kind[step], self.step_planet[step]
        if planet >= 0 and kind != "colonize" and not self.founded[planet]:
            blockers.append(("colony", planet))
        for owner, key, level in self.requires[step]:
            have = self.research.get(key, 0) if owner < 0 else self.levels[owner].get(key, 0)
            if have < level:
                blockers.append(("level", owner, key, level))
        pay_planet = self.pay_planet[step]
        pay_levels = self.levels[pay_planet]
        for position, needed in enumerate(self.warehouse_levels[step]):
            if needed > 0 and needed > pay_levels.get(WAREHOUSE_KEYS[position], 0):
                blockers.append(("storage", pay_planet, position, needed))
        if kind == "build" and self.fields_used[planet] >= self.fields_max[planet]:
            blockers.append(("field", planet))
        return blockers

    def is_next_of_its_chain(self, step: int) -> bool:
        chain = self.step_chain[step]
        position = self.next_step[chain]
        return position < len(self.chain_steps[chain]) and self.chain_steps[chain][position] == step

    def wait_hours(self, stock: Sequence[float], step: int) -> float:
        cost, floor = self.cost[step], self.cost_floor[step]
        wait = 0.0
        for position in (2, 3):
            if stock[position] < floor[position]:
                rate = self.rate_off[position]
                if rate <= 0.0:
                    return PATH_NEVER
                wait = max(wait, (cost[position] - stock[position]) / rate)
        pig_iron_lacking = cost[0] - stock[0]
        smelting_hours = 0.0
        if stock[1] < floor[1]:
            if self.rate_on[1] <= 0.0:
                return PATH_NEVER
            smelting_hours = (cost[1] - stock[1]) / self.rate_on[1]
            pig_iron_lacking -= self.rate_on[0] * smelting_hours
        if pig_iron_lacking > cost[0] - floor[0]:
            if self.rate_off[0] <= 0.0:
                return PATH_NEVER
            smelting_hours += pig_iron_lacking / self.rate_off[0]
        return max(wait, smelting_hours)

    def _hours_to_stock_event(self) -> float:
        if self.reserved < 0:
            return PATH_NEVER
        cost, floor = self.cost[self.reserved], self.cost_floor[self.reserved]
        if self.smelting:
            rate = self.rate_on[1]
            return (cost[1] - self.stock[1]) / rate if rate > 0.0 else PATH_NEVER
        wait = 0.0
        for position in (0, 2, 3):
            if self.stock[position] < floor[position]:
                rate = self.rate_off[position]
                if rate <= 0.0:
                    return PATH_NEVER
                wait = max(wait, (cost[position] - self.stock[position]) / rate)
        return wait

    def _advance(self, elapsed: float, *, ends_a_step: bool) -> None:
        if elapsed > 0.0:
            self._accrue_weekly(self.clock, elapsed)
            rates = self.rate_on if self.smelting else self.rate_off
            gross_attack = self.attack_now
            keep = self.keep
            for position in range(4):
                self.loot_collected[position] += keep[position] * gross_attack[position] * elapsed
                if keep[position] > 0.0:
                    gross_rate = rates[position] / keep[position]
                    self.spare_diverted[position] += (1.0 - keep[position]) * gross_rate * elapsed
            for position in range(4):
                self.stock[position] += rates[position] * elapsed
            if self.smelting:
                self.metal_smelted += self.furnace_metal * elapsed
                if self.smelting_spans and self.smelting_spans[-1][1] == self.clock:
                    self.smelting_spans[-1][1] = self.clock + elapsed
                else:
                    self.smelting_spans.append([self.clock, self.clock + elapsed])
        self.clock = self.heap[0][0] if ends_a_step else self.clock + elapsed
        while self.heap and self.heap[0][0] <= self.clock:
            self._complete(heapq.heappop(self.heap)[1])
        if self.completions_since_review >= self.review_every or self.clock >= self.review_at:
            self._review()

    def _review(self) -> None:
        self.reviews += 1
        self.completions_since_review = 0
        horizon, self.review_every, look_again = self.strategy.review(self)
        if 0.0 < horizon < PATH_NEVER:
            self.review_at = self.clock + max(0.02 * horizon, 1e-3, 1e-9 * self.clock)
        else:
            self.review_at = PATH_NEVER
        if look_again:
            self.dirty.update(range(self.planet_count + 1))

    def _start(self, step: int) -> None:
        cost = self.cost[step]
        for position in range(4):
            self.stock[position] = max(0.0, self.stock[position] - cost[position])
            self.need[position] -= cost[position]
            if self.in_horizon[step]:
                self.horizon_need[position] = max(0.0, self.horizon_need[position] - cost[position])
        hours = self.duration(step)
        self.started[step] = True
        self.order.append(step)
        self.start[step] = self.clock
        self.end[step] = self.clock + hours
        heapq.heappush(self.heap, (self.clock + hours, step))
        self.unstarted -= 1
        self.next_step[self.step_chain[step]] += 1
        self._count_work(step, -1.0)
        queue, planet = self.queue[step], self.step_planet[step]
        if queue >= 0:
            self.queue_step[queue] = step
            self.queue_end[queue] = self.clock + hours
        if self.kind[step] == "build":
            self.fields_used[planet] += 1
        if planet < 0:
            self.dirty.add(self.research_queue)

    def _complete(self, step: int) -> None:
        kind, planet, key = self.kind[step], self.step_planet[step], self.key[step]
        if self.queue[step] >= 0:
            self.queue_step[self.queue[step]] = -1
        self.completions_since_review += 1
        if kind == "research":
            self.research[key] = self.level_to[step]
            self.dirty.add(self.research_queue)
            self.dirty.update(self.research_dependents.get(key, ()))
            if key in ("r1", "r2"):
                for other in range(self.planet_count):
                    self._refresh_planet(other)
                self._refresh_rates()
                self.dirty.update(range(self.planet_count))
            return
        if kind == "colonize":
            self.founded[planet] = True
            self._refresh_rates()
        elif kind == "ships":
            if key == "s9":
                self.solar[planet] += self.level_to[step]
                self._refresh_planet(planet)
                self._refresh_rates()
        else:
            if self.level_to[step] > 0:
                self.levels[planet][key] = self.level_to[step]
            else:
                self.levels[planet].pop(key, None)
            if kind == "demolish":
                self.fields_used[planet] -= 1
            if key in PATH_FLOW_BUILDING_KEYS:
                self._refresh_planet(planet)
                if self.founded[planet]:
                    self._refresh_rates()
            elif key == "b10" and self.founded[planet] and self.fleet_command_center_level is None:
                self._refresh_rates()
        self.dirty.add(planet)
        if planet == self.lab_planet:
            self.dirty.add(self.research_queue)
        if planet == self.central_planet:
            self.dirty.update(other for other in range(self.planet_count) if not self.founded[other])

    def _full_horizon_hours(self) -> float:
        work, _ = self.queue_work()
        missing = self.missing_resources()
        horizon = max(work) if work else 0.0
        for position in range(4):
            if missing[position] > 0.0:
                supply = self.final_supply[position]
                horizon = max(horizon, missing[position] / supply if supply > 0.0 else PATH_NEVER)
        return horizon

    def _decide(self) -> None:
        for _ in range(self.queue_count + 2):
            if not self._start_proposals(self.strategy.proposals(self, forced=False)):
                break
        if self.reserved < 0 and not self.heap and self.unstarted > 0:
            allow_forced = (
                self.clock == 0.0
                or self.run_limit_hours == PATH_NEVER
                or self._full_horizon_hours() <= self.run_limit_hours
            )
            if allow_forced:
                for _ in range(self.queue_count + 2):
                    if not self._start_proposals(self.strategy.proposals(self, forced=True)):
                        break

    def _start_proposals(self, proposals: list[tuple[tuple, int]]) -> bool:
        reserved = -1
        limit = 0.0
        started_any = False
        stock = self.stock
        patience = self.strategy.patience
        for _priority, step in proposals:
            queue = self.queue[step]
            if self.started[step] or (queue >= 0 and self.queue_step[queue] >= 0):
                continue
            floor = self.cost_floor[step]
            if stock[0] >= floor[0] and stock[1] >= floor[1] and stock[2] >= floor[2] and stock[3] >= floor[3]:
                if reserved >= 0:
                    hours = self.duration(step)
                    if queue >= 0 and queue == self.queue[reserved] and hours > limit:
                        continue
                    cost = self.cost[step]
                    delayed = self.wait_hours([stock[position] - cost[position] for position in range(4)], reserved)
                    if delayed > limit + patience * hours + 1e-9 * limit + 1e-9:
                        continue
                    if not self.is_next_of_its_chain(step) or self.blockers(step):
                        continue
                    limit = max(limit, delayed)
                elif not self.is_next_of_its_chain(step) or self.blockers(step):
                    continue
                self._start(step)
                started_any = True
            elif reserved < 0 and self.is_next_of_its_chain(step) and not self.blockers(step):
                reserved = step
                limit = self.wait_hours(stock, step)
        self.reserved = reserved
        self.smelting = reserved >= 0 and stock[1] < self.cost_floor[reserved][1]
        return started_any


# ---------------------------------------------------------------------------
# Discounted-Payback Strategy & 8-Gene Genetic Algorithm
# ---------------------------------------------------------------------------


class PathStrategy:
    """Discounted NPV payback / scarcity / b10 slot-loot strategy matching `full_planner.ipynb` §11.5."""

    def __init__(
        self,
        name: str,
        *,
        rule: str,
        values: str = "merchant",
        focus: float = 1.0,
        critical_share: float = 0.9,
        patience: float = 0.25,
        speed_weight: float = 1.5,
        bias: tuple[float, ...] = (1.0, 1.0, 1.0, 1.0),
        revalue_every_steps: int = 25,
    ) -> None:
        if rule not in ("cheapest", "payback") or values not in ("merchant", "scarcity"):
            raise ValueError(f"Unknown rule {rule!r} or values {values!r}")
        self.name = name
        self.rule = rule
        self.value_mode = values
        self.focus = float(focus)
        self.critical_share = float(critical_share)
        self.patience = float(patience)
        self.speed_weight = float(speed_weight)
        self.bias = tuple(float(value) for value in bias)
        self.revalue_every = max(1, int(revalue_every_steps))

    def parameters(self) -> dict[str, Any]:
        if self.rule == "cheapest":
            return {"rule": self.rule}
        return {
            "rule": self.rule,
            "values": self.value_mode,
            "focus": self.focus,
            "critical_share": self.critical_share,
            "patience": self.patience,
            "speed_weight": self.speed_weight,
            "bias": list(self.bias),
        }

    def begin(self, sim: EmpirePathSimulator) -> None:
        owners = sim.planet_count + 1
        self.forced = False
        self.views: dict[bool, dict[str, Any]] = {
            forced: {
                "entries": [[] for _ in range(owners)],
                "requests": [{} for _ in range(owners)],
                "dirty": set(range(owners)),
                "ranked": None,
            }
            for forced in (False, True)
        }
        self.entries: list[list[tuple[tuple, int]]] = self.views[False]["entries"]
        self.requests: list[dict[int, tuple]] = self.views[False]["requests"]
        self.research_gains: dict[str, tuple[int, tuple[float, ...]]] = {}
        self.critical = [False] * sim.queue_count
        self.needs_speed = [False] * sim.queue_count
        self.merchant_values = tuple(1.0 / float(game_data.MERCHANT_RATES_IN_SPICE[r]) for r in RESOURCE_KEYS)
        self.values = self.merchant_values
        self.income_value = 0.0
        self.horizon = PATH_NEVER
        self.colony_cache: dict[tuple, float] = {}

    def review(self, sim: EmpirePathSimulator) -> tuple[float, int, bool]:
        if self.rule == "cheapest":
            return PATH_NEVER, len(sim.steps) + 1, False
        work, slow_work = sim.queue_work()
        bounded = sim.run_limit_hours < PATH_NEVER
        missing = sim.horizon_missing_resources() if bounded else sim.missing_resources()
        full_missing = sim.missing_resources()
        full_horizon = max(work) if work else 0.0
        for position in range(4):
            if full_missing[position] > 0.0:
                supply = sim.final_supply[position]
                full_horizon = max(full_horizon, full_missing[position] / supply if supply > 0.0 else PATH_NEVER)
        rem_hours = max(0.0, sim.run_limit_hours - sim.clock) if bounded else full_horizon
        horizon = rem_hours if (bounded and rem_hours > 0.0) else full_horizon
        self.horizon = horizon
        measurable = 0.0 < horizon < PATH_NEVER
        can_finish_all = (not bounded) or (full_horizon <= sim.run_limit_hours)
        critical = [
            can_finish_all and measurable and hours > 0.0 and hours >= self.critical_share * horizon for hours in work
        ]
        needs_speed = [
            measurable and hours > 0.0 and hours >= self.critical_share * horizon for hours in slow_work
        ]

        supply_hours = [
            (missing[position] / sim.supply[position] if sim.supply[position] > 0.0 else PATH_NEVER)
            if missing[position] > 0.0
            else 0.0
            for position in range(4)
        ]
        starved = any(hours == PATH_NEVER for hours in supply_hours)
        if self.value_mode == "scarcity":
            longest = max((hours for hours in supply_hours if hours < PATH_NEVER), default=0.0)
            values = []
            for position in range(4):
                if supply_hours[position] == PATH_NEVER:
                    share = 1.0
                elif starved or longest <= 0.0:
                    share = 0.01 if starved else 1.0
                else:
                    share = max(0.01, supply_hours[position] / longest)
                values.append(self.bias[position] * (share**self.focus) / max(sim.supply[position], 1e-9))
        else:
            values = [self.bias[position] * self.merchant_values[position] for position in range(4)]
        income_value = sum(values[position] * sim.supply[position] for position in range(4))

        def moved(new: float, old: float) -> bool:
            return abs(new - old) > 0.15 * abs(old)

        changed = (
            self.income_value <= 0.0
            or critical != self.critical
            or needs_speed != self.needs_speed
            or moved(income_value, self.income_value)
            or any(moved(new, old) for new, old in zip(values, self.values))
        )
        if changed:
            self.critical, self.needs_speed = critical, needs_speed
            self.values, self.income_value = tuple(values), income_value
            self.colony_cache.clear()
        return horizon, (1 if starved else self.revalue_every), changed

    def proposals(self, sim: EmpirePathSimulator, *, forced: bool) -> list[tuple[tuple, int]]:
        if sim.dirty:
            for view in self.views.values():
                view["dirty"].update(sim.dirty)
            sim.dirty.clear()
        view = self.views[forced]
        if not view["dirty"] and view["ranked"] is not None:
            return view["ranked"]
        self.forced = forced
        self.entries, self.requests = view["entries"], view["requests"]
        for owner in sorted(view["dirty"]):
            if owner == sim.research_queue:
                self._scan_research(sim)
            else:
                self._scan_planet(sim, owner)
        view["dirty"].clear()
        wanted: dict[int, tuple] = {}
        for entries in self.entries:
            for priority, step in entries:
                if step not in wanted or priority > wanted[step]:
                    wanted[step] = priority
        for requests in self.requests:
            for step, priority in requests.items():
                if step not in wanted or priority > wanted[step]:
                    wanted[step] = priority
        view["ranked"] = sorted(((priority, step) for step, priority in wanted.items()), reverse=True)
        return view["ranked"]

    def _value(self, amounts: Sequence[float]) -> float:
        values = self.values
        return values[0] * amounts[0] + values[1] * amounts[1] + values[2] * amounts[2] + values[3] * amounts[3]

    def _gain(self, sim: EmpirePathSimulator, planet: int, changes: Mapping[str, int]) -> tuple[float, ...]:
        after = planet_hourly_output(sim.changed_totals(planet, changes))
        before = sim.output[planet]
        keep = sim.keep
        dl_b10 = 0
        if "b10" in changes and sim.fleet_command_center_level is None:
            dl_b10 = max(0, int(changes["b10"]) - sim.levels[planet].get("b10", 0))
        return tuple(
            keep[pos] * (after[pos + 1] - before[pos + 1] + dl_b10 * sim.slot_attack[pos]) for pos in range(4)
        )

    def _rank(
        self, sim: EmpirePathSimulator, added: float, price: float, urgent: float, hours: float
    ) -> float:
        if price <= 0.0:
            return PATH_NEVER if (added > 0.0 or urgent > 0.0) else 0.0
        if sim.run_limit_hours < PATH_NEVER:
            now_window = sim.remaining_discounted_hours(sim.clock)
            future_window = sim.remaining_discounted_hours(sim.clock + hours)
            if now_window > 0.0 and future_window <= 0.0:
                return 0.0
            window_factor = (future_window / now_window) if now_window > 0.0 else 1.0
            total_window = sim.suffix_discounted_hours[0]
            rem_share = (future_window / total_window) if total_window > 0.0 else 1.0
        else:
            window_factor = rem_share = 1.0
        rank = (added * window_factor) / (price + added * hours) if added > 0.0 else 0.0
        if urgent > 0.0:
            rank = max(rank, urgent * rem_share * self.income_value / price)
        return rank

    def _offer(
        self,
        sim: EmpirePathSimulator,
        step: int,
        gain: Sequence[float],
        urgent: float,
        entries: list[tuple[tuple, int]],
        requests: dict[int, tuple],
        *,
        extra_enablers: Optional[list[int]] = None,
        bundle_planet: int = -1,
        accelerators: Sequence[Any] = (),
    ) -> None:
        blockers = sim.blockers(step)
        enablers: dict[int, list[tuple]] = {}
        if blockers:
            self._collect_enablers(sim, blockers, enablers)
        for extra in extra_enablers or ():
            if extra not in enablers:
                enablers[extra] = sim.blockers(extra)
                if enablers[extra]:
                    self._collect_enablers(sim, enablers[extra], enablers)
        if blockers and not enablers:
            return
        cost = list(sim.cost[step])
        for enabler in enablers:
            enabler_cost = sim.cost[enabler]
            for position in range(4):
                cost[position] += enabler_cost[position]
        if enablers and bundle_planet >= 0:
            changes = {sim.key[step]: sim.level_to[step]}
            for enabler in enablers:
                if sim.kind[enabler] == "build" and sim.step_planet[enabler] == bundle_planet:
                    changes[sim.key[enabler]] = max(changes.get(sim.key[enabler], 0), sim.level_to[enabler])
            gain = self._gain(sim, bundle_planet, changes)
        added = self._value(gain)
        own_hours = other_hours = 0.0
        if added > 0.0 or urgent > 0.0:
            own_hours = sim.duration(step)
            queue = sim.queue[step]
            other_hours = sum(sim.duration(enabler) for enabler in enablers if sim.queue[enabler] == queue)
        price = self._value(cost)
        rank = self._rank(sim, added, price, urgent, own_hours + other_hours)
        if own_hours > 0.0 and added > 0.0:
            for accelerator, needs, faster, needs_price, needs_hours in accelerators:
                if accelerator in enablers:
                    continue
                faster_rank = self._rank(
                    sim, added, price + needs_price, urgent, own_hours / faster + other_hours + needs_hours
                )
                if faster_rank > rank:
                    rank = faster_rank
                    price += needs_price
                    other_hours += needs_hours
                    own_hours /= faster
                    enablers = {**enablers, **needs}
        if rank <= 0.0:
            return
        if not enablers:
            entries.append(((rank, -price, -step), step))
            return
        for enabler, enabler_blockers in enablers.items():
            if not enabler_blockers and sim.is_next_of_its_chain(enabler):
                priority = (rank, -price, -enabler)
                if enabler not in requests or priority > requests[enabler]:
                    requests[enabler] = priority

    def _collect_enablers(
        self, sim: EmpirePathSimulator, blockers: Sequence[tuple], enablers: dict[int, list[tuple]]
    ) -> None:
        added: list[int] = []
        for blocker in blockers:
            reason = blocker[0]
            candidates: list[int] = []
            if reason == "colony":
                step = sim.colonize_step[blocker[1]]
                if step >= 0 and not sim.started[step]:
                    candidates.append(step)
            elif reason == "field":
                # Zero auto-demolition invariant: never suggest demolishing a building to free fields.
                continue
            else:
                if reason == "level":
                    _, owner, key, level = blocker
                    chain = sim.research_chains.get(key) if owner < 0 else sim.build_chains[owner].get(key)
                else:
                    _, owner, position, level = blocker
                    chain = sim.build_chains[owner].get(WAREHOUSE_KEYS[position])
                if chain is not None:
                    for step in sim.chain_steps[chain][sim.next_step[chain] :]:
                        if sim.level_to[step] > level:
                            break
                        candidates.append(step)
            for step in candidates:
                if step not in enablers:
                    enablers[step] = sim.blockers(step)
                    added.append(step)
        for step in added:
            if enablers[step] and len(enablers) < 400:
                self._collect_enablers(sim, enablers[step], enablers)

    def _energy_steps(self, sim: EmpirePathSimulator, planet: int, totals: Sequence[float]) -> list[int]:
        energy_produced, energy_used = totals[6], totals[7]
        if energy_used <= energy_produced + 1e-9:
            return []
        chain = sim.build_chains[planet].get("b5")
        if chain is None or sim.next_step[chain] >= len(sim.chain_steps[chain]):
            return []
        temperature = sim.temperature[planet]
        mining, energy = sim.research["r1"], sim.research["r2"]
        standing = sim.levels[planet].get("b5", 0)
        standing_energy = sim.level_flows("b5", standing, temperature, mining, energy)[6] if standing > 0 else 0.0
        needed: list[int] = []
        for step in sim.chain_steps[chain][sim.next_step[chain] :]:
            needed.append(step)
            fusion_energy = sim.level_flows("b5", sim.level_to[step], temperature, mining, energy)[6]
            if energy_used <= energy_produced - standing_energy + fusion_energy + 1e-9:
                break
        return needed

    def _research_gain(self, sim: EmpirePathSimulator, key: str) -> tuple[float, ...]:
        token = sim.supply_version
        cached = self.research_gains.get(key)
        if cached is not None and cached[0] == token:
            return cached[1]
        mining = sim.research["r1"] + (1 if key == "r1" else 0)
        energy = sim.research["r2"] + (1 if key == "r2" else 0)
        outputs = [
            planet_hourly_output(sim.planet_totals(planet, sim.levels[planet], sim.solar[planet], mining, energy))
            for planet in range(sim.planet_count)
        ]
        gain = tuple(
            sim.keep[pos]
            * sum(outputs[p][pos + 1] - sim.output[p][pos + 1] for p in range(sim.planet_count) if sim.founded[p])
            for pos in range(4)
        )
        self.research_gains[key] = (token, gain)
        return gain

    def _colony_payback(self, sim: EmpirePathSimulator, planet: int, depth: int = 25) -> float:
        target = sim.planets[planet]["target_levels"]
        token = (sim.temperature[planet], tuple(sorted(target.items())), tuple(sorted(sim.levels[planet].items())))
        cached = self.colony_cache.get(token)
        if cached is not None:
            return cached
        mining, energy = sim.research["r1"], sim.research["r2"]
        solar = sim.solar[planet]
        values = self.values
        keep = sim.keep
        fixed_cc = sim.fleet_command_center_level

        def supply_value(levels: Mapping[str, int]) -> float:
            output = planet_hourly_output(sim.planet_totals(planet, levels, solar, mining, energy))
            slots = 1 + (levels.get("b10", 0) if fixed_cc is None else fixed_cc)
            return sum(
                values[pos] * keep[pos] * (output[pos + 1] + slots * sim.slot_attack[pos]) for pos in range(4)
            )

        def level_price(key: str, level: int) -> float:
            cost = full_cost(game_data.building_cost(int(key[1:]), level))
            return sum(values[position] * float(cost[resource]) for position, resource in enumerate(RESOURCE_KEYS))

        levels = dict(sim.levels[planet])
        ship = sim.colonize_step[planet]
        spent = self._value(sim.cost[ship]) if ship >= 0 else 0.0
        current = supply_value(levels)
        added = current
        best = (added / spent) if spent > 0.0 else 0.0
        for _ in range(depth):
            choice: Optional[tuple[float, dict[str, int], float, float]] = None
            for key in PATH_MINE_KEYS:
                level = levels.get(key, 0) + 1
                if level > target.get(key, 0):
                    continue
                trial = dict(levels)
                trial[key] = level
                price = level_price(key, level)
                while True:
                    totals = sim.planet_totals(planet, trial, solar, mining, energy)
                    fusion = trial.get("b5", 0) + 1
                    if totals[7] <= totals[6] + 1e-9 or fusion > target.get("b5", 0):
                        break
                    trial["b5"] = fusion
                    price += level_price("b5", fusion)
                value = supply_value(trial) - current
                ratio = value / price if price > 0.0 else 0.0
                if value > 0.0 and (choice is None or ratio > choice[0]):
                    choice = (ratio, trial, value, price)
            if choice is None:
                break
            _, levels, value, price = choice
            current += value
            added += value
            spent += price
            if spent > 0.0:
                best = max(best, added / spent)
        self.colony_cache[token] = best
        return best

    def _scan_planet(self, sim: EmpirePathSimulator, planet: int) -> None:
        entries: list[tuple[tuple, int]] = []
        requests: dict[int, tuple] = {}
        self.entries[planet], self.requests[planet] = entries, requests
        cheapest = self.rule == "cheapest"
        forced = self.forced
        nothing = (0.0, 0.0, 0.0, 0.0)

        if not sim.founded[planet]:
            step = sim.colonize_step[planet]
            if step < 0 or sim.started[step]:
                return
            blockers = sim.blockers(step)
            if cheapest:
                if not blockers:
                    entries.append(((-sim.cost_spice_eq[step], 0.0, -step), step))
                return
            price = self._value(sim.cost[step])
            colony_hours = sim.duration(step)
            if sim.run_limit_hours < PATH_NEVER and sim.clock + colony_hours >= sim.run_limit_hours and not forced:
                return
            forced_rank = self._rank(sim, 0.0, price, 1.0 if forced else 0.0, colony_hours)
            rank = max(self._colony_payback(sim, planet), forced_rank)
            if rank <= 0.0:
                return
            if not blockers:
                entries.append(((rank, -price, -step), step))
            else:
                enablers: dict[int, list[tuple]] = {}
                self._collect_enablers(sim, blockers, enablers)
                for enabler, enabler_blockers in enablers.items():
                    if not enabler_blockers and sim.is_next_of_its_chain(enabler):
                        requests[enabler] = (rank, -price, -enabler)
            return

        if cheapest:
            for chain in sim.build_chains[planet].values():
                position = sim.next_step[chain]
                if position < len(sim.chain_steps[chain]):
                    step = sim.chain_steps[chain][position]
                    if not sim.blockers(step):
                        entries.append(((-sim.cost_spice_eq[step], 0.0, -step), step))
            return

        urgent = 1.0 if (self.critical[planet] or forced) else 0.0
        needs_speed = self.needs_speed[planet]
        research_needs_speed = planet == sim.lab_planet and self.needs_speed[sim.research_queue]
        before = sim.output[planet]
        accelerators = []
        for key in PATH_ACCELERATOR_KEYS:
            chain = sim.build_chains[planet].get(key)
            if chain is None or sim.next_step[chain] >= len(sim.chain_steps[chain]):
                continue
            step = sim.chain_steps[chain][sim.next_step[chain]]
            needs = {step: sim.blockers(step)}
            if needs[step]:
                self._collect_enablers(sim, needs[step], needs)
            level = sim.level_to[step]
            accelerators.append(
                (
                    step,
                    needs,
                    2.0 if key == "b17" else (level + 1.0) / level,
                    sum(self._value(sim.cost[need]) for need in needs),
                    sum(sim.duration(need) for need in needs if sim.queue[need] == planet),
                )
            )

        for key, chain in sim.build_chains[planet].items():
            position = sim.next_step[chain]
            if position >= len(sim.chain_steps[chain]):
                continue
            step = sim.chain_steps[chain][position]
            level = sim.level_to[step]
            if key in PATH_FLOW_BUILDING_KEYS:
                totals = sim.changed_totals(planet, {key: level})
                after = planet_hourly_output(totals)
                gain = tuple(sim.keep[pos] * (after[pos + 1] - before[pos + 1]) for pos in range(4))
                extra = self._energy_steps(sim, planet, totals) if key in PATH_MINE_KEYS else None
                self._offer(
                    sim,
                    step,
                    gain,
                    urgent,
                    entries,
                    requests,
                    extra_enablers=extra,
                    bundle_planet=planet,
                    accelerators=accelerators,
                )
            elif key == "b10" and sim.fleet_command_center_level is None and any(x > 0.0 for x in sim.slot_attack):
                slot_gain = tuple(sim.keep[pos] * sim.slot_attack[pos] for pos in range(4))
                self._offer(
                    sim,
                    step,
                    slot_gain,
                    urgent,
                    entries,
                    requests,
                    bundle_planet=planet,
                    accelerators=accelerators,
                )
            elif key in PATH_ACCELERATOR_KEYS:
                saves = self.speed_weight * (0.5 if key == "b17" else 1.0 / (level + 1.0))
                speed_urgent = max(urgent, saves if needs_speed else 0.0)
                if speed_urgent > 0.0:
                    self._offer(sim, step, nothing, speed_urgent, entries, requests)
            elif key == "b8" and research_needs_speed:
                self._offer(sim, step, nothing, max(urgent, self.speed_weight / (level + 1.0)), entries, requests)
            elif urgent > 0.0:
                self._offer(sim, step, nothing, urgent, entries, requests)

    def _scan_research(self, sim: EmpirePathSimulator) -> None:
        owner = sim.research_queue
        entries: list[tuple[tuple, int]] = []
        requests: dict[int, tuple] = {}
        self.entries[owner], self.requests[owner] = entries, requests
        urgent = 1.0 if (self.critical[owner] or self.forced) else 0.0
        for key, chain in sim.research_chains.items():
            position = sim.next_step[chain]
            if position >= len(sim.chain_steps[chain]):
                continue
            step = sim.chain_steps[chain][position]
            if self.rule == "cheapest":
                if not sim.blockers(step):
                    entries.append(((-sim.cost_spice_eq[step], 0.0, -step), step))
                continue
            if key in ("r1", "r2"):
                self._offer(sim, step, self._research_gain(sim, key), urgent, entries, requests)
            elif urgent > 0.0:
                self._offer(sim, step, (0.0, 0.0, 0.0, 0.0), urgent, entries, requests)


def strategy_from_genes(name: str, genes: Sequence[float], revalue_every_steps: int) -> PathStrategy:
    focus, critical_share, patience, speed_weight, *value_genes = (float(gene) for gene in genes)
    return PathStrategy(
        name,
        rule="payback",
        values="scarcity",
        focus=focus,
        critical_share=critical_share,
        patience=patience,
        speed_weight=speed_weight,
        bias=tuple(2.0**gene for gene in value_genes),
        revalue_every_steps=revalue_every_steps,
    )


def path_fitness_key(run: Mapping[str, Any]) -> tuple[float, float]:
    return (float(run["discounted_reward"]), -float(run["hours"]))


def tune_path_strategy(
    simulator: EmpirePathSimulator,
    start_genes: Sequence[float],
    *,
    population_size: int,
    generations: int,
    tournament_size: int,
    crossover_rate: float,
    mutation_rate: float,
    elite_count: int,
    random_seed: int,
    revalue_every_steps: int,
    progress_cb: Optional[Callable[[str, float], None]] = None,
    progress_range: tuple[float, float] = (0.25, 0.90),
) -> tuple[np.ndarray, dict[str, Any], list[dict[str, Any]]]:
    """8-gene real-coded GA maximizing W-week net `discounted_reward`."""
    rng = np.random.default_rng(int(random_seed))
    low = np.array([bounds[0] for bounds in PATH_GENE_RANGES.values()], dtype=np.float64)
    high = np.array([bounds[1] for bounds in PATH_GENE_RANGES.values()], dtype=np.float64)
    size = max(2, int(population_size))
    elites = min(max(1, int(elite_count)), size - 1)
    results: dict[tuple[float, ...], dict[str, Any]] = {}

    def simulate(genes: np.ndarray) -> dict[str, Any]:
        token = tuple(round(float(gene), 9) for gene in genes)
        if token not in results:
            results[token] = simulator.run(strategy_from_genes("tuned", genes, revalue_every_steps))
        return results[token]

    def pick_parent(population: np.ndarray, keys: list[tuple[float, float]]) -> np.ndarray:
        contenders = rng.integers(0, size, size=max(1, int(tournament_size)))
        winner = max(contenders, key=lambda idx: keys[int(idx)])
        return population[int(winner)]

    first = np.clip(np.asarray(start_genes, dtype=np.float64), low, high)
    population = np.vstack([first, rng.uniform(low, high, (size - 1, len(low)))])
    history: list[dict[str, Any]] = []
    best_genes, best_result = population[0].copy(), simulate(population[0])
    p0, p1 = progress_range
    total_gens = max(1, int(generations))

    for generation in range(int(generations) + 1):
        runs = [simulate(genes) for genes in population]
        keys = [path_fitness_key(run) for run in runs]
        order = sorted(range(size), key=lambda idx: keys[idx], reverse=True)
        if keys[order[0]] > path_fitness_key(best_result):
            best_genes, best_result = population[order[0]].copy(), runs[order[0]]
        rewards = np.array([float(run["discounted_reward"]) for run in runs], dtype=np.float64)
        stuck = sum(1 for run in runs if run["status"] == "stuck")
        history.append(
            {
                "generation": generation,
                "best_reward": float(rewards[order[0]]),
                "mean_reward": float(rewards.mean()),
                "paths_that_got_stuck": int(stuck),
                "best_so_far_reward": float(best_result["discounted_reward"]),
                "simulations_so_far": len(results),
            }
        )
        if progress_cb is not None:
            progress_cb("tuning_ga", p0 + (p1 - p0) * (generation / float(total_gens)))
        if generation == int(generations):
            break
        children = [population[index].copy() for index in order[:elites]]
        while len(children) < size:
            mother, father = pick_parent(population, keys), pick_parent(population, keys)
            child = mother.copy()
            if rng.random() < crossover_rate:
                share = rng.random(len(low))
                child = share * mother + (1.0 - share) * father
            moves = rng.random(len(low)) < mutation_rate
            child = np.clip(child + moves * rng.normal(0.0, 0.15, len(low)) * (high - low), low, high)
            children.append(child)
        population = np.vstack(children)
    return best_genes, best_result, history


# ---------------------------------------------------------------------------
# AI-Planner Runner & TXT Export
# ---------------------------------------------------------------------------


def _normalize_ai_params(raw_params: Mapping[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = copy.deepcopy(DEFAULT_AI_PLANNER_PARAMS)
    if isinstance(raw_params, Mapping):
        for raw_key, raw_val in raw_params.items():
            key = _UPPER_AI_PARAM_ALIASES.get(str(raw_key), str(raw_key))
            if key == "resource_spare_percent" and isinstance(raw_val, Mapping):
                merged[key] = {
                    r: float(raw_val.get(r, merged[key][r])) for r in RESOURCE_KEYS
                }
            elif raw_val is None and key in ("fleet_command_center_level", "attacks_per_day"):
                merged[key] = None
            elif raw_val is not None:
                merged[key] = raw_val

    merged["reward_weeks"] = int(merged["reward_weeks"])
    merged["weekly_discount"] = float(merged["weekly_discount"])
    merged["resource_spare_percent"] = {
        r: float(merged["resource_spare_percent"][r]) for r in RESOURCE_KEYS
    }
    merged["fleet_command_center_level"] = (
        None if merged["fleet_command_center_level"] is None else int(merged["fleet_command_center_level"])
    )
    merged["attacks_per_day"] = (
        None if merged["attacks_per_day"] is None else float(merged["attacks_per_day"])
    )
    merged["scarcity_focus"] = float(merged["scarcity_focus"])
    merged["critical_queue_share"] = float(merged["critical_queue_share"])
    merged["patience"] = float(merged["patience"])
    merged["speed_weight"] = float(merged["speed_weight"])
    merged["revalue_every_steps"] = max(1, int(merged["revalue_every_steps"]))
    merged["search_tolerance"] = max(0.0, float(merged["search_tolerance"]))
    merged["ga_population_size"] = max(2, int(merged["ga_population_size"]))
    merged["ga_generations"] = max(0, int(merged["ga_generations"]))
    merged["ga_tournament_size"] = max(1, int(merged["ga_tournament_size"]))
    merged["ga_crossover_rate"] = float(merged["ga_crossover_rate"])
    merged["ga_mutation_rate"] = float(merged["ga_mutation_rate"])
    merged["ga_elite_count"] = max(1, min(int(merged["ga_elite_count"]), merged["ga_population_size"] - 1))
    merged["ga_random_seed"] = int(merged["ga_random_seed"])
    merged["pad_generic_colonies"] = bool(merged["pad_generic_colonies"])
    return merged


def _build_factor_levels(simulator: EmpirePathSimulator, run: Mapping[str, Any]) -> list[dict[str, Any]]:
    limit = float(run["stopped_at_hours"])
    rows: list[dict[str, Any]] = [
        {
            "factor_kind": "colonies",
            "key": "colonies",
            "name": "Colonies founded",
            "start_max_level": sum(1 for planet in simulator.planets if planet["founded"]),
            "reached_max_level": sum(1 for flag in run["final_founded"] if flag),
            "started_max_level": (
                sum(1 for planet in simulator.planets if planet["founded"])
                + sum(
                    1
                    for step in run["order"]
                    if simulator.kind[step] == "colonize" and float(run["start"][step]) <= limit
                )
            ),
            "target_max_level": simulator.planet_count,
            "planets_at_reached_max": sum(1 for flag in run["final_founded"] if flag),
        }
    ]
    building_keys = sorted(
        {
            key
            for planet in simulator.planets
            for key, level in (*planet["levels"].items(), *planet["target_levels"].items())
            if level > 0
        },
        key=lambda item: int(item[1:]),
    )
    started_building_max: dict[str, int] = {
        key: max((planet["levels"].get(key, 0) for planet in simulator.planets), default=0) for key in building_keys
    }
    for step in run["order"]:
        if simulator.kind[step] == "build" and simulator.key[step] in started_building_max:
            started_building_max[simulator.key[step]] = max(
                started_building_max[simulator.key[step]], simulator.level_to[step]
            )
    for key in building_keys:
        building_id = int(key[1:])
        reached = max((levels.get(key, 0) for levels in run["final_levels"]), default=0)
        rows.append(
            {
                "factor_kind": "building",
                "key": key,
                "name": str(game_data.BUILDING_BY_ID[building_id]["name"]),
                "start_max_level": max((planet["levels"].get(key, 0) for planet in simulator.planets), default=0),
                "reached_max_level": reached,
                "started_max_level": max(reached, started_building_max[key]),
                "target_max_level": max(
                    (planet["target_levels"].get(key, 0) for planet in simulator.planets), default=0
                ),
                "planets_at_reached_max": (
                    sum(1 for levels in run["final_levels"] if levels.get(key, 0) == reached) if reached > 0 else 0
                ),
            }
        )
    research_keys = sorted(
        {
            key
            for key, level in (
                *simulator.problem["research_levels"].items(),
                *simulator.problem["research_target"].items(),
            )
            if level > 0
        },
        key=lambda item: int(item[1:]),
    )
    started_research: dict[str, int] = {
        key: int(simulator.problem["research_levels"].get(key, 0)) for key in research_keys
    }
    for step in run["order"]:
        if simulator.kind[step] == "research" and simulator.key[step] in started_research:
            started_research[simulator.key[step]] = max(started_research[simulator.key[step]], simulator.level_to[step])
    for key in research_keys:
        reached = int(run["final_research"].get(key, 0))
        rows.append(
            {
                "factor_kind": "research",
                "key": key,
                "name": str(game_data.RESEARCH_BY_ID[key]["name"]),
                "start_max_level": int(simulator.problem["research_levels"].get(key, 0)),
                "reached_max_level": reached,
                "started_max_level": max(reached, started_research[key]),
                "target_max_level": int(simulator.problem["research_target"].get(key, 0)),
                "planets_at_reached_max": 1 if reached > 0 else 0,
            }
        )
    return rows


def run_ai_planner(
    state: GameState,
    target_config: Mapping[str, Any],
    *,
    attack_telemetry: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
    progress_cb: Callable[[str, float], None] | None = None,
) -> dict[str, Any]:
    """Plan the W-week discounted-reward build/research sequence from live `state` to `target_config`."""
    t0 = time.perf_counter()
    if progress_cb is not None:
        progress_cb("preparing", 0.04)

    cfg = _normalize_ai_params(params)
    problem = build_path_problem(
        state,
        target_config,
        pad_generic_colonies=cfg["pad_generic_colonies"],
        colony_founding_hours=float(cfg.get("colony_founding_hours", 0.0)),
    )

    telemetry = dict(attack_telemetry) if isinstance(attack_telemetry, Mapping) else compute_attack_telemetry([], None, state=state)
    raw_median_loot = telemetry.get("median_loot") if isinstance(telemetry.get("median_loot"), Mapping) else {}
    median_loot = {r: max(0.0, game_data.safe_finite_float(raw_median_loot.get(r), 0.0)) for r in RESOURCE_KEYS}
    median_rt = game_data.safe_finite_float(
        telemetry.get("median_round_trip_seconds", telemetry.get("median_round_trip_sec")),
        DEFAULT_MEDIAN_ROUND_TRIP_SECONDS,
    )
    if median_rt <= 0.0:
        median_rt = DEFAULT_MEDIAN_ROUND_TRIP_SECONDS
    sample_count = game_data.safe_int(telemetry.get("sample_count", telemetry.get("attacks_in_history")), -1)
    has_active_loot = (sample_count > 0) or (sample_count < 0 and any(v > 0.0 for v in median_loot.values()))
    per_slot_hour = (3600.0 / median_rt) if (has_active_loot and median_rt > 0.0) else 0.0

    fixed_cc = cfg["fleet_command_center_level"]
    if fixed_cc is not None and fixed_cc < 0:
        raise ValueError(f"fleet_command_center_level must be >= 0 or None, got {fixed_cc!r}")

    if cfg["attacks_per_day"] is None:
        slot_attack_income = {r: median_loot[r] * per_slot_hour for r in RESOURCE_KEYS}
        fixed_attack_income = {r: 0.0 for r in RESOURCE_KEYS}
        pace_set_by = (
            "Fleet base (b10) slots x median round-trip"
            if fixed_cc is None
            else f"fleet_command_center_level ({fixed_cc}) x median round-trip"
        )
    else:
        per_day = max(0.0, float(cfg["attacks_per_day"]))
        slot_attack_income = {r: 0.0 for r in RESOURCE_KEYS}
        fixed_attack_income = {r: median_loot[r] * per_day / 24.0 for r in RESOURCE_KEYS}
        pace_set_by = "attacks_per_day"

    simulator = EmpirePathSimulator(
        problem,
        fixed_attack_income,
        slot_attack_income=slot_attack_income,
        fleet_command_center_level=fixed_cc,
        spare_percent=cfg["resource_spare_percent"],
        reward_weeks=cfg["reward_weeks"],
        weekly_discount=cfg["weekly_discount"],
    )

    if progress_cb is not None:
        progress_cb("simulating_baselines", 0.12)

    strategies: list[PathStrategy] = [
        PathStrategy("cheapest step first", rule="cheapest", patience=cfg["patience"]),
        PathStrategy(
            "discounted payback at Merchant values",
            rule="payback",
            values="merchant",
            critical_share=cfg["critical_queue_share"],
            patience=cfg["patience"],
            speed_weight=cfg["speed_weight"],
            revalue_every_steps=cfg["revalue_every_steps"],
        ),
        PathStrategy(
            "discounted payback at scarcity values",
            rule="payback",
            values="scarcity",
            focus=cfg["scarcity_focus"],
            critical_share=cfg["critical_queue_share"],
            patience=cfg["patience"],
            speed_weight=cfg["speed_weight"],
            revalue_every_steps=cfg["revalue_every_steps"],
        ),
    ]
    baseline_runs: dict[str, dict[str, Any]] = {}
    for idx, strat in enumerate(strategies):
        baseline_runs[strat.name] = simulator.run(strat)
        if progress_cb is not None:
            progress_cb("simulating_baselines", 0.12 + 0.13 * (idx + 1) / len(strategies))

    reward_upper_bound = float(simulator.reward_upper_bound)
    active = [s for s in strategies if baseline_runs[s.name]["steps_started"] > 0]
    search_summary: dict[str, Any] = {
        "reward_upper_bound": reward_upper_bound,
        "genetic_algorithm_ran": False,
        "simulations_of_the_genetic_algorithm": 0,
        "tuned_genes": None,
        "tuned_reward": None,
        "ga_history": [],
    }

    if not active and len(simulator.steps) > 0:
        best_strategy = strategies[-1]
        best_run = baseline_runs[best_strategy.name]
        search_summary["why"] = "no strategy can start any step"
    else:
        candidates = active if active else list(strategies)
        best_strategy = max(candidates, key=lambda s: path_fitness_key(baseline_runs[s.name]))
        best_run = baseline_runs[best_strategy.name]
        best_reward = float(best_run["discounted_reward"])
        headroom = max(0.0, 1.0 - best_reward / reward_upper_bound) if reward_upper_bound > 0.0 else 0.0
        if cfg["ga_generations"] <= 0 or cfg["ga_population_size"] < 2 or len(simulator.steps) == 0:
            search_summary["why"] = "the genetic algorithm is switched off"
        elif headroom <= cfg["search_tolerance"]:
            search_summary["why"] = (
                f"{best_strategy.name} is within {100.0 * headroom:.3f} % of the reward ceiling "
                f"(tolerance {100.0 * cfg['search_tolerance']:.3f} %)"
            )
        else:
            start_genes = (
                cfg["scarcity_focus"],
                cfg["critical_queue_share"],
                cfg["patience"],
                cfg["speed_weight"],
                0.0,
                0.0,
                0.0,
                0.0,
            )
            genes, tuned_run, ga_history = tune_path_strategy(
                simulator,
                start_genes,
                population_size=cfg["ga_population_size"],
                generations=cfg["ga_generations"],
                tournament_size=cfg["ga_tournament_size"],
                crossover_rate=cfg["ga_crossover_rate"],
                mutation_rate=cfg["ga_mutation_rate"],
                elite_count=cfg["ga_elite_count"],
                random_seed=cfg["ga_random_seed"],
                revalue_every_steps=cfg["revalue_every_steps"],
                progress_cb=progress_cb,
                progress_range=(0.25, 0.90),
            )
            search_summary["genetic_algorithm_ran"] = True
            search_summary["simulations_of_the_genetic_algorithm"] = int(
                ga_history[-1]["simulations_so_far"] if ga_history else 0
            )
            search_summary["tuned_genes"] = {name: round(float(g), 6) for name, g in zip(PATH_GENE_RANGES, genes)}
            search_summary["tuned_reward"] = float(tuned_run["discounted_reward"])
            search_summary["ga_history"] = ga_history
            search_summary["why"] = f"{best_strategy.name} left {100.0 * headroom:.2f} % headroom below ceiling"
            if path_fitness_key(tuned_run) > path_fitness_key(best_run):
                best_strategy = strategy_from_genes(
                    "discounted payback tuned by the genetic algorithm", genes, cfg["revalue_every_steps"]
                )
                best_run = tuned_run

    search_summary["best_strategy"] = best_strategy.name
    search_summary["best_reward"] = float(best_run["discounted_reward"])
    search_summary["strategy_comparisons"] = [
        {
            "strategy": s.name,
            "status": baseline_runs[s.name]["status"],
            "discounted_reward": round(float(baseline_runs[s.name]["discounted_reward"]), 2),
            "total_spice_eq_produced": round(float(baseline_runs[s.name]["total_spice_eq_produced"]), 2),
            "steps_started": int(baseline_runs[s.name]["steps_started"]),
            "steps_completed": int(baseline_runs[s.name]["steps_completed"]),
        }
        for s in strategies
    ]

    if progress_cb is not None:
        progress_cb("finalizing", 0.94)

    lower_bounds = sorted(simulator.lower_bounds(), key=lambda item: item[1], reverse=True)
    top_lb_label, top_lb_hours = lower_bounds[0] if lower_bounds else ("none", 0.0)

    # Queue busy summary
    limit_hr = float(best_run["stopped_at_hours"])
    busiest_queue = "none"
    busiest_hours = -1.0
    for q in range(simulator.planet_count + 1):
        q_name = f"building queue of {simulator.planets[q]['coords']}" if q < simulator.planet_count else "research queue"
        busy = sum(
            max(0.0, min(limit_hr, float(best_run["end"][s])) - float(best_run["start"][s]))
            for s in range(len(simulator.steps))
            if simulator.queue[s] == q and best_run["start"][s] is not None
        )
        if busy > busiest_hours:
            busiest_hours = busy
            busiest_queue = q_name

    # Weekly breakdown
    weekly_breakdown: list[dict[str, Any]] = []
    cum_disc = 0.0
    for w_idx in range(simulator.reward_weeks):
        weight = float(simulator.week_weights[w_idx])
        amounts = best_run["weekly_resources"][w_idx]
        sp_eq = float(best_run["weekly_spice_eq"][w_idx])
        disc = weight * sp_eq
        cum_disc += disc
        weekly_breakdown.append(
            {
                "week": w_idx + 1,
                "discount_weight": round(weight, 6),
                **{r: round(float(amounts[pos]), 2) for pos, r in enumerate(RESOURCE_KEYS)},
                "spice_eq": round(sp_eq, 2),
                "discounted_spice_eq": round(disc, 2),
                "cumulative_discounted_spice_eq": round(cum_disc, 2),
            }
        )

    factor_levels = _build_factor_levels(simulator, best_run)
    buildings_reached = {
        str(row["name"]): f"{int(row['reached_max_level'])}/{int(row['target_max_level'])}"
        for row in factor_levels
        if row["factor_kind"] == "building"
    }
    research_reached = {
        str(row["name"]): f"{int(row['reached_max_level'])}/{int(row['target_max_level'])}"
        for row in factor_levels
        if row["factor_kind"] == "research"
    }

    # Sequential steps output strictly using kind in {"BUILD", "RESEARCH", "COLONIZE_PREVIEW"}
    steps_out: list[dict[str, Any]] = []
    for seq, step_id in enumerate(best_run["order"], start=1):
        details = simulator.steps[step_id]
        raw_kind = str(details["kind"])
        if raw_kind == "build":
            out_kind = "BUILD"
        elif raw_kind == "research":
            out_kind = "RESEARCH"
        elif raw_kind == "colonize":
            out_kind = "COLONIZE_PREVIEW"
        else:
            continue

        start_hr = float(best_run["start"][step_id])
        end_hr = float(best_run["end"][step_id])
        dur_hr = max(0.0, end_hr - start_hr)
        week_num = min(simulator.reward_weeks, max(1, int(start_hr // PATH_HOURS_PER_WEEK) + 1))

        p_idx = int(details["planet"])
        if p_idx >= 0:
            p_info = simulator.planets[p_idx]
            p_id = int(p_info["planet_id"])
            p_coords = str(p_info["coords"])
            p_name = str(p_info["name"])
        else:
            p_id = 0
            p_coords = "account"
            p_name = "Account"

        cost_map = {r: int(round(details["cost"][pos])) for pos, r in enumerate(RESOURCE_KEYS)}
        steps_out.append(
            {
                "seq": seq,
                "start_sec": round(start_hr * 3600.0, 2),
                "duration_sec": round(dur_hr * 3600.0, 2),
                "finish_sec": round(end_hr * 3600.0, 2),
                "start_text": fmt_path_time(start_hr),
                "duration_text": fmt_path_time(dur_hr),
                "finish_text": fmt_path_time(end_hr),
                "week": week_num,
                "kind": out_kind,
                "planet_id": p_id,
                "planet_coords": p_coords,
                "planet_name": p_name,
                "entity_key": str(details["key"]),
                "entity_name": str(details["name"]),
                "from_level": int(details["level_from"]),
                "target_level": int(details["level_to"]),
                "cost": cost_map,
                "cost_spice_eq": round(float(details["cost_spice_eq"]), 2),
            }
        )

    started_steps = best_run["order"]
    cost_spent = [sum(simulator.cost[s][pos] for s in started_steps) for pos in range(4)]
    loot_collected = list(best_run["loot_collected"])
    spare_diverted = list(best_run["spare_diverted"])
    cost_val = float(game_data.spice_equivalent(dict(zip(RESOURCE_KEYS, cost_spent))))
    loot_val = float(game_data.spice_equivalent(dict(zip(RESOURCE_KEYS, loot_collected))))
    weekly_sp = best_run["weekly_spice_eq"]
    waiting_for_step = int(best_run["waiting_for_step"])
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    run_hours = float(best_run["hours"])

    start_slots = int(best_run["start_fleet_slots"])
    income_now = {
        r: fixed_attack_income[r] + start_slots * slot_attack_income[r] for r in RESOURCE_KEYS
    }
    enriched_telemetry = {
        **telemetry,
        "median_loot": {
            **{r: round(median_loot[r], 2) for r in RESOURCE_KEYS},
            "spice_eq": round(
                float(raw_median_loot.get("spice_eq", game_data.spice_equivalent(median_loot))), 2
            ),
        },
        "median_round_trip_seconds": round(median_rt, 2),
        "sample_count": max(0, sample_count) if sample_count >= 0 else (1 if has_active_loot else 0),
        "current_fleet_slots": start_slots,
        "attacks_per_slot_per_hour": round(per_slot_hour, 4),
        "estimated_attacks_per_hour": round(
            (float(cfg["attacks_per_day"]) / 24.0) if cfg["attacks_per_day"] is not None else (start_slots * per_slot_hour),
            4,
        ),
        "pace_set_by": pace_set_by,
        "income_per_slot_per_hour": {r: round(slot_attack_income[r], 2) for r in RESOURCE_KEYS},
        "hourly_loot": {
            **{r: round(income_now[r], 2) for r in RESOURCE_KEYS},
            "spice_eq": round(float(game_data.spice_equivalent(income_now)), 2),
        },
    }

    disc_reward = float(best_run["discounted_reward"])
    total_net_sp = float(best_run["total_spice_eq_produced"])

    summary = {
        "strategy": best_strategy.name,
        "status": str(best_run["status"]),
        "reward_weeks": int(simulator.reward_weeks),
        "weekly_discount": float(simulator.weekly_discount),
        "horizon_hours": float(simulator.horizon_hours),
        "horizon": fmt_path_time(simulator.horizon_hours),
        "discounted_reward": round(disc_reward, 2),
        "discounted_reward_spice_eq": round(disc_reward, 2),
        "reward_upper_bound": round(reward_upper_bound, 2),
        "share_of_reward_ceiling_percent": (
            round(100.0 * disc_reward / reward_upper_bound, 2) if reward_upper_bound > 0.0 else 0.0
        ),
        "total_spice_eq_produced": round(total_net_sp, 2),
        "total_net_spice_eq": round(total_net_sp, 2),
        "week_1_spice_eq": round(float(weekly_sp[0]) if weekly_sp else 0.0, 2),
        "last_week_spice_eq": round(float(weekly_sp[-1]) if weekly_sp else 0.0, 2),
        "hours": round(run_hours, 4) if math.isfinite(run_hours) else None,
        "time": fmt_path_time(run_hours),
        "lower_bound_hours": round(top_lb_hours, 4) if math.isfinite(top_lb_hours) else None,
        "lower_bound": fmt_path_time(top_lb_hours),
        "lower_bound_set_by": str(top_lb_label),
        "steps": int(len(simulator.steps)),
        "steps_started": int(best_run["steps_started"]),
        "steps_completed": int(best_run["steps_completed"]),
        "steps_running": int(best_run["steps_running"]),
        "actions_count": int(len(steps_out)),
        "stopped_at_hours": round(float(best_run["stopped_at_hours"]), 4),
        "waiting_for": (
            f"{simulator.steps[waiting_for_step]['name']} {simulator.steps[waiting_for_step]['level_to']}"
            if waiting_for_step >= 0
            else None
        ),
        "colonies_founded": int(sum(1 for flag in best_run["final_founded"] if flag)),
        "colonies_target": int(simulator.planet_count),
        "start_fleet_slots": start_slots,
        "final_fleet_slots": int(best_run["final_fleet_slots"]),
        "attacks_per_slot_per_hour": round(per_slot_hour, 4),
        "spare_percent": dict(zip(RESOURCE_KEYS, simulator.spare_percent)),
        "spare_diverted": {r: int(round(spare_diverted[pos])) for pos, r in enumerate(RESOURCE_KEYS)},
        "max_building_levels_reached": buildings_reached,
        "research_levels_reached": research_reached,
        "busiest_queue": busiest_queue,
        "busy_hours_of_that_queue": round(max(0.0, busiest_hours), 2),
        "cost_spent": {r: int(round(cost_spent[pos])) for pos, r in enumerate(RESOURCE_KEYS)},
        "metal_smelted": int(round(best_run["metal_smelted"])),
        "loot_counted": {r: int(round(loot_collected[pos])) for pos, r in enumerate(RESOURCE_KEYS)},
        "loot_share_of_cost_spent": round(loot_val / cost_val, 4) if cost_val > 0.0 else 0.0,
        "elapsed_ms": elapsed_ms,
    }

    result = _json_ready(
        {
            "schema_version": 1,
            "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "config_generated_at": str(target_config.get("generated_at") or ""),
            "meta_parameters": cfg,
            "attack_telemetry": enriched_telemetry,
            "summary": summary,
            "search": search_summary,
            "weekly_breakdown": weekly_breakdown,
            "factor_levels": factor_levels,
            "steps": steps_out,
            "elapsed_ms": elapsed_ms,
        }
    )

    if progress_cb is not None:
        progress_cb("completed", 1.0)
    return result


# ---------------------------------------------------------------------------
# Live Action Reconciliation (zero auto-demolition + max-field conflict prompt)
# ---------------------------------------------------------------------------


def _resolve_in_flight_buildings(state: GameState) -> tuple[dict[int, dict[str, int]], dict[int, int]]:
    """Return `(in_flight_target_levels[planet_id][bkey], active_job_count[planet_id])`."""
    in_flight: dict[int, dict[str, int]] = {}
    counts: dict[int, int] = {}
    planets = list(state.planets or ())
    by_coords: dict[str, PlanetState] = {}
    for p in planets:
        c = str(p.coords or "").strip()
        if c:
            by_coords[c] = p
            by_coords[normalize_coords(c)] = p
    active_p = state.active_planet()

    for job in state.active_queues or ():
        if getattr(job, "category", "") != "building":
            continue
        origin = str(getattr(job, "origin_coords", "") or "").strip()
        target_planet = by_coords.get(origin) or (by_coords.get(normalize_coords(origin)) if origin else None)
        if target_planet is None:
            target_planet = active_p
        if target_planet is None:
            continue
        pid = int(target_planet.planet_id)
        counts[pid] = counts.get(pid, 0) + 1

        bid = game_data.safe_optional_int(getattr(job, "building_id", None), None)
        if bid is None:
            clean = re.sub(r"\(.*?\)", "", str(getattr(job, "name", "") or ""))
            clean = _TIMER_RE.sub("", clean).strip().lower()
            bid = BUILDING_ID_BY_NAME.get(clean)
        if bid is None:
            continue
        cur_lvl = target_planet.building_level(bid)
        tgt_lvl = game_data.safe_optional_int(getattr(job, "target_level", None), None)
        if tgt_lvl is None or tgt_lvl <= cur_lvl:
            m = _LEVEL_RE.search(str(getattr(job, "name", "") or ""))
            tgt_lvl = max(cur_lvl + 1, int(m.group(1))) if m else (cur_lvl + 1)
        bkey = f"b{bid}"
        pmap = in_flight.setdefault(pid, {})
        pmap[bkey] = max(pmap.get(bkey, 0), int(tgt_lvl))

    # Also inspect active-progress hints on parsed BuildingOffers
    for p in planets:
        pid = int(p.planet_id)
        for off in p.building_offers or ():
            st = str(getattr(off, "status_text", "") or "").lower()
            if any(tok in st for tok in ("in progress", "active building", "constructing", "under construction")):
                bid = game_data.safe_optional_int(getattr(off, "building_id", None), None)
                if bid is None:
                    continue
                cur_lvl = p.building_level(bid)
                tgt_lvl = max(cur_lvl + 1, game_data.safe_int(getattr(off, "target_level", cur_lvl + 1), cur_lvl + 1))
                bkey = f"b{bid}"
                pmap = in_flight.setdefault(pid, {})
                pmap[bkey] = max(pmap.get(bkey, 0), int(tgt_lvl))
                counts[pid] = max(counts.get(pid, 0), 1)
    return in_flight, counts


def _resolve_in_flight_research(state: GameState) -> tuple[dict[str, int], bool]:
    """Return `(in_flight_target_level_by_rkey, research_queue_busy)`."""
    in_flight: dict[str, int] = {}
    busy = False
    for off in state.research_offers or ():
        if getattr(off, "active", False):
            busy = True
            rkey = str(getattr(off, "research_key", "") or "").strip()
            if rkey:
                cur = max(0, int((state.researches or {}).get(rkey, 0) or 0))
                tgt = max(cur + 1, game_data.safe_int(getattr(off, "target_level", cur + 1), cur + 1))
                in_flight[rkey] = max(in_flight.get(rkey, 0), tgt)

    name_to_rkey = {
        game_data.normalize_unit_key(str(info["name"])): rkey for rkey, info in research_catalog()
    }
    for job in state.active_queues or ():
        if getattr(job, "category", "") != "research":
            continue
        busy = True
        raw_name = str(getattr(job, "name", "") or "")
        clean = re.sub(r"\(.*?\)", "", raw_name)
        clean = _TIMER_RE.sub("", clean)
        clean = re.sub(r"\b(?:level|lvl|l)\s*\d+\b", "", clean, flags=re.IGNORECASE).strip()
        rkey = name_to_rkey.get(game_data.normalize_unit_key(clean))
        if not rkey:
            continue
        cur = max(0, int((state.researches or {}).get(rkey, 0) or 0))
        tgt = game_data.safe_optional_int(getattr(job, "target_level", None), None)
        if tgt is None or tgt <= cur:
            m = _LEVEL_RE.search(raw_name)
            tgt = max(cur + 1, int(m.group(1))) if m else (cur + 1)
        in_flight[rkey] = max(in_flight.get(rkey, 0), int(tgt))
    return in_flight, busy


def _is_stale_remote_resource_wait(offer: Any) -> bool:
    if offer is None or getattr(offer, "can_build", False):
        return False
    st = str(getattr(offer, "status_text", "") or "").strip().lower()
    if not st:
        return True
    blocking = (
        "in progress",
        "construction",
        "queue",
        "full",
        "require",
        "prereq",
        "locked",
        "blocked",
        "field",
        "max",
        "unmet",
        "demolish",
        "research",
        "occupied",
        "already",
        "not allowed",
        "unavailable",
    )
    return not any(tok in st for tok in blocking)


def _field_conflict_candidates(planet: PlanetState, blocked_bkey: str) -> list[dict[str, Any]]:
    """List existing non-zero buildings on `planet` ordered from safest demolition candidate to riskiest."""
    # Prefer optional/non-power buildings over core mines and power plants (b5 last).
    def _sort_key(item: tuple[int, str, int]) -> tuple[int, int, int]:
        bid, bkey, lvl = item
        if bid == 5:  # Fusion plant — protect energy backbone
            group = 4
        elif bid == 6:  # Thermosolar plant (spice-burning) — easiest swap on normal planets
            group = 0
        elif bkey == blocked_bkey:
            group = 3
        elif bid in (1, 2, 3, 4):
            group = 2
        else:
            group = 1
        return (group, -lvl, bid)

    raw_items: list[tuple[int, str, int]] = []
    for bid in sorted(game_data.BUILDING_BY_ID):
        bkey = f"b{bid}"
        lvl = planet.building_level(bid)
        if lvl > 0:
            raw_items.append((bid, bkey, lvl))
    raw_items.sort(key=_sort_key)

    candidates: list[dict[str, Any]] = []
    for bid, bkey, lvl in raw_items:
        info = game_data.BUILDING_BY_ID[bid]
        candidates.append(
            {
                "building_key": bkey,
                "building_id": bid,
                "building_name": str(info["name"]),
                "current_level": int(lvl),
                "is_power_plant": bid in (5, 6),
            }
        )
    return candidates


def reconcile_live_ai_actions(
    state: GameState,
    plan_result: Mapping[str, Any],
    config: StrategyConfig,
) -> tuple[list[PlannedAction], list[dict[str, Any]]]:
    """Map a saved AI-Planner sequential plan onto live causally ready `PlannedAction`s.

    - Skips steps already met by live `state` or currently in-flight queues.
    - Skips `COLONIZE_PREVIEW` (and never emits `DEMOLISH_BUILDING`).
    - Emits at most 1 earliest causally unblocked building action per planet lane
      plus at most 1 earliest causally unblocked global research action.
    - Emits structured field-conflict dicts whenever a planet has
      `fields_used >= fields_max > 0` and has a pending `BUILD` step.
    """
    steps = plan_result.get("steps") if isinstance(plan_result, Mapping) else None
    if not isinstance(steps, Sequence) or not steps:
        return [], []

    planets_by_id: dict[int, PlanetState] = {}
    planets_by_coords: dict[str, PlanetState] = {}
    non_raid_planets: list[PlanetState] = []
    for p in state.planets or ():
        pid = int(p.planet_id)
        planets_by_id[pid] = p
        c = str(p.coords or "").strip()
        if c:
            planets_by_coords[c] = p
            planets_by_coords[normalize_coords(c)] = p
        if normalize_planet_type(p) != "raid":
            non_raid_planets.append(p)

    in_flight_buildings, planet_queue_counts = _resolve_in_flight_buildings(state)
    in_flight_research, research_queue_busy = _resolve_in_flight_research(state)
    max_build_slots = max(1, game_data.safe_int(getattr(config, "max_build_queue_slots", 1), 1))

    research_offers_by_key: dict[str, ResearchOffer] = {}
    for roff in state.research_offers or ():
        rk = str(getattr(roff, "research_key", "") or "").strip()
        if rk:
            research_offers_by_key[rk] = roff

    lab_planet: Optional[PlanetState] = None
    if non_raid_planets:
        best_lab = max(
            non_raid_planets,
            key=lambda p: (
                p.building_level(8),
                1 if (state.active_planet_id is not None and p.planet_id == state.active_planet_id) else 0,
            ),
        )
        if best_lab.building_level(8) >= 1:
            lab_planet = best_lab

    actions: list[PlannedAction] = []
    field_conflicts: list[dict[str, Any]] = []
    handled_planets: set[int] = set()
    handled_research = False

    for step in steps:
        if not isinstance(step, Mapping):
            continue
        kind = str(step.get("kind") or "").strip().upper()
        if kind in ("COLONIZE_PREVIEW", "COLONIZE", "DEMOLISH", "DEMOLISH_BUILDING"):
            continue

        entity_key = str(step.get("entity_key") or "").strip()
        target_level = game_data.safe_int(step.get("target_level"), 0)
        seq = game_data.safe_int(step.get("seq"), 0)
        if not entity_key or target_level <= 0:
            continue

        if kind == "BUILD":
            step_pid = game_data.safe_optional_int(step.get("planet_id"), None)
            step_coords = str(step.get("planet_coords") or "").strip()
            planet = planets_by_id.get(step_pid) if step_pid is not None else None
            if planet is None and step_coords:
                planet = planets_by_coords.get(step_coords) or planets_by_coords.get(normalize_coords(step_coords))
            if planet is None or normalize_planet_type(planet) != "normal":
                continue
            pid = int(planet.planet_id)
            if pid in handled_planets:
                continue

            if not (entity_key.startswith("b") and entity_key[1:].isdigit()):
                continue
            bid = int(entity_key[1:])
            b_info = game_data.BUILDING_BY_ID.get(bid)
            if not b_info:
                continue
            b_name = str(step.get("entity_name") or b_info["name"])

            live_level = planet.building_level(bid)
            in_flight_lvl = in_flight_buildings.get(pid, {}).get(entity_key, 0)
            if max(live_level, in_flight_lvl) >= target_level:
                continue  # Already built or currently in flight

            # Must be the immediate next level in chain (causally unblocked)
            if target_level != live_level + 1:
                continue

            offers_by_id = {
                int(o.building_id): o
                for o in (planet.building_offers or ())
                if getattr(o, "building_id", None) is not None
            }
            if not is_building_allowed_on_planet_type(planet, bid, has_b8_offer=(8 in offers_by_id)):
                continue

            unmet_prereqs = game_data.missing_prerequisites(
                "buildings", bid, buildings=planet.buildings, researches=state.researches
            )
            if unmet_prereqs:
                continue

            # HARD REQUIREMENT: Max-field conflict detection (never emit DEMOLISH_BUILDING)
            if planet.fields_max > 0 and planet.fields_used >= planet.fields_max:
                candidates = _field_conflict_candidates(planet, entity_key)
                top_cand = candidates[0] if candidates else None
                conflict_key = f"p{pid}:{entity_key}:L{target_level}"
                field_conflicts.append(
                    {
                        "conflict_key": conflict_key,
                        "planet_id": pid,
                        "planet_coords": str(planet.coords),
                        "planet_name": str(planet.name or planet.coords),
                        "fields_used": int(planet.fields_used),
                        "fields_max": int(planet.fields_max),
                        "blocked_building_key": entity_key,
                        "blocked_building_name": b_name,
                        "blocked_target_level": int(target_level),
                        "candidate_building_key": str(top_cand["building_key"]) if top_cand else "",
                        "candidate_building_name": str(top_cand["building_name"]) if top_cand else "",
                        "candidate_level": int(top_cand["current_level"]) if top_cand else 0,
                        "candidates": candidates,
                    }
                )
                handled_planets.add(pid)
                continue

            # Check storage capacity blocker for non-warehouse buildings when storage is populated
            raw_wiki_cost = game_data.building_cost(bid, target_level)
            wiki_cost = Resources(**full_cost(raw_wiki_cost))
            if bid not in (11, 12, 13, 14):
                storage_blocked = False
                for r in RESOURCE_KEYS:
                    cap = game_data.safe_int(getattr(planet.storage, r, 0), 0)
                    need_r = game_data.safe_int(getattr(wiki_cost, r, 0), 0)
                    if cap > 0 and need_r > cap:
                        storage_blocked = True
                        break
                if storage_blocked:
                    continue

            queue_full = planet_queue_counts.get(pid, 0) >= max_build_slots
            is_remote = state.active_planet_id is not None and pid != int(state.active_planet_id)
            offer = offers_by_id.get(bid)
            reason = f"AI-Planner #{seq}: build {b_name} L{target_level}"

            if offer is not None and game_data.safe_int(offer.target_level, target_level) == target_level:
                cost = offer.cost
                missing = planet.resources.missing(cost)
                stale_remote_ok = is_remote and _is_stale_remote_resource_wait(offer) and not cost.is_zero()
                can_exec = (
                    (not queue_full)
                    and (bool(offer.can_build) or stale_remote_ok)
                    and planet.resources.can_afford(cost)
                )
                href = (str(offer.build_href) or None) if (can_exec and offer.can_build) else None
                out_reason = f"{reason} (build queue full)" if queue_full else reason
            else:
                cost = wiki_cost
                missing = planet.resources.missing(cost)
                can_exec = (
                    (not queue_full)
                    and is_remote
                    and (not cost.is_zero())
                    and planet.resources.can_afford(cost)
                )
                href = None
                if queue_full:
                    out_reason = f"{reason} (build queue full)"
                elif not is_remote and not can_exec:
                    out_reason = f"{reason} (building card not currently visible)"
                else:
                    out_reason = reason

            actions.append(
                PlannedAction(
                    priority="P1",
                    kind="BUILD" if can_exec else "WAIT_RESOURCES",
                    planet_id=pid,
                    planet_coords=str(planet.coords),
                    target_id=bid,
                    target_name=b_name,
                    target_level=target_level,
                    cost=cost,
                    missing=missing,
                    reason=out_reason,
                    executable_now=can_exec,
                    action_href=href,
                )
            )
            handled_planets.add(pid)

        elif kind == "RESEARCH":
            if handled_research:
                continue
            r_info = game_data.RESEARCH_BY_ID.get(entity_key)
            if not r_info:
                continue
            r_name = str(step.get("entity_name") or r_info["name"])
            rid_num = int(entity_key[1:])
            live_level = max(0, int((state.researches or {}).get(entity_key, 0) or 0))
            in_flight_lvl = in_flight_research.get(entity_key, 0)
            if max(live_level, in_flight_lvl) >= target_level:
                continue  # Already researched or currently in flight
            if target_level != live_level + 1:
                continue
            if lab_planet is None:
                continue
            unmet_prereqs = game_data.missing_prerequisites(
                "research", entity_key, buildings=lab_planet.buildings, researches=state.researches
            )
            if unmet_prereqs:
                continue

            offer = research_offers_by_key.get(entity_key)
            reason = f"AI-Planner #{seq}: research {r_name} L{target_level}"
            if offer is not None and game_data.safe_int(offer.target_level, target_level) == target_level:
                cost = offer.cost
                missing = lab_planet.resources.missing(cost)
                can_exec = (
                    (not research_queue_busy)
                    and bool(offer.can_research)
                    and lab_planet.resources.can_afford(cost)
                )
                href = (str(offer.research_href) or None) if can_exec else None
                out_reason = f"{reason} (research slot busy)" if research_queue_busy else reason
                tid = game_data.safe_optional_int(offer.research_id, rid_num)
            else:
                raw_cost = ((r_info.get("levels") or {}).get(target_level) or {}).get("cost") or {}
                cost = Resources(**full_cost(raw_cost))
                missing = lab_planet.resources.missing(cost)
                can_exec = False
                href = None
                out_reason = f"{reason} (research slot busy)" if research_queue_busy else reason
                tid = rid_num

            actions.append(
                PlannedAction(
                    priority="P1",
                    kind="RESEARCH",
                    planet_id=int(lab_planet.planet_id),
                    planet_coords=str(lab_planet.coords),
                    target_id=tid,
                    target_name=r_name,
                    target_level=target_level,
                    cost=cost,
                    missing=missing,
                    reason=out_reason,
                    executable_now=can_exec,
                    action_href=href,
                )
            )
            handled_research = True

    if actions:
        actions = attach_action_etas(state, actions)
    return actions, field_conflicts


# ---------------------------------------------------------------------------
# full_planner_path.txt Exporter (14-line header + causal numbered lines)
# ---------------------------------------------------------------------------


def _format_factor_levels(entries: Mapping[str, Any] | None) -> str:
    if not isinstance(entries, Mapping) or not entries:
        return "none"
    return ", ".join(f"{name} {value}" for name, value in entries.items())


def export_ai_planner_txt(plan_result: Mapping[str, Any]) -> str:
    """Export `run_ai_planner` output in the exact 14-line header + causal line format of `full_planner_path.txt`."""
    summary = plan_result.get("summary") if isinstance(plan_result.get("summary"), Mapping) else {}
    steps = plan_result.get("steps") if isinstance(plan_result.get("steps"), Sequence) else ()

    width = max(1, len(str(len(steps))))
    lines: list[str] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, Mapping):
            continue
        seq = game_data.safe_int(step.get("seq"), idx)
        kind = str(step.get("kind") or "").strip().upper()
        entity_name = str(step.get("entity_name") or step.get("entity_key") or "Unknown")
        target_level = game_data.safe_int(step.get("target_level"), 0)
        start_sec = game_data.safe_finite_float(step.get("start_sec"), 0.0)
        dur_sec = game_data.safe_finite_float(step.get("duration_sec"), 0.0)
        start_txt = str(step.get("start_text") or fmt_path_time(start_sec / 3600.0))
        dur_txt = str(step.get("duration_text") or fmt_path_time(dur_sec / 3600.0))
        coords = str(step.get("planet_coords") or "account")

        if kind == "BUILD":
            action = f"build {entity_name} level {target_level}"
        elif kind == "RESEARCH":
            action = f"research {entity_name} level {target_level}"
        elif kind in ("COLONIZE_PREVIEW", "COLONIZE"):
            action = "found the colony"
        else:
            action = f"{kind.lower()} {entity_name} level {target_level}"
        where = "" if (kind == "RESEARCH" or coords == "account") else f" on {coords}"
        lines.append(f"{seq:>{width}}. at {start_txt}{where}: {action} (takes {dur_txt})")

    weeks = game_data.safe_int(summary.get("reward_weeks"), 26)
    discount = game_data.safe_finite_float(summary.get("weekly_discount"), 0.90)
    status = str(summary.get("status") or "horizon")
    if status == "reached":
        result_txt = (
            f"configuration reached after {summary.get('time', '0s')} "
            f"within the {weeks}-week horizon ({len(lines)} actions)"
        )
    elif status == "horizon":
        result_txt = (
            f"{weeks}-week horizon ended with {game_data.safe_int(summary.get('steps_started'), len(lines))} "
            f"of {game_data.safe_int(summary.get('steps'), len(lines))} actions started "
            f"({game_data.safe_int(summary.get('steps_completed'), 0)} finished, "
            f"{game_data.safe_int(summary.get('steps_running'), 0)} running)"
        )
    else:
        result_txt = (
            f"NOT reached. The path stops after {game_data.safe_int(summary.get('steps_started'), len(lines))} "
            f"of {game_data.safe_int(summary.get('steps'), len(lines))} actions: "
            f"the empire can never pay for {summary.get('waiting_for')}"
        )

    written = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    config_saved = str(plan_result.get("config_generated_at") or "")
    buildings_line = _format_factor_levels(summary.get("max_building_levels_reached"))
    research_line = _format_factor_levels(summary.get("research_levels_reached"))
    s0 = game_data.safe_int(summary.get("start_fleet_slots"), 0)
    s1 = game_data.safe_int(summary.get("final_fleet_slots"), 0)
    per_slot = game_data.safe_finite_float(summary.get("attacks_per_slot_per_hour"), 0.0)
    spare_map = summary.get("spare_percent") if isinstance(summary.get("spare_percent"), Mapping) else {}
    spare_txt = ", ".join(
        f"{res} {game_data.safe_finite_float(spare_map.get(res), 0.0):.1f}%" for res in RESOURCE_KEYS
    )
    disc_reward = game_data.safe_finite_float(summary.get("discounted_reward"), 0.0)
    total_sp = game_data.safe_finite_float(summary.get("total_spice_eq_produced"), 0.0)
    w1_sp = game_data.safe_finite_float(summary.get("week_1_spice_eq"), 0.0)
    wl_sp = game_data.safe_finite_float(summary.get("last_week_spice_eq"), 0.0)
    horizon_txt = str(summary.get("horizon") or fmt_path_time(PATH_HOURS_PER_WEEK * weeks))

    header = [
        f"Best {weeks}-week path toward the saved configuration (notebooks/full_planner.ipynb, Section 11)",
        f"Written: {written}",
        f"Configuration: config_optimizer_result.json, saved {config_saved}",
        f"Strategy: {summary.get('strategy', 'discounted payback')}",
        f"Horizon: {weeks} weeks ({horizon_txt}), weekly discount {discount:.2f}",
        f"Attacks & spare: {s0} -> {s1} fleet slots ({s0 * per_slot:.2f} -> {s1 * per_slot:.2f} attacks/h); "
        f"spare {spare_txt}",
        f"Reward: {disc_reward:,.1f} SpiceEq net discounted "
        f"({total_sp:,.1f} net SpiceEq produced in {weeks} weeks; "
        f"week 1: {w1_sp:,.1f}, week {weeks}: {wl_sp:,.1f})",
        f"Result: {result_txt}",
        f"Colonies reached: {game_data.safe_int(summary.get('colonies_founded'), 0)}/"
        f"{game_data.safe_int(summary.get('colonies_target'), 0)} founded",
        f"Max building levels reached: {buildings_line}",
        f"Research levels reached: {research_line}",
        f"Actions: {len(lines)} within {weeks} weeks, in the order in which they start. "
        "Times count from the moment this file was written.",
        "Planets are named by their coordinates. "
        'EMPTY:nn is an empty colony slot: "found the colony" comes first there.',
        "",
    ]
    return "\n".join([*header, *lines]) + "\n"
