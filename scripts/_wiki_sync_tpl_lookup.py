"""Generated catalog lookup & numeric coercion template for sync_wiki_data."""

from __future__ import annotations

_HELPER_CODE_LOOKUP: str = '''

BUILDING_BY_ID: dict[int, dict[str, Any]] = {
    int(info["id"]): info for info in BUILDINGS.values() if info.get("id") is not None
}
BUILDING_LEVELS: dict[str, dict[int, dict[str, Any]]] = {
    slug: info["levels"] for slug, info in BUILDINGS.items()
}
RESEARCH_BY_ID: dict[str, dict[str, Any]] = {
    str(info["id"]): info for info in RESEARCHES.values() if info.get("id") is not None
}
RESEARCH_LEVELS: dict[str, dict[int, dict[str, Any]]] = {
    slug: info["levels"] for slug, info in RESEARCHES.items()
}


def normalize_unit_key(value: str) -> str:
    """Normalize names/slugs across US and UK spellings (defense/defence, analyzer/analyser)."""
    norm = value.strip().lower().replace("_", " ").replace("-", " ")
    norm = norm.replace("defence", "defense").replace("analyser", "analyzer")
    return " ".join(norm.split())


_UNIT_ALIAS_INDEX: dict[str, tuple[str, str]] = {}
for _cat, _catalog in (
    ("buildings", BUILDINGS),
    ("research", RESEARCHES),
    ("ships", SHIPS),
    ("defense", DEFENSES),
):
    for _slug, _info in _catalog.items():
        _UNIT_ALIAS_INDEX[_slug] = (_cat, _slug)
        _UNIT_ALIAS_INDEX[normalize_unit_key(_slug)] = (_cat, _slug)
        _UNIT_ALIAS_INDEX[normalize_unit_key(str(_info["name"]))] = (_cat, _slug)
        if _info.get("id") is not None:
            _uid = str(_info["id"])
            _UNIT_ALIAS_INDEX[_uid] = (_cat, _slug)
            if _cat == "buildings" and _uid.isascii() and _uid.isdigit():
                _UNIT_ALIAS_INDEX[f"b{_uid}"] = (_cat, _slug)


def resolve_unit(category: Optional[str], identifier: str | int) -> Optional[dict[str, Any]]:
    """Look up a unit dict by slug, numeric building ID, `'b1'`/`'r1'` key, or display name."""
    if identifier is None or isinstance(identifier, bool):
        return None
    if isinstance(identifier, os.PathLike):
        try:
            identifier = os.fspath(identifier)
        except Exception:
            return None
        if identifier is None or isinstance(identifier, bool):
            return None
    if isinstance(identifier, int):
        b_info = BUILDING_BY_ID.get(identifier)
        if b_info is not None and category in (None, "buildings"):
            return b_info
        return None
    if isinstance(identifier, (bytes, bytearray)):
        key = bytes(identifier).decode("utf-8", errors="replace").strip()
    elif isinstance(identifier, str):
        key = identifier.strip()
    else:
        try:
            key = str(identifier).strip()
        except Exception:
            return None
    if key.isascii() and key.isdigit() and len(key) <= 12:
        b_info = BUILDING_BY_ID.get(int(key))
        if b_info is not None and category in (None, "buildings"):
            return b_info
    hit = _UNIT_ALIAS_INDEX.get(key) or _UNIT_ALIAS_INDEX.get(normalize_unit_key(key))
    if not hit:
        return None
    found_cat, slug = hit
    if category is not None and found_cat != category:
        return None
    return {"buildings": BUILDINGS, "research": RESEARCHES, "ships": SHIPS, "defense": DEFENSES}[found_cat].get(slug)


MAX_JSON_SAFE_INT = 10**200
_MAX_FORMAT_INT_BITS = 12_000
_LOG10_2 = 0.3010299956639812


def safe_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        txt = value.strip()
        if (
            not txt
            or not txt.isascii()
            or txt.lower()
            in (
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
            )
        ):
            return default
        value = txt
    try:
        return int(value)
    except Exception:
        try:
            f = float(value)
            return int(f) if math.isfinite(f) else default
        except Exception:
            return default


def safe_optional_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        txt = value.strip()
        if (
            not txt
            or not txt.isascii()
            or txt.lower()
            in (
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
            )
        ):
            return default
        value = txt
    try:
        return int(value)
    except Exception:
        try:
            f = float(value)
            return int(f) if math.isfinite(f) else default
        except Exception:
            return default


def safe_finite_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, (bool, Mapping, list, tuple, set, frozenset)):
        return default
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, str):
        txt = value.strip()
        if not txt or not txt.isascii() or txt.lower() in ("null", "none", "~"):
            return default
        value = txt
    try:
        f = float(value)
    except OverflowError:
        if isinstance(value, int):
            return 1e300 if value > 0 else -1e300
        return default
    except Exception:
        return default
    return f if math.isfinite(f) else default


def cap_json_int(value: Any, *, limit: int = MAX_JSON_SAFE_INT, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        v = value
    else:
        v = safe_int(value, default)
    if v > limit:
        return limit
    if v < -limit:
        return -limit
    return v
'''
