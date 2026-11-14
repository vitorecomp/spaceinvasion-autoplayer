# SpaceInvasion Game API Command Reference

Generated automatically from `CommandRegistry`.

| Command | Browser Lock | Mutates | Parameters | Summary |
| --- | :---: | :---: | --- | --- |
| `abandon_colony` | Yes | Yes | `AbandonColonyParams` | Safely delete an undersized newly settled colony. |
| `action_graph` | No | No | `ActionGraphParams` | Build the Kahn dependency DAG for current or simulated strategy settings. |
| `activate_transmitter` | Yes | Yes | `ActivateTransmitterParams` | Collect daily login bonus and activate the free Interdimensional Transmitter spin. |
| `ai_planner_get` | No | No | `AiPlannerGetParams` | Read active AI-Planner graph, steps, and adaptive status. |
| `ai_planner_run` | No | Yes | `AiPlannerRunParams` | Start or synchronously execute an AI-Planner run. |
| `attack` | Yes | Yes | `AttackParams` | Dispatch an attack fleet after protection, bash, and 1:1 combat safety checks. |
| `attack_results` | No | No | `AttackResultsParams` | Return battle reports, raid stats, and active target blocks. |
| `bot_control` | No | Yes | `BotControlParams` | Pause/resume automation, clear CAPTCHA gates on resume, or update mode_override. |
| `build` | Yes | Yes | `BuildParams` | Queue or start a building upgrade on the specified planet. |
| `build_ship` | Yes | Yes | `BuildShipParams` | Commission ships in the planet's Weapons Factory. |
| `buildings_eta` | No | No | `PlanetViewParams` | Compute building upgrade ETAs and Config Optimizer goals for a planet. |
| `claim_tutorial` | Yes | Yes | `ClaimTutorialParams` | Claim a completed SpaceInvasion tutorial step reward. |
| `colonize` | Yes | Yes | `ColonizeParams` | Send a colony ship to settle target coordinates. |
| `colony_candidates` | No | No | `ColonyCandidatesParams` | List graded uninhabited colony candidates and colonization attempt stats. |
| `combat_overview` | No | No | `PlanetViewParams` | Summarize planet fleet/defense combat strength and raid targets. |
| `config` | No | No | `ConfigParams` | Return the active StrategyConfig snapshot. |
| `config_optimizer_get` | No | No | `ConfigOptimizerGetParams` | Read active Config Optimizer result and run history. |
| `config_optimizer_run` | No | Yes | `ConfigOptimizerRunParams` | Start or synchronously execute a Config Optimizer run. |
| `config_optimizer_set_specializations` | No | Yes | `ConfigOptimizerSetSpecializationsParams` | Update or replace per-planet Config Optimizer specializations. |
| `config_optimizer_update_planet` | No | Yes | `ConfigOptimizerUpdatePlanetParams` | Apply manual target building overrides on a planet. |
| `config_optimizer_yearly_projection` | No | No | `ConfigOptimizerYearlyProjectionParams` | Compute multi-year ships/week ramp projection. |
| `db_summary` | No | No | `DbSummaryParams` | Return database row counts, table tiers, and recent action/intel summaries. |
| `db_verify` | No | No | `DbVerifyParams` | Verify database schema migrations and optional full integrity. |
| `demolish_building` | Yes | Yes | `DemolishBuildingParams` | Safely demolish one level of a building to reclaim planet fields. |
| `discard_colony_candidate` | No | Yes | `DiscardColonyCandidateParams` | Mark a colony candidate coordinate as discarded. |
| `ensure_session` | Yes | No | `EnsureSessionParams` | Verify Chrome debug port and logged-in SpaceInvasion game session. |
| `escape_flight` | Yes | Yes | `EscapeFlightParams` | Dispatch an invisible Escape Flight to save fleet and resources. |
| `espionage_intel` | No | No | `EspionageIntelParams` | List enriched espionage targets and reports. |
| `fleet_planner_get` | No | No | `FleetPlannerGetParams` | Evaluate Fleet & Attack Planner graph and spare ship budget. |
| `fleetsave_plan` | Yes | No | `FleetsavePlanParams` | Compute an Escape Flight packing plan for a planet. |
| `galaxy_sweep` | No | Yes | `GalaxySweepParams` | Start, stop, reset, inspect, or dry-run the background galaxy sweep. |
| `navigate` | Yes | Yes | `NavigateParams` | Navigate to an allowed in-game menu page and harvest intel side effects. |
| `notification_ack` | No | Yes | `NotificationAckParams` | Acknowledge, dismiss, or re-mark a single operator notification. |
| `notification_get` | No | No | `NotificationGetParams` | Fetch a single operator notification by numeric ID or NTF-XXXX code. |
| `notifications_ack_all` | No | Yes | `NotificationsAckAllParams` | Mark all active operator notifications as read or dismissed. |
| `notifications_list` | No | No | `NotificationsListParams` | List persisted operator notifications and current unread count. |
| `plan` | Yes | No | `PlanParams` | Compute prioritized next actions for the active planner mode. |
| `planner_jobs_status` | No | No | `PlannerJobsStatusParams` | Inspect planner background jobs and field conflicts. |
| `queue_add` | No | Yes | `QueueAddParams` | Enqueue a manual command into the persistent priority queue. |
| `queue_cancel` | No | Yes | `QueueCancelParams` | Cancel a pending manual queue item. |
| `queue_clear` | No | Yes | `QueueClearParams` | Clear all pending commands from the manual queue. |
| `queue_update` | No | Yes | `QueueUpdateParams` | Update priority, ordering, target level, or note on a pending queue item. |
| `recycle` | Yes | Yes | `RecycleParams` | Dispatch recyclers to harvest a rubble field. |
| `recycler_results` | No | No | `RecyclerResultsParams` | Return recycle reports, active debris targets, and inbox messages. |
| `rename_planet` | No | Yes | `RenamePlanetParams` | Set or clear custom display names for one or more owned planets. |
| `research` | Yes | Yes | `ResearchParams` | Start a research upgrade on the specified planet. |
| `resolve_field_conflict` | No | Yes | `ResolveFieldConflictParams` | Resolve a pending colony field-capacity conflict. |
| `scan_galaxy` | Yes | Yes | `ScanGalaxyParams` | Scan a single galaxy sector and persist slots and debris fields. |
| `scanned_sectors` | No | No | `ScannedSectorsParams` | List scanned sectors, galaxy stats, and smart sweep queue. |
| `simulate_combat` | No | No | `SimulateCombatParams` | Run the 1:1 zero-rapidfire combat simulator. |
| `spy` | Yes | Yes | `SpyParams` | Dispatch spy probe(s) to target coordinates. |
| `station` | Yes | Yes | `StationParams` | Rebase ships permanently to another owned non-raid planet. |
| `sync_economy` | Yes | Yes | `SyncEconomyParams` | Read live internalEconomy production percentages for a planet. |
| `sync_messages` | Yes | Yes | `SyncMessagesParams` | Ingest spy, battle, fleet, and inbox messages into PostgreSQL and clean processed pages. |
| `sync_planet_names` | Yes | Yes | `SyncPlanetNamesParams` | Sync custom planet display names to SpaceInvasion internalSettings. |
| `throttle_building` | Yes | Yes | `ThrottleBuildingParams` | Adjust a mine or power plant production percentage (0..100 in steps of 10). |
| `tick_once` | Yes | Yes | `TickOnceParams` | Refresh live state and execute the highest-priority ready action once. |
| `transport` | Yes | Yes | `TransportParams` | Transport resources between own planets. |
| `universe_research_max` | No | No | `UniverseResearchMaxParams` | Return universe-wide max research levels. |
| `update_config` | No | Yes | `UpdateConfigParams` | Validate, persist, and propagate a StrategyConfig patch. |

