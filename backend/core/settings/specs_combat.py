"""Canonical setting specs for the `combat` and `runtime` groups (stdlib only)."""

from __future__ import annotations

from backend.core.settings.schema import SettingSpec

COMBAT_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="allow_raids",
        title="Autonomous Espionage & Inactive/Spacia Raids",
        group="combat",
        kind="bool",
        default=False,
        control="checkbox",
        bounds_label="default: false · boolean",
        summary="Enable P1 SPY scouting and P2 ATTACK/RECYCLE waves against farms and debris.",
        mechanics_help=(
            "Enables P1 SPY scouting (s10 on inhabited planets and Spacia, refreshing intel older "
            "than 4h) and P2 ATTACK/RECYCLE harvest waves against yellow inactives, Spacia NPC "
            "planets, and rubble fields ranked by total resources per round-trip flight time (res/h), "
            "bounded by 1 + b10 concurrent fleet slots. Boolean toggle."
        ),
    ),
    SettingSpec(
        key="allow_transport",
        title="Autonomous Inter-Planet Resource Transport",
        group="combat",
        kind="bool",
        default=False,
        control="checkbox",
        bounds_label="default: false · boolean",
        summary="Dispatch P1 TRANSPORT flights strictly between owned planets for shortages.",
        mechanics_help=(
            "Dispatches P1 TRANSPORT flights (s3/s2/s1) strictly between your own planets to supply "
            "P1/P2 resource shortfalls and bootstrap young colonies from surplus planets (respecting "
            "Spice fuel reserves and 85% recipient storage caps). Runs at Fleet Priority #2: after "
            "SPY (#1) and ahead of COLONIZE (#3) and ATTACK/RECYCLE (#4). Boolean toggle."
        ),
    ),
    SettingSpec(
        key="reserved_spy_slots",
        title="Reserved Spy Fleet Slots",
        group="combat",
        kind="int",
        default=1,
        min_value=0,
        max_value=10,
        step=1,
        unit="slots",
        control="number",
        bounds_label="default: 1 · 0–10 slots",
        summary="Fleet base (1 + b10) concurrent flight slots held exclusively for SPY missions.",
        mechanics_help=(
            "Number of Fleet base (1 + b10) concurrent flight seats held exclusively for SPY probe "
            "missions (#1) whenever ready spy targets and stationed s10 probes exist, enforcing the "
            "4-tier fleet priority SPY (#1) → TRANSPORT (#2) → COLONIZE (#3) → ATTACK & RECYCLE (#4). "
            "Accepts integers from 0 to 10."
        ),
    ),
    SettingSpec(
        key="spy_reserved_slots_only",
        title="Restrict Spy Probes to Reserved Slot(s) Only",
        group="combat",
        kind="bool",
        default=False,
        control="checkbox",
        bounds_label="default: false · boolean",
        summary="Cap SPY launches per planet to reserved_spy_slots without spilling into extra seats.",
        mechanics_help=(
            "When enabled, SPY probe launches on each planet are strictly capped to the Reserved Spy "
            "Fleet Slots count (reserved_spy_slots) and will never spill into extra unreserved fleet "
            "slots—keeping remaining Fleet base (1 + b10) slots free for TRANSPORT, COLONIZE, ATTACK, "
            "and RECYCLE flights. Boolean toggle."
        ),
    ),
    SettingSpec(
        key="spy_dispatch_planet_id",
        title="Designated Spy Dispatch Colony",
        group="combat",
        kind="planet_id",
        default=0,
        min_value=0,
        max_value=2**63 - 1,
        step=1,
        control="planet-select",
        bounds_label="default: 0 (Auto) · colony planet_id",
        summary="Lock SPY probe launches to one owned colony planet_id (0 = auto-select).",
        mechanics_help=(
            "Locks all SPY probe launches to a specific owned non-raid colony planet_id (holding SPY "
            "and prioritizing s10 construction there if its probe stock hits zero), or set 0 to "
            "automatically select the active or closest colony carrying stationed Spy probes (s10). "
            "Accepts 0 (Auto) or an owned colony ID."
        ),
    ),
    SettingSpec(
        key="allow_pvp_in_az",
        title="Allow Active-Player PvP Raids Inside Attack Zone",
        group="combat",
        kind="bool",
        default=False,
        control="checkbox",
        bounds_label="default: false · boolean",
        summary="Permit SPY/ATTACK against active players in your AZ with 3/24h Bash enforcement.",
        mechanics_help=(
            "Permits P2 SPY and ATTACK missions against active (light_red) players within your "
            "SpaceInvasion Attack Zone (AZ) tier, strictly enforcing the 3 attacks per 24 hours Bash "
            "rule per target planet and 6-hour cooldowns on newbie, vacation, or out-of-attackzone "
            "targets. Boolean toggle."
        ),
    ),
    SettingSpec(
        key="combat_safety_margin",
        title="1:1 Combat Simulator Safety Margin",
        group="combat",
        kind="float",
        default=1.4,
        min_value=1.0,
        max_value=1000.0,
        step=0.1,
        unit="x",
        control="number",
        bounds_label="default: 1.4 · 1.0–1000.0x",
        summary="Minimum attacker-to-defender effective power ratio in the 1:1 combat simulator.",
        mechanics_help=(
            "Minimum attacker-to-defender effective power ratio required in SpaceInvasion Classic's "
            "zero-rapidfire 1:1 combat simulator (incorporating Weapons r11, Shield r12, and Defense "
            "r13 tech multipliers) before authorizing any ATTACK wave. Accepts floats from 1.0 to 1000.0."
        ),
    ),
    SettingSpec(
        key="raid_min_loot_spice_eq",
        title="Minimum Raid Plunder Threshold (Spice Eq)",
        group="combat",
        kind="float",
        default=1500.0,
        min_value=0.0,
        max_value=1e12,
        step=100.0,
        unit="SpEq",
        control="number",
        bounds_label="default: 1500 · >= 0 SpEq",
        summary="Minimum expected 50% raid plunder in Spice-equivalent units.",
        mechanics_help=(
            "Minimum expected 50% raid plunder converted into Spice-equivalent units (4 : 2 : 2 : 1 "
            "standard value) required before committing cargo hulls, Spice flight fuel, and a 1 + b10 "
            "fleet slot to an ATTACK mission. Accepts floats of 0 or higher."
        ),
    ),
    SettingSpec(
        key="attack_min_res_per_hour",
        title="Min Attack Yield (res/h)",
        group="combat",
        kind="float",
        default=0.0,
        min_value=0.0,
        max_value=1e12,
        step=100.0,
        unit="res/h",
        control="number",
        bounds_label="default: 0 (off) · >= 0 res/h",
        summary="Minimum expected raid plunder per round-trip flight hour (0 disables filter).",
        mechanics_help=(
            "Minimum expected total plunder throughput per round-trip flight hour (Pig iron + Metal "
            "+ Kryptonite + Spice divided by round-trip hours) required from a candidate origin "
            "planet before triggering an ATTACK mission. Origins below this threshold are skipped so "
            "closer colonies can qualify; set to 0 to disable. Tutorial fight missions remain exempt."
        ),
    ),
    SettingSpec(
        key="recycle_min_res_per_hour",
        title="Min Recycle Yield (res/h)",
        group="combat",
        kind="float",
        default=0.0,
        min_value=0.0,
        max_value=1e12,
        step=100.0,
        unit="res/h",
        control="number",
        bounds_label="default: 0 (off) · >= 0 res/h",
        summary="Minimum expected debris recovery per round-trip Recycler flight hour (0 disables).",
        mechanics_help=(
            "Minimum expected debris recovery throughput per round-trip Recycler flight hour "
            "(expected collectible rubble divided by round-trip hours) required from a candidate "
            "origin planet before triggering a RECYCLE mission. Distant origins below this threshold "
            "are skipped so closer Recycler hubs can qualify; set to 0 to disable. Tutorial recycle "
            "missions remain exempt."
        ),
    ),
    SettingSpec(
        key="allow_fleetsave",
        title="P0 Automatic Hostile Escape Flight (SAVE)",
        group="combat",
        kind="bool",
        default=False,
        control="checkbox",
        bounds_label="default: false · boolean",
        summary="Launch immediate P0 SAVE Escape flights when incoming hostile attacks are detected.",
        mechanics_help=(
            "Launches an immediate P0 ESCAPE_FLIGHT (SAVE mission) whenever an incoming hostile fleet "
            "targets a colony, evacuating stationed ships and packing cargo in Spice → Kryptonite → "
            "Metal → Pig iron priority order; SpaceInvasion Classic Escape flights are invisible to "
            "Asteroid Galaxy scanners. Boolean toggle."
        ),
    ),
    SettingSpec(
        key="fleet_save_fuel_reserve_spice",
        title="Escape Flight Spice Fuel Reserve",
        group="combat",
        kind="int",
        default=500,
        min_value=0,
        max_value=1_000_000_000_000,
        step=50,
        unit="Spice",
        control="number",
        bounds_label="default: 500 · >= 0 Spice",
        summary="Planetary Spice held back as propellant before packing ESCAPE_FLIGHT cargo.",
        mechanics_help=(
            "Quantity of planetary Spice held back on the origin colony before loading ESCAPE_FLIGHT "
            "cargo holds so stationed fleets always retain sufficient propellant to launch the SAVE "
            "flight and follow-up missions. Accepts integers of 0 or higher."
        ),
    ),
)

