"""Deterministic documentation generator (`--write` / `--check`) for settings and API commands."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import importlib
from pathlib import Path
import sys

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.core.settings import generate_settings_reference_markdown  # noqa: E402
from backend.runtime.api.registry import (  # noqa: E402
    CommandRegistry,
    generate_command_reference_markdown,
)

SETTINGS_DOC_PATH: Path = REPO_ROOT / "docs" / "settings-reference.md"
API_COMMANDS_DOC_PATH: Path = REPO_ROOT / "docs" / "api-commands.md"

# Canonical 60-command fallback matching `generate_command_reference_markdown(build_command_registry())`.
# Used only before Wave 4 merges `backend/runtime/api/handlers/`; once present, live introspection runs.
# Top-level split("|", 5): name|needs_browser|mutates|params_class|summary|params_semi_list
# Each param entry split("~", 4): name~type~required~default~constraints
_FALLBACK_COMMAND_ROWS: str = """
abandon_colony|1|1|AbandonColonyParams|Safely delete an undersized newly settled colony.|planet_id~object~No~`None`~—;target_coords~object~No~`None`~—;min_colony_fields~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
action_graph|0|0|ActionGraphParams|Build the Kahn dependency DAG for current or simulated strategy settings.|config~object~No~`None`~—;planner_mode~object~No~`None`~—;simulated~object~No~`None`~—;planet_id~object~No~`None`~—;refresh~object~No~`False`~—
activate_transmitter|1|1|ActivateTransmitterParams|Collect daily login bonus and activate the free Interdimensional Transmitter spin.|planet_id~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
ai_planner_get|0|0|AiPlannerGetParams|Read active AI-Planner graph, steps, and adaptive status.|window_size~int~No~`40`~min=5, max=200;week~int | None~No~`None`~min=1, max=520;planet_id~object~No~`None`~—;page~int~No~`1`~min=1, max=100000;page_size~int~No~`50`~min=5, max=500;search~object~No~`''`~—;limit~int~No~`20`~min=1, max=200
ai_planner_run|0|1|AiPlannerRunParams|Start or synchronously execute an AI-Planner run.|params~object~No~`None`~—;sync~object~No~`False`~—;refresh~object~No~`False`~—;auto_run_config_if_missing~object~No~`True`~—;trigger_source~object~No~`'manual'`~—
attack|1|1|AttackParams|Dispatch an attack fleet after protection, bash, and 1:1 combat safety checks.|target_coords~object~No~`None`~—;planet_id~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
attack_results|0|0|AttackResultsParams|Return battle reports, raid stats, and active target blocks.|limit~int~No~`150`~min=1, max=1000;coords~object~No~`None`~—
bot_control|0|1|BotControlParams|Pause/resume automation, clear CAPTCHA gates on resume, or update mode_override.|paused~bool | _UnsetType~No~`UNSET`~—;action~str | None~No~`None`~—;mode_override~str | None | _UnsetType~No~`UNSET`~—
build|1|1|BuildParams|Queue or start a building upgrade on the specified planet.|building_id~object~No~`None`~—;planet_id~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
build_ship|1|1|BuildShipParams|Commission ships in the planet's Weapons Factory.|ship_id~object~No~`None`~—;quantity~object~No~`1`~—;planet_id~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
buildings_eta|0|0|PlanetViewParams|Compute building upgrade ETAs and Config Optimizer goals for a planet.|planet_id~object~No~`None`~—;refresh~bool~No~`False`~—
claim_tutorial|1|1|ClaimTutorialParams|Claim a completed SpaceInvasion tutorial step reward.|dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
colonize|1|1|ColonizeParams|Send a colony ship to settle target coordinates.|target_coords~object~No~`None`~—;planet_id~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
colony_candidates|0|0|ColonyCandidatesParams|List graded uninhabited colony candidates and colonization attempt stats.|limit~int~No~`200`~min=1, max=1000;include_discarded~bool~No~`False`~—;min_fields~int | None~No~`None`~min=50, max=600
combat_overview|0|0|PlanetViewParams|Summarize planet fleet/defense combat strength and raid targets.|planet_id~object~No~`None`~—;refresh~bool~No~`False`~—
config|0|0|ConfigParams|Return the active StrategyConfig snapshot.|
config_optimizer_get|0|0|ConfigOptimizerGetParams|Read active Config Optimizer result and run history.|limit~int~No~`20`~min=1, max=200
config_optimizer_run|0|1|ConfigOptimizerRunParams|Start or synchronously execute a Config Optimizer run.|params~object~No~`None`~—;auto_replan_ai~object~No~`True`~—;sync~object~No~`False`~—;trigger_source~object~No~`'manual'`~—
config_optimizer_set_specializations|0|1|ConfigOptimizerSetSpecializationsParams|Update or replace per-planet Config Optimizer specializations.|planet_specializations~object~No~`None`~—;replace~object~No~`False`~—
config_optimizer_update_planet|0|1|ConfigOptimizerUpdatePlanetParams|Apply manual target building overrides on a planet.|target_buildings~object~No~`None`~—;slot~int | None~No~`None`~min=1, max=100;planet_id~int | None~No~`None`~min=-1000, max=9223372036854775807;coords~object~No~`None`~—;fields_max_override~int | None~No~`None`~min=1, max=2000;locked_specialization~object~No~`None`~—;planet_name~object~No~`None`~—;lock_for_ga~object~No~`True`~—;auto_warehouses~object~No~`False`~—;auto_replan_ai~object~No~`True`~—
config_optimizer_yearly_projection|0|0|ConfigOptimizerYearlyProjectionParams|Compute multi-year ships/week ramp projection.|horizon_years~object~No~`5`~—
db_summary|0|0|DbSummaryParams|Return database row counts, table tiers, and recent action/intel summaries.|
db_verify|0|0|DbVerifyParams|Verify database schema migrations and optional full integrity.|full_integrity~bool~No~`False`~—
demolish_building|1|1|DemolishBuildingParams|Safely demolish one level of a building to reclaim planet fields.|building_id~object~No~`6`~—;planet_id~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
discard_colony_candidate|0|1|DiscardColonyCandidateParams|Mark a colony candidate coordinate as discarded.|target_coords~object~No~`None`~—;coords~object~No~`None`~—
ensure_session|1|0|EnsureSessionParams|Verify Chrome debug port and logged-in SpaceInvasion game session.|
escape_flight|1|1|EscapeFlightParams|Dispatch an invisible Escape Flight to save fleet and resources.|planet_id~object~No~`None`~—;target_coords~object~No~`None`~—;target_name~object~No~`None`~—;reason~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
espionage_intel|0|0|EspionageIntelParams|List enriched espionage targets and reports.|limit~int~No~`150`~min=1, max=1000;coords~object~No~`None`~—
fleet_planner_get|0|0|FleetPlannerGetParams|Evaluate Fleet & Attack Planner graph and spare ship budget.|config~object~No~`None`~—
fleetsave_plan|1|0|FleetsavePlanParams|Compute an Escape Flight packing plan for a planet.|planet_id~object~No~`None`~—;fuel_reserve_spice~object~No~`None`~—;target_coords~object~No~`None`~—;refresh~bool~No~`False`~—
galaxy_sweep|0|1|GalaxySweepParams|Start, stop, reset, inspect, or dry-run the background galaxy sweep.|action~str | None~No~`None`~—;pages~int | None~No~`None`~min=1, max=400;galaxy~int | None~No~`None`~min=1, max=14;start_galaxy~int | None~No~`None`~min=1, max=14;start_system~int | None~No~`None`~min=1, max=400;system~int | None~No~`None`~min=1, max=400;delay_min_ms~int | None~No~`None`~min=0, max=60000;delay_max_ms~int | None~No~`None`~min=0, max=60000;dry_run~bool~No~`False`~—
navigate|1|1|NavigateParams|Navigate to an allowed in-game menu page and harvest intel side effects.|action~object~No~`None`~—;galaxy~object~No~`None`~—;system~object~No~`None`~—;message_type~object~No~`None`~—;building_detail_id~object~No~`None`~—;include_html~bool~No~`False`~—;return_home~bool~No~`False`~—
notification_ack|0|1|NotificationAckParams|Acknowledge, dismiss, or re-mark a single operator notification.|notification_id~int | str | None~No~`None`~—;notification_code~str | None~No~`None`~—;status~str | None~No~`'read'`~—
notification_get|0|0|NotificationGetParams|Fetch a single operator notification by numeric ID or NTF-XXXX code.|notification_id~int | str | None~No~`None`~—;notification_code~str | None~No~`None`~—;id~int | str | None~No~`None`~—
notifications_ack_all|0|1|NotificationsAckAllParams|Mark all active operator notifications as read or dismissed.|status~str | None~No~`'read'`~—
notifications_list|0|0|NotificationsListParams|List persisted operator notifications and current unread count.|limit~int~No~`50`~min=1, max=200;status~str | None~No~`'all'`~—
plan|1|0|PlanParams|Compute prioritized next actions for the active planner mode.|planner_mode~object~No~`None`~—;refresh~object~No~`None`~—;dry_run~bool~No~`False`~—
planner_jobs_status|0|0|PlannerJobsStatusParams|Inspect planner background jobs and field conflicts.|
queue_add|0|1|QueueAddParams|Enqueue a manual command into the persistent priority queue.|kind~object~No~`None`~—;priority~object~No~`'P1'`~—;position~object~No~`'top'`~—;planet_id~int | None~No~`0`~min=0, max=9223372036854775807;target_id~int | None~No~`None`~min=1, max=2147483647;target_level~object~No~`None`~—;quantity~object~No~`None`~—;percentage~object~No~`None`~—;target_name~object~No~`''`~—;target_coords~object~No~`''`~—;note~object~No~`''`~—;deduplicate_pending~bool~No~`False`~—
queue_cancel|0|1|QueueCancelParams|Cancel a pending manual queue item.|queue_id~object~No~`None`~—
queue_clear|0|1|QueueClearParams|Clear all pending commands from the manual queue.|
queue_update|0|1|QueueUpdateParams|Update priority, ordering, target level, or note on a pending queue item.|queue_id~object~No~`None`~—;priority~object~No~`None`~—;move~object~No~`None`~—;sort_rank~int | None~No~`None`~min=-1000000, max=1000000;target_level~object~No~`UNSET`~—;note~object~No~`UNSET`~—
recycle|1|1|RecycleParams|Dispatch recyclers to harvest a rubble field.|target_coords~object~No~`None`~—;quantity~object~No~`None`~—;planet_id~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
recycler_results|0|0|RecyclerResultsParams|Return recycle reports, active debris targets, and inbox messages.|limit~int~No~`150`~min=1, max=1000;category~object~No~`None`~—
rename_planet|0|1|RenamePlanetParams|Set or clear custom display names for one or more owned planets.|planet_id~object~No~`None`~—;name~object~No~`''`~—;planet_names~object~No~`None`~—
research|1|1|ResearchParams|Start a research upgrade on the specified planet.|research_id~object~No~`None`~—;planet_id~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
resolve_field_conflict|0|1|ResolveFieldConflictParams|Resolve a pending colony field-capacity conflict.|conflict_key~object~No~`None`~—;decision~object~No~`None`~—;action~object~No~`None`~—;sync~object~No~`False`~—;candidate_building_key~object~No~`None`~—;building_key~object~No~`None`~—
scan_galaxy|1|1|ScanGalaxyParams|Scan a single galaxy sector and persist slots and debris fields.|planet_id~object~No~`None`~—;galaxy~object~No~`None`~—;system~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
scanned_sectors|0|0|ScannedSectorsParams|List scanned sectors, galaxy stats, and smart sweep queue.|galaxy~int | None~No~`None`~min=0, max=14;include_unscanned~object~No~`None`~—;limit~int~No~`5600`~min=1, max=5600;planet_id~int | None~No~`None`~min=1, max=9223372036854775807;preview_refresh_pct~int | None~No~`None`~min=0, max=100;preview_sweep_pages~int | None~No~`None`~min=1, max=400;preview_all_galaxies~object~No~`None`~—
simulate_combat|0|0|SimulateCombatParams|Run the 1:1 zero-rapidfire combat simulator.|attacker_ships~object~No~`None`~—;defender_ships~object~No~`None`~—;defender_defense~object~No~`None`~—;defender_researches~object~No~`None`~—;target_resources~object~No~`None`~—;target_coords~object~No~`None`~—;safety_margin~object~No~`UNSET`~—
spy|1|1|SpyParams|Dispatch spy probe(s) to target coordinates.|target_coords~object~No~`None`~—;quantity~object~No~`1`~—;planet_id~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
station|1|1|StationParams|Rebase ships permanently to another owned non-raid planet.|target_coords~object~No~`None`~—;dest_planet_id~object~No~`None`~—;ships~object~No~`None`~—;ships_spec~object~No~`None`~—;planet_id~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
sync_economy|1|1|SyncEconomyParams|Read live internalEconomy production percentages for a planet.|planet_id~object~No~`None`~—;refresh~bool~No~`False`~—
sync_messages|1|1|SyncMessagesParams|Ingest spy, battle, fleet, and inbox messages into PostgreSQL and clean processed pages.|
sync_planet_names|1|1|SyncPlanetNamesParams|Sync custom planet display names to SpaceInvasion internalSettings.|planet_names~object~No~`None`~—;planet_id~object~No~`None`~—;name~object~No~`None`~—;force~bool~No~`False`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
throttle_building|1|1|ThrottleBuildingParams|Adjust a mine or power plant production percentage (0..100 in steps of 10).|building_id~object~No~`2`~—;percentage~object~No~`None`~—;planet_id~object~No~`None`~—;reason~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
tick_once|1|1|TickOnceParams|Refresh live state and execute the highest-priority ready action once.|dry_run~bool~No~`False`~—
transport|1|1|TransportParams|Transport resources between own planets.|target_coords~object~No~`None`~—;dest_planet_id~object~No~`None`~—;resources~object~No~`None`~—;planet_id~object~No~`None`~—;dry_run~bool~No~`False`~—;refresh~bool~No~`False`~—
universe_research_max|0|0|UniverseResearchMaxParams|Return universe-wide max research levels.|sync~bool~No~`False`~—;dry_run~bool~No~`False`~—
update_config|0|1|UpdateConfigParams|Validate, persist, and propagate a StrategyConfig patch.|config~object~No~`None`~—;include_action_graph~object~No~`None`~—;planet_id~int | None~No~`None`~min=1, max=9223372036854775807
""".strip()


def _parse_fallback_specs() -> list[tuple[str, bool, bool, str, str, str]]:
    specs: list[tuple[str, bool, bool, str, str, str]] = []
    for raw_line in _FALLBACK_COMMAND_ROWS.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        name, br, mut, params_cls, summary, params_spec = line.split("|", 5)
        specs.append((name, br == "1", mut == "1", params_cls, summary, params_spec))
    return specs


def _render_fallback_api_commands_markdown() -> str:
    """Render deterministic 60-command API reference identical to `generate_command_reference_markdown`."""
    specs = _parse_fallback_specs()
    lines: list[str] = [
        "# SpaceInvasion Game API Command Reference",
        "",
        "Generated automatically from `CommandRegistry`.",
        "",
        "| Command | Browser Lock | Mutates | Parameters | Summary |",
        "| --- | :---: | :---: | --- | --- |",
    ]
    for name, needs_browser, mutates, params_cls, summary, _params_spec in specs:
        browser_txt = "Yes" if needs_browser else "No"
        mutates_txt = "Yes" if mutates else "No"
        summary_txt = summary.replace("|", "\\|") if summary else "—"
        lines.append(f"| `{name}` | {browser_txt} | {mutates_txt} | `{params_cls}` | {summary_txt} |")

    for name, _needs_browser, _mutates, _params_cls, summary, params_spec in specs:
        lines.extend(
            [
                "",
                f"## `{name}`",
                "",
                summary or "No summary provided.",
                "",
                "| Parameter | Type | Required | Default | Constraints |",
                "| --- | --- | :---: | --- | --- |",
            ]
        )
        for entry in (item for item in params_spec.split(";") if item):
            fname, f_type, required_txt, default_txt, constraints = entry.split("~", 4)
            lines.append(f"| `{fname}` | `{f_type}` | {required_txt} | {default_txt} | {constraints} |")
    return "\n".join(lines) + "\n"


def render_api_commands_markdown() -> str:
    """Render `docs/api-commands.md` from live `build_command_registry()` or the canonical fallback."""
    try:
        handlers_mod = importlib.import_module("backend.runtime.api.handlers")
    except ModuleNotFoundError:
        return _render_fallback_api_commands_markdown()
    builder = handlers_mod.__dict__.get("build_command_registry")
    if callable(builder):
        registry = builder()
        if isinstance(registry, CommandRegistry):
            return generate_command_reference_markdown(registry)
    return _render_fallback_api_commands_markdown()


def render_all_docs() -> dict[Path, str]:
    """Return mapping of generated documentation file paths to deterministic Markdown contents."""
    return {
        SETTINGS_DOC_PATH: generate_settings_reference_markdown(),
        API_COMMANDS_DOC_PATH: render_api_commands_markdown(),
    }


def _sync_docs(*, write: bool) -> int:
    rendered = render_all_docs()
    drifted: list[Path] = []
    for path, expected in rendered.items():
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        if current != expected:
            drifted.append(path)
            if write:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(expected, encoding="utf-8")
    if write:
        sys.stdout.write(f"[OK] Wrote {len(rendered)} generated docs ({len(drifted)} updated).\n")
        return 0
    if drifted:
        rels = ", ".join(str(p.relative_to(REPO_ROOT)) for p in drifted)
        sys.stderr.write(f"[DRIFT] Generated docs out of date: {rels}. Run scripts/generate_docs.py --write\n")
        return 1
    sys.stdout.write(f"[OK] All {len(rendered)} generated docs are up to date.\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint supporting `--write` and `--check` modes."""
    parser = argparse.ArgumentParser(description="Generate or verify SpaceInvasion Markdown reference docs.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="Write generated reference docs to docs/.")
    mode.add_argument("--check", action="store_true", help="Verify docs/ matches generated output without modifying.")
    args = parser.parse_args(list(argv) if argv is not None else None)
    return _sync_docs(write=bool(args.write))


if __name__ == "__main__":
    raise SystemExit(main())
