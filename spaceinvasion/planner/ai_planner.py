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
    _flight_distance_units,
    _parse_coords_distance,
    estimate_fleet_flight_seconds,
    normalize_coords,
    ship_effective_speed,
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
    _resolve_planet_display_name,
    _safe_speedup,
    build_monotonic_base_times,
    building_level_flows,
    building_time_divisor,
    fmt_duration,
    full_cost,
    normalize_planet_names_map,
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
# `attack_telemetry["income_basis"]` value when raid income is the loot and recycling the empire
# actually received over the trailing window, instead of fleet slots x median round-trip x median loot.
REALIZED_INCOME_BASIS: str = "realized_24h"

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
    "transport_dispatch_delay_minutes": 120.0,
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
    "PATH_TRANSPORT_DISPATCH_DELAY_MINUTES": "transport_dispatch_delay_minutes",
    "fleet_dispatch_delay_minutes": "transport_dispatch_delay_minutes",
}

_COORDS_RE = re.compile(r"^\d{1,2}:\d{1,3}:\d{1,2}$", re.ASCII)
_EMBEDDED_COORDS_RE = re.compile(r"(\d{1,2}:\d{1,3}:\d{1,2})", re.ASCII)
_TIMER_RE = re.compile(r"(\d+):(\d{2}):(\d{2})", re.ASCII)
_DAYS_TIMER_RE = re.compile(r"(?:(\d+)\s*d\s*)?(\d+):(\d{2}):(\d{2})", re.IGNORECASE | re.ASCII)
_SHORT_TIMER_RE = re.compile(r"^\s*(\d+):(\d{2})\s*$", re.ASCII)
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


def is_realized_income_basis(telemetry: Any) -> bool:
    """Return True when `telemetry` carries raid income measured from received loot and recycling."""
    if not isinstance(telemetry, Mapping):
        return False
    return str(telemetry.get("income_basis") or "").strip().lower().startswith("realized")


def compute_attack_telemetry(
    raid_rows: Sequence[Mapping[str, Any]],
    message_rows: Sequence[Mapping[str, Any]] | None = None,
    *,
    state: GameState | None = None,
    realized_hourly_loot: Mapping[str, Any] | None = None,
    realized_missions_per_hour: float | None = None,
) -> dict[str, Any]:
    """Compute live median attack loot, round-trip flight time, fleet slots, and hourly loot.

    By default hourly loot is modelled as `fleet slots x (3600 / median round-trip) x median loot`,
    i.e. every fleet slot raiding back to back. When `realized_hourly_loot` is given (per-resource
    loot plus recycling actually received per hour), it replaces that model: `hourly_loot` becomes
    the realized figure, `income_per_slot_per_hour` is the realized figure divided by the current
    fleet slots, `income_basis` is `REALIZED_INCOME_BASIS`, and the modelled figure is kept as
    `modelled_hourly_loot`. The output is unchanged when `realized_hourly_loot` is None.
    """
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

    realized_extra: dict[str, Any] = {}
    if isinstance(realized_hourly_loot, Mapping):
        modelled_hourly_loot = dict(hourly_loot)
        realized = {
            r: max(0.0, game_data.safe_finite_float(realized_hourly_loot.get(r), 0.0)) for r in RESOURCE_KEYS
        }
        slot_div = float(max(1, current_fleet_slots))
        income_per_slot_per_hour = {r: realized[r] / slot_div for r in RESOURCE_KEYS}
        hourly_loot = dict(realized)
        hourly_loot["spice_eq"] = float(game_data.spice_equivalent(realized))
        realized_mph = game_data.safe_finite_float(realized_missions_per_hour, -1.0)
        if realized_missions_per_hour is not None and realized_mph >= 0.0:
            estimated_attacks_per_hour = realized_mph
            attacks_per_slot_per_hour = realized_mph / slot_div
        realized_extra = {
            "income_basis": REALIZED_INCOME_BASIS,
            "modelled_hourly_loot": {k: round(v, 2) for k, v in modelled_hourly_loot.items()},
        }

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
            **realized_extra,
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


def estimate_interplanet_transport_seconds(
    origin_coords: str,
    target_coords: str,
    *,
    origin_ships: Mapping[str, Any] | None = None,
    empire_ships: Mapping[str, Any] | None = None,
    researches: Mapping[str, Any] | None = None,
    fallback_distance_units: float = 1175.0,
) -> tuple[float, float, str]:
    """Return `(one_way_seconds, distance_units, representative_hull)` between two colonies.

    Uses the SpaceInvasion Classic RETRO-calibrated flight duration formula from
    `spaceinvasion.combat.fleet` (`10 + 4765 * dist / eff_speed`) and chooses the
    realistic transport hull stationed on the donor (falling back to empire hulls,
    then `s2` Large transport).
    """
    orig = str(origin_coords or "").strip()
    dest = str(target_coords or "").strip()
    if orig and dest and normalize_coords(orig) == normalize_coords(dest):
        return 0.0, 0.0, "local"

    def _pick_hull(pool: Mapping[str, Any] | None) -> Optional[str]:
        if not isinstance(pool, Mapping):
            return None
        s3 = max(0, game_data.safe_int(pool.get("s3"), 0))
        s2 = max(0, game_data.safe_int(pool.get("s2"), 0))
        s1 = max(0, game_data.safe_int(pool.get("s1"), 0))
        if s3 > 0 and s2 == 0 and s1 == 0:
            return "s3"
        if s2 > 0 and s1 == 0:
            return "s2"
        if s1 > 0:
            # Large transports (25k cargo) dominate when carrying >= 80% of capacity;
            # otherwise mixed fleets travel at Small transport (`s1`) speed.
            if s2 * 25_000 >= max(1, int((s2 * 25_000 + s1 * 5_000) * 0.80)):
                return "s2"
            return "s1"
        if s2 > 0:
            return "s2"
        if s3 > 0:
            return "s3"
        return None

    hull = _pick_hull(origin_ships) or _pick_hull(empire_ships) or "s2"
    if _is_valid_coords(orig) and _is_valid_coords(dest):
        dist = float(_flight_distance_units(orig, dest))
        one_way = float(
            estimate_fleet_flight_seconds(
                orig,
                dest,
                {hull: 1},
                researches=researches,
                round_trip=False,
            )
        )
        return round(max(1.0, one_way), 2), round(dist, 2), hull

    # Empty colony slot (`EMPTY:nn`): estimate using representative inter-system distance
    dist = max(100.0, float(fallback_distance_units))
    eff_speed = max(100.0, float(ship_effective_speed(hull, researches)))
    one_way = 10.0 + (4765.0 * dist) / max(1.0, eff_speed)
    return round(max(1.0, one_way), 2), round(dist, 2), hull


def _parse_queue_remaining_seconds(
    job: Any,
    *,
    state_ts: float = 0.0,
    fallback_seconds: float = 0.0,
) -> float:
    """Resolve remaining seconds on a `QueueJob` from numeric remaining, finish_ts, text, or fallback."""
    rem_raw = getattr(job, "remaining_seconds", None)
    if rem_raw is not None:
        rem_f = game_data.safe_finite_float(rem_raw, -1.0)
        if rem_f > 0.0:
            return float(rem_f)
    fin_raw = getattr(job, "finish_ts", None)
    if fin_raw is not None and state_ts > 0.0:
        fin_f = game_data.safe_finite_float(fin_raw, 0.0)
        if fin_f > state_ts:
            return float(fin_f - state_ts)
    rem_txt = str(getattr(job, "remaining_text", "") or "").strip()
    if rem_txt:
        m_full = _DAYS_TIMER_RE.search(rem_txt)
        if m_full:
            days = int(m_full.group(1) or 0)
            hrs = int(m_full.group(2) or 0)
            mins = int(m_full.group(3) or 0)
            secs = int(m_full.group(4) or 0)
            total = days * 86400.0 + hrs * 3600.0 + mins * 60.0 + secs
            if total > 0.0:
                return float(total)
        m_short = _SHORT_TIMER_RE.match(rem_txt)
        if m_short:
            total = int(m_short.group(1)) * 60.0 + int(m_short.group(2))
            if total > 0.0:
                return float(total)
    return max(0.0, float(fallback_seconds))


_ACTIVE_CONSTRUCTION_TOKENS: tuple[str, ...] = (
    "in progress",
    "in construction",
    "active building",
    "constructing",
    "under construction",
)


def _is_active_construction_status(status_text: Any) -> bool:
    st = str(status_text or "").lower()
    return any(tok in st for tok in _ACTIVE_CONSTRUCTION_TOKENS)


def _is_queue_job_expired(job: Any, state_ts: float) -> bool:
    raw_fin = getattr(job, "finish_ts", None)
    if raw_fin is None or state_ts <= 0.0:
        return False
    fin_ts = game_data.safe_finite_float(raw_fin, 0.0)
    return fin_ts > 0.0 and fin_ts <= state_ts


def _extract_building_job_id_and_level(job: Any, fallback_level: int = 0) -> tuple[Optional[int], Optional[int]]:
    bid = game_data.safe_optional_int(getattr(job, "building_id", None), None)
    if bid is None:
        clean = re.sub(r"\(.*?\)", "", str(getattr(job, "name", "") or ""))
        clean = _TIMER_RE.sub("", clean).strip().lower()
        bid = BUILDING_ID_BY_NAME.get(clean)
    tgt_lvl = game_data.safe_optional_int(getattr(job, "target_level", None), None)
    if tgt_lvl is None or tgt_lvl <= 0:
        m_lvl = _LEVEL_RE.search(str(getattr(job, "name", "") or ""))
        if m_lvl:
            tgt_lvl = int(m_lvl.group(1))
        elif fallback_level > 0:
            tgt_lvl = fallback_level
    return bid, tgt_lvl


def _resolve_untagged_building_job_planet(
    bid: Optional[int],
    tgt_lvl: Optional[int],
    planets: Sequence[PlanetState],
    active_p: Optional[PlanetState],
) -> Optional[PlanetState]:
    """Safely attribute a legacy `QueueJob(category='building', origin_coords='')` without falsely blocking `active_p`."""
    if active_p is None:
        return None
    active_offers = active_p.building_offers or ()
    if any(_is_active_construction_status(getattr(off, "status_text", "")) for off in active_offers):
        return active_p
    if active_offers and bid is not None and tgt_lvl is not None and active_p.building_level(bid) + 1 != tgt_lvl:
        offer_matches = [
            p
            for p in planets
            if p.planet_id != active_p.planet_id
            and any(
                game_data.safe_optional_int(getattr(off, "building_id", None), None) == bid
                and _is_active_construction_status(getattr(off, "status_text", ""))
                for off in (p.building_offers or ())
            )
        ]
        if len(offer_matches) == 1:
            return offer_matches[0]
        level_matches = [
            p
            for p in planets
            if p.planet_id != active_p.planet_id and p.building_level(bid) + 1 == tgt_lvl
        ]
        if len(level_matches) == 1:
            return level_matches[0]
        return None
    return active_p