RUNTIME_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="action_batch_interval_seconds",
        title="Minimum Batch & Snapshot Refresh Interval (s)",
        group="runtime",
        kind="int",
        default=120,
        min_value=5,
        max_value=86400,
        step=5,
        unit="s",
        control="number",
        bounds_label="default: 120 · 5–86400 s",
        summary="Minimum pause in seconds between autonomous Commander tick batches.",
        mechanics_help=(
            "Minimum pause in seconds between autonomous Commander execution batches and full Chrome "
            "DOM snapshot refreshes when no active building or fleet timer completes sooner, pacing "
            "navigation under SpaceInvasion page-view limits. Accepts integers from 5 to 86400 seconds."
        ),
    ),
    SettingSpec(
        key="max_actions_per_hour",
        title="Max Live Actions per Rolling Hour",
        group="runtime",
        kind="int",
        default=60,
        min_value=1,
        max_value=100_000,
        step=1,
        unit="ops/hr",
        control="number",
        bounds_label="default: 60 · 1–100000 ops/hr",
        summary="Rolling 60-minute cap on live browser mutations executed by GameApiBridge.",
        mechanics_help=(
            "Rolling 60-minute action cap limiting total live browser mutations (BUILD, RESEARCH, "
            "BUILD_SHIP, THROTTLE_BUILDING, and fleet dispatches) executed by GameApiBridge to "
            "prevent runaway click loops. Accepts integers from 1 to 100000 actions/hour."
        ),
    ),
    SettingSpec(
        key="page_view_limit_per_minute",
        title="Max Page Views per Rolling Minute",
        group="runtime",
        kind="int",
        default=60,
        min_value=6,
        max_value=600,
        step=2,
        unit="views/min",
        control="number",
        bounds_label="default: 60 · 6–600 views/min",
        summary="Rolling 60-second local browser page-view budget enforced by PageViewLimiter.",
        mechanics_help=(
            "Rolling 60-second local browser page-view budget enforced by PageViewLimiter across "
            "Commander ticks and interactive commands (reserving up to 6 slots for interactive bursts "
            "and scaling inter-page pacing delay proportionally). BitMeUp's game server enforces its "
            "own server-side throttle around 30–35 views/min with a 45s cooldown if exceeded. "
            "Accepts integers from 6 to 600 views/minute."
        ),
    ),
    SettingSpec(
        key="ga_population_size",
        title="Evolutionary Population Size",
        group="runtime",
        kind="int",
        default=24,
        min_value=4,
        max_value=200,
        step=1,
        unit="genomes",
        control="number",
        bounds_label="default: 24 · 4–200 genomes",
        summary="Candidate chromosomes maintained per evolutionary search generation.",
        mechanics_help=(
            "Number of candidate chromosomes maintained per evolutionary generation in offline/batch "
            "planner search; larger populations explore broader mine, Fusion power, and tech orders "
            "at higher CPU cost. Accepts integers from 4 to 200."
        ),
    ),
    SettingSpec(
        key="ga_generations",
        title="Evolutionary Generations",
        group="runtime",
        kind="int",
        default=12,
        min_value=1,
        max_value=200,
        step=1,
        unit="cycles",
        control="number",
        bounds_label="default: 12 · 1–200 cycles",
        summary="Number of selection, crossover, and mutation cycles per evolutionary search pass.",
        mechanics_help=(
            "Number of tournament-selection, crossover, and mutation cycles executed per evolutionary "
            "search pass to converge on lower-wait, higher-throughput build plans. Accepts integers "
            "from 1 to 200."
        ),
    ),
    SettingSpec(
        key="ga_mutation_rate",
        title="Evolutionary Genome Mutation Rate",
        group="runtime",
        kind="float",
        default=0.25,
        min_value=0.0,
        max_value=1.0,
        step=0.05,
        control="number",
        bounds_label="default: 0.25 · 0.00–1.00",
        summary="Per-gene mutation probability during evolutionary offspring reproduction.",
        mechanics_help=(
            "Per-gene mutation probability applied during evolutionary offspring reproduction to "
            "diversify build-order search and escape local optima such as warehouse or power "
            "bottlenecks. Accepts floats from 0.00 to 1.00."
        ),
    ),
)
