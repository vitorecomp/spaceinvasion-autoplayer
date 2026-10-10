"""Generated SpaceInvasion Classic wiki building catalog data loader and scalar constants."""

from __future__ import annotations

from typing import Any

from spaceinvasion.core.catalog._data_loader import load_catalog_table

WIKI_SOURCE_URL: str = "https://spaceinvasion.bitmeup.com/wiki/en/"
WIKI_SYNCED_AT_UTC: str = "2026-11-15T02:03:10Z"

# Official wiki merchant exchange ratios: 6 Pig iron = 2.5 Metal = 3 Kryptonite = 1 Spice
MERCHANT_RATES_IN_SPICE: dict[str, float] = {
    "pig_iron": 6.0,
    "metal": 2.5,
    "kryptonite": 3.0,
    "spice": 1.0,
}

# Mining technology grants +2% per level to Pig iron, Kryptonite, and Spice mines (NOT Blast furnace)
MINING_TECH_BONUS_PER_LEVEL: float = 0.02

BUILDINGS: dict[str, dict[str, Any]] = load_catalog_table(
    "buildings.json",
    int_key_fields=frozenset({"levels", "prod_by_temp"}),
)