def _extract_live_in_progress_state(
    state: GameState,
    problem_planets: Sequence[Mapping[str, Any]],
    *,
    speedup: float,
    research_now: Mapping[str, int],
    base_hours_fn: Callable[[str, int], float],
    lab_planet_index: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract live in-progress buildings, research, and incoming friendly fleet cargo from `state`."""
    state_ts = max(0.0, game_data.safe_finite_float(getattr(state, "timestamp", 0.0), 0.0))
    idx_by_pid: dict[int, int] = {}
    idx_by_coords: dict[str, int] = {}
    for entry in problem_planets:
        p_idx = int(entry["index"])
        pid = int(entry["planet_id"])
        if pid > 0:
            idx_by_pid[pid] = p_idx
        c = str(entry.get("coords") or "").strip()
        if c and not c.startswith("EMPTY:"):
            idx_by_coords[c] = p_idx
            idx_by_coords[normalize_coords(c)] = p_idx

    active_p = state.active_planet()
    all_live_planets = list(state.planets or ())

    live_planets_by_pid: dict[int, PlanetState] = {
        int(p.planet_id): p for p in all_live_planets if int(p.planet_id) > 0
    }

    # 1. In-progress buildings (deduplicated by (planet_index, bkey, level_to))
    builds_map: dict[tuple[int, str, int], dict[str, Any]] = {}
    for job in state.active_queues or ():
        if str(getattr(job, "category", "") or "").strip().lower() != "building":
            continue
        if _is_queue_job_expired(job, state_ts):
            continue
        bid, parsed_tgt = _extract_building_job_id_and_level(job)
        origin = str(getattr(job, "origin_coords", "") or "").strip()
        p_idx = (
            idx_by_coords.get(origin)
            if origin
            else None
        )
        if p_idx is None and origin:
            p_idx = idx_by_coords.get(normalize_coords(origin))
        if p_idx is None and not origin:
            matched_p = _resolve_untagged_building_job_planet(bid, parsed_tgt, all_live_planets, active_p)
            if matched_p is not None:
                p_idx = idx_by_pid.get(int(matched_p.planet_id))
        if p_idx is None:
            continue
        p_entry = problem_planets[p_idx]
        if bid is None or bid not in game_data.BUILDING_BY_ID:
            continue
        bkey = f"b{bid}"
        built = int(p_entry["levels"].get(bkey, 0))
        tgt_lvl = parsed_tgt
        if tgt_lvl is None or tgt_lvl <= built:
            m_lvl = _LEVEL_RE.search(str(getattr(job, "name", "") or ""))
            tgt_lvl = max(built + 1, int(m_lvl.group(1))) if m_lvl else (built + 1)
        if tgt_lvl <= built:
            continue
        div = building_time_divisor(
            int(p_entry["levels"].get("b7", 0)),
            int(p_entry["levels"].get("b17", 0)),
            speedup,
        )
        fallback_sec = max(1.0, (base_hours_fn(bkey, tgt_lvl) / max(1e-9, div)) * 3600.0)
        rem_sec = _parse_queue_remaining_seconds(job, state_ts=state_ts, fallback_seconds=fallback_sec)
        if rem_sec <= 0.0:
            rem_sec = 1.0
        dedup_k = (p_idx, bkey, int(tgt_lvl))
        prev = builds_map.get(dedup_k)
        if prev is None or rem_sec > float(prev["remaining_seconds"]):
            builds_map[dedup_k] = {
                "kind": "build",
                "planet": p_idx,
                "planet_id": int(p_entry["planet_id"]),
                "planet_coords": str(p_entry["coords"]),
                "planet_name": str(p_entry["name"]),
                "key": bkey,
                "name": str(game_data.BUILDING_BY_ID[bid]["name"]),
                "level_from": int(tgt_lvl - 1),
                "level_to": int(tgt_lvl),
                "queue": p_idx,
                "remaining_seconds": round(rem_sec, 2),
                "remaining_hours": rem_sec / 3600.0,
                "remaining_text": fmt_path_time(rem_sec / 3600.0),
            }

    # Also capture active building offers not yet mirrored into `state.active_queues`
    for p_entry in problem_planets:
        p_idx = int(p_entry["index"])
        live_p = live_planets_by_pid.get(int(p_entry["planet_id"]))
        if live_p is None:
            continue
        for off in live_p.building_offers or ():
            if not _is_active_construction_status(getattr(off, "status_text", "")):
                continue
            bid = game_data.safe_optional_int(getattr(off, "building_id", None), None)
            if bid is None or bid not in game_data.BUILDING_BY_ID:
                continue
            bkey = f"b{bid}"
            built = int(p_entry["levels"].get(bkey, 0))
            tgt_lvl = max(built + 1, game_data.safe_int(getattr(off, "target_level", built + 1), built + 1))
            dedup_k = (p_idx, bkey, int(tgt_lvl))
            if dedup_k in builds_map:
                continue
            div = building_time_divisor(
                int(p_entry["levels"].get("b7", 0)),
                int(p_entry["levels"].get("b17", 0)),
                speedup,
            )
            fallback_sec = max(1.0, (base_hours_fn(bkey, tgt_lvl) / max(1e-9, div)) * 3600.0)
            builds_map[dedup_k] = {
                "kind": "build",
                "planet": p_idx,
                "planet_id": int(p_entry["planet_id"]),
                "planet_coords": str(p_entry["coords"]),
                "planet_name": str(p_entry["name"]),
                "key": bkey,
                "name": str(game_data.BUILDING_BY_ID[bid]["name"]),
                "level_from": int(tgt_lvl - 1),
                "level_to": int(tgt_lvl),
                "queue": p_idx,
                "remaining_seconds": round(fallback_sec, 2),
                "remaining_hours": fallback_sec / 3600.0,
                "remaining_text": fmt_path_time(fallback_sec / 3600.0),
            }

    in_progress_builds = sorted(
        builds_map.values(),
        key=lambda row: (int(row["planet"]), float(row["remaining_seconds"]), int(row["level_to"])),
    )

    # 2. In-progress research
    name_to_rkey = {
        game_data.normalize_unit_key(str(info["name"])): rkey for rkey, info in research_catalog()
    }
    lab_b8 = (
        int(problem_planets[lab_planet_index]["levels"].get("b8", 0))
        if 0 <= lab_planet_index < len(problem_planets)
        else 0
    )
    res_div = research_time_divisor(lab_b8, speedup)
    research_map: dict[tuple[str, int], dict[str, Any]] = {}
    for job in state.active_queues or ():
        if str(getattr(job, "category", "") or "").strip().lower() != "research":
            continue
        raw_name = str(getattr(job, "name", "") or "")
        clean = re.sub(r"\(.*?\)", "", raw_name)
        clean = _TIMER_RE.sub("", clean)
        clean = re.sub(r"\b(?:level|lvl|l)\s*\d+\b", "", clean, flags=re.IGNORECASE).strip()
        rkey = name_to_rkey.get(game_data.normalize_unit_key(clean))
        if not rkey or rkey not in game_data.RESEARCH_BY_ID:
            continue
        cur = max(0, int(research_now.get(rkey, 0)))
        tgt = game_data.safe_optional_int(getattr(job, "target_level", None), None)
        if tgt is None or tgt <= cur:
            m_lvl = _LEVEL_RE.search(raw_name)
            tgt = max(cur + 1, int(m_lvl.group(1))) if m_lvl else (cur + 1)
        if tgt <= cur:
            continue
        fallback_sec = max(1.0, (base_hours_fn(rkey, tgt) / max(1e-9, res_div)) * 3600.0)
        rem_sec = _parse_queue_remaining_seconds(job, state_ts=state_ts, fallback_seconds=fallback_sec)
        if rem_sec <= 0.0:
            rem_sec = 1.0
        dedup_k = (rkey, int(tgt))
        prev = research_map.get(dedup_k)
        if prev is None or rem_sec > float(prev["remaining_seconds"]):
            research_map[dedup_k] = {
                "kind": "research",
                "planet": -1,
                "planet_id": 0,
                "planet_coords": "account",
                "planet_name": "Account",
                "key": rkey,
                "name": str(game_data.RESEARCH_BY_ID[rkey]["name"]),
                "level_from": int(tgt - 1),
                "level_to": int(tgt),
                "queue": len(problem_planets),
                "remaining_seconds": round(rem_sec, 2),
                "remaining_hours": rem_sec / 3600.0,
                "remaining_text": fmt_path_time(rem_sec / 3600.0),
            }

    for roff in state.research_offers or ():
        if not getattr(roff, "active", False):
            continue
        rkey = str(getattr(roff, "research_key", "") or "").strip()
        if not rkey or rkey not in game_data.RESEARCH_BY_ID:
            continue
        cur = max(0, int(research_now.get(rkey, 0)))
        tgt = max(cur + 1, game_data.safe_int(getattr(roff, "target_level", cur + 1), cur + 1))
        dedup_k = (rkey, int(tgt))
        if dedup_k in research_map:
            continue
        fallback_sec = max(1.0, (base_hours_fn(rkey, tgt) / max(1e-9, res_div)) * 3600.0)
        research_map[dedup_k] = {
            "kind": "research",
            "planet": -1,
            "planet_id": 0,
            "planet_coords": "account",
            "planet_name": "Account",
            "key": rkey,
            "name": str(game_data.RESEARCH_BY_ID[rkey]["name"]),
            "level_from": int(tgt - 1),
            "level_to": int(tgt),
            "queue": len(problem_planets),
            "remaining_seconds": round(fallback_sec, 2),
            "remaining_hours": fallback_sec / 3600.0,
            "remaining_text": fmt_path_time(fallback_sec / 3600.0),
        }

    in_progress_research = sorted(
        research_map.values(),
        key=lambda row: (float(row["remaining_seconds"]), str(row["key"]), int(row["level_to"])),
    )

    # 3. Live in-flight friendly cargo arrivals (outbound Transport/Station + returning fleets carrying cargo)
    in_flight_arrivals: list[dict[str, Any]] = []
    for job in state.active_queues or ():
        parsed_fleet = _parse_friendly_inbound_fleet_job(
            job,
            idx_by_coords,
            state_ts=state_ts,
            skip_expired=False,
            strict_hostile_text=False,
            use_last_coords=False,
        )
        if parsed_fleet is None:
            continue
        dest_idx, cargo_tuple, is_returning, q_name = parsed_fleet
        rem_sec = _parse_queue_remaining_seconds(job, state_ts=state_ts, fallback_seconds=60.0)
        if rem_sec <= 0.0:
            rem_sec = 1.0
        p_entry = problem_planets[int(dest_idx)]
        in_flight_arrivals.append(
            {
                "planet": int(dest_idx),
                "planet_id": int(p_entry["planet_id"]),
                "planet_coords": str(p_entry["coords"]),
                "planet_name": str(p_entry["name"]),
                "mission": q_name,
                "returning": bool(is_returning),
                "remaining_seconds": round(rem_sec, 2),
                "remaining_hours": rem_sec / 3600.0,
                "remaining_text": fmt_path_time(rem_sec / 3600.0),
                "cargo": {r: int(round(cargo_tuple[pos])) for pos, r in enumerate(RESOURCE_KEYS)},
                "cargo_tuple": cargo_tuple,
            }
        )
    in_flight_arrivals.sort(key=lambda row: (float(row["remaining_seconds"]), int(row["planet"])))
    return in_progress_builds, in_progress_research, in_flight_arrivals


def _parse_friendly_inbound_fleet_job(
    job: Any,
    coords_to_key: Mapping[str, Any],
    *,
    state_ts: float = 0.0,
    skip_expired: bool = False,
    strict_hostile_text: bool = True,
    use_last_coords: bool = True,
) -> Optional[tuple[Any, tuple[float, float, float, float], bool, str]]:
    """Parse one non-hostile fleet `QueueJob` into `(dest_key, cargo_tuple, is_returning, name)` if inbound."""
    if str(getattr(job, "category", "") or "").strip().lower() != "fleet":
        return None
    if skip_expired and _is_queue_job_expired(job, state_ts):
        return None
    q_name = str(getattr(job, "name", "") or "")
    q_det = str(getattr(job, "detail", "") or "")
    q_name_l = q_name.lower()
    q_det_l = q_det.lower()
    if bool(getattr(job, "hostile", False)):
        return None
    if strict_hostile_text and (
        "hostile" in q_name_l
        or "hostile" in q_det_l
        or "enemy" in q_name_l
        or "enemy" in q_det_l
    ):
        return None
    raw_cargo = getattr(job, "cargo", None)
    if raw_cargo is None:
        return None
    if isinstance(raw_cargo, Mapping):
        cargo_tuple = tuple(
            max(0.0, game_data.safe_finite_float(raw_cargo.get(r), 0.0))
            for r in RESOURCE_KEYS
        )
    else:
        cargo_tuple = tuple(
            max(0.0, game_data.safe_finite_float(getattr(raw_cargo, r, 0.0), 0.0))
            for r in RESOURCE_KEYS
        )
    if sum(cargo_tuple) <= 0.0:
        return None
    is_returning = ("return" in q_det_l) or ("↩" in q_name)
    dest_key: Any = None
    if is_returning:
        orig = str(getattr(job, "origin_coords", "") or "").strip()
        if orig:
            dest_key = coords_to_key.get(orig)
            if dest_key is None:
                dest_key = coords_to_key.get(normalize_coords(orig))
    elif any(tok in q_name_l or tok in q_det_l for tok in ("transport", "station")):
        coord_matches = _EMBEDDED_COORDS_RE.findall(q_name) or _EMBEDDED_COORDS_RE.findall(q_det)
        if coord_matches:
            raw_dst = coord_matches[-1] if use_last_coords else coord_matches[0]
            dest_key = coords_to_key.get(raw_dst)
            if dest_key is None:
                dest_key = coords_to_key.get(normalize_coords(raw_dst))
    if dest_key is None:
        return None
    return dest_key, cargo_tuple, is_returning, q_name


def compute_inbound_friendly_cargo_by_planet(state: GameState) -> dict[int, Resources]:
    """Return `{planet_id: Resources}` of non-hostile inbound fleet cargo heading to each normal colony.

    Counts outbound `Transport` / `Station` flights destined for an owned normal colony plus
    returning friendly fleets carrying cargo back to their owned normal `origin_coords`.
    """
    if state is None:
        return {}
    state_ts = max(0.0, game_data.safe_finite_float(getattr(state, "timestamp", 0.0), 0.0))
    pid_by_coords: dict[str, int] = {}
    for p in state.planets or ():
        if normalize_planet_type(p) != "normal":
            continue
        pid = int(p.planet_id)
        c = str(p.coords or "").strip()
        if c:
            pid_by_coords[c] = pid
            pid_by_coords[normalize_coords(c)] = pid
    if not pid_by_coords:
        return {}

    totals: dict[int, list[float]] = {}
    for job in state.active_queues or ():
        parsed = _parse_friendly_inbound_fleet_job(
            job,
            pid_by_coords,
            state_ts=state_ts,
            skip_expired=True,
        )
        if parsed is None:
            continue
        dest_pid, cargo_tuple, _is_ret, _q_name = parsed
        buf = totals.setdefault(int(dest_pid), [0.0, 0.0, 0.0, 0.0])
        for pos in range(4):
            buf[pos] += float(cargo_tuple[pos])

    out: dict[int, Resources] = {}
    for pid, buf in totals.items():
        res = Resources(
            pig_iron=max(0, int(round(buf[0]))),
            metal=max(0, int(round(buf[1]))),
            kryptonite=max(0, int(round(buf[2]))),
            spice=max(0, int(round(buf[3]))),
        )
        if not res.is_zero():
            out[pid] = res
    return out


def compute_in_progress_build_rem_by_planet(state: GameState) -> dict[int, float]:
    """Return `{planet_id: max_remaining_seconds}` for active building jobs per normal colony."""
    if state is None:
        return {}
    state_ts = max(0.0, game_data.safe_finite_float(getattr(state, "timestamp", 0.0), 0.0))
    speedup = _safe_speedup(state, {})
    all_live_planets = list(state.planets or ())
    normal_planets = [p for p in all_live_planets if normalize_planet_type(p) == "normal"]
    if not normal_planets:
        return {}

    active_p = state.active_planet()
    by_coords: dict[str, PlanetState] = {}
    for p in normal_planets:
        c = str(p.coords or "").strip()
        if c:
            by_coords[c] = p
            by_coords[normalize_coords(c)] = p

    base_times_cache: dict[str, dict[int, float]] = {}

    def _base_hours(bkey: str, level: int) -> float:
        if bkey not in base_times_cache:
            info = game_data.BUILDING_BY_ID[int(bkey[1:])]
            levels_map = info.get("levels") or {}
            top_level = max(int(info.get("max_table_level") or len(levels_map)), level)
            base_times_cache[bkey] = build_monotonic_base_times(levels_map, top_level)
        return float(base_times_cache[bkey].get(level, 0.0)) / 3600.0

    rem_by_pid: dict[int, float] = {}
    seen_jobs: set[tuple[int, int, int]] = set()

    for job in state.active_queues or ():
        if str(getattr(job, "category", "") or "").strip().lower() != "building":
            continue
        if _is_queue_job_expired(job, state_ts):
            continue
        bid, parsed_tgt = _extract_building_job_id_and_level(job)
        origin = str(getattr(job, "origin_coords", "") or "").strip()
        target_planet = by_coords.get(origin) or (by_coords.get(normalize_coords(origin)) if origin else None)
        if target_planet is None and not origin:
            matched_p = _resolve_untagged_building_job_planet(bid, parsed_tgt, all_live_planets, active_p)
            if matched_p is not None and normalize_planet_type(matched_p) == "normal":
                target_planet = matched_p
        if target_planet is None:
            continue
        pid = int(target_planet.planet_id)
        fallback_sec = 60.0
        tgt_lvl = parsed_tgt
        if bid is not None and bid in game_data.BUILDING_BY_ID:
            built = target_planet.building_level(bid)
            if tgt_lvl is None or tgt_lvl <= built:
                m_lvl = _LEVEL_RE.search(str(getattr(job, "name", "") or ""))
                tgt_lvl = max(built + 1, int(m_lvl.group(1))) if m_lvl else (built + 1)
            div = building_time_divisor(
                target_planet.building_level(7),
                target_planet.building_level(17),
                speedup,
            )
            fallback_sec = max(1.0, (_base_hours(f"b{bid}", tgt_lvl) / max(1e-9, div)) * 3600.0)
            seen_jobs.add((pid, int(bid), int(tgt_lvl)))
        rem_sec = _parse_queue_remaining_seconds(job, state_ts=state_ts, fallback_seconds=fallback_sec)
        if rem_sec <= 0.0:
            rem_sec = 1.0
        rem_by_pid[pid] = max(rem_by_pid.get(pid, 0.0), round(float(rem_sec), 2))

    for p in normal_planets:
        pid = int(p.planet_id)
        for off in p.building_offers or ():
            if not _is_active_construction_status(getattr(off, "status_text", "")):
                continue
            bid = game_data.safe_optional_int(getattr(off, "building_id", None), None)
            if bid is not None and bid in game_data.BUILDING_BY_ID:
                built = p.building_level(bid)
                tgt_lvl = max(built + 1, game_data.safe_int(getattr(off, "target_level", built + 1), built + 1))
                if (pid, int(bid), int(tgt_lvl)) in seen_jobs:
                    continue
                div = building_time_divisor(p.building_level(7), p.building_level(17), speedup)
                fallback_sec = max(1.0, (_base_hours(f"b{bid}", tgt_lvl) / max(1e-9, div)) * 3600.0)
                rem_by_pid[pid] = max(rem_by_pid.get(pid, 0.0), round(float(fallback_sec), 2))
            elif pid not in rem_by_pid:
                rem_by_pid[pid] = 60.0

    return rem_by_pid


def compute_in_progress_research_rem_seconds(state: GameState) -> float:
    """Return max remaining seconds for any active global research job (0.0 if idle)."""
    if state is None:
        return 0.0
    state_ts = max(0.0, game_data.safe_finite_float(getattr(state, "timestamp", 0.0), 0.0))
    speedup = _safe_speedup(state, {})
    non_raid = [p for p in (state.planets or ()) if normalize_planet_type(p) != "raid"]
    lab_b8 = max((p.building_level(8) for p in non_raid), default=0)
    res_div = research_time_divisor(lab_b8, speedup)
    name_to_rkey = {
        game_data.normalize_unit_key(str(info["name"])): rkey for rkey, info in research_catalog()
    }
    base_times_cache: dict[str, dict[int, float]] = {}

    def _base_hours(rkey: str, level: int) -> float:
        if rkey not in base_times_cache:
            info = game_data.RESEARCH_BY_ID[rkey]
            levels_map = info.get("levels") or {}
            top_level = max(int(info.get("max_table_level") or len(levels_map)), level)
            base_times_cache[rkey] = build_monotonic_base_times(levels_map, top_level)
        return float(base_times_cache[rkey].get(level, 0.0)) / 3600.0

    max_rem = 0.0
    seen_res: set[tuple[str, int]] = set()

    for job in state.active_queues or ():
        if str(getattr(job, "category", "") or "").strip().lower() != "research":
            continue
        if _is_queue_job_expired(job, state_ts):
            continue
        raw_name = str(getattr(job, "name", "") or "")
        clean = re.sub(r"\(.*?\)", "", raw_name)
        clean = _TIMER_RE.sub("", clean)
        clean = re.sub(r"\b(?:level|lvl|l)\s*\d+\b", "", clean, flags=re.IGNORECASE).strip()
        rkey = name_to_rkey.get(game_data.normalize_unit_key(clean))
        fallback_sec = 60.0
        if rkey and rkey in game_data.RESEARCH_BY_ID:
            cur = max(0, int((state.researches or {}).get(rkey, 0) or 0))
            tgt = game_data.safe_optional_int(getattr(job, "target_level", None), None)
            if tgt is None or tgt <= cur:
                m_lvl = _LEVEL_RE.search(raw_name)
                tgt = max(cur + 1, int(m_lvl.group(1))) if m_lvl else (cur + 1)
            fallback_sec = max(1.0, (_base_hours(rkey, tgt) / max(1e-9, res_div)) * 3600.0)
            seen_res.add((rkey, int(tgt)))
        rem_sec = _parse_queue_remaining_seconds(job, state_ts=state_ts, fallback_seconds=fallback_sec)
        if rem_sec <= 0.0:
            rem_sec = 1.0
        max_rem = max(max_rem, float(rem_sec))

    for roff in state.research_offers or ():
        if not getattr(roff, "active", False):
            continue
        rkey = str(getattr(roff, "research_key", "") or "").strip()
        if rkey and rkey in game_data.RESEARCH_BY_ID:
            cur = max(0, int((state.researches or {}).get(rkey, 0) or 0))
            tgt = max(cur + 1, game_data.safe_int(getattr(roff, "target_level", cur + 1), cur + 1))
            if (rkey, int(tgt)) in seen_res:
                continue
            fallback_sec = max(1.0, (_base_hours(rkey, tgt) / max(1e-9, res_div)) * 3600.0)
            max_rem = max(max_rem, float(fallback_sec))
        elif max_rem <= 0.0:
            max_rem = 60.0

    return round(max_rem, 2)


def summarize_blast_furnace_entries(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize per-colony Blast furnace (`b2`) levels and live throttle percentages."""
    planets_out: list[dict[str, Any]] = []
    for row in entries:
        if not isinstance(row, Mapping):
            continue
        b2_lvl = max(0, game_data.safe_int(row.get("b2_level"), 0))
        if b2_lvl > 0:
            pct = max(0, min(100, game_data.safe_int(row.get("initial_pct"), 100)))
        else:
            pct = 0
        enabled = bool(b2_lvl > 0 and pct > 0)
        planets_out.append(
            {
                "planet_index": game_data.safe_int(row.get("planet_index"), -1),
                "planet_id": game_data.safe_int(row.get("planet_id"), 0),
                "coords": str(row.get("coords") or ""),
                "name": str(row.get("name") or row.get("coords") or ""),
                "b2_level": b2_lvl,
                "initial_pct": pct,
                "initial_enabled": enabled,
                "rate_known": bool(row.get("rate_known", False)),
            }
        )

    b2_capable = [row for row in planets_out if row["b2_level"] > 0]
    enabled_count = sum(1 for row in b2_capable if row["initial_enabled"])
    disabled_count = len(b2_capable) - enabled_count
    full_on_count = sum(1 for row in b2_capable if row["initial_pct"] >= 100)
    full_off_count = sum(1 for row in b2_capable if row["initial_pct"] <= 0)
    if not b2_capable:
        mode = "none"
        any_enabled = False
        all_enabled = False
    elif full_on_count == len(b2_capable):
        mode = "on"
        any_enabled = True
        all_enabled = True
    elif full_off_count == len(b2_capable):
        mode = "off"
        any_enabled = False
        all_enabled = False
    else:
        mode = "mixed"
        any_enabled = enabled_count > 0
        all_enabled = enabled_count == len(b2_capable)

    return {
        "mode": mode,
        "any_enabled": any_enabled,
        "all_enabled": all_enabled,
        "enabled_count": enabled_count,
        "disabled_count": disabled_count,
        "total_b2_planets": len(b2_capable),
        "planets": planets_out,
    }


def summarize_live_blast_furnace_state(state: Optional[GameState]) -> dict[str, Any]:
    """Capture live Blast furnace (`b2`) enabled/disabled state across owned normal colonies."""
    if state is None:
        return summarize_blast_furnace_entries(())
    entries: list[dict[str, Any]] = []
    normal_planets = [p for p in (state.planets or ()) if normalize_planet_type(p) == "normal"]
    for idx, p in enumerate(normal_planets):
        b2_lvl = max(0, int((p.buildings or {}).get("b2", 0) or 0))
        eco_rates = getattr(p, "economy_rates", None) or {}
        rate_known = "resource2" in eco_rates
        pct = max(0, min(100, game_data.safe_int(eco_rates.get("resource2", 100), 100))) if b2_lvl > 0 else 0
        entries.append(
            {
                "planet_index": idx,
                "planet_id": int(p.planet_id),
                "coords": str(p.coords or ""),
                "name": str(p.name or p.coords or ""),
                "b2_level": b2_lvl,
                "initial_pct": pct,
                "rate_known": rate_known,
            }
        )
    return summarize_blast_furnace_entries(entries)


def build_path_problem(
    state: GameState,
    target_config: Mapping[str, Any],
    *,
    pad_generic_colonies: bool = False,
    colony_founding_hours: float = 0.0,
    planet_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the causal step problem from live `state` to `target_config` (ZERO demolition steps).

    Always starts strictly from the current live state of every owned colony
    (live per-colony resources, live built levels, live researches, live ships,
    active in-progress construction/research queues, and in-flight incoming cargo)
    and precomputes pairwise planet distance and one-way transport travel times.
    """
    if not isinstance(target_config, Mapping) or target_config.get("schema_version") != 1:
        found = target_config.get("schema_version") if isinstance(target_config, Mapping) else type(target_config).__name__
        raise ValueError(f"target_config must be a schema_version=1 dict, got {found!r}")
    raw_planets = target_config.get("planets")
    if not isinstance(raw_planets, Sequence) or not raw_planets:
        raise ValueError("target_config['planets'] must be a non-empty sequence")

    merged_planet_names: dict[str, str] = {}
    tc_meta = target_config.get("meta_parameters")
    if isinstance(tc_meta, Mapping):
        merged_planet_names.update(normalize_planet_names_map(tc_meta.get("planet_names")))
        merged_planet_names.update(normalize_planet_names_map(tc_meta.get("custom_planet_names")))
    merged_planet_names.update(normalize_planet_names_map(target_config.get("planet_names")))
    merged_planet_names.update(normalize_planet_names_map(target_config.get("custom_planet_names")))
    if isinstance(planet_names, Mapping):
        merged_planet_names.update(normalize_planet_names_map(planet_names))

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

    def _planet_entry_from_live(
        live_planet: PlanetState,
        raw_entry: Mapping[str, Any] | None,
        index: int,
    ) -> dict[str, Any]:
        levels = {
            str(k): max(0, int(v or 0))
            for k, v in (live_planet.buildings or {}).items()
            if max(0, int(v or 0)) > 0
        }
        stationed_s9 = max(0, int((live_planet.ships or {}).get("s9", 0) or 0))
        transport_ships = {
            sk: max(0, int((live_planet.ships or {}).get(sk, 0) or 0))
            for sk in ("s1", "s2", "s3")
            if max(0, int((live_planet.ships or {}).get(sk, 0) or 0)) > 0
        }
        res_tuple = tuple(
            max(0.0, float(getattr(live_planet.resources, r, 0) or 0)) for r in RESOURCE_KEYS
        )
        raw_target = _extract_target_buildings(raw_entry) if raw_entry is not None else {}
        # HARD REQUIREMENT: never demolish — target level on every building is >= live built level
        target_levels = dict(raw_target)
        for bk, blvl in levels.items():
            if blvl > target_levels.get(bk, 0):
                target_levels[bk] = blvl
        slot_idx = (
            game_data.safe_int(raw_entry.get("slot", raw_entry.get("slot_index")), index + 1)
            if raw_entry is not None
            else (index + 1)
        )
        resolved_name, resolved_game_name = _resolve_planet_display_name(
            live_planet, slot_idx, merged_planet_names or None
        )
        b2_lvl = levels.get("b2", 0)
        eco_rates = getattr(live_planet, "economy_rates", None) or {}
        b2_rate_known = "resource2" in eco_rates
        blast_furnace_pct = (
            max(0, min(100, game_data.safe_int(eco_rates.get("resource2", 100), 100)))
            if b2_lvl > 0
            else 0
        )
        blast_furnace_enabled = bool(b2_lvl > 0 and blast_furnace_pct > 0)
        return {
            "index": index,
            "slot_index": slot_idx,
            "planet_id": int(live_planet.planet_id),
            "coords": str(live_planet.coords),
            "name": resolved_name,
            "game_name": resolved_game_name,
            "planet_status": "colonized",
            "is_placeholder": False,
            "temperature_c": int(live_planet.temperature),
            "fields_max": max(int(live_planet.fields_max), sum(levels.values())),
            "founded": True,
            "is_central_planet": bool(raw_entry.get("is_central_planet", False)) if raw_entry is not None else False,
            "pays_for_research": bool(raw_entry.get("pays_for_research", False)) if raw_entry is not None else False,
            "levels": levels,
            "target_levels": target_levels,
            "solar_cells": stationed_s9,
            "resources": res_tuple,
            "ships": transport_ships,
            "blast_furnace_pct": blast_furnace_pct,
            "blast_furnace_enabled": blast_furnace_enabled,
            "blast_furnace_rate_known": b2_rate_known,
            "saved_production": dict(raw_entry.get("production") or {}) if raw_entry is not None else {},
            "missing_ships": {},
        }

    problem_planets: list[dict[str, Any]] = []
    used_live_ids: set[int] = set()
    deferred_placeholders: list[Mapping[str, Any]] = []

    for raw_entry in raw_planets:
        if not isinstance(raw_entry, Mapping):
            continue
        is_placeholder_entry = bool(
            raw_entry.get("is_placeholder", False)
            or str(raw_entry.get("planet_status") or "") == "empty_colony_slot"
            or game_data.safe_int(raw_entry.get("planet_id"), 0) < 0
        )
        if is_placeholder_entry:
            deferred_placeholders.append(raw_entry)
            continue
        live_planet = _match_live_planet(raw_entry, owned_normal, used_live_ids)
        if live_planet is not None:
            used_live_ids.add(int(live_planet.planet_id))
            problem_planets.append(_planet_entry_from_live(live_planet, raw_entry, len(problem_planets)))
        else:
            if not pad_generic_colonies and owned_normal:
                continue
            raw_target = _extract_target_buildings(raw_entry)
            slot_idx = game_data.safe_int(raw_entry.get("slot", raw_entry.get("slot_index")), len(problem_planets) + 1)
            problem_planets.append(
                {
                    "index": len(problem_planets),
                    "slot_index": slot_idx,
                    "planet_id": game_data.safe_int(raw_entry.get("planet_id"), -(len(problem_planets) + 1)),
                    "coords": str(raw_entry.get("coords") or f"EMPTY:{len(problem_planets) + 1:02d}"),
                    "name": str(raw_entry.get("name") or f"Empty Colony Slot {len(problem_planets) + 1}"),
                    "planet_status": "empty_colony_slot",
                    "is_placeholder": True,
                    "temperature_c": game_data.safe_int(raw_entry.get("temperature", raw_entry.get("temperature_c")), 0),
                    "fields_max": max(1, game_data.safe_int(raw_entry.get("fields_max"), 300)),
                    "founded": False,
                    "is_central_planet": bool(raw_entry.get("is_central_planet", False)),
                    "pays_for_research": bool(raw_entry.get("pays_for_research", False)),
                    "levels": {},
                    "target_levels": dict(raw_target),
                    "solar_cells": 0,
                    "resources": (0.0, 0.0, 0.0, 0.0),
                    "ships": {},
                    "blast_furnace_pct": 0,
                    "blast_furnace_enabled": False,
                    "blast_furnace_rate_known": False,
                    "saved_production": dict(raw_entry.get("production") or {}),
                    "missing_ships": {},
                }
            )

    # Bind any newly founded live colonies (e.g., colonized after `target_config` was generated)
    # onto unused placeholder templates first, or append them directly so reruns always start from
    # the real empire state.
    unmatched_live = [p for p in owned_normal if int(p.planet_id) not in used_live_ids]
    placeholder_cursor = 0
    for live_planet in unmatched_live:
        template_entry: Optional[Mapping[str, Any]] = None
        if placeholder_cursor < len(deferred_placeholders):
            template_entry = deferred_placeholders[placeholder_cursor]
            placeholder_cursor += 1
        used_live_ids.add(int(live_planet.planet_id))
        problem_planets.append(_planet_entry_from_live(live_planet, template_entry, len(problem_planets)))

    # Append remaining empty colony placeholders only when `pad_generic_colonies` is enabled
    if pad_generic_colonies or not problem_planets:
        for raw_entry in deferred_placeholders[placeholder_cursor:]:
            if not pad_generic_colonies and owned_normal:
                continue
            raw_target = _extract_target_buildings(raw_entry)
            slot_idx = game_data.safe_int(raw_entry.get("slot", raw_entry.get("slot_index")), len(problem_planets) + 1)
            problem_planets.append(
                {
                    "index": len(problem_planets),
                    "slot_index": slot_idx,
                    "planet_id": game_data.safe_int(raw_entry.get("planet_id"), -(len(problem_planets) + 1)),
                    "coords": str(raw_entry.get("coords") or f"EMPTY:{len(problem_planets) + 1:02d}"),
                    "name": str(raw_entry.get("name") or f"Empty Colony Slot {len(problem_planets) + 1}"),
                    "planet_status": "empty_colony_slot",
                    "is_placeholder": True,
                    "temperature_c": game_data.safe_int(raw_entry.get("temperature", raw_entry.get("temperature_c")), 0),
                    "fields_max": max(1, game_data.safe_int(raw_entry.get("fields_max"), 300)),
                    "founded": False,
                    "is_central_planet": bool(raw_entry.get("is_central_planet", False)),
                    "pays_for_research": bool(raw_entry.get("pays_for_research", False)),
                    "levels": {},
                    "target_levels": dict(raw_target),
                    "solar_cells": 0,
                    "resources": (0.0, 0.0, 0.0, 0.0),
                    "ships": {},
                    "blast_furnace_pct": 0,
                    "blast_furnace_enabled": False,
                    "blast_furnace_rate_known": False,
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

    # Extract live in-progress buildings, research, and in-flight incoming friendly cargo
    in_progress_builds, in_progress_research, in_flight_arrivals = _extract_live_in_progress_state(
        state,
        problem_planets,
        speedup=speedup,
        research_now=research_now,
        base_hours_fn=base_hours,
        lab_planet_index=lab_planet,
    )

    # Raise target floors to cover already-in-progress upgrades (already paid & running)
    in_progress_b_max: dict[tuple[int, str], int] = {}
    in_progress_b_count: dict[int, int] = {}
    for job in in_progress_builds:
        p_idx = int(job["planet"])
        bkey = str(job["key"])
        lvl_to = int(job["level_to"])
        in_progress_b_max[(p_idx, bkey)] = max(in_progress_b_max.get((p_idx, bkey), 0), lvl_to)
        in_progress_b_count[p_idx] = in_progress_b_count.get(p_idx, 0) + 1
        if lvl_to > problem_planets[p_idx]["target_levels"].get(bkey, 0):
            problem_planets[p_idx]["target_levels"][bkey] = lvl_to
    for p_idx, cnt in in_progress_b_count.items():
        used_plus_prog = sum(problem_planets[p_idx]["levels"].values()) + cnt
        if used_plus_prog > problem_planets[p_idx]["fields_max"]:
            problem_planets[p_idx]["fields_max"] = used_plus_prog

    in_progress_r_max: dict[str, int] = {}
    for job in in_progress_research:
        rkey = str(job["key"])
        lvl_to = int(job["level_to"])
        in_progress_r_max[rkey] = max(in_progress_r_max.get(rkey, 0), lvl_to)
        if lvl_to > research_target.get(rkey, 0):
            research_target[rkey] = lvl_to

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
            in_prog_lvl = in_progress_b_max.get((index, key), 0)
            start_after = max(built, in_prog_lvl)
            wanted = planet["target_levels"].get(key, 0)
            for level in range(start_after + 1, wanted + 1):
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
        start_after = max(research_now[key], in_progress_r_max.get(key, 0))
        for level in range(start_after + 1, research_target[key] + 1):
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

    # Validate reachability of prerequisites & warehouses
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
    # while keeping exact per-colony stock on each entry (any non-normal/unmatched planet's
    # landed resources are placed on `central_planet` so sum(planet.resources) == start_stock).
    stock = [0.0, 0.0, 0.0, 0.0]
    extra_stock = [0.0, 0.0, 0.0, 0.0]
    for planet in state.planets or ():
        is_bound = int(planet.planet_id) in used_live_ids
        for pos, resource in enumerate(RESOURCE_KEYS):
            amt = max(0.0, float(getattr(planet.resources, resource, 0) or 0))
            stock[pos] += amt
            if not is_bound:
                extra_stock[pos] += amt
    if any(v > 0.0 for v in extra_stock):
        cp_res = problem_planets[central_planet]["resources"]
        problem_planets[central_planet]["resources"] = tuple(
            float(cp_res[pos]) + extra_stock[pos] for pos in range(4)
        )

    # Precompute pairwise planet distance & one-way transport travel-time matrices
    empire_transport_ships: dict[str, int] = {"s1": 0, "s2": 0, "s3": 0}
    for p in owned_normal:
        for sk in ("s1", "s2", "s3"):
            empire_transport_ships[sk] += max(0, int((p.ships or {}).get(sk, 0) or 0))

    # Median inter-colony distance for placeholder fallback
    real_dists: list[float] = []
    for i in range(planet_count):
        ci = str(problem_planets[i]["coords"])
        if not _is_valid_coords(ci):
            continue
        for j in range(i + 1, planet_count):
            cj = str(problem_planets[j]["coords"])
            if _is_valid_coords(cj):
                real_dists.append(float(_flight_distance_units(ci, cj)))
    fallback_dist = float(np.median(np.asarray(real_dists, dtype=np.float64))) if real_dists else 1175.0

    travel_seconds: list[list[float]] = [[0.0] * planet_count for _ in range(planet_count)]
    travel_hours: list[list[float]] = [[0.0] * planet_count for _ in range(planet_count)]
    distance_units: list[list[float]] = [[0.0] * planet_count for _ in range(planet_count)]
    transport_hull_by_planet: list[str] = ["s2"] * planet_count
    off_diag_secs: list[float] = []
    for i in range(planet_count):
        src_ships = problem_planets[i].get("ships") or {}
        src_coords = str(problem_planets[i]["coords"])
        for j in range(planet_count):
            if i == j:
                continue
            dst_coords = str(problem_planets[j]["coords"])
            ow_sec, dist_u, hull = estimate_interplanet_transport_seconds(
                src_coords,
                dst_coords,
                origin_ships=src_ships,
                empire_ships=empire_transport_ships,
                researches=research_now,
                fallback_distance_units=fallback_dist,
            )
            travel_seconds[i][j] = ow_sec
            travel_hours[i][j] = ow_sec / 3600.0
            distance_units[i][j] = dist_u
            transport_hull_by_planet[i] = hull
            off_diag_secs.append(ow_sec)

    avg_travel_sec = float(np.mean(np.asarray(off_diag_secs, dtype=np.float64))) if off_diag_secs else 0.0
    max_travel_sec = float(max(off_diag_secs)) if off_diag_secs else 0.0

    initial_blast_furnace = summarize_blast_furnace_entries(
        [
            {
                "planet_index": int(p["index"]),
                "planet_id": int(p["planet_id"]),
                "coords": str(p["coords"]),
                "name": str(p["name"]),
                "b2_level": int((p.get("levels") or {}).get("b2", 0)),
                "initial_pct": int(
                    p.get("blast_furnace_pct", 100 if int((p.get("levels") or {}).get("b2", 0)) > 0 else 0)
                ),
                "rate_known": bool(p.get("blast_furnace_rate_known", False)),
            }
            for p in problem_planets
            if p.get("founded")
        ]
    )

    return {
        "speedup_factor": speedup,
        "planets": problem_planets,
        "initial_blast_furnace": initial_blast_furnace,
        "research_levels": research_now,
        "research_target": research_target,
        "lab_planet": lab_planet,
        "central_planet": central_planet,
        "start_stock": tuple(stock),
        "steps": steps,
        "in_progress_builds": in_progress_builds,
        "in_progress_research": in_progress_research,
        "in_flight_arrivals": in_flight_arrivals,
        "empire_transport_ships": empire_transport_ships,
        "fallback_distance_units": fallback_dist,
        "travel_seconds": travel_seconds,
        "travel_hours": travel_hours,
        "distance_units": distance_units,
        "transport_hull_by_planet": transport_hull_by_planet,
        "avg_transport_travel_seconds": round(avg_travel_sec, 2),
        "max_transport_travel_seconds": round(max_travel_sec, 2),
        "live_snapshot_ts": max(0.0, game_data.safe_finite_float(getattr(state, "timestamp", 0.0), 0.0)),
        "started_from_live_state": True,
    }


class EmpirePathSimulator:
    """Event-driven W-week empire rollout simulator matching `full_planner.ipynb` §11.4,
    extended with per-colony resources, non-instantaneous coordinate-based inter-planet
    transports, Fleet Command dispatch delay, early pre-shipping, and live in-progress
    building/research/cargo queues.
    """

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
        transport_dispatch_delay_minutes: float = 120.0,
        model_transport_travel: bool = True,
    ) -> None:
        if int(reward_weeks) < 1:
            raise ValueError(f"reward_weeks must be at least 1, got {reward_weeks!r}")
        if not (0.0 < float(weekly_discount) <= 1.0):
            raise ValueError(f"weekly_discount must be in (0, 1], got {weekly_discount!r}")
        if fleet_command_center_level is not None and int(fleet_command_center_level) < 0:
            raise ValueError(
                f"fleet_command_center_level must be >= 0 or None, got {fleet_command_center_level!r}"
            )
        if float(transport_dispatch_delay_minutes) < 0.0:
            raise ValueError(
                f"transport_dispatch_delay_minutes must be >= 0, got {transport_dispatch_delay_minutes!r}"
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
        self.transport_dispatch_delay_minutes = min(1440.0, max(0.0, float(transport_dispatch_delay_minutes)))
        self.dispatch_delay_hours = self.transport_dispatch_delay_minutes / 60.0
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

        self.model_transport_travel_requested = bool(model_transport_travel)
        self.model_transport_travel = bool(model_transport_travel) and self.planet_count >= 2
        self.initial_jobs: tuple[dict[str, Any], ...] = tuple(
            list(problem.get("in_progress_builds") or ()) + list(problem.get("in_progress_research") or ())
        )
        self.initial_arrivals: tuple[dict[str, Any], ...] = tuple(problem.get("in_flight_arrivals") or ())
        raw_init_bf = problem.get("initial_blast_furnace")
        if isinstance(raw_init_bf, Mapping) and isinstance(raw_init_bf.get("planets"), Sequence):
            self.initial_blast_furnace: dict[str, Any] = summarize_blast_furnace_entries(
                raw_init_bf.get("planets") or ()
            )
        else:
            self.initial_blast_furnace = summarize_blast_furnace_entries(
                [
                    {
                        "planet_index": int(p.get("index", idx)),
                        "planet_id": int(p.get("planet_id", idx + 1)),
                        "coords": str(p.get("coords") or ""),
                        "name": str(p.get("name") or p.get("coords") or ""),
                        "b2_level": int((p.get("levels") or {}).get("b2", 0)),
                        "initial_pct": int(
                            p.get(
                                "blast_furnace_pct",
                                100 if int((p.get("levels") or {}).get("b2", 0)) > 0 else 0,
                            )
                        ),
                        "rate_known": bool(p.get("blast_furnace_rate_known", False)),
                    }
                    for idx, p in enumerate(self.planets)
                    if p.get("founded")
                ]
            )
        self._initial_b2_capable_planets: tuple[dict[str, Any], ...] = tuple(
            row for row in self.initial_blast_furnace["planets"] if int(row["b2_level"]) > 0
        )
        self._empire_transport_ships: dict[str, int] = dict(problem.get("empire_transport_ships") or {})
        self._fallback_distance_units: float = float(problem.get("fallback_distance_units", 1175.0))
        self._base_travel_hours: list[list[float]] = self._compute_travel_hours_matrix(
            problem.get("research_levels") or {},
            precomputed=problem.get("travel_hours"),
        )
        self.travel_hours: list[list[float]] = [list(row) for row in self._base_travel_hours]
        self.lead_hours: list[list[float]] = [[0.0] * self.planet_count for _ in range(self.planet_count)]
        self.donors_by_dst: list[list[int]] = [[] for _ in range(self.planet_count)]
        self.min_lead_hours_by_dst: list[float] = [0.0] * self.planet_count
        self.max_lead_hours_by_dst: list[float] = [0.0] * self.planet_count
        self._sync_lead_matrix()

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
        b2_founded = 0
        for planet in range(self.planet_count):
            if self.founded[planet]:
                if self.levels[planet].get("b2", 0) > 0:
                    b2_founded += 1
                out = self.output[planet]
                mined += out[0]
                left += out[1]
                metal += out[2]
                kry += out[3]
                spice += out[4]
        self.b2_founded_count = b2_founded
        self.fleet_slots = self.fleet_slots_of(self.levels, self.founded)
        attack = self.gross_attack_of(self.fleet_slots)
        self.attack_now = attack
        keep = self.keep
        k0, k1, k2, k3 = keep[0], keep[1], keep[2], keep[3]
        a0, a1, a2, a3 = attack[0], attack[1], attack[2], attack[3]
        self.supply = (
            k0 * (mined + a0),
            k1 * (metal + a1),
            k2 * (kry + a2),
            k3 * (spice + a3),
        )
        self.supply_version += 1
        self.furnace_metal = metal
        self.rate_off = (
            k0 * (mined + a0),
            k1 * a1,
            k2 * (kry + a2),
            k3 * (spice + a3),
        )
        self.rate_on = (
            k0 * (left + a0),
            k1 * (metal + a1),
            k2 * (kry + a2),
            k3 * (spice + a3),
        )
        # Precompute per-planet net rates (both smelting OFF and ON) for O(1) lookup in hot loops.
        fixed = self.fleet_command_center_level
        inv_slots = (1.0 / float(self.fleet_slots)) if self.fleet_slots > 0 else 0.0
        p_off: list[tuple[float, float, float, float]] = []
        p_on: list[tuple[float, float, float, float]] = []
        for planet in range(self.planet_count):
            if not self.founded[planet]:
                p_off.append((0.0, 0.0, 0.0, 0.0))
                p_on.append((0.0, 0.0, 0.0, 0.0))
                continue
            out = self.output[planet]
            slots_p = 1 + (self.levels[planet].get("b10", 0) if fixed is None else fixed)
            share = slots_p * inv_slots
            sa0 = share * a0
            sa1 = share * a1
            rk = k2 * (out[3] + share * a2)
            rs = k3 * (out[4] + share * a3)
            p_off.append((k0 * (out[0] + sa0), k1 * sa1, rk, rs))
            p_on.append((k0 * (out[1] + sa0), k1 * (out[2] + sa1), rk, rs))
        self.planet_rate_off = p_off
        self.planet_rate_on = p_on

    def _compute_travel_hours_matrix(
        self,
        research_levels: Mapping[str, Any],
        *,
        precomputed: Any = None,
    ) -> list[list[float]]:
        count = self.planet_count
        if (
            isinstance(precomputed, Sequence)
            and len(precomputed) == count
            and all(isinstance(row, Sequence) and len(row) == count for row in precomputed)
        ):
            return [[max(0.0, float(val)) if i != j else 0.0 for j, val in enumerate(row)] for i, row in enumerate(precomputed)]
        mat: list[list[float]] = [[0.0] * count for _ in range(count)]
        for i in range(count):
            src_ships = self.planets[i].get("ships") or {}
            src_coords = str(self.planets[i].get("coords") or "")
            for j in range(count):
                if i == j:
                    continue
                dst_coords = str(self.planets[j].get("coords") or "")
                ow_sec, _, _ = estimate_interplanet_transport_seconds(
                    src_coords,
                    dst_coords,
                    origin_ships=src_ships,
                    empire_ships=self._empire_transport_ships,
                    researches=research_levels,
                    fallback_distance_units=self._fallback_distance_units,
                )
                mat[i][j] = ow_sec / 3600.0
        return mat

    def _sync_lead_matrix(self) -> None:
        count = self.planet_count
        delay = self.dispatch_delay_hours
        self.lead_hours = [
            [0.0 if i == j else (delay + self.travel_hours[i][j]) for j in range(count)]
            for i in range(count)
        ]
        self.donors_by_dst = []
        self.min_lead_hours_by_dst = []
        self.max_lead_hours_by_dst = []
        for dst in range(count):
            donors = sorted(
                (src for src in range(count) if src != dst),
                key=lambda src: (self.lead_hours[src][dst], self.travel_hours[src][dst], src),
            )
            self.donors_by_dst.append(donors)
            if donors:
                self.min_lead_hours_by_dst.append(self.lead_hours[donors[0]][dst])
                self.max_lead_hours_by_dst.append(self.lead_hours[donors[-1]][dst])
            else:
                self.min_lead_hours_by_dst.append(0.0)
                self.max_lead_hours_by_dst.append(0.0)

    def _rebuild_transport_matrix(self, research_levels: Mapping[str, Any]) -> None:
        self.travel_hours = self._compute_travel_hours_matrix(research_levels, precomputed=None)
        self._sync_lead_matrix()

    def _planet_net_rates(self, planet: int, *, smelting: Optional[bool] = None) -> tuple[float, float, float, float]:
        if planet < 0 or planet >= self.planet_count:
            return (0.0, 0.0, 0.0, 0.0)
        smelt = self.smelting if smelting is None else bool(smelting)
        return self.planet_rate_on[planet] if smelt else self.planet_rate_off[planet]

    def _enqueue_transport_batch(
        self,
        dst: int,
        cargo: tuple[float, float, float, float],
        *,
        sources: Sequence[str],
        max_travel_hr: float,
        max_lead_hr: float,
        step: int,
        is_early: bool,
    ) -> None:
        arrival = self.clock + max_lead_hr
        self._transit_seq += 1
        heapq.heappush(self.transit_heap, (arrival, self._transit_seq, dst, cargo, step))
        in_dst = self.in_transit_to[dst]
        tot_tr = self.total_in_transit
        res_tr = self.resources_transported
        in_dst[0] += cargo[0]
        in_dst[1] += cargo[1]
        in_dst[2] += cargo[2]
        in_dst[3] += cargo[3]
        tot_tr[0] += cargo[0]
        tot_tr[1] += cargo[1]
        tot_tr[2] += cargo[2]
        tot_tr[3] += cargo[3]
        res_tr[0] += cargo[0]
        res_tr[1] += cargo[1]
        res_tr[2] += cargo[2]
        res_tr[3] += cargo[3]
        n_flights = max(1, len(sources))
        self.transports_dispatched += n_flights
        if is_early:
            self.early_transports_dispatched += n_flights
        if step >= 0:
            self.planet_earmark_step[dst] = step
            info = self.step_transport_info.get(step)
            if info is None:
                self.step_transport_info[step] = {
                    "earliest_request_hr": self.clock,
                    "latest_arrival_hr": arrival,
                    "max_travel_hr": max_travel_hr,
                    "max_lead_hr": max_lead_hr,
                    "sources": list(sources),
                    "total_shipped": [cargo[0], cargo[1], cargo[2], cargo[3]],
                }
            else:
                if self.clock < float(info["earliest_request_hr"]):
                    info["earliest_request_hr"] = self.clock
                if arrival > float(info["latest_arrival_hr"]):
                    info["latest_arrival_hr"] = arrival
                if max_travel_hr > float(info["max_travel_hr"]):
                    info["max_travel_hr"] = max_travel_hr
                if max_lead_hr > float(info["max_lead_hr"]):
                    info["max_lead_hr"] = max_lead_hr
                src_list = info["sources"]
                for sc in sources:
                    if sc not in src_list:
                        src_list.append(sc)
                tot_sh = info["total_shipped"]
                tot_sh[0] += cargo[0]
                tot_sh[1] += cargo[1]
                tot_sh[2] += cargo[2]
                tot_sh[3] += cargo[3]

    def _enqueue_transport(
        self,
        src: int,
        dst: int,
        cargo: tuple[float, float, float, float],
        *,
        step: int,
        is_early: bool,
    ) -> None:
        self._enqueue_transport_batch(
            dst,
            cargo,
            sources=(str(self.planets[src]["coords"]),),
            max_travel_hr=self.travel_hours[src][dst],
            max_lead_hr=self.lead_hours[src][dst],
            step=step,
            is_early=is_early,
        )

    def _dispatch_transports_for_step(
        self,
        step: int,
        *,
        free_in: float = 0.0,
        protect_reserved: bool = False,
    ) -> bool:
        if not self.model_transport_travel:
            return False
        dst = self.pay_planet[step]
        earmarks = self.planet_earmark_step
        started = self.started
        if protect_reserved:
            ek_dst = earmarks[dst]
            if ek_dst >= 0:
                if started[ek_dst]:
                    earmarks[dst] = -1
                elif ek_dst != step:
                    # Another unstarted step is already earmarked on `dst`; avoid piling competing waves.
                    return False

        existing = self.step_transport_info.get(step)
        existing_rem = 0.0
        if existing is not None:
            existing_rem = float(existing["latest_arrival_hr"]) - self.clock
            if existing_rem > 1e-6 and protect_reserved:
                # Secondary wave already in transit for this step; wait for arrival.
                return False

        c0, c1, c2, c3 = self.cost[step]
        f0, f1, f2, f3 = self.cost_floor[step]
        t0, t1, t2, t3 = c0 - f0, c1 - f1, c2 - f2, c3 - f3
        ps_dst = self.planet_stock[dst]
        in_dst = self.in_transit_to[dst]
        h0 = ps_dst[0] + in_dst[0]
        h1 = ps_dst[1] + in_dst[1]
        h2 = ps_dst[2] + in_dst[2]
        h3 = ps_dst[3] + in_dst[3]
        if h0 >= f0 and h1 >= f1 and h2 >= f2 and h3 >= f3:
            return False

        need_metal_smelt = h1 < f1
        lr0, lr1, lr2, lr3 = self.planet_rate_on[dst] if (need_metal_smelt or self.smelting) else self.planet_rate_off[dst]
        if lr0 < 0.0:
            lr0 = 0.0
        if lr1 < 0.0:
            lr1 = 0.0
        if lr2 < 0.0:
            lr2 = 0.0
        if lr3 < 0.0:
            lr3 = 0.0

        check_wait = free_in if free_in > existing_rem else existing_rem
        if check_wait > 0.0:
            if (
                h0 + lr0 * check_wait >= f0
                and h1 + lr1 * check_wait >= f1
                and h2 + lr2 * check_wait >= f2
                and h3 + lr3 * check_wait >= f3
            ):
                return False

        res_dst = -1
        gs0 = gs1 = gs2 = gs3 = PATH_NEVER
        if protect_reserved and self.reserved >= 0:
            res_dst = self.pay_planet[self.reserved]
            rc0, rc1, rc2, rc3 = self.cost[self.reserved]
            in_res = self.in_transit_to[res_dst]
            st = self.stock
            gs0 = max(0.0, (st[0] + in_res[0]) - rc0)
            gs1 = max(0.0, (st[1] + in_res[1]) - rc1)
            gs2 = max(0.0, (st[2] + in_res[2]) - rc2)
            gs3 = max(0.0, (st[3] + in_res[3]) - rc3)
            if gs0 + gs1 + gs2 + gs3 <= 1e-6:
                return False

        elig_donors: list[tuple[int, float, float, float, float, float]] = []
        lead_mat = self.lead_hours
        p_stocks = self.planet_stock
        costs_all = self.cost
        clock = self.clock
        earliest_future_req = PATH_NEVER
        for src in self.donors_by_dst[dst]:
            if protect_reserved and src == res_dst:
                continue
            ps = p_stocks[src]
            a0, a1, a2, a3 = ps[0], ps[1], ps[2], ps[3]
            if protect_reserved:
                ek_src = earmarks[src]
                if ek_src >= 0:
                    if started[ek_src]:
                        earmarks[src] = -1
                    else:
                        ec0, ec1, ec2, ec3 = costs_all[ek_src]
                        a0 = a0 - ec0 if a0 > ec0 else 0.0
                        a1 = a1 - ec1 if a1 > ec1 else 0.0
                        a2 = a2 - ec2 if a2 > ec2 else 0.0
                        a3 = a3 - ec3 if a3 > ec3 else 0.0
            if a0 + a1 + a2 + a3 <= 1e-6:
                continue
            lead = lead_mat[src][dst]
            if free_in > lead + 1e-6:
                req_at = clock + free_in - lead
                if req_at > clock + 1e-6 and req_at < earliest_future_req:
                    earliest_future_req = req_at
                continue
            elig_donors.append((src, lead, a0, a1, a2, a3))

        if earliest_future_req < self.early_dispatch_at:
            self.early_dispatch_at = earliest_future_req

        if not elig_donors:
            return False

        pa0 = pa1 = pa2 = pa3 = 0.0
        chosen_idx = -1
        rq0 = rq1 = rq2 = rq3 = 0.0
        for idx, (_src, lead, a0, a1, a2, a3) in enumerate(elig_donors):
            pa0 += a0
            pa1 += a1
            pa2 += a2
            pa3 += a3
            if protect_reserved:
                if pa0 > gs0:
                    pa0 = gs0
                if pa1 > gs1:
                    pa1 = gs1
                if pa2 > gs2:
                    pa2 = gs2
                if pa3 > gs3:
                    pa3 = gs3
            eff_wait = free_in if free_in > lead else lead
            r0 = c0 - (h0 + lr0 * eff_wait)
            r1 = c1 - (h1 + lr1 * eff_wait)
            r2 = c2 - (h2 + lr2 * eff_wait)
            r3 = c3 - (h3 + lr3 * eff_wait)
            if r0 < 0.0:
                r0 = 0.0
            if r1 < 0.0:
                r1 = 0.0
            if r2 < 0.0:
                r2 = 0.0
            if r3 < 0.0:
                r3 = 0.0
            if r0 <= t0 and r1 <= t1 and r2 <= t2 and r3 <= t3:
                # Local production on `dst` will reach `cost` before donor `idx` can arrive.
                return False
            if pa0 + t0 >= r0 and pa1 + t1 >= r1 and pa2 + t2 >= r2 and pa3 + t3 >= r3:
                chosen_idx = idx
                rq0, rq1, rq2, rq3 = r0, r1, r2, r3
                break

        if chosen_idx < 0:
            return False

        b0 = b1 = b2 = b3 = 0.0
        used_sources: list[str] = []
        max_lead_used = 0.0
        max_travel_used = 0.0
        st = self.stock
        travel_mat = self.travel_hours
        planets_meta = self.planets
        for d_src, d_lead, a0, a1, a2, a3 in elig_donors[: chosen_idx + 1]:
            s0 = s1 = s2 = s3 = 0.0
            ps = p_stocks[d_src]
            if rq0 > 1e-9:
                cap = a0 if (not protect_reserved or a0 <= gs0) else gs0
                take = rq0 if rq0 <= cap else cap
                if take > 1e-9:
                    s0 = take
                    rq0 = rq0 - take if rq0 > take else 0.0
                    ps[0] = ps[0] - take if ps[0] > take else 0.0
                    st[0] = st[0] - take if st[0] > take else 0.0
                    if protect_reserved:
                        gs0 = gs0 - take if gs0 > take else 0.0
            if rq1 > 1e-9:
                cap = a1 if (not protect_reserved or a1 <= gs1) else gs1
                take = rq1 if rq1 <= cap else cap
                if take > 1e-9:
                    s1 = take
                    rq1 = rq1 - take if rq1 > take else 0.0
                    ps[1] = ps[1] - take if ps[1] > take else 0.0
                    st[1] = st[1] - take if st[1] > take else 0.0
                    if protect_reserved:
                        gs1 = gs1 - take if gs1 > take else 0.0
            if rq2 > 1e-9:
                cap = a2 if (not protect_reserved or a2 <= gs2) else gs2
                take = rq2 if rq2 <= cap else cap
                if take > 1e-9:
                    s2 = take
                    rq2 = rq2 - take if rq2 > take else 0.0
                    ps[2] = ps[2] - take if ps[2] > take else 0.0
                    st[2] = st[2] - take if st[2] > take else 0.0
                    if protect_reserved:
                        gs2 = gs2 - take if gs2 > take else 0.0
            if rq3 > 1e-9:
                cap = a3 if (not protect_reserved or a3 <= gs3) else gs3
                take = rq3 if rq3 <= cap else cap
                if take > 1e-9:
                    s3 = take
                    rq3 = rq3 - take if rq3 > take else 0.0
                    ps[3] = ps[3] - take if ps[3] > take else 0.0
                    st[3] = st[3] - take if st[3] > take else 0.0
                    if protect_reserved:
                        gs3 = gs3 - take if gs3 > take else 0.0
            if s0 + s1 + s2 + s3 > 1e-9:
                b0 += s0
                b1 += s1
                b2 += s2
                b3 += s3
                used_sources.append(str(planets_meta[d_src]["coords"]))
                if d_lead > max_lead_used:
                    max_lead_used = d_lead
                tr_hr = travel_mat[d_src][dst]
                if tr_hr > max_travel_used:
                    max_travel_used = tr_hr
                if not protect_reserved and earmarks[d_src] >= 0:
                    # #1 reserved drew from an earmarked colony; release that colony's old earmark.
                    earmarks[d_src] = -1

        if b0 + b1 + b2 + b3 <= 1e-9 or not used_sources:
            return False

        self._enqueue_transport_batch(
            dst,
            (b0, b1, b2, b3),
            sources=used_sources,
            max_travel_hr=max_travel_used,
            max_lead_hr=max_lead_used,
            step=step,
            is_early=(free_in > 0.0),
        )
        return True

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
        avail = [self.stock[p] + self.total_in_transit[p] for p in range(4)]
        metal_missing = max(0.0, self.need[1] - avail[1])
        missing = (
            max(0.0, self.need[0] - avail[0] + 2.0 * metal_missing),
            metal_missing,
            max(0.0, self.need[2] - avail[2]),
            max(0.0, self.need[3] - avail[3]),
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
        event_limit = 160 * len(self.steps) + 20_000
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
            until_done = max(0.0, self.heap[0][0] - self.clock) if self.heap else PATH_NEVER
            until_transit = max(0.0, self.transit_heap[0][0] - self.clock) if self.transit_heap else PATH_NEVER
            until_stock = self._hours_to_stock_event()
            until_early = (
                max(1e-9, self.early_dispatch_at - self.clock)
                if (self.model_transport_travel and self.unstarted > 0 and self.early_dispatch_at < PATH_NEVER)
                else PATH_NEVER
            )
            until_review = PATH_NEVER
            if (
                self.unstarted > 0
                and (self.heap or self.transit_heap or until_stock < PATH_NEVER)
                and self.review_at < PATH_NEVER
            ):
                until_review = max(1e-9, self.review_at - self.clock)
            elapsed = min(until_done, until_transit, until_stock, until_early, until_review)
            if elapsed == PATH_NEVER:
                if self.reserved < 0 and limit < PATH_NEVER:
                    self._advance(max(0.0, limit - self.clock), ends_a_step=False, ends_a_transit=False)
                    status = "reached" if (self.unstarted == 0 and not self.heap) else "horizon"
                else:
                    status = "stuck"
                break
            until_limit = limit - self.clock
            if until_limit < elapsed:
                self._advance(max(0.0, until_limit), ends_a_step=False, ends_a_transit=False)
                status = "reached" if (self.unstarted == 0 and not self.heap) else "horizon"
                break
            events += 1
            if events > event_limit:
                raise RuntimeError(f"Path simulation did not finish within {event_limit} events")
            self._advance(
                max(0.0, elapsed),
                ends_a_step=(elapsed == until_done and bool(self.heap)),
                ends_a_transit=(elapsed == until_transit and bool(self.transit_heap)),
            )

        if not self._smelting_initialized:
            self._record_smelting_transition()
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
            "initial_blast_furnace": self.initial_blast_furnace,
            "initial_smelting_target": bool(self.initial_smelting_target),
            "initial_toggle_needed": bool(self.initial_toggle_needed),
            "initial_planets_needing_toggle": [dict(row) for row in self.initial_planets_needing_toggle],
            "smelting_events": [dict(ev) for ev in self.smelting_events],
            "events": events,
            "reviews": self.reviews,
            "final_stock": tuple(self.stock),
            "final_planet_stock": [tuple(row) for row in self.planet_stock],
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
            "transports_dispatched": int(self.transports_dispatched),
            "early_transports_dispatched": int(self.early_transports_dispatched),
            "resources_transported": tuple(self.resources_transported),
            "step_transport_info": {
                k: {
                    "earliest_request_hr": float(v["earliest_request_hr"]),
                    "latest_arrival_hr": float(v["latest_arrival_hr"]),
                    "max_travel_hr": float(v["max_travel_hr"]),
                    "max_lead_hr": float(v["max_lead_hr"]),
                    "sources": list(v["sources"]),
                    "total_shipped": tuple(v["total_shipped"]),
                }
                for k, v in self.step_transport_info.items()
            },
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
        if all("resources" in planet for planet in self.planets):
            self.planet_stock = [
                [max(0.0, float(val)) for val in planet["resources"]] for planet in self.planets
            ]
        else:
            self.planet_stock = [[0.0, 0.0, 0.0, 0.0] for _ in range(count)]
            if count > 0:
                self.planet_stock[self.central_planet] = list(self.stock)
        if count > 0:
            # Ensure exact initial sum consistency between `planet_stock` and `self.stock`
            for pos in range(4):
                ps_sum = sum(self.planet_stock[p][pos] for p in range(count))
                diff = self.stock[pos] - ps_sum
                if diff > 1e-6:
                    self.planet_stock[self.central_planet][pos] += diff
                elif self.model_transport_travel:
                    self.stock[pos] = ps_sum

        self.levels = [dict(planet["levels"]) for planet in self.planets]
        self.research = dict(self.problem["research_levels"])
        self.solar = [int(planet["solar_cells"]) for planet in self.planets]
        self.founded = [bool(planet["founded"]) for planet in self.planets]
        self.fields_used = [sum(levels.values()) for levels in self.levels]
        self.next_step = [0] * len(self.chain_steps)
        self.queue_step = [-1] * self.queue_count
        self.queue_end = [0.0] * self.queue_count
        self.queue_initial_count = [0] * self.queue_count
        self.heap: list[tuple[float, int]] = []
        self.transit_heap: list[tuple[float, int, int, tuple[float, float, float, float], int]] = []
        self._transit_seq = 0
        self.in_transit_to: list[list[float]] = [[0.0, 0.0, 0.0, 0.0] for _ in range(count)]
        self.total_in_transit: list[float] = [0.0, 0.0, 0.0, 0.0]
        self.early_dispatch_at = PATH_NEVER
        self.step_transport_info: dict[int, dict[str, Any]] = {}
        self.planet_earmark_step: list[int] = [-1] * count
        self.transports_dispatched = 0
        self.early_transports_dispatched = 0
        self.resources_transported = [0.0, 0.0, 0.0, 0.0]

        # Reset transport travel/lead matrix to starting research levels
        self.travel_hours = [list(row) for row in self._base_travel_hours]
        self._sync_lead_matrix()

        # Pre-load live in-flight incoming friendly cargo arrivals
        for arrival in self.initial_arrivals:
            dst = int(arrival["planet"])
            if not (0 <= dst < count):
                continue
            rem_hr = max(
                1e-6,
                float(arrival.get("remaining_hours", float(arrival.get("remaining_seconds", 60.0)) / 3600.0)),
            )
            cargo_raw = arrival.get("cargo_tuple")
            if cargo_raw is None and isinstance(arrival.get("cargo"), Mapping):
                cargo_raw = tuple(max(0.0, float(arrival["cargo"].get(r, 0.0))) for r in RESOURCE_KEYS)
            if not cargo_raw:
                continue
            cargo = (float(cargo_raw[0]), float(cargo_raw[1]), float(cargo_raw[2]), float(cargo_raw[3]))
            if cargo[0] + cargo[1] + cargo[2] + cargo[3] <= 0.0:
                continue
            self._transit_seq += 1
            heapq.heappush(self.transit_heap, (rem_hr, self._transit_seq, dst, cargo, -1))
            for pos in range(4):
                self.in_transit_to[dst][pos] += cargo[pos]
                self.total_in_transit[pos] += cargo[pos]

        # Pre-load live in-progress buildings & research (already paid; occupy queues until completion)
        for job_idx, job in enumerate(self.initial_jobs):
            q = int(job["queue"])
            rem_hr = max(
                1e-6,
                float(job.get("remaining_hours", float(job.get("remaining_seconds", 60.0)) / 3600.0)),
            )
            neg_id = -(2 + job_idx)
            heapq.heappush(self.heap, (rem_hr, neg_id))
            if 0 <= q < self.queue_count:
                self.queue_initial_count[q] += 1
                self.queue_step[q] = neg_id
                self.queue_end[q] = max(self.queue_end[q], rem_hr)
            if str(job.get("kind") or "") == "build":
                p_idx = int(job["planet"])
                if 0 <= p_idx < count:
                    self.fields_used[p_idx] += 1

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
        self.smelting_events: list[dict[str, Any]] = []
        self._smelting_initialized = False
        self._last_recorded_smelt = False
        self.initial_smelting_target = False
        self.initial_toggle_needed = False
        self.initial_planets_needing_toggle: list[dict[str, Any]] = []
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
            if self.queue_step[queue] != -1:
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
        s0 = self.stock[0] + self.total_in_transit[0]
        s1 = self.stock[1] + self.total_in_transit[1]
        s2 = self.stock[2] + self.total_in_transit[2]
        s3 = self.stock[3] + self.total_in_transit[3]
        metal = max(0.0, self.need[1] - s1)
        return (
            max(0.0, self.need[0] + 2.0 * metal - s0),
            metal,
            max(0.0, self.need[2] - s2),
            max(0.0, self.need[3] - s3),
        )

    def horizon_missing_resources(self) -> tuple[float, float, float, float]:
        s0 = self.stock[0] + self.total_in_transit[0]
        s1 = self.stock[1] + self.total_in_transit[1]
        s2 = self.stock[2] + self.total_in_transit[2]
        s3 = self.stock[3] + self.total_in_transit[3]
        metal = max(0.0, self.horizon_need[1] - s1)
        return (
            max(0.0, self.horizon_need[0] + 2.0 * metal - s0),
            metal,
            max(0.0, self.horizon_need[2] - s2),
            max(0.0, self.horizon_need[3] - s3),
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
        if not self.model_transport_travel:
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

        dst = self.pay_planet[self.reserved]
        have_or_inbound = [self.planet_stock[dst][pos] + self.in_transit_to[dst][pos] for pos in range(4)]
        if all(have_or_inbound[pos] >= floor[pos] for pos in range(4)):
            # Full cost is already landed or in transit to `dst`; `until_transit` handles arrival.
            return PATH_NEVER

        local_rates = self._planet_net_rates(dst, smelting=self.smelting)
        max_lead = self.max_lead_hours_by_dst[dst]
        min_lead = self.min_lead_hours_by_dst[dst]

        # 1. Time until empire-wide supply triggers donor dispatch (accounting for `dst`'s local output over flight)
        dispatch_wait = 0.0
        dispatch_possible = True
        positions = (1,) if self.smelting else (0, 2, 3)
        rates = self.rate_on if self.smelting else self.rate_off
        for pos in positions:
            if have_or_inbound[pos] >= floor[pos]:
                continue
            eff_avail = self.stock[pos] + self.in_transit_to[dst][pos] + max(0.0, local_rates[pos]) * max_lead
            if eff_avail < floor[pos]:
                rate = rates[pos]
                if rate <= 0.0:
                    dispatch_possible = False
                    break
                dispatch_wait = max(dispatch_wait, (cost[pos] - eff_avail) / rate)

        if dispatch_possible and dispatch_wait > 1e-12:
            # Also check if `dst`'s own local production finishes sooner than waiting to dispatch + fly
            local_wait = self._local_finish_wait_hours(dst, self.reserved, local_rates)
            return min(dispatch_wait, local_wait)

        # 2. Either dispatch condition is already satisfied locally (dst beats donor flight) or waiting on dst local production
        local_wait = self._local_finish_wait_hours(dst, self.reserved, local_rates)
        if local_wait < PATH_NEVER and local_wait > 1e-12:
            return local_wait

        # 3. Fallback when `max_lead` satisfied `eff_avail` via a distant donor's lead projection,
        #    while actual closest donor still needs slightly more stock to trigger dispatch.
        if dispatch_possible and min_lead < max_lead:
            alt_wait = 0.0
            for pos in positions:
                if have_or_inbound[pos] >= floor[pos]:
                    continue
                eff_min = self.stock[pos] + self.in_transit_to[dst][pos] + max(0.0, local_rates[pos]) * min_lead
                if eff_min < floor[pos]:
                    rate = rates[pos]
                    if rate <= 0.0:
                        return PATH_NEVER
                    alt_wait = max(alt_wait, (cost[pos] - eff_min) / rate)
            if alt_wait > 1e-12:
                return alt_wait

        return PATH_NEVER

    def _local_finish_wait_hours(
        self,
        dst: int,
        step: int,
        local_rates: tuple[float, float, float, float],
    ) -> float:
        cost, floor = self.cost[step], self.cost_floor[step]
        have_or_inbound = [self.planet_stock[dst][pos] + self.in_transit_to[dst][pos] for pos in range(4)]
        positions = (1,) if self.smelting else (0, 2, 3)
        wait = 0.0
        for pos in positions:
            if have_or_inbound[pos] < floor[pos]:
                lr = local_rates[pos]
                if lr <= 0.0:
                    return PATH_NEVER
                wait = max(wait, (cost[pos] - have_or_inbound[pos]) / lr)
        return wait

    def _advance(self, elapsed: float, *, ends_a_step: bool, ends_a_transit: bool = False) -> None:
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

            if self.model_transport_travel:
                p_rates = self.planet_rate_on if self.smelting else self.planet_rate_off
                spice_deficit = 0.0
                for planet in range(self.planet_count):
                    if not self.founded[planet]:
                        continue
                    pr0, pr1, pr2, pr3 = p_rates[planet]
                    p_stock = self.planet_stock[planet]
                    v0 = p_stock[0] + pr0 * elapsed
                    v1 = p_stock[1] + pr1 * elapsed
                    v2 = p_stock[2] + pr2 * elapsed
                    p_stock[0] = v0 if v0 > 0.0 else 0.0
                    p_stock[1] = v1 if v1 > 0.0 else 0.0
                    p_stock[2] = v2 if v2 > 0.0 else 0.0
                    dp3 = pr3 * elapsed
                    if dp3 >= 0.0:
                        p_stock[3] += dp3
                    else:
                        burn = -dp3
                        if p_stock[3] >= burn:
                            p_stock[3] -= burn
                        else:
                            spice_deficit += burn - p_stock[3]
                            p_stock[3] = 0.0
                # Conserve empire Spice across colonies when a Fusion-powered warm colony burns Spice
                if spice_deficit > 1e-9:
                    total_spice = sum(self.planet_stock[p][3] for p in range(self.planet_count))
                    if total_spice > 1e-9:
                        ratio = min(1.0, spice_deficit / total_spice)
                        scale = 1.0 - ratio
                        for p in range(self.planet_count):
                            self.planet_stock[p][3] = max(0.0, self.planet_stock[p][3] * scale)
                    else:
                        for p in range(self.planet_count):
                            self.planet_stock[p][3] = 0.0
                s0 = s1 = s2 = s3 = 0.0
                for p in range(self.planet_count):
                    ps = self.planet_stock[p]
                    s0 += ps[0]
                    s1 += ps[1]
                    s2 += ps[2]
                    s3 += ps[3]
                self.stock[0] = s0
                self.stock[1] = s1
                self.stock[2] = s2
                self.stock[3] = s3
            else:
                for position in range(4):
                    self.stock[position] += rates[position] * elapsed

            if self.smelting:
                self.metal_smelted += self.furnace_metal * elapsed
                if self.smelting_spans and self.smelting_spans[-1][1] == self.clock:
                    self.smelting_spans[-1][1] = self.clock + elapsed
                else:
                    self.smelting_spans.append([self.clock, self.clock + elapsed])

        if ends_a_step and self.heap:
            self.clock = self.heap[0][0]
        elif ends_a_transit and self.transit_heap:
            self.clock = self.transit_heap[0][0]
        else:
            self.clock += elapsed

        # Deliver all arrived inter-planet / live in-flight cargo
        while self.transit_heap and self.transit_heap[0][0] <= self.clock + 1e-12:
            _arr_hr, _seq_id, dst, cargo, _for_step = heapq.heappop(self.transit_heap)
            for pos in range(4):
                self.in_transit_to[dst][pos] = max(0.0, self.in_transit_to[dst][pos] - cargo[pos])
                self.total_in_transit[pos] = max(0.0, self.total_in_transit[pos] - cargo[pos])
                if self.model_transport_travel:
                    self.planet_stock[dst][pos] += cargo[pos]
                self.stock[pos] += cargo[pos]

        while self.heap and self.heap[0][0] <= self.clock + 1e-12:
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
        dst = self.pay_planet[step]
        for position in range(4):
            if self.model_transport_travel:
                self.planet_stock[dst][position] = max(0.0, self.planet_stock[dst][position] - cost[position])
            self.stock[position] = max(0.0, self.stock[position] - cost[position])
            self.need[position] -= cost[position]
            if self.in_horizon[step]:
                self.horizon_need[position] = max(0.0, self.horizon_need[position] - cost[position])
        hours = self.duration(step)
        self.started[step] = True
        if self.planet_earmark_step[dst] == step:
            self.planet_earmark_step[dst] = -1
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

    def _complete_initial_job(self, job_idx: int) -> None:
        job = self.initial_jobs[job_idx]
        q = int(job["queue"])
        if 0 <= q < self.queue_count:
            self.queue_initial_count[q] = max(0, self.queue_initial_count[q] - 1)
            if self.queue_initial_count[q] == 0:
                self.queue_step[q] = -1
                self.queue_end[q] = 0.0
        self.completions_since_review += 1
        if str(job.get("kind") or "") == "research":
            rkey = str(job["key"])
            self.research[rkey] = max(self.research.get(rkey, 0), int(job["level_to"]))
            self.dirty.add(self.research_queue)
            self.dirty.update(self.research_dependents.get(rkey, ()))
            if rkey in ("r1", "r2"):
                for other in range(self.planet_count):
                    self._refresh_planet(other)
                self._refresh_rates()
                self.dirty.update(range(self.planet_count))
            if rkey in ("r4", "r5") and self.model_transport_travel:
                self._rebuild_transport_matrix(self.research)
            return

        planet = int(job["planet"])
        bkey = str(job["key"])
        self.levels[planet][bkey] = max(self.levels[planet].get(bkey, 0), int(job["level_to"]))
        if bkey in PATH_FLOW_BUILDING_KEYS:
            self._refresh_planet(planet)
            if self.founded[planet]:
                self._refresh_rates()
        elif bkey == "b10" and self.founded[planet] and self.fleet_command_center_level is None:
            self._refresh_rates()
        self.dirty.add(planet)
        if planet == self.lab_planet:
            self.dirty.add(self.research_queue)
        if planet == self.central_planet:
            self.dirty.update(other for other in range(self.planet_count) if not self.founded[other])

    def _complete(self, step: int) -> None:
        if step < 0:
            self._complete_initial_job((-step) - 2)
            return
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
            if key in ("r4", "r5") and self.model_transport_travel:
                self._rebuild_transport_matrix(self.research)
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

    def _format_smelt_step_label(self, step_id: int) -> str:
        if 0 <= step_id < len(self.steps):
            st = self.steps[step_id]
            p_idx = int(st["planet"])
            where = f" on {self.planets[p_idx]['coords']}" if p_idx >= 0 else ""
            return f"{st['name']} L{int(st['level_to'])}{where}"
        return ""

    def _record_smelting_transition(self) -> None:
        effective_smelt = bool(self.smelting) and self.b2_founded_count > 0
        if (not self._smelting_initialized) or (
            self.clock <= 1e-12 and not any(not ev["is_initial"] for ev in self.smelting_events)
        ):
            self._smelting_initialized = True
            self._last_recorded_smelt = effective_smelt
            self.initial_smelting_target = effective_smelt
            target_pct = 100 if effective_smelt else 0
            needing_toggle = [
                {
                    "planet_id": int(row["planet_id"]),
                    "coords": str(row["coords"]),
                    "name": str(row["name"]),
                    "b2_level": int(row["b2_level"]),
                    "from_pct": int(row["initial_pct"]),
                    "to_pct": target_pct,
                }
                for row in self._initial_b2_capable_planets
                if int(row["initial_pct"]) != target_pct
            ]
            self.initial_planets_needing_toggle = needing_toggle
            self.initial_toggle_needed = bool(needing_toggle)
            if self.smelting_events and self.smelting_events[-1]["is_initial"]:
                self.smelting_events.pop()
            if needing_toggle:
                live_mode = str(self.initial_blast_furnace["mode"])
                live_desc = (
                    f"{live_mode.upper()} "
                    f"({self.initial_blast_furnace['enabled_count']}/{self.initial_blast_furnace['total_b2_planets']} ON)"
                )
                step_lbl = self._format_smelt_step_label(self.reserved)
                if effective_smelt:
                    reason = (
                        f"Initial live b2 {live_desc} -> ENABLE 100%: Metal needed for {step_lbl}"
                        if step_lbl
                        else f"Initial live b2 {live_desc} -> ENABLE 100%"
                    )
                elif step_lbl:
                    reason = f"Initial live b2 {live_desc} -> DISABLE 0%: Metal sufficient for {step_lbl}"
                else:
                    reason = f"Initial live b2 {live_desc} -> DISABLE 0%: No waiting step requires Metal"
                self.smelting_events.append(
                    {
                        "time_hours": 0.0,
                        "action": "enable" if effective_smelt else "disable",
                        "enabled": effective_smelt,
                        "target_pct": target_pct,
                        "from_mode": live_mode,
                        "from_enabled": bool(self.initial_blast_furnace["any_enabled"]),
                        "is_initial": True,
                        "reserved_step_id": int(self.reserved),
                        "affected_planets": needing_toggle,
                        "reason": reason,
                    }
                )
            return

        if effective_smelt == self._last_recorded_smelt:
            return

        prev_enabled = self._last_recorded_smelt
        self._last_recorded_smelt = effective_smelt
        target_pct = 100 if effective_smelt else 0

        if (
            self.smelting_events
            and not self.smelting_events[-1]["is_initial"]
            and abs(float(self.smelting_events[-1]["time_hours"]) - self.clock) <= 1e-12
        ):
            if bool(self.smelting_events[-1]["from_enabled"]) == effective_smelt:
                self.smelting_events.pop()
                return
            self.smelting_events.pop()

        step_lbl = self._format_smelt_step_label(self.reserved)
        if effective_smelt:
            reason = (
                f"ENABLE 100%: Metal shortfall for {step_lbl}"
                if step_lbl
                else "ENABLE 100%: Metal required"
            )
        elif step_lbl:
            reason = f"DISABLE 0%: Metal satisfied for {step_lbl}"
        else:
            reason = "DISABLE 0%: Queue complete or no waiting Metal shortfall"

        self.smelting_events.append(
            {
                "time_hours": float(self.clock),
                "action": "enable" if effective_smelt else "disable",
                "enabled": effective_smelt,
                "target_pct": target_pct,
                "from_mode": "on" if prev_enabled else "off",
                "from_enabled": prev_enabled,
                "is_initial": False,
                "reserved_step_id": int(self.reserved),
                "affected_planets": [],
                "reason": reason,
            }
        )

    def _decide(self) -> None:
        self.early_dispatch_at = PATH_NEVER
        for _ in range(self.queue_count + 2):
            if not self._start_proposals(self.strategy.proposals(self, forced=False)):
                break
        if self.reserved < 0 and not self.heap and not self.transit_heap and self.unstarted > 0:
            allow_forced = (
                self.clock == 0.0
                or self.run_limit_hours == PATH_NEVER
                or self._full_horizon_hours() <= self.run_limit_hours
            )
            if allow_forced:
                for _ in range(self.queue_count + 2):
                    if not self._start_proposals(self.strategy.proposals(self, forced=True)):
                        break
        self._record_smelting_transition()

    def _start_proposals(self, proposals: list[tuple[tuple, int]]) -> bool:
        reserved = -1
        limit = 0.0
        started_any = False
        stock = self.stock
        patience = self.strategy.patience
        use_travel = self.model_transport_travel
        busy_candidates: list[tuple[int, float]] = []
        free_wait_candidates: list[int] = []
        seen_busy_queues: set[int] = set()
        seen_free_wait_queues: set[int] = set()
        eff_stock = (
            [stock[p] + self.total_in_transit[p] for p in range(4)]
            if use_travel
            else stock
        )

        for _priority, step in proposals:
            queue = self.queue[step]
            if self.started[step]:
                continue
            if queue >= 0 and self.queue_step[queue] != -1:
                if (
                    use_travel
                    and queue not in seen_busy_queues
                    and self.is_next_of_its_chain(step)
                    and not self.blockers(step)
                ):
                    seen_busy_queues.add(queue)
                    busy_candidates.append((step, max(0.0, self.queue_end[queue] - self.clock)))
                continue
            floor = self.cost_floor[step]
            pay_stock = self.planet_stock[self.pay_planet[step]] if use_travel else stock
            if (
                pay_stock[0] >= floor[0]
                and pay_stock[1] >= floor[1]
                and pay_stock[2] >= floor[2]
                and pay_stock[3] >= floor[3]
            ):
                if reserved >= 0:
                    hours = self.duration(step)
                    if queue >= 0 and queue == self.queue[reserved] and hours > limit:
                        continue
                    cost = self.cost[step]
                    delayed = self.wait_hours(
                        [eff_stock[position] - cost[position] for position in range(4)],
                        reserved,
                    )
                    if delayed > limit + patience * hours + 1e-9 * limit + 1e-9:
                        continue
                    if not self.is_next_of_its_chain(step) or self.blockers(step):
                        continue
                    limit = max(limit, delayed)
                elif not self.is_next_of_its_chain(step) or self.blockers(step):
                    continue
                self._start(step)
                started_any = True
                if use_travel:
                    eff_stock = [stock[p] + self.total_in_transit[p] for p in range(4)]
            elif reserved < 0 and self.is_next_of_its_chain(step) and not self.blockers(step):
                reserved = step
                limit = self.wait_hours(eff_stock, step)
            elif (
                use_travel
                and reserved >= 0
                and queue >= 0
                and queue != self.queue[reserved]
                and queue not in seen_free_wait_queues
                and self.is_next_of_its_chain(step)
                and not self.blockers(step)
            ):
                seen_free_wait_queues.add(queue)
                free_wait_candidates.append(step)

        self.reserved = reserved
        if not use_travel:
            self.smelting = reserved >= 0 and stock[1] < self.cost_floor[reserved][1]
            return started_any

        # Coordinate-aware inter-planet transport scheduling:
        # 1. Dispatch for #1 reserved step first (never starved by lower-priority early dispatches).
        if reserved >= 0:
            self._dispatch_transports_for_step(reserved, free_in=0.0, protect_reserved=False)
        # 2. Early pre-ship for busy queues entering their `dispatch_delay + travel_time` window.
        res_queue = self.queue[reserved] if reserved >= 0 else -1
        for cand_step, free_in in busy_candidates:
            if reserved >= 0:
                c_hours = self.duration(cand_step)
                if self.queue[cand_step] == res_queue and c_hours > limit:
                    continue
                c_cost = self.cost[cand_step]
                delayed = self.wait_hours(
                    (
                        eff_stock[0] - c_cost[0],
                        eff_stock[1] - c_cost[1],
                        eff_stock[2] - c_cost[2],
                        eff_stock[3] - c_cost[3],
                    ),
                    reserved,
                )
                if delayed > limit + patience * c_hours + 1e-9 * limit + 1e-9:
                    continue
            self._dispatch_transports_for_step(cand_step, free_in=free_in, protect_reserved=True)
        # 3. Also supply secondary free queues using genuine surplus above `reserved`
        #    (only when the step actually passes the patience gate so cargo cannot ping-pong).
        for cand_step in free_wait_candidates:
            if reserved >= 0:
                c_hours = self.duration(cand_step)
                if self.queue[cand_step] == res_queue and c_hours > limit:
                    continue
                c_cost = self.cost[cand_step]
                delayed = self.wait_hours(
                    (
                        eff_stock[0] - c_cost[0],
                        eff_stock[1] - c_cost[1],
                        eff_stock[2] - c_cost[2],
                        eff_stock[3] - c_cost[3],
                    ),
                    reserved,
                )
                if delayed > limit + patience * c_hours + 1e-9 * limit + 1e-9:
                    continue
            self._dispatch_transports_for_step(cand_step, free_in=0.0, protect_reserved=True)

        if reserved >= 0:
            res_dst = self.pay_planet[reserved]
            res_floor_m = self.cost_floor[reserved][1]
            dst_metal = self.planet_stock[res_dst][1] + self.in_transit_to[res_dst][1]
            if dst_metal >= res_floor_m:
                self.smelting = False
            elif stock[1] + self.in_transit_to[res_dst][1] < res_floor_m:
                self.smelting = True
            else:
                # Donor metal exists elsewhere, yet dispatch was skipped because `res_dst`'s own
                # blast furnace can finish smelting faster than donor flight; keep smelting ON.
                self.smelting = self.output[res_dst][2] > 0.0
        else:
            self.smelting = False
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


_GENE_NAMES: tuple[str, ...] = tuple(PATH_GENE_RANGES.keys())
_VALUE_GENE_NAMES: tuple[str, ...] = (
    "value_pig_iron",
    "value_metal",
    "value_kryptonite",
    "value_spice",
)
_WARM_GENE_DEFAULTS: dict[str, float] = {
    "focus": 1.0,
    "critical_share": 0.9,
    "patience": 0.25,
    "speed_weight": 1.5,
    "value_pig_iron": 0.0,
    "value_metal": 0.0,
    "value_kryptonite": 0.0,
    "value_spice": 0.0,
}


def _parse_warm_start_genes(raw: Any) -> Optional[tuple[float, ...]]:
    """Parse optional `warm_start_genes` (gene dict, `best_parameters` dict, or 8-float sequence) clamped to `PATH_GENE_RANGES`."""
    if raw is None:
        return None
    bounds = [PATH_GENE_RANGES[name] for name in _GENE_NAMES]

    def _clamp(idx: int, val: float) -> float:
        lo, hi = bounds[idx]
        return min(hi, max(lo, float(val)))

    if isinstance(raw, Mapping):
        if str(raw.get("rule") or "").strip().lower() == "cheapest":
            return None
        recognized = set(_GENE_NAMES) | {"bias"}
        if not any(k in raw for k in recognized):
            return None
        raw_bias = raw.get("bias")
        bias_seq: Optional[list[float]] = None
        if (
            isinstance(raw_bias, (Sequence, np.ndarray))
            and not isinstance(raw_bias, (str, bytes, bytearray))
            and len(raw_bias) == 4
        ):
            parsed_bias: list[float] = []
            for item in raw_bias:
                if isinstance(item, bool):
                    parsed_bias = []
                    break
                fv = game_data.safe_finite_float(item, float("nan"))
                if not math.isfinite(fv) or fv <= 0.0:
                    parsed_bias = []
                    break
                parsed_bias.append(fv)
            if len(parsed_bias) == 4:
                bias_seq = parsed_bias

        vals: list[float] = []
        for idx, gname in enumerate(_GENE_NAMES):
            raw_v = raw.get(gname)
            if raw_v is not None and not isinstance(raw_v, bool):
                fv = game_data.safe_finite_float(raw_v, float("nan"))
                if not math.isfinite(fv):
                    return None
                vals.append(_clamp(idx, fv))
            elif gname in _VALUE_GENE_NAMES and bias_seq is not None:
                v_pos = _VALUE_GENE_NAMES.index(gname)
                vals.append(_clamp(idx, math.log2(max(1e-6, bias_seq[v_pos]))))
            else:
                vals.append(_clamp(idx, _WARM_GENE_DEFAULTS[gname]))
        return tuple(vals)

    if isinstance(raw, (Sequence, np.ndarray)) and not isinstance(raw, (str, bytes, bytearray)):
        if len(raw) != len(_GENE_NAMES):
            return None
        vals = []
        for idx, item in enumerate(raw):
            if isinstance(item, bool):
                return None
            fv = game_data.safe_finite_float(item, float("nan"))
            if not math.isfinite(fv):
                return None
            vals.append(_clamp(idx, fv))
        return tuple(vals)

    return None


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
    merged["transport_dispatch_delay_minutes"] = min(
        1440.0,
        max(0.0, game_data.safe_finite_float(merged.get("transport_dispatch_delay_minutes"), 120.0)),
    )
    if "warm_start_genes" in merged:
        parsed_warm = _parse_warm_start_genes(merged.get("warm_start_genes"))
        if parsed_warm is not None:
            merged["warm_start_genes"] = {
                name: round(float(g), 6) for name, g in zip(_GENE_NAMES, parsed_warm)
            }
        else:
            merged.pop("warm_start_genes", None)
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
    for job in simulator.initial_jobs:
        if str(job.get("kind") or "") == "build" and str(job.get("key") or "") in started_building_max:
            jk = str(job["key"])
            started_building_max[jk] = max(started_building_max[jk], int(job["level_to"]))
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
    for job in simulator.initial_jobs:
        if str(job.get("kind") or "") == "research" and str(job.get("key") or "") in started_research:
            jk = str(job["key"])
            started_research[jk] = max(started_research[jk], int(job["level_to"]))
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
    param_planet_names: dict[str, str] = {}
    if isinstance(params, Mapping):
        param_planet_names.update(normalize_planet_names_map(params.get("custom_planet_names")))
        param_planet_names.update(normalize_planet_names_map(params.get("planet_names")))
    problem = build_path_problem(
        state,
        target_config,
        pad_generic_colonies=cfg["pad_generic_colonies"],
        colony_founding_hours=float(cfg.get("colony_founding_hours", 0.0)),
        planet_names=param_planet_names or None,
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

    # Realized basis: raid income per fleet slot comes from loot and recycling actually received,
    # not from every slot raiding back to back. Only the API passes it (adaptive execution on).
    # An explicit `attacks_per_day` still wins over either basis.
    raw_realized_slot = telemetry.get("income_per_slot_per_hour")
    use_realized_income = (
        cfg["attacks_per_day"] is None
        and is_realized_income_basis(telemetry)
        and isinstance(raw_realized_slot, Mapping)
    )

    fixed_cc = cfg["fleet_command_center_level"]
    if fixed_cc is not None and fixed_cc < 0:
        raise ValueError(f"fleet_command_center_level must be >= 0 or None, got {fixed_cc!r}")

    if use_realized_income:
        slot_attack_income = {
            r: max(0.0, game_data.safe_finite_float(raw_realized_slot.get(r), 0.0)) for r in RESOURCE_KEYS
        }
        fixed_attack_income = {r: 0.0 for r in RESOURCE_KEYS}
        pace_set_by = (
            "realized loot and recycling per Fleet base (b10) slot"
            if fixed_cc is None
            else f"realized loot and recycling per fleet_command_center_level ({fixed_cc}) slot"
        )
    elif cfg["attacks_per_day"] is None:
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
        transport_dispatch_delay_minutes=cfg["transport_dispatch_delay_minutes"],
        model_transport_travel=True,
    )

    if progress_cb is not None:
        progress_cb("simulating_baselines", 0.12)

    warm_genes = _parse_warm_start_genes(
        params.get("warm_start_genes") if isinstance(params, Mapping) else None
    )

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
    if warm_genes is not None:
        strategies.append(
            strategy_from_genes("warm-start tuned strategy", warm_genes, cfg["revalue_every_steps"])
        )
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
        "warm_start_used": warm_genes is not None,
        "warm_start_genes": (
            {name: round(float(g), 6) for name, g in zip(PATH_GENE_RANGES, warm_genes)}
            if warm_genes is not None
            else None
        ),
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
                warm_genes
                if warm_genes is not None
                else (
                    cfg["scarcity_focus"],
                    cfg["critical_queue_share"],
                    cfg["patience"],
                    cfg["speed_weight"],
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                )
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
    search_summary["best_parameters"] = best_strategy.parameters()
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
    step_tr_map = best_run.get("step_transport_info") if isinstance(best_run.get("step_transport_info"), Mapping) else {}
    dispatch_delay_sec = round(simulator.dispatch_delay_hours * 3600.0, 2)
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
        tr_info = step_tr_map.get(step_id)
        needs_transport = isinstance(tr_info, Mapping)
        if needs_transport:
            req_hr = max(0.0, float(tr_info.get("earliest_request_hr", start_hr)))
            tr_req_sec: Optional[float] = round(req_hr * 3600.0, 2)
            tr_req_txt: Optional[str] = "now" if req_hr <= 1e-6 else fmt_path_time(req_hr)
            tr_travel_sec = round(max(0.0, float(tr_info.get("max_travel_hr", 0.0))) * 3600.0, 2)
            tr_lead_sec = round(max(0.0, float(tr_info.get("max_lead_hr", 0.0))) * 3600.0, 2)
            tr_sources = [str(x) for x in (tr_info.get("sources") or ())]
        else:
            tr_req_sec = None
            tr_req_txt = None
            tr_travel_sec = 0.0
            tr_lead_sec = 0.0
            tr_sources = []

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
                "needs_transport": needs_transport,
                "transport_request_at_sec": tr_req_sec,
                "transport_request_text": tr_req_txt,
                "transport_travel_sec": tr_travel_sec,
                "transport_dispatch_delay_sec": dispatch_delay_sec if needs_transport else 0.0,
                "transport_lead_sec": tr_lead_sec,
                "transport_sources": tr_sources,
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
    if cfg["attacks_per_day"] is not None:
        tel_per_slot = per_slot_hour
        tel_attacks_per_hour = float(cfg["attacks_per_day"]) / 24.0
    elif use_realized_income:
        # Keep the measured mission rate that came with the realized telemetry.
        tel_per_slot = max(0.0, game_data.safe_finite_float(telemetry.get("attacks_per_slot_per_hour"), 0.0))
        tel_attacks_per_hour = max(
            0.0, game_data.safe_finite_float(telemetry.get("estimated_attacks_per_hour"), 0.0)
        )
    else:
        tel_per_slot = per_slot_hour
        tel_attacks_per_hour = start_slots * per_slot_hour
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
        "attacks_per_slot_per_hour": round(tel_per_slot, 4),
        "estimated_attacks_per_hour": round(tel_attacks_per_hour, 4),
        "pace_set_by": pace_set_by,
        "income_per_slot_per_hour": {r: round(slot_attack_income[r], 2) for r in RESOURCE_KEYS},
        "hourly_loot": {
            **{r: round(income_now[r], 2) for r in RESOURCE_KEYS},
            "spice_eq": round(float(game_data.spice_equivalent(income_now)), 2),
        },
    }
    if cfg["attacks_per_day"] is not None and "income_basis" in telemetry:
        # The realized figure was offered but the fixed attacks-per-day pace was used instead.
        enriched_telemetry["income_basis"] = "attacks_per_day"

    disc_reward = float(best_run["discounted_reward"])
    total_net_sp = float(best_run["total_spice_eq_produced"])

    in_prog_buildings_out = [dict(row) for row in (problem.get("in_progress_builds") or ())]
    in_prog_research_out = [dict(row) for row in (problem.get("in_progress_research") or ())]
    in_flight_arrivals_out = [
        {k: v for k, v in row.items() if k != "cargo_tuple"}
        for row in (problem.get("in_flight_arrivals") or ())
    ]

    travel_sec_mat = problem.get("travel_seconds") or ()
    dist_mat = problem.get("distance_units") or ()
    hull_list = problem.get("transport_hull_by_planet") or ()
    transport_pairs: list[dict[str, Any]] = []
    for i in range(simulator.planet_count):
        if not simulator.planets[i]["founded"]:
            continue
        for j in range(simulator.planet_count):
            if i == j or not simulator.planets[j]["founded"]:
                continue
            ow_s = float(travel_sec_mat[i][j]) if i < len(travel_sec_mat) and j < len(travel_sec_mat[i]) else 0.0
            du = float(dist_mat[i][j]) if i < len(dist_mat) and j < len(dist_mat[i]) else 0.0
            hull_i = str(hull_list[i]) if i < len(hull_list) else "s2"
            transport_pairs.append(
                {
                    "origin_coords": str(simulator.planets[i]["coords"]),
                    "target_coords": str(simulator.planets[j]["coords"]),
                    "distance_units": round(du, 2),
                    "one_way_seconds": round(ow_s, 2),
                    "lead_seconds": round(ow_s + dispatch_delay_sec, 2),
                    "hull": hull_i,
                }
            )
    avg_one_way_sec = round(max(0.0, game_data.safe_finite_float(problem.get("avg_transport_travel_seconds"), 0.0)), 2)
    transport_matrix_out = {
        "dispatch_delay_minutes": round(simulator.transport_dispatch_delay_minutes, 2),
        "dispatch_delay_seconds": dispatch_delay_sec,
        "avg_one_way_seconds": avg_one_way_sec,
        "max_one_way_seconds": round(float(problem.get("max_transport_travel_seconds", 0.0)), 2),
        "pairs": transport_pairs,
    }

    cargo_4h_f = 0.0
    cargo_24h_f = 0.0
    max_single_shipment_f = 0.0
    shipment_steps_4h = 0
    shipment_steps_24h = 0
    for tr_info in step_tr_map.values():
        if not isinstance(tr_info, Mapping):
            continue
        raw_shipped = tr_info.get("total_shipped") or ()
        step_cargo = sum(max(0.0, game_data.safe_finite_float(v, 0.0)) for v in raw_shipped)
        if step_cargo <= 0.0:
            continue
        req_hr = max(0.0, game_data.safe_finite_float(tr_info.get("earliest_request_hr"), 0.0))
        if req_hr <= 24.0 + 1e-9:
            cargo_24h_f += step_cargo
            shipment_steps_24h += 1
            if step_cargo > max_single_shipment_f:
                max_single_shipment_f = step_cargo
            if req_hr <= 4.0 + 1e-9:
                cargo_4h_f += step_cargo
                shipment_steps_4h += 1

    cargo_per_hour_4h = round(cargo_4h_f / 4.0, 2)
    cargo_per_hour_24h = round(cargo_24h_f / 24.0, 2)
    rt_per_hour = round(3600.0 / (2.0 * avg_one_way_sec), 4) if avg_one_way_sec > 0.0 else 2.5
    util_assumed = 0.5
    rec_cargo_cap = (
        int(math.ceil(cargo_per_hour_24h / max(0.25, rt_per_hour) / util_assumed))
        if cargo_per_hour_24h > 0.0
        else 0
    )
    transport_demand_out = {
        "cargo_4h": int(round(cargo_4h_f)),
        "cargo_per_hour_4h": cargo_per_hour_4h,
        "cargo_24h": int(round(cargo_24h_f)),
        "cargo_per_hour_24h": cargo_per_hour_24h,
        "max_single_shipment_cargo": int(round(max_single_shipment_f)),
        "shipment_steps_4h": int(shipment_steps_4h),
        "shipment_steps_24h": int(shipment_steps_24h),
        "avg_one_way_seconds": avg_one_way_sec,
        "round_trips_per_hour": rt_per_hour,
        "utilization_assumed": util_assumed,
        "recommended_cargo_capacity": rec_cargo_cap,
        "recommended_large_transports": int(math.ceil(rec_cargo_cap / 30_000.0)) if rec_cargo_cap > 0 else 0,
        "recommended_small_transports": int(math.ceil(rec_cargo_cap / 6_000.0)) if rec_cargo_cap > 0 else 0,
    }

    raw_smelting_spans = list(best_run.get("smelting_spans") or ())
    smelting_hours_total = sum(
        max(0.0, float(span[1]) - float(span[0]))
        for span in raw_smelting_spans
        if isinstance(span, (list, tuple)) and len(span) >= 2
    )
    smelting_spans_out = [
        {
            "start_hours": round(float(span[0]), 4),
            "end_hours": round(float(span[1]), 4),
            "start_sec": round(float(span[0]) * 3600.0, 2),
            "end_sec": round(float(span[1]) * 3600.0, 2),
            "duration_sec": round(max(0.0, float(span[1]) - float(span[0])) * 3600.0, 2),
            "start_text": "now" if float(span[0]) <= 1e-6 else fmt_path_time(float(span[0])),
            "end_text": fmt_path_time(float(span[1])),
            "duration_text": fmt_path_time(max(0.0, float(span[1]) - float(span[0]))),
        }
        for span in raw_smelting_spans
        if isinstance(span, (list, tuple)) and len(span) >= 2
    ]

    raw_smelting_events = list(best_run.get("smelting_events") or ())
    smelting_events_out: list[dict[str, Any]] = []
    for ev_seq, ev in enumerate(raw_smelting_events, start=1):
        ev_hr = max(0.0, float(ev.get("time_hours", 0.0)))
        ev_wk = min(simulator.reward_weeks, max(1, int(ev_hr // PATH_HOURS_PER_WEEK) + 1))
        step_id = ev.get("reserved_step_id", -1)
        step_label: Optional[str] = None
        step_planet_coords: Optional[str] = None
        if isinstance(step_id, int) and 0 <= step_id < len(simulator.steps):
            st_det = simulator.steps[step_id]
            step_label = f"{st_det['name']} L{int(st_det['level_to'])}"
            sp_idx = int(st_det["planet"])
            step_planet_coords = str(simulator.planets[sp_idx]["coords"]) if sp_idx >= 0 else "account"
        smelting_events_out.append(
            {
                "seq": ev_seq,
                "time_hours": round(ev_hr, 4),
                "time_sec": round(ev_hr * 3600.0, 2),
                "time_text": "now" if ev_hr <= 1e-6 else fmt_path_time(ev_hr),
                "week": ev_wk,
                "action": str(ev.get("action") or ("enable" if ev.get("enabled") else "disable")),
                "enabled": bool(ev.get("enabled", False)),
                "target_pct": 100 if ev.get("enabled") else 0,
                "from_mode": str(ev.get("from_mode") or "off"),
                "from_enabled": bool(ev.get("from_enabled", False)),
                "is_initial": bool(ev.get("is_initial", False)),
                "affected_planets": list(ev.get("affected_planets") or ()),
                "step_label": step_label,
                "step_planet_coords": step_planet_coords,
                "reason": str(ev.get("reason") or ""),
            }
        )

    init_bf_info = dict(best_run.get("initial_blast_furnace") or simulator.initial_blast_furnace)
    smelting_out = {
        "initial_live_mode": str(init_bf_info.get("mode") or "none"),
        "initial_live_any_enabled": bool(init_bf_info.get("any_enabled", False)),
        "initial_live_all_enabled": bool(init_bf_info.get("all_enabled", False)),
        "initial_live_enabled_count": int(init_bf_info.get("enabled_count", 0)),
        "initial_live_disabled_count": int(init_bf_info.get("disabled_count", 0)),
        "total_b2_planets": int(init_bf_info.get("total_b2_planets", 0)),
        "initial_target_enabled": bool(best_run.get("initial_smelting_target", False)),
        "initial_target_pct": 100 if best_run.get("initial_smelting_target", False) else 0,
        "initial_toggle_needed": bool(best_run.get("initial_toggle_needed", False)),
        "initial_planets_needing_toggle": list(best_run.get("initial_planets_needing_toggle") or ()),
        "metal_smelted": int(round(best_run["metal_smelted"])),
        "smelting_hours_total": round(smelting_hours_total, 4),
        "smelting_time_text": fmt_path_time(smelting_hours_total),
        "smelting_toggles_count": len(smelting_events_out),
        "smelting_spans_count": len(smelting_spans_out),
        "smelting_spans": smelting_spans_out,
        "smelting_events": smelting_events_out,
        "planets": list(init_bf_info.get("planets") or ()),
    }

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
        "attacks_per_slot_per_hour": round(tel_per_slot, 4),
        **({"attack_income_basis": REALIZED_INCOME_BASIS} if use_realized_income else {}),
        "spare_percent": dict(zip(RESOURCE_KEYS, simulator.spare_percent)),
        "spare_diverted": {r: int(round(spare_diverted[pos])) for pos, r in enumerate(RESOURCE_KEYS)},
        "max_building_levels_reached": buildings_reached,
        "research_levels_reached": research_reached,
        "busiest_queue": busiest_queue,
        "busy_hours_of_that_queue": round(max(0.0, busiest_hours), 2),
        "cost_spent": {r: int(round(cost_spent[pos])) for pos, r in enumerate(RESOURCE_KEYS)},
        "metal_smelted": int(round(best_run["metal_smelted"])),
        "initial_smelting_live_mode": smelting_out["initial_live_mode"],
        "initial_smelting_live_enabled": smelting_out["initial_live_any_enabled"],
        "initial_smelting_target_enabled": smelting_out["initial_target_enabled"],
        "initial_smelting_toggle_needed": smelting_out["initial_toggle_needed"],
        "smelting_hours_total": smelting_out["smelting_hours_total"],
        "smelting_toggles_count": smelting_out["smelting_toggles_count"],
        "loot_counted": {r: int(round(loot_collected[pos])) for pos, r in enumerate(RESOURCE_KEYS)},
        "loot_share_of_cost_spent": round(loot_val / cost_val, 4) if cost_val > 0.0 else 0.0,
        "transport_dispatch_delay_minutes": round(simulator.transport_dispatch_delay_minutes, 2),
        "transports_dispatched": int(best_run.get("transports_dispatched", 0)),
        "early_transports_dispatched": int(best_run.get("early_transports_dispatched", 0)),
        "avg_transport_travel_seconds": round(float(problem.get("avg_transport_travel_seconds", 0.0)), 2),
        "max_transport_travel_seconds": round(float(problem.get("max_transport_travel_seconds", 0.0)), 2),
        "planned_cargo_per_hour_24h": transport_demand_out["cargo_per_hour_24h"],
        "recommended_cargo_capacity": transport_demand_out["recommended_cargo_capacity"],
        "recommended_large_transports": transport_demand_out["recommended_large_transports"],
        "in_progress_buildings_count": int(len(in_prog_buildings_out)),
        "in_progress_research_count": int(len(in_prog_research_out)),
        "in_flight_arrivals_count": int(len(in_flight_arrivals_out)),
        "started_from_live_state": True,
        "live_snapshot_ts": float(problem.get("live_snapshot_ts", 0.0)),
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
            "in_progress": {
                "buildings": in_prog_buildings_out,
                "research": in_prog_research_out,
                "fleet_arrivals": in_flight_arrivals_out,
            },
            "smelting": smelting_out,
            "transport_matrix": transport_matrix_out,
            "transport_demand": transport_demand_out,
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
    state_ts = max(0.0, game_data.safe_finite_float(getattr(state, "timestamp", 0.0), 0.0))
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
        if _is_queue_job_expired(job, state_ts):
            continue
        bid, parsed_tgt = _extract_building_job_id_and_level(job)
        origin = str(getattr(job, "origin_coords", "") or "").strip()
        target_planet = by_coords.get(origin) or (by_coords.get(normalize_coords(origin)) if origin else None)
        if target_planet is None and not origin:
            target_planet = _resolve_untagged_building_job_planet(bid, parsed_tgt, planets, active_p)
        if target_planet is None:
            continue
        pid = int(target_planet.planet_id)
        counts[pid] = counts.get(pid, 0) + 1

        if bid is None:
            continue
        cur_lvl = target_planet.building_level(bid)
        tgt_lvl = parsed_tgt
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
            if _is_active_construction_status(getattr(off, "status_text", "")):
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
    allow_multi_planet = bool(getattr(config, "allow_multi_planet", True))

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
            allow_planet = (not is_remote) or allow_multi_planet
            offer = offers_by_id.get(bid)
            reason = f"AI-Planner #{seq}: build {b_name} L{target_level}"

            if offer is not None and game_data.safe_int(offer.target_level, target_level) == target_level:
                cost = offer.cost
                missing = planet.resources.missing(cost)
                stale_resource_ok = _is_stale_remote_resource_wait(offer) and not cost.is_zero()
                can_exec = (
                    allow_planet
                    and (not queue_full)
                    and (bool(offer.can_build) or stale_resource_ok)
                    and planet.resources.can_afford(cost)
                )
                href = (str(offer.build_href) or None) if (can_exec and offer.can_build) else None
                if not allow_planet:
                    out_reason = f"{reason} (multi-planet disabled)"
                elif queue_full:
                    out_reason = f"{reason} (build queue full)"
                else:
                    out_reason = reason
            else:
                cost = wiki_cost
                missing = planet.resources.missing(cost)
                can_live_verify = (
                    is_remote
                    or (not offers_by_id)
                    or (offer is not None and game_data.safe_int(offer.target_level, 0) < target_level)
                )
                can_exec = (
                    allow_planet
                    and (not queue_full)
                    and can_live_verify
                    and (not cost.is_zero())
                    and planet.resources.can_afford(cost)
                )
                href = None
                if not allow_planet:
                    out_reason = f"{reason} (multi-planet disabled)"
                elif queue_full:
                    out_reason = f"{reason} (build queue full)"
                elif not can_live_verify and not can_exec:
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
            adaptive_exec = bool(getattr(config, "ai_adaptive_execution", False))
            if not adaptive_exec:
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
            else:
                allow_research = bool(getattr(config, "allow_research", True))
                is_lab_active = (
                    state.active_planet_id is not None
                    and int(lab_planet.planet_id) == int(state.active_planet_id)
                )
                is_remote_lab = (
                    state.active_planet_id is not None
                    and int(lab_planet.planet_id) != int(state.active_planet_id)
                )
                if (
                    is_lab_active
                    and offer is not None
                    and game_data.safe_int(offer.target_level, target_level) == target_level
                ):
                    cost = offer.cost
                    missing = lab_planet.resources.missing(cost)
                    can_exec = bool(
                        allow_research
                        and (not research_queue_busy)
                        and (not unmet_prereqs)
                        and bool(offer.can_research)
                        and lab_planet.resources.can_afford(cost)
                    )
                    href = (str(offer.research_href) or None) if can_exec else None
                    tid = game_data.safe_optional_int(offer.research_id, rid_num)
                else:
                    raw_cost = ((r_info.get("levels") or {}).get(target_level) or {}).get("cost") or {}
                    cost = Resources(**full_cost(raw_cost))
                    missing = lab_planet.resources.missing(cost)
                    can_exec = bool(
                        allow_research
                        and (not is_remote_lab or allow_multi_planet)
                        and (not research_queue_busy)
                        and (not unmet_prereqs)
                        and (not cost.is_zero())
                        and lab_planet.resources.can_afford(cost)
                    )
                    href = None
                    tid = rid_num
                if not allow_research:
                    out_reason = f"{reason} (research disabled)"
                elif is_remote_lab and not allow_multi_planet:
                    out_reason = f"{reason} (multi-planet disabled)"
                elif research_queue_busy:
                    out_reason = f"{reason} (research slot busy)"
                else:
                    out_reason = reason

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


AI_COLONY_IDLE_STATES: tuple[str, ...] = (
    "building",
    "ready_not_started",
    "field_conflict",
    "storage_blocked",
    "transport_inbound",
    "waiting_metal",
    "waiting_pig_iron",
    "waiting_kryptonite",
    "waiting_spice",
    "idle_complete",
)

_RESOURCE_DISPLAY_LABELS: dict[str, str] = {
    "pig_iron": "Pig iron",
    "metal": "Metal",
    "kryptonite": "Kryptonite",
    "spice": "Spice",
}

_RESOURCE_TIE_PRIORITY: dict[str, int] = {
    "metal": 4,
    "kryptonite": 3,
    "spice": 2,
    "pig_iron": 1,
}


def _coerce_resources_obj(raw: Any) -> Resources:
    if isinstance(raw, Resources):
        return raw
    if isinstance(raw, Mapping):
        return Resources(
            pig_iron=max(0, game_data.safe_int(raw.get("pig_iron"), 0)),
            metal=max(0, game_data.safe_int(raw.get("metal"), 0)),
            kryptonite=max(0, game_data.safe_int(raw.get("kryptonite"), 0)),
            spice=max(0, game_data.safe_int(raw.get("spice"), 0)),
        )
    if raw is not None:
        return Resources(
            pig_iron=max(0, game_data.safe_int(getattr(raw, "pig_iron", 0), 0)),
            metal=max(0, game_data.safe_int(getattr(raw, "metal", 0), 0)),
            kryptonite=max(0, game_data.safe_int(getattr(raw, "kryptonite", 0), 0)),
            spice=max(0, game_data.safe_int(getattr(raw, "spice", 0), 0)),
        )
    return Resources()


def diagnose_ai_colony_idle_states(
    state: GameState,
    plan_result: Mapping[str, Any],
    ai_actions: Sequence[PlannedAction] = (),
    config: Optional[StrategyConfig] = None,
    *,
    field_conflicts: Sequence[Mapping[str, Any]] = (),
    inbound_cargo_by_planet: Optional[Mapping[int, Resources]] = None,
) -> dict[str, Any]:
    """Classify every normal colony's live AI-Planner builder/idle state and Step-5 gate metrics."""
    counts: dict[str, int] = {st: 0 for st in AI_COLONY_IDLE_STATES}
    if state is None:
        return {
            "active_builders": 0,
            "total_colonies": 0,
            "colonies": [],
            "counts": counts,
            "idle_with_affordable_count": 0,
            "ready_not_started_count": 0,
        }

    normal_planets = [p for p in (state.planets or ()) if normalize_planet_type(p) == "normal"]
    if not normal_planets:
        return {
            "active_builders": 0,
            "total_colonies": 0,
            "colonies": [],
            "counts": counts,
            "idle_with_affordable_count": 0,
            "ready_not_started_count": 0,
        }

    steps_seq = plan_result.get("steps") if isinstance(plan_result, Mapping) else None
    has_steps = isinstance(steps_seq, Sequence) and len(steps_seq) > 0
    eff_actions: Sequence[PlannedAction] = ai_actions
    eff_conflicts: Sequence[Mapping[str, Any]] = field_conflicts
    if not eff_actions and not eff_conflicts and has_steps:
        eff_actions, eff_conflicts = reconcile_live_ai_actions(
            state,
            plan_result,
            config or StrategyConfig(),
        )

    if inbound_cargo_by_planet is None:
        inbound_map: dict[int, Resources] = compute_inbound_friendly_cargo_by_planet(state)
    else:
        inbound_map = {
            game_data.safe_int(pid, 0): _coerce_resources_obj(val)
            for pid, val in inbound_cargo_by_planet.items()
        }

    in_flight_buildings, planet_queue_counts = _resolve_in_flight_buildings(state)
    in_prog_rem_by_pid = compute_in_progress_build_rem_by_planet(state)

    building_action_by_pid: dict[int, PlannedAction] = {}
    for act in eff_actions or ():
        if act.kind not in ("BUILD", "WAIT_RESOURCES"):
            continue
        pid = int(act.planet_id)
        if pid not in building_action_by_pid:
            building_action_by_pid[pid] = act

    conflicts_by_pid: dict[int, Mapping[str, Any]] = {}
    for c in eff_conflicts or ():
        if isinstance(c, Mapping):
            cpid = game_data.safe_int(c.get("planet_id"), 0)
            if cpid > 0 and cpid not in conflicts_by_pid:
                conflicts_by_pid[cpid] = c

    planets_by_id: dict[int, PlanetState] = {}
    planets_by_coords: dict[str, PlanetState] = {}
    for p in normal_planets:
        pid = int(p.planet_id)
        planets_by_id[pid] = p
        c_str = str(p.coords or "").strip()
        if c_str:
            planets_by_coords[c_str] = p
            planets_by_coords[normalize_coords(c_str)] = p

    steps_by_pid: dict[int, list[Mapping[str, Any]]] = {}
    if has_steps:
        for step in steps_seq:
            if not isinstance(step, Mapping):
                continue
            if str(step.get("kind") or "").strip().upper() != "BUILD":
                continue
            step_pid = game_data.safe_optional_int(step.get("planet_id"), None)
            step_coords = str(step.get("planet_coords") or "").strip()
            p_match = planets_by_id.get(step_pid) if step_pid is not None else None
            if p_match is None and step_coords:
                p_match = planets_by_coords.get(step_coords) or planets_by_coords.get(
                    normalize_coords(step_coords)
                )
            if p_match is not None:
                steps_by_pid.setdefault(int(p_match.planet_id), []).append(step)

    colonies_out: list[dict[str, Any]] = []
    for planet in normal_planets:
        pid = int(planet.planet_id)
        coords_s = str(planet.coords or "")
        name_s = str(planet.name or coords_s)

        b2_level = max(0, planet.building_level(2))
        eco_rates = getattr(planet, "economy_rates", None) or {}
        b2_pct = (
            max(0, min(100, game_data.safe_int(eco_rates.get("resource2", 100), 100)))
            if b2_level > 0
            else 0
        )
        energy_net = int(getattr(planet.energy, "net", 0))
        b2_full_energy = game_data.mine_energy_needed(2, b2_level) if b2_level > 0 else 0
        b2_extra_energy = (
            int(math.ceil(b2_full_energy * max(0, 100 - b2_pct) / 100.0)) if b2_level > 0 else 0
        )
        b2_energy_margin_at_100 = int(energy_net - b2_extra_energy)

        active_build_count = planet_queue_counts.get(pid, 0)
        in_flight_bmap = in_flight_buildings.get(pid, {})
        b_act = building_action_by_pid.get(pid)
        conflict = conflicts_by_pid.get(pid)
        inbound_res = inbound_map.get(pid, Resources())
        offers_by_id = {
            int(o.building_id): o
            for o in (planet.building_offers or ())
            if getattr(o, "building_id", None) is not None
        }
        fields_full = bool(planet.fields_max > 0 and planet.fields_used >= planet.fields_max)

        first_storage_blocked: Optional[tuple[str, str, int, int]] = None
        first_field_blocked: Optional[str] = None
        first_prereq_blocked: Optional[str] = None
        has_affordable_target_step = False

        for step in steps_by_pid.get(pid, ()):
            entity_key = str(step.get("entity_key") or "").strip()
            target_level = game_data.safe_int(step.get("target_level"), 0)
            if not (entity_key.startswith("b") and entity_key[1:].isdigit()) or target_level <= 0:
                continue
            bid = int(entity_key[1:])
            b_info = game_data.BUILDING_BY_ID.get(bid)
            if not b_info:
                continue
            live_level = planet.building_level(bid)
            in_flight_lvl = in_flight_bmap.get(entity_key, 0)
            if max(live_level, in_flight_lvl) >= target_level:
                continue
            b_name = str(step.get("entity_name") or b_info["name"])
            step_lbl = f"{b_name} L{target_level}"
            if target_level != live_level + 1:
                continue
            if not is_building_allowed_on_planet_type(planet, bid, has_b8_offer=(8 in offers_by_id)):
                continue
            if game_data.missing_prerequisites(
                "buildings", bid, buildings=planet.buildings, researches=state.researches
            ):
                if first_prereq_blocked is None:
                    first_prereq_blocked = step_lbl
                continue
            if fields_full:
                if first_field_blocked is None:
                    first_field_blocked = step_lbl
                continue

            wiki_cost = Resources(**full_cost(game_data.building_cost(bid, target_level)))
            storage_blocker: Optional[tuple[str, int, int]] = None
            if bid not in (11, 12, 13, 14):
                for r in RESOURCE_KEYS:
                    cap = game_data.safe_int(getattr(planet.storage, r, 0), 0)
                    need_r = game_data.safe_int(getattr(wiki_cost, r, 0), 0)
                    if cap > 0 and need_r > cap:
                        storage_blocker = (_RESOURCE_DISPLAY_LABELS[r], need_r, cap)
                        break
            if storage_blocker is not None:
                if first_storage_blocked is None:
                    first_storage_blocked = (
                        step_lbl,
                        storage_blocker[0],
                        storage_blocker[1],
                        storage_blocker[2],
                    )
                continue

            if (not wiki_cost.is_zero()) and planet.resources.can_afford(wiki_cost):
                has_affordable_target_step = True

        next_label: Optional[str] = None
        if active_build_count > 0:
            col_state = "building"
            if in_flight_bmap:
                first_bkey, first_lvl = next(iter(in_flight_bmap.items()))
                if first_bkey.startswith("b") and first_bkey[1:].isdigit():
                    binfo = game_data.BUILDING_BY_ID.get(int(first_bkey[1:]))
                    bname = str(binfo["name"]) if binfo else first_bkey
                else:
                    bname = first_bkey
                next_label = f"{bname} L{int(first_lvl)}"
            elif b_act is not None:
                next_label = (
                    f"{b_act.target_name} L{b_act.target_level}"
                    if b_act.target_level
                    else str(b_act.target_name)
                )
            rem_s = in_prog_rem_by_pid.get(pid, 0.0)
            if next_label and rem_s > 0.0:
                reason = f"Building {next_label} ({fmt_path_time(rem_s / 3600.0)} left)"
            elif next_label:
                reason = f"Building {next_label}"
            else:
                reason = "Build queue active"
        elif b_act is not None and b_act.executable_now:
            col_state = "ready_not_started"
            next_label = (
                f"{b_act.target_name} L{b_act.target_level}"
                if b_act.target_level
                else str(b_act.target_name)
            )
            reason = str(b_act.reason or f"Ready to start {next_label}")
        elif conflict is not None or first_field_blocked is not None:
            col_state = "field_conflict"
            if conflict is not None:
                blk_name = str(
                    conflict.get("blocked_building_name")
                    or conflict.get("blocked_building_key")
                    or "building"
                )
                blk_lvl = game_data.safe_int(conflict.get("blocked_target_level"), 0)
                next_label = f"{blk_name} L{blk_lvl}" if blk_lvl > 0 else blk_name
            else:
                next_label = first_field_blocked
            reason = (
                f"Fields full ({planet.fields_used}/{planet.fields_max}) blocking {next_label}"
                if next_label
                else f"Fields full ({planet.fields_used}/{planet.fields_max})"
            )
        elif b_act is not None and not b_act.missing.is_zero():
            next_label = (
                f"{b_act.target_name} L{b_act.target_level}"
                if b_act.target_level
                else str(b_act.target_name)
            )
            rem_missing = {
                r: max(
                    0,
                    game_data.safe_int(getattr(b_act.missing, r, 0), 0)
                    - game_data.safe_int(getattr(inbound_res, r, 0), 0),
                )
                for r in RESOURCE_KEYS
            }
            if sum(rem_missing.values()) == 0:
                col_state = "transport_inbound"
                reason = f"Inbound friendly cargo covers {next_label}"
            else:
                top_res = max(
                    (r for r, amt in rem_missing.items() if amt > 0),
                    key=lambda r: (
                        rem_missing[r] / max(1e-9, float(game_data.MERCHANT_RATES_IN_SPICE[r])),
                        _RESOURCE_TIE_PRIORITY[r],
                    ),
                )
                col_state = f"waiting_{top_res}"
                parts = []
                for r in ("metal", "pig_iron", "kryptonite", "spice"):
                    rem_amt = rem_missing[r]
                    if rem_amt > 0:
                        inb_amt = game_data.safe_int(getattr(inbound_res, r, 0), 0)
                        if inb_amt > 0:
                            parts.append(
                                f"{rem_amt:,} {_RESOURCE_DISPLAY_LABELS[r]} ({inb_amt:,} inbound)"
                            )
                        else:
                            parts.append(f"{rem_amt:,} {_RESOURCE_DISPLAY_LABELS[r]}")
                reason = f"Waiting for {', '.join(parts)} for {next_label}"
        elif first_storage_blocked is not None:
            col_state = "storage_blocked"
            next_label = first_storage_blocked[0]
            reason = (
                f"{next_label} exceeds {first_storage_blocked[1]} storage "
                f"({first_storage_blocked[2]:,} > {first_storage_blocked[3]:,})"
            )
        else:
            col_state = "idle_complete"
            if b_act is not None:
                next_label = (
                    f"{b_act.target_name} L{b_act.target_level}"
                    if b_act.target_level
                    else str(b_act.target_name)
                )
                reason = str(b_act.reason or f"Waiting on {next_label}")
            elif first_prereq_blocked is not None:
                next_label = first_prereq_blocked
                reason = f"Waiting on prerequisites for {first_prereq_blocked}"
            else:
                next_label = None
                reason = "All planned steps complete"

        idle_with_affordable = bool(
            has_affordable_target_step and col_state not in ("building", "ready_not_started")
        )
        counts[col_state] += 1
        colonies_out.append(
            {
                "planet_id": pid,
                "coords": coords_s,
                "name": name_s,
                "state": col_state,
                "reason": reason,
                "next_label": next_label,
                "idle_with_affordable_target_step": idle_with_affordable,
                "b2_level": b2_level,
                "b2_pct": b2_pct,
                "energy_net": energy_net,
                "b2_energy_margin_at_100": b2_energy_margin_at_100,
            }
        )

    return _json_ready(
        {
            "active_builders": int(counts["building"]),
            "total_colonies": int(len(colonies_out)),
            "colonies": colonies_out,
            "counts": counts,
            "idle_with_affordable_count": int(
                sum(1 for row in colonies_out if row["idle_with_affordable_target_step"])
            ),
            "ready_not_started_count": int(counts["ready_not_started"]),
        }
    )


def compute_early_transport_needs(
    state: GameState,
    plan_result: Mapping[str, Any],
    ai_actions: Sequence[PlannedAction] = (),
    *,
    config: Optional[StrategyConfig] = None,
) -> dict[int, dict[str, Any]]:
    """Compute distance- & dispatch-delay-aware live transport needs for AI-Planner steps.

    Combines:
    - **Rule A (Immediate stalled steps)**: Waiting `ai_actions` (`not executable_now`)
      with missing resources, minus live incoming friendly cargo.
    - **Rule B (Early pre-ship behind active in-progress builds/research)**: Upcoming
      planned steps (including same-chain next levels like `b1 L16` behind active
      `b1 L15`, or prereq-unlocked steps like `b17 L1` behind active `b7 L10`) whose
      active queue finishes within `transport_dispatch_delay + one_way_travel_time`,
      subtracting local stock, live incoming cargo, and projected local production
      over the remaining queue time.
    - **Rule C (Early pre-ship behind an immediately-launching step)**: If a colony
      has an `executable_now=True` step in `ai_actions` whose build duration is within
      the lead window, pre-ships resources for the subsequent planned step minus the
      immediate step's cost.
    """
    del config
    if not isinstance(plan_result, Mapping):
        return {}
    steps = plan_result.get("steps")
    if not isinstance(steps, Sequence) or not steps:
        return {}

    normal_planets = [p for p in (state.planets or ()) if normalize_planet_type(p) == "normal"]
    if len(normal_planets) < 2:
        return {}

    meta = plan_result.get("meta_parameters") if isinstance(plan_result.get("meta_parameters"), Mapping) else {}
    dispatch_delay_min = min(
        1440.0,
        max(0.0, game_data.safe_finite_float(meta.get("transport_dispatch_delay_minutes"), 120.0)),
    )
    dispatch_delay_sec = dispatch_delay_min * 60.0

    planets_by_id: dict[int, PlanetState] = {}
    planets_by_coords: dict[str, PlanetState] = {}
    empire_ships: dict[str, int] = {"s1": 0, "s2": 0, "s3": 0}
    for p in normal_planets:
        pid = int(p.planet_id)
        planets_by_id[pid] = p
        c = str(p.coords or "").strip()
        if c:
            planets_by_coords[c] = p
            planets_by_coords[normalize_coords(c)] = p
        for sk in ("s1", "s2", "s3"):
            empire_ships[sk] += max(0, int((p.ships or {}).get(sk, 0) or 0))

    # Build lightweight planet entries for unified in-progress / arrival extraction
    speedup = _safe_speedup(state, {})
    research_now = {
        key: max(0, int((state.researches or {}).get(key, 0) or 0)) for key, _ in research_catalog()
    }
    mini_planets: list[dict[str, Any]] = []
    for idx, p in enumerate(normal_planets):
        mini_planets.append(
            {
                "index": idx,
                "planet_id": int(p.planet_id),
                "coords": str(p.coords),
                "name": str(p.name or p.coords),
                "levels": {str(k): max(0, int(v or 0)) for k, v in (p.buildings or {}).items() if max(0, int(v or 0)) > 0},
            }
        )
    lab_idx = -1
    if normal_planets:
        best_lab_idx = max(
            range(len(normal_planets)),
            key=lambda i: (
                normal_planets[i].building_level(8),
                1 if (state.active_planet_id is not None and normal_planets[i].planet_id == state.active_planet_id) else 0,
            ),
        )
        if normal_planets[best_lab_idx].building_level(8) >= 1:
            lab_idx = best_lab_idx

    base_times_cache: dict[str, dict[int, float]] = {}

    def _base_hours(key: str, level: int) -> float:
        if key not in base_times_cache:
            info = game_data.BUILDING_BY_ID[int(key[1:])] if key.startswith("b") else game_data.RESEARCH_BY_ID[key]
            levels_map = info.get("levels") or {}
            top_level = max(int(info.get("max_table_level") or len(levels_map)), level)
            base_times_cache[key] = build_monotonic_base_times(levels_map, top_level)
        return float(base_times_cache[key].get(level, 0.0)) / 3600.0

    in_prog_builds, in_prog_research, in_flight_arrivals = _extract_live_in_progress_state(
        state,
        mini_planets,
        speedup=speedup,
        research_now=research_now,
        base_hours_fn=_base_hours,
        lab_planet_index=lab_idx,
    )

    inbound_cargo_by_pid: dict[int, list[float]] = {}
    for arr in in_flight_arrivals:
        pid = int(arr["planet_id"])
        buf = inbound_cargo_by_pid.setdefault(pid, [0.0, 0.0, 0.0, 0.0])
        ct = arr.get("cargo_tuple") or (0.0, 0.0, 0.0, 0.0)
        for pos in range(4):
            buf[pos] += float(ct[pos])

    queue_rem_by_pid: dict[int, float] = {}
    eff_buildings_by_pid: dict[int, dict[str, int]] = {
        int(p.planet_id): {
            str(k): max(0, int(v or 0))
            for k, v in (p.buildings or {}).items()
            if max(0, int(v or 0)) > 0
        }
        for p in normal_planets
    }
    for job in in_prog_builds:
        pid = int(job["planet_id"])
        bkey = str(job["key"])
        lvl = int(job["level_to"])
        rem_s = max(0.0, float(job["remaining_seconds"]))
        queue_rem_by_pid[pid] = max(queue_rem_by_pid.get(pid, 0.0), rem_s)
        pmap = eff_buildings_by_pid.setdefault(pid, {})
        pmap[bkey] = max(pmap.get(bkey, 0), lvl)

    research_rem_sec = 0.0
    eff_researches = dict(research_now)
    for job in in_prog_research:
        rkey = str(job["key"])
        lvl = int(job["level_to"])
        rem_s = max(0.0, float(job["remaining_seconds"]))
        research_rem_sec = max(research_rem_sec, rem_s)
        eff_researches[rkey] = max(eff_researches.get(rkey, 0), lvl)

    def _best_donor_travel_sec(dest_planet: PlanetState) -> float:
        ship_donors = [
            p
            for p in normal_planets
            if p.planet_id != dest_planet.planet_id
            and sum(max(0, int((p.ships or {}).get(sk, 0) or 0)) for sk in ("s1", "s2", "s3")) > 0
        ]
        pool = ship_donors if ship_donors else [p for p in normal_planets if p.planet_id != dest_planet.planet_id]
        best_s = PATH_NEVER
        for src in pool:
            ow_s, _, _ = estimate_interplanet_transport_seconds(
                str(src.coords),
                str(dest_planet.coords),
                origin_ships=src.ships,
                empire_ships=empire_ships,
                researches=state.researches,
            )
            if ow_s < best_s:
                best_s = ow_s
        return 0.0 if not math.isfinite(best_s) else float(best_s)

    def _planet_hourly_rates(p: PlanetState) -> tuple[float, float, float, float]:
        rates_obj = getattr(p, "rates", None)
        if rates_obj is not None:
            net = getattr(rates_obj, "net_per_hour", None)
            if net is not None:
                vals = (
                    max(0.0, float(getattr(net, "pig_iron", 0) or 0)),
                    max(0.0, float(getattr(net, "metal", 0) or 0)),
                    max(0.0, float(getattr(net, "kryptonite", 0) or 0)),
                    max(0.0, float(getattr(net, "spice", 0) or 0)),
                )
                if sum(vals) > 0.0:
                    return vals
        ctx = {
            "speedup_factor": speedup,
            "planet_temperature_c": int(p.temperature),
            "r1_mining_tech": research_now.get("r1", 0),
            "r2_energy_tech": research_now.get("r2", 0),
        }
        totals = [0.0] * len(PRODUCTION_FLOW_KEYS)
        for bk in PATH_FLOW_BUILDING_KEYS:
            lvl = max(0, int((p.buildings or {}).get(bk, 0) or 0))
            if lvl > 0:
                for pos, fv in enumerate(building_level_flows(bk, lvl, ctx)):
                    totals[pos] += fv
        s9 = max(0, int((p.ships or {}).get("s9", 0) or 0))
        if s9 > 0:
            totals[6] += s9 * solar_cell_energy_per_unit(int(p.temperature)) * technology_multiplier("r2", research_now.get("r2", 0))
        out = planet_hourly_output(totals)
        return (max(0.0, out[1]), max(0.0, out[2]), max(0.0, out[3]), max(0.0, out[4]))

    def _storage_cap_ratio_for(p: PlanetState, cost_vals: Sequence[float]) -> float:
        ratio = 0.85
        for pos, r in enumerate(RESOURCE_KEYS):
            cap = max(0, int(getattr(p.storage, r, 0) or 0))
            if cap > 0 and cost_vals[pos] > cap * 0.85 and cost_vals[pos] <= cap:
                ratio = max(ratio, min(0.98, (cost_vals[pos] / float(cap)) + 0.02))
        return round(ratio, 4)

    needs: dict[int, dict[str, Any]] = {}
    exec_cost_by_pid: dict[int, list[float]] = {}
    exec_dur_by_pid: dict[int, float] = {}
    exec_res_dur = 0.0

    # Rule A: Immediate stalled/waiting AI-Planner actions + track immediately-launching step costs/durations
    for act in ai_actions:
        if act.kind not in ("BUILD", "WAIT_RESOURCES", "RESEARCH"):
            continue
        pid = int(act.planet_id)
        p = planets_by_id.get(pid)
        if p is None:
            continue
        if act.executable_now:
            buf = exec_cost_by_pid.setdefault(pid, [0.0, 0.0, 0.0, 0.0])
            for pos, r in enumerate(RESOURCE_KEYS):
                buf[pos] += max(0.0, float(getattr(act.cost, r, 0) or 0))
            if act.kind == "BUILD" and act.target_id is not None and act.target_level:
                bkey = f"b{int(act.target_id)}"
                eff_buildings_by_pid.setdefault(pid, {})[bkey] = max(
                    eff_buildings_by_pid.get(pid, {}).get(bkey, 0), int(act.target_level)
                )
                div = building_time_divisor(p.building_level(7), p.building_level(17), speedup)
                dur_s = (_base_hours(bkey, int(act.target_level)) / max(1e-9, div)) * 3600.0
                exec_dur_by_pid[pid] = max(exec_dur_by_pid.get(pid, 0.0), dur_s)
            elif act.kind == "RESEARCH" and act.target_id is not None and act.target_level:
                rkey = f"r{int(act.target_id)}"
                eff_researches[rkey] = max(eff_researches.get(rkey, 0), int(act.target_level))
                div = research_time_divisor(p.building_level(8), speedup)
                dur_s = (_base_hours(rkey, int(act.target_level)) / max(1e-9, div)) * 3600.0
                exec_res_dur = max(exec_res_dur, dur_s)
            continue

        if act.missing.is_zero():
            continue
        inb = inbound_cargo_by_pid.get(pid, [0.0, 0.0, 0.0, 0.0])
        gap = [
            max(0, int(math.ceil(max(0.0, float(getattr(act.missing, r, 0) or 0) - inb[pos]))))
            for pos, r in enumerate(RESOURCE_KEYS)
        ]
        if sum(gap) <= 0:
            continue
        lead_s = dispatch_delay_sec + _best_donor_travel_sec(p)
        cost_vals = [float(getattr(act.cost, r, 0) or 0) for r in RESOURCE_KEYS]
        cap_ratio = _storage_cap_ratio_for(p, cost_vals)
        prev = needs.get(pid)
        if prev is None:
            needs[pid] = {
                "pig_iron": gap[0],
                "metal": gap[1],
                "kryptonite": gap[2],
                "spice": gap[3],
                "label": f"{act.target_name} L{act.target_level}" if act.target_level else str(act.target_name),
                "need_by_sec": 0.0,
                "lead_sec": round(lead_s, 1),
                "storage_cap_ratio": cap_ratio,
            }
        else:
            for pos, r in enumerate(RESOURCE_KEYS):
                prev[r] = max(int(prev[r]), gap[pos])
            prev["storage_cap_ratio"] = max(float(prev["storage_cap_ratio"]), cap_ratio)

    # Rule B & C: Early pre-ship for the next planned BUILD / RESEARCH behind active or launching queues
    early_checked_planets: set[int] = set(needs.keys())
    early_checked_research = False
    lab_planet = normal_planets[lab_idx] if 0 <= lab_idx < len(normal_planets) else None

    for step in steps:
        if not isinstance(step, Mapping):
            continue
        kind = str(step.get("kind") or "").strip().upper()
        entity_key = str(step.get("entity_key") or "").strip()
        target_level = game_data.safe_int(step.get("target_level"), 0)
        seq = game_data.safe_int(step.get("seq"), 0)
        if not entity_key or target_level <= 0:
            continue

        if kind == "BUILD":
            step_pid = game_data.safe_optional_int(step.get("planet_id"), None)
            step_coords = str(step.get("planet_coords") or "").strip()
            p = planets_by_id.get(step_pid) if step_pid is not None else None
            if p is None and step_coords:
                p = planets_by_coords.get(step_coords) or planets_by_coords.get(normalize_coords(step_coords))
            if p is None:
                continue
            pid = int(p.planet_id)
            if pid in early_checked_planets:
                continue
            if not (entity_key.startswith("b") and entity_key[1:].isdigit()):
                continue
            bid = int(entity_key[1:])
            b_info = game_data.BUILDING_BY_ID.get(bid)
            if not b_info:
                continue
            eff_b = eff_buildings_by_pid.get(pid, {})
            cur_eff = eff_b.get(entity_key, 0)
            if cur_eff >= target_level:
                continue
            if target_level != cur_eff + 1:
                continue
            if game_data.missing_prerequisites("buildings", bid, buildings=eff_b, researches=eff_researches):
                continue
            if p.fields_max > 0 and p.fields_used >= p.fields_max:
                early_checked_planets.add(pid)
                continue

            rem_s = max(queue_rem_by_pid.get(pid, 0.0), exec_dur_by_pid.get(pid, 0.0))
            if rem_s <= 0.0:
                continue
            lead_s = dispatch_delay_sec + _best_donor_travel_sec(p)
            early_checked_planets.add(pid)
            if rem_s > lead_s + 1e-6:
                continue

            cost_dict = full_cost(game_data.building_cost(bid, target_level))
            cost_vals = [float(cost_dict[r]) for r in RESOURCE_KEYS]
            if bid not in (11, 12, 13, 14) and any(
                int(getattr(p.storage, r, 0) or 0) > 0 and cost_vals[pos] > int(getattr(p.storage, r, 0) or 0)
                for pos, r in enumerate(RESOURCE_KEYS)
            ):
                continue

            spent = exec_cost_by_pid.get(pid, [0.0, 0.0, 0.0, 0.0])
            inb = inbound_cargo_by_pid.get(pid, [0.0, 0.0, 0.0, 0.0])
            rates_p = _planet_hourly_rates(p)
            rem_hr = rem_s / 3600.0
            gap = []
            for pos, r in enumerate(RESOURCE_KEYS):
                local_after = max(0.0, float(getattr(p.resources, r, 0) or 0) - spent[pos])
                proj = local_after + inb[pos] + rates_p[pos] * rem_hr
                gap.append(max(0, int(math.ceil(cost_vals[pos] - proj))))
            if sum(gap) <= 0:
                continue
            b_name = str(step.get("entity_name") or b_info["name"])
            needs[pid] = {
                "pig_iron": gap[0],
                "metal": gap[1],
                "kryptonite": gap[2],
                "spice": gap[3],
                "label": (
                    f"early pre-ship #{seq}: {b_name} L{target_level} "
                    f"(queue free in {fmt_path_time(rem_hr)}, lead {fmt_path_time(lead_s / 3600.0)})"
                ),
                "need_by_sec": round(rem_s, 1),
                "lead_sec": round(lead_s, 1),
                "storage_cap_ratio": _storage_cap_ratio_for(p, cost_vals),
            }

        elif kind == "RESEARCH":
            if early_checked_research or lab_planet is None:
                continue
            r_info = game_data.RESEARCH_BY_ID.get(entity_key)
            if not r_info:
                continue
            cur_eff = eff_researches.get(entity_key, 0)
            if cur_eff >= target_level:
                continue
            if target_level != cur_eff + 1:
                continue
            lab_pid = int(lab_planet.planet_id)
            if game_data.missing_prerequisites(
                "research", entity_key, buildings=eff_buildings_by_pid.get(lab_pid, {}), researches=eff_researches
            ):
                continue

            rem_s = max(research_rem_sec, exec_res_dur)
            if rem_s <= 0.0:
                continue
            lead_s = dispatch_delay_sec + _best_donor_travel_sec(lab_planet)
            early_checked_research = True
            if rem_s > lead_s + 1e-6:
                continue

            raw_c = ((r_info.get("levels") or {}).get(target_level) or {}).get("cost") or {}
            cost_dict = full_cost(raw_c)
            cost_vals = [float(cost_dict[r]) for r in RESOURCE_KEYS]
            spent = exec_cost_by_pid.get(lab_pid, [0.0, 0.0, 0.0, 0.0])
            inb = inbound_cargo_by_pid.get(lab_pid, [0.0, 0.0, 0.0, 0.0])
            rates_lab = _planet_hourly_rates(lab_planet)
            rem_hr = rem_s / 3600.0
            gap = []
            for pos, r in enumerate(RESOURCE_KEYS):
                local_after = max(0.0, float(getattr(lab_planet.resources, r, 0) or 0) - spent[pos])
                proj = local_after + inb[pos] + rates_lab[pos] * rem_hr
                gap.append(max(0, int(math.ceil(cost_vals[pos] - proj))))
            if sum(gap) <= 0:
                continue
            r_name = str(step.get("entity_name") or r_info["name"])
            cap_ratio = _storage_cap_ratio_for(lab_planet, cost_vals)
            prev = needs.get(lab_pid)
            if prev is None:
                needs[lab_pid] = {
                    "pig_iron": gap[0],
                    "metal": gap[1],
                    "kryptonite": gap[2],
                    "spice": gap[3],
                    "label": (
                        f"early pre-ship #{seq}: {r_name} L{target_level} "
                        f"(lab free in {fmt_path_time(rem_hr)}, lead {fmt_path_time(lead_s / 3600.0)})"
                    ),
                    "need_by_sec": round(rem_s, 1),
                    "lead_sec": round(lead_s, 1),
                    "storage_cap_ratio": cap_ratio,
                }
            else:
                for pos, r in enumerate(RESOURCE_KEYS):
                    prev[r] = max(int(prev[r]), gap[pos])
                prev["storage_cap_ratio"] = max(float(prev["storage_cap_ratio"]), cap_ratio)

    return needs


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