## `abandon_colony`

Safely delete an undersized newly settled colony.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `planet_id` | `object` | No | `None` | — |
| `target_coords` | `object` | No | `None` | — |
| `min_colony_fields` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `action_graph`

Build the Kahn dependency DAG for current or simulated strategy settings.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `config` | `object` | No | `None` | — |
| `planner_mode` | `object` | No | `None` | — |
| `simulated` | `object` | No | `None` | — |
| `planet_id` | `object` | No | `None` | — |
| `refresh` | `object` | No | `False` | — |

## `activate_transmitter`

Collect daily login bonus and activate the free Interdimensional Transmitter spin.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `planet_id` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `ai_planner_get`

Read active AI-Planner graph, steps, and adaptive status.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `window_size` | `int` | No | `40` | min=5, max=200 |
| `week` | `int | None` | No | `None` | min=1, max=520 |
| `planet_id` | `object` | No | `None` | — |
| `page` | `int` | No | `1` | min=1, max=100000 |
| `page_size` | `int` | No | `50` | min=5, max=500 |
| `search` | `object` | No | `''` | — |
| `limit` | `int` | No | `20` | min=1, max=200 |

## `ai_planner_run`

Start or synchronously execute an AI-Planner run.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `params` | `object` | No | `None` | — |
| `sync` | `object` | No | `False` | — |
| `refresh` | `object` | No | `False` | — |
| `auto_run_config_if_missing` | `object` | No | `True` | — |
| `trigger_source` | `object` | No | `'manual'` | — |

