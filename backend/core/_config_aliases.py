"""Canonical alias tables for `StrategyConfig` shipyard and fleet-research targets."""

from __future__ import annotations

_SHIP_ID_SLUGS: tuple[tuple[int, str], ...] = (
    (1, "small-transport"),
    (2, "large-transport"),
    (3, "transmitter"),
    (4, "small-fighter"),
    (5, "warship"),
    (6, "starcruiser"),
    (7, "phoenix"),
    (8, "colony-ship"),
    (9, "solar-cells"),
    (10, "spy-probe"),
    (11, "recycler"),
    (13, "battleship"),
    (14, "stealth-bomber"),
    (15, "destroyer"),
    (16, "imperial-starbase"),
)

SHIPYARD_SLUG_ALIASES: dict[str, str] = {
    **{f"s{sid}": slug for sid, slug in _SHIP_ID_SLUGS},
    **{f"ship[{sid}]": slug for sid, slug in _SHIP_ID_SLUGS},
    **{slug.replace("-", " "): slug for _, slug in _SHIP_ID_SLUGS},
    "solar cell": "solar-cells",
}

FLEET_RESEARCH_KEY_ALIASES: dict[str, str] = {
    **{f"r{i}": f"r{i}" for i in range(1, 16)},
    **{f"research[{i}]": f"r{i}" for i in range(1, 16)},
    "mining technology": "r1",
    "energy technology": "r2",
    "engine research": "r3",
    "rocket engine": "r4",
    "diffusion engine": "r5",
    "warp engine": "r6",
    "spy technology": "r7",
    "weapons technology": "r8",
    "shield technology": "r9",
    "defense systems": "r10",
    "targeting systems": "r11",
    "particle analyzer": "r12",
    "graviton technology": "r13",
    "teleportation": "r14",
    "black hole research": "r15",
}


def canonicalize_shipyard_target_key(raw_key: str) -> str:
    stripped = raw_key.strip()
    if not stripped or (stripped.isascii() and stripped.isdigit()):
        return raw_key
    norm = " ".join(stripped.lower().replace("_", " ").replace("-", " ").split())
    return SHIPYARD_SLUG_ALIASES.get(norm, raw_key)


def canonicalize_fleet_research_key(raw_key: str) -> str:
    stripped = raw_key.strip()
    if not stripped or (stripped.isascii() and stripped.isdigit()):
        return raw_key
    norm = " ".join(stripped.lower().replace("_", " ").replace("-", " ").split())
    return FLEET_RESEARCH_KEY_ALIASES.get(norm, raw_key)
