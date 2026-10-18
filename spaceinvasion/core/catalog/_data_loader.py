"""Internal loader for generated JSON catalog tables."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent / "tables"


def _restore_int_keys(obj: Any, int_key_fields: frozenset[str]) -> Any:
    if isinstance(obj, dict):
        return {
            k: (
                {int(ik): _restore_int_keys(iv, int_key_fields) for ik, iv in v.items()}
                if k in int_key_fields and isinstance(v, dict)
                else _restore_int_keys(v, int_key_fields)
            )
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_restore_int_keys(item, int_key_fields) for item in obj]
    return obj


def load_catalog_table(
    filename: str,
    *,
    int_key_fields: frozenset[str] = frozenset(),
) -> dict[str, dict[str, Any]]:
    """Load a generated JSON catalog table from `spaceinvasion/core/catalog/tables/`."""
    raw = json.loads((_DATA_DIR / filename).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object in catalog table {filename}")
    restored = _restore_int_keys(raw, int_key_fields) if int_key_fields else raw
    return dict(restored)