## `attack`

Dispatch an attack fleet after protection, bash, and 1:1 combat safety checks.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `target_coords` | `object` | No | `None` | — |
| `planet_id` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `attack_results`

Return battle reports, raid stats, and active target blocks.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `limit` | `int` | No | `150` | min=1, max=1000 |
| `coords` | `object` | No | `None` | — |

## `bot_control`

Pause/resume automation, clear CAPTCHA gates on resume, or update mode_override.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `paused` | `bool | _UnsetType` | No | `UNSET` | — |
| `action` | `str | None` | No | `None` | — |
| `mode_override` | `str | None | _UnsetType` | No | `UNSET` | — |

## `build`

Queue or start a building upgrade on the specified planet.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `building_id` | `object` | No | `None` | — |
| `planet_id` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `build_ship`

Commission ships in the planet's Weapons Factory.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `ship_id` | `object` | No | `None` | — |
| `quantity` | `object` | No | `1` | — |
| `planet_id` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `buildings_eta`

Compute building upgrade ETAs and Config Optimizer goals for a planet.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `planet_id` | `object` | No | `None` | — |
| `refresh` | `bool` | No | `False` | — |

## `claim_tutorial`

Claim a completed SpaceInvasion tutorial step reward.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `colonize`

Send a colony ship to settle target coordinates.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `target_coords` | `object` | No | `None` | — |
| `planet_id` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `colony_candidates`

List graded uninhabited colony candidates and colonization attempt stats.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `limit` | `int` | No | `200` | min=1, max=1000 |
| `include_discarded` | `bool` | No | `False` | — |
| `min_fields` | `int | None` | No | `None` | min=50, max=600 |

## `combat_overview`

Summarize planet fleet/defense combat strength and raid targets.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `planet_id` | `object` | No | `None` | — |
| `refresh` | `bool` | No | `False` | — |

## `config`

Return the active StrategyConfig snapshot.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |

## `config_optimizer_get`

Read active Config Optimizer result and run history.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `limit` | `int` | No | `20` | min=1, max=200 |

## `config_optimizer_run`

Start or synchronously execute a Config Optimizer run.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `params` | `object` | No | `None` | — |
| `auto_replan_ai` | `object` | No | `True` | — |
| `sync` | `object` | No | `False` | — |
| `trigger_source` | `object` | No | `'manual'` | — |

## `config_optimizer_set_specializations`

Update or replace per-planet Config Optimizer specializations.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `planet_specializations` | `object` | No | `None` | — |
| `replace` | `object` | No | `False` | — |

## `config_optimizer_update_planet`

Apply manual target building overrides on a planet.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `target_buildings` | `object` | No | `None` | — |
| `slot` | `int | None` | No | `None` | min=1, max=100 |
| `planet_id` | `int | None` | No | `None` | min=-1000, max=9223372036854775807 |
| `coords` | `object` | No | `None` | — |
| `fields_max_override` | `int | None` | No | `None` | min=1, max=2000 |
| `locked_specialization` | `object` | No | `None` | — |
| `planet_name` | `object` | No | `None` | — |
| `lock_for_ga` | `object` | No | `True` | — |
| `auto_warehouses` | `object` | No | `False` | — |
| `auto_replan_ai` | `object` | No | `True` | — |

## `config_optimizer_yearly_projection`

Compute multi-year ships/week ramp projection.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `horizon_years` | `object` | No | `5` | — |

## `db_summary`

Return database row counts, table tiers, and recent action/intel summaries.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |

## `db_verify`

Verify database schema migrations and optional full integrity.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `full_integrity` | `bool` | No | `False` | — |

## `demolish_building`

Safely demolish one level of a building to reclaim planet fields.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `building_id` | `object` | No | `6` | — |
| `planet_id` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `discard_colony_candidate`

Mark a colony candidate coordinate as discarded.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `target_coords` | `object` | No | `None` | — |
| `coords` | `object` | No | `None` | — |

## `ensure_session`

Verify Chrome debug port and logged-in SpaceInvasion game session.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |

## `escape_flight`

Dispatch an invisible Escape Flight to save fleet and resources.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `planet_id` | `object` | No | `None` | — |
| `target_coords` | `object` | No | `None` | — |
| `target_name` | `object` | No | `None` | — |
| `reason` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `espionage_intel`

