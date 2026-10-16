"""Live attack telemetry and path time formatting for the AI-Planner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import datetime
import math

import numpy as np

from spaceinvasion.core import catalog as game_data
from spaceinvasion.core.coercion import coerce_finite_float
from spaceinvasion.core.coords import coords_distance as _parse_coords_distance
from spaceinvasion.core.model import normalize_planet_type
from spaceinvasion.planner.ai._types import (
    DEFAULT_MEDIAN_ROUND_TRIP_SECONDS,
    REALIZED_INCOME_BASIS,
    GameStateLike,
    is_valid_coords,
    parse_json_dict,
    safe_speedup,
)
from spaceinvasion.planner.config_optimizer import RESOURCE_KEYS, fmt_duration


def fmt_path_time(hours: float) -> str:
    """Format hours as text: ``fmt_duration`` below 2 years, then years of 365 days."""
    if not math.isfinite(hours):
        return "never"
    if hours >= 2.0 * 8760.0:
        return f"{hours / 8760.0:,.1f} years"
    return str(fmt_duration(hours * 3600.0))


def is_realized_income_basis(telemetry: object) -> bool:
    """Return True when ``telemetry`` carries raid income measured from received loot and recycling."""
    if not isinstance(telemetry, Mapping):
        return False
    return str(telemetry.get("income_basis") or "").strip().lower().startswith("realized")


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


def _parse_server_clock(text: object) -> float | None:
    try:
        parsed = datetime.datetime.strptime(str(text or "").strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.timezone.utc).timestamp()


def _infer_server_tz_offset_sec(
    battle_rows: Sequence[Mapping[str, object]],
    server_clock_ts: Sequence[float | None],
    raid_rows: Sequence[Mapping[str, object]],
) -> float:
    votes: list[float] = []
    for battle, clock_ts in zip(battle_rows, server_clock_ts, strict=False):
        if clock_ts is None:
            continue
        b_target = str(battle.get("target_coords") or "")
        for raid in raid_rows:
            if str(raid.get("target_coords") or "") != b_target:
                continue
            dispatch_ts = coerce_finite_float(raid.get("attacked_ts"), 0.0)
            offset_hours = round((dispatch_ts - clock_ts) / 3600.0)
            if abs(offset_hours) <= 14 and 30.0 <= (clock_ts + offset_hours * 3600.0 - dispatch_ts) <= 3600.0:
                votes.append(offset_hours * 3600.0)
    return float(np.median(np.asarray(votes, dtype=np.float64))) if votes else 0.0


def _extract_battle_rows_from_messages(message_rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    battles: list[dict[str, object]] = []
    for row in message_rows:
        if not isinstance(row, Mapping):
            continue
        if any(k in row for k in ("loot_spice_eq", "battle_id", "attacker_lost_spice")) or (
            all(k in row for k in RESOURCE_KEYS) and "outcome" in row
        ):
            battles.append({str(k): v for k, v in row.items()})
            continue
        cat = str(row.get("category") or "").strip().lower()
        parsed = parse_json_dict(row.get("parsed_json"))
        if cat == "battle" or (parsed and any(k in parsed for k in ("loot", "pig_iron", "loot_spice_eq", "outcome"))):
            raw_loot = parsed.get("loot")
            loot_map: Mapping[str, object] = raw_loot if isinstance(raw_loot, Mapping) else parsed
            loot_vals = {r: max(0.0, coerce_finite_float(loot_map.get(r), 0.0)) for r in RESOURCE_KEYS}
            sp_eq = coerce_finite_float(parsed.get("loot_spice_eq"), float(game_data.spice_equivalent(loot_vals)))
            battles.append(
                {
                    "ts": coerce_finite_float(row.get("ingested_ts", row.get("ts")), 0.0),
                    "msg_date_text": str(row.get("msg_date_text") or parsed.get("msg_date_text") or ""),
                    "target_coords": str(
                        row.get("coords") or parsed.get("target_coords") or parsed.get("coords") or ""
                    ),
                    "outcome": str(parsed.get("outcome") or row.get("outcome") or "win"),
                    **loot_vals,
                    "loot_spice_eq": sp_eq,
                    "attacker_lost_spice": coerce_finite_float(parsed.get("attacker_lost_spice"), 0.0),
                    "one_way_sec": parsed.get("one_way_sec", row.get("one_way_sec")),
                    "round_trip_sec": parsed.get("round_trip_sec", row.get("round_trip_sec")),
                }
            )
    return battles


def _collect_battle_samples(
    battle_rows: Sequence[Mapping[str, object]],
    norm_raids: Sequence[Mapping[str, object]],
    default_origin: str,
    fleet_speed: float,
) -> tuple[dict[str, list[float]], list[float], list[float], int, int]:
    server_clock_ts = [_parse_server_clock(b.get("msg_date_text")) for b in battle_rows]
    tz_offset = _infer_server_tz_offset_sec(battle_rows, server_clock_ts, norm_raids)
    used_raids: set[int] = set()
    sec_per_sqrt: list[float] = []
    paired: list[tuple[Mapping[str, object], float | None, str]] = []
    for battle, clock_ts in zip(battle_rows, server_clock_ts, strict=False):
        target = str(battle.get("target_coords") or "").strip()
        impact_ts = (clock_ts + tz_offset) if clock_ts is not None else coerce_finite_float(battle.get("ts"), 0.0)
        explicit_rt = coerce_finite_float(battle.get("round_trip_sec"), 0.0)
        explicit_ow = coerce_finite_float(battle.get("one_way_sec"), 0.0)
        flight_ow: float | None = None
        origin = str(battle.get("origin_coords") or default_origin).strip()
        if explicit_rt > 0.0:
            flight_ow = explicit_rt / 2.0
        elif explicit_ow > 0.0:
            flight_ow = explicit_ow
        else:
            paired_idx: int | None = None
            for idx, raid in enumerate(norm_raids):
                if idx in used_raids or str(raid.get("target_coords") or "").strip() != target:
                    continue
                gap = impact_ts - coerce_finite_float(raid.get("attacked_ts"), 0.0)
                if 30.0 <= gap <= 7200.0 and (flight_ow is None or gap < flight_ow):
                    flight_ow = gap
                    origin = str(raid.get("origin_coords") or default_origin).strip()
                    paired_idx = idx
            if paired_idx is not None and flight_ow is not None:
                used_raids.add(paired_idx)
                if is_valid_coords(origin) and is_valid_coords(target):
                    dist = max(1.0, float(_parse_coords_distance(origin, target)))
                    sec_per_sqrt.append(max(1.0, flight_ow - 10.0) / math.sqrt(dist))
        paired.append((battle, flight_ow, origin))

    calibration = float(np.median(np.asarray(sec_per_sqrt, dtype=np.float64))) if sec_per_sqrt else None
    loot_samples: dict[str, list[float]] = {r: [] for r in RESOURCE_KEYS}
    spice_eq_samples: list[float] = []
    rt_samples: list[float] = []
    wins, losses = 0, 0
    for battle, flight_ow, origin in paired:
        target = str(battle.get("target_coords") or "").strip()
        if flight_ow is not None and flight_ow > 0.0:
            rt_samples.append(2.0 * flight_ow)
        elif calibration is not None and is_valid_coords(origin) and is_valid_coords(target):
            dist = max(1.0, float(_parse_coords_distance(origin, target)))
            rt_samples.append(2.0 * (10.0 + calibration * math.sqrt(dist)))
        elif is_valid_coords(origin) and is_valid_coords(target):
            rt_samples.append(2.0 * _estimate_physics_one_way_seconds(origin, target, fleet_speed_factor=fleet_speed))
        outcome = str(battle.get("outcome") or "").strip().lower()
        if outcome in ("win", "victory") or ("loss" not in outcome and "defeat" not in outcome and outcome != ""):
            wins += 1
        else:
            losses += 1
        loot = {r: max(0.0, coerce_finite_float(battle.get(r), 0.0)) for r in RESOURCE_KEYS}
        for r in RESOURCE_KEYS:
            loot_samples[r].append(loot[r])
        sp_eq = coerce_finite_float(battle.get("loot_spice_eq"), float(game_data.spice_equivalent(loot)))
        spice_eq_samples.append(max(0.0, sp_eq))
    return loot_samples, spice_eq_samples, rt_samples, wins, losses


def _collect_raid_samples(
    norm_raids: Sequence[Mapping[str, object]],
    default_origin: str,
    fleet_speed: float,
) -> tuple[dict[str, list[float]], list[float], list[float], int, int]:
    loot_samples: dict[str, list[float]] = {r: [] for r in RESOURCE_KEYS}
    spice_eq_samples: list[float] = []
    rt_samples: list[float] = []
    wins, losses = 0, 0
    for raid in norm_raids:
        raw_loot = raid.get("loot")
        loot_obj: Mapping[str, object] = (
            raw_loot if isinstance(raw_loot, Mapping) else parse_json_dict(raid.get("loot_json"))
        )
        if not loot_obj and any(r in raid for r in RESOURCE_KEYS):
            loot_obj = raid
        loot = {r: max(0.0, coerce_finite_float(loot_obj.get(r), 0.0)) for r in RESOURCE_KEYS}
        for r in RESOURCE_KEYS:
            loot_samples[r].append(loot[r])
        sp_eq = coerce_finite_float(raid.get("loot_spice_eq"), float(game_data.spice_equivalent(loot)))
        spice_eq_samples.append(max(0.0, sp_eq))
        outcome = str(raid.get("outcome") or "").strip().lower()
        if ("defeat" not in outcome) and ("loss" not in outcome):
            wins += 1
        else:
            losses += 1
        explicit_rt = coerce_finite_float(raid.get("round_trip_sec", raid.get("round_trip_seconds")), 0.0)
        explicit_ow = coerce_finite_float(raid.get("one_way_sec", raid.get("one_way_seconds")), 0.0)
        origin = str(raid.get("origin_coords") or default_origin).strip()
        target = str(raid.get("target_coords") or "").strip()
        if explicit_rt > 0.0:
            rt_samples.append(explicit_rt)
        elif explicit_ow > 0.0:
            rt_samples.append(2.0 * explicit_ow)
        elif is_valid_coords(origin) and is_valid_coords(target):
            rt_samples.append(2.0 * _estimate_physics_one_way_seconds(origin, target, fleet_speed_factor=fleet_speed))
    return loot_samples, spice_eq_samples, rt_samples, wins, losses


def compute_attack_telemetry(
    raid_rows: Sequence[Mapping[str, object]],
    message_rows: Sequence[Mapping[str, object]] | None = None,
    *,
    state: GameStateLike | None = None,
    realized_hourly_loot: Mapping[str, object] | None = None,
    realized_missions_per_hour: float | None = None,
) -> dict[str, object]:
    """Compute live median attack loot, round-trip flight time, fleet slots, and hourly loot."""
    normal_planets = [p for p in (state.planets if state is not None else ()) if normalize_planet_type(p) == "normal"]
    current_fleet_slots = int(sum(1 + p.building_level(10) for p in normal_planets))
    default_origin = (
        str(normal_planets[0].coords) if normal_planets and is_valid_coords(normal_planets[0].coords) else ""
    )
    fleet_speed = safe_speedup(state, {}) if state is not None else 5.0
    if fleet_speed <= 0.0:
        fleet_speed = 5.0

    norm_raids = [{str(k): v for k, v in r.items()} for r in (raid_rows or ()) if isinstance(r, Mapping)]
    battle_rows = _extract_battle_rows_from_messages(message_rows or ())

    if battle_rows:
        source = "battle_reports"
        loot_samples, spice_eq_samples, rt_samples, wins, losses = _collect_battle_samples(
            battle_rows, norm_raids, default_origin, fleet_speed
        )
    elif norm_raids:
        source = "raid_history"
        loot_samples, spice_eq_samples, rt_samples, wins, losses = _collect_raid_samples(
            norm_raids, default_origin, fleet_speed
        )
    else:
        source = "cold_start"
        loot_samples = {r: [] for r in RESOURCE_KEYS}
        spice_eq_samples, rt_samples, wins, losses = [], [], 0, 0

    sample_count = len(spice_eq_samples)
    median_rt = (
        float(np.median(np.asarray(rt_samples, dtype=np.float64))) if rt_samples else DEFAULT_MEDIAN_ROUND_TRIP_SECONDS
    )
    if not math.isfinite(median_rt) or median_rt <= 0.0:
        median_rt = DEFAULT_MEDIAN_ROUND_TRIP_SECONDS

    median_loot = {
        r: float(np.median(np.asarray(loot_samples[r], dtype=np.float64))) if sample_count > 0 else 0.0
        for r in RESOURCE_KEYS
    }
    median_loot["spice_eq"] = (
        float(np.median(np.asarray(spice_eq_samples, dtype=np.float64))) if sample_count > 0 else 0.0
    )
    mean_loot = {
        r: float(np.mean(np.asarray(loot_samples[r], dtype=np.float64))) if sample_count > 0 else 0.0
        for r in RESOURCE_KEYS
    }
    mean_loot["spice_eq"] = float(np.mean(np.asarray(spice_eq_samples, dtype=np.float64))) if sample_count > 0 else 0.0

    attacks_per_slot_per_hour = (3600.0 / median_rt) if (sample_count > 0 and median_rt > 0.0) else 0.0
    estimated_attacks_per_hour = float(current_fleet_slots) * attacks_per_slot_per_hour
    income_per_slot_per_hour = {r: median_loot[r] * attacks_per_slot_per_hour for r in RESOURCE_KEYS}
    hourly_loot: dict[str, float] = {r: income_per_slot_per_hour[r] * float(current_fleet_slots) for r in RESOURCE_KEYS}
    hourly_loot["spice_eq"] = float(game_data.spice_equivalent({r: hourly_loot[r] for r in RESOURCE_KEYS}))

    realized_extra: dict[str, object] = {}
    if isinstance(realized_hourly_loot, Mapping):
        modelled_hourly_loot = dict(hourly_loot)
        realized = {r: max(0.0, coerce_finite_float(realized_hourly_loot.get(r), 0.0)) for r in RESOURCE_KEYS}
        slot_div = float(max(1, current_fleet_slots))
        income_per_slot_per_hour = {r: realized[r] / slot_div for r in RESOURCE_KEYS}
        hourly_loot = dict(realized)
        hourly_loot["spice_eq"] = float(game_data.spice_equivalent(realized))
        realized_mph = coerce_finite_float(realized_missions_per_hour, -1.0)
        if realized_missions_per_hour is not None and realized_mph >= 0.0:
            estimated_attacks_per_hour = realized_mph
            attacks_per_slot_per_hour = realized_mph / slot_div
        realized_extra = {
            "income_basis": REALIZED_INCOME_BASIS,
            "modelled_hourly_loot": {k: round(v, 2) for k, v in modelled_hourly_loot.items()},
        }

    return {
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
