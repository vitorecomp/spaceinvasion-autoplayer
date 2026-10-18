"""Generated SpaceInvasion Classic wiki ship catalog data loader."""

from __future__ import annotations

from typing import Any

from spaceinvasion.core.catalog._data_loader import load_catalog_table

SHIPS: dict[str, dict[str, Any]] = load_catalog_table(
    "ships.json",
    int_key_fields=frozenset({"solar_cell_energy_by_temp"}),
)