List enriched espionage targets and reports.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `limit` | `int` | No | `150` | min=1, max=1000 |
| `coords` | `object` | No | `None` | — |

## `fleet_planner_get`

Evaluate Fleet & Attack Planner graph and spare ship budget.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `config` | `object` | No | `None` | — |

## `fleetsave_plan`

Compute an Escape Flight packing plan for a planet.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `planet_id` | `object` | No | `None` | — |
| `fuel_reserve_spice` | `object` | No | `None` | — |
| `target_coords` | `object` | No | `None` | — |
| `refresh` | `bool` | No | `False` | — |

## `galaxy_sweep`

Start, stop, reset, inspect, or dry-run the background galaxy sweep.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `action` | `str | None` | No | `None` | — |
| `pages` | `int | None` | No | `None` | min=1, max=400 |
| `galaxy` | `int | None` | No | `None` | min=1, max=14 |
| `start_galaxy` | `int | None` | No | `None` | min=1, max=14 |
| `start_system` | `int | None` | No | `None` | min=1, max=400 |
| `system` | `int | None` | No | `None` | min=1, max=400 |
| `delay_min_ms` | `int | None` | No | `None` | min=0, max=60000 |
| `delay_max_ms` | `int | None` | No | `None` | min=0, max=60000 |
| `dry_run` | `bool` | No | `False` | — |

## `navigate`

Navigate to an allowed in-game menu page and harvest intel side effects.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `action` | `object` | No | `None` | — |
| `galaxy` | `object` | No | `None` | — |
| `system` | `object` | No | `None` | — |
| `message_type` | `object` | No | `None` | — |
| `building_detail_id` | `object` | No | `None` | — |
| `include_html` | `bool` | No | `False` | — |
| `return_home` | `bool` | No | `False` | — |

## `notification_ack`

Acknowledge, dismiss, or re-mark a single operator notification.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `notification_id` | `int | str | None` | No | `None` | — |
| `notification_code` | `str | None` | No | `None` | — |
| `status` | `str | None` | No | `'read'` | — |

## `notification_get`

Fetch a single operator notification by numeric ID or NTF-XXXX code.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `notification_id` | `int | str | None` | No | `None` | — |
| `notification_code` | `str | None` | No | `None` | — |
| `id` | `int | str | None` | No | `None` | — |

## `notifications_ack_all`

Mark all active operator notifications as read or dismissed.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `status` | `str | None` | No | `'read'` | — |

## `notifications_list`

List persisted operator notifications and current unread count.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `limit` | `int` | No | `50` | min=1, max=200 |
| `status` | `str | None` | No | `'all'` | — |

## `plan`

Compute prioritized next actions for the active planner mode.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `planner_mode` | `object` | No | `None` | — |
| `refresh` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |

## `planner_jobs_status`

Inspect planner background jobs and field conflicts.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |

## `queue_add`

Enqueue a manual command into the persistent priority queue.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `kind` | `object` | No | `None` | — |
| `priority` | `object` | No | `'P1'` | — |
| `position` | `object` | No | `'top'` | — |
| `planet_id` | `int | None` | No | `0` | min=0, max=9223372036854775807 |
| `target_id` | `int | None` | No | `None` | min=1, max=2147483647 |
| `target_level` | `object` | No | `None` | — |
| `quantity` | `object` | No | `None` | — |
| `percentage` | `object` | No | `None` | — |
| `target_name` | `object` | No | `''` | — |
| `target_coords` | `object` | No | `''` | — |
| `note` | `object` | No | `''` | — |
| `deduplicate_pending` | `bool` | No | `False` | — |

## `queue_cancel`

Cancel a pending manual queue item.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `queue_id` | `object` | No | `None` | — |

## `queue_clear`

Clear all pending commands from the manual queue.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |

## `queue_update`

Update priority, ordering, target level, or note on a pending queue item.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `queue_id` | `object` | No | `None` | — |
| `priority` | `object` | No | `None` | — |
| `move` | `object` | No | `None` | — |
| `sort_rank` | `int | None` | No | `None` | min=-1000000, max=1000000 |
| `target_level` | `object` | No | `UNSET` | — |
| `note` | `object` | No | `UNSET` | — |

## `recycle`

Dispatch recyclers to harvest a rubble field.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `target_coords` | `object` | No | `None` | — |
| `quantity` | `object` | No | `None` | — |
| `planet_id` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `recycler_results`

