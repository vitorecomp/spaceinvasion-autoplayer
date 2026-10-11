"""Shared constants, typed protocols, and pure scalar helpers for `spaceinvasion.planner.ai`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Protocol, runtime_checkable

from spaceinvasion.core.coercion import coerce_finite_float, coerce_int, coerce_optional_int
from spaceinvasion.core.model import BUILDING_ID_BY_NAME, Resources

PATH_FLOW_BUILDING_KEYS: tuple[str, ...] = ("b1", "b2", "b3", "b4", "b5", "b6")
PATH_MINE_KEYS: tuple[str, ...] = ("b1", "b2", "b3", "b4")
PATH_ACCELERATOR_KEYS: tuple[str, ...] = ("b7", "b17")
PATH_HOURS_PER_WEEK: float = 168.0
PATH_NEVER: float = float("inf")
DEFAULT_MEDIAN_ROUND_TRIP_SECONDS: float = 1800.0
REALIZED_INCOME_BASIS: str = "realized_24h"

DEFAULT_AI_PLANNER_PARAMS: dict[str, object] = {
    "reward_weeks": 26,
    "weekly_discount": 0.90,
    "resource_spare_percent": {
        "pig_iron": 10.0,
        "metal": 10.0,
        "kryptonite": 10.0,
        "spice": 10.0,
    },
    "fleet_command_center_level": None,
    "attacks_per_day": None,
    "scarcity_focus": 1.0,
    "critical_queue_share": 0.9,
    "patience": 0.25,
    "speed_weight": 1.5,
    "revalue_every_steps": 25,
    "search_tolerance": 0.01,
    "ga_population_size": 10,
    "ga_generations": 6,
    "ga_tournament_size": 3,
    "ga_crossover_rate": 0.9,
    "ga_mutation_rate": 0.3,
    "ga_elite_count": 2,
    "ga_random_seed": 20261117,
    "pad_generic_colonies": False,
    "transport_dispatch_delay_minutes": 120.0,
}

PATH_GENE_RANGES: dict[str, tuple[float, float]] = {
    "focus": (0.0, 3.0),
    "critical_share": (0.2, 1.0),
    "patience": (0.0, 1.0),
    "speed_weight": (0.0, 3.0),
    "value_pig_iron": (-2.0, 2.0),
    "value_metal": (-2.0, 2.0),
    "value_kryptonite": (-2.0, 2.0),
    "value_spice": (-2.0, 2.0),
}

UPPER_AI_PARAM_ALIASES: dict[str, str] = {
    "PATH_REWARD_WEEKS": "reward_weeks",
    "PATH_WEEKLY_DISCOUNT": "weekly_discount",
    "PATH_RESOURCE_SPARE_PERCENT": "resource_spare_percent",
    "PATH_FLEET_COMMAND_CENTER_LEVEL": "fleet_command_center_level",
    "PATH_ATTACKS_PER_DAY": "attacks_per_day",
    "PATH_SCARCITY_FOCUS": "scarcity_focus",
    "PATH_CRITICAL_QUEUE_SHARE": "critical_queue_share",
    "PATH_PATIENCE": "patience",
    "PATH_SPEED_WEIGHT": "speed_weight",
    "PATH_REVALUE_EVERY_STEPS": "revalue_every_steps",
    "PATH_SEARCH_TOLERANCE": "search_tolerance",
    "PATH_GA_POPULATION_SIZE": "ga_population_size",
    "PATH_GA_GENERATIONS": "ga_generations",
    "PATH_GA_TOURNAMENT_SIZE": "ga_tournament_size",
    "PATH_GA_CROSSOVER_RATE": "ga_crossover_rate",
    "PATH_GA_MUTATION_RATE": "ga_mutation_rate",
    "PATH_GA_ELITE_COUNT": "ga_elite_count",
    "PATH_GA_RANDOM_SEED": "ga_random_seed",
    "PATH_TRANSPORT_DISPATCH_DELAY_MINUTES": "transport_dispatch_delay_minutes",
    "fleet_dispatch_delay_minutes": "transport_dispatch_delay_minutes",
}

GENE_NAMES: tuple[str, ...] = tuple(PATH_GENE_RANGES.keys())
VALUE_GENE_NAMES: tuple[str, ...] = ("value_pig_iron", "value_metal", "value_kryptonite", "value_spice")
WARM_GENE_DEFAULTS: dict[str, float] = {
    "focus": 1.0,
    "critical_share": 0.9,
    "patience": 0.25,
    "speed_weight": 1.5,
    "value_pig_iron": 0.0,
    "value_metal": 0.0,
    "value_kryptonite": 0.0,
    "value_spice": 0.0,
}

AI_FILL_REASON_PREFIX: str = "AI-Planner fill"
AI_FILL_MIN_PATIENCE: float = 0.25
AI_FILL_MAX_PATIENCE: float = 0.75
AI_FILL_POOL_LEAD_HOURS: float = 1.0
AI_FILL_NEVER_HOURS: float = 1e9
AI_STEP_SEQ_RE: re.Pattern[str] = re.compile(r"^AI-Planner(?:\s+fill)?\s+#(\d+)\b", re.ASCII)

AI_COLONY_IDLE_STATES: tuple[str, ...] = (
    "building",
    "ready_to_build",
    "waiting_inbound",
    "waiting_transport",
    "waiting_local_resources",
    "waiting_research_prereq",
    "waiting_build_prereq",
    "blocked_fields",
    "plan_complete",
    "unmatched",
)

RESOURCE_DISPLAY_LABELS: dict[str, str] = {
    "pig_iron": "Pig iron",
    "metal": "Metal",
    "kryptonite": "Kryptonite",
    "spice": "Spice",
}

RESOURCE_TIE_PRIORITY: dict[str, int] = {
    "metal": 0,
    "kryptonite": 1,
    "spice": 2,
    "pig_iron": 3,
}

COORDS_RE: re.Pattern[str] = re.compile(r"^\d{1,2}:\d{1,3}:\d{1,2}$", re.ASCII)
EMBEDDED_COORDS_RE: re.Pattern[str] = re.compile(r"(\d{1,2}:\d{1,3}:\d{1,2})", re.ASCII)
TIMER_RE: re.Pattern[str] = re.compile(r"(\d+):(\d{2}):(\d{2})", re.ASCII)
DAYS_TIMER_RE: re.Pattern[str] = re.compile(r"(?:(\d+)\s*d\s*)?(\d+):(\d{2}):(\d{2})", re.IGNORECASE | re.ASCII)
SHORT_TIMER_RE: re.Pattern[str] = re.compile(r"^\s*(\d+):(\d{2})\s*$", re.ASCII)
LEVEL_RE: re.Pattern[str] = re.compile(r"\(\s*(?:Level|Lvl|L)?\s*(\d+)\s*\)", re.IGNORECASE | re.ASCII)

ACTIVE_CONSTRUCTION_TOKENS: tuple[str, ...] = (
    "in progress",
    "in construction",
    "active building",
    "constructing",
    "under construction",
)


@runtime_checkable
class ResourcesLike(Protocol):
    """Structural protocol satisfied by both ``core.model.Resources`` and legacy ``core.state.Resources``."""

    pig_iron: int
    metal: int
    kryptonite: int
    spice: int


@runtime_checkable
class EnergyLike(Protocol):
    """Structural protocol for planetary energy balance."""

    produced: int
    used: int


@runtime_checkable
class BuildingOfferLike(Protocol):
    """Structural protocol for a parsed building card."""

    building_id: int
    name: str
    current_level: int
    target_level: int
    cost: ResourcesLike
    can_build: bool
    status_text: str
    build_href: str | None


@runtime_checkable
class ResearchOfferLike(Protocol):
    """Structural protocol for a parsed research card."""

    research_id: int
    research_key: str
    name: str
    current_level: int
    target_level: int
    cost: ResourcesLike
    can_research: bool
    status_text: str
    research_href: str | None
    active: bool


@runtime_checkable
class QueueJobLike(Protocol):
    """Structural protocol for an active queue job."""

    category: str
    name: str
    building_id: int | None
    target_level: int | None
    remaining_text: str
    remaining_seconds: float | None
    finish_ts: float | None
    detail: str
    origin_coords: str
    cargo: object


@runtime_checkable
class HostileFlagged(Protocol):
    """Optional ``hostile`` marker on synthetic fleet queue entries."""

    hostile: object


@runtime_checkable
class PlanetLike(Protocol):
    """Structural protocol satisfied by ``core.model.PlanetState`` and ``core.state.PlanetState``."""

    planet_id: int
    name: str
    coords: str
    planet_type: str
    temperature: int
    fields_used: int
    fields_max: int
    resources: ResourcesLike
    storage: ResourcesLike
    energy: EnergyLike
    buildings: Mapping[str, int]
    ships: Mapping[str, int]
    economy_rates: Mapping[str, int]
    building_offers: Sequence[BuildingOfferLike]

    def building_level(self, building_id: int) -> int: ...


@runtime_checkable
class GameStateLike(Protocol):
    """Structural protocol satisfied by ``core.model.GameState`` and ``core.state.GameState``."""

    timestamp: float
    planets: Sequence[PlanetLike]
    active_planet_id: int | None
    researches: Mapping[str, int]
    active_queues: Sequence[QueueJobLike]
    research_offers: Sequence[ResearchOfferLike]
    speedup_factor: float | None

    def active_planet(self) -> PlanetLike | None: ...


@runtime_checkable
class ConfigLike(Protocol):
    """Structural protocol for planner strategy configuration flags."""

    allow_multi_planet: bool
    ai_adaptive_execution: bool


def parse_json_dict(raw: object) -> dict[str, object]:
    """Decode a mapping or JSON object string into a plain ``dict[str, object]``."""
    if isinstance(raw, Mapping):
        return {str(k): v for k, v in raw.items()}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed: object = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(parsed, Mapping):
            return {str(k): v for k, v in parsed.items()}
    return {}


def is_valid_coords(raw: object) -> bool:
    """Return True if ``raw`` matches canonical ``GG:SSS:PP`` coordinate syntax."""
    return bool(COORDS_RE.match(str(raw or "").strip()))


def is_active_construction_status(status_text: object) -> bool:
    """Return True if ``status_text`` indicates active in-progress construction."""
    st = str(status_text or "").lower()
    return any(tok in st for tok in ACTIVE_CONSTRUCTION_TOKENS)


def is_queue_job_expired(job: QueueJobLike, state_ts: float) -> bool:
    """Return True if ``job.finish_ts`` is strictly at or before ``state_ts``."""
    if job.finish_ts is None or state_ts <= 0.0:
        return False
    fin_ts = coerce_finite_float(job.finish_ts, 0.0)
    return fin_ts > 0.0 and fin_ts <= state_ts


def parse_queue_remaining_seconds(job: QueueJobLike, *, state_ts: float = 0.0, fallback_seconds: float = 0.0) -> float:
    """Resolve remaining seconds on a queue job from numeric remaining, finish_ts, timer text, or fallback."""
    if job.remaining_seconds is not None:
        rem_f = coerce_finite_float(job.remaining_seconds, -1.0)
        if rem_f > 0.0:
            return rem_f
    if job.finish_ts is not None and state_ts > 0.0:
        fin_f = coerce_finite_float(job.finish_ts, 0.0)
        if fin_f > state_ts:
            return fin_f - state_ts
    rem_txt = str(job.remaining_text or "").strip()
    if rem_txt:
        m_full = DAYS_TIMER_RE.search(rem_txt)
        if m_full:
            days = int(m_full.group(1) or 0)
            total = days * 86400.0 + int(m_full.group(2)) * 3600.0 + int(m_full.group(3)) * 60.0 + int(m_full.group(4))
            if total > 0.0:
                return total
        m_short = SHORT_TIMER_RE.match(rem_txt)
        if m_short:
            total = int(m_short.group(1)) * 60.0 + int(m_short.group(2))
            if total > 0.0:
                return total
    return max(0.0, float(fallback_seconds))


def extract_building_job_id_and_level(job: QueueJobLike, fallback_level: int = 0) -> tuple[int | None, int | None]:
    """Extract ``(building_id, target_level)`` from a building queue job."""
    bid = coerce_optional_int(job.building_id, None)
    if bid is None:
        clean = re.sub(r"\(.*?\)", "", str(job.name or ""))
        clean = TIMER_RE.sub("", clean).strip().lower()
        bid = BUILDING_ID_BY_NAME.get(clean)
    tgt_lvl = coerce_optional_int(job.target_level, None)
    if tgt_lvl is None or tgt_lvl <= 0:
        m_lvl = LEVEL_RE.search(str(job.name or ""))
        if m_lvl:
            tgt_lvl = int(m_lvl.group(1))
        elif fallback_level > 0:
            tgt_lvl = fallback_level
    return bid, tgt_lvl


def resources_tuple(res: ResourcesLike) -> tuple[float, float, float, float]:
    """Extract a 4-tuple of non-negative resource floats ``(pig, metal, kry, spice)``."""
    return (
        max(0.0, float(res.pig_iron or 0)),
        max(0.0, float(res.metal or 0)),
        max(0.0, float(res.kryptonite or 0)),
        max(0.0, float(res.spice or 0)),
    )


def safe_speedup(state: GameStateLike | None, params: Mapping[str, object]) -> float:
    """Return a positive finite universe speedup factor from ``state`` or ``params`` (default ``1.0``)."""
    state_val: object = state.speedup_factor if state is not None else None
    for candidate in (state_val, params.get("speedup_factor")):
        val = coerce_finite_float(candidate, 0.0)
        if val > 0.0:
            return val
    return 1.0


def coerce_resources_obj(raw: object) -> Resources:
    """Coerce a ``Resources`` instance or mapping into a typed ``core.model.Resources``."""
    if isinstance(raw, Resources):
        return raw
    if isinstance(raw, ResourcesLike):
        return Resources(
            pig_iron=max(0, coerce_int(raw.pig_iron, 0)),
            metal=max(0, coerce_int(raw.metal, 0)),
            kryptonite=max(0, coerce_int(raw.kryptonite, 0)),
            spice=max(0, coerce_int(raw.spice, 0)),
        )
    if isinstance(raw, Mapping):
        return Resources(
            pig_iron=max(0, coerce_int(raw.get("pig_iron"), 0)),
            metal=max(0, coerce_int(raw.get("metal"), 0)),
            kryptonite=max(0, coerce_int(raw.get("kryptonite"), 0)),
            spice=max(0, coerce_int(raw.get("spice"), 0)),
        )
    return Resources()
