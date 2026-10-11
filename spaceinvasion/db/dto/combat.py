"""Frozen DTO dataclasses for raids, bash tracking, inbox messages, and combat reports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RaidHistoryRecord:
    """Typed append-only raid dispatch ledger row (`raid_history`)."""

    id: int
    target_coords: str
    attacked_ts: float
    is_exempt: bool
    outcome: str
    loot_json: str
    loot_spice_eq: float
    won: bool


@dataclass(frozen=True)
class BashBlockRecord:
    """Typed append-only bash/protection cooldown block row (`bash_blocks`)."""

    id: int
    target_coords: str
    blocked_ts: float
    blocked_until_ts: float
    cooldown_seconds: float
    is_bash: bool
    reason: str


@dataclass(frozen=True)
class BashWindowEntry:
    """Per-coordinate 3/24h Bash tracker state returned by `query_bash_window`."""

    target_coords: str
    hits_in_window: int
    remaining_attacks: int
    is_bash_blocked: bool
    first_hit_ts: float | None
    last_hit_ts: float | None
    blocked_until_ts: float
    block_reason: str
    next_slot_opens_ts: float | None


@dataclass(frozen=True)
class TargetRaidStats:
    """Aggregated raid performance metrics for a single target or overall empire."""

    target_coords: str
    attempts: int
    wins: int
    losses: int
    success_rate: float
    pig_iron: int
    metal: int
    kryptonite: int
    spice: int
    total_loot_spice_eq: float
    avg_loot_spice_eq: float
    recency_weighted_loot_spice_eq: float
    last_attacked_ts: float | None
    last_outcome: str


@dataclass(frozen=True)
class ActiveTargetBlock:
    """Active block cooldown currently in effect on a coordinate."""

    target_coords: str
    blocked_until_ts: float
    remaining_seconds: float
    reason: str
    bash_hits: int
    last_blocked_ts: float


@dataclass(frozen=True)
class RaidStatsSummary:
    """Full raid performance rollup returned by `query_raid_stats`."""

    overall: TargetRaidStats
    per_target: tuple[TargetRaidStats, ...]
    active_blocks: tuple[ActiveTargetBlock, ...]
    bash_blocks_count: int


@dataclass(frozen=True)
class BattleReportRecord:
    """Typed structured combat report row (`battle_reports`)."""

    id: int
    ts: float
    msg_uid: str
    server_msg_id: str
    battle_id: str
    msg_date_text: str
    target_coords: str
    attacker: str
    defender: str
    outcome: str
    rounds: int
    pig_iron: int
    metal: int
    kryptonite: int
    spice: int
    loot_spice_eq: float
    attacker_lost_spice: int
    defender_lost_spice: int
    debris_pig_iron: int
    debris_metal: int
    debris_spice_eq: float
    raw_text: str


@dataclass(frozen=True)
class GameMessageRecord:
    """Typed append-only BitMeUp inbox message row (`game_messages`)."""

    id: int
    ingested_ts: float
    msg_uid: str
    server_msg_id: str
    folder_type: str
    category: str
    msg_date_text: str
    sender: str
    subject: str
    coords: str
    body_text: str
    raw_html: str
    parsed_json: str
    deleted_in_game: bool
    deleted_ts: float | None


@dataclass(frozen=True)
class RecycleReportRecord:
    """Typed structured debris harvest report row (`recycle_reports`)."""

    id: int
    ts: float
    msg_uid: str
    server_msg_id: str
    msg_date_text: str
    coords: str
    recycler_count: int
    cargo_capacity: int
    debris_avail_pig: int
    debris_avail_metal: int
    harvested_pig_iron: int
    harvested_metal: int
    harvested_kryptonite: int
    harvested_spice: int
    harvested_spice_eq: float
    raw_text: str


@dataclass(frozen=True)
class RecycleHarvestSummary:
    """Set-based SQL harvest rollup across `recycle_reports`."""

    report_count: int
    total_recyclers: int
    total_pig_iron: int
    total_metal: int
    total_kryptonite: int
    total_spice: int
    total_spice_eq: float
    latest_ts: float | None


__all__ = [
    "ActiveTargetBlock",
    "BashBlockRecord",
    "BashWindowEntry",
    "BattleReportRecord",
    "GameMessageRecord",
    "RaidHistoryRecord",
    "RaidStatsSummary",
    "RecycleHarvestSummary",
    "RecycleReportRecord",
    "TargetRaidStats",
]
