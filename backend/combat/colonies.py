"""Pure SpaceInvasion Classic 300+-field colony candidate grading and ranking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Protocol, TypeVar

from backend.combat.flight import normalize_coords, parse_coords_distance
from backend.core.catalog import safe_int, safe_optional_int


class ColonySlotLike(Protocol):
    """Read-only structural protocol satisfied by both new and legacy `GalaxySlot` objects."""

    @property
    def coords(self) -> str: ...

    @property
    def position(self) -> int: ...

    @property
    def status_color(self) -> str: ...


class ColonyReportLike(Protocol):
    """Read-only structural protocol satisfied by uninhabited-planet spy reports."""

    @property
    def uninhabited_fields(self) -> int | None: ...


S = TypeVar("S", bound=ColonySlotLike)


def _orbital_zone_meta(pos: int) -> tuple[str, str]:
    if 7 <= pos <= 12:
        return ("Prime Large-Field Belt (Pos 7-12)", "10°C .. 55°C")
    if 13 <= pos <= 16:
        return ("Cold Spice Belt (Pos 13-16)", "-70°C .. 5°C")
    if 4 <= pos <= 6:
        return ("Temperate Mid Belt (Pos 4-6)", "55°C .. 95°C")
    return ("Hot Inner Core (Pos 1-3)", "100°C .. 220°C")


def _grade_confirmed_fields(
    fields_val: int,
    min_tgt: int,
    dist_penalty: int,
    zone_label: str,
) -> tuple[str, int, bool, str, str, str]:
    fields_display = f"{fields_val} fields"
    if fields_val >= 310:
        grade = "S"
        base_score = min(100, 95 + (fields_val - 310) // 4)
    elif fields_val >= 300:
        grade = "A+"
        base_score = 90 + min(4, int((fields_val - 300) * 0.5))
    elif fields_val >= 270:
        grade = "A"
        base_score = 78 + min(11, int((fields_val - 270) * 0.4))
    elif fields_val >= 230:
        grade = "B"
        base_score = 62 + min(15, int((fields_val - 230) * 0.4))
    elif fields_val >= 180:
        grade = "C"
        base_score = 40 + min(21, int((fields_val - 180) * 0.42))
    else:
        grade = "D"
        base_score = max(5, min(39, int(fields_val * 0.22)))
    score = max(1, min(100, base_score - dist_penalty))
    meets_target = fields_val >= min_tgt
    recommendation = "COLONIZE_NOW" if meets_target else "SKIP_TOO_SMALL"
    if meets_target:
        reason = f"Confirmed {fields_val} fields >= {min_tgt} target ({zone_label}) — ready to colonize"
    else:
        reason = f"Confirmed {fields_val} fields < {min_tgt} target — skip for {min_tgt}+ end-game focus"
    return grade, score, meets_target, recommendation, reason, fields_display


def _grade_range_ecology(
    c_min_val: int | None,
    c_max_val: int,
    min_tgt: int,
    dist_penalty: int,
    temp_val: int | None,
) -> tuple[str, int, bool, str, str, str]:
    eff_min_f = c_min_val if (c_min_val is not None and c_min_val <= c_max_val) else c_max_val
    fields_display = f"{eff_min_f}–{c_max_val} fields" if eff_min_f != c_max_val else f"{c_max_val} fields"
    meets_target = c_max_val >= min_tgt
    if c_max_val >= 340:
        grade = "S" if meets_target else "B"
        base_score = min(100, 95 + max(0, (eff_min_f - 130) // 5) + max(0, (c_max_val - 340) // 5))
    elif c_max_val >= 310:
        grade = "A+" if meets_target else "B"
        base_score = 90 + min(4, max(0, (c_max_val - 310) // 8))
    elif c_max_val >= 300:
        grade = "A" if meets_target else "B"
        base_score = 84 + min(5, max(0, (c_max_val - 300) // 2))
    elif c_max_val >= 270:
        grade = "A" if meets_target else "B"
        base_score = 68 + min(12, int((c_max_val - 270) * 0.4))
    elif c_max_val >= 230:
        grade = "B" if meets_target else "C"
        base_score = 50 + min(15, int((c_max_val - 230) * 0.35))
    else:
        grade = "D"
        base_score = max(10, min(44, int(c_max_val * 0.18)))
    score = max(1, min(100, base_score - dist_penalty))
    recommendation = "COLONIZE_NOW" if meets_target else "SKIP_TOO_SMALL"
    temp_suffix = f", {temp_val}°C" if temp_val is not None else ""
    if meets_target:
        reason = (
            f"Scouted {eff_min_f}–{c_max_val} fields (max {c_max_val} >= {min_tgt} target{temp_suffix}) "
            "— ready to colonize"
        )
    else:
        reason = (
            f"Scouted {eff_min_f}–{c_max_val} fields (max {c_max_val} < {min_tgt} target{temp_suffix}) "
            f"— skip for {min_tgt}+ end-game focus"
        )
    return grade, score, meets_target, recommendation, reason, fields_display


def _grade_unprobed_slot(pos: int, min_tgt: int, dist_penalty: int) -> tuple[str, int, bool, str, str, str]:
    if 7 <= pos <= 12:
        pot = 82 - int(abs(pos - 9.5) * 3.0)
        rec = "PROBE_PRIME_SLOT"
        reason = f"Unprobed prime slot (pos {pos}) — highest statistical odds for {min_tgt}+ fields"
    elif 4 <= pos <= 6:
        pot = 56 - abs(pos - 5) * 3
        rec = "PROBE_SECONDARY"
        reason = f"Unprobed temperate slot (pos {pos}) — moderate field odds; probe after pos 7-12"
    elif 13 <= pos <= 16:
        pot = 48 - abs(pos - 14) * 2
        rec = "PROBE_COLD_SPICE"
        reason = f"Unprobed cold Spice slot (pos {pos}) — bonus Spice output, typically smaller field roll"
    else:
        pot = 30
        rec = "LOW_FIELD_ODDS"
        reason = f"Unprobed hot inner slot (pos {pos}) — low probability of {min_tgt}+ fields"
    return "?", max(5, min(85, pot - dist_penalty)), False, rec, reason, ""


def grade_colony_candidate(
    first_coords: str = "",
    second_coords: str | None = None,
    *,
    coords: str | None = None,
    origin_coords: str = "",
    position: int | None = None,
    uninhabited_fields: int | None = None,
    colonize_min_fields: int | None = None,
    colonize_max_fields: int | None = None,
    temperature: int | None = None,
    spied: bool = False,
    min_fields_target: int = 300,
    last_spied_ts: float = 0.0,
    last_scanned_ts: float = 0.0,
) -> dict[str, object]:
    """Grade an uninhabited/free galaxy slot (`S`..`D` when spied/scouted, `?` when unprobed)."""
    if coords is not None:
        eff_coords, eff_origin = coords, origin_coords or first_coords
    elif second_coords is not None:
        eff_coords, eff_origin = second_coords, origin_coords or first_coords
    else:
        eff_coords, eff_origin = first_coords, origin_coords

    norm_c = normalize_coords(eff_coords)
    m = re.fullmatch(r"(\d+):(\d+):(\d+)", norm_c, re.ASCII)
    gal, sys_num = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    pos = int(position) if (position is not None and 1 <= int(position) <= 16) else (int(m.group(3)) if m else 0)

    norm_orig = normalize_coords(eff_origin) if eff_origin else norm_c
    m_orig = re.fullmatch(r"(\d+):(\d+):(\d+)", norm_orig, re.ASCII)
    orig_gal, orig_sys = (int(m_orig.group(1)), int(m_orig.group(2))) if m_orig else (gal, sys_num)
    sys_dist = abs(gal - orig_gal) * 400 + abs(sys_num - orig_sys) if (gal > 0 and sys_num > 0) else 0

    dist = parse_coords_distance(norm_orig or norm_c, norm_c) if norm_c else 0.0
    dist_penalty = min(5, int(max(0.0, dist - 1000.0) / 800.0))
    min_tgt = max(50, min(600, safe_int(min_fields_target, 300)))
    zone_label, est_temp = _orbital_zone_meta(pos)

    fields_val = safe_optional_int(uninhabited_fields, None)
    if fields_val is not None and fields_val <= 0:
        fields_val = None
    c_min_val = safe_optional_int(colonize_min_fields, None)
    if c_min_val is not None and c_min_val <= 0:
        c_min_val = None
    c_max_val = safe_optional_int(colonize_max_fields, None)
    if c_max_val is not None and c_max_val <= 0:
        c_max_val = None
    if c_min_val is not None and c_max_val is not None and c_min_val > c_max_val:
        c_min_val, c_max_val = c_max_val, c_min_val
    temp_val = safe_optional_int(temperature, None)
    if temp_val is not None and not (-300 <= temp_val <= 600):
        temp_val = None

    has_range_ecology = c_max_val is not None
    is_spied = bool(spied or fields_val is not None or has_range_ecology or last_spied_ts > 0.0)
    is_probed = bool((is_spied and fields_val is not None) or has_range_ecology)

    if fields_val is not None:
        grade, score, meets_target, rec, reason, fields_display = _grade_confirmed_fields(
            fields_val, min_tgt, dist_penalty, zone_label
        )
    elif has_range_ecology and c_max_val is not None:
        grade, score, meets_target, rec, reason, fields_display = _grade_range_ecology(
            c_min_val, c_max_val, min_tgt, dist_penalty, temp_val
        )
    else:
        grade, score, meets_target, rec, reason, fields_display = _grade_unprobed_slot(pos, min_tgt, dist_penalty)

    return {
        "coords": norm_c,
        "galaxy": gal,
        "system": sys_num,
        "position": pos,
        "spied": is_probed,
        "probed": is_probed,
        "uninhabited_fields": fields_val,
        "colonize_min_fields": c_min_val,
        "colonize_max_fields": c_max_val,
        "temperature": temp_val,
        "fields_display": fields_display,
        "grade": grade,
        "score": int(score),
        "meets_target": bool(meets_target),
        "min_fields_target": min_tgt,
        "zone_label": zone_label,
        "orbital_zone": zone_label,
        "orbital_band": f"{zone_label} ({temp_val}°C)" if temp_val is not None else f"{zone_label} ({est_temp})",
        "est_temp_range": est_temp,
        "distance": round(float(dist), 1),
        "system_distance": int(sys_dist),
        "recommendation": rec,
        "reason": reason,
        "last_spied_ts": float(last_spied_ts or 0.0),
        "last_scanned_ts": float(last_scanned_ts or 0.0),
    }


def pick_colony_candidates(
    slots: Sequence[S],
    *,
    goal: str = "large_fields",
    min_fields_target: int = 300,
    uninhabited_reports: Mapping[str, ColonyReportLike] | None = None,
) -> list[S]:
    """Filter & rank free galaxy slots for large economy colonies (pos 7-12, 300+ fields) or cold Spice (13-16)."""
    reports = uninhabited_reports or {}
    filtered: list[tuple[float, S]] = []
    for slot in slots:
        if slot.status_color != "free":
            continue
        rep = reports.get(slot.coords)
        if rep is not None and rep.uninhabited_fields is not None and rep.uninhabited_fields < min_fields_target:
            if goal != "cold_spice":
                continue
        if goal == "cold_spice":
            pos_score = 100.0 - abs(slot.position - 15) * 15.0
        else:
            pos_score = 100.0 - abs(slot.position - 9.5) * 12.0
        if rep is not None and rep.uninhabited_fields:
            pos_score += rep.uninhabited_fields * 0.25
        filtered.append((pos_score, slot))

    filtered.sort(key=lambda pair: (-pair[0], pair[1].position))
    return [slot for _, slot in filtered]
