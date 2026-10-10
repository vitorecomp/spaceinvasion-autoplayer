"""Safe numeric coercion, ratio scaling, and compact integer formatting helpers.

Pure stdlib helpers extracted from `spaceinvasion.core.game_data` with strict
type annotations and bounded resource guards.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import SupportsFloat, SupportsIndex, SupportsInt

MAX_JSON_SAFE_INT = 10**200
_MAX_FORMAT_INT_BITS = 12_000
_LOG10_2 = 0.3010299956639812

_NON_NUMERIC_TOKENS: frozenset[str] = frozenset(
    {
        "null",
        "none",
        "~",
        "nan",
        "inf",
        "+inf",
        "-inf",
        "infinity",
        "+infinity",
        "-infinity",
    }
)
_COERCE_ERRORS = (ValueError, TypeError, OverflowError, RuntimeError, RecursionError)


def _coerce_optional_int(value: object) -> int | None:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    raw: object = value
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, str):
        txt = raw.strip()
        if not txt or not txt.isascii() or txt.lower() in _NON_NUMERIC_TOKENS:
            return None
        raw = txt
    if isinstance(raw, (str, SupportsInt, SupportsIndex)):
        try:
            return int(raw)
        except _COERCE_ERRORS:
            pass
    if isinstance(raw, (str, SupportsFloat, SupportsIndex)):
        try:
            f = float(raw)
        except _COERCE_ERRORS:
            return None
        return int(f) if math.isfinite(f) else None
    return None


def safe_int(value: object, default: int = 0) -> int:
    """Coerce an arbitrary value to `int`, returning `default` on non-finite or invalid input."""
    parsed = _coerce_optional_int(value)
    return default if parsed is None else parsed


def safe_optional_int(value: object, default: int | None = None) -> int | None:
    """Coerce an arbitrary value to `int | None`, preserving `default` on failure."""
    parsed = _coerce_optional_int(value)
    return default if parsed is None else parsed


def safe_finite_float(value: object, default: float = 0.0) -> float:
    """Coerce an arbitrary value to a finite `float`, returning `default` on invalid input."""
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return default
    raw: object = value
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if isinstance(raw, str):
        txt = raw.strip()
        if not txt or not txt.isascii() or txt.lower() in ("null", "none", "~"):
            return default
        raw = txt
    if not isinstance(raw, (str, SupportsFloat, SupportsIndex)):
        return default
    try:
        f = float(raw)
    except OverflowError:
        if isinstance(raw, int):
            return 1e300 if raw > 0 else -1e300
        return default
    except (ValueError, TypeError, RuntimeError, RecursionError):
        return default
    return f if math.isfinite(f) else default


def cap_json_int(value: object, *, limit: int = MAX_JSON_SAFE_INT, default: int = 0) -> int:
    """Clamp an integer (or coercible value) to `[-limit, limit]` for safe JSON serialization."""
    v = value if (isinstance(value, int) and not isinstance(value, bool)) else safe_int(value, default)
    if v > limit:
        return limit
    if v < -limit:
        return -limit
    return v


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
    num: object,
    den: object,
    *,
    cap: float = 1e200,
    allow_negative: bool = False,
) -> float:
    """Compute `num / den` safely for arbitrarily large integers and floats without overflow."""
    if isinstance(num, bool) or isinstance(den, bool):
        return 0.0
    n_val: int | float = num if isinstance(num, (int, float)) else safe_finite_float(num, 0.0)
    d_val: int | float = den if isinstance(den, (int, float)) else safe_finite_float(den, 0.0)
    if isinstance(d_val, float) and (not math.isfinite(d_val) or d_val <= 0.0):
        return 0.0
    if isinstance(d_val, int) and d_val <= 0:
        return 0.0
    if isinstance(n_val, float) and not math.isfinite(n_val):
        return 0.0
    if (not allow_negative and n_val <= 0) or n_val == 0:
        return 0.0

    low_cap = -cap if allow_negative else 0.0
    if (
        isinstance(n_val, int)
        and isinstance(d_val, int)
        and abs(n_val).bit_length() <= 1020
        and d_val.bit_length() <= 1020
    ):
        val = n_val / d_val
        return max(low_cap, min(cap, val)) if math.isfinite(val) else 0.0

    if isinstance(n_val, float) and isinstance(d_val, float):
        try:
            val = n_val / d_val
            if math.isfinite(val):
                return max(low_cap, min(cap, val))
        except OverflowError:
            pass

    m_n, e_n = _to_mant_exp2(n_val)
    m_d, e_d = _to_mant_exp2(d_val)
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
    raw: object,
    scale: float,
    *,
    default: int = 0,
    max_result: int | None = None,
) -> int:
    """Scale a non-negative integer by a positive finite float with exact rounding and bit safety."""
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
    value: object,
    *,
    signed: bool = False,
    grouped: bool = False,
    default: int = 0,
) -> str:
    """Format an integer safely without triggering Python's str-to-int digit limit."""
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
