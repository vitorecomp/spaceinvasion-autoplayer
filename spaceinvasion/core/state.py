"""Minimal compatibility shim for untouched ``notebooks/*.ipynb``."""

from __future__ import annotations

from spaceinvasion.core.model import (
    EnergyBalance as EnergyBalance,
)
from spaceinvasion.core.model import (
    PlanetState as PlanetState,
)
from spaceinvasion.core.model import (
    Resources as Resources,
)

__all__ = ["EnergyBalance", "PlanetState", "Resources"]