Return recycle reports, active debris targets, and inbox messages.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `limit` | `int` | No | `150` | min=1, max=1000 |
| `category` | `object` | No | `None` | — |

## `rename_planet`

Set or clear custom display names for one or more owned planets.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `planet_id` | `object` | No | `None` | — |
| `name` | `object` | No | `''` | — |
| `planet_names` | `object` | No | `None` | — |

## `research`

Start a research upgrade on the specified planet.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `research_id` | `object` | No | `None` | — |
| `planet_id` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `resolve_field_conflict`

Resolve a pending colony field-capacity conflict.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `conflict_key` | `object` | No | `None` | — |
| `decision` | `object` | No | `None` | — |
| `action` | `object` | No | `None` | — |
| `sync` | `object` | No | `False` | — |
| `candidate_building_key` | `object` | No | `None` | — |
| `building_key` | `object` | No | `None` | — |

## `scan_galaxy`

Scan a single galaxy sector and persist slots and debris fields.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `planet_id` | `object` | No | `None` | — |
| `galaxy` | `object` | No | `None` | — |
| `system` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `scanned_sectors`

List scanned sectors, galaxy stats, and smart sweep queue.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `galaxy` | `int | None` | No | `None` | min=0, max=14 |
| `include_unscanned` | `object` | No | `None` | — |
| `limit` | `int` | No | `5600` | min=1, max=5600 |
| `planet_id` | `int | None` | No | `None` | min=1, max=9223372036854775807 |
| `preview_refresh_pct` | `int | None` | No | `None` | min=0, max=100 |
| `preview_sweep_pages` | `int | None` | No | `None` | min=1, max=400 |
| `preview_all_galaxies` | `object` | No | `None` | — |

## `simulate_combat`

Run the 1:1 zero-rapidfire combat simulator.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `attacker_ships` | `object` | No | `None` | — |
| `defender_ships` | `object` | No | `None` | — |
| `defender_defense` | `object` | No | `None` | — |
| `defender_researches` | `object` | No | `None` | — |
| `target_resources` | `object` | No | `None` | — |
| `target_coords` | `object` | No | `None` | — |
| `safety_margin` | `object` | No | `UNSET` | — |

## `spy`

Dispatch spy probe(s) to target coordinates.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `target_coords` | `object` | No | `None` | — |
| `quantity` | `object` | No | `1` | — |
| `planet_id` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `station`

Rebase ships permanently to another owned non-raid planet.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `target_coords` | `object` | No | `None` | — |
| `dest_planet_id` | `object` | No | `None` | — |
| `ships` | `object` | No | `None` | — |
| `ships_spec` | `object` | No | `None` | — |
| `planet_id` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `sync_economy`

Read live internalEconomy production percentages for a planet.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `planet_id` | `object` | No | `None` | — |
| `refresh` | `bool` | No | `False` | — |

## `sync_messages`

Ingest spy, battle, fleet, and inbox messages into PostgreSQL and clean processed pages.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |

## `sync_planet_names`

Sync custom planet display names to SpaceInvasion internalSettings.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `planet_names` | `object` | No | `None` | — |
| `planet_id` | `object` | No | `None` | — |
| `name` | `object` | No | `None` | — |
| `force` | `bool` | No | `False` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `throttle_building`

Adjust a mine or power plant production percentage (0..100 in steps of 10).

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `building_id` | `object` | No | `2` | — |
| `percentage` | `object` | No | `None` | — |
| `planet_id` | `object` | No | `None` | — |
| `reason` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `tick_once`

Refresh live state and execute the highest-priority ready action once.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `dry_run` | `bool` | No | `False` | — |

## `transport`

Transport resources between own planets.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `target_coords` | `object` | No | `None` | — |
| `dest_planet_id` | `object` | No | `None` | — |
| `resources` | `object` | No | `None` | — |
| `planet_id` | `object` | No | `None` | — |
| `dry_run` | `bool` | No | `False` | — |
| `refresh` | `bool` | No | `False` | — |

## `universe_research_max`

Return universe-wide max research levels.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `sync` | `bool` | No | `False` | — |
| `dry_run` | `bool` | No | `False` | — |

## `update_config`

Validate, persist, and propagate a StrategyConfig patch.

| Parameter | Type | Required | Default | Constraints |
| --- | --- | :---: | --- | --- |
| `config` | `object` | No | `None` | — |
| `include_action_graph` | `object` | No | `None` | — |
| `planet_id` | `int | None` | No | `None` | min=1, max=9223372036854775807 |
