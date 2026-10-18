"""Generated SpaceInvasion Classic wiki research catalog data loader."""

from __future__ import annotations

from typing import Any

from spaceinvasion.core.catalog._data_loader import load_catalog_table

RESEARCHES: dict[str, dict[str, Any]] = load_catalog_table(
    "research.json",
    int_key_fields=frozenset({"levels"}),
)
