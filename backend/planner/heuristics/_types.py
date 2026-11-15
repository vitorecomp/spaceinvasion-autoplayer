"""Shared domain constants and `PlannedAction` dataclass for heuristic planning."""

from __future__ import annotations

import re

from backend.core.model import PlannedAction as PlannedAction

SHIP_SLUG_BY_KEY: dict[str, str] = {
    "s1": "small-transport",
    "s2": "large-transport",
    "s3": "transmitter",
    "s4": "small-fighter",
    "s5": "warship",
    "s6": "starcruiser",
    "s7": "phoenix",
    "s8": "colony-ship",
    "s9": "solar-cells",
    "s10": "spy-probe",
    "s11": "recycler",
    "s13": "battleship",
    "s14": "stealth-bomber",
    "s15": "destroyer",
    "s16": "imperial-starbase",
}
_SLUG_TO_SHIP_KEY: dict[str, str] = {slug: skey for skey, slug in SHIP_SLUG_BY_KEY.items()}

# SpaceInvasion building IDs matching `internalBuildings`
PIG_IRON_MINE: int = 1
BLAST_FURNACE: int = 2
KRYPTONITE_MINE: int = 3
SPICE_MINE: int = 4
FUSION_PLANT: int = 5
THERMOSOLAR_PLANT: int = 6
DEVELOPMENT_CENTER: int = 7
RESEARCH_LAB: int = 8
WEAPONS_FACTORY: int = 9
FLEET_BASE: int = 10
PIG_IRON_WAREHOUSE: int = 11
METAL_WAREHOUSE: int = 12
KRYPTONITE_WAREHOUSE: int = 13
SPICE_WAREHOUSE: int = 14

SOLAR_CELLS_ID: int = 9

_MINE_IDS: tuple[int, ...] = (PIG_IRON_MINE, BLAST_FURNACE, KRYPTONITE_MINE, SPICE_MINE)
_MINE_RES_KEY: dict[int, str] = {
    PIG_IRON_MINE: "pig_iron",
    BLAST_FURNACE: "metal",
    KRYPTONITE_MINE: "kryptonite",
    SPICE_MINE: "spice",
}
_MINE_LABELS: dict[int, str] = {
    PIG_IRON_MINE: "Pig iron mine",
    BLAST_FURNACE: "Blast furnace",
    KRYPTONITE_MINE: "Kryptonite mine",
    SPICE_MINE: "Spice mine",
}
_WAREHOUSE_BY_RESOURCE: dict[str, tuple[int, str]] = {
    "pig_iron": (PIG_IRON_WAREHOUSE, "Pig iron warehouse"),
    "metal": (METAL_WAREHOUSE, "Metal warehouse"),
    "kryptonite": (KRYPTONITE_WAREHOUSE, "Kryptonite warehouse"),
    "spice": (SPICE_WAREHOUSE, "Spice warehouse"),
}

_COMBAT_AND_RAID_SHIP_KEYS: tuple[str, ...] = (
    "s4",
    "s5",
    "s6",
    "s7",
    "s13",
    "s14",
    "s15",
    "s16",
    "s1",
    "s2",
    "s3",
)

# Plan v4 plan-order pooling constants
AI_PLAN_ORDER_LOAN_TAG: str = "[Plan order loan]"
PLAN_ORDER_LOAN_MIN_CARGO: int = 20_000
PLAN_ORDER_LOAN_MIN_SHARE: float = 0.25
_AI_PLAN_STEP_RANK_RE: re.Pattern[str] = re.compile(r"^AI-Planner #(\d+):", re.ASCII)
_PLAN_ORDER_NO_RANK: int = 10**9

# Plan v2/v4 infrastructure cargo target constants
AI_INFRA_CARGO_TAG: str = "[Plan infra cargo target]"
INFRA_TRANSPORT_INCOME_SHARE: float = 0.25
INFRA_TRANSPORT_MIN_UTILIZATION: float = 0.5
INFRA_TRANSPORT_MAX_BATCH: int = 10
INFRA_TRANSPORT_FLEET_CAP_RATIO: float = 2.0
INFRA_TRANSPORT_RAID_ALLOWANCE_CARGO: int = 900_000
_TRANSPORT_HULL_KEYS: tuple[str, ...] = ("s1", "s2", "s3")
_IN_FLIGHT_DETAIL_CAP: int = 140

# Budget & Blast furnace constants
_BUDGET_RES_KEYS: tuple[str, ...] = ("pig_iron", "metal", "kryptonite", "spice")
_METAL_CONSUMER_KINDS: frozenset[str] = frozenset({"BUILD", "WAIT_RESOURCES", "RESEARCH", "BUILD_SHIP"})
AI_THROTTLE_COOLDOWN_SECONDS: float = 60.0
METAL_OVERFLOW_RESUME_PCT: int = 95
EMPIRE_SMELT_HOLD_RATIO: float = 1.10
EMPIRE_SMELT_MIN_SPARE_PIG: int = 50_000
