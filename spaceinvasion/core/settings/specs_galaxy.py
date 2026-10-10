"""Canonical setting specs for the `galaxy` group (stdlib only)."""

from __future__ import annotations

from spaceinvasion.core.settings.schema import SettingSpec

GALAXY_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="allow_galaxy_scan",
        title="Autonomous Galaxy Scanner & Background Sweep",
        group="galaxy",
        kind="bool",
        default=False,
        control="checkbox",
        bounds_label="default: false · boolean",
        summary="Enable spiral SCAN_GALAXY recon and background 2D Galaxy sweeps.",
        mechanics_help=(
            "Enables P2 outward spiral SCAN_GALAXY system recon (1800s per-system cooldown) and "
            "background randomized 2D Galaxy sweeps that index yellow inactive farms, Spacia NPC "
            "planets, orbital rubble fields, and free colony slots. Boolean toggle."
        ),
    ),
    SettingSpec(
        key="galaxy_scan_block_other_actions",
        title="Block Other Browser Actions During Galaxy Scan",
        group="galaxy",
        kind="bool",
        default=True,
        control="checkbox",
        bounds_label="default: true · boolean",
        summary="Keep Chrome exclusively on internalGalaxy during multi-page sweeps (P0 SAVE exempt).",
        mechanics_help=(
            "Keeps Chrome exclusively on internalGalaxy during multi-page Galaxy sweeps so "
            "step_galaxy_system arrow chains are never interrupted by Commander ticks, economy syncs, "
            "or manual live page jumps. Defers autonomous ticks until the sweep finishes, warns on "
            "manual live browser actions mid-scan, and still lets Pause, disabling Galaxy Scan, or P0 "
            "emergency Escape Flights (SAVE) interrupt immediately. Boolean toggle."
        ),
    ),
    SettingSpec(
        key="galaxy_sweep_pages",
        title="Galaxy Sweep Pages per Cycle",
        group="galaxy",
        kind="int",
        default=100,
        min_value=1,
        max_value=400,
        step=10,
        unit="pages",
        control="number",
        bounds_label="default: 100 · 1–400 pages",
        summary="Number of solar-system pages visited per Galaxy sweep cycle.",
        mechanics_help=(
            "Number of solar system pages (gal:sun) visited per randomized 2D Galaxy sweep cycle "
            "(and default page count for the Sweep 100 Pages action button) to keep sector "
            "intelligence fresh across neighboring systems. Accepts integers from 1 to 400 pages."
        ),
    ),
    SettingSpec(
        key="galaxy_sweep_interval_seconds",
        title="Max Background Galaxy Sweep Interval (s)",
        group="galaxy",
        kind="int",
        default=270,
        min_value=30,
        max_value=86400,
        step=15,
        unit="s",
        control="number",
        bounds_label="default: 270 · 30–86400 s",
        summary="Maximum seconds between automatic background Galaxy sweeps.",
        mechanics_help=(
            "Maximum interval in seconds between automatic background multi-page Galaxy sweep runs "
            "when allow_galaxy_scan is enabled, jittered so sector recon refreshes regularly without "
            "starving build or fleet ticks. Accepts integers from 30 to 86400 seconds."
        ),
    ),
    SettingSpec(
        key="galaxy_scan_click_delay_seconds",
        title="Time Between Galaxy Scan Clicks (s)",
        group="galaxy",
        kind="float",
        default=1.5,
        min_value=0.2,
        max_value=60.0,
        step=0.1,
        unit="s",
        control="number",
        bounds_label="default: 1.5 · 0.2–60.0 s",
        summary="Base delay in seconds between consecutive Galaxy page navigation clicks.",
        mechanics_help=(
            "Base delay in seconds waited between consecutive step_galaxy_system / si.navigate page "
            "clicks during multi-page Galaxy sweeps to stay safely under SpaceInvasion's 30-page-view "
            "rate-limit cooldown. Accepts floats from 0.2 to 60.0 seconds."
        ),
    ),
    SettingSpec(
        key="galaxy_scan_click_jitter_seconds",
        title="Random Jitter Between Galaxy Scan Pages (s)",
        group="galaxy",
        kind="float",
        default=0.6,
        min_value=0.0,
        max_value=30.0,
        step=0.1,
        unit="s",
        control="number",
        bounds_label="default: 0.6 · 0.0–30.0 s",
        summary="Uniform +/- jitter offset around the base Galaxy click delay.",
        mechanics_help=(
            "Uniform random +/-jitter offset in seconds added around galaxy_scan_click_delay_seconds "
            "on each Galaxy sweep page step (floored at 0.2s total wait) so page navigation cadence "
            "stays organic rather than fixed-interval. Accepts floats from 0.0 to 30.0 seconds."
        ),
    ),
    SettingSpec(
        key="galaxy_scan_explore_first",
        title="Smart Scan Phase 1: Explore Unscanned Systems First",
        group="galaxy",
        kind="bool",
        default=True,
        control="checkbox",
        bounds_label="default: true · boolean",
        summary="Discover all unscanned systems in owned galaxies before weighted refresh.",
        mechanics_help=(
            "Phase 1 (exploratory): Galaxy sweeps and SCAN_GALAXY visit every never-scanned solar "
            "system (1–400) in each galaxy where you own a non-raid colony before they revisit any "
            "system. Systems are ordered outward from your nearest colony so si.navigate steps stay "
            "contiguous. Once coverage reaches 100%, or right away when this is off, sweeps switch "
            "to Phase 2: weighted refresh by proximity, attack history, and staleness. Boolean toggle."
        ),
    ),
    SettingSpec(
        key="galaxy_scan_refresh_pct",
        title="Smart Scan Refresh vs Discovery Split (%)",
        group="galaxy",
        kind="int",
        default=0,
        min_value=0,
        max_value=100,
        step=5,
        unit="%",
        control="number",
        bounds_label="default: 0% · 0–100%",
        summary="Percentage of each sweep batch devoted to refreshing already-scanned sectors.",
        mechanics_help=(
            "Percentage of each galaxy sweep batch allocated to refreshing already-scanned sectors "
            "(by proximity, raid affinity, debris/targets, and staleness), while the remaining "
            "(100 - refresh)% discovers never-scanned systems. Unused quota spills automatically to "
            "the other pool. Accepts integers from 0 to 100."
        ),
    ),
    SettingSpec(
        key="galaxy_scan_all_galaxies",
        title="Smart Scan Scope: Discover Across All 14 Galaxies (G1–G14)",
        group="galaxy",
        kind="bool",
        default=False,
        control="checkbox",
        bounds_label="default: false · boolean",
        summary="Expand smart galaxy sweeps beyond owned galaxies across all 14 galaxies.",
        mechanics_help=(
            "When enabled, smart galaxy sweeps and the Sectors Scan Queue expand beyond galaxies "
            "where you already own a colony to discover and refresh all 14 galaxies (G1–G14) outward "
            "from your colonies (nearest neighbor galaxies first). When disabled, sweeps stay inside "
            "owned galaxies unless a mixed Refresh/Discovery split (1–99%) has already finished 100% "
            "of owned galaxies. Boolean toggle."
        ),
    ),
    SettingSpec(
        key="galaxy_scan_near_weight",
        title="Smart Scan Phase 2: Near-Sector Proximity Weight",
        group="galaxy",
        kind="float",
        default=2.0,
        min_value=0.0,
        max_value=20.0,
        step=0.5,
        control="number",
        bounds_label="default: 2.0 · 0.0–20.0",
        summary="Phase 2 targeted-refresh multiplier on circular proximity to your closest colony.",
        mechanics_help=(
            "Phase 2 (targeted_refresh) multiplier on the proximity score of each (galaxy, system) "
            "sector. Proximity uses the circular system distance to your closest non-raid colony, "
            "min(|s - own_s|, 400 - |s - own_s|), with a penalty for other galaxies. Higher values "
            "refresh nearby sectors more often; 0.0 ignores distance. Accepts floats from 0.0 to 20.0."
        ),
    ),
    SettingSpec(
        key="galaxy_scan_attack_weight",
        title="Smart Scan Phase 2: Frequent-Attack Player & Sector Weight",
        group="galaxy",
        kind="float",
        default=3.0,
        min_value=0.0,
        max_value=20.0,
        step=0.5,
        control="number",
        bounds_label="default: 3.0 · 0.0–20.0",
        summary="Phase 2 targeted-refresh multiplier on raid-history affinity per sector and owner.",
        mechanics_help=(
            "Phase 2 (targeted_refresh) multiplier on attack-history affinity from raid_history. "
            "Combines direct raids and loot in a GG:SSS sector with per-owner frequency, adding "
            "debris, yellow inactive, Spacia, and 300+ free-slot value multiplied by scan staleness. "
            "Higher values keep frequent farm targets fresh; 0.0 ignores attack history. Accepts "
            "floats from 0.0 to 20.0."
        ),
    ),
    SettingSpec(
        key="allow_colonize",
        title="Autonomous Free-Slot Colonization",
        group="galaxy",
        kind="bool",
        default=False,
        control="checkbox",
        bounds_label="default: false · boolean",
        summary="Launch Colony ships (s8) on verified 300+-field candidates up to max_planets.",
        mechanics_help=(
            "Probes non-discarded free slots (Prime Mid-Orbit 7–12 first) until qualified 300+-field "
            "candidates cover max_planets minus allocated colonies, then launches stationed Colony "
            "ships (s8) on verified 300+-field candidates when 1 + b10 fleet slots permit. Boolean toggle."
        ),
    ),
)
