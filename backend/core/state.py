"""Minimal compatibility shim for untouched ``notebooks/*.ipynb``."""

from __future__ import annotations

from backend.core.model import (
    EnergyBalance as EnergyBalance,
)
from backend.core.model import (
    PlanetState as PlanetState,
)
from backend.core.model import (
    Resources as Resources,
)

__all__ = ["EnergyBalance", "PlanetState", "Resources"]
