"""Generated ratio/scaling/economy/prereq helper template for sync_wiki_data."""

from __future__ import annotations

_HELPER_CODE_MATH: str = '''

def _to_mant_exp2(val: int | float) -> tuple[float, int]:
    if isinstance(val, int):
        if val == 0:
            return 0.0, 0
        sign = -1.0 if val < 0 else 1.0
        aval = -val if val < 0 else val
        shift = max(0, aval.bit_length() - 53)
        return sign * float(aval >> shift), shift
    return math.frexp(float(val))


def safe_big_ratio(
    num: Any,
    den: Any,
    *,
    cap: float = 1e200,
    allow_negative: bool = False,
) -> float:
    if isinstance(num, bool) or isinstance(den, bool):
        return 0.0
    if not isinstance(num, (int, float)):
        num = safe_finite_float(num, 0.0)
    if not isinstance(den, (int, float)):
        den = safe_finite_float(den, 0.0)
    if isinstance(den, float) and (not math.isfinite(den) or den <= 0.0):
        return 0.0
    if isinstance(den, int) and den <= 0:
        return 0.0
    if isinstance(num, float) and not math.isfinite(num):
        return 0.0
    if not allow_negative and num <= 0:
        return 0.0
    if num == 0:
        return 0.0

    low_cap = -cap if allow_negative else 0.0
    if (
        isinstance(num, int)
        and isinstance(den, int)
        and abs(num).bit_length() <= 1020
        and den.bit_length() <= 1020
    ):
        val = num / den
        return max(low_cap, min(cap, val)) if math.isfinite(val) else 0.0

    if isinstance(num, float) and isinstance(den, float):
        try:
            val = num / den
            if math.isfinite(val):
                return max(low_cap, min(cap, val))
        except OverflowError:
            pass

    m_n, e_n = _to_mant_exp2(num)
    m_d, e_d = _to_mant_exp2(den)
    if m_d == 0.0 or m_n == 0.0:
        return 0.0
    m_q, r_e = math.frexp(m_n / m_d)
    total_e = r_e + e_n - e_d
    if total_e > 900:
        return -cap if (allow_negative and m_q < 0.0) else cap
    if total_e < -1074:
        return 0.0
    val = math.ldexp(m_q, total_e)
    return max(low_cap, min(cap, val)) if math.isfinite(val) else 0.0


def safe_scale_round_int(
    raw: Any,
    scale: float,
    *,
    default: int = 0,
    max_result: Optional[int] = None,
) -> int:
    raw_i = safe_int(raw, default)
    if raw_i <= 0 or isinstance(scale, bool) or not isinstance(scale, (int, float)):
        return 0
    scale_f = float(scale)
    if not math.isfinite(scale_f) or scale_f <= 0.0:
        return 0
    if raw_i.bit_length() <= 1000:
        prod = raw_i * scale_f
        if math.isfinite(prod):
            res = max(0, int(round(prod)))
            return min(res, max(0, int(max_result))) if max_result is not None else res
    mant, exp = math.frexp(scale_f)
    if max_result is not None:
        max_r = max(0, int(max_result))
        if raw_i.bit_length() + exp - 2 > max_r.bit_length():
            return max_r
    mant_i = int(round(mant * (1 << 53)))
    shift = max(0, raw_i.bit_length() - 256)
    num = (raw_i >> shift) * mant_i
    net = shift + exp - 53
    if net >= 0:
        res = num << net
    else:
        rshift = -net
        if rshift > num.bit_length() + 1:
            return 0
        half = 1 << (rshift - 1)
        res = (num + half) >> rshift
    res = max(0, res)
    if max_result is not None:
        return min(res, max(0, int(max_result)))
    return res


def fmt_compact_int(
    value: Any,
    *,
    signed: bool = False,
    grouped: bool = False,
    default: int = 0,
) -> str:
    v = safe_int(value, default)
    av = abs(v)
    if av.bit_length() >= _MAX_FORMAT_INT_BITS:
        sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
        exp = int(av.bit_length() * _LOG10_2)
        return f"{sign}~1e{exp}"
    if signed and grouped:
        return f"{v:+,}"
    if signed:
        return f"{v:+d}"
    if grouped:
        return f"{v:,}"
    return str(v)


def _safe_cost_int(value: Any) -> int:
    return safe_int(value, 0)


def _safe_cost_float(value: Any) -> float:
    return safe_finite_float(value, 0.0)


def _cost_dict(cost: Any) -> dict[str, int]:
    if cost is None:
        return {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}
    if isinstance(cost, Mapping):
        return {k: _safe_cost_int(cost.get(k, 0)) for k in ("pig_iron", "metal", "kryptonite", "spice")}
    return {
        "pig_iron": _safe_cost_int(getattr(cost, "pig_iron", 0)),
        "metal": _safe_cost_int(getattr(cost, "metal", 0)),
        "kryptonite": _safe_cost_int(getattr(cost, "kryptonite", 0)),
        "spice": _safe_cost_int(getattr(cost, "spice", 0)),
    }


def _cost_float_dict(cost: Any) -> dict[str, float]:
    if cost is None:
        return {"pig_iron": 0.0, "metal": 0.0, "kryptonite": 0.0, "spice": 0.0}
    if isinstance(cost, Mapping):
        return {k: _safe_cost_float(cost.get(k, 0.0)) for k in ("pig_iron", "metal", "kryptonite", "spice")}
    return {
        "pig_iron": _safe_cost_float(getattr(cost, "pig_iron", 0.0)),
        "metal": _safe_cost_float(getattr(cost, "metal", 0.0)),
        "kryptonite": _safe_cost_float(getattr(cost, "kryptonite", 0.0)),
        "spice": _safe_cost_float(getattr(cost, "spice", 0.0)),
    }


def spice_equivalent(cost_or_res: Any) -> float:
    """Convert any resource bundle to Spice equivalents at fixed Merchant parity."""
    vals = _cost_float_dict(cost_or_res)
    total = (
        vals["pig_iron"] / MERCHANT_RATES_IN_SPICE["pig_iron"]
        + vals["metal"] / MERCHANT_RATES_IN_SPICE["metal"]
        + vals["kryptonite"] / MERCHANT_RATES_IN_SPICE["kryptonite"]
        + vals["spice"] / MERCHANT_RATES_IN_SPICE["spice"]
    )
    return total if math.isfinite(total) else 0.0


def building_level_stats(building_id_or_slug: int | str, level: int) -> dict[str, Any]:
    """Return wiki table row for `(building, level)`, or `{}` if `level <= 0` or out of range."""
    lvl = safe_int(level, 0)
    if lvl <= 0 or lvl > 10_000:
        return {}
    info = resolve_unit("buildings", building_id_or_slug)
    if not info:
        return {}
    return info.get("levels", {}).get(lvl, {})


def building_cost(building_id_or_slug: int | str, target_level: int) -> dict[str, int]:
    """Return wiki resource cost dict for upgrading `building` to `target_level`."""
    row = building_level_stats(building_id_or_slug, target_level)
    cost = row.get("cost")
    if isinstance(cost, dict):
        return dict(cost)
    return {"pig_iron": 0, "metal": 0, "kryptonite": 0, "spice": 0}


def interpolate_temp_value(temp_map: Mapping[int, int], temperature: int | float) -> float:
    """Linearly interpolate a wiki temperature table for an arbitrary planet temperature."""
    if not temp_map or not isinstance(temp_map, Mapping):
        return 0.0
    temp = safe_finite_float(temperature, 0.0)
    pts_raw: list[tuple[int, float]] = []
    for k, v in temp_map.items():
        ki = safe_optional_int(k, None)
        if ki is None:
            continue
        pts_raw.append((cap_json_int(ki, limit=10**12), safe_finite_float(v, 0.0)))
    if not pts_raw:
        return 0.0
    pts = sorted(pts_raw)
    if temp <= pts[0][0]:
        return pts[0][1]
    if temp >= pts[-1][0]:
        return pts[-1][1]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= temp <= t1:
            if t1 == t0:
                return v0
            ratio = (temp - t0) / (t1 - t0)
            return v0 + ratio * (v1 - v0)
    return pts[-1][1]


def mine_production_per_hour(
    building_id: int,
    level: int,
    *,
    temperature: int = 0,
    mining_tech_level: int = 0,
) -> float:
    """Return hourly base output for mines (`b1..b4`), including temp curve & Mining tech (+2%/lvl).

    Blast furnace (`b2`) converts Pig iron to Metal and is not boosted by Mining technology.
    """
    lvl = safe_int(level, 0)
    bid = safe_int(building_id, 0)
    if lvl <= 0:
        return 0.0
    row = building_level_stats(bid, lvl)
    if not row:
        return 0.0
    if "prod_by_temp" in row and row["prod_by_temp"]:
        base = interpolate_temp_value(row["prod_by_temp"], temperature)
    else:
        base = float(row.get("prod", 0))
    safe_tech = max(0, min(100_000, _safe_cost_int(mining_tech_level)))
    if bid in (1, 3, 4) and safe_tech > 0:
        base *= 1.0 + MINING_TECH_BONUS_PER_LEVEL * safe_tech
    return base


def blast_furnace_pig_iron_used(level: int) -> float:
    """Return hourly Pig iron consumed by Blast furnace (`b2`) at `level`."""
    lvl = safe_int(level, 0)
    if lvl <= 0:
        return 0.0
    return float(building_level_stats(2, lvl).get("pig_iron_used", 0))


def mine_energy_needed(building_id_or_slug: int | str, level: int) -> int:
    """Return positive energy consumed by `building` at `level` (0 for power plants)."""
    lvl = safe_int(level, 0)
    if lvl <= 0:
        return 0
    val = int(building_level_stats(building_id_or_slug, lvl).get("energy", 0))
    return -val if val < 0 else 0


def building_energy_delta(building_id_or_slug: int | str, target_level: int) -> int:
    """Signed net energy change when upgrading `building` from `target_level - 1` to `target_level`."""
    tgt = safe_int(target_level, 0)
    if tgt <= 0:
        return 0
    cur_energy = int(building_level_stats(building_id_or_slug, tgt - 1).get("energy", 0))
    nxt_energy = int(building_level_stats(building_id_or_slug, tgt).get("energy", 0))
    return nxt_energy - cur_energy


def _current_level_for_req(
    req_cat: str,
    req_slug: str,
    buildings: Mapping[str, int],
    researches: Mapping[str, int],
) -> int:
    b_map = buildings if isinstance(buildings, Mapping) else {}
    r_map = researches if isinstance(researches, Mapping) else {}
    if req_cat == "buildings":
        info = BUILDINGS.get(req_slug)
        if not info or info.get("id") is None:
            return 0
        bid = int(info["id"])
        raw = b_map.get(f"b{bid}", b_map.get(str(bid), b_map.get(req_slug, 0)))
        return max(0, _safe_cost_int(raw))
    if req_cat == "research":
        info = RESEARCHES.get(req_slug)
        if info and info.get("id"):
            rid = str(info["id"])
            if rid in r_map:
                return max(0, _safe_cost_int(r_map[rid]))
        for k, v in r_map.items():
            if not isinstance(k, str):
                continue
            if normalize_unit_key(k) == normalize_unit_key(req_slug):
                return max(0, _safe_cost_int(v))
        return 0
    return 0


def missing_prerequisites(
    category: str,
    slug_or_id: str | int,
    *,
    buildings: Mapping[str, int],
    researches: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Return list of unmet prerequisites `{category, slug, name, required, current}`."""
    info = resolve_unit(category, slug_or_id)
    if not info:
        return []
    missing: list[dict[str, Any]] = []
    for req in info.get("requirements", []):
        req_cat = str(req["category"])
        req_slug = str(req["slug"])
        req_lvl = int(req["level"])
        cur_lvl = _current_level_for_req(req_cat, req_slug, buildings, researches)
        if cur_lvl < req_lvl:
            missing.append(
                {
                    "category": req_cat,
                    "slug": req_slug,
                    "name": str(req["name"]),
                    "required": req_lvl,
                    "current": cur_lvl,
                }
            )
    return missing
'''
