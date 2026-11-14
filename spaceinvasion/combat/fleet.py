"""Minimal compatibility shim for untouched ``notebooks/*.ipynb``."""

from __future__ import annotations

from spaceinvasion.core.catalog import (
    DEFENSE_SLUG_BY_KEY as DEFENSE_SLUG_BY_KEY,
)
from spaceinvasion.core.catalog import (
    SHIP_SLUG_BY_KEY as SHIP_SLUG_BY_KEY,
)
from spaceinvasion.core.coords import coords_distance as _parse_coords_distance

__all__ = ["DEFENSE_SLUG_BY_KEY", "SHIP_SLUG_BY_KEY", "_parse_coords_distance"]
