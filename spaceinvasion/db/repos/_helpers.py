"""Pure stdlib normalization, coordinate, and JSON helpers for ORM repositories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import math
import re
import time

from sqlalchemy.engine import CursorResult

PROTECTED_META_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "schema_version_tag",
        "schema_checksum",
    }
)

_COORDS_RE = re.compile(r"^\s*(\d{1,3})\s*[:\-/]\s*(\d{1,4})\s*[:\-/]\s*(\d{1,3})\s*$", re.ASCII)
_SID_RE = re.compile(r"(?i)\b(sid|session_id|phpsessid|token)\s*([=:])\s*([A-Za-z0-9._~+/=-]{4,})")
_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1


def round_ts(ts: float | None = None) -> float:
    """Return a finite epoch timestamp rounded to milliseconds."""
    val = float(ts) if (ts is not None and math.isfinite(float(ts))) else time.time()
    return round(val, 3)


def redact_text(text: str) -> str:
    """Redact session-id/token query fragments from persisted strings."""
    return _SID_RE.sub(r"\1\2<redacted>", text)


def sanitize_text(raw: object, max_len: int | None = None) -> str:
    """Convert `raw` to clean redacted text and optionally truncate."""
    if raw is None:
        return ""
    out = redact_text(str(raw))
    if max_len is not None and len(out) > max_len:
        return out[:max_len]
    return out


def clamp_int64(value: int) -> int:
    """Clamp an integer to signed PostgreSQL `BIGINT` bounds."""
    if value < _INT64_MIN:
        return _INT64_MIN
    if value > _INT64_MAX:
        return _INT64_MAX
    return value


def normalize_coords(raw: str) -> str:
    """Normalize SpaceInvasion coordinates like `'2:380:4'` to `'02:380:04'`."""
    cleaned = raw.strip()
    if (
        len(cleaned) == 9
        and cleaned[2] == ":"
        and cleaned[6] == ":"
        and cleaned[0:2].isascii()
        and cleaned[0:2].isdigit()
        and cleaned[3:6].isascii()
        and cleaned[3:6].isdigit()
        and cleaned[7:9].isascii()
        and cleaned[7:9].isdigit()
    ):
        return cleaned
    match = _COORDS_RE.match(cleaned)
    if match:
        return f"{int(match.group(1)):02d}:{int(match.group(2)):03d}:{int(match.group(3)):02d}"
    return cleaned


def parse_coords_gsp(raw: str) -> tuple[int, int, int] | None:
    """Parse normalized or raw coordinates into `(galaxy, system, position)`."""
    match = _COORDS_RE.match(raw.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def coord_query_variants(raw: str) -> tuple[str, ...]:
    """Return canonical + unpadded coordinate variants for exact SQL `IN` lookups."""
    cleaned = raw.strip()
    if not cleaned:
        return ()
    norm = normalize_coords(cleaned)
    variants: list[str] = [norm]
    parsed = parse_coords_gsp(norm)
    if parsed is not None:
        g, s, p = parsed
        unpadded = f"{g}:{s}:{p}"
        if unpadded not in variants:
            variants.append(unpadded)
    if cleaned not in variants:
        variants.append(cleaned)
    return tuple(variants)


def normalize_building_key(raw: str) -> str:
    """Normalize numeric building identifiers to canonical `b<id>` form."""
    txt = sanitize_text(raw).strip().lower()
    if not txt:
        return ""
    if txt.isascii() and txt.isdigit():
        return f"b{int(txt)}"
    if txt.startswith("b") and txt[1:].isascii() and txt[1:].isdigit():
        return f"b{int(txt[1:])}"
    return txt[:32]


def _sanitize_json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {redact_text(str(k)): _sanitize_json_value(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_sanitize_json_value(item) for item in value]
    return redact_text(str(value))


def dumps_json(payload: object) -> str:
    """Serialize `payload` to canonical compact redacted JSON matching `storage.py`."""
    clean = _sanitize_json_value(payload)
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), allow_nan=False)


def loads_json_dict(raw: str) -> dict[str, object]:
    """Parse a JSON string into a plain dictionary, returning `{}` on invalid input."""
    stripped = raw.strip()
    if not stripped:
        return {}
    try:
        parsed: object = json.loads(stripped)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, object] = {}
    for k, v in parsed.items():
        out[str(k)] = v
    return out


def affected_rows(result: object) -> int:
    """Return `rowcount` from a SQLAlchemy `CursorResult` safely without `Any`."""
    if isinstance(result, CursorResult):
        return int(result.rowcount or 0)
    return 0


def as_str_dict(raw: object) -> dict[str, object]:
    """Narrow a mapping-like value into a plain `dict[str, object]`."""
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): v for k, v in raw.items()}


def to_float(raw: object, default: float = 0.0) -> float:
    """Parse a float value safely with a fallback default."""
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            return default
    return default


def to_int(raw: object, default: int = 0) -> int:
    """Parse an integer value safely with a fallback default."""
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(float(raw.strip()))
        except ValueError:
            return default
    return default


RESOURCE_KEYS: tuple[str, ...] = ("pig_iron", "metal", "kryptonite", "spice")
_LOSS_TOKENS: tuple[str, ...] = (
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
_WIN_TOKENS: tuple[str, ...] = (
    "win",
    "won",
    "vict",
    "succ",
    "loot",
    "plunder",
    "captur",
    "ok",
    "dispatch",
)


def spice_equivalent_from_counts(pig_iron: int, metal: int, kryptonite: int, spice: int) -> float:
    """Convert resource counts into Spice equivalents using fixed Classic Merchant parity."""
    total = (
        max(0, int(pig_iron)) / 6.0
        + max(0, int(metal)) / 2.5
        + max(0, int(kryptonite)) / 3.0
        + float(max(0, int(spice)))
    )
    return total if math.isfinite(total) else 0.0


def normalize_loot_dict(raw: Mapping[str, object] | str | None) -> dict[str, int]:
    """Normalize a 4-resource loot dict or JSON string into non-negative `BIGINT` counts."""
    parsed: Mapping[str, object] = {}
    if isinstance(raw, str):
        parsed = loads_json_dict(raw)
    elif isinstance(raw, Mapping):
        parsed = raw
    return {rk: clamp_int64(max(0, to_int(parsed.get(rk, 0), 0))) for rk in RESOURCE_KEYS}


def is_win_outcome(outcome: str, loot_spice_eq: float) -> bool:
    """Classify whether a raid outcome counts as a win matching `storage.py`."""
    low = outcome.strip().lower()
    if low and any(tok in low for tok in _LOSS_TOKENS):
        return False
    if float(loot_spice_eq) > 0.0:
        return True
    return bool(low and any(tok in low for tok in _WIN_TOKENS))


def dumps_json_unsorted(payload: object) -> str:
    """Serialize `payload` to compact UTF-8 JSON preserving insertion order (for hot sector caches)."""
    clean = _sanitize_json_value(payload)
    return json.dumps(clean, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def loads_json_list(raw: str) -> list[object]:
    """Parse a JSON string into a list, returning `[]` on invalid input."""
    stripped = raw.strip()
    if not stripped:
        return []
    try:
        parsed: object = json.loads(stripped)
    except ValueError:
        return []
    return list(parsed) if isinstance(parsed, list) else []


def to_opt_int(raw: object) -> int | None:
    """Parse an optional integer value safely, returning `None` when absent/invalid."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return clamp_int64(raw)
    if isinstance(raw, float) and math.isfinite(raw):
        return clamp_int64(int(raw))
    if isinstance(raw, str) and raw.strip():
        try:
            return clamp_int64(int(float(raw.strip())))
        except ValueError:
            return None
    return None


__all__ = [
    "PROTECTED_META_KEYS",
    "RESOURCE_KEYS",
    "affected_rows",
    "as_str_dict",
    "clamp_int64",
    "coord_query_variants",
    "dumps_json",
    "dumps_json_unsorted",
    "is_win_outcome",
    "loads_json_dict",
    "loads_json_list",
    "normalize_building_key",
    "normalize_coords",
    "normalize_loot_dict",
    "parse_coords_gsp",
    "redact_text",
    "round_ts",
    "sanitize_text",
    "spice_equivalent_from_counts",
    "to_float",
    "to_int",
    "to_opt_int",
]
