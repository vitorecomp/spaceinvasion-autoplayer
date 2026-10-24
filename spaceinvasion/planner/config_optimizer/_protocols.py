"""Read-only structural protocols and typed dict accessors for ``config_opt``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from spaceinvasion.core.coercion import (
    coerce_finite_float,
    coerce_int,
    coerce_optional_int,
)


@runtime_checkable
class PlanetLike(Protocol):
    """Minimal read-only planet contract satisfied by ``core.model`` and ``core.state``."""

    @property
    def planet_id(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def coords(self) -> str: ...

    @property
    def planet_type(self) -> str: ...

    @property
    def temperature(self) -> int: ...

    @property
    def fields_used(self) -> int: ...

    @property
    def fields_max(self) -> int: ...

    @property
    def buildings(self) -> Mapping[str, int]: ...

    @property
    def ships(self) -> Mapping[str, int]: ...


@runtime_checkable
class GameStateLike(Protocol):
    """Minimal read-only game state contract satisfied by ``core.model`` and ``core.state``."""

    @property
    def planets(self) -> Sequence[PlanetLike]: ...

    @property
    def researches(self) -> Mapping[str, int]: ...

    @property
    def speedup_factor(self) -> float | None: ...


def as_mapping(val: object) -> Mapping[str, object]:
    """Return ``val`` narrowed to a string-keyed mapping, or an empty dict."""
    if isinstance(val, Mapping):
        return {str(k): v for k, v in val.items()}
    return {}


def as_int_dict(val: object) -> dict[str, int]:
    """Coerce a mapping of ``str -> int`` values."""
    if not isinstance(val, Mapping):
        return {}
    return {str(k): coerce_int(v, 0) for k, v in val.items()}


def as_float_dict(val: object) -> dict[str, float]:
    """Coerce a mapping of ``str -> float`` values."""
    if not isinstance(val, Mapping):
        return {}
    return {str(k): coerce_finite_float(v, 0.0) for k, v in val.items()}


def as_str_dict(val: object) -> dict[str, str]:
    """Coerce a mapping of ``str -> str`` values."""
    if not isinstance(val, Mapping):
        return {}
    return {str(k): "" if v is None else str(v) for k, v in val.items()}


def map_int(mapping: Mapping[str, object], key: str, default: int = 0) -> int:
    """Read an integer from an untyped dictionary with fallback ``default``."""
    return coerce_int(mapping.get(key, default), default)


def map_opt_int(mapping: Mapping[str, object], key: str) -> int | None:
    """Read an optional integer from an untyped dictionary."""
    return coerce_optional_int(mapping.get(key), None)


def map_float(mapping: Mapping[str, object], key: str, default: float = 0.0) -> float:
    """Read a finite float from an untyped dictionary with fallback ``default``."""
    return coerce_finite_float(mapping.get(key, default), default)


def map_str(mapping: Mapping[str, object], key: str, default: str = "") -> str:
    """Read a string from an untyped dictionary with fallback ``default``."""
    raw = mapping.get(key)
    return default if raw is None else str(raw)
