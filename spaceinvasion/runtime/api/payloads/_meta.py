"""Shared metadata helpers for typed API payload dataclasses."""

from __future__ import annotations

from typing import Final

OPT: Final[dict[str, bool]] = {"optional": True}
JsonDict = dict[str, object]
