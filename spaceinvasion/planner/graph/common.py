"""Shared constants, coercers, unit resolvers, and classifiers for planner DAG builders."""

from __future__ import annotations

from collections.abc import Mapping
import math
import re

from spaceinvasion.combat.flight import normalize_coords
from spaceinvasion.core.catalog import (
    BUILDING_BY_ID,
    RESEARCH_BY_ID,
    SHIPS,
    cap_json_int,
    resolve_unit,
    safe_finite_float,
    safe_int,
    safe_key,
    safe_optional_int,
)
from spaceinvasion.planner.graph._dag import (
    EDGE_RANK,
    EDGE_TYPE_PRIORITY,
    _freeze_sig_tree,
    assemble_lanes,
    diff_strategy_configs,
    freeze_sig_tree,
    has_path,
    kahn_longest_layers,
    merge_strategy_config_overrides,
    serialize_config,
)

__all__ = [
    "EDGE_RANK",
    "EDGE_TYPE_PRIORITY",
    "MINE_RESOURCE_BY_BID",
    "RESOURCE_KEYS",
    "RESOURCE_LABELS",
    "SHIP_SLUG_BY_ID",
    "WAREHOUSE_RESOURCE_BY_BID",
    "_freeze_sig_tree",
    "assemble_lanes",
    "classify_node_category",
    "classify_node_status",
    "clean_float",
    "clean_int",
    "clean_optional_float",
    "clean_optional_int",
    "clean_str",
    "diff_strategy_configs",
    "extract_first_coords",
    "extract_system_key",
    "format_eta_short",
    "freeze_sig_tree",
    "has_path",
    "kahn_longest_layers",
    "merge_strategy_config_overrides",
    "res_to_dict",
    "resolve_building_id",
    "resolve_research_key",
    "resolve_ship_id_and_slug",
    "serialize_config",
    "slot_key_for_action",
]

SHIP_SLUG_BY_ID: dict[int, str] = {
    1: "small-transport",
    2: "large-transport",
    3: "transmitter",
    4: "small-fighter",
    5: "warship",
    6: "starcruiser",
    7: "phoenix",
    8: "colony-ship",
    9: "solar-cells",
    10: "spy-probe",
    11: "recycler",
    13: "battleship",
    14: "stealth-bomber",
    15: "destroyer",
    16: "imperial-starbase",
}

RESOURCE_KEYS: tuple[str, ...] = ("pig_iron", "metal", "kryptonite", "spice")
MINE_RESOURCE_BY_BID: dict[int, str] = {1: "pig_iron", 2: "metal", 3: "kryptonite", 4: "spice"}
WAREHOUSE_RESOURCE_BY_BID: dict[int, str] = {11: "pig_iron", 12: "metal", 13: "kryptonite", 14: "spice"}
RESOURCE_LABELS: dict[str, str] = {
    "pig_iron": "Pig iron",
    "metal": "Metal",
    "kryptonite": "Kryptonite",
    "spice": "Spice",
}

_SYS_COORDS_RE = re.compile(r"(\d{1,2}):(\d{1,3})", re.ASCII)
_COORDS_FULL_RE = re.compile(r"(\d{1,2}):(\d{1,3}):(\d{1,2})", re.ASCII)


def clean_int(value: object, default: int = 0) -> int:
    return cap_json_int(safe_int(value, default))


def clean_optional_int(value: object) -> int | None:
    parsed = safe_optional_int(value, None)
    return None if parsed is None else cap_json_int(parsed)


def clean_float(value: object, default: float = 0.0) -> float:
    val = safe_finite_float(value, default)
    return round(val, 4) if math.isfinite(val) else default


def clean_optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        val = float(value)
    elif isinstance(value, str):
        try:
            val = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return round(val, 4) if math.isfinite(val) else None


def clean_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    return value if isinstance(value, str) else safe_key(value)


def res_to_dict(res: object) -> dict[str, int]:
    if res is None:
        return {rk: 0 for rk in RESOURCE_KEYS}
    if isinstance(res, Mapping):
        return {rk: max(0, clean_int(res.get(rk, 0), 0)) for rk in RESOURCE_KEYS}
    return {rk: max(0, clean_int(getattr(res, rk, 0), 0)) for rk in RESOURCE_KEYS}


