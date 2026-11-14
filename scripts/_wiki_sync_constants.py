"""Static slug, header, and unit-multiplier tables for `scripts/sync_wiki_data.py`."""

from __future__ import annotations

WIKI_INDEX_URL = "https://spaceinvasion.bitmeup.com/wiki/en/"
MERCHANT_URL = "https://spaceinvasion.bitmeup.com/wiki/en/economy/merchant"

BUILDING_ID_BY_SLUG: dict[str, int] = {
    "pig-iron-mine": 1,
    "blast-furnace": 2,
    "kryptonite-mine": 3,
    "spice-mine": 4,
    "fusion-plant": 5,
    "thermosolar-plant": 6,
    "development-center": 7,
    "research-lab": 8,
    "weapons-factory": 9,
    "fleet-base": 10,
    "pig-iron-warehouse": 11,
    "metal-warehouse": 12,
    "kryptonite-warehouse": 13,
    "spice-warehouse": 14,
    "particle-cannon": 15,
    "particle-shield": 16,
    "microsystem-accelerator": 17,
    "teleporter": 18,
    "galaxy-scanner": 19,
    "tunnel-system": 20,
    "quantum-array": 21,
}

RESEARCH_ID_BY_SLUG: dict[str, str] = {
    "mining-technology": "r1",
    "energy-technology": "r2",
    "engine-research": "r3",
    "rocket-engine": "r4",
    "diffusion-engine": "r5",
    "warp-engine": "r6",
    "spy-technology": "r7",
    "weapons-technology": "r8",
    "shield-technology": "r9",
    "defense-systems": "r10",
    "targeting-systems": "r11",
    "particle-analyzer": "r12",
    "graviton-technology": "r13",
    "teleportation": "r14",
    "black-hole-research": "r15",
}

_RES_CLASS_TO_KEY: dict[str, str] = {
    "w-res1": "pig_iron",
    "w-res2": "metal",
    "w-res3": "kryptonite",
    "w-res4": "spice",
}

_COST_HEADER_TO_KEY: dict[str, str] = {
    "Pig iron": "pig_iron",
    "Metal": "metal",
    "Kryptonite": "kryptonite",
    "Spice": "spice",
}

_DT_STAT_TO_KEY: dict[str, str] = {
    "Hull (structure)": "hull",
    "Shield": "shield",
    "Attack": "attack",
    "Speed": "speed",
    "Cargo capacity": "cargo",
    "Fuel": "fuel",
    "Engine": "engine",
}

_SUFFIX_MULTIPLIERS: dict[str, int] = {
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
    "t": 1_000_000_000_000,
    "qa": 1_000_000_000_000_000,
    "qi": 1_000_000_000_000_000_000,
}

__all__ = [
    "BUILDING_ID_BY_SLUG",
    "MERCHANT_URL",
    "RESEARCH_ID_BY_SLUG",
    "WIKI_INDEX_URL",
    "_COST_HEADER_TO_KEY",
    "_DT_STAT_TO_KEY",
    "_RES_CLASS_TO_KEY",
    "_SUFFIX_MULTIPLIERS",
]
