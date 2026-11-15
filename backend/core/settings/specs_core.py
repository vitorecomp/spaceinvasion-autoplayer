"""Canonical setting specs for the `doctrine` and `economy` groups (stdlib only)."""

from __future__ import annotations

from backend.core.settings.schema import (
    DEFAULT_FLEET_RESEARCH_TARGETS,
    DEFAULT_SHIPYARD_TARGETS,
    VALID_PLANNER_MODES,
    VALID_PLAYSTYLES,
    SettingOption,
    SettingSpec,
)

DOCTRINE_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="planner_mode",
        title="Planner Mode",
        group="doctrine",
        kind="enum",
        default="priority",
        enum_values=VALID_PLANNER_MODES,
        enum_options=(
            SettingOption("priority", "priority — Rule-Based Heuristic"),
            SettingOption("ai_planner", "ai_planner — AI-Planner (Full Horizon)"),
        ),
        control="select",
        bounds_label="default: priority · priority | ai_planner",
        summary="Active autonomous planner engine (rule-based P0-P3 vs full-horizon AI-Planner).",
        mechanics_help=(
            "Selects between the deterministic rule-based P0–P3 heuristic pipeline (priority) "
            "and the full-horizon sequential rollout optimizer (ai_planner, auto-unlocks for live "
            "bot execution at 11/11 colonies). Legacy 'ga_nn' profiles normalize to 'priority' on load."
        ),
        legacy_aliases=(("ga_nn", "priority"),),
    ),
    SettingSpec(
        key="ai_adaptive_execution",
        title="Adaptive AI-Planner Execution",
        group="doctrine",
        kind="bool",
        default=False,
        control="checkbox",
        bounds_label="default: false · Boolean toggle",
        summary="Enable Plan v2 adaptive smelting, remote lab starts, and drift re-tuning.",
        mechanics_help=(
            "Enables Plan v2 adaptive execution: per-colony Blast furnace (b2) 2:1 smelting control, "
            "immediate remote Research lab starts across colonies, in-flight ship hull ownership "
            "accounting, plan-sized transport fleet scaling up to 25% of hourly Metal and Kryptonite "
            "income, and drift-triggered background path re-tuning. Boolean toggle."
        ),
    ),
    SettingSpec(
        key="ai_resource_spare_pct",
        title="AI-Planner Spare Resource Budget (%)",
        group="doctrine",
        kind="float",
        default=10.0,
        min_value=0.0,
        max_value=100.0,
        step=1.0,
        unit="%",
        control="number",
        show_in_config_page=False,
        bounds_label="default: 10.0% · 0.0–100.0%",
        summary="Soft spare resource share allocated to Fleet Planner hulls and tech unlocks.",
        mechanics_help=(
            "Percentage of surplus planetary resources (after reserving the next waiting P1 "
            "AI-Planner build or research step) that Fleet Planner may spend on shipyard hulls "
            "and fleet unlock research. Accepts floats from 0.0 to 100.0."
        ),
    ),
    SettingSpec(
        key="playstyle",
        title="Playstyle Doctrine",
        group="doctrine",
        kind="enum",
        default="fleeter",
        enum_values=VALID_PLAYSTYLES,
        enum_options=(
            SettingOption("fleeter", "fleeter — Combat & Techs"),
            SettingOption("hybrid", "hybrid — Balanced"),
            SettingOption("miner", "miner — Economy ROI"),
        ),
        control="select",
        bounds_label="default: fleeter · fleeter | hybrid | miner",
        summary="Doctrine weighting for research ordering and shipyard hull quotas.",
        mechanics_help=(
            "Weights P2 research ordering and shipyard hull quotas toward offensive warships and "
            "combat/drive technologies (fleeter), compound mine ROI payback (miner), or a balanced "
            "empire progression (hybrid). Applies whenever research or shipyard automation is enabled."
        ),
    ),
    SettingSpec(
        key="allow_multi_planet",
        title="Multi-Planet Empire Planning",
        group="doctrine",
        kind="bool",
        default=True,
        control="checkbox",
        bounds_label="default: true · boolean",
        summary="Schedule buildings, power, warehouses, and fleets across all owned colonies.",
        mechanics_help=(
            "Evaluates construction queues, energy balance, warehouse caps, and fleet dispatch "
            "across every owned colony and Asteroid instead of restricting planning to the currently "
            "active planet. Respects SpaceInvasion Classic slot rules for normal planets, solar-only "
            "Asteroids (1 build slot), and isolated [RK] raid colonies."
        ),
    ),
    SettingSpec(
        key="max_planets",
        title="Max Empire Planets Cap",
        group="doctrine",
        kind="int",
        default=11,
        min_value=1,
        max_value=50,
        step=1,
        unit="planets",
        control="number",
        bounds_label="default: 11 · 1–50 planets",
        summary="Upper cap on normal colonies before halting autonomous colonization.",
        mechanics_help=(
            "Caps total normal planets (owned colonies plus outbound COLONIZE flights) before halting "
            "autonomous Colony ship (s8) dispatches; SpaceInvasion Classic permits 1 homeworld plus "
            "up to 10 normal colonies (11 total, excluding Asteroids and [RK] raid colonies). "
            "Accepts integers from 1 to 50."
        ),
    ),
    SettingSpec(
        key="min_colony_fields",
        title="Minimum Colony Fields Target",
        group="doctrine",
        kind="int",
        default=300,
        min_value=50,
        max_value=600,
        step=10,
        unit="fields",
        control="number",
        bounds_label="default: 300 · 50–600 fields",
        summary="Minimum buildable field count required by the colony grader and COLONIZE selector.",
        mechanics_help=(
            "Minimum buildable planet size required by the colony grader and P2 COLONIZE selector, "
            "favoring Prime Mid-Orbit positions 7–12 capable of rolling 300+ fields so late-game "
            "mines and Fusion plants never stall on slot exhaustion. Accepts integers from 50 to 600 fields."
        ),
    ),
    SettingSpec(
        key="min_free_fields_warning",
        title="Low Free Fields Advisory Threshold",
        group="doctrine",
        kind="int",
        default=20,
        min_value=1,
        max_value=1000,
        step=1,
        unit="fields",
        control="number",
        bounds_label="default: 20 · 1–1000 fields",
        summary="Free-field buffer below which a non-executable colony space advisory fires.",
        mechanics_help=(
            "Raises a P3 non-executable colony space advisory (executable_now=False) and steers power "
            "upgrades toward compact Fusion plant levels once remaining buildable slots "
            "(fields_max - fields_used) drop at or below this buffer. Accepts integers from 1 to 1000 fields."
        ),
    ),
)