def format_eta_short(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "—"
    if seconds <= 0.5:
        return "Ready"
    total = int(round(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def resolve_building_id(target_id: object, target_name: str) -> int | None:
    tid = clean_optional_int(target_id)
    if tid is not None and tid in BUILDING_BY_ID:
        return tid
    if isinstance(target_id, str):
        s = target_id.strip().lower()
        sub = s[1:] if s.startswith("b") else s
        if sub.isascii() and sub.isdigit() and len(sub) <= 18:
            bid = int(sub)
            if bid in BUILDING_BY_ID:
                return bid
    if target_name:
        info = resolve_unit("buildings", target_name)
        if info and isinstance(info.get("id"), int):
            bid = int(info["id"])
            if bid in BUILDING_BY_ID:
                return bid
    return None


def resolve_research_key(target_id: object, target_name: str) -> str | None:
    if isinstance(target_id, str):
        s = target_id.strip().lower()
        if s in RESEARCH_BY_ID:
            return s
        sub = s[1:] if s.startswith("r") else s
        if sub.isascii() and sub.isdigit() and len(sub) <= 18:
            rk = f"r{int(sub)}"
            if rk in RESEARCH_BY_ID:
                return rk
    tid = clean_optional_int(target_id)
    if tid is not None and 1 <= tid <= 100:
        rk = f"r{tid}"
        if rk in RESEARCH_BY_ID:
            return rk
    if target_name:
        info = resolve_unit("research", target_name)
        if info and info.get("id"):
            rk = str(info["id"])
            if rk in RESEARCH_BY_ID:
                return rk
    return None


def resolve_ship_id_and_slug(target_id: object, target_name: str) -> tuple[int | None, str | None]:
    tid = clean_optional_int(target_id)
    if tid is not None and tid in SHIP_SLUG_BY_ID:
        return tid, SHIP_SLUG_BY_ID[tid]
    if isinstance(target_id, str):
        s = target_id.strip().lower()
        sub = s[1:] if s.startswith("s") else s
        if sub.isascii() and sub.isdigit() and len(sub) <= 18:
            sid = int(sub)
            if sid in SHIP_SLUG_BY_ID:
                return sid, SHIP_SLUG_BY_ID[sid]
        if s in SHIPS:
            for sid, slug in SHIP_SLUG_BY_ID.items():
                if slug == s:
                    return sid, slug
            return None, s
    if target_name:
        clean_name = re.sub(r"\s*\(x\d+\)\s*$", "", target_name.strip(), flags=re.IGNORECASE)
        info = resolve_unit("ships", clean_name)
        if info and info.get("slug"):
            slug = str(info["slug"])
            for sid, s_slug in SHIP_SLUG_BY_ID.items():
                if s_slug == slug:
                    return sid, slug
            return None, slug
    return None, None


def extract_system_key(text: str) -> str | None:
    if not text:
        return None
    m = _SYS_COORDS_RE.search(text)
    return f"{int(m.group(1)):02d}:{int(m.group(2)):03d}" if m else None


def extract_first_coords(text: str) -> str:
    if not text:
        return ""
    m = _COORDS_FULL_RE.search(text)
    return normalize_coords(f"{int(m.group(1))}:{int(m.group(2))}:{int(m.group(3))}") if m else ""


def classify_node_category(kind: str, bid: int | None, sid: int | None) -> str:
    if kind in ("ALERT_HOSTILE", "ESCAPE_FLIGHT"):
        return "survival"
    if kind == "THROTTLE_BUILDING":
        return "throttle"
    if kind in ("CLAIM_TUTORIAL", "TUTORIAL_TASK"):
        return "tutorial"
    if kind == "RESEARCH":
        return "research"
    if kind in ("SCAN_GALAXY", "SPY", "TRANSPORT", "STATION", "RECYCLE", "COLONIZE", "ABANDON_COLONY"):
        return "recon"
    if kind == "ATTACK":
        return "combat"
    if kind == "BUILD_SHIP":
        return "energy" if sid == 9 else "shipyard"
    if kind in ("BUILD", "WAIT_RESOURCES", "DEMOLISH_BUILDING"):
        if bid in (5, 6):
            return "energy"
        if bid in (11, 12, 13, 14):
            return "storage"
        return "economy"
    return "economy"


def classify_node_status(kind: str, executable_now: bool, missing_dict: Mapping[str, int], reason: str) -> str:
    if executable_now:
        return "ready"
    r_low = reason.lower()
    if (
        kind == "ALERT_HOSTILE"
        or "[hold:" in r_low
        or "allow_" in r_low
        or "prerequisites unmet" in r_low
        or "required)" in r_low
        or "in transit" in r_low
    ):
        return "blocked_gate"
    if any(v > 0 for v in missing_dict.values()):
        return "waiting_resources"
    if any(tok in r_low for tok in ("queue full", "future/queued", "waiting/queued", "in research", "busy")):
        return "queued"
    return "waiting_resources" if kind == "WAIT_RESOURCES" else "queued"


def slot_key_for_action(kind: str, planet_id: int) -> str | None:
    if kind == "RESEARCH":
        return "global:research"
    if planet_id <= 0:
        return None
    if kind in ("BUILD", "WAIT_RESOURCES", "DEMOLISH_BUILDING"):
        return f"planet:{planet_id}:build"
    if kind == "BUILD_SHIP":
        return f"planet:{planet_id}:shipyard"
    if kind in (
        "ESCAPE_FLIGHT",
        "SCAN_GALAXY",
        "SPY",
        "TRANSPORT",
        "STATION",
        "ATTACK",
        "RECYCLE",
        "COLONIZE",
        "ABANDON_COLONY",
    ):
        return f"planet:{planet_id}:fleet"
    return None
