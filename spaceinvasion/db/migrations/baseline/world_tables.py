"""Frozen `0001` baseline DDL for galaxy, intel, messages, raids, and colonies tables."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from spaceinvasion.db.migrations.baseline._cols import bi, dbl, id_pk, txt

WORLD_TABLE_NAMES: tuple[str, ...] = (
    "galaxy_scans",
    "galaxy_scan_history",
    "galaxy_slots",
    "galaxy_slot_history",
    "galaxy_sector_hot",
    "spy_targets",
    "spy_reports",
    "universe_research_max",
    "universe_research_max_history",
    "game_messages",
    "battle_reports",
    "recycle_reports",
    "raid_history",
    "bash_blocks",
    "loot_recycle_hourly",
    "colony_candidates_hot",
    "colony_attempts",
)


def _create_galaxy_tables() -> None:
    op.create_table(
        "galaxy_scans",
        bi("galaxy", pk=True),
        bi("system", pk=True),
        dbl("last_scanned_ts"),
        bi("occupied_count", default="0"),
        bi("target_count", default="0"),
        bi("free_count", default="0"),
    )
    op.create_index("idx_galaxy_scans_ts", "galaxy_scans", ["last_scanned_ts"])

    op.create_table(
        "galaxy_scan_history",
        id_pk(),
        dbl("ts"),
        bi("galaxy"),
        bi("system"),
        bi("occupied_count", default="0"),
        bi("target_count", default="0"),
        bi("free_count", default="0"),
        bi("debris_count", default="0"),
        bi("asteroid_count", default="0"),
    )
    op.create_index("idx_galaxy_scan_history_sys_ts", "galaxy_scan_history", ["galaxy", "system", "ts"])

    op.create_table(
        "galaxy_slots",
        txt("coords", pk=True),
        bi("galaxy"),
        bi("system"),
        bi("position"),
        txt("planet_name", default="''"),
        txt("owner", default="''"),
        txt("status_color", default="'free'"),
        bi("is_inactive", default="0"),
        bi("is_spacia", default="0"),
        bi("has_debris", default="0"),
        dbl("last_scanned_ts"),
        bi("colonize_min_fields", nullable=True),
        bi("colonize_max_fields", nullable=True),
        bi("temperature", nullable=True),
    )
    op.create_index("idx_galaxy_slots_sys", "galaxy_slots", ["galaxy", "system", "position"])

    op.create_table(
        "galaxy_slot_history",
        id_pk(),
        dbl("ts"),
        txt("coords"),
        bi("galaxy"),
        bi("system"),
        bi("position"),
        txt("planet_name", default="''"),
        txt("owner", default="''"),
        txt("status_color", default="'free'"),
        bi("is_inactive", default="0"),
        bi("is_spacia", default="0"),
        bi("has_debris", default="0"),
        bi("has_asteroid", default="0"),
        bi("colonize_min_fields", nullable=True),
        bi("colonize_max_fields", nullable=True),
        bi("temperature", nullable=True),
    )
    op.create_index("idx_galaxy_slot_history_coords_ts", "galaxy_slot_history", ["coords", "ts"])

    op.create_table(
        "galaxy_sector_hot",
        bi("galaxy", pk=True),
        bi("system", pk=True),
        dbl("last_scanned_ts", default="0.0"),
        bi("scan_count", default="0"),
        bi("occupied_count", default="0"),
        bi("target_count", default="0"),
        bi("inactive_count", default="0"),
        bi("spacia_count", default="0"),
        bi("debris_count", default="0"),
        bi("free_count", default="0"),
        bi("prime_colony_free_count", default="0"),
        bi("hist_debris_max", default="0"),
        txt("owners_json", default="'[]'"),
        txt("owner_planet_counts_json", default="'{}'"),
        txt("coord_owners_json", default="'{}'"),
        txt("spacia_coords_json", default="'[]'"),
        dbl("updated_ts", default="0.0"),
    )
    op.create_index("idx_galaxy_sector_hot_ts", "galaxy_sector_hot", ["last_scanned_ts"])


def _create_intel_tables() -> None:
    op.create_table(
        "spy_targets",
        txt("coords", pk=True),
        txt("owner", default="''"),
        txt("planet_name", default="''"),
        txt("status_color", default="'yellow'"),
        bi("is_inactive", default="0"),
        bi("is_spacia", default="0"),
        bi("has_debris", default="0"),
        bi("points", nullable=True),
        bi("pig_iron", default="0"),
        bi("metal", default="0"),
        bi("kryptonite", default="0"),
        bi("spice", default="0"),
        txt("fleet_json", default="'{}'"),
        txt("defense_json", default="'{}'"),
        bi("dug_in", default="0"),
        dbl("last_scanned_ts"),
        dbl("last_spied_ts", default="0.0"),
        dbl("blocked_until_ts", default="0.0"),
        txt("block_reason", default="''"),
        bi("bash_hits", default="0"),
        dbl("last_blocked_ts", default="0.0"),
        bi("uninhabited_fields", nullable=True),
        bi("spy_count", default="0"),
        bi("is_own_planet", default="0"),
        txt("merged_into", default="''"),
        bi("colonize_min_fields", nullable=True),
        bi("colonize_max_fields", nullable=True),
        bi("temperature", nullable=True),
    )
    op.create_index(
        "idx_spy_targets_colony_filter",
        "spy_targets",
        ["is_own_planet", "merged_into", "is_spacia", "is_inactive"],
    )

    op.create_table(
        "spy_reports",
        id_pk(),
        dbl("ts"),
        txt("target_coords"),
        txt("owner", default="''"),
        txt("planet_name", default="''"),
        bi("is_spacia", default="0"),
        bi("pig_iron", default="0"),
        bi("metal", default="0"),
        bi("kryptonite", default="0"),
        bi("spice", default="0"),
        dbl("loot_spice_eq", default="0.0"),
        txt("buildings_json", default="'{}'"),
        txt("fleet_json", default="'{}'"),
        txt("defense_json", default="'{}'"),
        txt("researches_json", default="'{}'"),
        bi("dug_in", nullable=True),
        bi("uninhabited_fields", nullable=True),
        txt("server_msg_id", default="''"),
        txt("msg_date_text", default="''"),
        txt("raw_text", default="''"),
    )
    op.create_index("idx_spy_reports_coords_ts", "spy_reports", ["target_coords", "ts"])

    op.create_table(
        "universe_research_max",
        txt("research_key", pk=True),
        bi("research_id"),
        txt("slug"),
        txt("name"),
        bi("own_level", default="0"),
        bi("spied_max_level", default="0"),
        bi("server_record_level", nullable=True),
        bi("universe_max_level", default="0"),
        bi("catalog_max_level", nullable=True),
        txt("holder", default="''"),
        txt("holder_coords", default="''"),
        dbl("updated_ts"),
    )

    op.create_table(
        "universe_research_max_history",
        id_pk(),
        dbl("ts"),
        txt("source", default="'refresh'"),
        txt("records_json"),
    )
    op.create_index("idx_universe_research_max_hist_ts", "universe_research_max_history", ["ts"])


def _create_message_tables() -> None:
    op.create_table(
        "game_messages",
        id_pk(),
        dbl("ingested_ts"),
        txt("msg_uid", unique=True),
        txt("server_msg_id", default="''"),
        txt("folder_type", default="''"),
        txt("category", default="'other'"),
        txt("msg_date_text", default="''"),
        txt("sender", default="''"),
        txt("subject", default="''"),
        txt("coords", default="''"),
        txt("body_text", default="''"),
        txt("raw_html", default="''"),
        txt("parsed_json", default="'{}'"),
        bi("deleted_in_game", default="0"),
        dbl("deleted_ts", nullable=True),
    )
    op.create_index("idx_game_messages_cat", "game_messages", ["category", "ingested_ts"])
    op.create_index("idx_game_messages_coords", "game_messages", ["coords", "ingested_ts"])
    op.create_index("idx_game_messages_ts", "game_messages", ["ingested_ts"])

    op.create_table(
        "battle_reports",
        id_pk(),
        dbl("ts"),
        txt("msg_uid", unique=True),
        txt("server_msg_id", default="''"),
        txt("battle_id", default="''"),
        txt("msg_date_text", default="''"),
        txt("target_coords", default="''"),
        txt("attacker", default="''"),
        txt("defender", default="''"),
        txt("outcome", default="'win'"),
        bi("rounds", default="0"),
        bi("pig_iron", default="0"),
        bi("metal", default="0"),
        bi("kryptonite", default="0"),
        bi("spice", default="0"),
        dbl("loot_spice_eq", default="0.0"),
        bi("attacker_lost_spice", default="0"),
        bi("defender_lost_spice", default="0"),
        bi("debris_pig_iron", default="0"),
        bi("debris_metal", default="0"),
        dbl("debris_spice_eq", default="0.0"),
        txt("raw_text", default="''"),
    )
    op.create_index("idx_battle_reports_coords_ts", "battle_reports", ["target_coords", "ts"])
    op.create_index("idx_battle_reports_ts", "battle_reports", ["ts"])

    op.create_table(
        "recycle_reports",
        id_pk(),
        dbl("ts"),
        txt("msg_uid", unique=True),
        txt("server_msg_id", default="''"),
        txt("msg_date_text", default="''"),
        txt("coords", default="''"),
        bi("recycler_count", default="0"),
        bi("cargo_capacity", default="0"),
        bi("debris_avail_pig", default="0"),
        bi("debris_avail_metal", default="0"),
        bi("harvested_pig_iron", default="0"),
        bi("harvested_metal", default="0"),
        bi("harvested_kryptonite", default="0"),
        bi("harvested_spice", default="0"),
        dbl("harvested_spice_eq", default="0.0"),
        txt("raw_text", default="''"),
    )
    op.create_index("idx_recycle_reports_coords_ts", "recycle_reports", ["coords", "ts"])
    op.create_index("idx_recycle_reports_ts", "recycle_reports", ["ts"])


def _create_raid_and_colony_tables() -> None:
    op.create_table(
        "raid_history",
        id_pk(),
        txt("target_coords"),
        dbl("attacked_ts"),
        bi("is_exempt", default="0"),
        txt("outcome", default="''"),
        txt("loot_json", default="'{}'"),
    )
    op.create_index("idx_raid_history_coords_ts", "raid_history", ["target_coords", "attacked_ts"])
    op.create_index("idx_raid_history_ts", "raid_history", ["attacked_ts"])

    op.create_table(
        "bash_blocks",
        id_pk(),
        txt("target_coords"),
        dbl("blocked_ts"),
        dbl("blocked_until_ts"),
        dbl("cooldown_seconds", default="0.0"),
        bi("is_bash", default="1"),
        txt("reason", default="''"),
    )
    op.create_index("idx_bash_blocks_coords_ts", "bash_blocks", ["target_coords", "blocked_ts"])
    op.create_index("idx_bash_blocks_until_ts", "bash_blocks", ["blocked_until_ts"])

    op.create_table(
        "loot_recycle_hourly",
        bi("hour_bucket", pk=True),
        dbl("stolen_pig_iron", default="0"),
        dbl("stolen_metal", default="0"),
        dbl("stolen_kryptonite", default="0"),
        dbl("stolen_spice", default="0"),
        dbl("stolen_total", default="0"),
        dbl("recycled_pig_iron", default="0"),
        dbl("recycled_metal", default="0"),
        dbl("recycled_kryptonite", default="0"),
        dbl("recycled_spice", default="0"),
        dbl("recycled_total", default="0"),
        bi("attack_count", default="0"),
        bi("recycle_count", default="0"),
        dbl("updated_at", default="0"),
    )

    op.create_table(
        "colony_candidates_hot",
        txt("coords", pk=True),
        bi("galaxy", default="0"),
        bi("system", default="0"),
        bi("position", default="0"),
        bi("uninhabited_fields", nullable=True),
        bi("colonize_min_fields", nullable=True),
        bi("colonize_max_fields", nullable=True),
        bi("max_fields_est", default="0"),
        bi("temperature", nullable=True),
        bi("is_probed", default="0"),
        dbl("last_spied_ts", default="0.0"),
        dbl("last_scanned_ts", default="0.0"),
        bi("spy_count", default="0"),
        dbl("blocked_until_ts", default="0.0"),
        txt("block_reason", default="''"),
        bi("is_discarded", default="0"),
        bi("is_active", default="1"),
        dbl("updated_ts", default="0.0"),
    )
    op.create_index(
        "idx_colony_candidates_hot_rank",
        "colony_candidates_hot",
        ["is_active", "is_discarded", "is_probed", sa.desc(sa.column("max_fields_est")), "position", "coords"],
    )
    op.create_index("idx_colony_candidates_hot_sys", "colony_candidates_hot", ["galaxy", "system", "is_active"])

    op.create_table(
        "colony_attempts",
        id_pk(),
        dbl("ts"),
        txt("event_type"),
        txt("target_coords"),
        txt("origin_coords", default="''"),
        bi("planet_id", nullable=True),
        bi("fields_rolled", nullable=True),
        bi("min_fields_target", default="300"),
        bi("expected_min_fields", nullable=True),
        bi("expected_max_fields", nullable=True),
        bi("temperature", nullable=True),
        txt("grade", default="''"),
        bi("score", nullable=True),
        txt("status_message", default="''"),
        txt("details_json", default="'{}'"),
    )
    op.create_index("idx_colony_attempts_coords_ts", "colony_attempts", ["target_coords", "ts"])
    op.create_index("idx_colony_attempts_ts", "colony_attempts", ["ts"])


def create_world_tables() -> None:
    """Create the 17 frozen baseline world, intel, message, raid, and colony tables."""
    _create_galaxy_tables()
    _create_intel_tables()
    _create_message_tables()
    _create_raid_and_colony_tables()


__all__ = ["WORLD_TABLE_NAMES", "create_world_tables"]
