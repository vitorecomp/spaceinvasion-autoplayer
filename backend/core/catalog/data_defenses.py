"""Generated SpaceInvasion Classic wiki defense catalog data loader."""

from __future__ import annotations

from typing import Any

from backend.core.catalog._data_loader import load_catalog_table

DEFENSES: dict[str, dict[str, Any]] = load_catalog_table("defenses.json")
