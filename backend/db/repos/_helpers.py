"""Pure stdlib normalization, coordinate, and JSON helpers for ORM repositories."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
import json
import math
import os
import re
import time

from sqlalchemy.engine import CursorResult

from backend.core.catalog.json_sanitize import safe_key, sanitize_json_tree
from backend.core.redaction import redact_sensitive
from backend.core.redaction import redact_text as _core_redact_text

PROTECTED_META_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "schema_version_tag",
        "schema_checksum",
    }
)

_COORDS_RE = re.compile(r"^\s*(\d{1,3})\s*[:\-/]\s*(\d{1,4})\s*[:\-/]\s*(\d{1,3})\s*$", re.ASCII)
_INT64_MIN = -(1 << 63)
_INT64_MAX = (1 << 63) - 1


def round_ts(ts: float | None = None) -> float:
    """Return a finite epoch timestamp rounded to milliseconds."""
    if ts is not None and isinstance(ts, (int, float)) and not isinstance(ts, bool):
        val = float(ts)
        if math.isfinite(val):
            return round(val, 3)
    return round(time.time(), 3)


def redact_text(text: str) -> str:
    """Redact session-id/token/credential fragments from persisted strings."""
    return _core_redact_text(text) if text else text


def sanitize_text(raw: object, max_len: int | None = None) -> str:
    """Convert `raw` to clean redacted text safely (resisting cycles, broken PathLike, huge ints)."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        out = _core_redact_text(raw)
    elif isinstance(raw, (bytes, bytearray)):
        out = _core_redact_text(bytes(raw).decode("utf-8", errors="replace"))
    elif isinstance(raw, os.PathLike):
        red = redact_sensitive(raw)
        out = red if isinstance(red, str) else _core_redact_text(safe_key(red))
    else:
        red = redact_sensitive(raw)
        out = red if isinstance(red, str) else _core_redact_text(safe_key(red))
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


def clamp_limit(limit: object, default: int = 200, *, max_limit: int = 100_000) -> int:
    """Clamp a SQL `LIMIT` argument into `[1, max_limit]` safely (resisting >int64 ints/NaN)."""
    val = to_int(limit, default)
    if val < 1:
        return 1
    if val > max_limit:
        return max_limit
    return val


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
    return redact_sensitive(sanitize_json_tree(redact_sensitive(value), nonfinite_float=None))


sanitize_obj = _sanitize_json_value


def dumps_json(payload: object) -> str:
    """Serialize `payload` to canonical compact redacted JSON matching `storage.py`."""
    clean = _sanitize_json_value(payload)
    return json.dumps(clean, sort_keys=True, separators=(",", ":"), allow_nan=False)


json_dumps = dumps_json


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
    """Parse a finite float value safely with a fallback default."""
    if isinstance(raw, (int, float, Decimal)) and not isinstance(raw, bool):
        try:
            val = float(raw)
        except (ValueError, OverflowError):
            return default
        return val if math.isfinite(val) else default
    if isinstance(raw, str):
        try:
            val = float(raw.strip())
            return val if math.isfinite(val) else default
        except (ValueError, OverflowError):
            return default
    return default


def to_int(raw: object, default: int = 0) -> int:
    """Parse an integer value safely with a fallback default (guarding NaN/Inf)."""
    if isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        return raw
    if isinstance(raw, Decimal):
        return int(raw) if raw.is_finite() else default
    if isinstance(raw, float):
        return int(raw) if math.isfinite(raw) else default
    if isinstance(raw, str):
        txt = raw.strip()
        if not txt:
            return default
        try:
            fval = float(txt)
            return int(fval) if math.isfinite(fval) else default
        except (ValueError, OverflowError):
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


def is_win_outcome(outcome: object, loot_spice_eq: float) -> bool:
    """Classify whether a raid outcome counts as a win matching `storage.py`."""
    low = sanitize_text(outcome).strip().lower()
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


_RECYCLE_EXPLICIT_TOKENS: tuple[str, ...] = (
    "recycling report",
    "trümmerfeldbericht",
    "truemmerfeldbericht",
    "space rubble",
    "trümmerfeld abgebaut",
    "couldn't find any more resources",
    "could not find any more resources",
    "pieces of rubble",
)
_NON_RECYCLE_NOTICE_TOKENS: tuple[str, ...] = (
    "was re-stationed",
    "re-stationed",
    "restationed",
    "has been stationed",
    "wurde stationiert",
    "umstationiert",
    "stationiert",
    "it delivers",
    "has delivered",
    "delivered resources",
    "fleet return",
    "flottenrückkehr",
    "flottenrueckkehr",
    "returning from duty",
    "were unloaded",
    "wurden ausgeladen",
    "espionage report",
    "spionagebericht",
    "spy report",
)
_STATION_SUBJ_RE = re.compile(r"\]\s*station\b", re.IGNORECASE)
_STATION_END_RE = re.compile(r"\bstation\s*$", re.IGNORECASE)


def is_non_recycle_notice_text(text: str, subject: str = "") -> bool:
    """Return True for stationing/transport/return/spy notices mistakenly tagged as recycle reports."""
    combined = f"{(subject or '').strip().lower()} {(text or '').strip().lower()}".strip()
    if not combined or any(tok in combined for tok in _RECYCLE_EXPLICIT_TOKENS):
        return False
    if any(tok in combined for tok in _NON_RECYCLE_NOTICE_TOKENS):
        return True
    subj_low = (subject or "").strip().lower()
    return bool(_STATION_SUBJ_RE.search(combined) or _STATION_END_RE.search(subj_low))


__all__ = [
    "PROTECTED_META_KEYS",
    "RESOURCE_KEYS",
    "affected_rows",
    "as_str_dict",
    "clamp_int64",
    "clamp_limit",
    "coord_query_variants",
    "dumps_json",
    "dumps_json_unsorted",
    "is_non_recycle_notice_text",
    "is_win_outcome",
    "json_dumps",
    "loads_json_dict",
    "loads_json_list",
    "normalize_building_key",
    "normalize_coords",
    "normalize_loot_dict",
    "parse_coords_gsp",
    "redact_text",
    "round_ts",
    "sanitize_obj",
    "sanitize_text",
    "spice_equivalent_from_counts",
    "to_float",
    "to_int",
    "to_opt_int",
]
