"""Genetic Algorithm + Neural Network action-combination optimizer & forward empire simulator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import math
import operator
import os
from pathlib import Path
import random
import re
import threading
import time
from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

from spaceinvasion import game_data
from spaceinvasion.config import StrategyConfig
from spaceinvasion.fleet import (
    DEFENSE_SLUG_BY_KEY,
    SHIP_SLUG_BY_KEY,
    BashTracker,
    GalaxySlot,
    SpyReport,
    fleet_cargo_capacity,
    normalize_coords,
    plan_escape_flight,
    score_raid_target,
    simulate_raid_outcome,
)
from spaceinvasion.ranking import POINTS_PER_RESOURCE_DIVISOR, _research_level_stats
from spaceinvasion.state import (
    BUILDING_ID_BY_NAME,
    RAID_COLONY_FORBIDDEN_BUILDINGS,
    BuildingOffer,
    EnergyBalance,
    GameState,
    PlanetState,
    QueueJob,
    ResearchOffer,
    Resources,
    UnitOffer,
    _parse_cost_from_text,
    is_asteroid_planet,
    is_building_allowed_on_planet_type,
    is_raid_planet,
    normalize_planet_type,
)

if TYPE_CHECKING:
    from spaceinvasion.strategy import PlannedAction

try:
    import torch
    import torch.nn as nn
except ImportError:  # pragma: no cover - optional runtime dependency
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]

TORCH_AVAILABLE: bool = torch is not None
STATE_FEATURE_DIM: int = 46
DEFAULT_ATTACK_HISTORY_PATH: Path = Path("data") / "attack_history.json"
_LAST_SUMMARY_LOCK = threading.Lock()
_LAST_OPTIMIZER_SUMMARY: Optional[dict[str, Any]] = None


def get_last_optimizer_summary() -> Optional[dict[str, Any]]:
    """Return a thread-safe JSON-safe snapshot of the last `run_optimizer` summary."""
    with _LAST_SUMMARY_LOCK:
        if _LAST_OPTIMIZER_SUMMARY is None:
            return None
        return json.loads(json.dumps(_json_safe(_LAST_OPTIMIZER_SUMMARY), allow_nan=False))


_RESOURCE_KEYS: tuple[str, ...] = ("pig_iron", "metal", "kryptonite", "spice")
_WAREHOUSE_ID_BY_RES: dict[str, tuple[int, str]] = {
    "pig_iron": (11, "Pig iron warehouse"),
    "metal": (12, "Metal warehouse"),
    "kryptonite": (13, "Kryptonite warehouse"),
    "spice": (14, "Spice warehouse"),
}
_RAID_COLONY_BANNED_BUILDINGS: frozenset[int] = RAID_COLONY_FORBIDDEN_BUILDINGS
_COMBAT_AND_RAID_SHIP_KEYS: tuple[str, ...] = (
    "s4",
    "s5",
    "s6",
    "s7",
    "s13",
    "s14",
    "s15",
    "s16",
    "s1",
    "s2",
    "s3",
)
_SHIP_KEY_BY_SLUG: dict[str, str] = {slug: skey for skey, slug in SHIP_SLUG_BY_KEY.items()}
_DEFENSE_KEY_BY_SLUG: dict[str, str] = {slug: dkey for dkey, slug in DEFENSE_SLUG_BY_KEY.items()}
_DEFAULT_MAX_WAIT_S: float = 365.0 * 86400.0
_MIN_RAID_ROUND_TRIP_S: float = 1800.0
_TARGET_RAID_COOLDOWN_S: float = 1800.0
_TIMER_RE = re.compile(r"(\d+):(\d{2}):(\d{2})", re.ASCII)
_LEVEL_RE = re.compile(r"\(\s*Level\s+(\d+)\s*\)", re.IGNORECASE | re.ASCII)
_GAL_SYS_RE = re.compile(r"(\d{1,2}):(\d{1,3})(?::(\d{1,2}))?", re.ASCII)


def _make_planned_action(**kwargs: Any) -> "PlannedAction":
    """Construct `PlannedAction` without importing `strategy` at module import time."""
    from spaceinvasion.strategy import PlannedAction

    return PlannedAction(**kwargs)


def _safe_float(val: float) -> float:
    return float(val) if math.isfinite(val) else 0.0


def _safe_float_any(val: Any, default: float = 0.0) -> float:
    if type(val) is float:
        return val if math.isfinite(val) else default
    if val is None or isinstance(val, bool):
        return default
    if type(val) is int:
        try:
            parsed = float(val)
        except OverflowError:
            return default
        return parsed if math.isfinite(parsed) else default
    if isinstance(val, (bytes, bytearray)):
        val = bytes(val).decode("utf-8", errors="replace")
    if isinstance(val, str):
        txt = val.strip()
        if not txt or not txt.isascii() or txt.lower() in ("null", "none", "~"):
            return default
        val = txt
    elif isinstance(val, (dict, list, tuple, set, frozenset)) or isinstance(val, Mapping):
        return default
    try:
        parsed = float(val)
    except Exception:
        return default
    return parsed if math.isfinite(parsed) else default


def _safe_int(val: Any, default: int = 0) -> int:
    return game_data.safe_int(val, default)


def _safe_action_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, os.PathLike):
        try:
            val = os.fspath(val)
        except Exception:
            return game_data.safe_key(val)
        if val is None:
            return ""
    if isinstance(val, str):
        return val
    if isinstance(val, int) and not isinstance(val, bool):
        return game_data.fmt_compact_int(val) if abs(val).bit_length() >= 12_000 else str(val)
    return game_data.safe_key(val)


def _parse_sim_quantity(raw: Any) -> Optional[int]:
    if raw is None:
        return 1
    if isinstance(raw, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, float):
        if not math.isfinite(raw) or not raw.is_integer():
            return None
        val = int(raw)
        return val if val > 0 else None
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        body = s[1:] if s[0] in "+-" else s
        if body and body.isascii() and body.isdigit():
            if s[0] == "-":
                return None
            digits = body.lstrip("0") or "0"
            if len(digits) > 4000:
                return game_data.MAX_JSON_SAFE_INT
            val = int(digits)
            return val if val > 0 else None
        return None
    parsed = game_data.safe_optional_int(raw, None)
    return parsed if parsed is not None and parsed > 0 else None


def _safe_level(
    val: Any,
    default: int = 1,
    *,
    min_val: int = 1,
    max_val: int = 1_000_000,
) -> int:
    parsed = game_data.safe_optional_int(val, None)
    if parsed is None:
        parsed = default
    return max(min_val, min(max_val, parsed))


def _fmt_sp_int(val: float) -> str:
    if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(float(val)):
        return "0"
    clamped = min(1e200, max(0.0, float(val)))
    return game_data.fmt_compact_int(int(round(clamped)), grouped=True)


def _safe_half_sq(err: float) -> float:
    if not math.isfinite(err):
        return 0.0
    if abs(err) > 1e140:
        return 0.5 * 1e280
    try:
        sq = 0.5 * err * err
    except OverflowError:
        return 0.5 * 1e280
    return sq if math.isfinite(sq) else 0.5 * 1e280


_MAX_OPT_TREE_NODES = 32_768
_MAX_OPT_TREE_DEPTH = 64


def _json_safe(
    obj: Any,
    *,
    _depth: int = 0,
    _seen: Optional[set[int]] = None,
    _budget: Optional[list[int]] = None,
) -> Any:
    seen = _seen if _seen is not None else set()
    budget = _budget if _budget is not None else [_MAX_OPT_TREE_NODES]

    def _walk(val: Any, d: int) -> Any:
        tv = type(val)
        if val is None or tv is bool or tv is str:
            return val
        if tv is int:
            return val if -game_data.MAX_JSON_SAFE_INT <= val <= game_data.MAX_JSON_SAFE_INT else game_data.cap_json_int(val)
        if tv is float:
            return val if math.isfinite(val) else 0.0
        is_dict = tv is dict
        is_seq = tv is list or tv is tuple
        if not (is_dict or is_seq):
            if isinstance(val, bool):
                return bool(val)
            if isinstance(val, str):
                return str(val)
            if isinstance(val, int):
                return game_data.cap_json_int(int(val))
            if isinstance(val, float):
                fv = float(val)
                return fv if math.isfinite(fv) else 0.0
            if isinstance(val, Resources):
                return {
                    "pig_iron": game_data.cap_json_int(getattr(val, "pig_iron", 0)),
                    "metal": game_data.cap_json_int(getattr(val, "metal", 0)),
                    "kryptonite": game_data.cap_json_int(getattr(val, "kryptonite", 0)),
                    "spice": game_data.cap_json_int(getattr(val, "spice", 0)),
                }
        if is_dict or is_seq or isinstance(val, (Mapping, list, tuple, set, frozenset)):
            vid = id(val)
            if vid in seen:
                return "[Cycle]"
            if d >= _MAX_OPT_TREE_DEPTH or budget[0] <= 0:
                return "[Truncated]"
            budget[0] -= 1
            seen.add(vid)
            try:
                if is_dict or isinstance(val, Mapping):
                    out: dict[str, Any] = {}
                    for k, v in val.items():
                        if budget[0] <= 0:
                            out["[Truncated]"] = "[Truncated]"
                            break
                        sk = k if type(k) is str else _safe_action_str(k)
                        out[sk] = _walk(v, d + 1)
                    return out
                out_list: list[Any] = []
                for v in val:
                    if budget[0] <= 0:
                        out_list.append("[Truncated]")
                        break
                    out_list.append(_walk(v, d + 1))
                return out_list
            finally:
                seen.discard(vid)
        return _safe_action_str(val)

    return _walk(obj, _depth)


def _parse_building_key_id(key: Any) -> Optional[int]:
    if not isinstance(key, str) or not key.startswith("b"):
        return None
    suffix = key[1:]
    if not suffix or len(suffix) > 6 or not (suffix.isascii() and suffix.isdigit()):
        return None
    bid = game_data.safe_optional_int(suffix, None)
    if bid is None or bid not in game_data.BUILDING_BY_ID:
        return None
    return bid


def _parse_building_target_id(target_id: Optional[int | str], default: int = 0) -> int:
    if target_id is None or isinstance(target_id, bool):
        return default
    if isinstance(target_id, int):
        return target_id
    raw = _safe_action_str(target_id).strip().lower()
    if raw.startswith("b"):
        raw = raw[1:].strip()
    if not raw:
        return default
    body = raw[1:] if raw[0] in "+-" else raw
    if not body or not (body.isascii() and body.isdigit()) or len(body) > 10:
        return default
    return int(raw)


def _resolve_queue_building(
    qjob: QueueJob,
    current_buildings: Mapping[str, int],
) -> Optional[tuple[int, int]]:
    """Resolve `(building_id, target_level)` for `QueueJob` including `building_id=None` home rows."""
    bid = game_data.safe_optional_int(qjob.building_id, None)
    if bid is None:
        clean_name = re.sub(r"\(.*?\)", "", _safe_action_str(qjob.name))
        clean_name = _TIMER_RE.sub("", clean_name).strip().lower()
        bid = BUILDING_ID_BY_NAME.get(clean_name)
    if bid is None:
        return None
    cur_l = max(0, _safe_int(current_buildings.get(f"b{bid}", 0), 0))
    q_tgt = game_data.safe_optional_int(qjob.target_level, None)
    if q_tgt is not None and q_tgt > cur_l:
        tgt_l = q_tgt
    else:
        m_lvl = _LEVEL_RE.search(_safe_action_str(qjob.name))
        tgt_l = _safe_int(m_lvl.group(1), cur_l + 1) if m_lvl else cur_l + 1
    tgt_l = _safe_level(tgt_l, default=cur_l + 1, min_val=cur_l + 1, max_val=1_000_000)
    return int(bid), int(tgt_l)


def _empty_loot_int() -> dict[str, int]:
    return {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}


def _empty_loot_float() -> dict[str, float]:
    return {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0}


def _parse_loot_dict(raw: Any) -> dict[str, int]:
    if raw is None:
        return _empty_loot_int()
    if isinstance(raw, Resources):
        return {
            "pig_iron": max(0, _safe_int(raw.pig_iron, 0)),
            "metal": max(0, _safe_int(raw.metal, 0)),
            "kryptonite": max(0, _safe_int(raw.kryptonite, 0)),
            "spice": max(0, _safe_int(raw.spice, 0)),
        }
    if isinstance(raw, str):
        txt = raw.strip()
        if not txt:
            return _empty_loot_int()
        try:
            raw = json.loads(txt)
        except ValueError:
            return _empty_loot_int()
    if isinstance(raw, Mapping):
        return {rk: max(0, _safe_int(raw.get(rk, 0), 0)) for rk in _RESOURCE_KEYS}
    return _empty_loot_int()


def _parse_loot_float_dict(raw: Any) -> dict[str, float]:
    if raw is None:
        return _empty_loot_float()
    if isinstance(raw, Resources):
        return {rk: _safe_res_float(getattr(raw, rk, 0), 1e200) for rk in _RESOURCE_KEYS}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return _empty_loot_float()
    if isinstance(raw, Mapping):
        return {rk: max(0.0, _safe_float_any(raw.get(rk, 0.0), 0.0)) for rk in _RESOURCE_KEYS}
    return _empty_loot_float()


def _outcome_explicit_loss(outcome: Any) -> bool:
    s = _safe_action_str(outcome).strip().lower()
    if not s:
        return False
    return any(
        tok in s
        for tok in (
            "loss",
            "lost",
            "defeat",
            "fail",
            "abort",
            "draw",
            "destroy",
            "repel",
            "blocked",
            "error",
            "timeout",
            "intercept",
        )
    )


def _is_win_entry(
    outcome: Any,
    loot_sp_eq: float,
    *,
    won_flag: Any = None,
    success_flag: Any = None,
) -> bool:
    if won_flag is not None:
        return bool(won_flag)
    if success_flag is not None:
        return bool(success_flag)
    if _outcome_explicit_loss(outcome):
        return False
    if loot_sp_eq > 0.0:
        return True
    s = _safe_action_str(outcome).strip().lower()
    return any(tok in s for tok in ("win", "won", "vict", "succ", "loot", "plunder", "captur", "ok"))


def load_attack_history_stats(path: Optional[Path | str] = None) -> dict[str, Any]:
    """Safely load read-only attack & telemetry history JSON (`data/attack_history.json`)."""
    if path is not None and isinstance(path, str) and not path.strip():
        return {}
    target_path = Path(path) if path is not None else DEFAULT_ATTACK_HISTORY_PATH
    try:
        if not target_path.is_file():
            return {}
        raw = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if isinstance(raw, list):
        return _json_safe({"history": [dict(x) for x in raw if isinstance(x, Mapping)]})
    if not isinstance(raw, Mapping):
        return {}
    out = dict(raw)
    if not any(k in out for k in ("overall", "per_target", "history", "telemetry_summary")):
        for wrap_key in ("attack_history", "raid_stats", "stats", "data"):
            inner = out.get(wrap_key)
            if isinstance(inner, Mapping):
                return _json_safe(dict(inner))
            if isinstance(inner, list):
                return _json_safe({"history": [dict(x) for x in inner if isinstance(x, Mapping)]})
    return _json_safe(out)


def _aggregate_group_stats(
    rows: Sequence[dict[str, Any]],
    *,
    ref_ts: float,
) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "attempts": 0,
            "wins": 0,
            "losses": 0,
            "success_rate": 0.0,
            "total_loot": _empty_loot_int(),
            "avg_loot": _empty_loot_float(),
            "total_loot_spice_eq": 0.0,
            "avg_loot_spice_eq": 0.0,
            "recency_weighted_loot_spice_eq": 0.0,
        }
    wins = sum(1 for r in rows if r["won"])
    losses = n - wins
    tot_loot = _empty_loot_int()
    tot_sp = 0.0
    order_weights: list[float] = []
    combined_weights: list[float] = []
    sp_values: list[float] = []
    for idx, r in enumerate(rows):
        for rk in _RESOURCE_KEYS:
            tot_loot[rk] += int(r["loot"][rk])
        sp = float(r["loot_spice_eq"])
        sp_values.append(sp)
        tot_sp += sp
        rank_from_latest = (n - 1) - idx
        order_w = 0.65 ** rank_from_latest
        ts_i = float(r["attacked_ts"])
        time_w = math.exp(-max(0.0, ref_ts - ts_i) / (3.0 * 86400.0)) if (ts_i > 0.0 and ref_ts > 0.0) else 0.75
        order_weights.append(order_w)
        combined_weights.append(order_w * time_w)
    weight_tot = sum(combined_weights)
    if weight_tot <= 1e-12:
        weights = order_weights
        weight_tot = sum(weights)
    else:
        weights = combined_weights
    weighted_sum = sum(w * sp for w, sp in zip(weights, sp_values))
    avg_loot = {
        rk: round(game_data.safe_big_ratio(tot_loot[rk], n, cap=1e200), 3)
        for rk in _RESOURCE_KEYS
    }
    return {
        "attempts": n,
        "wins": wins,
        "losses": losses,
        "success_rate": round(wins / n, 6),
        "total_loot": tot_loot,
        "avg_loot": avg_loot,
        "total_loot_spice_eq": round(min(1e200, tot_sp), 3),
        "avg_loot_spice_eq": round(min(1e200, tot_sp / n), 3),
        "recency_weighted_loot_spice_eq": round(min(1e200, weighted_sum / max(1e-12, weight_tot)), 3),
    }


def _aggregate_raw_raid_history(
    entries: Sequence[Mapping[str, Any]],
    *,
    now_ts: Optional[float] = None,
) -> dict[str, Any]:
    """Aggregate raw `raid_history` records into exact `overall`, `per_target`, and `history` dicts."""
    norm_rows: list[dict[str, Any]] = []
    for orig_idx, raw in enumerate(entries or ()):
        if not isinstance(raw, Mapping):
            continue
        raw_coords = _safe_action_str(raw.get("target_coords") or raw.get("coords")).strip()
        coords = normalize_coords(raw_coords) if raw_coords else ""
        raw_ts = (
            raw.get("attacked_ts")
            if raw.get("attacked_ts") is not None
            else (raw.get("ts") if raw.get("ts") is not None else raw.get("timestamp"))
        )
        ts_val = _safe_float_any(raw_ts, 0.0)
        is_exempt = bool(raw.get("is_exempt", False))
        outcome = _safe_action_str(raw.get("outcome") or raw.get("result"))
        loot_src = (
            raw.get("loot")
            if isinstance(raw.get("loot"), Mapping)
            else (raw.get("loot_json") if raw.get("loot_json") is not None else raw.get("resources"))
        )
        loot = _parse_loot_dict(loot_src)
        explicit_sp = raw.get("loot_spice_eq")
        default_sp = float(game_data.spice_equivalent(loot))
        loot_sp = max(0.0, _safe_float_any(explicit_sp, default_sp)) if explicit_sp is not None else default_sp
        won = _is_win_entry(
            outcome,
            loot_sp,
            won_flag=raw.get("won"),
            success_flag=raw.get("success"),
        )
        norm_rows.append(
            {
                "_orig_idx": orig_idx,
                "target_coords": coords,
                "attacked_ts": ts_val,
                "is_exempt": is_exempt,
                "outcome": outcome,
                "loot": loot,
                "loot_spice_eq": round(loot_sp, 3),
                "won": won,
            }
        )

    norm_rows.sort(key=lambda r: (float(r["attacked_ts"]), int(r["_orig_idx"])))
    max_row_ts = max((float(r["attacked_ts"]) for r in norm_rows), default=0.0)
    ref_ts = float(now_ts) if (now_ts is not None and now_ts > 0.0) else max_row_ts

    overall = _aggregate_group_stats(norm_rows, ref_ts=ref_ts)
    if norm_rows:
        last_r = norm_rows[-1]
        overall["last_attack"] = {
            "target_coords": last_r["target_coords"],
            "attacked_ts": last_r["attacked_ts"],
            "outcome": last_r["outcome"],
            "loot": dict(last_r["loot"]),
            "loot_spice_eq": last_r["loot_spice_eq"],
        }
    else:
        overall["last_attack"] = None

    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in norm_rows:
        c = r["target_coords"]
        if c:
            grouped.setdefault(c, []).append(r)

    per_target: dict[str, dict[str, Any]] = {}
    for coords, t_rows in grouped.items():
        t_agg = _aggregate_group_stats(t_rows, ref_ts=ref_ts)
        t_last = t_rows[-1]
        t_agg["last_outcome"] = t_last["outcome"]
        t_agg["last_attacked_ts"] = t_last["attacked_ts"]
        t_agg["last_loot"] = dict(t_last["loot"])
        t_agg["last_loot_spice_eq"] = t_last["loot_spice_eq"]
        per_target[coords] = t_agg

    clean_history = [
        {k: v for k, v in r.items() if k != "_orig_idx"}
        for r in norm_rows
    ]
    return {
        "overall": overall,
        "per_target": per_target,
        "history": clean_history,
        "telemetry_summary": {},
    }


def _normalize_target_stat_entry(raw: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    has_hist = False
    attempts = 0
    explicit_zero_attempts = False
    for k in ("attack_attempts", "attempts", "attack_count"):
        if raw.get(k) is not None:
            parsed_att = _safe_int(raw.get(k), -1)
            if parsed_att >= 0:
                attempts = parsed_att
                if attempts > 0:
                    has_hist = True
                else:
                    explicit_zero_attempts = True
                break
    wins: Optional[int] = None
    for k in ("attack_wins", "wins", "win_count"):
        if raw.get(k) is not None:
            parsed_w = _safe_int(raw.get(k), -1)
            if parsed_w >= 0:
                wins = parsed_w
                has_hist = has_hist or wins > 0
                break
    losses: Optional[int] = None
    for k in ("attack_losses", "losses", "loss_count"):
        if raw.get(k) is not None:
            parsed_l = _safe_int(raw.get(k), -1)
            if parsed_l >= 0:
                losses = parsed_l
                has_hist = has_hist or losses > 0
                break
    raw_sr: Optional[float] = None
    for k in ("attack_success_rate", "success_rate", "win_rate"):
        if raw.get(k) is not None:
            val = _safe_float_any(raw[k], float("nan"))
            if math.isfinite(val):
                raw_sr = max(0.0, min(1.0, val))
                if not explicit_zero_attempts or raw_sr > 0.0:
                    has_hist = True
                break

    last_loot_raw = (
        raw.get("last_attack_loot")
        if raw.get("last_attack_loot") is not None
        else (raw.get("last_loot") if raw.get("last_loot") is not None else None)
    )
    last_loot = _parse_loot_dict(last_loot_raw)
    last_loot_sp: Optional[float] = None
    if raw.get("last_attack_loot_spice_eq") is not None or raw.get("last_loot_spice_eq") is not None:
        v = raw.get("last_attack_loot_spice_eq") if raw.get("last_attack_loot_spice_eq") is not None else raw.get("last_loot_spice_eq")
        parsed_v = _safe_float_any(v, float("nan"))
        if math.isfinite(parsed_v):
            last_loot_sp = max(0.0, parsed_v)
            if not explicit_zero_attempts or last_loot_sp > 0.0:
                has_hist = True
    elif last_loot_raw is not None:
        if isinstance(last_loot_raw, (int, float)) and not isinstance(last_loot_raw, bool):
            last_loot_sp = max(0.0, _safe_float_any(last_loot_raw, 0.0))
        else:
            last_loot_sp = float(game_data.spice_equivalent(last_loot))
        if not explicit_zero_attempts or last_loot_sp > 0.0:
            has_hist = True

    avg_loot_raw = raw.get("avg_attack_loot") if raw.get("avg_attack_loot") is not None else raw.get("avg_loot")
    avg_loot = _parse_loot_float_dict(avg_loot_raw)
    avg_loot_sp: Optional[float] = None
    if raw.get("avg_attack_loot_spice_eq") is not None or raw.get("avg_loot_spice_eq") is not None:
        v = raw.get("avg_attack_loot_spice_eq") if raw.get("avg_attack_loot_spice_eq") is not None else raw.get("avg_loot_spice_eq")
        parsed_v = _safe_float_any(v, float("nan"))
        if math.isfinite(parsed_v):
            avg_loot_sp = max(0.0, parsed_v)
            if not explicit_zero_attempts or avg_loot_sp > 0.0:
                has_hist = True
    elif avg_loot_raw is not None:
        if isinstance(avg_loot_raw, (int, float)) and not isinstance(avg_loot_raw, bool):
            avg_loot_sp = max(0.0, _safe_float_any(avg_loot_raw, 0.0))
        else:
            avg_loot_sp = float(game_data.spice_equivalent(avg_loot))
        if not explicit_zero_attempts or avg_loot_sp > 0.0:
            has_hist = True

    tot_loot_raw = raw.get("total_attack_loot") if raw.get("total_attack_loot") is not None else raw.get("total_loot")
    tot_loot = _parse_loot_dict(tot_loot_raw)
    tot_loot_sp: Optional[float] = None
    if raw.get("total_attack_loot_spice_eq") is not None or raw.get("total_loot_spice_eq") is not None:
        v = raw.get("total_attack_loot_spice_eq") if raw.get("total_attack_loot_spice_eq") is not None else raw.get("total_loot_spice_eq")
        parsed_v = _safe_float_any(v, float("nan"))
        if math.isfinite(parsed_v):
            tot_loot_sp = max(0.0, parsed_v)
            if not explicit_zero_attempts or tot_loot_sp > 0.0:
                has_hist = True
    elif tot_loot_raw is not None:
        if isinstance(tot_loot_raw, (int, float)) and not isinstance(tot_loot_raw, bool):
            tot_loot_sp = max(0.0, _safe_float_any(tot_loot_raw, 0.0))
        else:
            tot_loot_sp = float(game_data.spice_equivalent(tot_loot))
        if not explicit_zero_attempts or tot_loot_sp > 0.0:
            has_hist = True

    rec_loot_sp: Optional[float] = None
    if raw.get("recency_weighted_loot_spice_eq") is not None:
        parsed_v = _safe_float_any(raw["recency_weighted_loot_spice_eq"], float("nan"))
        if math.isfinite(parsed_v):
            rec_loot_sp = max(0.0, parsed_v)
            if not explicit_zero_attempts or rec_loot_sp > 0.0:
                has_hist = True

    last_outcome = _safe_action_str(
        raw.get("last_attack_outcome") or raw.get("last_outcome")
    )
    if last_outcome:
        has_hist = True
    last_ts = max(
        0.0,
        _safe_float_any(
            raw.get("last_attack_ts")
            if raw.get("last_attack_ts") is not None
            else raw.get("last_attacked_ts"),
            0.0,
        ),
    )
    if last_ts > 0.0:
        has_hist = True

    if not has_hist:
        return None

    if attempts == 0 and (wins is not None or losses is not None):
        attempts = (wins or 0) + (losses or 0)
    if attempts == 0 and (
        not explicit_zero_attempts
        or (raw_sr is not None and raw_sr > 0.0)
        or (last_loot_sp is not None and last_loot_sp > 0.0)
        or (rec_loot_sp is not None and rec_loot_sp > 0.0)
        or (avg_loot_sp is not None and avg_loot_sp > 0.0)
        or (tot_loot_sp is not None and tot_loot_sp > 0.0)
        or bool(last_outcome)
        or last_ts > 0.0
    ):
        attempts = 1
    if wins is None and losses is not None:
        wins = max(0, attempts - losses)
    elif wins is None and raw_sr is not None:
        wins = max(0, min(attempts, int(round(attempts * raw_sr))))
    elif wins is None:
        wins = 0 if _outcome_explicit_loss(last_outcome) else (1 if (last_loot_sp or 0.0) > 0.0 else 0)
    if losses is None:
        losses = max(0, attempts - wins)

    sr = raw_sr if raw_sr is not None else ((wins / attempts) if attempts > 0 else 0.0)
    eff_last_sp = last_loot_sp if last_loot_sp is not None else (avg_loot_sp if avg_loot_sp is not None else 0.0)
    eff_tot_sp = tot_loot_sp if tot_loot_sp is not None else (
        (avg_loot_sp * attempts) if avg_loot_sp is not None else eff_last_sp * attempts
    )
    eff_avg_sp = avg_loot_sp if avg_loot_sp is not None else (
        (eff_tot_sp / attempts) if attempts > 0 else eff_last_sp
    )
    eff_rec_sp = rec_loot_sp if rec_loot_sp is not None else (0.6 * eff_last_sp + 0.4 * eff_avg_sp)

    return {
        "attempts": int(attempts),
        "wins": int(wins),
        "losses": int(losses),
        "success_rate": round(float(sr), 6),
        "total_loot": tot_loot,
        "avg_loot": avg_loot,
        "total_loot_spice_eq": round(float(eff_tot_sp), 3),
        "avg_loot_spice_eq": round(float(eff_avg_sp), 3),
        "recency_weighted_loot_spice_eq": round(float(eff_rec_sp), 3),
        "last_outcome": last_outcome,
        "last_attacked_ts": float(last_ts),
        "last_loot": last_loot,
        "last_loot_spice_eq": round(float(eff_last_sp), 3),
        "_explicit_sr": raw_sr is not None,
    }


def _normalize_telemetry_summary(
    raw: Any,
    *,
    raid_targets_count: int = 0,
) -> dict[str, Any]:
    src = dict(raw) if isinstance(raw, Mapping) else {}

    def _f(*keys: str, default: float = 0.0) -> float:
        for k in keys:
            if src.get(k) is not None:
                val = _safe_float_any(src[k], float("nan"))
                if math.isfinite(val):
                    return val
        return default

    def _i(*keys: str, default: int = 0) -> int:
        for k in keys:
            if src.get(k) is not None:
                val = _safe_int(src[k], -1)
                if val >= 0:
                    return val
        return default

    pts_delta = _f("points_delta", "points_growth", "points_trend")
    pts_trend = _f("points_trend", "points_delta", "points_growth")
    pts_hr = _f("pts_per_hr", "economy_trend", "pts_per_hr_steady")
    econ_trend = _f("economy_trend", "economy_delta", "pts_per_hr")
    act_ok = _i("action_successes", "executed_actions")
    act_tot = _i("action_count", "actions_total", "total_actions")
    act_sr = _f(
        "action_success_rate",
        default=(act_ok / act_tot) if act_tot > 0 else 1.0,
    )
    act_sr = max(0.0, min(1.0, act_sr))
    spy_cnt = max(raid_targets_count, _i("spy_targets_count", "spy_target_count", "targets_count"))
    return {
        "points_delta": round(pts_delta, 3),
        "points_trend": round(pts_trend, 3),
        "pts_per_hr": round(pts_hr, 3),
        "economy_trend": round(econ_trend, 3),
        "action_success_rate": round(act_sr, 4),
        "action_count": act_tot,
        "spy_targets_count": spy_cnt,
    }


def _synthesize_overall_from_per_target(per_target: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if not per_target:
        return {
            "attempts": 0,
            "wins": 0,
            "losses": 0,
            "success_rate": 0.0,
            "total_loot": _empty_loot_int(),
            "avg_loot": _empty_loot_float(),
            "total_loot_spice_eq": 0.0,
            "avg_loot_spice_eq": 0.0,
            "recency_weighted_loot_spice_eq": 0.0,
            "last_attack": None,
        }
    attempts = 0
    wins = 0
    losses = 0
    tot_sp = 0.0
    rec_num = 0.0
    rec_den = 0.0
    tot_loot = _empty_loot_int()
    latest_ts = -1.0
    latest_coords = ""
    latest_entry: Optional[Mapping[str, Any]] = None
    for coords, item in per_target.items():
        att = max(0, _safe_int(item.get("attempts", 0), 0))
        w = max(0, _safe_int(item.get("wins", 0), 0))
        l = max(0, _safe_int(item.get("losses", max(0, att - w)), 0))
        attempts += att
        wins += w
        losses += l
        sp_tot = max(0.0, _safe_float_any(item.get("total_loot_spice_eq", 0.0), 0.0))
        tot_sp += sp_tot
        wt = max(1, att)
        rec_sp = max(0.0, _safe_float_any(item.get("recency_weighted_loot_spice_eq", 0.0), 0.0))
        rec_num += rec_sp * wt
        rec_den += wt
        tl = _parse_loot_dict(item.get("total_loot"))
        for rk in _RESOURCE_KEYS:
            tot_loot[rk] += tl[rk]
        ts_val = max(0.0, _safe_float_any(item.get("last_attacked_ts", 0.0), 0.0))
        if latest_entry is None or ts_val >= latest_ts:
            latest_ts = ts_val
            latest_coords = coords
            latest_entry = item
    sr = (wins / attempts) if attempts > 0 else (
        sum(_safe_float_any(v.get("success_rate", 0.0), 0.0) for v in per_target.values()) / max(1, len(per_target))
    )
    avg_sp = (tot_sp / attempts) if attempts > 0 else 0.0
    avg_loot = {
        rk: round(game_data.safe_big_ratio(tot_loot[rk], max(1, attempts), cap=1e200), 3)
        for rk in _RESOURCE_KEYS
    }
    last_atk = None
    if latest_entry is not None:
        last_atk = {
            "target_coords": latest_coords,
            "attacked_ts": max(0.0, _safe_float_any(latest_entry.get("last_attacked_ts", 0.0), 0.0)),
            "outcome": _safe_action_str(latest_entry.get("last_outcome")),
            "loot": _parse_loot_dict(latest_entry.get("last_loot")),
            "loot_spice_eq": max(0.0, _safe_float_any(latest_entry.get("last_loot_spice_eq", 0.0), 0.0)),
        }
    return {
        "attempts": attempts,
        "wins": wins,
        "losses": losses,
        "success_rate": round(max(0.0, min(1.0, sr)), 6),
        "total_loot": tot_loot,
        "avg_loot": avg_loot,
        "total_loot_spice_eq": round(min(1e200, tot_sp), 3),
        "avg_loot_spice_eq": round(min(1e200, avg_sp), 3),
        "recency_weighted_loot_spice_eq": round(min(1e200, rec_num / max(1.0, rec_den)), 3),
        "last_attack": last_atk,
    }


def _merge_stats_layers(
    base: dict[str, Any],
    overlay: Mapping[str, Any],
    *,
    now_ts: float,
) -> dict[str, Any]:
    """Merge an overlay stats dict cleanly onto `base`, recomputing missing aggregates from raw `history`."""
    ov = dict(overlay)
    ov_hist = ov.get("history")
    if isinstance(ov_hist, Sequence) and not isinstance(ov_hist, (str, bytes)) and len(ov_hist) > 0:
        agg_ov = _aggregate_raw_raid_history(ov_hist, now_ts=now_ts)
        if not isinstance(ov.get("overall"), Mapping) or _safe_int((ov.get("overall") or {}).get("attempts", 0), 0) == 0:
            ov["overall"] = agg_ov["overall"]
        if not isinstance(ov.get("per_target"), Mapping) or not ov.get("per_target"):
            ov["per_target"] = agg_ov["per_target"]
        ov["history"] = agg_ov["history"]

    merged_pt: dict[str, Any] = {}
    for k, v in (base.get("per_target") or {}).items():
        k_str = _safe_action_str(k).strip()
        if not k_str:
            continue
        merged_pt[normalize_coords(k_str)] = v
    pt_updated = False
    if isinstance(ov.get("per_target"), Mapping):
        for coords, raw_pt in ov["per_target"].items():
            if not isinstance(raw_pt, Mapping):
                continue
            raw_c_key = _safe_action_str(coords).strip()
            if not raw_c_key:
                continue
            c_key = normalize_coords(raw_c_key)
            norm_pt = _normalize_target_stat_entry(raw_pt)
            if norm_pt is not None:
                merged_pt[c_key] = norm_pt
                if int(norm_pt.get("attempts", 0) or 0) > 0:
                    pt_updated = True

    merged_hist = list(base.get("history") or [])
    if isinstance(ov.get("history"), list) and ov["history"]:
        merged_hist = list(ov["history"])

    merged_overall = dict(base.get("overall") or {})
    ov_overall = ov.get("overall")
    has_ov_overall = False
    if isinstance(ov_overall, Mapping):
        ov_att = max(0, _safe_int(ov_overall.get("attempts", 0), 0))
        ov_sr_parsed = _safe_float_any(ov_overall.get("success_rate"), float("nan"))
        has_ov_sr = ov_overall.get("success_rate") is not None and math.isfinite(ov_sr_parsed)
        if ov_att > 0 or ov_overall.get("last_attack") is not None or has_ov_sr:
            has_ov_overall = True
            ov_avg_sp = max(0.0, _safe_float_any(ov_overall.get("avg_loot_spice_eq", 0.0), 0.0))
            ov_rec_raw = ov_overall.get("recency_weighted_loot_spice_eq")
            ov_rec_sp = max(0.0, _safe_float_any(ov_rec_raw, ov_avg_sp)) if ov_rec_raw is not None else ov_avg_sp
            merged_overall = {
                "attempts": ov_att,
                "wins": max(0, _safe_int(ov_overall.get("wins", 0), 0)),
                "losses": max(0, _safe_int(ov_overall.get("losses", 0), 0)),
                "success_rate": max(0.0, min(1.0, ov_sr_parsed if has_ov_sr else 0.0)),
                "total_loot": _parse_loot_dict(ov_overall.get("total_loot")),
                "avg_loot": _parse_loot_float_dict(ov_overall.get("avg_loot")),
                "total_loot_spice_eq": max(0.0, _safe_float_any(ov_overall.get("total_loot_spice_eq", 0.0), 0.0)),
                "avg_loot_spice_eq": ov_avg_sp,
                "recency_weighted_loot_spice_eq": ov_rec_sp,
                "last_attack": _json_safe(ov_overall.get("last_attack")),
            }
    if pt_updated and not has_ov_overall and merged_pt:
        merged_overall = _synthesize_overall_from_per_target(merged_pt)

    merged_tel = dict(base.get("telemetry_summary") or {})
    if isinstance(ov.get("telemetry_summary"), Mapping):
        merged_tel.update({_safe_action_str(k): _json_safe(v) for k, v in ov["telemetry_summary"].items() if v is not None})

    return {
        "overall": merged_overall,
        "per_target": merged_pt,
        "history": merged_hist,
        "telemetry_summary": merged_tel,
    }


def _resolve_complete_attack_history(
    state: GameState,
    *,
    raid_stats: Optional[Mapping[str, Any]] = None,
    raid_history: Optional[Sequence[Mapping[str, Any]]] = None,
    attack_history_path: Optional[Path | str] = None,
) -> dict[str, Any]:
    """Merge complete attack & telemetry history in precedence order:
    1. Explicit `raid_stats` / `raid_history` / `attack_history_path` args (suppresses default disk fallback),
    2. `state.raid_stats` / `state.raid_history`,
    3. `state.raid_targets` per-target fields,
    4. Fallback `load_attack_history_stats(None)` (`data/attack_history.json`).
    """
    now_ts = max(0.0, _safe_float_any(getattr(state, "timestamp", 0.0), 0.0))
    explicit_given = (
        raid_stats is not None
        or raid_history is not None
        or attack_history_path is not None
    )

    state_stats_raw = getattr(state, "raid_stats", None)
    state_hist_raw = getattr(state, "raid_history", None)
    has_state_stats = isinstance(state_stats_raw, Mapping) and bool(state_stats_raw)
    has_state_hist = isinstance(state_hist_raw, Sequence) and not isinstance(state_hist_raw, (str, bytes)) and len(state_hist_raw) > 0

    rt_per_target: dict[str, dict[str, Any]] = {}
    for raw in state.raid_targets or ():
        if not isinstance(raw, Mapping):
            continue
        raw_c = _safe_action_str(raw.get("coords") or raw.get("target_coords")).strip()
        if not raw_c:
            continue
        coords = normalize_coords(raw_c)
        norm = _normalize_target_stat_entry(raw)
        if norm is not None:
            rt_per_target[coords] = norm
    has_rt_hist = bool(rt_per_target)

    merged: dict[str, Any] = _aggregate_raw_raid_history((), now_ts=now_ts)

    # Layer 4: Disk fallback only when NO explicit arg and NO state/target history provided
    if not explicit_given and not has_state_stats and not has_state_hist and not has_rt_hist:
        disk_stats = load_attack_history_stats(None)
        if disk_stats:
            merged = _merge_stats_layers(merged, disk_stats, now_ts=now_ts)

    # Layer 3: Per-target history embedded in `state.raid_targets`
    if rt_per_target:
        merged = _merge_stats_layers(merged, {"per_target": rt_per_target}, now_ts=now_ts)

    # Layer 2: `state.raid_stats` / `state.raid_history`
    if has_state_hist and state_hist_raw is not None:
        merged = _merge_stats_layers(merged, _aggregate_raw_raid_history(state_hist_raw, now_ts=now_ts), now_ts=now_ts)
    if has_state_stats and state_stats_raw is not None:
        merged = _merge_stats_layers(merged, state_stats_raw, now_ts=now_ts)

    # Layer 1: Explicit arguments (`attack_history_path`, `raid_history`, `raid_stats`)
    if attack_history_path is not None:
        file_stats = load_attack_history_stats(attack_history_path)
        if file_stats:
            merged = _merge_stats_layers(merged, file_stats, now_ts=now_ts)
    if raid_history is not None:
        merged = _merge_stats_layers(merged, _aggregate_raw_raid_history(raid_history, now_ts=now_ts), now_ts=now_ts)
    if raid_stats is not None:
        merged = _merge_stats_layers(merged, raid_stats, now_ts=now_ts)

    if _safe_int((merged.get("overall") or {}).get("attempts", 0), 0) == 0 and merged.get("per_target"):
        merged["overall"] = _synthesize_overall_from_per_target(merged["per_target"])

    merged["telemetry_summary"] = _normalize_telemetry_summary(
        merged.get("telemetry_summary"),
        raid_targets_count=len(state.raid_targets or ()),
    )
    return _json_safe(merged)


def _compute_effective_raid_expectation(
    combat_loot_sp: float,
    cargo_capacity: int,
    t_coords: str,
    attack_history_stats: Mapping[str, Any],
    *,
    target_depleted: bool = False,
) -> tuple[float, float, Optional[dict[str, Any]]]:
    """Return `(expected_loot_spice_eq, eff_win_prob, target_hist)` using Bayesian win-rate + full loot calibration."""
    per_target = attack_history_stats.get("per_target") or {}
    overall = attack_history_stats.get("overall") or {}
    raw_c = _safe_action_str(t_coords).strip()
    norm_c = normalize_coords(raw_c) if raw_c else ""
    t_hist: Any = None
    if isinstance(per_target, Mapping):
        if norm_c:
            t_hist = per_target.get(norm_c)
        if t_hist is None and raw_c:
            t_hist = per_target.get(raw_c)
        if t_hist is None and norm_c:
            for k, v in per_target.items():
                if normalize_coords(_safe_action_str(k)) == norm_c:
                    t_hist = v
                    break

    o_att = max(0, _safe_int(overall.get("attempts", 0), 0))
    o_wins = max(0, _safe_int(overall.get("wins", 0), 0))
    o_sr = max(0.0, min(1.0, _safe_float_any(overall.get("success_rate", 0.0), 0.0)))

    t_att = max(0, _safe_int(t_hist.get("attempts", 0), 0)) if isinstance(t_hist, Mapping) else 0
    if isinstance(t_hist, Mapping) and t_att > 0:
        t_wins = max(0, _safe_int(t_hist.get("wins", 0), 0))
        t_sr = max(0.0, min(1.0, _safe_float_any(t_hist.get("success_rate", 0.0), 0.0)))
        t_last_outcome = _safe_action_str(t_hist.get("last_outcome"))
        t_explicit_sr = bool(t_hist.get("_explicit_sr", False))

        if not (t_explicit_sr and t_att == 1 and t_wins == 0 and t_sr > 0.0):
            prior_n = 2.0
            prior_base = 0.85 if o_att == 0 else (0.55 + 0.35 * o_sr)
            eff_win_prob = (t_wins + prior_n * prior_base) / (t_att + prior_n)
            if t_explicit_sr and abs(t_sr - (t_wins / max(1, t_att))) > 0.15:
                eff_win_prob = 0.5 * eff_win_prob + 0.5 * (0.20 + 0.80 * t_sr)
        else:
            eff_win_prob = 0.15 + 0.85 * t_sr

        if _outcome_explicit_loss(t_last_outcome):
            eff_win_prob *= 0.82
        eff_win_prob = max(0.05, min(1.0, eff_win_prob))

        if target_depleted or combat_loot_sp <= 0.0:
            return 0.0, eff_win_prob, dict(t_hist)

        avg_sp = max(0.0, _safe_float_any(t_hist.get("avg_loot_spice_eq", 0.0), 0.0))
        rec_sp = max(0.0, _safe_float_any(t_hist.get("recency_weighted_loot_spice_eq", avg_sp), avg_sp))
        last_sp = max(0.0, _safe_float_any(t_hist.get("last_loot_spice_eq", avg_sp), avg_sp))
        tot_sp = max(0.0, _safe_float_any(t_hist.get("total_loot_spice_eq", avg_sp * max(1, t_att)), 0.0))
        emp_loot_sp = 0.45 * rec_sp + 0.30 * last_sp + 0.25 * avg_sp

        cap_limit = max(combat_loot_sp, _safe_res_float(cargo_capacity, 1e100))
        emp_capped = min(emp_loot_sp, cap_limit, 2.5 * combat_loot_sp)
        blended_loot = 0.55 * combat_loot_sp + 0.45 * emp_capped

        cum_bonus = 1.0 + (
            min(0.15, 0.03 * math.log1p(tot_sp / 1000.0))
            if (t_wins > 0 and last_sp > 0.0 and eff_win_prob >= 0.60)
            else 0.0
        )
        return max(0.0, blended_loot * eff_win_prob * cum_bonus), eff_win_prob, dict(t_hist)

    if o_att > 0:
        eff_win_prob = max(0.35, min(0.98, (o_wins + 4.0 * 0.90) / (o_att + 4.0)))
        if target_depleted or combat_loot_sp <= 0.0:
            return 0.0, eff_win_prob, None
        o_rec = max(0.0, _safe_float_any(overall.get("recency_weighted_loot_spice_eq", 0.0), 0.0))
        o_last_raw = overall.get("last_attack")
        o_last = (
            max(0.0, _safe_float_any(o_last_raw.get("loot_spice_eq", 0.0), 0.0))
            if isinstance(o_last_raw, Mapping)
            else 0.0
        )
        emp_hint = 0.6 * o_rec + 0.4 * o_last
        if emp_hint > 0.0:
            cap_limit = max(combat_loot_sp, _safe_res_float(cargo_capacity, 1e100))
            blended_loot = 0.80 * combat_loot_sp + 0.20 * min(emp_hint, cap_limit, 2.0 * combat_loot_sp)
        else:
            blended_loot = combat_loot_sp
        return max(0.0, blended_loot * eff_win_prob), eff_win_prob, None

    if target_depleted or combat_loot_sp <= 0.0:
        return 0.0, 1.0, None
    return max(0.0, combat_loot_sp), 1.0, None


def _coords_distance(a: str, b: str) -> float:
    pa = [min(999_999, int(x[:9])) for x in re.findall(r"\d+", _safe_action_str(a), re.ASCII)[:3]]
    pb = [min(999_999, int(x[:9])) for x in re.findall(r"\d+", _safe_action_str(b), re.ASCII)[:3]]
    if len(pa) < 3 or len(pb) < 3:
        return 100.0
    if pa[0] != pb[0]:
        return 20000.0 * abs(pa[0] - pb[0])
    if pa[1] != pb[1]:
        return 2700.0 + 95.0 * abs(pa[1] - pb[1])
    return 1000.0 + 5.0 * abs(pa[2] - pb[2])


def _compute_raid_round_trip_s(
    origin_coords: str,
    target_coords: str,
    atk_ships: Mapping[str, int],
    researches: Mapping[str, int],
    speedup: float,
) -> float:
    """Compute realistic round-trip flight time (`>= 1800s`) from coordinates, slowest ship speed, and engines."""
    min_spd: Optional[float] = None
    for skey, cnt in atk_ships.items():
        if _safe_int(cnt, 0) <= 0:
            continue
        slug = SHIP_SLUG_BY_KEY.get(str(skey))
        stats = (game_data.SHIPS.get(slug or "") or {}).get("stats") or {}
        base_spd = float(stats.get("speed", 0) or 0)
        if base_spd <= 0.0:
            continue
        engine = str(stats.get("engine") or "").lower()
        eng_bonus = 0.0
        if "rocket" in engine:
            eng_bonus = 0.10 * min(100_000, max(0, _safe_int(researches.get("r4", 0), 0)))
        elif "diffusion" in engine:
            eng_bonus = 0.20 * min(100_000, max(0, _safe_int(researches.get("r5", 0), 0)))
        elif "warp" in engine:
            eng_bonus = 0.30 * min(100_000, max(0, _safe_int(researches.get("r6", 0), 0)))
        eff_spd = base_spd * (1.0 + eng_bonus)
        min_spd = eff_spd if min_spd is None else min(min_spd, eff_spd)
    spd = min_spd if (min_spd is not None and min_spd > 0.0) else 3000.0
    dist = _coords_distance(origin_coords, target_coords)
    clean_spd = _safe_float_any(speedup, 1.0)
    spd_fac = max(1.0, math.sqrt(max(1.0, clean_spd if clean_spd > 0.0 else 1.0)))
    raw_rt = (2.0 * 1800.0 * (dist / max(300.0, spd))) / spd_fac
    return max(_MIN_RAID_ROUND_TRIP_S, min(14400.0, raw_rt))


def _resources_from_dict(raw: Any) -> Resources:
    if not isinstance(raw, Mapping):
        return Resources()
    return Resources(
        pig_iron=max(0, _safe_int(raw.get("pig_iron", 0), 0)),
        metal=max(0, _safe_int(raw.get("metal", 0), 0)),
        kryptonite=max(0, _safe_int(raw.get("kryptonite", 0), 0)),
        spice=max(0, _safe_int(raw.get("spice", 0), 0)),
    )


def _clamp_finite(val: Any, default: float, lo: float, hi: float) -> float:
    parsed = _safe_float_any(val, default)
    if not math.isfinite(parsed):
        parsed = default
    return max(lo, min(hi, parsed))


def _safe_res_float(val: Any, cap: float = 1e300) -> float:
    if val is None or isinstance(val, (bool, Mapping, list, tuple, set, frozenset)):
        return 0.0
    if isinstance(val, int):
        if val <= 0:
            return 0.0
        if val.bit_length() >= 1000:
            return float(cap)
        try:
            f = float(val)
            return min(cap, max(0.0, f)) if math.isfinite(f) else float(cap)
        except OverflowError:
            return float(cap)
    parsed = _safe_float_any(val, 0.0)
    if not math.isfinite(parsed) or parsed <= 0.0:
        return 0.0
    return min(cap, parsed)


def _res_to_float_dict(res: Resources) -> dict[str, float]:
    return {
        "pig_iron": _safe_res_float(getattr(res, "pig_iron", 0)),
        "metal": _safe_res_float(getattr(res, "metal", 0)),
        "kryptonite": _safe_res_float(getattr(res, "kryptonite", 0)),
        "spice": _safe_res_float(getattr(res, "spice", 0)),
    }


def _dict_to_resources(raw: Mapping[str, float | int]) -> Resources:
    def _to_int_res(key: str) -> int:
        val = raw.get(key, 0.0)
        if val is None or isinstance(val, (bool, Mapping, list, tuple, set, frozenset)):
            return 0
        if isinstance(val, int):
            return max(0, min(10**18, val))
        parsed = _safe_float_any(val, 0.0)
        if not math.isfinite(parsed) or parsed <= 0.0:
            return 0
        parsed = min(1e18, parsed)
        return max(0, int(math.floor(parsed + 1e-9)))

    return Resources(
        pig_iron=_to_int_res("pig_iron"),
        metal=_to_int_res("metal"),
        kryptonite=_to_int_res("kryptonite"),
        spice=_to_int_res("spice"),
    )


def _building_time_scale(bid: int) -> float:
    if bid <= 6:
        return 1.51
    if bid <= 19:
        return 2.0
    if bid == 20:
        return 1.08
    return 1.44


def _build_safe_time_tables() -> tuple[dict[int, dict[int, float]], dict[str, dict[int, float]]]:
    b_tables: dict[int, dict[int, float]] = {}
    for bid, info in game_data.BUILDING_BY_ID.items():
        levels = info.get("levels") or {}
        scale = _building_time_scale(int(bid))
        table: dict[int, float] = {}
        prev = 0.0
        for lvl in sorted(int(k) for k in levels.keys()):
            raw_t = float(levels[lvl].get("base_time_s", 0.0) or 0.0)
            t = prev * scale if (lvl >= 2 and prev > 0.0 and raw_t < prev) else raw_t
            table[lvl] = t
            prev = t
        b_tables[int(bid)] = table

    r_tables: dict[str, dict[int, float]] = {}
    for rkey, info in game_data.RESEARCH_BY_ID.items():
        levels = info.get("levels") or {}
        table = {}
        prev = 0.0
        for lvl in sorted(int(k) for k in levels.keys()):
            raw_t = float(levels[lvl].get("base_time_s", 0.0) or 0.0)
            t = prev * 2.0 if (lvl >= 2 and prev > 0.0 and raw_t < prev) else raw_t
            table[lvl] = t
            prev = t
        r_tables[str(rkey)] = table
    return b_tables, r_tables


_SAFE_BUILDING_TIME_TABLE, _SAFE_RESEARCH_TIME_TABLE = _build_safe_time_tables()


def _build_cumulative_cost_tables() -> tuple[dict[int, dict[int, int]], dict[str, dict[int, int]]]:
    b_cum: dict[int, dict[int, int]] = {}
    for bid, info in game_data.BUILDING_BY_ID.items():
        levels = info.get("levels") or {}
        cum = {0: 0}
        running = 0
        for lvl in sorted(int(k) for k in levels.keys()):
            c = levels[lvl].get("cost") or {}
            running += sum(int(c.get(rk, 0) or 0) for rk in _RESOURCE_KEYS)
            cum[lvl] = running
        b_cum[int(bid)] = cum

    r_cum: dict[str, dict[int, int]] = {}
    for rkey, info in game_data.RESEARCH_BY_ID.items():
        levels = info.get("levels") or {}
        cum = {0: 0}
        running = 0
        for lvl in sorted(int(k) for k in levels.keys()):
            c = levels[lvl].get("cost") or {}
            running += sum(int(c.get(rk, 0) or 0) for rk in _RESOURCE_KEYS)
            cum[lvl] = running
        r_cum[str(rkey)] = cum
    return b_cum, r_cum


_CUM_BUILDING_COST_SUM, _CUM_RESEARCH_COST_SUM = _build_cumulative_cost_tables()


_CUM_RESEARCH_OVERFLOW_CACHE: dict[tuple[str, int], int] = {}


def _cum_building_res_sum(bid: int, level: int) -> int:
    lvl = max(0, _safe_int(level, 0))
    table = _CUM_BUILDING_COST_SUM.get(bid)
    if not table:
        return 0
    if lvl in table:
        return table[lvl]
    max_l = max(table.keys())
    return table[max_l]


def _cum_research_res_sum(rkey: str, level: int) -> int:
    lvl = min(1000, max(0, _safe_int(level, 0)))
    table = _CUM_RESEARCH_COST_SUM.get(rkey)
    if not table:
        return 0
    if lvl in table:
        return table[lvl]
    cache_key = (rkey, lvl)
    cached = _CUM_RESEARCH_OVERFLOW_CACHE.get(cache_key)
    if cached is not None:
        return cached
    info = game_data.RESEARCH_BY_ID.get(rkey)
    max_l = max(table.keys())
    total = table[max_l]
    if info:
        for l in range(max_l + 1, lvl + 1):
            c = _research_level_stats(info, l).get("cost") or {}
            total += sum(int(c.get(rk, 0) or 0) for rk in _RESOURCE_KEYS)
    _CUM_RESEARCH_OVERFLOW_CACHE[cache_key] = total
    return total


_PREREQ_CACHE: dict[tuple[str, str | int], tuple[tuple[str, str, int, str], ...]] = {}


def _fast_unmet_prereqs(
    category: str,
    slug_or_id: str | int,
    buildings: Mapping[str, int],
    researches: Mapping[str, int],
) -> list[dict[str, Any]]:
    cache_key = (category, slug_or_id)
    cached = _PREREQ_CACHE.get(cache_key)
    if cached is None:
        info = game_data.resolve_unit(category, slug_or_id)
        items: list[tuple[str, str, int, str]] = []
        if info:
            for req in info.get("requirements", []):
                rcat = str(req["category"])
                rslug = str(req["slug"])
                rlvl = int(req["level"])
                rname = str(req["name"])
                rkey = ""
                if rcat == "buildings":
                    rinfo = game_data.BUILDINGS.get(rslug)
                    if rinfo and rinfo.get("id") is not None:
                        rkey = f"b{int(rinfo['id'])}"
                elif rcat == "research":
                    rinfo = game_data.RESEARCHES.get(rslug)
                    if rinfo and rinfo.get("id"):
                        rkey = str(rinfo["id"])
                items.append((rcat, rkey or rslug, rlvl, rname))
        cached = tuple(items)
        _PREREQ_CACHE[cache_key] = cached

    missing: list[dict[str, Any]] = []
    for rcat, rkey, req_lvl, rname in cached:
        if rcat == "buildings":
            cur = max(0, _safe_int(buildings.get(rkey, 0), 0))
        elif rcat == "research":
            cur = max(0, _safe_int(researches.get(rkey, 0), 0))
        else:
            cur = 0
        if cur < req_lvl:
            missing.append(
                {
                    "category": rcat,
                    "slug": rkey,
                    "name": rname,
                    "required": req_lvl,
                    "current": cur,
                }
            )
    return missing


_MINE_BASE_PROD_CACHE: dict[tuple[int, int, int], float] = {}


def _fast_mine_prod(bid: int, level: int, temperature: int, mining_tech: int) -> float:
    if level <= 0:
        return 0.0
    t_key = _safe_int(temperature, 0) if bid == 4 else 0
    ck = (bid, level, t_key)
    base = _MINE_BASE_PROD_CACHE.get(ck)
    if base is None:
        base = game_data.mine_production_per_hour(
            bid, level, temperature=t_key, mining_tech_level=0
        )
        _MINE_BASE_PROD_CACHE[ck] = base
    safe_tech = max(0, min(100_000, _safe_int(mining_tech, 0)))
    if bid in (1, 3, 4) and safe_tech > 0:
        return base * (1.0 + game_data.MINING_TECH_BONUS_PER_LEVEL * safe_tech)
    return base


_BLAST_USED_CACHE: dict[int, float] = {}


def _fast_blast_pig_used(level: int) -> float:
    if level <= 0:
        return 0.0
    val = _BLAST_USED_CACHE.get(level)
    if val is None:
        val = game_data.blast_furnace_pig_iron_used(level)
        _BLAST_USED_CACHE[level] = val
    return val


def _safe_base_time_s(category: str, id_or_key: int | str, level: int) -> float:
    """Return monotonic `base_time_s`, repairing `game_data.py`'s `>= 1e14` truncation artifact."""
    lvl = _safe_int(level, 0)
    if lvl <= 0:
        return 0.0
    if category == "buildings":
        bid = _safe_int(id_or_key, 0)
        table = _SAFE_BUILDING_TIME_TABLE.get(bid, {})
        if lvl in table:
            return table[lvl]
        if not table:
            return 0.0
        max_lvl = max(table.keys())
        val = table[max_lvl] * (_building_time_scale(bid) ** min(600, max(0, lvl - max_lvl)))
        return min(1e200, val) if math.isfinite(val) else 1e200
    if category == "research":
        rkey = _safe_action_str(id_or_key)
        table = _SAFE_RESEARCH_TIME_TABLE.get(rkey, {})
        if lvl in table:
            return table[lvl]
        if not table:
            return 0.0
        max_lvl = max(table.keys())
        val = table[max_lvl] * (2.0 ** min(60, max(0, lvl - max_lvl)))
        return min(1e200, val) if math.isfinite(val) else 1e200
    return 0.0


def _building_duration_s(
    bid: int,
    target_level: int,
    *,
    dev_level: int,
    micro_level: int,
    speedup: float,
) -> float:
    base_t = _safe_base_time_s("buildings", bid, target_level)
    dev_s = min(1_000_000, max(0, _safe_int(dev_level, 0)))
    micro_s = min(60, max(0, _safe_int(micro_level, 0)))
    spd = _clamp_finite(speedup, 1.0, 1e-6, 1e9)
    divisor = max(1.0, float(dev_s + 1) * (2.0 ** micro_s) * spd)
    out = base_t / divisor
    return out if math.isfinite(out) and out >= 0.0 else 0.0


def _research_duration_s(
    rkey: str,
    target_level: int,
    *,
    lab_level: int,
    speedup: float,
) -> float:
    base_t = _safe_base_time_s("research", rkey, target_level)
    # Microsystem accelerator (`b17`) does not speed up research.
    lab_s = min(1_000_000, max(0, _safe_int(lab_level, 0)))
    spd = _clamp_finite(speedup, 1.0, 1e-6, 1e9)
    divisor = max(1.0, float(lab_s + 1) * spd)
    out = base_t / divisor
    return out if math.isfinite(out) and out >= 0.0 else 0.0


def _shipyard_duration_s(
    base_build_time_s: float,
    quantity: int,
    *,
    wf_level: int,
    micro_level: int,
    speedup: float,
) -> float:
    wf_s = min(1_000_000, max(1, _safe_int(wf_level, 1)))
    micro_s = min(60, max(0, _safe_int(micro_level, 0)))
    spd = _clamp_finite(speedup, 1.0, 1e-6, 1e9)
    qty = min(1_000_000_000, max(0, _safe_int(quantity, 0)))
    base_bt = _clamp_finite(base_build_time_s, 0.0, 0.0, 1e12)
    divisor = max(1.0, float(wf_s) * (2.0 ** micro_s) * spd)
    qty_f = 1e200 if (isinstance(qty, int) and qty.bit_length() > 666) else float(qty)
    out = qty_f * base_bt / divisor
    return out if math.isfinite(out) and out >= 0.0 else 0.0


def _parse_timer_seconds(text: str) -> Optional[float]:
    m = _TIMER_RE.search(_safe_action_str(text))
    if not m:
        return None
    h_str, m_str, s_str = m.group(1), m.group(2), m.group(3)
    if not (
        h_str.isascii()
        and h_str.isdigit()
        and m_str.isascii()
        and m_str.isdigit()
        and s_str.isascii()
        and s_str.isdigit()
    ):
        return None
    h_trim = h_str.lstrip("0") or "0"
    h = 10**12 if len(h_trim) > 12 else min(10**12, int(h_trim))
    return float(h * 3600 + int(m_str) * 60 + int(s_str))


def _parse_coords_tuple(coords: str) -> Optional[tuple[int, int, int]]:
    m = _GAL_SYS_RE.search(_safe_action_str(coords))
    if not m or m.group(3) is None:
        return None
    g1, g2, g3 = m.group(1), m.group(2), m.group(3)
    if not (g1.isascii() and g1.isdigit() and g2.isascii() and g2.isdigit() and g3.isascii() and g3.isdigit()):
        return None
    return int(g1), int(g2), int(g3)


def _resolve_raid_target_status_color(raw: Optional[Mapping[str, Any]]) -> str:
    """Resolve raid target status color, honoring explicit `is_inactive=False` when color is omitted."""
    if not raw:
        return "yellow"
    explicit = _safe_action_str(raw.get("status_color")).strip().lower()
    if explicit:
        return explicit
    if raw.get("is_spacia"):
        return "purple"
    if raw.get("is_inactive") is False:
        return "light_red"
    return "yellow"


def _parse_gal_sys(coords: str) -> tuple[int, int]:
    m = _GAL_SYS_RE.search(_safe_action_str(coords))
    if not m:
        return 1, 1
    g1, g2 = m.group(1), m.group(2)
    if not (g1.isascii() and g1.isdigit() and g2.isascii() and g2.isdigit()):
        return 1, 1
    return int(g1), int(g2)


def _resolve_research_key(target_id: Optional[int | str]) -> Optional[str]:
    if target_id is None or isinstance(target_id, bool):
        return None
    if isinstance(target_id, int):
        if not (0 <= target_id <= 10_000):
            return None
        rk = f"r{target_id}"
        return rk if rk in game_data.RESEARCH_BY_ID else None
    raw = _safe_action_str(target_id).strip()
    if not raw:
        return None
    if raw.isascii() and raw.isdigit():
        if len(raw) > 10:
            return None
        rk = f"r{int(raw)}"
        return rk if rk in game_data.RESEARCH_BY_ID else None
    if raw.lower() in game_data.RESEARCH_BY_ID:
        return raw.lower()
    info = game_data.resolve_unit("research", raw)
    if info and info.get("id") and str(info["id"]) in game_data.RESEARCH_BY_ID:
        return str(info["id"])
    return None


def _resolve_ship_key_and_slug(target_id: Optional[int | str]) -> tuple[Optional[str], Optional[str]]:
    if target_id is None or isinstance(target_id, bool):
        return None, None
    if isinstance(target_id, int):
        if not (0 <= target_id <= 10_000):
            return None, None
        skey = f"s{target_id}"
        slug = SHIP_SLUG_BY_KEY.get(skey)
        return (skey, slug) if slug and slug in game_data.SHIPS else (None, None)
    raw = _safe_action_str(target_id).strip()
    if not raw:
        return None, None
    if raw.isascii() and raw.isdigit():
        if len(raw) > 10:
            return None, None
        skey = f"s{int(raw)}"
        slug = SHIP_SLUG_BY_KEY.get(skey)
        return (skey, slug) if slug and slug in game_data.SHIPS else (None, None)
    low = raw.lower()
    if low in SHIP_SLUG_BY_KEY:
        slug = SHIP_SLUG_BY_KEY[low]
        return (low, slug) if slug in game_data.SHIPS else (None, None)
    info = game_data.resolve_unit("ships", raw)
    if info:
        slug = str(info.get("slug") or "")
        skey = _SHIP_KEY_BY_SLUG.get(slug)
        if skey and slug in game_data.SHIPS:
            return skey, slug
    return None, None


def _resolve_defense_key_and_slug(target_id: Optional[int | str]) -> tuple[Optional[str], Optional[str]]:
    if target_id is None or isinstance(target_id, bool):
        return None, None
    if isinstance(target_id, int):
        if not (0 <= target_id <= 10_000):
            return None, None
        dkey = f"d{target_id}"
        slug = DEFENSE_SLUG_BY_KEY.get(dkey)
        return (dkey, slug) if slug and slug in game_data.DEFENSES else (None, None)
    raw = _safe_action_str(target_id).strip()
    if not raw:
        return None, None
    if raw.isascii() and raw.isdigit():
        if len(raw) > 10:
            return None, None
        dkey = f"d{int(raw)}"
        slug = DEFENSE_SLUG_BY_KEY.get(dkey)
        return (dkey, slug) if slug and slug in game_data.DEFENSES else (None, None)
    low = raw.lower()
    if low in DEFENSE_SLUG_BY_KEY:
        slug = DEFENSE_SLUG_BY_KEY[low]
        return (low, slug) if slug in game_data.DEFENSES else (None, None)
    info = game_data.resolve_unit("defense", raw)
    if info:
        slug = str(info.get("slug") or "")
        dkey = _DEFENSE_KEY_BY_SLUG.get(slug)
        if dkey and slug in game_data.DEFENSES:
            return dkey, slug
    return None, None


# ---------------------------------------------------------------------------
# F2 Public Dataclasses & Internal Mutable Simulation Snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimAction:
    """Single empire simulation / GA chromosome gene action."""

    kind: str  # "BUILD" | "RESEARCH" | "BUILD_SHIP" | "BUILD_DEFENSE" | "THROTTLE_BUILDING" | "ATTACK" | "SCAN_GALAXY" | "CLAIM_TUTORIAL"
    planet_id: int
    target_id: Optional[int | str] = None
    quantity: int = 1
    target_coords: str = ""
    reason: str = ""


CandidateAction = SimAction


@dataclass(frozen=True)
class SimStepOutcome:
    """Execution record and timing outcome for one simulated action step."""

    index: int
    action: SimAction
    valid: bool
    rejection_reason: str = ""
    planet_id: int = 0
    target_name: str = ""
    target_level: Optional[int] = None
    cost: Resources = Resources()
    wait_s: float = 0.0
    duration_s: float = 0.0
    start_t: float = 0.0
    finish_t: float = 0.0
    executable_at_t0: bool = False
    raid_loot_spice_eq: float = 0.0


@dataclass(frozen=True)
class EmpireMetrics:
    """Aggregate economy, points, fleet, raid, and health metrics at a simulation snapshot."""

    points: float
    buildings_points: float
    research_points: float
    fleet_defense_points: float
    hourly_pig_iron_net: float
    hourly_metal_net: float
    hourly_kryptonite_net: float
    hourly_spice_net: float
    hourly_spice_eq: float
    hourly_points_steady: float
    fleet_power: float
    fleet_cargo_capacity: int
    potential_raid_spice_eq: float
    simulated_raid_loot_spice_eq: float
    unspent_spice_eq: float
    energy_deficit_planets: int
    total_energy_net: int
    composite_score: float


@dataclass(frozen=True)
class SimulationResult:
    """Result of simulating an action sequence forward from a `GameState`."""

    initial_metrics: EmpireMetrics
    final_metrics: EmpireMetrics
    final_state: GameState
    step_outcomes: tuple[SimStepOutcome, ...]
    valid_steps: int
    rejected_steps: int
    elapsed_seconds: float
    composite_growth_score: float
    points_delta: float
    hourly_spice_eq_delta: float
    raid_fleet_output_delta: float
    nn_terminal_value: float = 0.0
    fitness: float = 0.0
    energy_deficit_hours: float = 0.0
    overflow_penalty: float = 0.0
    pig_starvation_penalty: float = 0.0
    tutorial_tasks_advanced: int = 0


@dataclass
class _SimBuildJob:
    building_id: int
    target_level: int
    start_t: float
    finish_t: float
    duration_s: float


@dataclass
class _SimResearchJob:
    research_key: str
    target_level: int
    planet_id: int
    start_t: float
    finish_t: float


@dataclass
class _SimShipJob:
    category: str  # "ship" | "defense"
    key: str       # "s1" / "d1"
    slug: str
    quantity: int
    start_t: float
    finish_t: float
    base_build_time_s: float


@dataclass
class _SimFleetJob:
    planet_id: int
    target_coords: str
    loot: Resources
    loot_spice_eq: float
    start_t: float
    finish_t: float
    won: bool = True
    outcome: str = "win"
    is_repeat: bool = False


class _SimPlanet:
    """Mutable fast snapshot of one `PlanetState` for non-mutating rollout simulation."""

    __slots__ = (
        "planet_id",
        "name",
        "coords",
        "planet_type",
        "temperature",
        "fields_used",
        "fields_max",
        "res_f",
        "storage_cap",
        "non_b6_energy_raw",
        "b6_energy_raw",
        "energy_used",
        "buildings",
        "building_names",
        "ships",
        "defense",
        "economy_rates",
        "build_queue",
        "shipyard_queue",
        "_source",
    )

    def __init__(
        self,
        *,
        planet_id: int,
        name: str,
        coords: str,
        planet_type: str,
        temperature: int,
        fields_used: int,
        fields_max: int,
        res_f: dict[str, float],
        storage_cap: dict[str, int],
        non_b6_energy_raw: float,
        b6_energy_raw: float,
        energy_used: int,
        buildings: dict[str, int],
        building_names: dict[str, str],
        ships: dict[str, int],
        defense: dict[str, int],
        economy_rates: dict[str, int],
        build_queue: list[_SimBuildJob],
        shipyard_queue: list[_SimShipJob],
        source: Optional[PlanetState],
    ) -> None:
        self.planet_id = planet_id
        self.name = name
        self.coords = coords
        self.planet_type = normalize_planet_type(planet_type)
        self.temperature = temperature
        self.fields_used = fields_used
        self.fields_max = fields_max
        self.res_f = res_f
        self.storage_cap = storage_cap
        self.non_b6_energy_raw = non_b6_energy_raw
        self.b6_energy_raw = b6_energy_raw
        self.energy_used = energy_used
        self.buildings = buildings
        self.building_names = building_names
        self.ships = ships
        self.defense = defense
        self.economy_rates = economy_rates
        self.build_queue = build_queue
        self.shipyard_queue = shipyard_queue
        self._source = source

    @classmethod
    def from_planet_state(cls, planet: PlanetState, researches: Mapping[str, int]) -> _SimPlanet:
        buildings = {_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in (planet.buildings or {}).items()}
        building_names = {_safe_action_str(k): _safe_action_str(v) for k, v in (planet.building_names or {}).items()}
        ships = {_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in (planet.ships or {}).items()}
        defense = {_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in (planet.defense or {}).items()}
        economy_rates = {
            _safe_action_str(k): max(0, min(100, _safe_int(v, 100)))
            for k, v in (planet.economy_rates or {}).items()
            if v is not None
        }

        res_f = _res_to_float_dict(planet.resources)
        def _safe_cap(val: Any) -> int:
            parsed = _safe_int(val, 100_000)
            if parsed <= 0:
                return 100_000
            return min(10**18, parsed)

        storage_cap = {
            "pig_iron": _safe_cap(planet.storage.pig_iron),
            "metal": _safe_cap(planet.storage.metal),
            "kryptonite": _safe_cap(planet.storage.kryptonite),
            "spice": _safe_cap(planet.storage.spice),
        }

        r2_0 = min(100_000, max(0, _safe_int(researches.get("r2", 0), 0)))
        r2_mult = 1.0 + 0.01 * r2_0
        e_prod_val = _clamp_finite(getattr(planet.energy, "produced", 0), 0.0, 0.0, 1e15)
        e_used_val = max(0, min(10**15, _safe_int(getattr(planet.energy, "used", 0), 0)))
        total_raw = e_prod_val / r2_mult if r2_mult > 0 else e_prod_val
        b6_lvl = max(0, _safe_int(buildings.get("b6", 0), 0))
        b5_lvl = max(0, _safe_int(buildings.get("b5", 0), 0))
        s9_cnt = _safe_res_float(ships.get("s9", 0), 1e15)
        temp_c = _safe_int(planet.temperature, 0)
        b5_raw_full = float(game_data.building_level_stats(5, b5_lvl).get("energy", 0) or 0) if b5_lvl > 0 else 0.0
        if s9_cnt > 0.0:
            s9_tmap = game_data.SHIPS.get("solar-cells", {}).get("solar_cell_energy_by_temp") or {}
            s9_raw = game_data.interpolate_temp_value(s9_tmap, temp_c) * s9_cnt
        else:
            s9_raw = 0.0
        expected_non_b6 = b5_raw_full + s9_raw

        if b6_lvl > 0:
            b6_full = float(game_data.building_level_stats(6, b6_lvl).get("energy", 0) or 0)
            pct5 = max(0, min(100, _safe_int(economy_rates.get("resource5", 100), 100))) / 100.0
            b6_raw_full = b6_full * pct5
            if res_f["spice"] > 1e-6:
                if e_prod_val == 0.0 and expected_non_b6 == 0.0:
                    b6_energy_raw = b6_raw_full
                    non_b6_energy_raw = 0.0
                elif e_prod_val == 0.0 and expected_non_b6 > 0.0:
                    b6_energy_raw = b6_raw_full
                    non_b6_energy_raw = expected_non_b6
                else:
                    b6_energy_raw = min(total_raw, b6_raw_full)
                    non_b6_energy_raw = max(0.0, total_raw - b6_energy_raw)
            else:
                # At spice == 0, live `planet.energy.produced` already excludes collapsed `b6`
                b6_energy_raw = b6_raw_full
                if expected_non_b6 > 0.0:
                    if total_raw <= 1e-9:
                        non_b6_energy_raw = 0.0
                    elif total_raw <= expected_non_b6 + 1e-6:
                        non_b6_energy_raw = min(total_raw, expected_non_b6)
                    else:
                        non_b6_energy_raw = max(expected_non_b6, total_raw - b6_raw_full)
                else:
                    non_b6_energy_raw = max(0.0, total_raw - b6_raw_full)
        else:
            b6_energy_raw = 0.0
            non_b6_energy_raw = max(0.0, total_raw)

        return cls(
            planet_id=_safe_int(planet.planet_id, 0),
            name=_safe_action_str(planet.name),
            coords=_safe_action_str(planet.coords),
            planet_type=normalize_planet_type(planet),
            temperature=temp_c,
            fields_used=max(0, _safe_int(planet.fields_used, 0)),
            fields_max=max(0, _safe_int(planet.fields_max, 0)),
            res_f=res_f,
            storage_cap=storage_cap,
            non_b6_energy_raw=non_b6_energy_raw,
            b6_energy_raw=b6_energy_raw,
            energy_used=e_used_val,
            buildings=buildings,
            building_names=building_names,
            ships=ships,
            defense=defense,
            economy_rates=economy_rates,
            build_queue=[],
            shipyard_queue=[],
            source=planet,
        )

    def clone(self) -> _SimPlanet:
        return _SimPlanet(
            planet_id=self.planet_id,
            name=self.name,
            coords=self.coords,
            planet_type=self.planet_type,
            temperature=self.temperature,
            fields_used=self.fields_used,
            fields_max=self.fields_max,
            res_f=dict(self.res_f),
            storage_cap=dict(self.storage_cap),
            non_b6_energy_raw=self.non_b6_energy_raw,
            b6_energy_raw=self.b6_energy_raw,
            energy_used=self.energy_used,
            buildings=dict(self.buildings),
            building_names=dict(self.building_names),
            ships=dict(self.ships),
            defense=dict(self.defense),
            economy_rates=dict(self.economy_rates),
            build_queue=[
                _SimBuildJob(j.building_id, j.target_level, j.start_t, j.finish_t, j.duration_s)
                for j in self.build_queue
            ],
            shipyard_queue=[
                _SimShipJob(j.category, j.key, j.slug, j.quantity, j.start_t, j.finish_t, j.base_build_time_s)
                for j in self.shipyard_queue
            ],
            source=self._source,
        )

    def building_level(self, bid: int) -> int:
        return max(0, _safe_int(self.buildings.get(f"b{bid}", 0), 0))

    def effective_building_level(self, bid: int) -> int:
        lvl = self.building_level(bid)
        for job in self.build_queue:
            if job.building_id == bid:
                lvl = max(lvl, job.target_level)
        return lvl

    def projected_buildings(self) -> dict[str, int]:
        out = dict(self.buildings)
        for job in self.build_queue:
            key = f"b{job.building_id}"
            out[key] = max(out.get(key, 0), job.target_level)
        return out

    def max_build_slots(self, specs: Mapping[str, int]) -> int:
        if is_asteroid_planet(self):
            return 1
        specs_map = specs if isinstance(specs, Mapping) else {}
        if _safe_int(specs_map.get("construction_commander", 0), 0) > 0:
            return 6
        return 5


def _resolve_reference_now_ts(
    state: GameState,
    resolved_stats: Mapping[str, Any],
    now_ts: Optional[float] = None,
) -> float:
    """Resolve consistent reference clock shared by `_SimState` and `_materialize_planned_actions`."""
    if now_ts is not None:
        explicit_ts = _safe_float_any(now_ts, 0.0)
        if explicit_ts > 0.0:
            return explicit_ts
    state_ts = _safe_float_any(getattr(state, "timestamp", 0.0), 0.0)
    if state_ts > 0.0:
        return state_ts
    hist_rows = resolved_stats.get("history")
    max_hist_ts = 0.0
    if isinstance(hist_rows, Sequence) and not isinstance(hist_rows, (str, bytes)):
        for row in hist_rows:
            if not isinstance(row, Mapping):
                continue
            raw_ts = (
                row.get("attacked_ts")
                if row.get("attacked_ts") is not None
                else (row.get("ts") if row.get("ts") is not None else row.get("timestamp"))
            )
            ts_val = _safe_float_any(raw_ts, 0.0)
            if ts_val > max_hist_ts:
                max_hist_ts = ts_val
    wall_now = time.time()
    if max_hist_ts > 0.0 and max_hist_ts < wall_now - 365.0 * 86400.0:
        return max_hist_ts
    return wall_now


def _extract_active_throttle_cooldowns(
    throttle_cooldowns: Optional[Mapping[Any, float]],
    ref_now: float,
) -> dict[Any, float]:
    out: dict[Any, float] = {}
    if not isinstance(throttle_cooldowns, Mapping):
        return out
    for raw_k, raw_ts in throttle_cooldowns.items():
        ts_val = _safe_float_any(raw_ts, 0.0)
        if ts_val <= 0.0:
            continue
        if (ref_now - ts_val) < 300.0:
            if isinstance(raw_k, tuple) and len(raw_k) == 2:
                pid = _safe_int(raw_k[0], -1)
                bid = _safe_int(raw_k[1], -1)
                if pid >= 0 and bid > 0:
                    out[(pid, bid)] = ts_val
            else:
                pid = _safe_int(raw_k, -1)
                if pid >= 0:
                    out[pid] = ts_val
    return out


def _extract_recent_scanned_systems(
    scanned_systems: Optional[Mapping[str, float]],
    ref_now: float,
) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    if not isinstance(scanned_systems, Mapping):
        return out
    for raw_sys, raw_ts in scanned_systems.items():
        ts_val = _safe_float_any(raw_ts, 0.0)
        if ts_val <= 0.0:
            continue
        if (ref_now - ts_val) < 1800.0:
            parts = _safe_action_str(raw_sys).strip().split(":")
            if len(parts) >= 2:
                g = _safe_int(parts[0], 0)
                s = _safe_int(parts[1], 0)
                if g > 0 and s > 0:
                    out.add((g, s))
    return out


def _seed_bash_tracker_from_history(
    resolved_stats: Mapping[str, Any],
    ref_now_ts: float,
) -> BashTracker:
    """Seed `BashTracker` in ascending timestamp order, ignoring non-positive or >60s future-skewed rows."""
    tracker = BashTracker()
    hist_rows = resolved_stats.get("history")
    if not isinstance(hist_rows, Sequence) or isinstance(hist_rows, (str, bytes)):
        return tracker
    cutoff = ref_now_ts - 86400.0
    skew_max = ref_now_ts + 60.0
    entries: list[tuple[float, int, str]] = []
    for orig_idx, row in enumerate(hist_rows):
        if not isinstance(row, Mapping):
            continue
        if bool(row.get("is_exempt", False)):
            continue
        raw_t_coords = _safe_action_str(row.get("target_coords") or row.get("coords")).strip()
        if not raw_t_coords:
            continue
        t_coords = normalize_coords(raw_t_coords)
        raw_ts = (
            row.get("attacked_ts")
            if row.get("attacked_ts") is not None
            else (row.get("ts") if row.get("ts") is not None else row.get("timestamp"))
        )
        row_ts = _safe_float_any(raw_ts, 0.0)
        if row_ts <= 0.0:
            continue
        if row_ts > skew_max:
            continue
        if row_ts > ref_now_ts:
            row_ts = ref_now_ts
        if row_ts < cutoff:
            continue
        entries.append((row_ts, orig_idx, t_coords))
    entries.sort(key=lambda item: (item[0], item[1]))
    for row_ts, _, t_coords in entries:
        tracker.record_attack(t_coords, timestamp=row_ts)
    return tracker


class _SimState:
    """Mutable empire simulation state decoupled from caller `GameState`."""

    __slots__ = (
        "clock_s",
        "sim_base_ts",
        "speedup_factor",
        "planets",
        "planet_by_id",
        "active_planet_id",
        "researches",
        "research_names",
        "specs",
        "research_job",
        "fleet_jobs",
        "fleet_busy_until",
        "target_raid_cooldown_until",
        "bash_tracker",
        "raid_targets_info",
        "raid_target_res",
        "depleted_targets",
        "spied_targets",
        "raids_loot_spice_eq",
        "unique_raids_loot_spice_eq",
        "raid_risk_penalty",
        "attack_history_stats",
        "scanned_systems",
        "throttle_cooldowns",
        "tutorial_can_complete",
        "tutorial_reward",
        "tutorial_claimed",
        "tutorial_tasks_advanced",
        "allow_asteroid_research_lab",
        "energy_deficit_hours",
        "overflow_penalty",
        "pig_starvation_penalty",
        "_source_state",
    )

    def __init__(
        self,
        *,
        clock_s: float,
        sim_base_ts: float,
        speedup_factor: float,
        planets: list[_SimPlanet],
        active_planet_id: Optional[int],
        researches: dict[str, int],
        research_names: dict[str, str],
        specs: dict[str, int],
        research_job: Optional[_SimResearchJob],
        fleet_jobs: list[_SimFleetJob],
        fleet_busy_until: dict[int, float],
        target_raid_cooldown_until: dict[str, float],
        bash_tracker: BashTracker,
        raid_targets_info: dict[str, dict[str, Any]],
        raid_target_res: dict[str, Resources],
        depleted_targets: Optional[set[str]] = None,
        spied_targets: Optional[set[str]] = None,
        raids_loot_spice_eq: float,
        unique_raids_loot_spice_eq: float = 0.0,
        raid_risk_penalty: float,
        attack_history_stats: dict[str, Any],
        scanned_systems: set[tuple[int, int]],
        throttle_cooldowns: Optional[dict[Any, float]] = None,
        tutorial_can_complete: bool,
        tutorial_reward: Resources,
        tutorial_claimed: bool,
        tutorial_tasks_advanced: int,
        allow_asteroid_research_lab: bool,
        energy_deficit_hours: float,
        overflow_penalty: float,
        pig_starvation_penalty: float,
        source_state: GameState,
    ) -> None:
        self.clock_s = clock_s
        self.sim_base_ts = sim_base_ts
        self.speedup_factor = speedup_factor
        self.planets = planets
        self.planet_by_id = {p.planet_id: p for p in planets}
        self.active_planet_id = active_planet_id
        self.researches = researches
        self.research_names = research_names
        self.specs = specs
        self.research_job = research_job
        self.fleet_jobs = fleet_jobs
        self.fleet_busy_until = fleet_busy_until
        self.target_raid_cooldown_until = target_raid_cooldown_until
        self.bash_tracker = bash_tracker
        self.raid_targets_info = raid_targets_info
        self.raid_target_res = raid_target_res
        self.depleted_targets = depleted_targets if depleted_targets is not None else set()
        self.spied_targets = spied_targets if spied_targets is not None else set()
        self.raids_loot_spice_eq = raids_loot_spice_eq
        self.unique_raids_loot_spice_eq = unique_raids_loot_spice_eq
        self.raid_risk_penalty = raid_risk_penalty
        self.attack_history_stats = attack_history_stats
        self.scanned_systems = scanned_systems
        self.throttle_cooldowns = throttle_cooldowns if throttle_cooldowns is not None else {}
        self.tutorial_can_complete = tutorial_can_complete
        self.tutorial_reward = tutorial_reward
        self.tutorial_claimed = tutorial_claimed
        self.tutorial_tasks_advanced = tutorial_tasks_advanced
        self.allow_asteroid_research_lab = allow_asteroid_research_lab
        self.energy_deficit_hours = energy_deficit_hours
        self.overflow_penalty = overflow_penalty
        self.pig_starvation_penalty = pig_starvation_penalty
        self._source_state = source_state

    @classmethod
    def from_game_state(
        cls,
        state: GameState,
        *,
        allow_asteroid_research_lab: bool = False,
        raid_stats: Optional[Mapping[str, Any]] = None,
        raid_history: Optional[Sequence[Mapping[str, Any]]] = None,
        attack_history_path: Optional[Path | str] = None,
        resolved_attack_stats: Optional[dict[str, Any]] = None,
        now_ts: Optional[float] = None,
        scanned_systems: Optional[Mapping[str, float]] = None,
        throttle_cooldowns: Optional[Mapping[Any, float]] = None,
    ) -> _SimState:
        speedup = _safe_float_any(
            state.speedup_factor if state.speedup_factor is not None else 1.0,
            1.0,
        )
        if not math.isfinite(speedup) or speedup <= 0.0:
            speedup = 1.0
        speedup = max(0.1, min(1_000_000.0, speedup))
        researches = {
            _safe_action_str(k): max(0, _safe_int(v, 0))
            for k, v in (state.researches.items() if isinstance(state.researches, Mapping) else ())
        }
        research_names = {
            _safe_action_str(k): _safe_action_str(v)
            for k, v in (state.research_names.items() if isinstance(state.research_names, Mapping) else ())
        }
        specs = {
            _safe_action_str(k): max(0, _safe_int(v, 0))
            for k, v in (state.specs.items() if isinstance(state.specs, Mapping) else ())
        }

        attack_stats = (
            resolved_attack_stats
            if resolved_attack_stats is not None
            else _resolve_complete_attack_history(
                state,
                raid_stats=raid_stats,
                raid_history=raid_history,
                attack_history_path=attack_history_path,
            )
        )
        sim_base_ts = _resolve_reference_now_ts(state, attack_stats, now_ts=now_ts)
        recent_scans = _extract_recent_scanned_systems(scanned_systems, sim_base_ts)
        active_t_cds = _extract_active_throttle_cooldowns(throttle_cooldowns, sim_base_ts)

        planets = [_SimPlanet.from_planet_state(p, researches) for p in state.planets]
        planet_by_id = {p.planet_id: p for p in planets}
        active_p = state.active_planet()
        active_id = active_p.planet_id if active_p is not None else (planets[0].planet_id if planets else None)

        # Initialize active construction & research queues on `active_id`
        research_job: Optional[_SimResearchJob] = None
        if active_id is not None and active_id in planet_by_id:
            ap = planet_by_id[active_id]
            sim_b_levels: dict[int, int] = {}
            for k, v in ap.buildings.items():
                bid = _parse_building_key_id(k)
                if bid is not None:
                    sim_b_levels[bid] = max(0, _safe_int(v, 0))
            prev_finish = 0.0
            building_idx = 0
            for qjob in (state.active_queues or ()):
                if qjob.category == "building":
                    cur_b_map = {f"b{k}": v for k, v in sim_b_levels.items()}
                    resolved_q = _resolve_queue_building(qjob, cur_b_map)
                    if resolved_q is None:
                        continue
                    bid, tgt_l = resolved_q
                    rem_t = _parse_timer_seconds(qjob.remaining_text)
                    if rem_t is None:
                        rem_t = _parse_timer_seconds(qjob.name)
                    full_dur = _building_duration_s(
                        bid,
                        tgt_l,
                        dev_level=sim_b_levels.get(7, 0),
                        micro_level=sim_b_levels.get(17, 0),
                        speedup=speedup,
                    )
                    dur = rem_t if rem_t is not None else (0.5 * full_dur if building_idx == 0 else full_dur)
                    building_idx += 1
                    start_t = prev_finish
                    finish_t = start_t + max(1.0, dur)
                    ap.build_queue.append(
                        _SimBuildJob(
                            building_id=bid,
                            target_level=tgt_l,
                            start_t=start_t,
                            finish_t=finish_t,
                            duration_s=max(1.0, dur),
                        )
                    )
                    sim_b_levels[bid] = tgt_l
                    prev_finish = finish_t
                elif qjob.category == "research" and research_job is None:
                    clean_r = re.sub(r"\(.*?\)", "", _safe_action_str(qjob.name))
                    clean_r = _TIMER_RE.sub("", clean_r).strip()
                    rkey = _resolve_research_key(clean_r)
                    if rkey is None:
                        act_offer = next((o for o in state.research_offers if o.active), None)
                        if act_offer is not None:
                            rkey = _safe_action_str(act_offer.research_key) or None
                    if rkey is not None:
                        cur_r_lvl = max(0, _safe_int(researches.get(rkey, 0), 0))
                        q_r_tgt = game_data.safe_optional_int(qjob.target_level, None)
                        tgt_l = _safe_level(
                            q_r_tgt if (q_r_tgt is not None and q_r_tgt > 0) else (cur_r_lvl + 1),
                            default=cur_r_lvl + 1,
                        )
                        rem_t = _parse_timer_seconds(qjob.remaining_text)
                        if rem_t is None:
                            rem_t = _parse_timer_seconds(qjob.name)
                        dur = rem_t if rem_t is not None else 0.5 * _research_duration_s(
                            rkey, tgt_l, lab_level=ap.building_level(8), speedup=speedup
                        )
                        research_job = _SimResearchJob(
                            research_key=rkey,
                            target_level=tgt_l,
                            planet_id=ap.planet_id,
                            start_t=0.0,
                            finish_t=max(1.0, dur),
                        )

        if research_job is None and active_id is not None and active_id in planet_by_id:
            act_offer = next((o for o in (state.research_offers or ()) if o.active), None)
            if act_offer is not None:
                ap = planet_by_id[active_id]
                rkey = _safe_action_str(act_offer.research_key)
                cur_r_lvl = max(0, _safe_int(researches.get(rkey, 0), 0))
                o_r_tgt = game_data.safe_optional_int(act_offer.target_level, None)
                tgt_l = _safe_level(
                    o_r_tgt if (o_r_tgt is not None and o_r_tgt > 0) else (cur_r_lvl + 1),
                    default=cur_r_lvl + 1,
                )
                dur = 0.5 * _research_duration_s(rkey, tgt_l, lab_level=ap.building_level(8), speedup=speedup)
                research_job = _SimResearchJob(
                    research_key=rkey,
                    target_level=tgt_l,
                    planet_id=ap.planet_id,
                    start_t=0.0,
                    finish_t=max(1.0, dur),
                )

        raid_targets_info: dict[str, dict[str, Any]] = {}
        raid_target_res: dict[str, Resources] = {}
        for raw in state.raid_targets or ():
            if not isinstance(raw, Mapping):
                continue
            raw_c = _safe_action_str(raw.get("coords") or raw.get("target_coords")).strip()
            if not raw_c:
                continue
            coords = normalize_coords(raw_c)
            res_map = raw.get("resources") if isinstance(raw.get("resources"), Mapping) else raw
            res_obj = Resources(
                pig_iron=max(0, _safe_int(res_map.get("pig_iron", 0), 0)),
                metal=max(0, _safe_int(res_map.get("metal", 0), 0)),
                kryptonite=max(0, _safe_int(res_map.get("kryptonite", 0), 0)),
                spice=max(0, _safe_int(res_map.get("spice", 0), 0)),
            )
            raw_fleet = raw.get("fleet") if isinstance(raw.get("fleet"), Mapping) else {}
            raw_defense = raw.get("defense") if isinstance(raw.get("defense"), Mapping) else {}
            raid_targets_info[coords] = {
                "coords": coords,
                "owner": _safe_action_str(raw.get("owner")),
                "planet_name": _safe_action_str(raw.get("planet_name")),
                "is_spacia": bool(raw.get("is_spacia", False)),
                "status_color": _resolve_raid_target_status_color(raw),
                "fleet": {_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in raw_fleet.items()},
                "defense": {_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in raw_defense.items()},
                "dug_in": raw.get("dug_in"),
                "has_spy_intel": bool(raw.get("has_spy_intel")),
                "last_spied_ts": _safe_float_any(raw.get("last_spied_ts"), 0.0),
                "last_scanned_ts": _safe_float_any(raw.get("last_scanned_ts"), 0.0),
            }
            raid_target_res[coords] = res_obj

        bash_tracker = _seed_bash_tracker_from_history(attack_stats, sim_base_ts)

        tut_can = bool(state.tutorial and state.tutorial.can_complete)
        tut_rew = _parse_cost_from_text(_safe_action_str(state.tutorial.reward_text)) if state.tutorial and state.tutorial.reward_text else Resources()

        return cls(
            clock_s=0.0,
            sim_base_ts=sim_base_ts,
            speedup_factor=speedup,
            planets=planets,
            active_planet_id=active_id,
            researches=researches,
            research_names=research_names,
            specs=specs,
            research_job=research_job,
            fleet_jobs=[],
            fleet_busy_until={},
            target_raid_cooldown_until={},
            bash_tracker=bash_tracker,
            raid_targets_info=raid_targets_info,
            raid_target_res=raid_target_res,
            depleted_targets=set(),
            spied_targets=set(),
            raids_loot_spice_eq=0.0,
            unique_raids_loot_spice_eq=0.0,
            raid_risk_penalty=0.0,
            attack_history_stats=attack_stats,
            scanned_systems=recent_scans,
            throttle_cooldowns=active_t_cds,
            tutorial_can_complete=tut_can,
            tutorial_reward=tut_rew,
            tutorial_claimed=False,
            tutorial_tasks_advanced=0,
            allow_asteroid_research_lab=allow_asteroid_research_lab,
            energy_deficit_hours=0.0,
            overflow_penalty=0.0,
            pig_starvation_penalty=0.0,
            source_state=state,
        )

    def clone(self) -> _SimState:
        planets = [p.clone() for p in self.planets]
        bt = BashTracker(
            max_active_attacks_24h=self.bash_tracker.max_active_attacks_24h,
            war_max_24h=self.bash_tracker.war_max_24h,
        )
        for coords, dq in self.bash_tracker._history.items():
            for ts in dq:
                bt._history[coords].append(ts)
        rj = (
            _SimResearchJob(
                self.research_job.research_key,
                self.research_job.target_level,
                self.research_job.planet_id,
                self.research_job.start_t,
                self.research_job.finish_t,
            )
            if self.research_job is not None
            else None
        )
        fjs = [
            _SimFleetJob(
                fj.planet_id,
                fj.target_coords,
                fj.loot,
                fj.loot_spice_eq,
                fj.start_t,
                fj.finish_t,
                fj.won,
                fj.outcome,
                fj.is_repeat,
            )
            for fj in self.fleet_jobs
        ]
        return _SimState(
            clock_s=self.clock_s,
            sim_base_ts=self.sim_base_ts,
            speedup_factor=self.speedup_factor,
            planets=planets,
            active_planet_id=self.active_planet_id,
            researches=dict(self.researches),
            research_names=dict(self.research_names),
            specs=dict(self.specs),
            research_job=rj,
            fleet_jobs=fjs,
            fleet_busy_until=dict(self.fleet_busy_until),
            target_raid_cooldown_until=dict(self.target_raid_cooldown_until),
            bash_tracker=bt,
            raid_targets_info=self.raid_targets_info,
            raid_target_res=dict(self.raid_target_res),
            depleted_targets=set(self.depleted_targets),
            spied_targets=set(self.spied_targets),
            raids_loot_spice_eq=self.raids_loot_spice_eq,
            unique_raids_loot_spice_eq=self.unique_raids_loot_spice_eq,
            raid_risk_penalty=self.raid_risk_penalty,
            attack_history_stats=self.attack_history_stats,
            scanned_systems=set(self.scanned_systems),
            throttle_cooldowns=dict(self.throttle_cooldowns),
            tutorial_can_complete=self.tutorial_can_complete,
            tutorial_reward=self.tutorial_reward,
            tutorial_claimed=self.tutorial_claimed,
            tutorial_tasks_advanced=self.tutorial_tasks_advanced,
            allow_asteroid_research_lab=self.allow_asteroid_research_lab,
            energy_deficit_hours=self.energy_deficit_hours,
            overflow_penalty=self.overflow_penalty,
            pig_starvation_penalty=self.pig_starvation_penalty,
            source_state=self._source_state,
        )

    def active_planet(self) -> Optional[_SimPlanet]:
        if self.active_planet_id is not None and self.active_planet_id in self.planet_by_id:
            return self.planet_by_id[self.active_planet_id]
        return self.planets[0] if self.planets else None

    def to_game_state(self) -> GameState:
        src = self._source_state
        out_planets: list[PlanetState] = []
        for sp in self.planets:
            e_prod, e_used, _ = _planet_energy_balance(sp, self.researches, self.speedup_factor)
            src_p = sp._source
            out_planets.append(
                PlanetState(
                    planet_id=sp.planet_id,
                    name=sp.name,
                    coords=sp.coords,
                    planet_type=sp.planet_type,
                    temperature=sp.temperature,
                    fields_used=sp.fields_used,
                    fields_max=sp.fields_max,
                    resources=_dict_to_resources(sp.res_f),
                    storage=Resources(
                        pig_iron=sp.storage_cap["pig_iron"],
                        metal=sp.storage_cap["metal"],
                        kryptonite=sp.storage_cap["kryptonite"],
                        spice=sp.storage_cap["spice"],
                    ),
                    energy=EnergyBalance(produced=e_prod, used=e_used),
                    buildings=dict(sp.buildings),
                    building_names=dict(sp.building_names),
                    ships=dict(sp.ships),
                    defense=dict(sp.defense),
                    building_offers=src_p.building_offers if src_p else (),
                    ship_offers=src_p.ship_offers if src_p else (),
                    defense_offers=src_p.defense_offers if src_p else (),
                    economy_rates=dict(sp.economy_rates),
                )
            )

        out_queues: list[QueueJob] = []
        ap = self.active_planet()
        if ap is not None:
            for bj in ap.build_queue:
                bname = str(game_data.BUILDING_BY_ID.get(bj.building_id, {}).get("name", f"Building {bj.building_id}"))
                out_queues.append(
                    QueueJob(
                        category="building",
                        name=bname,
                        building_id=bj.building_id,
                        target_level=bj.target_level,
                    )
                )
        if self.research_job is not None:
            rinfo = game_data.RESEARCH_BY_ID.get(self.research_job.research_key, {})
            out_queues.append(
                QueueJob(
                    category="research",
                    name=str(rinfo.get("name", self.research_job.research_key)),
                    target_level=self.research_job.target_level,
                )
            )

        out_raids: list[dict[str, Any]] = []
        per_t = self.attack_history_stats.get("per_target") or {}
        for coords, meta in self.raid_targets_info.items():
            res = self.raid_target_res.get(coords, Resources())
            item: dict[str, Any] = {
                "target_coords": coords,
                "owner": meta["owner"],
                "planet_name": meta["planet_name"],
                "status_color": meta["status_color"],
                "is_spacia": meta["is_spacia"],
                "pig_iron": res.pig_iron,
                "metal": res.metal,
                "kryptonite": res.kryptonite,
                "spice": res.spice,
                "fleet": dict(meta["fleet"]),
                "defense": dict(meta["defense"]),
                "dug_in": meta["dug_in"],
            }
            th = per_t.get(normalize_coords(coords))
            if isinstance(th, Mapping):
                item["attack_attempts"] = th.get("attempts", 0)
                item["attack_wins"] = th.get("wins", 0)
                item["attack_success_rate"] = th.get("success_rate", 0.0)
                item["avg_attack_loot_spice_eq"] = th.get("avg_loot_spice_eq", 0.0)
                item["recency_weighted_loot_spice_eq"] = th.get("recency_weighted_loot_spice_eq", 0.0)
                item["last_attack_loot_spice_eq"] = th.get("last_loot_spice_eq", 0.0)
                item["last_attack_outcome"] = th.get("last_outcome", "")
            out_raids.append(item)

        base_ts = _safe_float_any(getattr(src, "timestamp", 0.0), 0.0)
        clock_s = _safe_float_any(self.clock_s, 0.0)
        final_ts = _safe_float_any(base_ts + clock_s, base_ts)

        return GameState(
            universe_id=src.universe_id,
            user_id=src.user_id,
            nickname=src.nickname,
            timestamp=final_ts,
            planets=tuple(out_planets),
            active_planet_id=self.active_planet_id,
            researches=dict(self.researches),
            research_names=dict(self.research_names),
            specs=dict(self.specs),
            unread_messages=dict(src.unread_messages),
            tutorial=None if self.tutorial_claimed else src.tutorial,
            active_queues=tuple(out_queues),
            fleet_alerts=src.fleet_alerts,
            research_offers=src.research_offers,
            speedup_factor=self.speedup_factor,
            newbie_protection=src.newbie_protection,
            account_points=src.account_points,
            account_rank=src.account_rank,
            raid_targets=tuple(out_raids),
        )


# ---------------------------------------------------------------------------
# Exact Planet Economy, Piecewise Warehouse/Dry Solver & Event Advancement
# ---------------------------------------------------------------------------


def _planet_energy_balance(
    planet: _SimPlanet,
    researches: Mapping[str, int],
    speedup: float,
) -> tuple[int, int, float]:
    """Return `(energy_produced, energy_used, thermo_ratio)` including exact `b4<->b6` Spice collapse & `r2`."""
    r1 = min(100_000, max(0, _safe_int(researches.get("r1", 0), 0)))
    r2 = min(100_000, max(0, _safe_int(researches.get("r2", 0), 0)))
    r2_mult = 1.0 + 0.01 * r2

    b4 = planet.building_level(4)
    b6 = planet.building_level(6)
    pct4 = max(0, min(100, _safe_int(planet.economy_rates.get("resource4", 100), 100))) / 100.0
    pct5 = max(0, min(100, _safe_int(planet.economy_rates.get("resource5", 100), 100))) / 100.0

    thermo_need_hr = (
        float(game_data.building_level_stats(6, b6).get("spice_used", 0) or 0) * speedup * pct5
        if b6 > 0
        else 0.0
    )

    if thermo_need_hr <= 1e-9 or planet.res_f["spice"] > 1e-6:
        thermo_ratio = 1.0
    else:
        b4_base_hr = _fast_mine_prod(4, b4, planet.temperature, r1) * speedup * pct4
        if planet.storage_cap["spice"] > 0 and planet.res_f["spice"] >= planet.storage_cap["spice"] - 1e-6:
            b4_base_hr *= 0.05
        alpha = b4_base_hr / thermo_need_hr
        if alpha <= 0.0:
            thermo_ratio = 0.0
        else:
            r_init = min(1.0, alpha)
            e_at_1 = (planet.non_b6_energy_raw + planet.b6_energy_raw * r_init) * r2_mult
            if planet.energy_used <= 0 or e_at_1 >= planet.energy_used - 1e-12:
                thermo_ratio = r_init
            else:
                # Exact piecewise linear fixed point of f(r) = min(1.0, c_0 + k * r)
                c_0 = alpha * (planet.non_b6_energy_raw * r2_mult) / planet.energy_used
                k = alpha * (planet.b6_energy_raw * r2_mult) / planet.energy_used
                if c_0 <= 1e-12:
                    # Without non-b6 bootstrap power (b5/s9), b4 cannot run when spice == 0 -> physical equilibrium is 0.0
                    thermo_ratio = 0.0
                else:
                    f_init = c_0 + k * r_init
                    if r_init >= 1.0 - 1e-12 and f_init >= 1.0 - 1e-12:
                        thermo_ratio = 1.0
                    elif k < 1.0 - 1e-12:
                        thermo_ratio = max(0.0, min(1.0, c_0 / (1.0 - k)))
                    else:
                        thermo_ratio = 0.0
                        for _ in range(64):
                            e_prod_f = (planet.non_b6_energy_raw + planet.b6_energy_raw * thermo_ratio) * r2_mult
                            pr = max(0.0, min(1.0, e_prod_f / planet.energy_used))
                            next_r = min(1.0, alpha * pr)
                            if abs(next_r - thermo_ratio) < 1e-10:
                                thermo_ratio = next_r
                                break
                            thermo_ratio = next_r
        if thermo_ratio < 1e-12:
            thermo_ratio = 0.0

    e_prod = int(round((planet.non_b6_energy_raw + planet.b6_energy_raw * thermo_ratio) * r2_mult))
    return e_prod, planet.energy_used, thermo_ratio


def _compute_planet_rates(
    planet: _SimPlanet,
    researches: Mapping[str, int],
    speedup: float,
) -> tuple[dict[str, float], int, int, bool, bool]:
    """Return `(net_rates_hr, energy_net, energy_prod, pig_starved, any_capped)`."""
    r1 = min(100_000, max(0, _safe_int(researches.get("r1", 0), 0)))
    spd = _clamp_finite(speedup, 1.0, 0.1, 1_000_000.0)
    e_prod, e_used, thermo_ratio = _planet_energy_balance(planet, researches, spd)
    e_net = e_prod - e_used
    prod_ratio = max(0.0, e_prod / e_used) if (e_used > 0 and e_prod < e_used) else 1.0
    factor = spd * prod_ratio

    pct1 = max(0, min(100, _safe_int(planet.economy_rates.get("resource1", 100), 100))) / 100.0
    pct2 = max(0, min(100, _safe_int(planet.economy_rates.get("resource2", 100), 100))) / 100.0
    pct3 = max(0, min(100, _safe_int(planet.economy_rates.get("resource3", 100), 100))) / 100.0
    pct4 = max(0, min(100, _safe_int(planet.economy_rates.get("resource4", 100), 100))) / 100.0
    pct5 = max(0, min(100, _safe_int(planet.economy_rates.get("resource5", 100), 100))) / 100.0

    b1 = planet.building_level(1)
    b2 = planet.building_level(2)
    b3 = planet.building_level(3)
    b4 = planet.building_level(4)
    b6 = planet.building_level(6)

    pig_gross_hr = _fast_mine_prod(1, b1, planet.temperature, r1) * factor * pct1
    # Blast furnace (`b2`) is NOT boosted by Mining technology (`r1`).
    met_gross_hr = _fast_mine_prod(2, b2, 0, 0) * factor * pct2
    pig_used_nom_hr = _fast_blast_pig_used(b2) * factor * pct2
    kryp_gross_hr = _fast_mine_prod(3, b3, planet.temperature, r1) * factor * pct3
    spice_gross_hr = _fast_mine_prod(4, b4, planet.temperature, r1) * factor * pct4

    capped = {
        rk: (planet.storage_cap[rk] > 0 and planet.res_f[rk] >= planet.storage_cap[rk] - 1e-6)
        for rk in _RESOURCE_KEYS
    }
    if capped["pig_iron"]:
        pig_gross_hr *= 0.05
    if capped["metal"]:
        met_gross_hr *= 0.05
        pig_used_nom_hr *= 0.05
    if capped["kryptonite"]:
        kryp_gross_hr *= 0.05
    if capped["spice"]:
        spice_gross_hr *= 0.05

    pig_starved = False
    if planet.res_f["pig_iron"] <= 1e-6 and pig_gross_hr < pig_used_nom_hr:
        met_net_hr = 0.5 * pig_gross_hr
        pig_net_hr = 0.0
        pig_starved = pig_used_nom_hr > pig_gross_hr + 1e-6
    else:
        pig_net_hr = pig_gross_hr - pig_used_nom_hr
        met_net_hr = met_gross_hr

    thermo_need_hr = (
        float(game_data.building_level_stats(6, b6).get("spice_used", 0) or 0) * spd * pct5
        if b6 > 0
        else 0.0
    )
    spice_burn_hr = thermo_need_hr * thermo_ratio
    if planet.res_f["spice"] <= 1e-6 and spice_gross_hr < spice_burn_hr:
        spice_burn_hr = spice_gross_hr
    spice_net_hr = spice_gross_hr - spice_burn_hr

    rates = {
        "pig_iron": pig_net_hr if math.isfinite(pig_net_hr) else 0.0,
        "metal": met_net_hr if math.isfinite(met_net_hr) else 0.0,
        "kryptonite": kryp_gross_hr if math.isfinite(kryp_gross_hr) else 0.0,
        "spice": spice_net_hr if math.isfinite(spice_net_hr) else 0.0,
    }
    any_capped = any(capped[rk] and rates[rk] > 1e-9 for rk in _RESOURCE_KEYS)
    return rates, e_net, e_prod, pig_starved, any_capped


def _next_regime_switch_h(planet: _SimPlanet, rates: Mapping[str, float], limit_h: float) -> float:
    best_h = limit_h
    if rates["pig_iron"] < -1e-9 and planet.res_f["pig_iron"] > 1e-6:
        best_h = min(best_h, planet.res_f["pig_iron"] / (-rates["pig_iron"]))
    if rates["spice"] < -1e-9 and planet.res_f["spice"] > 1e-6:
        best_h = min(best_h, planet.res_f["spice"] / (-rates["spice"]))
    for rk in _RESOURCE_KEYS:
        cap = planet.storage_cap[rk]
        if rates[rk] > 1e-9 and cap > 0 and planet.res_f[rk] < cap - 1e-6:
            best_h = min(best_h, (cap - planet.res_f[rk]) / rates[rk])
    return max(0.0, best_h)


def _snap_boundary_crossing(planet: _SimPlanet, rates: Mapping[str, float]) -> bool:
    snapped = False
    if rates["pig_iron"] < -1e-9 and 0.0 < planet.res_f["pig_iron"] <= 1e-4:
        planet.res_f["pig_iron"] = 0.0
        snapped = True
    if rates["spice"] < -1e-9 and 0.0 < planet.res_f["spice"] <= 1e-4:
        planet.res_f["spice"] = 0.0
        snapped = True
    for rk in _RESOURCE_KEYS:
        cap = planet.storage_cap[rk]
        if rates[rk] > 1e-9 and cap > 0 and (cap - 1e-4) <= planet.res_f[rk] < cap:
            planet.res_f[rk] = float(cap)
            snapped = True
    return snapped


def _accumulate_planet(
    sim: _SimState,
    planet: _SimPlanet,
    dt_s: float,
) -> None:
    if dt_s <= 1e-9:
        return
    rem_h = dt_s / 3600.0
    for _ in range(24):
        if rem_h <= 1e-12:
            break
        rates, e_net, _e_prod, pig_starved, any_capped = _compute_planet_rates(
            planet, sim.researches, sim.speedup_factor
        )
        step_h = _next_regime_switch_h(planet, rates, rem_h)
        if step_h <= 1e-12:
            if _snap_boundary_crossing(planet, rates):
                continue
            step_h = min(rem_h, 1e-9)
        for rk in _RESOURCE_KEYS:
            nxt = planet.res_f[rk] + rates[rk] * step_h
            if nxt <= 1e-7:
                nxt = 0.0
            cap = planet.storage_cap[rk]
            if cap > 0 and abs(nxt - cap) <= 1e-5 and rates[rk] > 0:
                nxt = float(cap)
            planet.res_f[rk] = nxt
        if e_net < 0:
            sim.energy_deficit_hours += step_h
        if pig_starved:
            sim.pig_starvation_penalty += step_h
        if any_capped:
            sim.overflow_penalty += step_h
        rem_h -= step_h


def _complete_build_job(planet: _SimPlanet, job: _SimBuildJob, speedup: float) -> None:
    bid = job.building_id
    bkey = f"b{bid}"
    old_lvl = planet.building_level(bid)
    new_lvl = max(old_lvl + 1, job.target_level)
    planet.buildings[bkey] = new_lvl
    planet.fields_used += max(0, new_lvl - old_lvl)

    b_info = game_data.BUILDING_BY_ID.get(bid) or {}
    max_tbl = max(int(b_info.get("max_table_level", 100) or 100), len(b_info.get("levels") or {}), 100)
    upper_lvl = min(new_lvl, max_tbl + 1)
    for lvl in range(old_lvl + 1, upper_lvl + 1):
        delta_e = game_data.building_energy_delta(bid, lvl)
        if bid == 6:
            pct5 = max(0, min(100, _safe_int(planet.economy_rates.get("resource5", 100), 100))) / 100.0
            planet.b6_energy_raw = max(0.0, planet.b6_energy_raw + delta_e * pct5)
        elif delta_e > 0:
            planet.non_b6_energy_raw += float(delta_e)
        elif delta_e < 0:
            pct_k = (
                max(0, min(100, _safe_int(planet.economy_rates.get(f"resource{bid}", 100), 100))) / 100.0
                if bid in (1, 2, 3, 4)
                else 1.0
            )
            old_need = game_data.mine_energy_needed(bid, lvl - 1)
            new_need = game_data.mine_energy_needed(bid, lvl)
            planet.energy_used += int(round(new_need * pct_k)) - int(round(old_need * pct_k))

    if bid in (11, 12, 13, 14):
        rk = _RESOURCE_KEYS[bid - 11]
        stat_cap = _safe_int(game_data.building_level_stats(bid, new_lvl).get("storage_capacity"), 0)
        cap_val = stat_cap or min(10**18, 100_000 * (2 ** min(60, max(0, new_lvl))))
        planet.storage_cap[rk] = max(planet.storage_cap[rk], cap_val)

    # Promote next queued job if present
    if planet.build_queue:
        nxt = planet.build_queue[0]
        dur = _building_duration_s(
            nxt.building_id,
            nxt.target_level,
            dev_level=planet.building_level(7),
            micro_level=planet.building_level(17),
            speedup=speedup,
        )
        nxt.start_t = job.finish_t
        nxt.finish_t = job.finish_t + max(1.0, dur)
        nxt.duration_s = max(1.0, dur)


def _complete_ship_job(planet: _SimPlanet, job: _SimShipJob, speedup: float) -> None:
    if job.category == "ship":
        planet.ships[job.key] = int(planet.ships.get(job.key, 0) or 0) + job.quantity
        if job.key == "s9":
            temp_map = game_data.SHIPS.get("solar-cells", {}).get("solar_cell_energy_by_temp") or {}
            per_cell = game_data.interpolate_temp_value(temp_map, planet.temperature)
            planet.non_b6_energy_raw += per_cell * job.quantity
    else:
        planet.defense[job.key] = int(planet.defense.get(job.key, 0) or 0) + job.quantity

    if planet.shipyard_queue:
        nxt = planet.shipyard_queue[0]
        dur = _shipyard_duration_s(
            nxt.base_build_time_s,
            nxt.quantity,
            wf_level=planet.building_level(9),
            micro_level=planet.building_level(17),
            speedup=speedup,
        )
        nxt.start_t = job.finish_t
        nxt.finish_t = job.finish_t + max(1.0, dur)


def _complete_fleet_job(sim: _SimState, fj: _SimFleetJob) -> None:
    p = sim.planet_by_id.get(fj.planet_id)
    if p is not None:
        p.res_f["pig_iron"] += _safe_res_float(fj.loot.pig_iron)
        p.res_f["metal"] += _safe_res_float(fj.loot.metal)
        p.res_f["kryptonite"] += _safe_res_float(fj.loot.kryptonite)
        p.res_f["spice"] += _safe_res_float(fj.loot.spice)
    sim.raids_loot_spice_eq += float(fj.loot_spice_eq)
    if not fj.is_repeat:
        sim.unique_raids_loot_spice_eq += float(fj.loot_spice_eq)


def _next_queue_finish_t(sim: _SimState) -> Optional[float]:
    earliest: Optional[float] = None
    if sim.research_job is not None:
        earliest = sim.research_job.finish_t
    for fj in sim.fleet_jobs:
        if earliest is None or fj.finish_t < earliest:
            earliest = fj.finish_t
    for p in sim.planets:
        if p.build_queue:
            t = p.build_queue[0].finish_t
            if earliest is None or t < earliest:
                earliest = t
        if p.shipyard_queue:
            t = p.shipyard_queue[0].finish_t
            if earliest is None or t < earliest:
                earliest = t
    return earliest


def _advance_clock_to(sim: _SimState, target_t: float) -> None:
    """Advance simulation clock to `target_t`, processing every in-flight queue event in exact order."""
    if target_t <= sim.clock_s + 1e-9:
        sim.clock_s = max(sim.clock_s, target_t)
        return

    for _ in range(512):
        nxt_t = _next_queue_finish_t(sim)
        step_t = target_t if (nxt_t is None or nxt_t > target_t + 1e-9) else nxt_t
        dt_s = step_t - sim.clock_s
        if dt_s > 1e-9:
            for p in sim.planets:
                _accumulate_planet(sim, p, dt_s)
            sim.clock_s = step_t

        if nxt_t is None or nxt_t > target_t + 1e-9:
            break

        # Complete all jobs finishing at `step_t`
        if sim.research_job is not None and sim.research_job.finish_t <= step_t + 1e-9:
            rj = sim.research_job
            sim.researches[rj.research_key] = max(int(sim.researches.get(rj.research_key, 0) or 0), rj.target_level)
            sim.research_job = None

        if sim.fleet_jobs:
            remaining_fj: list[_SimFleetJob] = []
            for fj in sim.fleet_jobs:
                if fj.finish_t <= step_t + 1e-9:
                    _complete_fleet_job(sim, fj)
                else:
                    remaining_fj.append(fj)
            sim.fleet_jobs = remaining_fj

        for p in sim.planets:
            if p.build_queue and p.build_queue[0].finish_t <= step_t + 1e-9:
                done_b = p.build_queue.pop(0)
                _complete_build_job(p, done_b, sim.speedup_factor)
            if p.shipyard_queue and p.shipyard_queue[0].finish_t <= step_t + 1e-9:
                done_s = p.shipyard_queue.pop(0)
                _complete_ship_job(p, done_s, sim.speedup_factor)

    sim.clock_s = target_t


def _can_afford_res_f(have_f: float, cost_i: int) -> bool:
    if cost_i <= 0:
        return True
    if cost_i.bit_length() > 1000:
        return False
    return have_f >= float(cost_i) - 1e-6


def _deficit_wait_h(cost_i: int, have_f: float, rate_hr: float) -> float:
    if cost_i <= 0 or rate_hr <= 0.0:
        return 0.0
    if cost_i.bit_length() <= 1000:
        return max(0.0, (float(cost_i) - have_f) / rate_hr)
    return game_data.safe_big_ratio(cost_i, rate_hr, cap=1e200)


def _sub_res_f(have_f: float, cost_i: int) -> float:
    if cost_i <= 0:
        return max(0.0, have_f)
    if cost_i.bit_length() > 1000:
        return 0.0
    return max(0.0, have_f - float(cost_i))


def _solve_planet_reach_dt_s(
    planet: _SimPlanet,
    researches: Mapping[str, int],
    speedup: float,
    cost: Mapping[str, int],
    max_dt_s: float,
) -> Optional[float]:
    """Return exact seconds needed at current piecewise economy to afford `cost`, or `None` if blocked."""
    if all(_can_afford_res_f(planet.res_f[rk], cost[rk]) for rk in _RESOURCE_KEYS):
        return 0.0

    tmp_p = planet.clone()
    elapsed_s = 0.0
    for _ in range(24):
        rates, _, _, _, _ = _compute_planet_rates(tmp_p, researches, speedup)
        need_keys = [rk for rk in _RESOURCE_KEYS if not _can_afford_res_f(tmp_p.res_f[rk], cost[rk])]
        if not need_keys:
            return elapsed_s
        for rk in need_keys:
            if rates[rk] <= 1e-9:
                return None

        need_h = max(_deficit_wait_h(cost[rk], tmp_p.res_f[rk], rates[rk]) for rk in need_keys)
        rem_budget_h = max(0.0, (max_dt_s - elapsed_s) / 3600.0)
        switch_h = _next_regime_switch_h(tmp_p, rates, min(need_h, rem_budget_h + 1.0))

        if switch_h < need_h - 1e-9:
            if elapsed_s + switch_h * 3600.0 > max_dt_s + 1e-9:
                return None
            if switch_h <= 1e-12:
                if not _snap_boundary_crossing(tmp_p, rates):
                    for rk in _RESOURCE_KEYS:
                        tmp_p.res_f[rk] += rates[rk] * 1e-9
                    elapsed_s += 1e-9 * 3600.0
                continue
            for rk in _RESOURCE_KEYS:
                nxt = tmp_p.res_f[rk] + rates[rk] * switch_h
                cap = tmp_p.storage_cap[rk]
                if nxt <= 1e-7 or (rates[rk] < -1e-9 and nxt <= 1e-5):
                    nxt = 0.0
                if cap > 0 and rates[rk] > 1e-9 and (nxt >= cap - 1e-6 or abs(nxt - cap) <= 1e-5):
                    nxt = float(cap)
                tmp_p.res_f[rk] = nxt
            elapsed_s += switch_h * 3600.0
            continue

        total_s = elapsed_s + need_h * 3600.0
        return total_s if total_s <= max_dt_s + 1e-6 else None
    return None


def _has_inflight_rate_relief(sim: _SimState, planet: _SimPlanet) -> bool:
    for job in planet.build_queue:
        if job.building_id in (1, 2, 3, 4, 5, 6):
            return True
    for sjob in planet.shipyard_queue:
        if sjob.key == "s9":
            return True
    if any(fj.planet_id == planet.planet_id for fj in sim.fleet_jobs):
        return True
    if sim.research_job is not None and sim.research_job.research_key in ("r1", "r2"):
        return True
    return False


def _advance_until_affordable(
    sim: _SimState,
    planet_id: int,
    cost: Resources,
    *,
    max_clock_s: float,
) -> tuple[bool, float, str]:
    cost_d = {
        "pig_iron": max(0, _safe_int(getattr(cost, "pig_iron", 0), 0)),
        "metal": max(0, _safe_int(getattr(cost, "metal", 0), 0)),
        "kryptonite": max(0, _safe_int(getattr(cost, "kryptonite", 0), 0)),
        "spice": max(0, _safe_int(getattr(cost, "spice", 0), 0)),
    }
    start_clock = sim.clock_s
    planet = sim.planet_by_id[planet_id]
    if all(_can_afford_res_f(planet.res_f[rk], cost_d[rk]) for rk in _RESOURCE_KEYS):
        return True, 0.0, ""

    for _ in range(64):
        planet = sim.planet_by_id[planet_id]
        if all(_can_afford_res_f(planet.res_f[rk], cost_d[rk]) for rk in _RESOURCE_KEYS):
            return True, sim.clock_s - start_clock, ""

        rem_s = max(0.0, max_clock_s - sim.clock_s)
        dt_need_s = _solve_planet_reach_dt_s(planet, sim.researches, sim.speedup_factor, cost_d, rem_s)
        nxt_q_t = _next_queue_finish_t(sim)

        if dt_need_s is None:
            if nxt_q_t is not None and nxt_q_t <= max_clock_s + 1e-9 and _has_inflight_rate_relief(sim, planet):
                _advance_clock_to(sim, nxt_q_t)
                continue
            rates, _, _, _, _ = _compute_planet_rates(planet, sim.researches, sim.speedup_factor)
            bad_rk = next(
                (rk for rk in _RESOURCE_KEYS if not _can_afford_res_f(planet.res_f[rk], cost_d[rk]) and rates[rk] <= 1e-9),
                "resource",
            )
            return False, 0.0, f"Blocked: non-positive {bad_rk} rate ({rates.get(bad_rk, 0.0):.1f}/h)"

        target_t = sim.clock_s + dt_need_s
        if nxt_q_t is not None and nxt_q_t < target_t - 1e-6:
            _advance_clock_to(sim, nxt_q_t)
            continue
        if target_t > max_clock_s + 1e-6:
            return False, 0.0, "Wait exceeds simulation horizon"
        _advance_clock_to(sim, target_t)
        planet = sim.planet_by_id[planet_id]
        if all(_can_afford_res_f(planet.res_f[rk], cost_d[rk]) for rk in _RESOURCE_KEYS):
            return True, sim.clock_s - start_clock, ""

    return False, 0.0, "Wait loop exceeded step limit"


def _prereq_inflight(
    sim: _SimState,
    planet: _SimPlanet,
    unmet: Sequence[Mapping[str, Any]],
) -> bool:
    proj_b = planet.projected_buildings()
    proj_r = dict(sim.researches)
    if sim.research_job is not None:
        proj_r[sim.research_job.research_key] = max(
            proj_r.get(sim.research_job.research_key, 0),
            sim.research_job.target_level,
        )
    for req in unmet:
        cat = str(req["category"])
        slug = str(req["slug"])
        req_lvl = int(req["required"])
        if cat == "buildings":
            if slug.startswith("b") and len(slug) <= 6 and slug[1:].isascii() and slug[1:].isdigit():
                if proj_b.get(slug, 0) < req_lvl:
                    return False
            else:
                info = game_data.BUILDINGS.get(slug)
                if not info or info.get("id") is None:
                    return False
                if proj_b.get(f"b{int(info['id'])}", 0) < req_lvl:
                    return False
        elif cat == "research":
            if slug in game_data.RESEARCH_BY_ID:
                if proj_r.get(slug, 0) < req_lvl:
                    return False
            else:
                info = game_data.RESEARCHES.get(slug)
                if not info or not info.get("id"):
                    return False
                if proj_r.get(str(info["id"]), 0) < req_lvl:
                    return False
        else:
            return False
    return True


def _wait_for_prereqs(
    sim: _SimState,
    planet: _SimPlanet,
    category: str,
    slug_or_id: int | str,
    *,
    max_clock_s: float,
) -> tuple[bool, str]:
    for _ in range(32):
        unmet = _fast_unmet_prereqs(
            category, slug_or_id, planet.buildings, sim.researches
        )
        if not unmet:
            return True, ""
        if not _prereq_inflight(sim, planet, unmet):
            req = unmet[0]
            return False, f"Unmet prerequisite: {req['name']} L{req['required']} (have L{req['current']})"
        nxt_t = _next_queue_finish_t(sim)
        if nxt_t is None or nxt_t > max_clock_s + 1e-6:
            return False, "Prerequisite finishes beyond simulation horizon"
        _advance_clock_to(sim, nxt_t)
    return False, "Prerequisite wait limit reached"


def _opt_tutorial_fight_task(tut: Any) -> Optional[Any]:
    if tut is None or getattr(tut, "can_complete", False):
        return None
    for task in getattr(tut, "tasks", ()) or ():
        if getattr(task, "completed", False) or getattr(task, "category", "") != "milestone":
            continue
        desc = _safe_action_str(getattr(task, "description", "")).lower()
        if any(w in desc for w in ("fight", "attack", "combat", "battle")):
            return task
    return None


def _opt_tutorial_spy_task(tut: Any) -> Optional[Any]:
    if tut is None or getattr(tut, "can_complete", False):
        return None
    for task in getattr(tut, "tasks", ()) or ():
        if getattr(task, "completed", False) or getattr(task, "category", "") != "milestone":
            continue
        desc = _safe_action_str(getattr(task, "description", "")).lower()
        if any(w in desc for w in ("spy", "espionage", "spionage", "probe")):
            return task
    return None


def _opt_milestone_helper_ship_id(desc_lower: str) -> Optional[int]:
    if any(w in desc_lower for w in ("spy", "espionage", "spionage", "probe")):
        return 10
    if any(w in desc_lower for w in ("recycle", "recycler", "debris", "rubble")):
        return 11
    if any(w in desc_lower for w in ("colonize", "colony", "colonisation", "colonization")):
        return 8
    return None


def _opt_resolve_prereq_chain(
    category: str,
    slug_or_id: int | str,
    goal_label: str,
    eff_buildings: Mapping[str, int],
    researches: Mapping[str, int],
    visited: Optional[set[tuple[str, str]]] = None,
) -> list[dict[str, Any]]:
    if visited is None:
        visited = set()
    key = (category, str(slug_or_id))
    if key in visited:
        return []
    visited.add(key)
    unmet = game_data.missing_prerequisites(
        category,
        slug_or_id,
        buildings=eff_buildings,
        researches=researches,
    )
    steps: list[dict[str, Any]] = []
    for req in unmet:
        sub = _opt_resolve_prereq_chain(
            str(req["category"]),
            req["slug"],
            goal_label,
            eff_buildings,
            researches,
            visited,
        )
        if sub:
            steps.extend(sub)
        else:
            steps.append({**req, "goal": goal_label})
    return steps


def _next_unscanned_system(
    origin_coords: str,
    scanned: set[tuple[int, int]],
    *,
    radius: int = 15,
) -> Optional[tuple[int, int]]:
    gal, sys_num = _parse_gal_sys(origin_coords)
    if (gal, sys_num) not in scanned:
        return (gal, sys_num)
    for delta in range(1, max(1, radius) + 1):
        for cand_sys in (sys_num + delta, sys_num - delta):
            if 1 <= cand_sys <= 400 and (gal, cand_sys) not in scanned:
                return (gal, cand_sys)
    return None


def _sim_tutorial_fight_pending(sim: _SimState) -> bool:
    src = getattr(sim, "_source_state", None)
    tut = getattr(src, "tutorial", None) if src is not None else None
    if _opt_tutorial_fight_task(tut) is None:
        return False
    if sim.fleet_jobs:
        return False
    if any(getattr(q, "category", "") == "fleet" for q in (getattr(src, "active_queues", ()) or ())):
        return False
    return True


def _sim_tutorial_spy_pending(sim: _SimState) -> bool:
    src = getattr(sim, "_source_state", None)
    tut = getattr(src, "tutorial", None) if src is not None else None
    if _opt_tutorial_spy_task(tut) is None:
        return False
    return not bool(sim.spied_targets)


def _tutorial_progress_match(sim: _SimState, kind: str, target_id: Optional[int | str]) -> int:
    tut = sim._source_state.tutorial
    if not tut or tut.can_complete:
        return 0
    ap = sim.active_planet()
    eff_b = ap.projected_buildings() if ap is not None else {}
    for task in tut.tasks:
        if task.completed:
            continue
        t_bid = game_data.safe_optional_int(task.building_id, None)
        if kind == "BUILD" and task.category == "building" and t_bid == target_id:
            return 1
        if kind == "RESEARCH" and task.category == "research" and _safe_action_str(task.research_id) == _safe_action_str(target_id):
            return 1
        if kind == "BUILD_SHIP" and task.category == "ship" and t_bid == target_id:
            return 1
        if kind == "ATTACK" and task.category == "milestone":
            desc = _safe_action_str(task.description).lower()
            if any(w in desc for w in ("fight", "attack", "combat", "battle")):
                return 1
        if kind == "SPY" and task.category == "milestone":
            desc = _safe_action_str(task.description).lower()
            if any(w in desc for w in ("spy", "espionage", "spionage", "probe")):
                return 1
        if kind == "BUILD_SHIP" and task.category == "milestone":
            helper_sid = _opt_milestone_helper_ship_id(_safe_action_str(task.description).lower())
            if helper_sid is not None and helper_sid == target_id:
                return 1
        if kind in ("BUILD", "RESEARCH"):
            goal_cat: Optional[str] = None
            goal_key: Optional[int | str] = None
            if task.category == "building" and t_bid is not None and t_bid > 0:
                goal_cat, goal_key = "buildings", t_bid
            elif task.category == "research" and task.research_id:
                goal_cat, goal_key = "research", _safe_action_str(task.research_id)
            elif task.category == "ship" and t_bid is not None and t_bid > 0:
                goal_cat, goal_key = "ships", SHIP_SLUG_BY_KEY.get(f"s{t_bid}")
            elif task.category == "milestone":
                h_sid = _opt_milestone_helper_ship_id(_safe_action_str(task.description).lower())
                if h_sid is not None:
                    goal_cat, goal_key = "ships", SHIP_SLUG_BY_KEY.get(f"s{h_sid}")
            if goal_cat and goal_key:
                for step in _opt_resolve_prereq_chain(goal_cat, goal_key, "tutorial", eff_b, sim.researches):
                    if kind == "BUILD" and step["category"] == "buildings":
                        b_unit = game_data.resolve_unit("buildings", step["slug"])
                        if b_unit and b_unit.get("id") == target_id:
                            return 1
                    elif kind == "RESEARCH" and step["category"] == "research":
                        r_unit = game_data.resolve_unit("research", step["slug"])
                        if r_unit and str(r_unit.get("id")) == _safe_action_str(target_id):
                            return 1
    return 0


# ---------------------------------------------------------------------------
# Metrics Computation & Public `simulate_action_sequence`
# ---------------------------------------------------------------------------


def _compute_sim_metrics(
    sim: _SimState,
    cfg: StrategyConfig,
    warn_ratio: float,
) -> EmpireMetrics:
    bld_total = 0
    flt_total = 0
    res_total = 0

    total_ships: dict[str, int] = {}
    total_cargo = 0
    unspent_sp = 0.0
    net_pig = 0.0
    net_met = 0.0
    net_kryp = 0.0
    net_spice = 0.0
    e_def_planets = 0
    total_e_net = 0

    for sp in sim.planets:
        for bkey, lvl in sp.buildings.items():
            bid = _parse_building_key_id(bkey)
            if bid is None:
                continue
            bld_total += _cum_building_res_sum(bid, _safe_int(lvl, 0))

        for skey, qty in sp.ships.items():
            cnt = max(0, _safe_int(qty, 0))
            if cnt <= 0:
                continue
            slug = SHIP_SLUG_BY_KEY.get(skey)
            unit = game_data.SHIPS.get(slug or "") if slug else None
            if not unit:
                continue
            bc = unit.get("base_cost") or {}
            flt_total += sum(_safe_int(bc.get(rk, 0), 0) for rk in _RESOURCE_KEYS) * cnt
            if skey != "s9":
                total_ships[skey] = total_ships.get(skey, 0) + cnt

        for dkey, qty in sp.defense.items():
            cnt = max(0, _safe_int(qty, 0))
            if cnt <= 0:
                continue
            slug = DEFENSE_SLUG_BY_KEY.get(dkey)
            unit = game_data.DEFENSES.get(slug or "") if slug else None
            if not unit:
                continue
            bc = unit.get("base_cost") or {}
            flt_total += sum(_safe_int(bc.get(rk, 0), 0) for rk in _RESOURCE_KEYS) * cnt

        total_cargo += fleet_cargo_capacity(sp.ships)
        unspent_sp += game_data.spice_equivalent(_dict_to_resources(sp.res_f))
        rates, e_net, _, _, _ = _compute_planet_rates(sp, sim.researches, sim.speedup_factor)
        net_pig += rates["pig_iron"]
        net_met += rates["metal"]
        net_kryp += rates["kryptonite"]
        net_spice += rates["spice"]
        total_e_net += e_net
        if e_net < 0:
            e_def_planets += 1

    for rkey, lvl in sim.researches.items():
        res_total += _cum_research_res_sum(rkey, _safe_int(lvl, 0))

    bld_pts = _safe_res_float(bld_total, 1e200) / POINTS_PER_RESOURCE_DIVISOR
    res_pts = _safe_res_float(res_total, 1e200) / POINTS_PER_RESOURCE_DIVISOR
    flt_pts = _safe_res_float(flt_total, 1e200) / POINTS_PER_RESOURCE_DIVISOR
    pts = bld_pts + res_pts + flt_pts

    hourly_spice_eq = game_data.spice_equivalent(
        {
            "pig_iron": max(0.0, net_pig),
            "metal": max(0.0, net_met),
            "kryptonite": max(0.0, net_kryp),
            "spice": max(0.0, net_spice),
        }
    )
    hourly_pts = (
        max(0.0, net_pig) + max(0.0, net_met) + max(0.0, net_kryp) + max(0.0, net_spice)
    ) / POINTS_PER_RESOURCE_DIVISOR

    mobile_ships = {k: total_ships[k] for k in _COMBAT_AND_RAID_SHIP_KEYS if total_ships.get(k, 0) > 0}
    fleet_power = (
        simulate_raid_outcome(
            mobile_ships,
            SpyReport(target_coords="01:001:01"),
            attacker_researches=sim.researches,
            safety_margin=0.0,
        ).attacker_power
        if mobile_ships
        else 0.0
    )

    potential_raid_sp = 0.0
    ap = sim.active_planet()
    origin_coords = ap.coords if ap else "01:001:01"
    if mobile_ships and sim.raid_targets_info:
        sim_now = sim.sim_base_ts + sim.clock_s
        raid_loots: list[float] = []
        for coords, meta in sim.raid_targets_info.items():
            is_inact = _safe_action_str(meta["status_color"]).lower() == "yellow"
            is_sp = bool(meta["is_spacia"])
            if not sim.bash_tracker.can_attack(
                coords, is_inactive=is_inact, is_spacia=is_sp, timestamp=sim_now
            ):
                continue
            _, _, pos = _parse_gal_sys_pos(coords)
            slot = GalaxySlot(
                coords=coords,
                position=pos,
                name=meta["planet_name"],
                owner=meta["owner"],
                status_color=meta["status_color"],
                is_spacia=is_sp,
            )
            rep = SpyReport(
                target_coords=coords,
                owner=meta["owner"],
                resources=sim.raid_target_res.get(coords, Resources()),
                fleet=meta["fleet"],
                defense=meta["defense"],
                dug_in=meta["dug_in"],
                is_spacia=is_sp,
            )
            rec = score_raid_target(
                origin_coords,
                slot,
                rep,
                mobile_ships,
                attacker_researches=sim.researches,
                bash_tracker=None,
                safety_margin=cfg.combat_safety_margin,
                min_loot_spice_eq=0.0,
                allow_pvp_in_az=cfg.allow_pvp_in_az,
            )
            if rec is not None:
                exp_sp, _, _ = _compute_effective_raid_expectation(
                    rec.loot_spice_equivalent,
                    rec.combat.cargo_capacity,
                    coords,
                    sim.attack_history_stats,
                    target_depleted=(coords in sim.depleted_targets),
                )
                raid_loots.append(exp_sp)
        raid_loots.sort(reverse=True)
        potential_raid_sp = sum(raid_loots[:3])

    composite = pts + 0.5 * hourly_spice_eq + (sim.raids_loot_spice_eq + 0.25 * potential_raid_sp) / 1000.0
    return EmpireMetrics(
        points=round(pts, 4),
        buildings_points=round(bld_pts, 4),
        research_points=round(res_pts, 4),
        fleet_defense_points=round(flt_pts, 4),
        hourly_pig_iron_net=round(net_pig, 3),
        hourly_metal_net=round(net_met, 3),
        hourly_kryptonite_net=round(net_kryp, 3),
        hourly_spice_net=round(net_spice, 3),
        hourly_spice_eq=round(hourly_spice_eq, 3),
        hourly_points_steady=round(hourly_pts, 4),
        fleet_power=round(fleet_power, 3),
        fleet_cargo_capacity=total_cargo,
        potential_raid_spice_eq=round(potential_raid_sp, 2),
        simulated_raid_loot_spice_eq=round(sim.raids_loot_spice_eq, 2),
        unspent_spice_eq=round(unspent_sp, 2),
        energy_deficit_planets=e_def_planets,
        total_energy_net=total_e_net,
        composite_score=round(composite, 4),
    )


def _parse_gal_sys_pos(coords: str) -> tuple[int, int, int]:
    parts = [p.strip() for p in _safe_action_str(coords).split(":")]
    if len(parts) == 3 and all(p.isascii() and p.isdigit() for p in parts):
        return int(parts[0][:9]), int(parts[1][:9]), int(parts[2][:9])
    return 1, 1, 1


def _building_step_cost(planet: _SimPlanet, bid: int, target_level: int, is_t0: bool) -> Resources:
    if is_t0 and planet._source is not None:
        for off in planet._source.building_offers:
            if off.building_id == bid and off.target_level == target_level and not off.cost.is_zero():
                return off.cost
    return _resources_from_dict(game_data.building_cost(bid, target_level))


def _research_step_cost(sim: _SimState, rkey: str, target_level: int, is_t0: bool) -> Resources:
    if is_t0:
        for off in sim._source_state.research_offers:
            if off.research_key == rkey and off.target_level == target_level and not off.cost.is_zero():
                return off.cost
    info = game_data.RESEARCH_BY_ID.get(rkey)
    if not info:
        return Resources()
    return _resources_from_dict(_research_level_stats(info, target_level).get("cost") or {})


def _unit_step_cost(
    planet: _SimPlanet,
    category: str,
    uid: int,
    slug: str,
    quantity: int,
    is_t0: bool,
) -> Resources:
    unit_cost: Optional[Resources] = None
    if is_t0 and planet._source is not None:
        offers = planet._source.ship_offers if category == "ship" else planet._source.defense_offers
        for off in offers:
            if off.unit_id == uid and not off.cost.is_zero():
                unit_cost = off.cost
                break
    if unit_cost is None:
        catalog = game_data.SHIPS if category == "ship" else game_data.DEFENSES
        unit_cost = _resources_from_dict((catalog.get(slug) or {}).get("base_cost") or {})
    qty = max(1, _safe_int(quantity, 1))
    return Resources(
        pig_iron=unit_cost.pig_iron * qty,
        metal=unit_cost.metal * qty,
        kryptonite=unit_cost.kryptonite * qty,
        spice=unit_cost.spice * qty,
    )


def _execute_sim_step(
    sim: _SimState,
    idx: int,
    action: SimAction,
    cfg: StrategyConfig,
    *,
    max_clock_s: float,
) -> SimStepOutcome:
    planet = (
        sim.planet_by_id.get(action.planet_id)
        if isinstance(action.planet_id, int) and not isinstance(action.planet_id, bool)
        else None
    )
    if planet is None:
        return SimStepOutcome(idx, action, False, f"Unknown planet_id {_safe_action_str(action.planet_id)}")

    ptype = normalize_planet_type(planet)
    kind = _safe_action_str(action.kind).strip().upper()
    entry_clock = sim.clock_s
    is_t0 = entry_clock <= 1e-6

    if kind == "BUILD":
        bid = _parse_building_target_id(action.target_id, -1)
        info = game_data.BUILDING_BY_ID.get(bid)
        if not info:
            return SimStepOutcome(
                idx,
                action,
                False,
                f"Unknown building {_safe_action_str(action.target_id)}",
                planet_id=planet.planet_id,
            )

        bname = str(info["name"])
        b8_offer_ok = (
            bid == 8
            and planet._source is not None
            and any(
                game_data.safe_optional_int(o.building_id, None) == 8
                for o in planet._source.building_offers
            )
        )
        if not is_building_allowed_on_planet_type(
            ptype,
            bid,
            has_b8_offer=b8_offer_ok,
            allow_asteroid_research_lab=sim.allow_asteroid_research_lab,
        ):
            if ptype == "asteroid":
                msg = f"Asteroid forbids building {bname} (b{bid})"
            elif bid == 19:
                msg = "Galaxy scanner (b19) is Asteroid-exclusive"
            elif ptype == "raid":
                msg = f"Raid colony forbids building {bname} (b{bid})"
            else:
                msg = f"Building {bname} (b{bid}) not permitted on {ptype}"
            return SimStepOutcome(idx, action, False, msg, planet_id=planet.planet_id, target_name=bname)

        if planet.fields_max <= 0 or (planet.fields_used + len(planet.build_queue)) >= planet.fields_max:
            return SimStepOutcome(
                idx,
                action,
                False,
                f"No free building fields ({game_data.fmt_compact_int(planet.fields_used)}/{game_data.fmt_compact_int(planet.fields_max)})",
                planet_id=planet.planet_id,
                target_name=bname,
            )

        max_slots = planet.max_build_slots(sim.specs)
        if len(planet.build_queue) >= max_slots:
            nxt_t = planet.build_queue[0].finish_t
            if nxt_t > max_clock_s + 1e-6:
                return SimStepOutcome(idx, action, False, "Build queue full within horizon", planet_id=planet.planet_id, target_name=bname)
            _advance_clock_to(sim, nxt_t)

        target_lvl = planet.effective_building_level(bid) + 1
        max_tbl = int(info.get("max_table_level", 100) or 100)
        if target_lvl > max_tbl:
            return SimStepOutcome(idx, action, False, f"{bname} reached max table level L{max_tbl}", planet_id=planet.planet_id, target_name=bname, target_level=target_lvl)

        prereq_ok, prereq_err = _wait_for_prereqs(sim, planet, "buildings", bid, max_clock_s=max_clock_s)
        if not prereq_ok:
            return SimStepOutcome(idx, action, False, prereq_err, planet_id=planet.planet_id, target_name=bname, target_level=target_lvl)

        # Re-check queue slot after prereq wait
        if len(planet.build_queue) >= max_slots:
            nxt_t = planet.build_queue[0].finish_t
            if nxt_t > max_clock_s + 1e-6:
                return SimStepOutcome(idx, action, False, "Build queue full", planet_id=planet.planet_id, target_name=bname, target_level=target_lvl)
            _advance_clock_to(sim, nxt_t)
        target_lvl = planet.effective_building_level(bid) + 1
        if target_lvl > max_tbl:
            return SimStepOutcome(idx, action, False, f"{bname} reached max table level L{max_tbl}", planet_id=planet.planet_id, target_name=bname, target_level=target_lvl)

        cost = _building_step_cost(planet, bid, target_lvl, is_t0 and sim.clock_s <= 1e-6)
        ok, _, wait_err = _advance_until_affordable(sim, planet.planet_id, cost, max_clock_s=max_clock_s)
        if not ok:
            return SimStepOutcome(idx, action, False, wait_err, planet_id=planet.planet_id, target_name=bname, target_level=target_lvl, cost=cost)

        # Re-check queue slot if waiting pushed us without freeing slot
        if len(planet.build_queue) >= max_slots:
            nxt_t = planet.build_queue[0].finish_t
            if nxt_t > max_clock_s + 1e-6:
                return SimStepOutcome(idx, action, False, "Build queue full", planet_id=planet.planet_id, target_name=bname, target_level=target_lvl, cost=cost)
            _advance_clock_to(sim, nxt_t)
        target_lvl = planet.effective_building_level(bid) + 1
        wait_s = sim.clock_s - entry_clock

        # Deduct resources
        planet.res_f["pig_iron"] = _sub_res_f(planet.res_f["pig_iron"], cost.pig_iron)
        planet.res_f["metal"] = _sub_res_f(planet.res_f["metal"], cost.metal)
        planet.res_f["kryptonite"] = _sub_res_f(planet.res_f["kryptonite"], cost.kryptonite)
        planet.res_f["spice"] = _sub_res_f(planet.res_f["spice"], cost.spice)

        proj_b = planet.projected_buildings()
        dur_s = _building_duration_s(
            bid,
            target_lvl,
            dev_level=proj_b.get("b7", 0),
            micro_level=proj_b.get("b17", 0),
            speedup=sim.speedup_factor,
        )
        job_start = planet.build_queue[-1].finish_t if planet.build_queue else sim.clock_s
        job_finish = job_start + max(1.0, dur_s)
        planet.build_queue.append(
            _SimBuildJob(
                building_id=bid,
                target_level=target_lvl,
                start_t=job_start,
                finish_t=job_finish,
                duration_s=max(1.0, dur_s),
            )
        )
        sim.tutorial_tasks_advanced += _tutorial_progress_match(sim, "BUILD", bid)
        exec_t0 = (
            sim.clock_s <= 1e-6
            and wait_s <= 1e-6
            and planet._source is not None
            and any(o.building_id == bid and o.can_build and o.target_level == target_lvl for o in planet._source.building_offers)
        )
        return SimStepOutcome(
            index=idx,
            action=action,
            valid=True,
            planet_id=planet.planet_id,
            target_name=bname,
            target_level=target_lvl,
            cost=cost,
            wait_s=wait_s,
            duration_s=max(1.0, dur_s),
            start_t=job_start,
            finish_t=job_finish,
            executable_at_t0=exec_t0,
        )

    if kind == "RESEARCH":
        if ptype == "raid":
            return SimStepOutcome(idx, action, False, "Raid colony forbids research", planet_id=planet.planet_id)
        rkey = _resolve_research_key(action.target_id)
        if not rkey:
            return SimStepOutcome(
                idx,
                action,
                False,
                f"Unknown or unsupported research {_safe_action_str(action.target_id)}",
                planet_id=planet.planet_id,
            )
        info = game_data.RESEARCH_BY_ID[rkey]
        rname = str(info["name"])

        if sim.research_job is not None:
            if sim.research_job.finish_t > max_clock_s + 1e-6:
                return SimStepOutcome(idx, action, False, "Research queue busy past horizon", planet_id=planet.planet_id, target_name=rname)
            _advance_clock_to(sim, sim.research_job.finish_t)

        target_lvl = int(sim.researches.get(rkey, 0) or 0) + 1
        max_tbl = int(info.get("max_table_level", 40) or 40)
        if target_lvl > max_tbl:
            return SimStepOutcome(idx, action, False, f"{rname} reached max table level L{max_tbl}", planet_id=planet.planet_id, target_name=rname, target_level=target_lvl)

        if planet.building_level(8) < 1:
            if planet.effective_building_level(8) < 1:
                return SimStepOutcome(idx, action, False, "Research lab (b8) L1+ required on executing planet", planet_id=planet.planet_id, target_name=rname, target_level=target_lvl)
            b8_job = next((j for j in planet.build_queue if j.building_id == 8 and j.target_level >= 1), None)
            if b8_job is None or b8_job.finish_t > max_clock_s + 1e-6:
                return SimStepOutcome(idx, action, False, "Research lab (b8) L1+ finishes beyond horizon", planet_id=planet.planet_id, target_name=rname, target_level=target_lvl)
            _advance_clock_to(sim, b8_job.finish_t)

        prereq_ok, prereq_err = _wait_for_prereqs(sim, planet, "research", rkey, max_clock_s=max_clock_s)
        if not prereq_ok:
            return SimStepOutcome(idx, action, False, prereq_err, planet_id=planet.planet_id, target_name=rname, target_level=target_lvl)
        if sim.research_job is not None:
            if sim.research_job.finish_t > max_clock_s + 1e-6:
                return SimStepOutcome(idx, action, False, "Research queue busy", planet_id=planet.planet_id, target_name=rname, target_level=target_lvl)
            _advance_clock_to(sim, sim.research_job.finish_t)

        target_lvl = int(sim.researches.get(rkey, 0) or 0) + 1
        if target_lvl > max_tbl:
            return SimStepOutcome(idx, action, False, f"{rname} reached max table level L{max_tbl}", planet_id=planet.planet_id, target_name=rname, target_level=target_lvl)

        cost = _research_step_cost(sim, rkey, target_lvl, sim.clock_s <= 1e-6)
        ok, _, wait_err = _advance_until_affordable(sim, planet.planet_id, cost, max_clock_s=max_clock_s)
        if not ok:
            return SimStepOutcome(idx, action, False, wait_err, planet_id=planet.planet_id, target_name=rname, target_level=target_lvl, cost=cost)
        if sim.research_job is not None:
            if sim.research_job.finish_t > max_clock_s + 1e-6:
                return SimStepOutcome(idx, action, False, "Research queue busy", planet_id=planet.planet_id, target_name=rname, target_level=target_lvl, cost=cost)
            _advance_clock_to(sim, sim.research_job.finish_t)

        wait_s = sim.clock_s - entry_clock
        planet.res_f["pig_iron"] = _sub_res_f(planet.res_f["pig_iron"], cost.pig_iron)
        planet.res_f["metal"] = _sub_res_f(planet.res_f["metal"], cost.metal)
        planet.res_f["kryptonite"] = _sub_res_f(planet.res_f["kryptonite"], cost.kryptonite)
        planet.res_f["spice"] = _sub_res_f(planet.res_f["spice"], cost.spice)

        dur_s = _research_duration_s(rkey, target_lvl, lab_level=planet.building_level(8), speedup=sim.speedup_factor)
        job_start = sim.clock_s
        job_finish = job_start + max(1.0, dur_s)
        sim.research_job = _SimResearchJob(rkey, target_lvl, planet.planet_id, job_start, job_finish)
        sim.tutorial_tasks_advanced += _tutorial_progress_match(sim, "RESEARCH", rkey)
        exec_t0 = (
            sim.clock_s <= 1e-6
            and wait_s <= 1e-6
            and planet.planet_id == sim.active_planet_id
            and planet._source is not None
            and planet._source.building_level(8) >= 1
            and not _fast_unmet_prereqs(
                "research",
                rkey,
                planet._source.buildings,
                sim._source_state.researches,
            )
            and any(
                o.research_key == rkey
                and o.can_research
                and game_data.safe_optional_int(o.target_level, None) == target_lvl
                for o in sim._source_state.research_offers
            )
        )
        return SimStepOutcome(
            index=idx,
            action=action,
            valid=True,
            planet_id=planet.planet_id,
            target_name=rname,
            target_level=target_lvl,
            cost=cost,
            wait_s=wait_s,
            duration_s=max(1.0, dur_s),
            start_t=job_start,
            finish_t=job_finish,
            executable_at_t0=exec_t0,
        )

    if kind in ("BUILD_SHIP", "BUILD_DEFENSE"):
        if ptype == "raid":
            return SimStepOutcome(
                idx,
                action,
                False,
                f"Raid colony forbids {'shipyard' if kind == 'BUILD_SHIP' else 'defense'} construction",
                planet_id=planet.planet_id,
            )
        is_ship = kind == "BUILD_SHIP"
        if is_ship:
            ukey, slug = _resolve_ship_key_and_slug(action.target_id)
            cat_gd = "ships"
            cat_short = "ship"
        else:
            ukey, slug = _resolve_defense_key_and_slug(action.target_id)
            cat_gd = "defense"
            cat_short = "defense"

        if not ukey or not slug:
            return SimStepOutcome(
                idx,
                action,
                False,
                f"Unknown or event-only {cat_short} {_safe_action_str(action.target_id)}",
                planet_id=planet.planet_id,
            )

        qty = _parse_sim_quantity(action.quantity)
        if qty is None:
            return SimStepOutcome(
                idx,
                action,
                False,
                f"Invalid {cat_short} quantity",
                planet_id=planet.planet_id,
            )
        uid = int(ukey[1:]) if (ukey[1:].isascii() and ukey[1:].isdigit()) else 0
        info = (game_data.SHIPS if is_ship else game_data.DEFENSES)[slug]
        uname = str(info["name"])

        prereq_ok, prereq_err = _wait_for_prereqs(sim, planet, cat_gd, slug, max_clock_s=max_clock_s)
        if not prereq_ok:
            return SimStepOutcome(idx, action, False, prereq_err, planet_id=planet.planet_id, target_name=uname, target_level=qty)

        cost = _unit_step_cost(planet, cat_short, uid, slug, qty, sim.clock_s <= 1e-6)
        ok, _, wait_err = _advance_until_affordable(sim, planet.planet_id, cost, max_clock_s=max_clock_s)
        if not ok:
            return SimStepOutcome(idx, action, False, wait_err, planet_id=planet.planet_id, target_name=uname, target_level=qty, cost=cost)

        wait_s = sim.clock_s - entry_clock
        planet.res_f["pig_iron"] = _sub_res_f(planet.res_f["pig_iron"], cost.pig_iron)
        planet.res_f["metal"] = _sub_res_f(planet.res_f["metal"], cost.metal)
        planet.res_f["kryptonite"] = _sub_res_f(planet.res_f["kryptonite"], cost.kryptonite)
        planet.res_f["spice"] = _sub_res_f(planet.res_f["spice"], cost.spice)

        base_bt = float(info.get("base_build_time_s", 0.0) or 0.0)
        dur_s = _shipyard_duration_s(
            base_bt,
            qty,
            wf_level=planet.building_level(9),
            micro_level=planet.building_level(17),
            speedup=sim.speedup_factor,
        )
        job_start = planet.shipyard_queue[-1].finish_t if planet.shipyard_queue else sim.clock_s
        job_finish = job_start + max(1.0, dur_s)
        planet.shipyard_queue.append(
            _SimShipJob(cat_short, ukey, slug, qty, job_start, job_finish, base_bt)
        )
        if is_ship:
            sim.tutorial_tasks_advanced += _tutorial_progress_match(sim, "BUILD_SHIP", uid)
        exec_t0 = False
        if (
            sim.clock_s <= 1e-6
            and wait_s <= 1e-6
            and planet._source is not None
            and planet._source.building_level(9) >= 1
            and not any(
                m.get("category") == "buildings"
                for m in _fast_unmet_prereqs(
                    cat_gd,
                    slug,
                    planet._source.buildings,
                    sim._source_state.researches,
                )
            )
        ):
            offers = planet._source.ship_offers if is_ship else planet._source.defense_offers
            exec_t0 = any(o.unit_id == uid and o.can_build for o in offers)
        return SimStepOutcome(
            index=idx,
            action=action,
            valid=True,
            planet_id=planet.planet_id,
            target_name=uname,
            target_level=qty,
            cost=cost,
            wait_s=wait_s,
            duration_s=max(1.0, dur_s),
            start_t=job_start,
            finish_t=job_finish,
            executable_at_t0=exec_t0,
        )

    if kind == "THROTTLE_BUILDING":
        if ptype in ("asteroid", "raid"):
            return SimStepOutcome(idx, action, False, f"{ptype} does not support throttle", planet_id=planet.planet_id)
        bid = 2 if action.target_id is None else _parse_building_target_id(action.target_id, -1)
        if bid not in (1, 2, 3, 4, 6):
            return SimStepOutcome(idx, action, False, f"Building b{_safe_action_str(bid)} cannot be throttled", planet_id=planet.planet_id)
        lvl = planet.building_level(bid)
        if lvl <= 0:
            return SimStepOutcome(idx, action, False, f"Building b{_safe_action_str(bid)} not constructed", planet_id=planet.planet_id)
        field_key = f"resource{bid}" if bid <= 4 else "resource5"
        cur_pct = max(0, min(100, _safe_int(planet.economy_rates.get(field_key, 100), 100)))
        target_pct = max(0, min(100, (_safe_int(action.quantity, 0) // 10) * 10))
        if cur_pct == target_pct:
            return SimStepOutcome(idx, action, False, f"{field_key} already at {target_pct}%", planet_id=planet.planet_id)
        planet.economy_rates[field_key] = target_pct
        if bid in (1, 2, 3, 4):
            needed = game_data.mine_energy_needed(bid, lvl)
            old_u = int(round(needed * cur_pct / 100.0))
            new_u = int(round(needed * target_pct / 100.0))
            planet.energy_used = max(0, planet.energy_used + (new_u - old_u))
        elif bid == 6:
            b6_full = float(game_data.building_level_stats(6, lvl).get("energy", 0) or 0)
            planet.b6_energy_raw = b6_full * (target_pct / 100.0)
        bname = str(game_data.BUILDING_BY_ID[bid]["name"])
        return SimStepOutcome(
            index=idx,
            action=action,
            valid=True,
            planet_id=planet.planet_id,
            target_name=f"{bname} {target_pct}%",
            target_level=target_pct,
            start_t=sim.clock_s,
            finish_t=sim.clock_s,
            executable_at_t0=sim.clock_s <= 1e-6,
        )

    if kind == "ATTACK":
        raw_t_coords = _safe_action_str(action.target_coords).strip()
        if not raw_t_coords:
            return SimStepOutcome(idx, action, False, "Missing target_coords", planet_id=planet.planet_id)
        t_coords = normalize_coords(raw_t_coords)
        own_coords = {normalize_coords(p.coords) for p in sim.planets}
        if t_coords in own_coords:
            return SimStepOutcome(idx, action, False, f"Cannot attack own planet {t_coords}", planet_id=planet.planet_id)
        meta = sim.raid_targets_info.get(t_coords)
        if meta is None:
            return SimStepOutcome(idx, action, False, f"Unknown raid target {t_coords}", planet_id=planet.planet_id)
        is_spacia = bool(meta["is_spacia"])
        status_color = _safe_action_str(meta["status_color"]).lower()
        is_inactive = status_color == "yellow"
        if not (is_inactive or is_spacia):
            if not (cfg.allow_pvp_in_az and status_color == "light_red"):
                return SimStepOutcome(idx, action, False, f"Target {t_coords} ({status_color}) disallowed by safety policy", planet_id=planet.planet_id)

        busy_until = sim.fleet_busy_until.get(planet.planet_id, 0.0)
        cd_until = sim.target_raid_cooldown_until.get(t_coords, 0.0)
        launch_t = max(sim.clock_s, busy_until, cd_until)
        if launch_t > sim.clock_s + 1e-6:
            if launch_t > max_clock_s + 1e-6:
                return SimStepOutcome(
                    idx,
                    action,
                    False,
                    f"Fleet or target {t_coords} on raid cooldown beyond horizon",
                    planet_id=planet.planet_id,
                    target_name=t_coords,
                )
            _advance_clock_to(sim, launch_t)

        sim_now = sim.sim_base_ts + sim.clock_s
        if not sim.bash_tracker.can_attack(
            t_coords,
            is_inactive=is_inactive,
            is_spacia=is_spacia,
            timestamp=sim_now,
        ):
            return SimStepOutcome(idx, action, False, f"Bash limit 3/24h reached for {t_coords}", planet_id=planet.planet_id)

        atk_ships = {
            k: max(0, _safe_int(planet.ships.get(k, 0), 0))
            for k in _COMBAT_AND_RAID_SHIP_KEYS
            if _safe_int(planet.ships.get(k, 0), 0) > 0
        }
        if not atk_ships:
            return SimStepOutcome(idx, action, False, "No mobile attack/transport ships stationed on origin", planet_id=planet.planet_id)

        t_res = sim.raid_target_res.get(t_coords, Resources())
        rep = SpyReport(
            target_coords=t_coords,
            owner=meta["owner"],
            resources=t_res,
            fleet=meta["fleet"],
            defense=meta["defense"],
            dug_in=meta["dug_in"],
            is_spacia=is_spacia,
        )
        combat = simulate_raid_outcome(
            atk_ships,
            rep,
            attacker_researches=sim.researches,
            safety_margin=cfg.combat_safety_margin,
        )
        if not combat.safe_to_attack:
            return SimStepOutcome(idx, action, False, combat.reason, planet_id=planet.planet_id, target_name=t_coords)

        tut_fight_ok = _sim_tutorial_fight_pending(sim)
        is_depleted = (t_coords in sim.depleted_targets) or (combat.loot_spice_equivalent <= 0.0)
        exp_loot_sp, eff_win_prob, t_hist = _compute_effective_raid_expectation(
            combat.loot_spice_equivalent,
            combat.cargo_capacity,
            t_coords,
            sim.attack_history_stats,
            target_depleted=is_depleted,
        )
        if not tut_fight_ok:
            if is_depleted or exp_loot_sp <= 0.0:
                return SimStepOutcome(
                    idx,
                    action,
                    False,
                    f"Target {t_coords} depleted or yields 0 loot",
                    planet_id=planet.planet_id,
                    target_name=t_coords,
                )
            min_req = max(0.0, _safe_float_any(getattr(cfg, "raid_min_loot_spice_eq", 1500.0), 1500.0))
            if exp_loot_sp < min_req - 1e-6:
                return SimStepOutcome(
                    idx,
                    action,
                    False,
                    f"Expected raid loot {exp_loot_sp:.0f} SpEq below min {min_req:.0f}",
                    planet_id=planet.planet_id,
                    target_name=t_coords,
                )

        scale = (exp_loot_sp / combat.loot_spice_equivalent) if combat.loot_spice_equivalent > 1e-6 else 0.0
        loot = Resources(
            pig_iron=game_data.safe_scale_round_int(
                combat.expected_loot.pig_iron, scale, max_result=game_data.MAX_JSON_SAFE_INT
            ),
            metal=game_data.safe_scale_round_int(
                combat.expected_loot.metal, scale, max_result=game_data.MAX_JSON_SAFE_INT
            ),
            kryptonite=game_data.safe_scale_round_int(
                combat.expected_loot.kryptonite, scale, max_result=game_data.MAX_JSON_SAFE_INT
            ),
            spice=game_data.safe_scale_round_int(
                combat.expected_loot.spice, scale, max_result=game_data.MAX_JSON_SAFE_INT
            ),
        )
        if loot.is_zero() and not tut_fight_ok:
            return SimStepOutcome(
                idx,
                action,
                False,
                f"Rounded raid loot on {t_coords} is 0",
                planet_id=planet.planet_id,
                target_name=t_coords,
            )

        if t_hist is not None and eff_win_prob < 0.70:
            sim.raid_risk_penalty += (0.70 - eff_win_prob) * 4.0
        else:
            o_stats = sim.attack_history_stats.get("overall") or {}
            o_att = max(0, _safe_int(o_stats.get("attempts", 0), 0))
            o_sr = max(0.0, min(1.0, _safe_float_any(o_stats.get("success_rate", 1.0), 1.0)))
            if o_att >= 2 and o_sr < 0.50:
                sim.raid_risk_penalty += (0.50 - o_sr) * 2.0

        round_trip_s = _compute_raid_round_trip_s(
            planet.coords, t_coords, atk_ships, sim.researches, sim.speedup_factor
        )
        start_t = sim.clock_s
        finish_t = start_t + round_trip_s
        wait_s = start_t - entry_clock

        is_repeat = t_coords in sim.target_raid_cooldown_until
        sim.fleet_busy_until[planet.planet_id] = finish_t
        sim.target_raid_cooldown_until[t_coords] = finish_t + _TARGET_RAID_COOLDOWN_S
        sim.fleet_jobs.append(
            _SimFleetJob(
                planet_id=planet.planet_id,
                target_coords=t_coords,
                loot=loot,
                loot_spice_eq=exp_loot_sp,
                start_t=start_t,
                finish_t=finish_t,
                won=eff_win_prob >= 0.5,
                outcome="win" if eff_win_prob >= 0.5 else "risky",
                is_repeat=is_repeat,
            )
        )
        raw_taken = combat.expected_loot
        taken_pig = min(t_res.pig_iron, max(raw_taken.pig_iron, loot.pig_iron))
        taken_met = min(t_res.metal, max(raw_taken.metal, loot.metal))
        taken_kry = min(t_res.kryptonite, max(raw_taken.kryptonite, loot.kryptonite))
        taken_spi = min(t_res.spice, max(raw_taken.spice, loot.spice))
        rem_res = Resources(
            pig_iron=max(0, t_res.pig_iron - taken_pig),
            metal=max(0, t_res.metal - taken_met),
            kryptonite=max(0, t_res.kryptonite - taken_kry),
            spice=max(0, t_res.spice - taken_spi),
        )
        sim.raid_target_res[t_coords] = rem_res
        if max(rem_res.pig_iron, rem_res.metal, rem_res.kryptonite, rem_res.spice) <= 1 or exp_loot_sp <= 0.0:
            sim.depleted_targets.add(t_coords)
        sim.bash_tracker.record_attack(t_coords, timestamp=sim_now)
        sim.tutorial_tasks_advanced += _tutorial_progress_match(sim, "ATTACK", None)
        return SimStepOutcome(
            index=idx,
            action=action,
            valid=True,
            planet_id=planet.planet_id,
            target_name=t_coords,
            wait_s=wait_s,
            duration_s=round_trip_s,
            start_t=start_t,
            finish_t=finish_t,
            executable_at_t0=(entry_clock <= 1e-6 and start_t <= 1e-6 and cfg.allow_raids),
            raid_loot_spice_eq=round(exp_loot_sp, 3),
        )

    if kind == "SPY":
        if ptype == "raid":
            return SimStepOutcome(idx, action, False, "Raid colony cannot dispatch tutorial spy", planet_id=planet.planet_id)
        if _safe_int(planet.ships.get("s10", 0), 0) <= 0:
            return SimStepOutcome(idx, action, False, "No Spy probe (s10) stationed on origin", planet_id=planet.planet_id)
        raw_tc = _safe_action_str(action.target_coords).strip()
        t_coords = normalize_coords(raw_tc) if raw_tc else ""
        own_coords = {normalize_coords(op.coords) for op in sim.planets}
        if not t_coords or t_coords in own_coords:
            return SimStepOutcome(idx, action, False, "Invalid spy target coordinates", planet_id=planet.planet_id)
        if t_coords in sim.spied_targets:
            return SimStepOutcome(idx, action, False, f"Target {t_coords} already spied", planet_id=planet.planet_id)
        meta = sim.raid_targets_info.get(t_coords)
        if meta is None:
            return SimStepOutcome(idx, action, False, f"Unknown spy target {t_coords}", planet_id=planet.planet_id)
        sim.spied_targets.add(t_coords)
        sim.tutorial_tasks_advanced += _tutorial_progress_match(sim, "SPY", 10)
        return SimStepOutcome(
            index=idx,
            action=action,
            valid=True,
            planet_id=planet.planet_id,
            target_name=t_coords,
            target_level=1,
            start_t=sim.clock_s,
            finish_t=sim.clock_s,
            executable_at_t0=(entry_clock <= 1e-6 and cfg.allow_galaxy_scan),
        )

    if kind == "SCAN_GALAXY":
        gal, sys_num = _parse_gal_sys(_safe_action_str(action.target_coords) or planet.coords)
        if (gal, sys_num) in sim.scanned_systems:
            return SimStepOutcome(idx, action, False, f"System {gal:02d}:{sys_num:03d} already scanned", planet_id=planet.planet_id)
        sim.scanned_systems.add((gal, sys_num))
        if _sim_tutorial_fight_pending(sim) or _sim_tutorial_spy_pending(sim):
            sim.tutorial_tasks_advanced += 1
        return SimStepOutcome(
            index=idx,
            action=action,
            valid=True,
            planet_id=planet.planet_id,
            target_name=f"Galaxy [{gal:02d}:{sys_num:03d}]",
            start_t=sim.clock_s,
            finish_t=sim.clock_s,
            executable_at_t0=sim.clock_s <= 1e-6 and cfg.allow_galaxy_scan,
        )

    if kind == "CLAIM_TUTORIAL":
        if not sim.tutorial_can_complete or sim.tutorial_claimed:
            return SimStepOutcome(idx, action, False, "Tutorial not ready to claim", planet_id=planet.planet_id)
        sim.tutorial_claimed = True
        sim.tutorial_can_complete = False
        ap = sim.active_planet() or planet
        ap.res_f["pig_iron"] += _safe_res_float(sim.tutorial_reward.pig_iron)
        ap.res_f["metal"] += _safe_res_float(sim.tutorial_reward.metal)
        ap.res_f["kryptonite"] += _safe_res_float(sim.tutorial_reward.kryptonite)
        ap.res_f["spice"] += _safe_res_float(sim.tutorial_reward.spice)
        return SimStepOutcome(
            index=idx,
            action=action,
            valid=True,
            planet_id=ap.planet_id,
            target_name="Claim Tutorial",
            start_t=sim.clock_s,
            finish_t=sim.clock_s,
            executable_at_t0=sim.clock_s <= 1e-6,
        )

    return SimStepOutcome(
        idx,
        action,
        False,
        f"Unsupported action kind {_safe_action_str(action.kind)}",
        planet_id=planet.planet_id,
    )


def _balanced_stream_score(m: EmpireMetrics) -> float:
    streams = sorted(
        (
            max(0.0, m.hourly_pig_iron_net) / 6.0,
            max(0.0, m.hourly_metal_net) / 2.5,
            max(0.0, m.hourly_kryptonite_net) / 3.0,
            max(0.0, m.hourly_spice_net),
        )
    )
    return 2.0 * streams[0] + 1.0 * streams[1]


def _queued_build_marginal_preview(
    sim: _SimState,
    cand: SimAction,
    cfg: StrategyConfig,
) -> float:
    """Estimate marginal composite-growth incentive for queued `BUILD` steps before queue completion."""
    if cand.kind != "BUILD":
        return 0.0
    p = sim.planet_by_id.get(cand.planet_id)
    if p is None:
        return 0.0
    bid = _parse_building_target_id(cand.target_id, 0)
    if bid not in (1, 2, 3, 4):
        return 0.0
    cur_lvl = p.effective_building_level(bid)
    nxt_lvl = cur_lvl + 1
    r1 = max(0, _safe_int(sim.researches.get("r1", 0), 0))
    spd = max(1.0, _safe_float_any(getattr(sim, "speedup_factor", 1.0), 1.0))
    if bid == 2:
        delta_hr = (
            _fast_mine_prod(2, nxt_lvl, 0, 0) - _fast_mine_prod(2, cur_lvl, 0, 0)
        ) * spd / 2.5 - (
            _fast_blast_pig_used(nxt_lvl) - _fast_blast_pig_used(cur_lvl)
        ) * spd / 6.0
        cur_stream_eq = sum(max(0.0, _fast_mine_prod(2, pl.building_level(2), 0, 0)) * spd / 2.5 for pl in sim.planets)
    else:
        div = 6.0 if bid == 1 else (3.0 if bid == 3 else 1.0)
        delta_hr = (
            _fast_mine_prod(bid, nxt_lvl, p.temperature, r1)
            - _fast_mine_prod(bid, cur_lvl, p.temperature, r1)
        ) * spd / div
        cur_stream_eq = sum(
            _fast_mine_prod(bid, pl.building_level(bid), pl.temperature, r1) * spd / div
            for pl in sim.planets
        )
    all_streams = [0.0, 0.0, 0.0, 0.0]
    for pl in sim.planets:
        rates, _, _, _, _ = _compute_planet_rates(pl, sim.researches, spd)
        all_streams[0] += max(0.0, rates["pig_iron"]) / 6.0
        all_streams[1] += max(0.0, rates["metal"]) / 2.5
        all_streams[2] += max(0.0, rates["kryptonite"]) / 3.0
        all_streams[3] += max(0.0, rates["spice"])
    min_stream = min(all_streams)
    bottleneck_mult = 2.0 if (not bool(getattr(cfg, "merchant_mode", False)) and cur_stream_eq <= min_stream * 1.15 + 1e-6) else 0.6
    playstyle = _safe_action_str(getattr(cfg, "playstyle", "fleeter") or "fleeter").strip().lower()
    w_inc = 3.0 if playstyle == "miner" else (2.0 if playstyle == "fleeter" else 2.5)
    return w_inc * 0.4 * delta_hr * bottleneck_mult


def _compute_composite_growth(
    m0: EmpireMetrics,
    m1: EmpireMetrics,
    sim: _SimState,
    cfg: StrategyConfig,
    rejected_steps: int,
) -> float:
    playstyle = _safe_action_str(getattr(cfg, "playstyle", "fleeter") or "fleeter").strip().lower()
    if playstyle == "miner":
        w_pts, w_inc, w_flt = 1.0, 3.0, 0.7
    elif playstyle == "fleeter":
        w_pts, w_inc, w_flt = 1.0, 2.0, 2.2
    else:
        w_pts, w_inc, w_flt = 1.0, 2.5, 1.4

    overall = (sim.attack_history_stats or {}).get("overall") or {}
    o_att = max(0, _safe_int(overall.get("attempts", 0), 0))
    o_sr = max(0.0, min(1.0, _safe_float_any(overall.get("success_rate", 1.0), 1.0)))
    telemetry = (sim.attack_history_stats or {}).get("telemetry_summary") or {}
    act_sr = max(0.0, min(1.0, _safe_float_any(telemetry.get("action_success_rate", 1.0), 1.0)))

    # Empirical calibration: high historical win rate supports fleet/raid weight; poor rate shifts to safe growth
    if o_att > 0:
        hist_flt_scale = 0.75 + 0.35 * o_sr
        w_flt *= hist_flt_scale

    d_pts = m1.points - m0.points
    d_inc = m1.hourly_spice_eq - m0.hourly_spice_eq
    d_bal = (
        (_balanced_stream_score(m1) - _balanced_stream_score(m0))
        if (len(sim.planets) > 1 and not bool(getattr(cfg, "merchant_mode", False)))
        else 0.0
    )
    spd = max(1.0, _safe_float_any(getattr(sim, "speedup_factor", 1.0), 1.0))
    loot_scale = max(1.0, spd * 1.25 if spd >= 10.0 else math.sqrt(spd / 2.0))
    pot_delta = max(0.0, m1.potential_raid_spice_eq - m0.potential_raid_spice_eq)
    unique_loot = min(m1.simulated_raid_loot_spice_eq, max(0.0, sim.unique_raids_loot_spice_eq))
    repeat_loot = max(0.0, m1.simulated_raid_loot_spice_eq - unique_loot)
    eff_raid_loot = unique_loot + 0.25 * repeat_loot
    d_raid_fleet = (
        (eff_raid_loot * loot_scale / 280.0)
        + 0.4 * (pot_delta / 1000.0)
        + 0.02 * (m1.fleet_power - m0.fleet_power)
        + 0.0005 * (m1.fleet_cargo_capacity - m0.fleet_cargo_capacity)
    )
    tel_bonus = 0.25 * (act_sr - 0.5) if _safe_int(telemetry.get("action_count", 0), 0) > 0 else 0.0
    penalties = (
        3.0 * sim.energy_deficit_hours
        + 0.5 * sim.overflow_penalty
        + 0.5 * sim.pig_starvation_penalty
        + 1.0 * sim.raid_risk_penalty
        + 5.0 * rejected_steps
        + 0.02 * (sim.clock_s / 3600.0)
    )
    return (
        w_pts * d_pts * 0.5
        + w_inc * (d_inc + 0.85 * d_bal) * 0.4
        + w_flt * d_raid_fleet
        + 35.0 * sim.tutorial_tasks_advanced
        + tel_bonus
        - penalties
    )


def _simulate_on_sim_state(
    sim: _SimState,
    actions: Sequence[SimAction],
    cfg: StrategyConfig,
    warn_ratio: float,
    *,
    horizon_hours: Optional[float] = None,
    initial_metrics: Optional[EmpireMetrics] = None,
) -> tuple[SimulationResult, _SimState, list[_SimState]]:
    m0 = initial_metrics or _compute_sim_metrics(sim, cfg, warn_ratio)
    if horizon_hours is None:
        horizon_s: Optional[float] = None
    else:
        horizon_h = max(0.0, min(87600.0, _safe_float_any(horizon_hours, 24.0)))
        horizon_s = horizon_h * 3600.0
    max_clock_s = horizon_s if horizon_s is not None else _DEFAULT_MAX_WAIT_S

    outcomes: list[SimStepOutcome] = []
    step_states: list[_SimState] = [sim.clone()]
    valid_cnt = 0
    rejected_cnt = 0

    for idx, act in enumerate(actions):
        outcome = _execute_sim_step(sim, idx, act, cfg, max_clock_s=max_clock_s)
        outcomes.append(outcome)
        if outcome.valid:
            valid_cnt += 1
        else:
            rejected_cnt += 1
        step_states.append(sim.clone())

    # Flush queued jobs to completion (or up to `horizon_s` when explicit horizon given)
    if horizon_s is not None:
        _advance_clock_to(sim, max(sim.clock_s, horizon_s))
    else:
        for _ in range(512):
            nxt_t = _next_queue_finish_t(sim)
            if nxt_t is None:
                break
            _advance_clock_to(sim, nxt_t)

    m1 = _compute_sim_metrics(sim, cfg, warn_ratio)
    growth = _compute_composite_growth(m0, m1, sim, cfg, rejected_cnt)
    raid_fleet_delta = (
        m1.simulated_raid_loot_spice_eq
        + (m1.potential_raid_spice_eq - m0.potential_raid_spice_eq)
        + (m1.fleet_power - m0.fleet_power)
    )
    res = SimulationResult(
        initial_metrics=m0,
        final_metrics=m1,
        final_state=sim.to_game_state(),
        step_outcomes=tuple(outcomes),
        valid_steps=valid_cnt,
        rejected_steps=rejected_cnt,
        elapsed_seconds=round(sim.clock_s, 3),
        composite_growth_score=round(growth, 4),
        points_delta=round(m1.points - m0.points, 4),
        hourly_spice_eq_delta=round(m1.hourly_spice_eq - m0.hourly_spice_eq, 3),
        raid_fleet_output_delta=round(raid_fleet_delta, 3),
        fitness=round(growth, 4),
        energy_deficit_hours=round(sim.energy_deficit_hours, 4),
        overflow_penalty=round(sim.overflow_penalty, 4),
        pig_starvation_penalty=round(sim.pig_starvation_penalty, 4),
        tutorial_tasks_advanced=sim.tutorial_tasks_advanced,
    )
    return res, sim, step_states


def simulate_action_sequence(
    state: GameState,
    actions: Sequence[SimAction],
    *,
    config: Optional[StrategyConfig] = None,
    storage_warn_ratio: Optional[float] = None,
    horizon_hours: Optional[float] = None,
    allow_asteroid_research_lab: bool = False,
    raid_stats: Optional[Mapping[str, Any]] = None,
    raid_history: Optional[Sequence[Mapping[str, Any]]] = None,
    attack_history_path: Optional[Path | str] = None,
    now_ts: Optional[float] = None,
    scanned_systems: Optional[Mapping[str, float]] = None,
    throttle_cooldowns: Optional[Mapping[Any, float]] = None,
) -> SimulationResult:
    """Deterministically simulate `actions` forward from `state` without mutating `state`."""
    cfg = config or StrategyConfig()
    warn_ratio = _clamp_finite(
        cfg.storage_warn_ratio if storage_warn_ratio is None else storage_warn_ratio,
        0.85,
        0.05,
        1.0,
    )
    sim = _SimState.from_game_state(
        state,
        allow_asteroid_research_lab=allow_asteroid_research_lab,
        raid_stats=raid_stats,
        raid_history=raid_history,
        attack_history_path=attack_history_path,
        now_ts=now_ts,
        scanned_systems=scanned_systems,
        throttle_cooldowns=throttle_cooldowns,
    )
    result, _, _ = _simulate_on_sim_state(
        sim, actions, cfg, warn_ratio, horizon_hours=horizon_hours
    )
    return result


# ---------------------------------------------------------------------------
# F3 — State Feature Encoder & Pure-Python + Optional-PyTorch Neural Network
# ---------------------------------------------------------------------------


def encode_empire_state(
    state: _SimState | GameState,
    *,
    raid_stats: Optional[Mapping[str, Any]] = None,
    raid_history: Optional[Sequence[Mapping[str, Any]]] = None,
    attack_history_path: Optional[Path | str] = None,
) -> list[float]:
    """Encode a `GameState` or `_SimState` into a normalized 46-element feature vector."""
    if isinstance(state, GameState):
        sim = _SimState.from_game_state(
            state,
            raid_stats=raid_stats,
            raid_history=raid_history,
            attack_history_path=attack_history_path,
        )
        stats_ctx = sim.attack_history_stats
    else:
        sim = state
        if raid_stats is not None or raid_history is not None or attack_history_path is not None:
            stats_ctx = _resolve_complete_attack_history(
                sim._source_state,
                raid_stats=raid_stats,
                raid_history=raid_history,
                attack_history_path=attack_history_path,
            )
        else:
            stats_ctx = sim.attack_history_stats

    planets = sim.planets
    n_p = max(1, len(planets))
    eco_planets = [p for p in planets if not is_raid_planet(p)] or planets
    n_eco = max(1, len(eco_planets))

    sum_res = {rk: 0.0 for rk in _RESOURCE_KEYS}
    max_fill = {rk: 0.0 for rk in _RESOURCE_KEYS}
    net_hr = {rk: 0.0 for rk in _RESOURCE_KEYS}
    min_energy_margin = 2.0
    deficit_cnt = 0
    total_cargo = 0
    total_ships: dict[str, int] = {}
    q_occ = 0.0

    bld_res_sum = 0
    flt_res_sum = 0
    res_res_sum = 0

    for p in planets:
        for rk in _RESOURCE_KEYS:
            sum_res[rk] += max(0.0, p.res_f[rk])
            cap = max(1, p.storage_cap[rk])
            max_fill[rk] = max(max_fill[rk], min(1.5, p.res_f[rk] / cap))
        rates, e_net, e_prod, _, _ = _compute_planet_rates(p, sim.researches, sim.speedup_factor)
        for rk in _RESOURCE_KEYS:
            net_hr[rk] += rates[rk]
        if not is_raid_planet(p):
            margin = (e_prod - p.energy_used) / max(1, p.energy_used)
            min_energy_margin = min(min_energy_margin, margin)
            if e_net < 0:
                deficit_cnt += 1
        total_cargo += fleet_cargo_capacity(p.ships)
        for skey, qty in p.ships.items():
            cnt = max(0, _safe_int(qty, 0))
            if cnt <= 0:
                continue
            slug = SHIP_SLUG_BY_KEY.get(skey)
            unit = game_data.SHIPS.get(slug or "") if slug else None
            if not unit:
                continue
            bc = unit.get("base_cost") or {}
            flt_res_sum += sum(_safe_int(bc.get(rk, 0), 0) for rk in _RESOURCE_KEYS) * cnt
            if skey != "s9":
                total_ships[skey] = total_ships.get(skey, 0) + cnt
        for dkey, qty in p.defense.items():
            cnt = max(0, _safe_int(qty, 0))
            if cnt <= 0:
                continue
            slug = DEFENSE_SLUG_BY_KEY.get(dkey)
            unit = game_data.DEFENSES.get(slug or "") if slug else None
            if not unit:
                continue
            bc = unit.get("base_cost") or {}
            flt_res_sum += sum(_safe_int(bc.get(rk, 0), 0) for rk in _RESOURCE_KEYS) * cnt
        for bkey, lvl in p.buildings.items():
            bid = _parse_building_key_id(bkey)
            if bid is not None:
                bld_res_sum += _cum_building_res_sum(bid, _safe_int(lvl, 0))
        slots = max(1, p.max_build_slots(sim.specs))
        q_occ += len(p.build_queue) / slots

    for rkey, lvl in sim.researches.items():
        res_res_sum += _cum_research_res_sum(rkey, _safe_int(lvl, 0))

    points = _safe_res_float(bld_res_sum + flt_res_sum + res_res_sum, 1e200) / POINTS_PER_RESOURCE_DIVISOR
    spice_eq_hr = game_data.spice_equivalent(
        {
            "pig_iron": max(0.0, net_hr["pig_iron"]),
            "metal": max(0.0, net_hr["metal"]),
            "kryptonite": max(0.0, net_hr["kryptonite"]),
            "spice": max(0.0, net_hr["spice"]),
        }
    )
    pts_hr = sum(max(0.0, net_hr[rk]) for rk in _RESOURCE_KEYS) / POINTS_PER_RESOURCE_DIVISOR

    mobile_ships = {k: total_ships[k] for k in _COMBAT_AND_RAID_SHIP_KEYS if total_ships.get(k, 0) > 0}
    fleet_power = (
        simulate_raid_outcome(
            mobile_ships,
            SpyReport(target_coords="01:001:01"),
            attacker_researches=sim.researches,
            safety_margin=0.0,
        ).attacker_power
        if mobile_ships
        else 0.0
    )

    raid_potential = 0.0
    for coords, res in sim.raid_target_res.items():
        raw_sp = game_data.spice_equivalent(res) * 0.5
        exp_sp, _, _ = _compute_effective_raid_expectation(
            raw_sp, max(total_cargo, int(round(raw_sp)) + 1), coords, stats_ctx
        )
        raid_potential += exp_sp

    mean_b = {
        bid: sum(min(100_000, p.building_level(bid)) for p in eco_planets) / n_eco
        for bid in (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 13, 14)
    }
    max_b8 = min(100_000, max((p.building_level(8) for p in planets), default=0))
    free_ratio = sum(
        (
            _safe_res_float(max(0, p.fields_max - p.fields_used), 1e100)
            / max(1.0, _safe_res_float(max(1, p.fields_max), 1e100))
        )
        if p.fields_max > 0
        else 0.0
        for p in planets
    ) / n_p

    r1 = _safe_res_float(sim.researches.get("r1", 0), 100_000.0)
    r2 = _safe_res_float(sim.researches.get("r2", 0), 100_000.0)
    r_eng = _safe_res_float(
        max(
            _safe_int(sim.researches.get("r4", 0), 0),
            _safe_int(sim.researches.get("r5", 0), 0),
            _safe_int(sim.researches.get("r6", 0), 0),
        ),
        100_000.0,
    )
    r7 = _safe_res_float(sim.researches.get("r7", 0), 100_000.0)
    r_cmb = _safe_res_float(
        _safe_int(sim.researches.get("r8", 0), 0)
        + _safe_int(sim.researches.get("r9", 0), 0)
        + _safe_int(sim.researches.get("r10", 0), 0),
        300_000.0,
    )

    overall = stats_ctx.get("overall") or {}
    per_target = stats_ctx.get("per_target") or {}
    telemetry = stats_ctx.get("telemetry_summary") or {}

    o_attempts = max(0, _safe_int(overall.get("attempts", 0), 0))
    o_sr = max(0.0, min(1.0, _safe_float_any(overall.get("success_rate", 0.0), 0.0)))
    o_tot_sp = max(0.0, _safe_float_any(overall.get("total_loot_spice_eq", 0.0), 0.0))
    o_avg_sp = max(0.0, _safe_float_any(overall.get("avg_loot_spice_eq", 0.0), 0.0))
    o_rec_sp = max(0.0, _safe_float_any(overall.get("recency_weighted_loot_spice_eq", o_avg_sp), o_avg_sp))
    last_atk = overall.get("last_attack")
    if isinstance(last_atk, Mapping):
        o_last_sp = max(0.0, _safe_float_any(last_atk.get("loot_spice_eq", 0.0), 0.0))
        o_last_sig = 1.0 if _is_win_entry(last_atk.get("outcome", ""), o_last_sp) else -1.0
    else:
        o_last_sp = 0.0
        o_last_sig = 0.0

    t_srs = [
        max(0.0, min(1.0, _safe_float_any(v.get("success_rate", 0.0), 0.0)))
        for v in per_target.values()
        if isinstance(v, Mapping)
    ]
    t_loots = [
        0.6 * max(0.0, _safe_float_any(v.get("recency_weighted_loot_spice_eq", 0.0), 0.0))
        + 0.4 * max(0.0, _safe_float_any(v.get("last_loot_spice_eq", 0.0), 0.0))
        for v in per_target.values()
        if isinstance(v, Mapping)
    ]
    target_sr_feat = (0.5 * (sum(t_srs) / len(t_srs)) + 0.5 * max(t_srs)) if t_srs else 0.0
    target_loot_feat = (
        math.log1p(max(0.0, 0.5 * (sum(t_loots) / len(t_loots)) + 0.5 * max(t_loots))) / 15.0
        if t_loots
        else 0.0
    )

    pts_delta = _safe_float_any(telemetry.get("points_delta", telemetry.get("points_trend", 0.0)), 0.0)
    econ_trend = _safe_float_any(telemetry.get("economy_trend", telemetry.get("pts_per_hr", 0.0)), 0.0)
    act_sr = max(0.0, min(1.0, _safe_float_any(telemetry.get("action_success_rate", 1.0), 1.0)))
    spy_cnt = max(len(sim.raid_targets_info), max(0, _safe_int(telemetry.get("spy_targets_count", 0), 0)))

    vec = [
        # [0..3] stockpiles
        math.log1p(sum_res["pig_iron"]) / 20.0,
        math.log1p(sum_res["metal"]) / 20.0,
        math.log1p(sum_res["kryptonite"]) / 20.0,
        math.log1p(sum_res["spice"]) / 20.0,
        # [4..7] warehouse pressure
        max_fill["pig_iron"],
        max_fill["metal"],
        max_fill["kryptonite"],
        max_fill["spice"],
        # [8..11] signed log net hourly rates
        math.copysign(math.log1p(abs(net_hr["pig_iron"])), net_hr["pig_iron"]) / 15.0,
        math.copysign(math.log1p(abs(net_hr["metal"])), net_hr["metal"]) / 15.0,
        math.copysign(math.log1p(abs(net_hr["kryptonite"])), net_hr["kryptonite"]) / 15.0,
        math.copysign(math.log1p(abs(net_hr["spice"])), net_hr["spice"]) / 15.0,
        # [12..13] SpiceEq/h & pts/h
        math.log1p(max(0.0, spice_eq_hr)) / 15.0,
        math.log1p(max(0.0, pts_hr)) / 10.0,
        # [14..15] energy health
        max(-1.0, min(2.0, min_energy_margin if planets else 0.0)),
        deficit_cnt / n_eco,
        # [16..19] mine averages
        mean_b[1] / 40.0,
        mean_b[2] / 40.0,
        mean_b[3] / 40.0,
        mean_b[4] / 40.0,
        # [20..24] power / infra / warehouse
        (mean_b[5] + mean_b[6]) / 40.0,
        mean_b[7] / 30.0,
        max_b8 / 30.0,
        mean_b[9] / 30.0,
        (mean_b[11] + mean_b[12] + mean_b[13] + mean_b[14]) / 80.0,
        # [25] free fields
        free_ratio,
        # [26..30] researches
        r1 / 25.0,
        r2 / 25.0,
        r_eng / 25.0,
        r7 / 25.0,
        r_cmb / 75.0,
        # [31..35] points, fleet, cargo, queues, raid potential
        math.log1p(max(0.0, points)) / 15.0,
        math.log1p(max(0.0, fleet_power)) / 15.0,
        math.log1p(max(0.0, _safe_res_float(total_cargo, 1e100))) / 18.0,
        (q_occ / n_p) + (0.5 if sim.research_job is not None else 0.0),
        math.log1p(max(0.0, raid_potential + sim.raids_loot_spice_eq)) / 15.0,
        # [36..41] overall historical attack stats & latest attack
        math.log1p(_safe_res_float(o_attempts, 1e100)) / 8.0,
        o_sr,
        math.log1p(o_tot_sp) / 18.0,
        math.log1p(0.4 * o_avg_sp + 0.6 * o_rec_sp) / 15.0,
        math.log1p(o_last_sp) / 15.0,
        o_last_sig,
        # [42..43] target-level empirical success rate & recency/last loot
        target_sr_feat,
        target_loot_feat,
        # [44..45] DB telemetry summary features
        0.5 * math.tanh(pts_delta / 1000.0) + 0.5 * math.tanh(econ_trend / 500.0),
        0.5 * act_sr + 0.5 * min(1.0, spy_cnt / 25.0),
    ]
    return [_safe_float(v) for v in vec]


@dataclass(frozen=True)
class TrainingStats:
    """Summary of neural value network rollout training."""

    backend: str
    epochs: int
    sample_count: int
    initial_loss: float
    final_loss: float
    weight_delta_norm: float


def _sanitize_nn_scalar(val: Any, default: float = 0.0, limit: float = 1e6) -> float:
    f = _safe_float_any(val, default)
    if not math.isfinite(f):
        return default
    if f > limit:
        return limit
    if f < -limit:
        return -limit
    return f


def _sanitize_nn_layer_sizes(
    raw: Any,
    *,
    default: tuple[int, ...] = (STATE_FEATURE_DIM, 32, 16, 1),
    require_at_least_two: bool = False,
) -> tuple[int, ...]:
    if isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        if require_at_least_two:
            raise ValueError("MLP requires at least input and output dimensions.")
        return default
    items = list(raw)[:16]
    if len(items) < 2:
        if require_at_least_two:
            raise ValueError("MLP requires at least input and output dimensions.")
        return default
    return tuple(max(1, min(4096, _safe_int(x, 1))) for x in items)


def _sanitize_nn_seed(raw: Any, default: int = 42) -> int:
    return game_data.cap_json_int(_safe_int(raw, default))


class PurePythonMLP:
    """Zero-dependency MLP (`STATE_FEATURE_DIM -> 32 -> 16 -> 1`) with `tanh` hidden layers & analytical MSE backprop."""

    def __init__(
        self,
        layer_sizes: tuple[int, ...] = (STATE_FEATURE_DIM, 32, 16, 1),
        *,
        seed: int = 42,
    ) -> None:
        self.layer_sizes: tuple[int, ...] = _sanitize_nn_layer_sizes(
            layer_sizes, require_at_least_two=True
        )
        self.seed = _sanitize_nn_seed(seed, 42)
        rng = random.Random(self.seed)
        self.weights: list[list[list[float]]] = []
        self.biases: list[list[float]] = []
        for fan_in, fan_out in zip(self.layer_sizes[:-1], self.layer_sizes[1:]):
            limit = math.sqrt(6.0 / (fan_in + fan_out))
            w_mat = [
                [rng.uniform(-limit, limit) for _ in range(fan_in)]
                for _ in range(fan_out)
            ]
            b_vec = [0.0] * fan_out
            self.weights.append(w_mat)
            self.biases.append(b_vec)
        self._step_counter = 0

    def _align_features(self, features: Sequence[float]) -> list[float]:
        in_dim = self.layer_sizes[0]
        aligned = [_safe_float_any(v, 0.0) for v in features[:in_dim]]
        if len(aligned) < in_dim:
            aligned.extend([0.0] * (in_dim - len(aligned)))
        return aligned

    def _forward_cache(self, features: Sequence[float]) -> tuple[float, list[list[float]]]:
        acts: list[list[float]] = [self._align_features(features)]
        last_idx = len(self.weights) - 1
        tanh = math.tanh
        mul = operator.mul
        fsum = sum
        for l_idx, (w_mat, b_vec) in enumerate(zip(self.weights, self.biases)):
            prev_a = acts[-1]
            if l_idx < last_idx:
                cur_a = [tanh(b + fsum(map(mul, row, prev_a))) for row, b in zip(w_mat, b_vec)]
            else:
                cur_a = [b + fsum(map(mul, row, prev_a)) for row, b in zip(w_mat, b_vec)]
            acts.append(cur_a)
        return (acts[-1][0] if math.isfinite(acts[-1][0]) else 0.0), acts

    def predict(self, features: Sequence[float]) -> float:
        out, _ = self._forward_cache(features)
        return out if math.isfinite(out) else 0.0

    def predict_batch(self, feature_batch: Sequence[Sequence[float]]) -> list[float]:
        return [self.predict(f) for f in feature_batch]

    def train_step(
        self,
        features: Sequence[float],
        target: float,
        *,
        lr: float = 0.02,
        weight_decay: float = 1e-4,
    ) -> float:
        lr_safe = max(0.0, min(1.0, _safe_float_any(lr, 0.02)))
        wd_safe = max(0.0, min(1.0, _safe_float_any(weight_decay, 1e-4)))
        pred, acts = self._forward_cache(features)
        tgt = _safe_float_any(target, 0.0)
        err = pred - tgt
        if not math.isfinite(err):
            err = 0.0
        loss = _safe_half_sq(err)
        out_heads = len(self.weights[-1]) if self.weights else 1
        delta = [max(-5.0, min(5.0, err))] + [0.0] * max(0, out_heads - 1)

        for l_idx in range(len(self.weights) - 1, -1, -1):
            w_mat = self.weights[l_idx]
            b_vec = self.biases[l_idx]
            in_a = acts[l_idx]
            out_dim = len(w_mat)
            in_dim = len(in_a)

            if l_idx > 0:
                back = [0.0] * in_dim
                for i in range(out_dim):
                    d_i = delta[i]
                    row = w_mat[i]
                    for j in range(in_dim):
                        back[j] += d_i * _sanitize_nn_scalar(row[j], 0.0)
                prev_delta = [
                    max(-5.0, min(5.0, _safe_float_any(back[j], 0.0) * (1.0 - in_a[j] * in_a[j])))
                    for j in range(in_dim)
                ]
            else:
                prev_delta = []

            for i in range(out_dim):
                d_i = delta[i]
                g_b = 5.0 if d_i > 5.0 else (-5.0 if d_i < -5.0 else d_i)
                b_vec[i] = _sanitize_nn_scalar(_safe_float_any(b_vec[i], 0.0) - lr_safe * g_b, 0.0)
                row = w_mat[i]
                for j in range(in_dim):
                    w_cur = _sanitize_nn_scalar(row[j], 0.0)
                    g_w = d_i * in_a[j] + wd_safe * w_cur
                    if not math.isfinite(g_w):
                        g_w = 0.0
                    elif g_w > 5.0:
                        g_w = 5.0
                    elif g_w < -5.0:
                        g_w = -5.0
                    row[j] = _sanitize_nn_scalar(w_cur - lr_safe * g_w, 0.0)

            delta = prev_delta

        self._step_counter += 1
        return loss

    def train_on_rollouts(
        self,
        samples: Sequence[tuple[Sequence[float], float]],
        *,
        epochs: int = 8,
        lr: float = 0.02,
        weight_decay: float = 1e-4,
    ) -> TrainingStats:
        epochs_safe = max(1, min(10_000, _safe_int(epochs, 8)))
        sample_list = [(self._align_features(x), _safe_float_any(y, 0.0)) for x, y in samples]
        if not sample_list:
            return TrainingStats("python", epochs_safe, 0, 0.0, 0.0, 0.0)

        init_loss = _safe_float(sum(_safe_half_sq(self.predict(x) - y) for x, y in sample_list) / len(sample_list))
        w_before = self.weight_snapshot()
        shuffler = random.Random(self.seed + self._step_counter)
        for _ in range(epochs_safe):
            shuffler.shuffle(sample_list)
            for x, y in sample_list:
                self.train_step(x, y, lr=lr, weight_decay=weight_decay)

        final_loss = _safe_float(sum(_safe_half_sq(self.predict(x) - y) for x, y in sample_list) / len(sample_list))
        w_after = self.weight_snapshot()
        delta_sq = 0.0
        for a, b in zip(w_before, w_after):
            diff = _safe_float_any(b, 0.0) - _safe_float_any(a, 0.0)
            if math.isfinite(diff):
                delta_sq += diff * diff
        delta_norm = _safe_float(math.sqrt(delta_sq) if math.isfinite(delta_sq) else 0.0)
        return TrainingStats(
            backend="python",
            epochs=epochs_safe,
            sample_count=len(sample_list),
            initial_loss=init_loss,
            final_loss=final_loss,
            weight_delta_norm=delta_norm,
        )

    def parameter_count(self) -> int:
        return sum(
            len(w_mat) * len(w_mat[0]) + len(b_vec)
            for w_mat, b_vec in zip(self.weights, self.biases)
        )

    def weight_snapshot(self) -> list[float]:
        flat: list[float] = []
        for w_mat, b_vec in zip(self.weights, self.biases):
            for row in w_mat:
                flat.extend(_sanitize_nn_scalar(v, 0.0) for v in row)
            flat.extend(_sanitize_nn_scalar(v, 0.0) for v in b_vec)
        return flat

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer_sizes": [int(x) for x in self.layer_sizes],
            "seed": game_data.cap_json_int(self.seed),
            "weights": [[[_sanitize_nn_scalar(v, 0.0) for v in row] for row in w_mat] for w_mat in self.weights],
            "biases": [[_sanitize_nn_scalar(v, 0.0) for v in b_vec] for b_vec in self.biases],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PurePythonMLP:
        safe_data = data if isinstance(data, Mapping) else {}
        layer_sizes = _sanitize_nn_layer_sizes(safe_data.get("layer_sizes"))
        seed = _sanitize_nn_seed(safe_data.get("seed", 42), 42)
        inst = cls(layer_sizes=layer_sizes, seed=seed)
        raw_weights = safe_data.get("weights")
        if not isinstance(raw_weights, Sequence) or isinstance(raw_weights, (str, bytes, bytearray)):
            raw_weights = ()
        raw_biases = safe_data.get("biases")
        if not isinstance(raw_biases, Sequence) or isinstance(raw_biases, (str, bytes, bytearray)):
            raw_biases = ()
        clean_weights: list[list[list[float]]] = []
        for l_idx, (fan_in, fan_out) in enumerate(zip(layer_sizes[:-1], layer_sizes[1:])):
            cand_mat = raw_weights[l_idx] if l_idx < len(raw_weights) else ()
            w_mat = (
                cand_mat
                if isinstance(cand_mat, Sequence) and not isinstance(cand_mat, (str, bytes, bytearray))
                else ()
            )
            clean_mat: list[list[float]] = []
            for r_idx in range(fan_out):
                cand_row = w_mat[r_idx] if r_idx < len(w_mat) else ()
                row = (
                    cand_row
                    if isinstance(cand_row, Sequence) and not isinstance(cand_row, (str, bytes, bytearray))
                    else ()
                )
                clean_mat.append(
                    [
                        _sanitize_nn_scalar(row[c_idx] if c_idx < len(row) else 0.0, 0.0)
                        for c_idx in range(fan_in)
                    ]
                )
            clean_weights.append(clean_mat)
        clean_biases: list[list[float]] = []
        for l_idx, fan_out in enumerate(layer_sizes[1:]):
            cand_b = raw_biases[l_idx] if l_idx < len(raw_biases) else ()
            b_vec = (
                cand_b
                if isinstance(cand_b, Sequence) and not isinstance(cand_b, (str, bytes, bytearray))
                else ()
            )
            clean_biases.append(
                [
                    _sanitize_nn_scalar(b_vec[r_idx] if r_idx < len(b_vec) else 0.0, 0.0)
                    for r_idx in range(fan_out)
                ]
            )
        inst.weights = clean_weights
        inst.biases = clean_biases
        return inst


if TORCH_AVAILABLE and nn is not None and torch is not None:  # pragma: no cover - tested when torch installed

    class TorchMLP(nn.Module):  # type: ignore[misc]
        """Optional PyTorch `nn.Module` twin matching `PurePythonMLP`'s architecture."""

        def __init__(
            self,
            layer_sizes: tuple[int, ...] = (STATE_FEATURE_DIM, 32, 16, 1),
            *,
            seed: int = 42,
        ) -> None:
            super().__init__()
            self.layer_sizes = _sanitize_nn_layer_sizes(layer_sizes, require_at_least_two=True)
            self.seed = _sanitize_nn_seed(seed, 42)
            self._epoch_counter = 0
            torch.manual_seed(self.seed & ((1 << 63) - 1))
            layers: list[Any] = []
            for idx, (fan_in, fan_out) in enumerate(zip(self.layer_sizes[:-1], self.layer_sizes[1:])):
                lin = nn.Linear(fan_in, fan_out)
                nn.init.xavier_uniform_(lin.weight)
                nn.init.zeros_(lin.bias)
                layers.append(lin)
                if idx < len(self.layer_sizes) - 2:
                    layers.append(nn.Tanh())
            self.net = nn.Sequential(*layers)

        def forward(self, x: Any) -> Any:
            return self.net(x)

        def _tensor_row(self, features: Sequence[float]) -> Any:
            in_dim = self.layer_sizes[0]
            aligned = [_safe_float_any(v, 0.0) for v in features[:in_dim]]
            if len(aligned) < in_dim:
                aligned.extend([0.0] * (in_dim - len(aligned)))
            return torch.tensor(aligned, dtype=torch.float32)

        def predict(self, features: Sequence[float]) -> float:
            with torch.no_grad():
                out = float(self.forward(self._tensor_row(features)).reshape(-1)[0].item())
                return out if math.isfinite(out) else 0.0

        def predict_batch(self, feature_batch: Sequence[Sequence[float]]) -> list[float]:
            if not feature_batch:
                return []
            with torch.no_grad():
                batch = torch.stack([self._tensor_row(f) for f in feature_batch])
                return [
                    float(v) if math.isfinite(float(v)) else 0.0
                    for v in self.forward(batch)[..., 0].reshape(-1).tolist()
                ]

        def train_step(
            self,
            features: Sequence[float],
            target: float,
            *,
            lr: float = 0.02,
            weight_decay: float = 1e-4,
        ) -> float:
            lr_safe = max(0.0, min(1.0, _safe_float_any(lr, 0.02)))
            wd_safe = max(0.0, min(1.0, _safe_float_any(weight_decay, 1e-4)))
            opt = torch.optim.SGD(self.parameters(), lr=lr_safe, weight_decay=wd_safe)
            opt.zero_grad()
            pred = self.forward(self._tensor_row(features)).reshape(-1)[0]
            tgt = torch.tensor(_safe_float_any(target, 0.0), dtype=torch.float32)
            loss = 0.5 * (pred - tgt) ** 2
            loss.backward()
            nn.utils.clip_grad_value_(self.parameters(), 5.0)
            opt.step()
            return _safe_float_any(loss.item(), 0.0)

        def train_on_rollouts(
            self,
            samples: Sequence[tuple[Sequence[float], float]],
            *,
            epochs: int = 8,
            lr: float = 0.02,
            weight_decay: float = 1e-4,
        ) -> TrainingStats:
            epochs_safe = max(1, min(10_000, _safe_int(epochs, 8)))
            lr_safe = max(0.0, min(1.0, _safe_float_any(lr, 0.02)))
            wd_safe = max(0.0, min(1.0, _safe_float_any(weight_decay, 1e-4)))
            if not samples:
                return TrainingStats("torch", epochs_safe, 0, 0.0, 0.0, 0.0)
            x_mat = torch.stack([self._tensor_row(x) for x, _ in samples])
            y_vec = torch.tensor([_safe_float_any(y, 0.0) for _, y in samples], dtype=torch.float32)
            with torch.no_grad():
                init_loss = _safe_float_any(
                    (0.5 * (self.forward(x_mat)[..., 0] - y_vec) ** 2).mean().item(), 0.0
                )
            w_before = self.weight_snapshot()
            opt = torch.optim.SGD(self.parameters(), lr=lr_safe, weight_decay=wd_safe)
            idx_list = list(range(len(samples)))
            for _ in range(epochs_safe):
                shuffler = random.Random(self.seed + self._epoch_counter)
                self._epoch_counter += 1
                shuffler.shuffle(idx_list)
                for idx in idx_list:
                    opt.zero_grad()
                    pred = self.forward(x_mat[idx]).reshape(-1)[0]
                    loss = 0.5 * (pred - y_vec[idx]) ** 2
                    loss.backward()
                    nn.utils.clip_grad_value_(self.parameters(), 5.0)
                    opt.step()
            with torch.no_grad():
                final_loss = _safe_float_any(
                    (0.5 * (self.forward(x_mat)[..., 0] - y_vec) ** 2).mean().item(), 0.0
                )
            w_after = self.weight_snapshot()
            delta_sq = sum(
                (_safe_float_any(b, 0.0) - _safe_float_any(a, 0.0)) ** 2
                for a, b in zip(w_before, w_after)
            )
            delta_norm = _safe_float(math.sqrt(delta_sq) if math.isfinite(delta_sq) else 0.0)
            return TrainingStats("torch", epochs_safe, len(samples), init_loss, final_loss, delta_norm)

        def parameter_count(self) -> int:
            return sum(p.numel() for p in self.parameters())

        def weight_snapshot(self) -> list[float]:
            flat: list[float] = []
            with torch.no_grad():
                for p in self.parameters():
                    flat.extend(_sanitize_nn_scalar(v, 0.0) for v in p.detach().flatten().tolist())
            return flat

        def to_dict(self) -> dict[str, Any]:
            weights = []
            biases = []
            for mod in self.net:
                if isinstance(mod, nn.Linear):
                    weights.append(
                        [
                            [_sanitize_nn_scalar(v, 0.0) for v in row]
                            for row in mod.weight.detach().tolist()
                        ]
                    )
                    biases.append(
                        [_sanitize_nn_scalar(v, 0.0) for v in mod.bias.detach().tolist()]
                    )
            return {
                "layer_sizes": [int(x) for x in self.layer_sizes],
                "seed": game_data.cap_json_int(self.seed),
                "weights": weights,
                "biases": biases,
            }

        @classmethod
        def from_dict(cls, data: Mapping[str, Any]) -> "TorchMLP":
            safe_data = data if isinstance(data, Mapping) else {}
            layer_sizes = _sanitize_nn_layer_sizes(safe_data.get("layer_sizes"))
            seed = _sanitize_nn_seed(safe_data.get("seed", 42), 42)
            inst = cls(layer_sizes=layer_sizes, seed=seed)
            raw_weights = safe_data.get("weights")
            if not isinstance(raw_weights, Sequence) or isinstance(raw_weights, (str, bytes, bytearray)):
                raw_weights = ()
            raw_biases = safe_data.get("biases")
            if not isinstance(raw_biases, Sequence) or isinstance(raw_biases, (str, bytes, bytearray)):
                raw_biases = ()
            linears = [m for m in inst.net if isinstance(m, nn.Linear)]
            with torch.no_grad():
                for l_idx, lin in enumerate(linears):
                    fan_out, fan_in = int(lin.weight.shape[0]), int(lin.weight.shape[1])
                    cand_mat = raw_weights[l_idx] if l_idx < len(raw_weights) else None
                    if isinstance(cand_mat, Sequence) and not isinstance(cand_mat, (str, bytes, bytearray)):
                        clean_mat = []
                        for r_idx in range(fan_out):
                            cand_row = cand_mat[r_idx] if r_idx < len(cand_mat) else ()
                            row = (
                                cand_row
                                if isinstance(cand_row, Sequence)
                                and not isinstance(cand_row, (str, bytes, bytearray))
                                else ()
                            )
                            clean_mat.append(
                                [
                                    _sanitize_nn_scalar(row[c_idx] if c_idx < len(row) else 0.0, 0.0)
                                    for c_idx in range(fan_in)
                                ]
                            )
                        lin.weight.copy_(torch.tensor(clean_mat, dtype=torch.float32))
                    cand_b = raw_biases[l_idx] if l_idx < len(raw_biases) else None
                    if isinstance(cand_b, Sequence) and not isinstance(cand_b, (str, bytes, bytearray)):
                        clean_b = [
                            _sanitize_nn_scalar(cand_b[r_idx] if r_idx < len(cand_b) else 0.0, 0.0)
                            for r_idx in range(fan_out)
                        ]
                        lin.bias.copy_(torch.tensor(clean_b, dtype=torch.float32))
            return inst

else:
    TorchMLP = None  # type: ignore[assignment,misc]


class NeuralValueNetwork:
    """Unified state-value neural network backed by `PurePythonMLP` or optional `TorchMLP`."""

    def __init__(
        self,
        *,
        backend: str = "auto",
        seed: int = 42,
        layer_sizes: tuple[int, ...] = (STATE_FEATURE_DIM, 32, 16, 1),
    ) -> None:
        safe_sizes = _sanitize_nn_layer_sizes(layer_sizes, require_at_least_two=True)
        safe_seed = _sanitize_nn_seed(seed, 42)
        req = _safe_action_str(backend or "auto").strip().lower()
        if req == "pure":
            req = "python"
        if req == "torch":
            if not TORCH_AVAILABLE or TorchMLP is None:
                raise RuntimeError("PyTorch backend requested ('torch'), but `torch` is not installed.")
            self.backend = "torch"
            self.impl: Any = TorchMLP(layer_sizes=safe_sizes, seed=safe_seed)
        elif req == "auto":
            if TORCH_AVAILABLE and TorchMLP is not None:
                self.backend = "torch"
                self.impl = TorchMLP(layer_sizes=safe_sizes, seed=safe_seed)
            else:
                self.backend = "python"
                self.impl = PurePythonMLP(layer_sizes=safe_sizes, seed=safe_seed)
        elif req == "python":
            self.backend = "python"
            self.impl = PurePythonMLP(layer_sizes=safe_sizes, seed=safe_seed)
        else:
            raise ValueError(f"Unsupported neural backend {_safe_action_str(backend)!r}; expected 'auto', 'python', or 'torch'.")
        self.eval_count: int = 0

    def predict(self, features: Sequence[float]) -> float:
        self.eval_count += 1
        return self.impl.predict(features)

    def predict_batch(self, feature_batch: Sequence[Sequence[float]]) -> list[float]:
        self.eval_count += len(feature_batch)
        return self.impl.predict_batch(feature_batch)

    def train_step(
        self,
        features: Sequence[float],
        target: float,
        *,
        lr: float = 0.02,
        weight_decay: float = 1e-4,
    ) -> float:
        return self.impl.train_step(features, target, lr=lr, weight_decay=weight_decay)

    def train_on_rollouts(
        self,
        samples: Sequence[tuple[Sequence[float], float]],
        *,
        epochs: int = 8,
        lr: float = 0.02,
        weight_decay: float = 1e-4,
    ) -> TrainingStats:
        stats = self.impl.train_on_rollouts(samples, epochs=epochs, lr=lr, weight_decay=weight_decay)
        return TrainingStats(
            backend=self.backend,
            epochs=stats.epochs,
            sample_count=stats.sample_count,
            initial_loss=stats.initial_loss,
            final_loss=stats.final_loss,
            weight_delta_norm=stats.weight_delta_norm,
        )

    def parameter_count(self) -> int:
        return self.impl.parameter_count()

    def weight_snapshot(self) -> list[float]:
        return self.impl.weight_snapshot()

    def to_dict(self) -> dict[str, Any]:
        return self.impl.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, backend: str = "auto") -> NeuralValueNetwork:
        safe_data = data if isinstance(data, Mapping) else {}
        seed = _sanitize_nn_seed(safe_data.get("seed", 42), 42)
        inst = cls(backend=backend, seed=seed, layer_sizes=(2, 1))
        if inst.backend == "torch" and TorchMLP is not None:
            inst.impl = TorchMLP.from_dict(safe_data)
        else:
            inst.impl = PurePythonMLP.from_dict(safe_data)
        return inst


def create_value_network(
    backend: str = "auto",
    *,
    seed: int = 42,
    layer_sizes: tuple[int, ...] = (STATE_FEATURE_DIM, 32, 16, 1),
) -> NeuralValueNetwork:
    """Factory helper creating a `NeuralValueNetwork`."""
    return NeuralValueNetwork(backend=backend, seed=seed, layer_sizes=layer_sizes)


# ---------------------------------------------------------------------------
# F4 — Repaired Genetic Algorithm + Online NN Co-Adaptation Optimizer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationStats:
    """Per-generation telemetry from the genetic algorithm optimizer."""

    generation: int
    best_fitness: float
    mean_fitness: float
    worst_fitness: float
    best_points_gain: float
    best_income_gain: float
    best_raid_value: float
    nn_loss: float

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(
            {
                "generation": int(self.generation),
                "best_fitness": float(self.best_fitness),
                "mean_fitness": float(self.mean_fitness),
                "worst_fitness": float(self.worst_fitness),
                "best_points_gain": float(self.best_points_gain),
                "best_income_gain": float(self.best_income_gain),
                "best_raid_value": float(self.best_raid_value),
                "nn_loss": float(self.nn_loss),
            }
        )


@dataclass(frozen=True)
class OptimizerRunSummary:
    """Complete optimization run summary including evolved sequence, metrics, and NN training stats."""

    actions: tuple["PlannedAction", ...]
    best_chromosome: tuple[SimAction, ...]
    best_simulation: SimulationResult
    generation_history: tuple[GenerationStats, ...]
    initial_best_fitness: float
    final_best_fitness: float
    initial_mean_fitness: float
    final_mean_fitness: float
    nn_initial_loss: float
    nn_final_loss: float
    nn_weight_delta_norm: float
    nn_backend: str
    seed: int
    elapsed_ms: float
    generations: int = 0
    population_size: int = 0
    nn_evaluations: int = 0
    attack_history_stats: dict[str, Any] = field(default_factory=dict)

    @property
    def baseline_fitness(self) -> float:
        return float(self.initial_best_fitness)

    @property
    def best_fitness(self) -> float:
        return float(self.final_best_fitness)

    @property
    def improvement_pct(self) -> float:
        base = float(self.initial_best_fitness)
        best = float(self.final_best_fitness)
        if abs(base) <= 1e-12 and abs(best) <= 1e-12:
            return 0.0
        return ((best - base) / max(1e-6, abs(base))) * 100.0

    def to_dict(self) -> dict[str, Any]:
        gens = int(self.generations) if int(self.generations) > 0 else len(self.generation_history)
        return _json_safe(
            {
                "generations": gens,
                "population_size": int(self.population_size),
                "nn_evaluations": int(self.nn_evaluations),
                "baseline_fitness": float(self.baseline_fitness),
                "best_fitness": float(self.best_fitness),
                "improvement_pct": float(self.improvement_pct),
                "initial_best_fitness": float(self.initial_best_fitness),
                "final_best_fitness": float(self.final_best_fitness),
                "initial_mean_fitness": float(self.initial_mean_fitness),
                "final_mean_fitness": float(self.final_mean_fitness),
                "nn_initial_loss": float(self.nn_initial_loss),
                "nn_final_loss": float(self.nn_final_loss),
                "nn_weight_delta_norm": float(self.nn_weight_delta_norm),
                "nn_backend": str(self.nn_backend),
                "seed": int(self.seed),
                "elapsed_ms": float(self.elapsed_ms),
                "actions_count": len(self.actions),
                "best_chromosome": [
                    {
                        "kind": _safe_action_str(a.kind),
                        "planet_id": game_data.cap_json_int(game_data.safe_int(a.planet_id, 0)),
                        "target_id": _json_safe(a.target_id),
                        "target_coords": _safe_action_str(a.target_coords),
                        "quantity": game_data.cap_json_int(game_data.safe_int(a.quantity, 1)),
                        "reason": _safe_action_str(a.reason),
                    }
                    for a in self.best_chromosome
                ],
                "best_simulation": {
                    "valid_steps": int(self.best_simulation.valid_steps),
                    "rejected_steps": int(self.best_simulation.rejected_steps),
                    "elapsed_seconds": float(self.best_simulation.elapsed_seconds),
                    "composite_growth_score": float(self.best_simulation.composite_growth_score),
                    "points_delta": float(self.best_simulation.points_delta),
                    "hourly_spice_eq_delta": float(self.best_simulation.hourly_spice_eq_delta),
                    "raid_fleet_output_delta": float(self.best_simulation.raid_fleet_output_delta),
                    "nn_terminal_value": float(self.best_simulation.nn_terminal_value),
                    "fitness": float(self.best_simulation.fitness),
                    "energy_deficit_hours": float(self.best_simulation.energy_deficit_hours),
                    "overflow_penalty": float(self.best_simulation.overflow_penalty),
                    "pig_starvation_penalty": float(self.best_simulation.pig_starvation_penalty),
                    "tutorial_tasks_advanced": int(self.best_simulation.tutorial_tasks_advanced),
                    "terminal_points": float(self.best_simulation.final_metrics.points),
                    "terminal_rate_spice_eq": float(self.best_simulation.final_metrics.hourly_spice_eq),
                },
                "generation_history": [g.to_dict() for g in self.generation_history],
                "attack_history_stats": dict(self.attack_history_stats or {}),
            }
        )


def _action_affordable_with_rates(
    rates: Mapping[str, float],
    has_relief: bool,
    planet: _SimPlanet,
    cost: Resources,
) -> bool:
    """Filter out actions blocked by non-positive rates with no in-flight relief."""
    if cost.pig_iron > planet.res_f["pig_iron"] + 1e-6 and rates["pig_iron"] <= 1e-9 and not has_relief:
        return False
    if cost.metal > planet.res_f["metal"] + 1e-6 and rates["metal"] <= 1e-9 and not has_relief:
        return False
    if cost.kryptonite > planet.res_f["kryptonite"] + 1e-6 and rates["kryptonite"] <= 1e-9 and not has_relief:
        return False
    if cost.spice > planet.res_f["spice"] + 1e-6 and rates["spice"] <= 1e-9 and not has_relief:
        return False
    return True


def _legal_candidates(sim: _SimState, cfg: StrategyConfig) -> list[SimAction]:
    """Enumerate valid next `SimAction` choices from `sim` respecting all game & config rules."""
    candidates: list[SimAction] = []
    active = sim.active_planet()
    if active is None:
        return candidates

    planets = list(sim.planets) if cfg.allow_multi_planet else [active]
    allow_build = bool(getattr(cfg, "allow_build", True))
    is_t0 = sim.clock_s <= 1e-6

    if sim.tutorial_can_complete and not sim.tutorial_claimed:
        candidates.append(
            SimAction("CLAIM_TUTORIAL", active.planet_id, reason="Claim tutorial reward")
        )

    planet_rates: dict[int, tuple[dict[str, float], bool]] = {}
    for p in planets:
        r_map, _, _, _, _ = _compute_planet_rates(p, sim.researches, sim.speedup_factor)
        planet_rates[p.planet_id] = (r_map, _has_inflight_rate_relief(sim, p))

    for p in planets:
        p_rates, has_relief = planet_rates[p.planet_id]
        ptype = normalize_planet_type(p)
        if (
            allow_build
            and ptype != "raid"
            and p.fields_max > 0
            and (p.fields_used + len(p.build_queue)) < p.fields_max
        ):
            b_ids: list[int] = []
            if ptype == "asteroid":
                b_ids = [7, 9, 10, 17, 19]
                b8_offer_ok = p._source is not None and any(o.building_id == 8 for o in p._source.building_offers)
                if sim.allow_asteroid_research_lab or b8_offer_ok:
                    b_ids.append(8)
            else:
                b_ids = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 17]

            for bid in b_ids:
                if bid == 2 and cfg.merchant_mode:
                    tut = sim._source_state.tutorial
                    tut_needs_b2 = bool(
                        tut
                        and not tut.can_complete
                        and any(t.category == "building" and t.building_id == 2 and not t.completed for t in tut.tasks)
                    )
                    if not tut_needs_b2:
                        continue
                info = game_data.BUILDING_BY_ID.get(bid)
                if not info:
                    continue
                nxt_lvl = p.effective_building_level(bid) + 1
                max_cap = min(int(info.get("max_table_level", 60) or 60), 60)
                if nxt_lvl > max_cap:
                    continue
                unmet = _fast_unmet_prereqs("buildings", bid, p.buildings, sim.researches)
                if unmet and not _prereq_inflight(sim, p, unmet):
                    continue
                cost = _building_step_cost(p, bid, nxt_lvl, is_t0)
                if not _action_affordable_with_rates(p_rates, has_relief, p, cost):
                    continue
                candidates.append(
                    SimAction("BUILD", p.planet_id, target_id=bid, reason=f"Upgrade {info['name']} to L{nxt_lvl}")
                )

        if (
            cfg.auto_throttle_blast_furnace
            and not cfg.merchant_mode
            and ptype not in ("asteroid", "raid")
            and p.building_level(2) > 0
        ):
            cur_pct = _safe_int(p.economy_rates.get("resource2", 100), 100)
            p_last_t = sim.throttle_cooldowns.get(p.planet_id) or sim.throttle_cooldowns.get((p.planet_id, 2))
            p_last_f = _safe_float_any(p_last_t, -1e18)
            t_cd_active = p_last_t is not None and (sim.sim_base_ts + sim.clock_s - p_last_f) < 300.0
            p_e_net = _compute_planet_rates(p, sim.researches, sim.speedup_factor)[1]
            if not t_cd_active or (p_e_net < 0 and cur_pct > 0):
                if cur_pct > 0:
                    candidates.append(
                        SimAction("THROTTLE_BUILDING", p.planet_id, target_id=2, quantity=0, reason="Pause Blast furnace to 0%")
                    )
                else:
                    candidates.append(
                        SimAction("THROTTLE_BUILDING", p.planet_id, target_id=2, quantity=100, reason="Resume Blast furnace to 100%")
                    )

        if cfg.allow_shipyard and ptype != "raid" and (p.building_level(9) >= 1 or p.effective_building_level(9) >= 1):
            ship_slugs: list[str] = ["solar-cells"]
            norm_targets: dict[str, int] = {}
            for raw_k, raw_cnt in (cfg.shipyard_targets or {}).items():
                k_s = _safe_action_str(raw_k).strip()
                if not k_s or (k_s.isascii() and k_s.isdigit()):
                    continue
                cnt_v = _safe_int(raw_cnt, 0)
                low_k = k_s.lower()
                canon_slug: Optional[str] = None
                if low_k in SHIP_SLUG_BY_KEY:
                    canon_slug = SHIP_SLUG_BY_KEY[low_k]
                else:
                    u_info = game_data.resolve_unit("ships", k_s)
                    if u_info:
                        u_slug = u_info.get("slug")
                        if isinstance(u_slug, str) and u_slug in _SHIP_KEY_BY_SLUG:
                            canon_slug = u_slug
                if canon_slug is not None:
                    norm_targets[canon_slug] = cnt_v
            for slug, tgt_cnt in norm_targets.items():
                if tgt_cnt > 0 and slug not in ship_slugs:
                    ship_slugs.append(slug)
            src_st = getattr(sim, "_source_state", None)
            tut_st = getattr(src_st, "tutorial", None) if src_st is not None else None
            if tut_st is not None and not getattr(tut_st, "can_complete", False):
                for t_task in getattr(tut_st, "tasks", ()) or ():
                    if getattr(t_task, "completed", False):
                        continue
                    t_cat = getattr(t_task, "category", "")
                    if t_cat == "ship":
                        t_bid = game_data.safe_optional_int(getattr(t_task, "building_id", None), None)
                        if t_bid is not None and t_bid > 0:
                            t_slug = SHIP_SLUG_BY_KEY.get(f"s{t_bid}")
                            if t_slug and t_slug not in ship_slugs:
                                ship_slugs.append(t_slug)
                    elif t_cat == "milestone":
                        h_sid = _opt_milestone_helper_ship_id(_safe_action_str(getattr(t_task, "description", "")).lower())
                        if h_sid is not None:
                            h_skey = f"s{h_sid}"
                            has_any_h = any(
                                max(0, _safe_int(pl.ships.get(h_skey, 0), 0)) > 0
                                for pl in planets
                                if not is_raid_planet(pl)
                            )
                            if not has_any_h:
                                h_slug = SHIP_SLUG_BY_KEY.get(h_skey)
                                if h_slug and h_slug not in ship_slugs:
                                    ship_slugs.append(h_slug)
            if cfg.playstyle == "fleeter":
                for fs in ("small-fighter", "small-transport", "starcruiser", "phoenix", "battleship", "stealth-bomber"):
                    if fs not in ship_slugs:
                        ship_slugs.append(fs)
            for slug in ship_slugs:
                skey = _SHIP_KEY_BY_SLUG.get(slug)
                if not skey:
                    continue
                sid = int(skey[1:])
                unmet = _fast_unmet_prereqs("ships", slug, p.buildings, sim.researches)
                if unmet and not _prereq_inflight(sim, p, unmet):
                    continue
                cost = _unit_step_cost(p, "ship", sid, slug, 1, is_t0)
                if not _action_affordable_with_rates(p_rates, has_relief, p, cost):
                    continue
                candidates.append(
                    SimAction("BUILD_SHIP", p.planet_id, target_id=sid, quantity=1, reason=f"Build {game_data.SHIPS[slug]['name']}")
                )

        if cfg.allow_galaxy_scan and ptype != "raid" and _safe_int(p.ships.get("s10", 0), 0) > 0 and _sim_tutorial_spy_pending(sim):
            own_coords_spy = {normalize_coords(op.coords) for op in sim.planets}
            for t_coords, meta in sim.raid_targets_info.items():
                norm_t = normalize_coords(t_coords)
                if norm_t in own_coords_spy or norm_t in sim.spied_targets:
                    continue
                is_spacia = bool(meta["is_spacia"])
                scolor = _safe_action_str(meta["status_color"]).lower()
                if not (scolor == "yellow" or is_spacia or (cfg.allow_pvp_in_az and scolor == "light_red")):
                    continue
                candidates.append(
                    SimAction(
                        "SPY",
                        p.planet_id,
                        target_id=10,
                        quantity=1,
                        target_coords=t_coords,
                        reason=f"Tutorial spy: Send Spy probe (s10) to scout {t_coords} before raiding",
                    )
                )

        if cfg.allow_raids:
            atk_ships = {
                k: max(0, _safe_int(p.ships.get(k, 0), 0))
                for k in _COMBAT_AND_RAID_SHIP_KEYS
                if _safe_int(p.ships.get(k, 0), 0) > 0
            }
            if atk_ships and sim.fleet_busy_until.get(p.planet_id, 0.0) <= sim.clock_s + 1e-6:
                sim_now = sim.sim_base_ts + sim.clock_s
                min_req = max(0.0, _safe_float_any(getattr(cfg, "raid_min_loot_spice_eq", 1500.0), 1500.0))
                tut_fight_ok = _sim_tutorial_fight_pending(sim)
                own_coords = {normalize_coords(op.coords) for op in sim.planets}
                for t_coords, meta in sim.raid_targets_info.items():
                    norm_t = normalize_coords(t_coords)
                    if norm_t in own_coords:
                        continue
                    if not tut_fight_ok and (norm_t in sim.depleted_targets or t_coords in sim.depleted_targets):
                        continue
                    if any(normalize_coords(fj.target_coords) == norm_t for fj in sim.fleet_jobs):
                        continue
                    if _safe_float_any(sim.target_raid_cooldown_until.get(norm_t, sim.target_raid_cooldown_until.get(t_coords, 0.0)), 0.0) > sim.clock_s + 1e-6:
                        continue
                    is_spacia = bool(meta["is_spacia"])
                    scolor = _safe_action_str(meta["status_color"]).lower()
                    is_inact = scolor == "yellow"
                    if not (is_inact or is_spacia or (cfg.allow_pvp_in_az and scolor == "light_red")):
                        continue
                    if not sim.bash_tracker.can_attack(
                        t_coords, is_inactive=is_inact, is_spacia=is_spacia, timestamp=sim_now
                    ):
                        continue
                    rep = SpyReport(
                        target_coords=t_coords,
                        owner=meta["owner"],
                        resources=sim.raid_target_res.get(t_coords, Resources()),
                        fleet=meta["fleet"],
                        defense=meta["defense"],
                        dug_in=meta["dug_in"],
                        is_spacia=is_spacia,
                    )
                    combat = simulate_raid_outcome(
                        atk_ships, rep, attacker_researches=sim.researches, safety_margin=cfg.combat_safety_margin
                    )
                    if combat.safe_to_attack and (combat.loot_spice_equivalent > 0.0 or tut_fight_ok):
                        exp_loot_sp, eff_win_prob, _ = _compute_effective_raid_expectation(
                            combat.loot_spice_equivalent,
                            combat.cargo_capacity,
                            t_coords,
                            sim.attack_history_stats,
                            target_depleted=(t_coords in sim.depleted_targets),
                        )
                        if tut_fight_ok or exp_loot_sp >= min_req - 1e-6:
                            candidates.append(
                                SimAction(
                                    "ATTACK",
                                    p.planet_id,
                                    target_coords=t_coords,
                                    reason=(
                                        f"Tutorial fight raid -> {t_coords}"
                                        if (tut_fight_ok and exp_loot_sp < min_req - 1e-6)
                                        else f"Raid {t_coords} (~{_fmt_sp_int(exp_loot_sp)} SpEq, p={eff_win_prob:.0%})"
                                    ),
                                )
                            )

    if cfg.allow_research:
        lab_planets = [
            p
            for p in planets
            if not is_raid_planet(p)
            and (p.building_level(8) >= 1 or p.effective_building_level(8) >= 1)
        ]
        if lab_planets:
            lab_p = max(lab_planets, key=lambda pl: (pl.building_level(8), 1 if pl.planet_id == active.planet_id else 0))
            lab_rates, lab_relief = planet_rates[lab_p.planet_id]
            for rkey in ("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11", "r13"):
                info = game_data.RESEARCH_BY_ID.get(rkey)
                if not info:
                    continue
                nxt_lvl = int(sim.researches.get(rkey, 0) or 0) + 1
                if nxt_lvl > min(int(info.get("max_table_level", 35) or 35), 35):
                    continue
                unmet = _fast_unmet_prereqs("research", rkey, lab_p.buildings, sim.researches)
                if unmet and not _prereq_inflight(sim, lab_p, unmet):
                    continue
                cost = _research_step_cost(sim, rkey, nxt_lvl, is_t0)
                if not _action_affordable_with_rates(lab_rates, lab_relief, lab_p, cost):
                    continue
                candidates.append(
                    SimAction("RESEARCH", lab_p.planet_id, target_id=int(rkey[1:]), reason=f"Research {info['name']} L{nxt_lvl}")
                )

    if cfg.allow_galaxy_scan:
        if _sim_tutorial_fight_pending(sim) or _sim_tutorial_spy_pending(sim):
            nxt_scan = _next_unscanned_system(active.coords, sim.scanned_systems)
            if nxt_scan is not None:
                s_gal, s_sys = nxt_scan
                candidates.append(
                    SimAction(
                        "SCAN_GALAXY",
                        active.planet_id,
                        target_coords=f"{s_gal:02d}:{s_sys:03d}",
                        reason=f"Scan system {s_gal:02d}:{s_sys:03d} for tutorial milestone",
                    )
                )
        else:
            gal, sys_num = _parse_gal_sys(active.coords)
            if (gal, sys_num) not in sim.scanned_systems:
                candidates.append(
                    SimAction("SCAN_GALAXY", active.planet_id, target_coords=f"{gal:02d}:{sys_num:03d}", reason="Scan home system")
                )

    return candidates


def _candidate_cost_spice_eq(sim: _SimState, act: SimAction) -> float:
    planet = (
        sim.planet_by_id.get(act.planet_id)
        if isinstance(act.planet_id, int) and not isinstance(act.planet_id, bool)
        else None
    )
    if planet is None:
        return 1e9
    if act.kind == "BUILD" and act.target_id is not None:
        bid = _parse_building_target_id(act.target_id, 0)
        if bid <= 0:
            return 1e9
        return game_data.spice_equivalent(_building_step_cost(planet, bid, planet.effective_building_level(bid) + 1, sim.clock_s <= 1e-6))
    if act.kind == "RESEARCH" and act.target_id is not None:
        rk = _resolve_research_key(act.target_id)
        if rk:
            return game_data.spice_equivalent(_research_step_cost(sim, rk, sim.researches.get(rk, 0) + 1, sim.clock_s <= 1e-6))
    if act.kind == "BUILD_SHIP" and act.target_id is not None:
        uk, slug = _resolve_ship_key_and_slug(act.target_id)
        if uk and slug:
            return game_data.spice_equivalent(_unit_step_cost(planet, "ship", int(uk[1:]), slug, act.quantity, sim.clock_s <= 1e-6))
    return 0.0


def _same_action_identity(a: SimAction, b: SimAction) -> bool:
    return (
        a.kind == b.kind
        and a.planet_id == b.planet_id
        and a.target_id == b.target_id
        and normalize_coords(_safe_action_str(a.target_coords)) == normalize_coords(_safe_action_str(b.target_coords))
    )


def _is_action_allowed_by_cfg(sim: _SimState, act: SimAction, cfg: StrategyConfig) -> bool:
    kind = _safe_action_str(act.kind).upper()
    active_id = sim.active_planet_id
    if not cfg.allow_multi_planet and active_id is not None and act.planet_id != active_id:
        return False
    planet = (
        sim.planet_by_id.get(act.planet_id)
        if isinstance(act.planet_id, int) and not isinstance(act.planet_id, bool)
        else None
    )
    if (
        planet is not None
        and is_raid_planet(planet)
        and kind in ("BUILD", "WAIT_RESOURCES", "BUILD_SHIP", "BUILD_DEFENSE", "THROTTLE_BUILDING", "RESEARCH")
    ):
        return False
    if kind == "BUILD":
        if not bool(getattr(cfg, "allow_build", True)):
            return False
        if _parse_building_target_id(act.target_id, 0) == 2 and cfg.merchant_mode:
            tut = sim._source_state.tutorial
            if not (tut and not tut.can_complete and any(t.category == "building" and t.building_id == 2 and not t.completed for t in tut.tasks)):
                return False
        return True
    if kind == "RESEARCH":
        return bool(cfg.allow_research)
    if kind == "BUILD_SHIP":
        return bool(cfg.allow_shipyard)
    if kind == "THROTTLE_BUILDING":
        if _parse_building_target_id(act.target_id, 2) not in (1, 2, 3, 4, 6):
            return False
        return bool(cfg.auto_throttle_blast_furnace and not cfg.merchant_mode)
    if kind == "ATTACK":
        return bool(cfg.allow_raids)
    if kind == "SCAN_GALAXY":
        return bool(cfg.allow_galaxy_scan)
    return kind == "CLAIM_TUTORIAL"


def _repair_and_evaluate(
    chromosome: Sequence[SimAction],
    base_sim: _SimState,
    base_metrics: EmpireMetrics,
    cfg: StrategyConfig,
    warn_ratio: float,
    horizon_steps: int,
    nn_net: NeuralValueNetwork,
    nn_weight: float,
    rng: random.Random,
    *,
    build_final_state: bool = False,
    collect_step_features: bool = False,
) -> tuple[tuple[SimAction, ...], SimulationResult, list[list[float]]]:
    """Repair invalid genes sequentially against forward physics and compute composite + NN fitness."""
    sim = base_sim.clone()
    repaired: list[SimAction] = []
    outcomes: list[SimStepOutcome] = []
    step_features: list[list[float]] = [encode_empire_state(sim)] if collect_step_features else []
    valid_cnt = 0
    rejected_cnt = 0

    for idx in range(horizon_steps):
        gene = chromosome[idx] if idx < len(chromosome) else None
        chosen: Optional[SimAction] = None
        outcome: Optional[SimStepOutcome] = None

        if gene is not None and _is_action_allowed_by_cfg(sim, gene, cfg):
            sim_try = sim.clone()
            out_try = _execute_sim_step(sim_try, idx, gene, cfg, max_clock_s=_DEFAULT_MAX_WAIT_S)
            if out_try.valid:
                sim = sim_try
                chosen = gene
                outcome = out_try

        if chosen is None or outcome is None:
            cands = _legal_candidates(sim, cfg)
            if not cands:
                break
            chosen = rng.choice(cands)
            sim_try = sim.clone()
            outcome = _execute_sim_step(sim_try, idx, chosen, cfg, max_clock_s=_DEFAULT_MAX_WAIT_S)
            if outcome.valid:
                sim = sim_try
            else:
                alt_cands = [c for c in cands if not _same_action_identity(c, chosen)]
                if alt_cands:
                    alt = rng.choice(alt_cands)
                    sim_retry = sim.clone()
                    out_retry = _execute_sim_step(sim_retry, idx, alt, cfg, max_clock_s=_DEFAULT_MAX_WAIT_S)
                    if out_retry.valid:
                        sim = sim_retry
                        chosen = alt
                        outcome = out_retry
                    else:
                        sim = sim_try
                else:
                    sim = sim_try

        repaired.append(chosen)
        outcomes.append(outcome)
        if outcome.valid:
            valid_cnt += 1
        else:
            rejected_cnt += 1
        if collect_step_features:
            step_features.append(encode_empire_state(sim))

    # Flush queued jobs created during the chromosome
    for _ in range(512):
        nxt_t = _next_queue_finish_t(sim)
        if nxt_t is None:
            break
        _advance_clock_to(sim, nxt_t)

    m1 = _compute_sim_metrics(sim, cfg, warn_ratio)
    raw_growth = _compute_composite_growth(base_metrics, m1, sim, cfg, rejected_cnt)
    if not base_sim.raid_targets_info:
        build_pair_counts: dict[tuple[int, int], int] = {}
        for step_a, out_a in zip(repaired, outcomes):
            if out_a.valid and step_a.kind == "BUILD":
                b_tid = _parse_building_target_id(step_a.target_id, 0)
                if b_tid > 0:
                    pair_k = (step_a.planet_id, b_tid)
                    build_pair_counts[pair_k] = build_pair_counts.get(pair_k, 0) + 1
        excess_same_build = sum(max(0, cnt - 2) for cnt in build_pair_counts.values())
        if excess_same_build > 0:
            raw_growth -= 200.0 * excess_same_build
    term_feats = encode_empire_state(sim)
    nn_val = nn_net.predict(term_feats)
    if not collect_step_features:
        step_features = [term_feats]

    raid_fleet_delta = (
        m1.simulated_raid_loot_spice_eq
        + (m1.potential_raid_spice_eq - base_metrics.potential_raid_spice_eq)
        + (m1.fleet_power - base_metrics.fleet_power)
    )
    final_gs = sim.to_game_state() if build_final_state else base_sim._source_state
    sim_res = SimulationResult(
        initial_metrics=base_metrics,
        final_metrics=m1,
        final_state=final_gs,
        step_outcomes=tuple(outcomes),
        valid_steps=valid_cnt,
        rejected_steps=rejected_cnt,
        elapsed_seconds=round(sim.clock_s, 3),
        composite_growth_score=round(raw_growth, 4),
        points_delta=round(m1.points - base_metrics.points, 4),
        hourly_spice_eq_delta=round(m1.hourly_spice_eq - base_metrics.hourly_spice_eq, 3),
        raid_fleet_output_delta=round(raid_fleet_delta, 3),
        nn_terminal_value=round(nn_val, 5),
        fitness=round(raw_growth, 5),
        energy_deficit_hours=round(sim.energy_deficit_hours, 4),
        overflow_penalty=round(sim.overflow_penalty, 4),
        pig_starvation_penalty=round(sim.pig_starvation_penalty, 4),
        tutorial_tasks_advanced=sim.tutorial_tasks_advanced,
    )
    return tuple(repaired), sim_res, step_features


def _make_baseline_cheapest(base_sim: _SimState, cfg: StrategyConfig, horizon_steps: int) -> tuple[SimAction, ...]:
    sim = base_sim.clone()
    seq: list[SimAction] = []
    for idx in range(horizon_steps):
        cands = _legal_candidates(sim, cfg)
        if not cands:
            break
        # Simple myopic baseline: pick cheapest candidate
        cands.sort(key=lambda a: (_candidate_cost_spice_eq(sim, a), a.kind, _safe_action_str(a.target_id)))
        chosen = cands[0]
        seq.append(chosen)
        _execute_sim_step(sim, idx, chosen, cfg, max_clock_s=_DEFAULT_MAX_WAIT_S)
    return tuple(seq)


def _make_baseline_warehouse_or_attack(base_sim: _SimState, cfg: StrategyConfig, horizon_steps: int) -> tuple[SimAction, ...]:
    sim = base_sim.clone()
    seq: list[SimAction] = []
    for idx in range(horizon_steps):
        cands = _legal_candidates(sim, cfg)
        if not cands:
            break
        # Myopic high-ID / warehouse-leaning baseline distinct from optimal ROI
        cands.sort(key=lambda a: (0 if a.kind in ("SCAN_GALAXY", "THROTTLE_BUILDING") else 1, -int(a.target_id or 0) if isinstance(a.target_id, int) else 0))
        chosen = cands[0]
        seq.append(chosen)
        _execute_sim_step(sim, idx, chosen, cfg, max_clock_s=_DEFAULT_MAX_WAIT_S)
    return tuple(seq)


def _attack_candidate_sort_key(
    sim: _SimState,
    cand: SimAction,
    cfg: StrategyConfig,
) -> tuple[float, float, str]:
    p = (
        sim.planet_by_id.get(cand.planet_id)
        if isinstance(cand.planet_id, int) and not isinstance(cand.planet_id, bool)
        else None
    )
    raw_tc = _safe_action_str(cand.target_coords).strip()
    t_coords = normalize_coords(raw_tc) if raw_tc else ""
    meta = sim.raid_targets_info.get(t_coords) or sim.raid_targets_info.get(raw_tc)
    if p is None or meta is None:
        return (0.0, 0.0, t_coords)
    atk_ships = {
        k: _safe_int(p.ships.get(k, 0), 0)
        for k in _COMBAT_AND_RAID_SHIP_KEYS
        if _safe_int(p.ships.get(k, 0), 0) > 0
    }
    if not atk_ships:
        return (0.0, 0.0, t_coords)
    rep = SpyReport(
        target_coords=t_coords,
        owner=meta["owner"],
        resources=sim.raid_target_res.get(t_coords, Resources()),
        fleet=meta["fleet"],
        defense=meta["defense"],
        dug_in=meta["dug_in"],
        is_spacia=bool(meta["is_spacia"]),
    )
    combat = simulate_raid_outcome(
        atk_ships,
        rep,
        attacker_researches=sim.researches,
        safety_margin=cfg.combat_safety_margin,
    )
    if not combat.safe_to_attack or combat.loot_spice_equivalent <= 0.0:
        return (0.0, 0.0, t_coords)
    exp_sp, eff_p, _ = _compute_effective_raid_expectation(
        combat.loot_spice_equivalent,
        combat.cargo_capacity,
        t_coords,
        sim.attack_history_stats,
        target_depleted=(t_coords in sim.depleted_targets),
    )
    return (-round(exp_sp, 3), -round(eff_p, 6), t_coords)


def _make_baseline_raid_and_economy(
    base_sim: _SimState,
    cfg: StrategyConfig,
    horizon_steps: int,
) -> tuple[SimAction, ...]:
    sim = base_sim.clone()
    seq: list[SimAction] = []
    raided_coords: set[str] = set()
    for idx in range(horizon_steps):
        cands = _legal_candidates(sim, cfg)
        if not cands:
            break
        atk_cands = [
            a
            for a in cands
            if a.kind == "ATTACK" and normalize_coords(_safe_action_str(a.target_coords)) not in raided_coords
        ]
        if atk_cands:
            atk_cands.sort(key=lambda a: _attack_candidate_sort_key(sim, a, cfg))
            chosen = atk_cands[0]
            raided_coords.add(normalize_coords(_safe_action_str(chosen.target_coords)))
        else:
            non_b2_builds = [
                a
                for a in cands
                if a.kind == "BUILD" and _parse_building_target_id(a.target_id, 0) != 2
            ]
            if non_b2_builds:
                pool = non_b2_builds
            else:
                non_throttle = [a for a in cands if a.kind != "THROTTLE_BUILDING"]
                pool = non_throttle if non_throttle else cands
            pool.sort(
                key=lambda a: (
                    _candidate_cost_spice_eq(sim, a),
                    a.kind,
                    _safe_action_str(a.target_id),
                )
            )
            chosen = pool[0]
        seq.append(chosen)
        _execute_sim_step(sim, idx, chosen, cfg, max_clock_s=_DEFAULT_MAX_WAIT_S)
    return tuple(seq)


def _make_random_rollout(
    base_sim: _SimState,
    cfg: StrategyConfig,
    horizon_steps: int,
    rng: random.Random,
) -> tuple[SimAction, ...]:
    sim = base_sim.clone()
    seq: list[SimAction] = []
    for idx in range(horizon_steps):
        cands = _legal_candidates(sim, cfg)
        if not cands:
            break
        chosen = rng.choice(cands)
        seq.append(chosen)
        _execute_sim_step(sim, idx, chosen, cfg, max_clock_s=_DEFAULT_MAX_WAIT_S)
    return tuple(seq)


def _collect_rollout_samples(
    step_features: Sequence[Sequence[float]],
    sim_res: SimulationResult,
    *,
    reward_scale: float,
    gamma: float = 0.90,
) -> list[tuple[list[float], float]]:
    if not step_features:
        return []
    terminal_score = sim_res.composite_growth_score
    horizon = max(1, len(step_features) - 1)
    out: list[tuple[list[float], float]] = []
    for t_idx, feats in enumerate(step_features[:-1]):
        rem = horizon - t_idx
        discounted = terminal_score * (gamma ** max(0, rem - 1))
        target = math.tanh(discounted / max(5.0, reward_scale))
        out.append((list(feats), target))
    out.append((list(step_features[-1]), math.tanh(terminal_score / max(5.0, reward_scale))))
    return out


def _nn_mse_loss(nn_net: NeuralValueNetwork, samples: Sequence[tuple[Sequence[float], float]]) -> float:
    if not samples:
        return 0.0
    preds = nn_net.predict_batch([x for x, _ in samples])
    return sum(_safe_half_sq(p - _safe_float_any(y, 0.0)) for p, (_, y) in zip(preds, samples)) / len(samples)


def _evolve_sequences(
    state: GameState,
    cfg: StrategyConfig,
    warn_ratio: float,
    *,
    pop_size: int,
    generations: int,
    horizon_steps: int,
    mutation_rate: float,
    nn_weight: float,
    seed: int,
    nn_backend: str,
    resolved_attack_stats: Optional[dict[str, Any]] = None,
    now_ts: Optional[float] = None,
    scanned_systems: Optional[Mapping[str, float]] = None,
    throttle_cooldowns: Optional[Mapping[Any, float]] = None,
) -> tuple[
    tuple[SimAction, ...],
    SimulationResult,
    list[GenerationStats],
    float,
    float,
    float,
    str,
    int,
]:
    rng = random.Random(seed)
    nn_net = NeuralValueNetwork(backend=nn_backend, seed=seed)
    w_init = nn_net.weight_snapshot()

    base_sim = _SimState.from_game_state(
        state,
        resolved_attack_stats=resolved_attack_stats,
        now_ts=now_ts,
        scanned_systems=scanned_systems,
        throttle_cooldowns=throttle_cooldowns,
    )
    base_metrics = _compute_sim_metrics(base_sim, cfg, warn_ratio)

    # Gen 0: 2 simple myopic baselines (raid+economy when raids enabled & scorable) + (pop_size - 2) seeded random rollouts
    use_raid_seed = bool(cfg.allow_raids) and any(
        c.kind == "ATTACK" for c in _legal_candidates(base_sim, cfg)
    )
    second_baseline = (
        _make_baseline_raid_and_economy(base_sim, cfg, horizon_steps)
        if use_raid_seed
        else _make_baseline_warehouse_or_attack(base_sim, cfg, horizon_steps)
    )
    raw_pop: list[tuple[SimAction, ...]] = [
        _make_baseline_cheapest(base_sim, cfg, horizon_steps),
        second_baseline,
    ]
    while len(raw_pop) < pop_size:
        raw_pop.append(_make_random_rollout(base_sim, cfg, horizon_steps, rng))

    evaluated: list[tuple[tuple[SimAction, ...], SimulationResult, list[list[float]]]] = [
        _repair_and_evaluate(
            chrom,
            base_sim,
            base_metrics,
            cfg,
            warn_ratio,
            horizon_steps,
            nn_net,
            nn_weight,
            rng,
        )
        for chrom in raw_pop
    ]
    evaluated.sort(key=lambda item: item[1].fitness, reverse=True)

    # Compute stable reward scale from Gen 0 trajectories
    abs_scores = sorted(abs(item[1].composite_growth_score) for item in evaluated)
    median_abs = abs_scores[len(abs_scores) // 2] if abs_scores else 10.0
    reward_scale = max(10.0, median_abs * 1.25)

    replay_buffer: list[tuple[list[float], float]] = []
    for elite_chrom, _, _ in evaluated[: min(len(evaluated), 4)]:
        _, e_res, e_feats = _repair_and_evaluate(
            elite_chrom,
            base_sim,
            base_metrics,
            cfg,
            warn_ratio,
            horizon_steps,
            nn_net,
            nn_weight,
            rng,
            collect_step_features=True,
        )
        replay_buffer.extend(_collect_rollout_samples(e_feats, e_res, reward_scale=reward_scale))
    replay_buffer = replay_buffer[-36:]

    nn_initial_loss = _nn_mse_loss(nn_net, replay_buffer)
    t_stats0 = nn_net.train_on_rollouts(replay_buffer, epochs=4, lr=0.025)
    last_nn_loss = t_stats0.final_loss

    def _record_gen_stats(gen_idx: int, pop_eval: list[tuple[tuple[SimAction, ...], SimulationResult, list[list[float]]]], loss_val: float) -> GenerationStats:
        fits = [item[1].fitness for item in pop_eval]
        best_res = pop_eval[0][1]
        return GenerationStats(
            generation=gen_idx,
            best_fitness=best_res.fitness,
            mean_fitness=sum(fits) / max(1, len(fits)),
            worst_fitness=min(fits) if fits else 0.0,
            best_points_gain=best_res.points_delta,
            best_income_gain=best_res.hourly_spice_eq_delta,
            best_raid_value=best_res.raid_fleet_output_delta,
            nn_loss=loss_val,
        )

    history: list[GenerationStats] = [_record_gen_stats(0, evaluated, last_nn_loss)]

    elite_count = max(2, pop_size // 6)
    tournament_k = min(3, len(evaluated))

    def _tournament_pick() -> tuple[SimAction, ...]:
        contenders = rng.sample(evaluated, k=tournament_k)
        contenders.sort(
            key=lambda item: item[1].fitness + 4.0 * max(0.0, nn_weight) * item[1].nn_terminal_value,
            reverse=True,
        )
        return contenders[0][0]

    def _mutate_chromosome(chrom: tuple[SimAction, ...]) -> tuple[SimAction, ...]:
        sim_m = base_sim.clone()
        out_seq: list[SimAction] = []
        for idx in range(horizon_steps):
            gene = chrom[idx] if idx < len(chrom) else None
            do_mut = (gene is None) or (rng.random() < mutation_rate)
            chosen: Optional[SimAction] = None
            if not do_mut and gene is not None and _is_action_allowed_by_cfg(sim_m, gene, cfg):
                sim_try = sim_m.clone()
                out_try = _execute_sim_step(sim_try, idx, gene, cfg, max_clock_s=_DEFAULT_MAX_WAIT_S)
                if out_try.valid:
                    sim_m = sim_try
                    out_seq.append(gene)
                    continue

            cands = _legal_candidates(sim_m, cfg)
            if not cands:
                break
            if do_mut and rng.random() < 0.35 and len(cands) > 1:
                if len(sim_m.planets) > 1:
                    sample_cands = list(rng.sample(cands, k=min(4, len(cands))))
                    top_roi = sorted(
                        (c for c in cands if c.kind == "BUILD"),
                        key=lambda c: _queued_build_marginal_preview(sim_m, c, cfg),
                        reverse=True,
                    )[:2]
                    for tc in top_roi:
                        if tc not in sample_cands:
                            sample_cands.append(tc)
                else:
                    sample_cands = list(rng.sample(cands, k=min(2, len(cands))))
                best_c = sample_cands[0]
                best_val = -1e18
                m_pre = _compute_sim_metrics(sim_m, cfg, warn_ratio)
                for cand in sample_cands:
                    s_tmp = sim_m.clone()
                    out_c = _execute_sim_step(s_tmp, idx, cand, cfg, max_clock_s=_DEFAULT_MAX_WAIT_S)
                    if not out_c.valid:
                        val = -1e9
                    else:
                        m_post = _compute_sim_metrics(s_tmp, cfg, warn_ratio)
                        imm = _compute_composite_growth(m_pre, m_post, s_tmp, cfg, 0)
                        if len(sim_m.planets) > 1 and cand.kind == "BUILD" and abs(imm) < 1e-9:
                            imm = _queued_build_marginal_preview(sim_m, cand, cfg)
                        nn_v = nn_net.predict(encode_empire_state(s_tmp))
                        val = imm + 4.0 * max(0.0, nn_weight) * nn_v
                    if val > best_val:
                        best_val = val
                        best_c = cand
                chosen = best_c
            else:
                chosen = rng.choice(cands)
            out_seq.append(chosen)
            _execute_sim_step(sim_m, idx, chosen, cfg, max_clock_s=_DEFAULT_MAX_WAIT_S)
        return tuple(out_seq)

    for gen_idx in range(1, generations):
        next_evaluated: list[tuple[tuple[SimAction, ...], SimulationResult, list[list[float]]]] = []
        seen_chroms: set[tuple[SimAction, ...]] = set()
        # Preserve top unique elites directly without re-simulating physical trajectory
        for item in evaluated:
            if item[0] in seen_chroms:
                continue
            seen_chroms.add(item[0])
            next_evaluated.append(item)
            if len(next_evaluated) >= elite_count:
                break
        elite_chroms: set[tuple[SimAction, ...]] = set(seen_chroms)

        while len(next_evaluated) < pop_size:
            p1 = _tournament_pick()
            p2 = _tournament_pick()
            if len(p1) >= 2 and len(p2) >= 2 and rng.random() < 0.80:
                cut = rng.randint(1, min(len(p1), len(p2)) - 1)
                child = p1[:cut] + p2[cut:]
            else:
                child = p1
            mutated = _mutate_chromosome(child)
            next_evaluated.append(
                _repair_and_evaluate(mutated, base_sim, base_metrics, cfg, warn_ratio, horizon_steps, nn_net, nn_weight, rng)
            )

        next_evaluated.sort(key=lambda item: item[1].fitness, reverse=True)
        evaluated = next_evaluated

        # Incremental online NN rollout training on top unique non-elite offspring (collecting full step features)
        new_samples: list[tuple[list[float], float]] = []
        rollout_picks: list[tuple[tuple[SimAction, ...], SimulationResult, list[list[float]]]] = []
        seen_rollout: set[tuple[SimAction, ...]] = set()
        for item in evaluated:
            chrom = item[0]
            if chrom in elite_chroms or chrom in seen_rollout:
                continue
            seen_rollout.add(chrom)
            rollout_picks.append(item)
            if len(rollout_picks) >= 3:
                break
        if len(rollout_picks) < min(3, len(evaluated)):
            for item in evaluated:
                chrom = item[0]
                if chrom in seen_rollout:
                    continue
                seen_rollout.add(chrom)
                rollout_picks.append(item)
                if len(rollout_picks) >= min(3, len(evaluated)):
                    break

        for p_chrom, p_res, p_feats in rollout_picks:
            if len(p_feats) <= 1 and horizon_steps >= 1:
                _, full_res, full_feats = _repair_and_evaluate(
                    p_chrom,
                    base_sim,
                    base_metrics,
                    cfg,
                    warn_ratio,
                    horizon_steps,
                    nn_net,
                    nn_weight,
                    rng,
                    collect_step_features=True,
                )
                new_samples.extend(_collect_rollout_samples(full_feats, full_res, reward_scale=reward_scale))
            else:
                new_samples.extend(_collect_rollout_samples(p_feats, p_res, reward_scale=reward_scale))

        if new_samples:
            replay_buffer = (replay_buffer + new_samples)[-36:]
        t_stats = nn_net.train_on_rollouts(replay_buffer, epochs=1, lr=0.02)
        last_nn_loss = t_stats.final_loss
        history.append(_record_gen_stats(gen_idx, evaluated, last_nn_loss))

    best_chrom, _, _ = evaluated[0]
    best_chrom, best_sim_res, _ = _repair_and_evaluate(
        best_chrom,
        base_sim,
        base_metrics,
        cfg,
        warn_ratio,
        horizon_steps,
        nn_net,
        nn_weight,
        rng,
        build_final_state=True,
    )
    w_final = nn_net.weight_snapshot()
    nn_weight_delta_norm = math.sqrt(sum((b - a) ** 2 for a, b in zip(w_init, w_final)))
    return (
        best_chrom,
        best_sim_res,
        history,
        nn_initial_loss,
        last_nn_loss,
        nn_weight_delta_norm,
        nn_net.backend,
        int(nn_net.eval_count),
    )


# ---------------------------------------------------------------------------
# Materializing `SimAction` Sequence -> `list[PlannedAction]` Contract
# ---------------------------------------------------------------------------


def _sub_resources(avail: Resources, cost: Resources) -> Resources:
    return Resources(
        pig_iron=max(0, _safe_int(getattr(avail, "pig_iron", 0), 0) - _safe_int(getattr(cost, "pig_iron", 0), 0)),
        metal=max(0, _safe_int(getattr(avail, "metal", 0), 0) - _safe_int(getattr(cost, "metal", 0), 0)),
        kryptonite=max(0, _safe_int(getattr(avail, "kryptonite", 0), 0) - _safe_int(getattr(cost, "kryptonite", 0), 0)),
        spice=max(0, _safe_int(getattr(avail, "spice", 0), 0) - _safe_int(getattr(cost, "spice", 0), 0)),
    )


def _materialize_planned_actions(
    state: GameState,
    best_chrom: Sequence[SimAction],
    cfg: StrategyConfig,
    warn_ratio: float,
    *,
    attack_history_stats: Optional[Mapping[str, Any]] = None,
    now_ts: Optional[float] = None,
    scanned_systems: Optional[Mapping[str, float]] = None,
    throttle_cooldowns: Optional[Mapping[Any, float]] = None,
) -> list["PlannedAction"]:
    active = state.active_planet()
    if active is None:
        return []

    resolved_stats = (
        dict(attack_history_stats)
        if attack_history_stats is not None
        else _resolve_complete_attack_history(state)
    )
    live_now_ts = _resolve_reference_now_ts(state, resolved_stats, now_ts=now_ts)
    bash_tracker = _seed_bash_tracker_from_history(resolved_stats, live_now_ts)
    recent_scans = _extract_recent_scanned_systems(scanned_systems, live_now_ts)
    active_t_cds = _extract_active_throttle_cooldowns(throttle_cooldowns, live_now_ts)

    sim_target_res: dict[str, Resources] = {}
    for raw_t in state.raid_targets or ():
        if not isinstance(raw_t, Mapping):
            continue
        raw_tc = _safe_action_str(raw_t.get("coords") or raw_t.get("target_coords")).strip()
        if not raw_tc:
            continue
        t_c = normalize_coords(raw_tc)
        rmap = raw_t.get("resources") if isinstance(raw_t.get("resources"), Mapping) else raw_t
        sim_target_res[t_c] = Resources(
            pig_iron=max(0, _safe_int(rmap.get("pig_iron", 0), 0)),
            metal=max(0, _safe_int(rmap.get("metal", 0), 0)),
            kryptonite=max(0, _safe_int(rmap.get("kryptonite", 0), 0)),
            spice=max(0, _safe_int(rmap.get("spice", 0), 0)),
        )
    own_coords = {normalize_coords(p.coords) for p in state.planets}
    raided_coords_this_tick: set[str] = set()
    depleted_target_coords: set[str] = set()

    plan: list["PlannedAction"] = []

    # P0: Hostile alerts + optimal Escape Flight
    active_coords_s = _safe_action_str(active.coords)
    for alert in state.fleet_alerts:
        if alert.hostile:
            plan.append(
                _make_planned_action(
                    priority="P0",
                    kind="ALERT_HOSTILE",
                    planet_id=active.planet_id,
                    planet_coords=active_coords_s,
                    target_name="Escape flight / Fleet Save",
                    reason=f"Hostile fleet detected: {_safe_action_str(alert.summary)}",
                    executable_now=False,
                )
            )
            other_coords = next(
                (p.coords for p in state.planets if p.planet_id != active.planet_id),
                active.coords,
            )
            escape = plan_escape_flight(
                active,
                target_coords=other_coords,
                fuel_reserve_spice=cfg.fleet_save_fuel_reserve_spice,
            )
            if escape.feasible:
                plan.append(
                    _make_planned_action(
                        priority="P0",
                        kind="ESCAPE_FLIGHT",
                        planet_id=active.planet_id,
                        planet_coords=active_coords_s,
                        target_name=f"Save -> {_safe_action_str(escape.target_coords)}",
                        cost=escape.cargo,
                        reason=_safe_action_str(escape.reason),
                        executable_now=bool(cfg.allow_fleetsave),
                    )
                )

    # P2: Claim completed tutorial step immediately
    if state.tutorial and state.tutorial.can_complete:
        plan.append(
            _make_planned_action(
                priority="P2",
                kind="CLAIM_TUTORIAL",
                planet_id=active.planet_id,
                planet_coords=active_coords_s,
                target_name=(
                    f"Tutorial {game_data.fmt_compact_int(state.tutorial.step)}"
                    f"/{game_data.fmt_compact_int(state.tutorial.total_steps)} Reward"
                ),
                reason=f"All tasks complete; claim {_safe_action_str(state.tutorial.reward_text) or 'tutorial reward'}",
                executable_now=True,
            )
        )

    planet_map = {p.planet_id: p for p in state.planets}
    b_offers_by_p = {
        p.planet_id: {
            o_bid: o
            for o in p.building_offers
            if (o_bid := game_data.safe_optional_int(o.building_id, None)) is not None
        }
        for p in state.planets
    }
    s_offers_by_p = {
        p.planet_id: {
            o_uid: o
            for o in p.ship_offers
            if (o_uid := game_data.safe_optional_int(o.unit_id, None)) is not None
        }
        for p in state.planets
    }
    r_offers = {
        rk: o
        for o in state.research_offers
        if (rk := _safe_action_str(getattr(o, "research_key", "") or ""))
    }

    specs_map = state.specs if isinstance(state.specs, Mapping) else {}
    has_cmdr = _safe_int(specs_map.get("construction_commander", 0), 0) > 0
    queued_b_cnt = sum(1 for q in state.active_queues if q.category == "building")
    queued_cnt_by_p: dict[int, int] = {
        p.planet_id: (queued_b_cnt if p.planet_id == active.planet_id else 0)
        for p in state.planets
    }
    max_slots_by_p: dict[int, int] = {
        p.planet_id: (1 if is_asteroid_planet(p) else (6 if has_cmdr else 5))
        for p in state.planets
    }
    avail_res_by_p: dict[int, Resources] = {
        p.planet_id: p.resources for p in state.planets
    }
    research_queue_busy = (
        any(q.category == "research" for q in state.active_queues)
        or any(o.active for o in state.research_offers)
    )
    allow_build = bool(getattr(cfg, "allow_build", True))

    # Track effective building levels on live state (including active queue on active planet, resolving building_id=None)
    live_eff_b: dict[int, dict[str, int]] = {}
    for p in state.planets:
        lvls = {_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in (p.buildings or {}).items()}
        if p.planet_id == active.planet_id:
            for q in state.active_queues:
                if q.category == "building":
                    resolved = _resolve_queue_building(q, lvls)
                    if resolved is not None:
                        q_bid, q_tgt = resolved
                        bk = f"b{q_bid}"
                        lvls[bk] = max(lvls.get(bk, 0), q_tgt)
        live_eff_b[p.planet_id] = lvls

    sim_step_b_lvl: dict[tuple[int, int], int] = {}
    sim_step_r_lvl: dict[str, int] = {_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in (state.researches or {}).items()}
    for r_off in state.research_offers:
        if r_off.active:
            rk = _safe_action_str(r_off.research_key)
            if not rk:
                continue
            cur_r = int(sim_step_r_lvl.get(rk, 0))
            o_tgt = game_data.safe_optional_int(r_off.target_level, None)
            tgt_r = _safe_level(o_tgt, default=cur_r + 1) if (o_tgt is not None and o_tgt > cur_r) else cur_r + 1
            sim_step_r_lvl[rk] = max(cur_r, tgt_r)
    for q in state.active_queues:
        if q.category == "research":
            clean_r = _LEVEL_RE.sub("", _TIMER_RE.sub("", re.sub(r"\(.*?\)", "", _safe_action_str(q.name)))).strip().lower()
            rk = None
            for cand_k, rinfo in game_data.RESEARCH_BY_ID.items():
                if str(rinfo.get("name", "")).strip().lower() == clean_r:
                    rk = str(cand_k)
                    break
            if not rk:
                rk = next(
                    (_safe_action_str(o.research_key) for o in state.research_offers if _safe_action_str(o.name).strip().lower() == clean_r),
                    None,
                )
            if rk:
                cur_r = int(sim_step_r_lvl.get(rk, 0))
                q_tgt = game_data.safe_optional_int(q.target_level, None)
                tgt_r = _safe_level(q_tgt, default=cur_r + 1) if (q_tgt is not None and q_tgt > cur_r) else cur_r + 1
                sim_step_r_lvl[rk] = max(cur_r, tgt_r)

    # Ensure urgent P1 energy or warehouse safety checks on planets claim resources before P2 GA steps
    p1_done_builds: set[tuple[int, int]] = set()
    p1_done_ships: set[tuple[int, int]] = set()
    ship_avail_before: dict[tuple[int, int], Resources] = {}
    planets_to_check = list(state.planets) if cfg.allow_multi_planet else [active]
    for p in planets_to_check:
        if is_raid_planet(p):
            continue
        is_astro_p = is_asteroid_planet(p)
        is_act = p.planet_id == active.planet_id
        offers = b_offers_by_p.get(p.planet_id, {})
        eff_lvls = live_eff_b.get(p.planet_id, {})
        avail_p = avail_res_by_p.get(p.planet_id, p.resources)
        cur_queued = queued_cnt_by_p.get(p.planet_id, 0)
        max_slots = max_slots_by_p.get(p.planet_id, 5)
        f_used = max(0, _safe_int(p.fields_used, 0))
        f_max = max(0, _safe_int(p.fields_max, 0))
        temp_c = _safe_int(p.temperature, 0)
        has_free_field = f_max > 0 and (f_used + cur_queued) < f_max
        queue_has_slot = cur_queued < max_slots

        if p.energy.net < 0 and not is_astro_p:
            offer = offers.get(5)
            cur_5 = eff_lvls.get("b5", p.building_level(5))
            nxt_5 = cur_5 + 1
            offer_tgt5 = game_data.safe_optional_int(offer.target_level, None) if offer else None
            cost = offer.cost if (offer and offer_tgt5 == nxt_5) else _resources_from_dict(game_data.building_cost(5, nxt_5))
            s9_offer = s_offers_by_p.get(p.planet_id, {}).get(9)
            s9_cost = (
                s9_offer.cost
                if (s9_offer is not None and not s9_offer.cost.is_zero())
                else _resources_from_dict((game_data.SHIPS.get("solar-cells") or {}).get("base_cost") or {})
            )
            can_exec_s9_hot = bool(
                temp_c >= cfg.hot_planet_solar_temp_threshold
                and f_max > 0
                and 0 < (f_max - f_used - cur_queued) <= cfg.min_free_fields_warning
                and p.building_level(9) >= 1
                and cfg.allow_shipyard
                and s9_offer is not None
                and s9_offer.can_build
                and not s9_cost.is_zero()
                and avail_p.can_afford(s9_cost)
            )
            e_prod_s = game_data.fmt_compact_int(p.energy.produced)
            e_used_s = game_data.fmt_compact_int(p.energy.used)
            f_used_s = game_data.fmt_compact_int(f_used)
            f_max_s = game_data.fmt_compact_int(f_max)
            temp_s = game_data.fmt_compact_int(temp_c)
            if not has_free_field or can_exec_s9_hot:
                if p.building_level(9) >= 1 and cfg.allow_shipyard:
                    can_exec_s9 = bool(
                        cfg.allow_shipyard
                        and s9_offer is not None
                        and s9_offer.can_build
                        and not s9_cost.is_zero()
                        and avail_p.can_afford(s9_cost)
                    )
                    if can_exec_s9:
                        ship_avail_before.setdefault((p.planet_id, 9), avail_p)
                        avail_res_by_p[p.planet_id] = _sub_resources(avail_p, s9_cost)
                        avail_p = avail_res_by_p[p.planet_id]
                        p1_done_ships.add((p.planet_id, 9))
                    else:
                        ship_avail_before.setdefault((p.planet_id, 9), avail_p)
                    reason_s9 = (
                        f"Negative energy ({e_prod_s}/{e_used_s}) & fields full ({f_used_s}/{f_max_s}); build Solar cells (0 fields)"
                        if not has_free_field
                        else f"Negative energy ({e_prod_s}/{e_used_s}) on hot tight planet ({temp_s}C, {f_used_s}/{f_max_s} fields); build Solar cells (0 fields)"
                    )
                    plan.append(
                        _make_planned_action(
                            priority="P1",
                            kind="BUILD_SHIP",
                            planet_id=p.planet_id,
                            planet_coords=_safe_action_str(p.coords),
                            target_id=9,
                            target_name=(_safe_action_str(s9_offer.name) if s9_offer else "") or "Solar cells",
                            target_level=1,
                            cost=s9_cost,
                            missing=avail_p.missing(s9_cost),
                            reason=reason_s9,
                            executable_now=can_exec_s9,
                        )
                    )
                else:
                    can_throttle_b2 = (
                        bool(cfg.auto_throttle_blast_furnace)
                        and not cfg.merchant_mode
                        and p.building_level(2) > 0
                        and _safe_int((p.economy_rates or {}).get("resource2", 100), 100) > 0
                        and not any(a.planet_id == p.planet_id and a.kind == "THROTTLE_BUILDING" and a.target_id == 2 for a in plan)
                    )
                    if can_throttle_b2:
                        plan.append(
                            _make_planned_action(
                                priority="P1",
                                kind="THROTTLE_BUILDING",
                                planet_id=p.planet_id,
                                planet_coords=_safe_action_str(p.coords),
                                target_id=2,
                                target_name="Blast furnace 0%",
                                target_level=0,
                                reason=f"Negative energy ({e_prod_s}/{e_used_s}) & fields full ({f_used_s}/{f_max_s}); throttle Blast furnace",
                                executable_now=True,
                            )
                        )
                    else:
                        plan.append(
                            _make_planned_action(
                                priority="P1",
                                kind="WAIT_RESOURCES",
                                planet_id=p.planet_id,
                                planet_coords=_safe_action_str(p.coords),
                                target_id=5,
                                target_name="Fusion plant",
                                target_level=nxt_5,
                                cost=cost,
                                missing=avail_p.missing(cost),
                                reason=f"Negative energy ({e_prod_s}/{e_used_s}); planet fields full ({f_used_s}/{f_max_s}) — build Solar cells or Terraformer",
                                executable_now=False,
                                action_href=None,
                            )
                        )
            else:
                prereq_5_ok = not game_data.missing_prerequisites(
                    "buildings", 5, buildings=p.buildings, researches=state.researches
                )
                offer_5_ok = (
                    (offer_tgt5 == nxt_5 and offer.can_build and prereq_5_ok)
                    if offer is not None
                    else (not is_act and cfg.allow_multi_planet and prereq_5_ok)
                )
                can_exec = bool(
                    allow_build
                    and has_free_field
                    and queue_has_slot
                    and not cost.is_zero()
                    and avail_p.can_afford(cost)
                    and offer_5_ok
                )
                if can_exec:
                    avail_res_by_p[p.planet_id] = _sub_resources(avail_p, cost)
                    queued_cnt_by_p[p.planet_id] = cur_queued + 1
                    avail_p = avail_res_by_p[p.planet_id]
                    cur_queued += 1
                    queue_has_slot = cur_queued < max_slots
                    has_free_field = f_max > 0 and (f_used + cur_queued) < f_max
                    eff_lvls["b5"] = max(eff_lvls.get("b5", 0), nxt_5)
                    p1_done_builds.add((p.planet_id, 5))
                plan.append(
                    _make_planned_action(
                        priority="P1",
                        kind="BUILD" if can_exec else "WAIT_RESOURCES",
                        planet_id=p.planet_id,
                        planet_coords=_safe_action_str(p.coords),
                        target_id=5,
                        target_name="Fusion plant",
                        target_level=nxt_5,
                        cost=cost,
                        missing=avail_p.missing(cost),
                        reason=f"Negative energy ({e_prod_s}/{e_used_s}); restore power",
                        executable_now=can_exec,
                        action_href=(_safe_action_str(offer.build_href) or None if (can_exec and offer) else None),
                    )
                )

        for rk, ratio in p.storage_fill_ratios().items():
            if ratio >= warn_ratio and not is_astro_p:
                wh_id, wh_name = _WAREHOUSE_ID_BY_RES[rk]
                has_wh = any(
                    a.planet_id == p.planet_id and a.target_id == wh_id and a.kind in ("BUILD", "WAIT_RESOURCES")
                    for a in plan
                )
                if not has_wh:
                    offer = offers.get(wh_id)
                    cur_w = eff_lvls.get(f"b{wh_id}", p.building_level(wh_id))
                    nxt_w = cur_w + 1
                    offer_tgt_w = game_data.safe_optional_int(offer.target_level, None) if offer else None
                    cost = offer.cost if (offer and offer_tgt_w == nxt_w) else _resources_from_dict(game_data.building_cost(wh_id, nxt_w))
                    prereq_w_ok = not game_data.missing_prerequisites(
                        "buildings", wh_id, buildings=p.buildings, researches=state.researches
                    )
                    offer_w_ok = (
                        (offer_tgt_w == nxt_w and offer.can_build)
                        if offer is not None
                        else (not is_act and cfg.allow_multi_planet and prereq_w_ok)
                    )
                    can_exec = bool(
                        allow_build
                        and has_free_field
                        and queue_has_slot
                        and not cost.is_zero()
                        and avail_p.can_afford(cost)
                        and offer_w_ok
                    )
                    if can_exec:
                        avail_res_by_p[p.planet_id] = _sub_resources(avail_p, cost)
                        queued_cnt_by_p[p.planet_id] = cur_queued + 1
                        avail_p = avail_res_by_p[p.planet_id]
                        cur_queued += 1
                        queue_has_slot = cur_queued < max_slots
                        has_free_field = f_max > 0 and (f_used + cur_queued) < f_max
                        eff_lvls[f"b{wh_id}"] = max(eff_lvls.get(f"b{wh_id}", 0), nxt_w)
                        p1_done_builds.add((p.planet_id, wh_id))
                    plan.append(
                        _make_planned_action(
                            priority="P1",
                            kind="BUILD" if can_exec else "WAIT_RESOURCES",
                            planet_id=p.planet_id,
                            planet_coords=_safe_action_str(p.coords),
                            target_id=wh_id,
                            target_name=wh_name,
                            target_level=nxt_w,
                            cost=cost,
                            missing=avail_p.missing(cost),
                            reason=f"{rk.replace('_', ' ').title()} storage at {ratio * 100:.0f}% (5% cap risk)",
                            executable_now=can_exec,
                            action_href=(_safe_action_str(offer.build_href) or None if (can_exec and offer) else None),
                        )
                    )

    own_fleet_in_flight = any(getattr(q, "category", "") == "fleet" for q in (state.active_queues or ()))
    tut_obj = state.tutorial if (state.tutorial is not None and not state.tutorial.can_complete) else None
    tutorial_fight_task = _opt_tutorial_fight_task(tut_obj)
    tutorial_spy_task = _opt_tutorial_spy_task(tut_obj)
    tutorial_ship_ids: set[int] = set()
    deferred_non_urgent_fusion_pids: set[int] = set()
    if tut_obj is not None:
        for t_item in tut_obj.tasks:
            if t_item.completed:
                continue
            if t_item.category == "ship":
                t_sid = game_data.safe_optional_int(t_item.building_id, None)
                if t_sid is not None and t_sid > 0:
                    tutorial_ship_ids.add(t_sid)
            elif t_item.category == "milestone":
                h_sid = _opt_milestone_helper_ship_id(_safe_action_str(t_item.description).lower())
                if h_sid is not None:
                    tutorial_ship_ids.add(h_sid)

    def _emit_opt_build_step(
        tgt_planet: PlanetState,
        bid_val: int,
        reason_str: str,
        prio_str: str = "P2",
    ) -> Optional["PlannedAction"]:
        b_info = game_data.BUILDING_BY_ID.get(bid_val)
        if not b_info:
            return None
        p_t = normalize_planet_type(tgt_planet)
        b8_ok = bid_val == 8 and any(
            game_data.safe_optional_int(o.building_id, None) == 8
            for o in tgt_planet.building_offers
        )
        if not is_building_allowed_on_planet_type(p_t, bid_val, has_b8_offer=b8_ok):
            return None
        is_p_act = tgt_planet.planet_id == active.planet_id
        p_offers_m = b_offers_by_p.get(tgt_planet.planet_id, {})
        b_offer = p_offers_m.get(bid_val)
        base_e = int(live_eff_b[tgt_planet.planet_id].get(f"b{bid_val}", tgt_planet.building_level(bid_val)))
        cur_s_lvl = sim_step_b_lvl.get((tgt_planet.planet_id, bid_val), base_e)
        nxt_s_lvl = cur_s_lvl + 1
        sim_step_b_lvl[(tgt_planet.planet_id, bid_val)] = nxt_s_lvl
        o_tgt_b = game_data.safe_optional_int(b_offer.target_level, None) if b_offer else None
        b_label = (
            _safe_action_str(b_offer.name)
            if (b_offer and b_offer.name and o_tgt_b == nxt_s_lvl)
            else ""
        ) or str(b_info["name"])
        av_p = avail_res_by_p.get(tgt_planet.planet_id, tgt_planet.resources)
        c_q = queued_cnt_by_p.get(tgt_planet.planet_id, 0)
        m_s = max_slots_by_p.get(tgt_planet.planet_id, 5)
        f_u = max(0, _safe_int(tgt_planet.fields_used, 0))
        f_m = max(0, _safe_int(tgt_planet.fields_max, 0))
        free_f = f_m > 0 and (f_u + c_q) < f_m
        slot_ok = c_q < m_s
        prereq_ok_now = not game_data.missing_prerequisites(
            "buildings", bid_val, buildings=tgt_planet.buildings, researches=state.researches
        )
        if b_offer is not None and o_tgt_b == nxt_s_lvl:
            c_res = b_offer.cost
            can_ex = bool(
                allow_build
                and free_f
                and slot_ok
                and prereq_ok_now
                and b_offer.can_build
                and av_p.can_afford(c_res)
            )
            if can_ex:
                avail_res_by_p[tgt_planet.planet_id] = _sub_resources(av_p, c_res)
                queued_cnt_by_p[tgt_planet.planet_id] = c_q + 1
            act_out = _make_planned_action(
                priority=prio_str,
                kind="BUILD" if can_ex else "WAIT_RESOURCES",
                planet_id=tgt_planet.planet_id,
                planet_coords=_safe_action_str(tgt_planet.coords),
                target_id=bid_val,
                target_name=b_label,
                target_level=nxt_s_lvl,
                cost=c_res,
                missing=av_p.missing(c_res),
                reason=reason_str if can_ex else f"{reason_str} (waiting/queued)",
                executable_now=can_ex,
                action_href=(_safe_action_str(b_offer.build_href) or None) if can_ex else None,
            )
        else:
            c_res = _resources_from_dict(game_data.building_cost(bid_val, nxt_s_lvl))
            rem_ok = bool(
                not is_p_act
                and cfg.allow_multi_planet
                and p_t != "raid"
                and allow_build
                and free_f
                and slot_ok
                and nxt_s_lvl == base_e + 1
                and not c_res.is_zero()
                and av_p.can_afford(c_res)
                and prereq_ok_now
            )
            if rem_ok:
                avail_res_by_p[tgt_planet.planet_id] = _sub_resources(av_p, c_res)
                queued_cnt_by_p[tgt_planet.planet_id] = c_q + 1
            act_out = _make_planned_action(
                priority=prio_str,
                kind="BUILD" if rem_ok else "WAIT_RESOURCES",
                planet_id=tgt_planet.planet_id,
                planet_coords=_safe_action_str(tgt_planet.coords),
                target_id=bid_val,
                target_name=b_label,
                target_level=nxt_s_lvl,
                cost=c_res,
                missing=av_p.missing(c_res),
                reason=reason_str if rem_ok else f"{reason_str} (future/queued upgrade)",
                executable_now=rem_ok,
                action_href=None,
            )
        plan.append(act_out)
        p1_done_builds.add((tgt_planet.planet_id, bid_val))
        return act_out

    def _emit_opt_research_step(
        tgt_planet: PlanetState,
        rkey_val: str,
        reason_str: str,
        prio_str: str = "P2",
    ) -> Optional["PlannedAction"]:
        nonlocal research_queue_busy
        r_info = game_data.RESEARCH_BY_ID.get(rkey_val)
        if not r_info:
            return None
        rid_v = int(rkey_val[1:])
        cur_rv = int(sim_step_r_lvl.get(rkey_val, _safe_int((state.researches or {}).get(rkey_val, 0), 0)))
        tgt_rv = cur_rv + 1
        sim_step_r_lvl[rkey_val] = tgt_rv
        r_off = r_offers.get(rkey_val)
        r_label = (_safe_action_str(r_off.name) if r_off and r_off.name else "") or str(r_info["name"])
        av_p = avail_res_by_p.get(tgt_planet.planet_id, tgt_planet.resources)
        o_tgt_r = game_data.safe_optional_int(r_off.target_level, None) if r_off is not None else None
        is_p_act = tgt_planet.planet_id == active.planet_id
        p_t = normalize_planet_type(tgt_planet)
        has_lab_v = tgt_planet.building_level(8) >= 1
        prereq_r_v = not _fast_unmet_prereqs("research", rkey_val, tgt_planet.buildings, state.researches)
        if r_off is not None and o_tgt_r == tgt_rv:
            c_res = r_off.cost
            can_ex = bool(
                is_p_act
                and p_t != "raid"
                and has_lab_v
                and prereq_r_v
                and bool(cfg.allow_research)
                and r_off.can_research
                and not research_queue_busy
                and av_p.can_afford(c_res)
            )
            href_v = (_safe_action_str(r_off.research_href) or None) if can_ex else None
        else:
            c_res = _resources_from_dict(_research_level_stats(r_info, tgt_rv).get("cost") or {})
            can_ex = False
            href_v = None
        if can_ex:
            avail_res_by_p[tgt_planet.planet_id] = _sub_resources(av_p, c_res)
            research_queue_busy = True
        act_out = _make_planned_action(
            priority=prio_str,
            kind="RESEARCH",
            planet_id=tgt_planet.planet_id,
            planet_coords=_safe_action_str(tgt_planet.coords),
            target_id=rid_v,
            target_name=r_label,
            target_level=tgt_rv,
            cost=c_res,
            missing=av_p.missing(c_res),
            reason=reason_str,
            executable_now=can_ex,
            action_href=href_v,
        )
        plan.append(act_out)
        return act_out

    if tut_obj is not None:
        tut_step_s = game_data.fmt_compact_int(tut_obj.step)
        tut_total_s = game_data.fmt_compact_int(tut_obj.total_steps)
        queued_b_by_id: dict[int, int] = {}
        for q_item in state.active_queues:
            if q_item.category == "building":
                q_bid_i = game_data.safe_optional_int(q_item.building_id, None)
                if q_bid_i is not None:
                    queued_b_by_id[q_bid_i] = queued_b_by_id.get(q_bid_i, 0) + 1
        active_r_inc: dict[str, int] = {}
        for rk_i, sim_rl in sim_step_r_lvl.items():
            base_rl = max(0, _safe_int((state.researches or {}).get(rk_i, 0), 0))
            if sim_rl > base_rl:
                active_r_inc[rk_i] = sim_rl - base_rl

        emitted_prereq_b: set[tuple[int, int]] = set()
        emitted_prereq_r: set[str] = set()

        def _emit_prereq_steps_for(
            tgt_p: PlanetState,
            cat_s: str,
            slug_or_id_v: int | str,
            goal_lbl: str,
        ) -> int:
            eff_b_map = dict(live_eff_b.get(tgt_p.planet_id, {}))
            for (sp_pid, sp_bid), sp_lvl in sim_step_b_lvl.items():
                if sp_pid == tgt_p.planet_id:
                    eff_b_map[f"b{sp_bid}"] = max(eff_b_map.get(f"b{sp_bid}", 0), sp_lvl)
            steps_req = _opt_resolve_prereq_chain(cat_s, slug_or_id_v, goal_lbl, eff_b_map, sim_step_r_lvl)
            cnt_emitted = 0
            for req_s in steps_req:
                if req_s["category"] == "buildings":
                    b_u = game_data.resolve_unit("buildings", req_s["slug"])
                    req_bid = int(b_u["id"]) if (b_u and b_u.get("id") is not None) else 0
                    if req_bid <= 0 or (tgt_p.planet_id, req_bid) in emitted_prereq_b:
                        continue
                    emitted_prereq_b.add((tgt_p.planet_id, req_bid))
                    pr_reason = (
                        f"Tutorial {tut_step_s}/{tut_total_s}: unlock {req_s['name']} "
                        f"({game_data.fmt_compact_int(req_s['current'])}/{game_data.fmt_compact_int(req_s['required'])}) for {goal_lbl}"
                    )
                    if _emit_opt_build_step(tgt_p, req_bid, pr_reason) is not None:
                        cnt_emitted += 1
                elif req_s["category"] == "research":
                    r_u = game_data.resolve_unit("research", req_s["slug"])
                    req_rk = str(r_u["id"]) if (r_u and r_u.get("id")) else ""
                    if not req_rk or req_rk in emitted_prereq_r:
                        continue
                    emitted_prereq_r.add(req_rk)
                    pr_reason = (
                        f"Tutorial {tut_step_s}/{tut_total_s}: unlock {req_s['name']} "
                        f"({game_data.fmt_compact_int(req_s['current'])}/{game_data.fmt_compact_int(req_s['required'])}) for {goal_lbl}"
                    )
                    if _emit_opt_research_step(tgt_p, req_rk, pr_reason) is not None:
                        cnt_emitted += 1
            return cnt_emitted

        # 1) Building & Research tutorial tasks on eligible planet
        for tut_p in planets_to_check:
            p_t_tut = normalize_planet_type(tut_p)
            if p_t_tut == "raid":
                continue
            is_tut_act = tut_p.planet_id == active.planet_id
            eff_b_tut = live_eff_b.get(tut_p.planet_id, {})
            lab_eff_tut = eff_b_tut.get("b8", tut_p.building_level(8))
            b8_off_tut = any(game_data.safe_optional_int(o.building_id, None) == 8 for o in tut_p.building_offers)
            can_res_tut = p_t_tut != "asteroid" or lab_eff_tut >= 1 or b8_off_tut

            b_tasks_tut: list[tuple[Any, int]] = []
            r_tasks_tut: list[tuple[Any, int]] = []
            for t_item in tut_obj.tasks:
                if t_item.completed:
                    continue
                t_c = max(0, _safe_int(t_item.current, 0))
                t_g = max(0, _safe_int(t_item.target, 0))
                if t_item.category == "research" and t_item.research_id:
                    rk_t = _safe_action_str(t_item.research_id).strip()
                    eff_rc = t_c + active_r_inc.get(rk_t, 0)
                    if can_res_tut and eff_rc < t_g:
                        r_tasks_tut.append((t_item, eff_rc))
                elif t_item.category == "building" and t_item.building_id is not None:
                    t_bid_v = game_data.safe_optional_int(t_item.building_id, None)
                    if t_bid_v is not None and t_bid_v > 0 and is_building_allowed_on_planet_type(
                        p_t_tut, t_bid_v, has_b8_offer=(t_bid_v == 8 and b8_off_tut)
                    ):
                        eff_bc = t_c + (queued_b_by_id.get(t_bid_v, 0) if is_tut_act else 0)
                        if eff_bc < t_g:
                            b_tasks_tut.append((t_item, eff_bc))

            if not b_tasks_tut and not r_tasks_tut:
                continue

            proj_e_net = _safe_int(tut_p.energy.net, 0)
            if is_tut_act:
                sim_e_lvls: dict[int, int] = {}
                for q_j in state.active_queues:
                    if q_j.category == "building" and q_j.building_id is not None:
                        qb_i = game_data.safe_optional_int(q_j.building_id, None)
                        if qb_i is not None:
                            c_l = sim_e_lvls.get(qb_i, tut_p.building_level(qb_i))
                            q_tl = game_data.safe_optional_int(q_j.target_level, None)
                            n_l = q_tl if (q_tl is not None and q_tl > c_l) else c_l + 1
                            for st_l in range(c_l + 1, min(n_l, 101) + 1):
                                proj_e_net += game_data.building_energy_delta(qb_i, st_l)
                            sim_e_lvls[qb_i] = n_l

            starving_mine = False
            for t_item, _ in b_tasks_tut:
                bid_chk = game_data.safe_optional_int(t_item.building_id, 0) or 0
                if bid_chk in (5, 6):
                    continue
                nxt_chk = eff_b_tut.get(f"b{bid_chk}", tut_p.building_level(bid_chk)) + 1
                ed_chk = game_data.building_energy_delta(bid_chk, nxt_chk)
                if ed_chk < 0 and (proj_e_net + ed_chk) < 0:
                    starving_mine = True
                    break

            need_pwr_first = proj_e_net <= 0 or starving_mine
            has_non_fusion_b = any(
                (game_data.safe_optional_int(ti.building_id, 0) or 0) != 5 for ti, _ in b_tasks_tut
            )
            if not need_pwr_first and has_non_fusion_b:
                deferred_non_urgent_fusion_pids.add(tut_p.planet_id)

            def _tut_b_key(pair: tuple[Any, int]) -> tuple[int, int, int]:
                ti, ef = pair
                b_v = game_data.safe_optional_int(ti.building_id, 99) or 99
                if b_v == 5 and need_pwr_first:
                    bkt = 0
                elif b_v == 7:
                    bkt = 1
                elif b_v == 5:
                    bkt = 4
                else:
                    bkt = 3
                return (bkt, ef, b_v)

            ordered_b_tut = sorted(b_tasks_tut, key=_tut_b_key)
            has_fusion_t = any((game_data.safe_optional_int(ti.building_id, 0) or 0) == 5 for ti, _ in b_tasks_tut)
            if (
                need_pwr_first
                and not has_fusion_t
                and b_tasks_tut
                and _safe_int(tut_p.energy.net, 0) >= 0
                and is_building_allowed_on_planet_type(p_t_tut, 5)
            ):
                _emit_opt_build_step(tut_p, 5, "Proactive power before tutorial mines")

            for t_item, eff_bc in ordered_b_tut:
                bid_t = game_data.safe_optional_int(t_item.building_id, None)
                if bid_t is None:
                    continue
                b_goal = _safe_action_str(t_item.building_name) or f"Building {bid_t}"
                emitted_pr = _emit_prereq_steps_for(tut_p, "buildings", bid_t, b_goal)
                if emitted_pr > 0 and game_data.missing_prerequisites(
                    "buildings", bid_t, buildings=tut_p.buildings, researches=state.researches
                ):
                    continue
                t_reason = (
                    f"Tutorial {tut_step_s}/{tut_total_s}: {_safe_action_str(t_item.description)} "
                    f"(progress {game_data.fmt_compact_int(eff_bc)}/{game_data.fmt_compact_int(t_item.target)})"
                )
                _emit_opt_build_step(tut_p, bid_t, t_reason)

            for r_item, eff_rc in r_tasks_tut:
                rk_t = _safe_action_str(r_item.research_id).strip()
                if not rk_t:
                    continue
                r_goal = _safe_action_str(r_item.building_name) or rk_t
                _emit_prereq_steps_for(tut_p, "research", rk_t, r_goal)
                if rk_t not in emitted_prereq_r:
                    r_reason = (
                        f"Tutorial {tut_step_s}/{tut_total_s}: {_safe_action_str(r_item.description)} "
                        f"(progress {game_data.fmt_compact_int(eff_rc)}/{game_data.fmt_compact_int(r_item.target)})"
                    )
                    _emit_opt_research_step(tut_p, rk_t, r_reason)
            break

        # 2) Ship tasks & fleet/milestone tasks (Steps 5-11)
        non_raid_p = [pl for pl in planets_to_check if not is_raid_planet(pl)]

        def _best_ship_planet_opt(sid_v: int) -> Optional[PlanetState]:
            if not non_raid_p:
                return None
            slug_v = SHIP_SLUG_BY_KEY.get(f"s{sid_v}")
            best_p: Optional[PlanetState] = None
            best_r: Optional[tuple[bool, bool, bool, int, bool]] = None
            for cand_p in non_raid_p:
                s_off_c = s_offers_by_p.get(cand_p.planet_id, {}).get(sid_v)
                unmet_c = _fast_unmet_prereqs("ships", slug_v, cand_p.buildings, state.researches) if slug_v else ()
                wf_l = cand_p.building_level(9)
                has_wf_c = wf_l >= 1 and not any(m.get("category") == "buildings" for m in unmet_c)
                can_ex_c = bool(
                    has_wf_c
                    and cfg.allow_shipyard
                    and s_off_c is not None
                    and s_off_c.can_build
                    and avail_res_by_p.get(cand_p.planet_id, cand_p.resources).can_afford(s_off_c.cost)
                )
                rnk = (can_ex_c, not unmet_c, has_wf_c, wf_l, cand_p.planet_id == active.planet_id)
                if best_r is None or rnk > best_r:
                    best_r = rnk
                    best_p = cand_p
            return best_p

        def _emit_opt_ship_with_prereqs(sid_v: int, qty_v: int, reason_str: str) -> None:
            cand_p = _best_ship_planet_opt(sid_v)
            if cand_p is None:
                return
            slug_v = SHIP_SLUG_BY_KEY.get(f"s{sid_v}")
            if not slug_v or slug_v not in game_data.SHIPS:
                return
            s_info = game_data.SHIPS[slug_v]
            s_off = s_offers_by_p.get(cand_p.planet_id, {}).get(sid_v)
            s_lbl = (_safe_action_str(s_off.name) if s_off and s_off.name else "") or str(s_info["name"])
            _emit_prereq_steps_for(cand_p, "ships", slug_v, s_lbl)
            u_cost = s_off.cost if (s_off is not None and not s_off.cost.is_zero()) else _resources_from_dict(s_info.get("base_cost") or {})
            q_eff = 1 if s_off is not None else max(1, qty_v)
            tot_cost = Resources(
                pig_iron=u_cost.pig_iron * q_eff,
                metal=u_cost.metal * q_eff,
                kryptonite=u_cost.kryptonite * q_eff,
                spice=u_cost.spice * q_eff,
            )
            av_p = avail_res_by_p.get(cand_p.planet_id, cand_p.resources)
            ship_avail_before.setdefault((cand_p.planet_id, sid_v), av_p)
            unmet_now = _fast_unmet_prereqs("ships", slug_v, cand_p.buildings, state.researches)
            can_ex = bool(
                not is_raid_planet(cand_p)
                and cand_p.building_level(9) >= 1
                and not unmet_now
                and bool(cfg.allow_shipyard)
                and s_off is not None
                and s_off.can_build
                and av_p.can_afford(tot_cost)
            )
            if can_ex:
                avail_res_by_p[cand_p.planet_id] = _sub_resources(av_p, tot_cost)
            p1_done_ships.add((cand_p.planet_id, sid_v))
            plan.append(
                _make_planned_action(
                    priority="P2",
                    kind="BUILD_SHIP",
                    planet_id=cand_p.planet_id,
                    planet_coords=_safe_action_str(cand_p.coords),
                    target_id=sid_v,
                    target_name=s_lbl,
                    target_level=q_eff,
                    cost=tot_cost,
                    missing=av_p.missing(tot_cost),
                    reason=reason_str,
                    executable_now=can_ex,
                )
            )

        for t_item in tut_obj.tasks:
            if t_item.completed:
                continue
            if t_item.category == "ship":
                sid_t = game_data.safe_optional_int(t_item.building_id, None)
                if sid_t is not None and sid_t > 0:
                    need_q = max(1, _safe_int(t_item.target, 1) - _safe_int(t_item.current, 0))
                    _emit_opt_ship_with_prereqs(
                        sid_t,
                        need_q,
                        f"Tutorial {tut_step_s}/{tut_total_s}: {_safe_action_str(t_item.description)}",
                    )
            elif t_item.category == "milestone":
                desc_s = _safe_action_str(t_item.description)
                desc_l = desc_s.lower()
                if any(w in desc_l for w in ("fight", "attack", "combat", "battle")):
                    continue
                helper_sid = _opt_milestone_helper_ship_id(desc_l)
                if helper_sid == 10:
                    has_probe_any = any(_safe_int(pl.ships.get("s10", 0), 0) > 0 for pl in non_raid_p)
                    if not has_probe_any:
                        _emit_opt_ship_with_prereqs(
                            10,
                            1,
                            f"Tutorial {tut_step_s}/{tut_total_s}: build Spy probe for {desc_s}",
                        )
                    continue
                if helper_sid in (8, 11):
                    has_helper_any = any(_safe_int(pl.ships.get(f"s{helper_sid}", 0), 0) > 0 for pl in non_raid_p)
                    if not has_helper_any:
                        h_name = "Colony ship" if helper_sid == 8 else "Recycler"
                        _emit_opt_ship_with_prereqs(
                            helper_sid,
                            1,
                            f"Tutorial {tut_step_s}/{tut_total_s}: build {h_name} for {desc_s}",
                        )
                plan.append(
                    _make_planned_action(
                        priority="P3",
                        kind="TUTORIAL_TASK",
                        planet_id=active.planet_id,
                        planet_coords=active_coords_s,
                        target_name=desc_s,
                        reason=f"Tutorial {tut_step_s}/{tut_total_s}: {desc_s}",
                        executable_now=False,
                        action_href=None,
                    )
                )

        # 3) Tutorial Spy / Fight fleet execution & galaxy spiral
        merged_scans_tut = set(recent_scans)
        for raw_rt in state.raid_targets or ():
            if isinstance(raw_rt, Mapping):
                rt_sc = _safe_float_any(raw_rt.get("last_scanned_ts"), 0.0)
                if rt_sc > 0.0 and (live_now_ts - rt_sc) < 1800.0:
                    rc_s = _safe_action_str(raw_rt.get("coords") or raw_rt.get("target_coords")).strip()
                    if rc_s:
                        merged_scans_tut.add(_parse_gal_sys(rc_s))

        if tutorial_spy_task is not None:
            probe_p = (
                active
                if _safe_int(active.ships.get("s10", 0), 0) > 0
                else next((pl for pl in non_raid_p if _safe_int(pl.ships.get("s10", 0), 0) > 0), None)
            )
            if probe_p is not None:
                emitted_spy = False
                for raw_rt in state.raid_targets or ():
                    if not isinstance(raw_rt, Mapping):
                        continue
                    tc_raw = _safe_action_str(raw_rt.get("coords") or raw_rt.get("target_coords")).strip()
                    if not tc_raw or normalize_coords(tc_raw) in own_coords:
                        continue
                    norm_tc = normalize_coords(tc_raw)
                    is_sp = bool(raw_rt.get("is_spacia", False))
                    sc_col = _resolve_raid_target_status_color(raw_rt)
                    if not (sc_col == "yellow" or is_sp or (bool(cfg.allow_pvp_in_az) and sc_col == "light_red")):
                        continue
                    ow_s = _safe_action_str(raw_rt.get("owner"))
                    pn_s = _safe_action_str(raw_rt.get("planet_name"))
                    tg_s = "Spacia" if is_sp else ("Inactive" if sc_col == "yellow" else sc_col.title())
                    plan.append(
                        _make_planned_action(
                            priority="P2",
                            kind="SPY",
                            planet_id=probe_p.planet_id,
                            planet_coords=_safe_action_str(probe_p.coords),
                            target_id=10,
                            target_level=1,
                            target_name=f"{norm_tc} ({ow_s or pn_s or tg_s})",
                            reason=f"Tutorial spy: Send Spy probe (s10) to scout {tg_s} {norm_tc} before raiding",
                            executable_now=bool(cfg.allow_galaxy_scan),
                        )
                    )
                    emitted_spy = True
                    break
                if not emitted_spy:
                    nxt_sc = _next_unscanned_system(_safe_action_str(probe_p.coords), merged_scans_tut)
                    if nxt_sc is not None:
                        sg, ss = nxt_sc
                        plan.append(
                            _make_planned_action(
                                priority="P2",
                                kind="SCAN_GALAXY",
                                planet_id=probe_p.planet_id,
                                planet_coords=_safe_action_str(probe_p.coords),
                                target_name=f"Galaxy [{sg:02d}:{ss:03d}]",
                                reason=f"Scan system {sg:02d}:{ss:03d} for tutorial spy target",
                                executable_now=bool(cfg.allow_galaxy_scan),
                            )
                        )

        if tutorial_fight_task is not None:
            atk_p = (
                active
                if any(_safe_int(active.ships.get(k, 0), 0) > 0 for k in _COMBAT_AND_RAID_SHIP_KEYS)
                else next(
                    (
                        pl
                        for pl in planets_to_check
                        if any(_safe_int(pl.ships.get(k, 0), 0) > 0 for k in _COMBAT_AND_RAID_SHIP_KEYS)
                    ),
                    active,
                )
            )
            atk_fl = {
                k: max(0, _safe_int(atk_p.ships.get(k, 0), 0))
                for k in _COMBAT_AND_RAID_SHIP_KEYS
                if _safe_int(atk_p.ships.get(k, 0), 0) > 0
            }
            emitted_fight_atk = False
            for raw_rt in state.raid_targets or ():
                if not isinstance(raw_rt, Mapping):
                    continue
                tc_raw = _safe_action_str(raw_rt.get("coords") or raw_rt.get("target_coords")).strip()
                if not tc_raw or normalize_coords(tc_raw) in own_coords:
                    continue
                norm_tc = normalize_coords(tc_raw)
                is_sp = bool(raw_rt.get("is_spacia", False))
                sc_col = _resolve_raid_target_status_color(raw_rt)
                is_in = sc_col == "yellow"
                if not (is_in or is_sp or (bool(cfg.allow_pvp_in_az) and sc_col == "light_red")):
                    continue
                b_ok = bash_tracker.can_attack(norm_tc, is_inactive=is_in, is_spacia=is_sp, timestamp=live_now_ts)
                if not b_ok:
                    continue
                rf_m = raw_rt.get("fleet") if isinstance(raw_rt.get("fleet"), Mapping) else {}
                rd_m = raw_rt.get("defense") if isinstance(raw_rt.get("defense"), Mapping) else {}
                rep_t = SpyReport(
                    target_coords=norm_tc,
                    owner=_safe_action_str(raw_rt.get("owner")),
                    resources=sim_target_res.get(norm_tc, Resources()),
                    fleet={_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in rf_m.items()},
                    defense={_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in rd_m.items()},
                    dug_in=raw_rt.get("dug_in"),
                    is_spacia=is_sp,
                )
                sim_fl_t = atk_fl if atk_fl else {"s1": 1}
                cmb_t = simulate_raid_outcome(
                    sim_fl_t, rep_t, attacker_researches=state.researches, safety_margin=cfg.combat_safety_margin
                )
                if not cmb_t.safe_to_attack:
                    continue
                ow_s = _safe_action_str(raw_rt.get("owner"))
                pn_s = _safe_action_str(raw_rt.get("planet_name"))
                tg_s = "Spacia" if is_sp else ("Inactive" if is_in else sc_col.title())
                can_ex_f = bool(cfg.allow_raids and atk_fl and not own_fleet_in_flight)
                f_reason = f"Tutorial {tut_step_s}/{tut_total_s}: {_safe_action_str(tutorial_fight_task.description)}"
                if own_fleet_in_flight:
                    f_reason += " [Hold: Tutorial attack already in flight]"
                elif not cfg.allow_raids:
                    f_reason += " [Hold: allow_raids=false in Config]"
                elif not atk_fl:
                    f_reason += " [Hold: Build attack/transport ship first]"
                if can_ex_f:
                    raided_coords_this_tick.add(norm_tc)
                    if not (is_in or is_sp):
                        bash_tracker.record_attack(norm_tc, timestamp=live_now_ts)
                plan.append(
                    _make_planned_action(
                        priority="P2" if can_ex_f else "P3",
                        kind="ATTACK",
                        planet_id=atk_p.planet_id,
                        planet_coords=_safe_action_str(atk_p.coords),
                        target_name=f"{norm_tc} ({ow_s or pn_s or tg_s})",
                        reason=f_reason,
                        executable_now=can_ex_f,
                    )
                )
                emitted_fight_atk = True
                break
            if not emitted_fight_atk and not own_fleet_in_flight:
                nxt_sc = _next_unscanned_system(_safe_action_str(atk_p.coords), merged_scans_tut)
                if nxt_sc is not None:
                    sg, ss = nxt_sc
                    plan.append(
                        _make_planned_action(
                            priority="P2",
                            kind="SCAN_GALAXY",
                            planet_id=atk_p.planet_id,
                            planet_coords=_safe_action_str(atk_p.coords),
                            target_name=f"Galaxy [{sg:02d}:{ss:03d}]",
                            reason=f"Scan system {sg:02d}:{ss:03d} for tutorial fight target",
                            executable_now=bool(cfg.allow_galaxy_scan),
                        )
                    )

        # 4) Expand warehouse if any tutorial WAIT_RESOURCES exceeds current storage cap
        for w_p in planets_to_check:
            p_t_w = normalize_planet_type(w_p)
            if p_t_w in ("asteroid", "raid"):
                continue
            w_wait = [
                a for a in plan
                if a.planet_id == w_p.planet_id and a.priority in ("P1", "P2") and a.kind == "WAIT_RESOURCES"
            ]
            for w_act in w_wait:
                for rk_w in _RESOURCE_KEYS:
                    c_v = _safe_int(getattr(w_act.cost, rk_w, 0), 0)
                    s_v = _safe_int(getattr(w_p.storage, rk_w, 0), 0)
                    if s_v <= 0 or c_v <= s_v:
                        continue
                    wh_id_v, _ = _WAREHOUSE_ID_BY_RES[rk_w]
                    if any(
                        a.planet_id == w_p.planet_id and a.target_id == wh_id_v and a.kind in ("BUILD", "WAIT_RESOURCES")
                        for a in plan
                    ):
                        continue
                    _emit_opt_build_step(
                        w_p,
                        wh_id_v,
                        f"Expand {rk_w.replace('_', ' ')} cap ({game_data.fmt_compact_int(s_v)}) for {w_act.target_name} ({game_data.fmt_compact_int(c_v)} needed)",
                        prio_str=w_act.priority,
                    )

    for step in best_chrom:
        planet = (
            planet_map.get(step.planet_id)
            if isinstance(step.planet_id, int) and not isinstance(step.planet_id, bool)
            else None
        )
        if planet is None:
            continue
        is_active = planet.planet_id == active.planet_id
        if not is_active and not cfg.allow_multi_planet:
            continue

        kind = _safe_action_str(step.kind).upper()
        ptype = normalize_planet_type(planet)
        if kind == "CLAIM_TUTORIAL":
            continue  # Already handled above when can_complete=True
        if ptype == "raid" and kind in (
            "BUILD",
            "WAIT_RESOURCES",
            "RESEARCH",
            "BUILD_SHIP",
            "BUILD_DEFENSE",
            "THROTTLE_BUILDING",
        ):
            continue

        if kind == "BUILD":
            bid = _parse_building_target_id(step.target_id, 0)
            if bid == 5 and planet.planet_id in deferred_non_urgent_fusion_pids:
                continue
            if (planet.planet_id, bid) in p1_done_builds:
                p1_done_builds.discard((planet.planet_id, bid))
                continue
            info = game_data.BUILDING_BY_ID.get(bid)
            if not info:
                continue
            b8_offer_ok = bid == 8 and any(
                game_data.safe_optional_int(o.building_id, None) == 8
                for o in planet.building_offers
            )
            if not is_building_allowed_on_planet_type(ptype, bid, has_b8_offer=b8_offer_ok):
                continue
            if tut_obj is not None:
                sim_p_blds = dict(live_eff_b.get(planet.planet_id, {}))
                for (sp_pid, sp_bid), sp_lvl in sim_step_b_lvl.items():
                    if sp_pid == planet.planet_id:
                        sim_p_blds[f"b{sp_bid}"] = max(sim_p_blds.get(f"b{sp_bid}", 0), sp_lvl)
                if game_data.missing_prerequisites(
                    "buildings", bid, buildings=sim_p_blds, researches=sim_step_r_lvl
                ):
                    continue
            offers = b_offers_by_p.get(planet.planet_id, {})
            offer = offers.get(bid)
            base_eff = int(live_eff_b[planet.planet_id].get(f"b{bid}", planet.building_level(bid)))
            cur_step_lvl = sim_step_b_lvl.get((planet.planet_id, bid), base_eff)
            tgt_lvl = cur_step_lvl + 1
            sim_step_b_lvl[(planet.planet_id, bid)] = tgt_lvl

            offer_tgt_b = game_data.safe_optional_int(offer.target_level, None) if offer else None
            bname = (_safe_action_str(offer.name) if offer and offer.name and offer_tgt_b == tgt_lvl else "") or str(info["name"])
            is_urgent_power = bid in (5, 6) and planet.energy.net < 0
            ratios = planet.storage_fill_ratios()
            is_urgent_wh = bid in (11, 12, 13, 14) and ratios.get(_RESOURCE_KEYS[bid - 11], 0.0) >= warn_ratio
            prio = "P1" if (is_urgent_power or is_urgent_wh) else "P2"
            reason = _safe_action_str(step.reason) or f"GA+NN plan: {bname} L{game_data.fmt_compact_int(tgt_lvl)}"
            p_coords_s = _safe_action_str(planet.coords)

            avail_p = avail_res_by_p.get(planet.planet_id, planet.resources)
            cur_queued = queued_cnt_by_p.get(planet.planet_id, 0)
            max_slots = max_slots_by_p.get(planet.planet_id, 5)
            p_f_used = max(0, _safe_int(planet.fields_used, 0))
            p_f_max = max(0, _safe_int(planet.fields_max, 0))
            has_free_field = p_f_max > 0 and (p_f_used + cur_queued) < p_f_max
            queue_has_slot = cur_queued < max_slots

            if offer is not None and offer_tgt_b == tgt_lvl:
                cost = offer.cost
                missing = avail_p.missing(cost)
                prereq_b_ok = not game_data.missing_prerequisites(
                    "buildings", bid, buildings=planet.buildings, researches=state.researches
                )
                can_exec = bool(
                    allow_build
                    and has_free_field
                    and queue_has_slot
                    and prereq_b_ok
                    and offer.can_build
                    and avail_p.can_afford(cost)
                )
                if can_exec:
                    avail_res_by_p[planet.planet_id] = _sub_resources(avail_p, cost)
                    queued_cnt_by_p[planet.planet_id] = cur_queued + 1
                if can_exec:
                    b_reason = reason
                elif not prereq_b_ok:
                    b_reason = f"{reason} (prerequisites unmet)"
                else:
                    b_reason = f"{reason} (waiting/queued)"
                plan.append(
                    _make_planned_action(
                        priority=prio,
                        kind="BUILD" if can_exec else "WAIT_RESOURCES",
                        planet_id=planet.planet_id,
                        planet_coords=p_coords_s,
                        target_id=bid,
                        target_name=bname,
                        target_level=tgt_lvl,
                        cost=cost,
                        missing=missing,
                        reason=b_reason,
                        executable_now=can_exec,
                        action_href=_safe_action_str(offer.build_href) or None if can_exec else None,
                    )
                )
            else:
                cost = _resources_from_dict(game_data.building_cost(bid, tgt_lvl))
                missing = avail_p.missing(cost)
                prereq_ok = not game_data.missing_prerequisites(
                    "buildings", bid, buildings=planet.buildings, researches=state.researches
                )
                remote_ready = bool(
                    not is_active
                    and cfg.allow_multi_planet
                    and ptype != "raid"
                    and allow_build
                    and has_free_field
                    and queue_has_slot
                    and tgt_lvl == base_eff + 1
                    and not cost.is_zero()
                    and avail_p.can_afford(cost)
                    and prereq_ok
                )
                if remote_ready:
                    avail_res_by_p[planet.planet_id] = _sub_resources(avail_p, cost)
                    queued_cnt_by_p[planet.planet_id] = cur_queued + 1
                plan.append(
                    _make_planned_action(
                        priority=prio,
                        kind="BUILD" if remote_ready else "WAIT_RESOURCES",
                        planet_id=planet.planet_id,
                        planet_coords=p_coords_s,
                        target_id=bid,
                        target_name=bname,
                        target_level=tgt_lvl,
                        cost=cost,
                        missing=missing,
                        reason=reason if remote_ready else f"{reason} (future/queued upgrade)",
                        executable_now=remote_ready,
                        action_href=None,
                    )
                )

        elif kind == "RESEARCH":
            rkey = _resolve_research_key(step.target_id)
            if not rkey:
                continue
            info = game_data.RESEARCH_BY_ID[rkey]
            rid = int(rkey[1:])
            cur_r = int(sim_step_r_lvl.get(rkey, _safe_int((state.researches or {}).get(rkey, 0), 0)))
            tgt_lvl = cur_r + 1
            sim_step_r_lvl[rkey] = tgt_lvl
            offer = r_offers.get(rkey)
            rname = (_safe_action_str(offer.name) if offer and offer.name else "") or str(info["name"])
            avail_p = avail_res_by_p.get(planet.planet_id, planet.resources)
            offer_tgt_r = game_data.safe_optional_int(offer.target_level, None) if offer is not None else None
            has_lab_now = planet.building_level(8) >= 1
            prereq_r_ok = not _fast_unmet_prereqs("research", rkey, planet.buildings, state.researches)
            if offer is not None and offer_tgt_r == tgt_lvl:
                cost = offer.cost
                can_exec = bool(
                    is_active
                    and ptype != "raid"
                    and has_lab_now
                    and prereq_r_ok
                    and bool(cfg.allow_research)
                    and offer.can_research
                    and not research_queue_busy
                    and avail_p.can_afford(cost)
                )
                href = _safe_action_str(offer.research_href) or None if can_exec else None
            else:
                cost = _resources_from_dict(_research_level_stats(info, tgt_lvl).get("cost") or {})
                can_exec = False
                href = None
            if can_exec:
                avail_res_by_p[planet.planet_id] = _sub_resources(avail_p, cost)
                research_queue_busy = True
            plan.append(
                _make_planned_action(
                    priority="P2",
                    kind="RESEARCH",
                    planet_id=planet.planet_id,
                    planet_coords=_safe_action_str(planet.coords),
                    target_id=rid,
                    target_name=rname,
                    target_level=tgt_lvl,
                    cost=cost,
                    missing=avail_p.missing(cost),
                    reason=_safe_action_str(step.reason) or f"GA+NN research {rname} L{game_data.fmt_compact_int(tgt_lvl)}",
                    executable_now=can_exec,
                    action_href=href,
                )
            )

        elif kind == "BUILD_SHIP":
            skey, slug = _resolve_ship_key_and_slug(step.target_id)
            if not skey or not slug:
                continue
            sid = int(skey[1:])
            if (planet.planet_id, sid) in p1_done_ships:
                p1_done_ships.discard((planet.planet_id, sid))
                continue
            qty = max(1, _safe_int(step.quantity, 1))
            offer = s_offers_by_p.get(planet.planet_id, {}).get(sid)
            info = game_data.SHIPS[slug]
            sname = (_safe_action_str(offer.name) if offer and offer.name else "") or str(info["name"])
            unit_cost = offer.cost if (offer is not None and not offer.cost.is_zero()) else _resources_from_dict(info.get("base_cost") or {})
            cost = Resources(
                pig_iron=unit_cost.pig_iron * qty,
                metal=unit_cost.metal * qty,
                kryptonite=unit_cost.kryptonite * qty,
                spice=unit_cost.spice * qty,
            )
            avail_p = avail_res_by_p.get(planet.planet_id, planet.resources)
            ship_avail_before.setdefault((planet.planet_id, sid), avail_p)
            has_wf_now = planet.building_level(9) >= 1 and not any(
                m.get("category") == "buildings"
                for m in _fast_unmet_prereqs("ships", slug, planet.buildings, state.researches)
            )
            hold_for_in_flight = (
                own_fleet_in_flight
                and sid not in tutorial_ship_ids
                and sid in (1, 2, 3, 4, 5, 6, 7, 13, 14, 15, 16)
            )
            can_exec = bool(
                ptype != "raid"
                and not hold_for_in_flight
                and has_wf_now
                and bool(cfg.allow_shipyard)
                and offer is not None
                and offer.can_build
                and avail_p.can_afford(cost)
            )
            if can_exec:
                avail_res_by_p[planet.planet_id] = _sub_resources(avail_p, cost)
            ship_reason = _safe_action_str(step.reason) or f"GA+NN shipyard build {sname}"
            if hold_for_in_flight and "in transit" not in ship_reason:
                ship_reason = f"{ship_reason} (hold while own fleet is in transit)"
            plan.append(
                _make_planned_action(
                    priority="P2",
                    kind="BUILD_SHIP",
                    planet_id=planet.planet_id,
                    planet_coords=_safe_action_str(planet.coords),
                    target_id=sid,
                    target_name=sname,
                    target_level=qty,
                    cost=cost,
                    missing=avail_p.missing(cost),
                    reason=ship_reason,
                    executable_now=can_exec,
                )
            )

        elif kind == "THROTTLE_BUILDING":
            bid = _parse_building_target_id(step.target_id, 2)
            if bid not in (1, 2, 3, 4, 6) or ptype in ("asteroid", "raid"):
                continue
            if any(a.planet_id == planet.planet_id and a.kind == "THROTTLE_BUILDING" and a.target_id == bid for a in plan):
                continue
            info = game_data.BUILDING_BY_ID.get(bid)
            bname = str((info or {}).get("name") or f"Building {bid}")
            pct = max(0, min(100, (_safe_int(step.quantity, 0) // 10) * 10))
            field_k = f"resource{bid}" if bid <= 4 else "resource5"
            cur_pct = max(0, min(100, _safe_int((planet.economy_rates or {}).get(field_k, 100), 100)))
            cd_active = (planet.planet_id in active_t_cds) or ((planet.planet_id, bid) in active_t_cds)
            if cd_active and not (bid == 2 and planet.energy.net < 0 and pct == 0 and cur_pct > 0):
                continue
            can_exec = (
                bool(cfg.auto_throttle_blast_furnace)
                and not cfg.merchant_mode
                and planet.building_level(bid) > 0
                and cur_pct != pct
            )
            plan.append(
                _make_planned_action(
                    priority="P1" if pct == 0 else "P2",
                    kind="THROTTLE_BUILDING",
                    planet_id=planet.planet_id,
                    planet_coords=_safe_action_str(planet.coords),
                    target_id=bid,
                    target_name=f"{bname} {pct}%",
                    target_level=pct,
                    reason=_safe_action_str(step.reason) or f"GA+NN throttle {bname} to {pct}%",
                    executable_now=can_exec,
                )
            )

        elif kind == "ATTACK":
            raw_coords = _safe_action_str(step.target_coords).strip()
            if not raw_coords:
                continue
            norm_step_coords = normalize_coords(raw_coords)
            if norm_step_coords in own_coords:
                continue
            atk_ships = {
                k: max(0, _safe_int(planet.ships.get(k, 0), 0))
                for k in _COMBAT_AND_RAID_SHIP_KEYS
                if _safe_int(planet.ships.get(k, 0), 0) > 0
            }
            meta = next(
                (
                    r
                    for r in (state.raid_targets or ())
                    if normalize_coords(_safe_action_str(r.get("coords") or r.get("target_coords"))) == norm_step_coords
                ),
                None,
            )
            coords = norm_step_coords
            owner = _safe_action_str((meta or {}).get("owner"))
            pname = _safe_action_str((meta or {}).get("planet_name"))
            is_spacia = bool((meta or {}).get("is_spacia", False))
            scolor = _resolve_raid_target_status_color(meta)
            is_inact = scolor == "yellow"
            tag = "Spacia" if is_spacia else ("Inactive" if is_inact else scolor.title())
            target_label = f"{coords} ({owner or pname or tag})"

            safe = False
            exp_loot_sp = 0.0
            eff_win_prob = 1.0
            t_hist: Optional[dict[str, Any]] = None
            min_req = max(0.0, _safe_float_any(getattr(cfg, "raid_min_loot_spice_eq", 1500.0), 1500.0))
            bash_ok = bash_tracker.can_attack(
                coords,
                is_inactive=is_inact,
                is_spacia=is_spacia,
                timestamp=live_now_ts,
            )
            if not bash_ok and not cfg.allow_raids:
                continue
            already_raided_t0 = norm_step_coords in raided_coords_this_tick
            is_depleted_now = norm_step_coords in depleted_target_coords
            if (already_raided_t0 or is_depleted_now) and cfg.allow_raids:
                continue
            policy_ok = is_inact or is_spacia or (bool(cfg.allow_pvp_in_az) and scolor == "light_red")
            combat_loot_raw = Resources()
            combat_loot_sp = 0.0
            cur_t_res = sim_target_res.get(coords, sim_target_res.get(norm_step_coords, Resources()))
            if atk_ships and meta is not None and policy_ok and bash_ok and not is_depleted_now:
                raw_fleet_m = meta.get("fleet") if isinstance(meta.get("fleet"), Mapping) else {}
                raw_def_m = meta.get("defense") if isinstance(meta.get("defense"), Mapping) else {}
                rep = SpyReport(
                    target_coords=coords,
                    owner=owner,
                    resources=cur_t_res,
                    fleet={_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in raw_fleet_m.items()},
                    defense={_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in raw_def_m.items()},
                    dug_in=meta.get("dug_in"),
                    is_spacia=is_spacia,
                )
                combat = simulate_raid_outcome(
                    atk_ships, rep, attacker_researches=state.researches, safety_margin=cfg.combat_safety_margin
                )
                safe = combat.safe_to_attack
                combat_loot_raw = combat.expected_loot
                combat_loot_sp = combat.loot_spice_equivalent
                exp_loot_sp, eff_win_prob, t_hist = _compute_effective_raid_expectation(
                    combat.loot_spice_equivalent,
                    combat.cargo_capacity,
                    coords,
                    resolved_stats,
                    target_depleted=(combat.loot_spice_equivalent <= 0.0),
                )
            else:
                exp_loot_sp, eff_win_prob, t_hist = _compute_effective_raid_expectation(
                    0.0, 0, coords, resolved_stats, target_depleted=True
                )

            scale = (exp_loot_sp / combat_loot_sp) if combat_loot_sp > 1e-6 else 0.0
            rounded_loot = Resources(
                pig_iron=game_data.safe_scale_round_int(
                    combat_loot_raw.pig_iron, scale, max_result=game_data.MAX_JSON_SAFE_INT
                ),
                metal=game_data.safe_scale_round_int(
                    combat_loot_raw.metal, scale, max_result=game_data.MAX_JSON_SAFE_INT
                ),
                kryptonite=game_data.safe_scale_round_int(
                    combat_loot_raw.kryptonite, scale, max_result=game_data.MAX_JSON_SAFE_INT
                ),
                spice=game_data.safe_scale_round_int(
                    combat_loot_raw.spice, scale, max_result=game_data.MAX_JSON_SAFE_INT
                ),
            )

            hist_note = ""
            if t_hist and _safe_int(t_hist.get("attempts", 0), 0) > 0:
                t_att = _safe_int(t_hist.get("attempts", 0), 0)
                t_wins = _safe_int(t_hist.get("wins", 0), 0)
                t_avg = _safe_float_any(t_hist.get("avg_loot_spice_eq", 0.0), 0.0)
                t_last = _safe_float_any(t_hist.get("last_loot_spice_eq", 0.0), 0.0)
                hist_note = (
                    f" | hist {t_wins}/{t_att} ({eff_win_prob:.0%}), "
                    f"avg {_fmt_sp_int(t_avg)}, last {_fmt_sp_int(t_last)} SpEq"
                )

            base_reason = _safe_action_str(step.reason) or f"GA+NN raid {coords} (~{_fmt_sp_int(exp_loot_sp)} SpEq)"
            full_reason = base_reason if ("hist " in base_reason or not hist_note) else f"{base_reason}{hist_note}"
            if not bash_ok and "Bash limit" not in full_reason:
                full_reason = f"{full_reason} [Hold: Bash limit 3/24h]"
            if (
                not already_raided_t0
                and not is_depleted_now
                and policy_ok
                and bash_ok
                and safe
                and combat_loot_sp > 0.0
                and rounded_loot.is_zero()
                and "Rounded raid loot" not in full_reason
            ):
                full_reason = f"{full_reason} [Hold: Rounded raid loot is 0]"
            hold_tut_inflight = (tutorial_fight_task is not None) and own_fleet_in_flight
            tut_zero_loot_ok = (tutorial_fight_task is not None) and not hold_tut_inflight and not already_raided_t0
            if hold_tut_inflight and "already in flight" not in full_reason:
                full_reason = f"{full_reason} [Hold: Tutorial attack already in flight]"
            if already_raided_t0 or (is_depleted_now and not tut_zero_loot_ok) or hold_tut_inflight:
                can_exec = False
            else:
                loot_ok = tut_zero_loot_ok or (
                    combat_loot_sp > 0.0
                    and exp_loot_sp > 0.0
                    and not rounded_loot.is_zero()
                    and exp_loot_sp >= min_req - 1e-6
                )
                can_exec = bool(
                    cfg.allow_raids
                    and atk_ships
                    and safe
                    and policy_ok
                    and bash_ok
                    and loot_ok
                )
            if can_exec:
                raided_coords_this_tick.add(norm_step_coords)
                taken_pig = min(cur_t_res.pig_iron, max(combat_loot_raw.pig_iron, rounded_loot.pig_iron))
                taken_met = min(cur_t_res.metal, max(combat_loot_raw.metal, rounded_loot.metal))
                taken_kry = min(cur_t_res.kryptonite, max(combat_loot_raw.kryptonite, rounded_loot.kryptonite))
                taken_spi = min(cur_t_res.spice, max(combat_loot_raw.spice, rounded_loot.spice))
                rem_t = Resources(
                    pig_iron=max(0, cur_t_res.pig_iron - taken_pig),
                    metal=max(0, cur_t_res.metal - taken_met),
                    kryptonite=max(0, cur_t_res.kryptonite - taken_kry),
                    spice=max(0, cur_t_res.spice - taken_spi),
                )
                sim_target_res[coords] = rem_t
                sim_target_res[norm_step_coords] = rem_t
                if max(rem_t.pig_iron, rem_t.metal, rem_t.kryptonite, rem_t.spice) <= 1:
                    depleted_target_coords.add(norm_step_coords)
            if can_exec and not (is_inact or is_spacia):
                bash_tracker.record_attack(coords, timestamp=live_now_ts)
            plan.append(
                _make_planned_action(
                    priority="P2",
                    kind="ATTACK",
                    planet_id=planet.planet_id,
                    planet_coords=_safe_action_str(planet.coords),
                    target_name=target_label,
                    reason=full_reason,
                    executable_now=can_exec,
                )
            )

        elif kind == "SPY":
            raw_coords = _safe_action_str(step.target_coords).strip()
            if not raw_coords or ptype == "raid":
                continue
            norm_spy_coords = normalize_coords(raw_coords)
            if norm_spy_coords in own_coords or any(a.kind == "SPY" for a in plan):
                continue
            meta_spy = next(
                (
                    r
                    for r in (state.raid_targets or ())
                    if normalize_coords(_safe_action_str(r.get("coords") or r.get("target_coords"))) == norm_spy_coords
                ),
                None,
            )
            if meta_spy is None:
                continue
            ow_spy = _safe_action_str(meta_spy.get("owner"))
            pn_spy = _safe_action_str(meta_spy.get("planet_name"))
            is_sp_spy = bool(meta_spy.get("is_spacia", False))
            sc_spy = _resolve_raid_target_status_color(meta_spy)
            pol_spy = sc_spy == "yellow" or is_sp_spy or (bool(cfg.allow_pvp_in_az) and sc_spy == "light_red")
            tg_spy = "Spacia" if is_sp_spy else ("Inactive" if sc_spy == "yellow" else sc_spy.title())
            can_exec_spy = bool(cfg.allow_galaxy_scan and _safe_int(planet.ships.get("s10", 0), 0) > 0 and pol_spy)
            plan.append(
                _make_planned_action(
                    priority="P2",
                    kind="SPY",
                    planet_id=planet.planet_id,
                    planet_coords=_safe_action_str(planet.coords),
                    target_id=10,
                    target_level=1,
                    target_name=f"{norm_spy_coords} ({ow_spy or pn_spy or tg_spy})",
                    reason=_safe_action_str(step.reason) or f"Send Spy probe (s10) to scout {tg_spy} {norm_spy_coords}",
                    executable_now=can_exec_spy,
                )
            )

        elif kind == "SCAN_GALAXY":
            gal, sys_num = _parse_gal_sys(_safe_action_str(step.target_coords) or _safe_action_str(planet.coords))
            on_scan_cd = (gal, sys_num) in recent_scans
            if on_scan_cd and cfg.allow_galaxy_scan:
                continue
            plan.append(
                _make_planned_action(
                    priority="P2",
                    kind="SCAN_GALAXY",
                    planet_id=planet.planet_id,
                    planet_coords=_safe_action_str(planet.coords),
                    target_name=f"Galaxy [{gal:02d}:{sys_num:03d}]",
                    reason=(
                        f"Scan system {gal:02d}:{sys_num:03d} [Hold: Scan cooldown]"
                        if on_scan_cd
                        else (_safe_action_str(step.reason) or f"Scan system {gal:02d}:{sys_num:03d}")
                    ),
                    executable_now=bool(cfg.allow_galaxy_scan and not on_scan_cd),
                )
            )

    # Surface gated non-executable advisories when offers/targets exist but `allow_*` is False
    active_ptype = normalize_planet_type(active)
    if (
        not cfg.allow_research
        and active_ptype != "raid"
        and active.building_level(8) >= 1
        and not any(a.kind == "RESEARCH" for a in plan)
    ):
        for r_off in state.research_offers:
            if r_off.can_research:
                rk_off = _safe_action_str(r_off.research_key)
                cur_r_off = _safe_int((state.researches or {}).get(rk_off, 0), 0)
                plan.append(
                    _make_planned_action(
                        priority="P3",
                        kind="RESEARCH",
                        planet_id=active.planet_id,
                        planet_coords=active_coords_s,
                        target_id=r_off.research_id,
                        target_name=_safe_action_str(r_off.name),
                        target_level=_safe_level(r_off.target_level, default=cur_r_off + 1),
                        cost=r_off.cost,
                        missing=active.resources.missing(r_off.cost),
                        reason="Research ready [Hold: allow_research=false in Config]",
                        executable_now=False,
                        action_href=None,
                    )
                )
                break

    if (
        not cfg.allow_shipyard
        and active_ptype != "raid"
        and active.building_level(9) >= 1
        and not any(a.kind == "BUILD_SHIP" for a in plan)
    ):
        for s_off in active.ship_offers:
            if s_off.can_build:
                plan.append(
                    _make_planned_action(
                        priority="P3",
                        kind="BUILD_SHIP",
                        planet_id=active.planet_id,
                        planet_coords=active_coords_s,
                        target_id=s_off.unit_id,
                        target_name=_safe_action_str(s_off.name),
                        target_level=1,
                        cost=s_off.cost,
                        missing=active.resources.missing(s_off.cost),
                        reason="Shipyard unit ready [Hold: allow_shipyard=false in Config]",
                        executable_now=False,
                    )
                )
                break

    if not cfg.allow_raids and state.raid_targets and not any(a.kind == "ATTACK" for a in plan):
        own_coords = {normalize_coords(p.coords) for p in state.planets}
        atk_ships = {
            k: max(0, _safe_int(active.ships.get(k, 0), 0))
            for k in _COMBAT_AND_RAID_SHIP_KEYS
            if _safe_int(active.ships.get(k, 0), 0) > 0
        }
        sim_fleet = atk_ships if atk_ships else {"s1": 1}
        min_req = max(0.0, _safe_float_any(getattr(cfg, "raid_min_loot_spice_eq", 1500.0), 1500.0))
        for raw in state.raid_targets:
            if not isinstance(raw, Mapping):
                continue
            t_coords = _safe_action_str(raw.get("coords") or raw.get("target_coords")).strip()
            if not t_coords or normalize_coords(t_coords) in own_coords:
                continue
            is_spacia = bool(raw.get("is_spacia", False))
            scolor = _resolve_raid_target_status_color(raw)
            is_inact = scolor == "yellow"
            if not (is_inact or is_spacia or (bool(cfg.allow_pvp_in_az) and scolor == "light_red")):
                continue
            if not bash_tracker.can_attack(t_coords, is_inactive=is_inact, is_spacia=is_spacia, timestamp=live_now_ts):
                continue
            owner = _safe_action_str(raw.get("owner"))
            pname = _safe_action_str(raw.get("planet_name"))
            res_m = raw.get("resources") if isinstance(raw.get("resources"), Mapping) else raw
            raw_fleet_p3 = raw.get("fleet") if isinstance(raw.get("fleet"), Mapping) else {}
            raw_def_p3 = raw.get("defense") if isinstance(raw.get("defense"), Mapping) else {}
            rep = SpyReport(
                target_coords=t_coords,
                owner=owner,
                resources=_resources_from_dict(res_m),
                fleet={_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in raw_fleet_p3.items()},
                defense={_safe_action_str(k): max(0, _safe_int(v, 0)) for k, v in raw_def_p3.items()},
                dug_in=raw.get("dug_in"),
                is_spacia=is_spacia,
            )
            combat = simulate_raid_outcome(
                sim_fleet,
                rep,
                attacker_researches=state.researches,
                safety_margin=cfg.combat_safety_margin,
            )
            if not combat.safe_to_attack or combat.loot_spice_equivalent <= 0.0:
                continue
            exp_loot_sp, eff_win_prob, _ = _compute_effective_raid_expectation(
                combat.loot_spice_equivalent,
                combat.cargo_capacity,
                t_coords,
                resolved_stats,
            )
            scale_p3 = exp_loot_sp / combat.loot_spice_equivalent
            rounded_p3 = Resources(
                pig_iron=game_data.safe_scale_round_int(
                    combat.expected_loot.pig_iron, scale_p3, max_result=game_data.MAX_JSON_SAFE_INT
                ),
                metal=game_data.safe_scale_round_int(
                    combat.expected_loot.metal, scale_p3, max_result=game_data.MAX_JSON_SAFE_INT
                ),
                kryptonite=game_data.safe_scale_round_int(
                    combat.expected_loot.kryptonite, scale_p3, max_result=game_data.MAX_JSON_SAFE_INT
                ),
                spice=game_data.safe_scale_round_int(
                    combat.expected_loot.spice, scale_p3, max_result=game_data.MAX_JSON_SAFE_INT
                ),
            )
            if not rounded_p3.is_zero() and exp_loot_sp >= min_req - 1e-6:
                tag = "Spacia" if is_spacia else ("Inactive" if is_inact else scolor.title())
                plan.append(
                    _make_planned_action(
                        priority="P3",
                        kind="ATTACK",
                        planet_id=active.planet_id,
                        planet_coords=active_coords_s,
                        target_name=f"{t_coords} ({owner or pname or tag})",
                        reason=f"{tag} raid ~{_fmt_sp_int(exp_loot_sp)} SpEq (p={eff_win_prob:.0%}) [Hold: allow_raids=false in Config]",
                        executable_now=False,
                    )
                )
                break

    if not cfg.allow_galaxy_scan and not any(a.kind in ("SCAN_GALAXY", "ATTACK") for a in plan):
        has_probes = any(_safe_int(p.ships.get("s10", 0), 0) > 0 for p in state.planets)
        has_tut_fight = bool(
            state.tutorial
            and not state.tutorial.can_complete
            and any(
                not t.completed
                and t.category == "milestone"
                and any(w in _safe_action_str(t.description).lower() for w in ("fight", "attack", "combat", "battle"))
                for t in state.tutorial.tasks
            )
        )
        if has_probes or has_tut_fight:
            gal, sys_num = _parse_gal_sys(active_coords_s)
            if (gal, sys_num) not in recent_scans:
                plan.append(
                    _make_planned_action(
                        priority="P3",
                        kind="SCAN_GALAXY",
                        planet_id=active.planet_id,
                        planet_coords=active_coords_s,
                        target_name=f"Galaxy [{gal:02d}:{sys_num:03d}]",
                        reason=f"Scan system {gal:02d}:{sys_num:03d} [Hold: allow_galaxy_scan=false in Config]",
                        executable_now=False,
                    )
                )

    # Stable priority-band sort + deduplication matching `strategy.py`
    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    plan.sort(
        key=lambda a: (
            priority_rank.get(a.priority, 9),
            0 if a.executable_now else 1,
        )
    )
    deduped: list["PlannedAction"] = []
    seen_keys: dict[tuple[str, int, Optional[int], str], int] = {}
    for act in plan:
        key = (act.kind, act.planet_id, act.target_id, act.target_name)
        if key not in seen_keys:
            seen_keys[key] = len(deduped)
            deduped.append(act)
            continue
        if act.kind == "BUILD_SHIP":
            idx = seen_keys[key]
            existing = deduped[idx]
            if existing.executable_now == act.executable_now:
                qty1 = max(1, _safe_int(existing.target_level, 1))
                qty2 = max(1, _safe_int(act.target_level, 1))
                comb_qty = qty1 + qty2
                comb_cost = Resources(
                    pig_iron=existing.cost.pig_iron + act.cost.pig_iron,
                    metal=existing.cost.metal + act.cost.metal,
                    kryptonite=existing.cost.kryptonite + act.cost.kryptonite,
                    spice=existing.cost.spice + act.cost.spice,
                )
                if existing.executable_now:
                    comb_missing = Resources()
                else:
                    avail_snap = ship_avail_before.get(
                        (existing.planet_id, _safe_int(existing.target_id, 0))
                    )
                    if avail_snap is not None:
                        comb_missing = avail_snap.missing(comb_cost)
                    else:
                        comb_missing = Resources(
                            pig_iron=existing.missing.pig_iron + act.cost.pig_iron,
                            metal=existing.missing.metal + act.cost.metal,
                            kryptonite=existing.missing.kryptonite + act.cost.kryptonite,
                            spice=existing.missing.spice + act.cost.spice,
                        )
                base_reason = re.sub(r"\s*\(x\d+\)$", "", _safe_action_str(existing.reason), flags=re.ASCII)
                comb_reason = f"{base_reason} (x{game_data.fmt_compact_int(comb_qty)})"
                deduped[idx] = replace(
                    existing,
                    target_level=comb_qty,
                    cost=comb_cost,
                    missing=comb_missing,
                    reason=comb_reason,
                )
    return deduped


def _deterministic_seed(state: GameState, cfg: StrategyConfig, explicit_seed: Optional[int]) -> int:
    if explicit_seed is not None:
        return _sanitize_nn_seed(explicit_seed, 42)
    cfg_seed = getattr(cfg, "ga_seed", None)
    if cfg_seed is not None:
        return _sanitize_nn_seed(cfg_seed, 42)
    ap = state.active_planet()
    sig = (
        f"{game_data.fmt_compact_int(state.universe_id)}:"
        f"{game_data.fmt_compact_int(state.user_id)}:"
        f"{game_data.fmt_compact_int(ap.planet_id if ap else 0)}:{len(state.planets)}"
    )
    digest = hashlib.blake2b(sig.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big")


def run_optimizer(
    state: GameState,
    *,
    storage_warn_ratio: Optional[float] = None,
    config: Optional[StrategyConfig] = None,
    population_size: Optional[int] = None,
    generations: Optional[int] = None,
    horizon_steps: Optional[int] = None,
    mutation_rate: Optional[float] = None,
    nn_weight: Optional[float] = None,
    seed: Optional[int] = None,
    nn_backend: str = "auto",
    raid_stats: Optional[Mapping[str, Any]] = None,
    raid_history: Optional[Sequence[Mapping[str, Any]]] = None,
    attack_history_path: Optional[Path | str] = None,
    now_ts: Optional[float] = None,
    scanned_systems: Optional[Mapping[str, float]] = None,
    throttle_cooldowns: Optional[Mapping[Any, float]] = None,
) -> OptimizerRunSummary:
    """Run GA + Neural Network evolutionary optimization and return full summary & `PlannedAction`s."""
    global _LAST_OPTIMIZER_SUMMARY
    t0 = time.perf_counter()
    cfg = config or StrategyConfig()
    warn_ratio = _clamp_finite(
        cfg.storage_warn_ratio if storage_warn_ratio is None else storage_warn_ratio,
        0.85,
        0.05,
        1.0,
    )

    resolved_attack_stats = _resolve_complete_attack_history(
        state,
        raid_stats=raid_stats,
        raid_history=raid_history,
        attack_history_path=attack_history_path,
    )
    attack_summary_json = _json_safe(resolved_attack_stats)

    active = state.active_planet()
    resolved_seed = _deterministic_seed(state, cfg, seed)
    if active is None:
        empty_sim = simulate_action_sequence(
            state,
            (),
            config=cfg,
            storage_warn_ratio=warn_ratio,
            raid_stats=raid_stats,
            raid_history=raid_history,
            attack_history_path=attack_history_path,
            now_ts=now_ts,
            scanned_systems=scanned_systems,
            throttle_cooldowns=throttle_cooldowns,
        )
        summary = OptimizerRunSummary(
            actions=(),
            best_chromosome=(),
            best_simulation=empty_sim,
            generation_history=(),
            initial_best_fitness=0.0,
            final_best_fitness=0.0,
            initial_mean_fitness=0.0,
            final_mean_fitness=0.0,
            nn_initial_loss=0.0,
            nn_final_loss=0.0,
            nn_weight_delta_norm=0.0,
            nn_backend=NeuralValueNetwork(backend=nn_backend, seed=resolved_seed).backend,
            seed=resolved_seed,
            elapsed_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            generations=0,
            population_size=0,
            nn_evaluations=0,
            attack_history_stats=attack_summary_json,
        )
        with _LAST_SUMMARY_LOCK:
            _LAST_OPTIMIZER_SUMMARY = summary.to_dict()
        return summary

    pop_sz = max(4, min(200, _safe_int(population_size if population_size is not None else getattr(cfg, "ga_population_size", 24), 24)))
    gens = max(1, min(200, _safe_int(generations if generations is not None else getattr(cfg, "ga_generations", 12), 12)))
    horiz = max(1, min(24, _safe_int(horizon_steps if horizon_steps is not None else getattr(cfg, "ga_horizon_steps", 6), 6)))
    mut_r = _clamp_finite(mutation_rate if mutation_rate is not None else getattr(cfg, "ga_mutation_rate", 0.25), 0.25, 0.0, 1.0)
    nn_w = _clamp_finite(nn_weight if nn_weight is not None else getattr(cfg, "ga_nn_weight", 0.35), 0.35, 0.0, 1.0)

    (
        best_chrom,
        best_sim_res,
        history,
        nn_init_loss,
        nn_final_loss,
        nn_delta_norm,
        used_backend,
        nn_evals,
    ) = _evolve_sequences(
        state,
        cfg,
        warn_ratio,
        pop_size=pop_sz,
        generations=gens,
        horizon_steps=horiz,
        mutation_rate=mut_r,
        nn_weight=nn_w,
        seed=resolved_seed,
        nn_backend=nn_backend,
        resolved_attack_stats=resolved_attack_stats,
        now_ts=now_ts,
        scanned_systems=scanned_systems,
        throttle_cooldowns=throttle_cooldowns,
    )

    actions = _materialize_planned_actions(
        state,
        best_chrom,
        cfg,
        warn_ratio,
        attack_history_stats=resolved_attack_stats,
        now_ts=now_ts,
        scanned_systems=scanned_systems,
        throttle_cooldowns=throttle_cooldowns,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    summary = OptimizerRunSummary(
        actions=tuple(actions),
        best_chromosome=best_chrom,
        best_simulation=best_sim_res,
        generation_history=tuple(history),
        initial_best_fitness=history[0].best_fitness if history else 0.0,
        final_best_fitness=history[-1].best_fitness if history else 0.0,
        initial_mean_fitness=history[0].mean_fitness if history else 0.0,
        final_mean_fitness=history[-1].mean_fitness if history else 0.0,
        nn_initial_loss=round(nn_init_loss, 6),
        nn_final_loss=round(nn_final_loss, 6),
        nn_weight_delta_norm=round(nn_delta_norm, 6),
        nn_backend=used_backend,
        seed=resolved_seed,
        elapsed_ms=elapsed_ms,
        generations=gens,
        population_size=pop_sz,
        nn_evaluations=int(nn_evals),
        attack_history_stats=attack_summary_json,
    )
    with _LAST_SUMMARY_LOCK:
        _LAST_OPTIMIZER_SUMMARY = summary.to_dict()
    return summary


run_ga_nn_optimizer = run_optimizer

_RUN_OPTIMIZER_KWARGS: frozenset[str] = frozenset(
    {
        "population_size",
        "generations",
        "horizon_steps",
        "mutation_rate",
        "nn_weight",
        "seed",
        "nn_backend",
        "now_ts",
        "scanned_systems",
        "throttle_cooldowns",
    }
)


def optimize_plan_actions(
    state: GameState,
    *,
    storage_warn_ratio: Optional[float] = None,
    config: Optional[StrategyConfig] = None,
    raid_stats: Optional[Mapping[str, Any]] = None,
    raid_history: Optional[Sequence[Mapping[str, Any]]] = None,
    attack_history_path: Optional[Path | str] = None,
    **kwargs: Any,
) -> list["PlannedAction"]:
    """Public GA + NN optimizer entrypoint returning prioritized `list[PlannedAction]`."""
    forwarded = {k: v for k, v in kwargs.items() if k in _RUN_OPTIMIZER_KWARGS}
    summary = run_optimizer(
        state,
        storage_warn_ratio=storage_warn_ratio,
        config=config,
        raid_stats=raid_stats,
        raid_history=raid_history,
        attack_history_path=attack_history_path,
        **forwarded,
    )
    return list(summary.actions)