ECONOMY_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="auto_throttle_blast_furnace",
        title="Auto-Throttle Blast Furnace (b2)",
        group="economy",
        kind="bool",
        default=True,
        control="checkbox",
        bounds_label="default: true · boolean",
        summary="Automatically toggle Blast furnace (b2) 0%/100% to avoid Pig iron starvation.",
        mechanics_help=(
            "Automatically throttles Blast furnace (b2) to 0% via P1 THROTTLE_BUILDING when 2:1 Pig "
            "iron smelting starves frontier Pig iron upgrades or when Metal storage is 98%+ full "
            "while Pig iron storage has headroom, then restores 100% when Metal is needed. In "
            "SpaceInvasion Classic, b2 consumes 2 Pig iron per 1 Metal and is not boosted by Mining "
            "technology (r1)."
        ),
    ),
    SettingSpec(
        key="blast_pig_iron_max_share",
        title="Max Blast Furnace Pig Iron Share",
        group="economy",
        kind="float",
        default=0.70,
        min_value=0.1,
        max_value=1.0,
        step=0.05,
        control="number",
        bounds_label="default: 0.70 · 0.10–1.00",
        summary="Maximum share of gross Pig iron output that Blast furnace (b2) may smelt.",
        mechanics_help=(
            "Maximum share of gross Pig iron mine (b1) hourly output that Blast furnace (b2) may "
            "consume through its 2:1 smelting ratio before the planner holds further b2 levels or "
            "pauses smelting to preserve positive net Pig iron income. Accepts float ratios from 0.10 to 1.00."
        ),
    ),
    SettingSpec(
        key="merchant_mode",
        title="Merchant Exchange Mode (Skip b2)",
        group="economy",
        kind="bool",
        default=False,
        control="checkbox",
        bounds_label="default: false · boolean",
        summary="Score mine payback with NPC Merchant ratios (6:2.5:3:1) and skip Blast furnace.",
        mechanics_help=(
            "Evaluates mine payback ROI using NPC Merchant trade ratios (6 : 2.5 : 3 : 1 Pig iron / "
            "Metal / Kryptonite / Spice) instead of standard player ratios (4 : 2 : 2 : 1) and skips "
            "Blast furnace (b2) construction when Metal is sourced via trader swaps. Boolean toggle."
        ),
    ),
    SettingSpec(
        key="forbid_planetary_solar",
        title="Forbid Solar Power on Normal Planets",
        group="economy",
        kind="bool",
        default=False,
        control="checkbox",
        bounds_label="default: false · boolean",
        summary="Rely exclusively on Fusion plant (b5) on normal planets (Asteroids exempt).",
        mechanics_help=(
            "Forbids Thermosolar plant (b6) and combat-destructible orbital Solar cells (s9) on "
            "normal planets—relying exclusively on Fusion plant (b5) scaled by Energy technology (r2) "
            "and scheduling P1 step-down DEMOLISH_BUILDING of existing b6 levels when spare energy "
            "covers the loss. Asteroids remain exempt because SpaceInvasion Classic restricts "
            "Asteroids to solar power only."
        ),
    ),
    SettingSpec(
        key="hot_planet_solar_temp_threshold",
        title="Hot Planet Solar Temperature Threshold (°C)",
        group="economy",
        kind="int",
        default=40,
        min_value=-273,
        max_value=1000,
        step=5,
        unit="°C",
        control="number",
        bounds_label="default: 40 · -273 to 1000 °C",
        summary="Minimum planet temperature before emergency power considers orbital Solar cells.",
        mechanics_help=(
            "Minimum colony temperature in °C required before emergency power recovery considers "
            "orbital Solar cells (s9) on tight-field normal planets when forbid_planetary_solar is "
            "disabled; hot inner-orbit slots boost solar yield whereas cold outer slots boost Spice "
            "mine (b4) output. Accepts integers from -273 to 1000 °C."
        ),
    ),
    SettingSpec(
        key="storage_warn_ratio",
        title="Warehouse Overflow Warning Ratio",
        group="economy",
        kind="float",
        default=0.85,
        min_value=0.1,
        max_value=0.99,
        step=0.05,
        control="number",
        bounds_label="default: 0.85 · 0.10–0.99",
        summary="Warehouse fill fraction triggering P1 storage upgrades (b11-b14).",
        mechanics_help=(
            "Schedules P1 warehouse upgrades (b11–b14: Pig iron, Metal, Kryptonite, or Spice "
            "warehouse) as soon as stored stock divided by warehouse capacity reaches this threshold, "
            "preventing SpaceInvasion mines from halting extraction at 100% cap. Accepts float ratios "
            "from 0.10 to 0.99."
        ),
    ),
    SettingSpec(
        key="allow_research",
        title="Autonomous Research Queue",
        group="economy",
        kind="bool",
        default=True,
        control="checkbox",
        bounds_label="default: true · boolean",
        summary="Enable P2 account-wide RESEARCH starts from the highest Research lab.",
        mechanics_help=(
            "Queues P2 account-wide RESEARCH upgrades (r1–r15) from the colony with the highest "
            "Research lab (b8) level whenever the global research slot is idle; research duration "
            "scales with (b8 + 1) * speedup and is unaffected by Microsystem (b17). Boolean toggle."
        ),
    ),
    SettingSpec(
        key="allow_fleet_research",
        title="Fleet & Ship Unlock Research (Spare Budget)",
        group="economy",
        kind="bool",
        default=True,
        control="checkbox",
        bounds_label="default: true · boolean",
        summary="Spend spare Fleet Planner budget on ship unlock prerequisites and r3-r15 targets.",
        mechanics_help=(
            "Spends Fleet Planner soft spare resources (ai_resource_spare_pct, after reserving the "
            "next waiting P1 AI-Planner step) on P2 RESEARCH upgrades to auto-unlock recursive "
            "prerequisites for configured shipyard_targets hulls and reach fleet_research_targets "
            "levels (r3–r15). Holds the Research lab whenever AI-Planner has a ready or next-waiting "
            "P1 research step; requires allow_research enabled."
        ),
    ),
    SettingSpec(
        key="allow_shipyard",
        title="Autonomous Shipyard Construction",
        group="economy",
        kind="bool",
        default=True,
        control="checkbox",
        bounds_label="default: true · boolean",
        summary="Enable P2 Weapons factory (b9) ship builds toward shipyard_targets quotas.",
        mechanics_help=(
            "Enables P2 BUILD_SHIP orders at Weapons factory (b9) to maintain configured "
            "shipyard_targets quotas (Spy probe s10, Transports, Recycler s11, Colony ship s8, and "
            "combat hulls), where per-unit build time divides by b9 * 2^b17 * speedup. Boolean toggle."
        ),
    ),
    SettingSpec(
        key="allow_transmitter",
        title="Daily Free Interdimensional Transmitter Spin",
        group="economy",
        kind="bool",
        default=False,
        control="checkbox",
        bounds_label="default: false · boolean",
        summary="Claim the free daily IDT spin once per Europe/Berlin day (never spends Urplasma).",
        mechanics_help=(
            "Schedules P2 ACTIVATE_TRANSMITTER once per Europe/Berlin server day to claim the free "
            "daily Interdimensional Transmitter spin, strictly guarded by _freeSpins >= 1 and "
            "idtSrc == 1 so zero paid Urplasma is ever spent. Boolean toggle."
        ),
    ),
    SettingSpec(
        key="shipyard_targets",
        title="Shipyard Hull Quotas",
        group="economy",
        kind="int_map",
        default=DEFAULT_SHIPYARD_TARGETS,
        min_value=0,
        max_value=1_000_000_000,
        step=1,
        unit="ships",
        control="map",
        show_in_config_page=False,
        bounds_label="default: baseline fleet quotas · 0–1,000,000,000 per hull",
        summary="Per-ship-slug target hull counts maintained by autonomous shipyard queues.",
        mechanics_help=(
            "Mapping of canonical hyphenated ship slugs (for example 'spy-probe', 'recycler', "
            "'colony-ship', 'battleship') to desired empire hull counts. Accepts integer values from "
            "0 to 1,000,000,000 per ship slug."
        ),
    ),
    SettingSpec(
        key="fleet_research_targets",
        title="Fleet Research Target Levels",
        group="economy",
        kind="int_map",
        default=DEFAULT_FLEET_RESEARCH_TARGETS,
        min_value=0,
        max_value=100,
        step=1,
        unit="level",
        control="map",
        show_in_config_page=False,
        bounds_label="default: {} · 0–100 per research key",
        summary="Per-research-key target levels pursued using Fleet Planner spare resources.",
        mechanics_help=(
            "Mapping of research keys (r3–r15) to desired minimum levels pursued when "
            "allow_fleet_research is enabled. Setting a key to 0 removes its target. Accepts integer "
            "levels from 0 to 100."
        ),
    ),
)
