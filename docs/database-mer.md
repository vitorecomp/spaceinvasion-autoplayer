# Database Entity-Relationship Model (MER / DER)

Complete data model and schema documentation for `state/spaceinvasion.db` (`spaceinvasion/db/storage.py` & `spaceinvasion/db/migrations.py`, schema **`V001..V012`**, **32 tables**: **15 Hot Operational Tables** + **17 Append-Only History Tables**).

---

## 1. Storage Architecture & Core Design Rules

1. **SQLite WAL Mode + Thread Lock**: `GameDatabase` (`spaceinvasion/db/storage.py`) opens `state/spaceinvasion.db` with `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL`, and `PRAGMA foreign_keys=ON`, guarded by an internal `threading.RLock` for thread-safe concurrent access inside `GameApiBridge`.
2. **Hot vs. History Table Division (`V012`)**: Every one of the **32 required tables** belongs to an explicit disjoint partition exported by `spaceinvasion/db/migrations.py` (`HOT_TABLES`, `HISTORY_TABLES`, `TABLE_TIER_BY_NAME`) and reported in `GameDatabase.summary()["table_tiers"]`, `MigrationRunner.inspect()["table_tiers"]`, and `scripts/db_migrate.py status`:
   - **Hot Operational Tables (15)** — compact current-state & pre-aggregated projections powering real-time UI/API/commander reads in milliseconds (`latest_state_hot`, `empire_planets_hot`, `galaxy_sector_hot`, `colony_candidates_hot`, `meta`, `strategy_config_store`, `economy_rates_cache`, `tutorial_progress`, `spy_targets`, `galaxy_scans`, `galaxy_slots`, `ui_notifications`, `universe_research_max`, `manual_queue`, `schema_migrations`).
   - **Append-Only History Tables (17)** — immutable longitudinal ledgers that **never delete rows**, preserving full historical universe telemetry for offline analytics, audit trails, and GA+NN experimentation (`rank_snapshots`, `planet_snapshots`, `state_snapshots`, `action_history`, `config_history`, `spy_reports`, `galaxy_scan_history`, `galaxy_slot_history`, `raid_history`, `bash_blocks`, `game_messages`, `battle_reports`, `recycle_reports`, `manual_queue_events`, `universe_research_max_history`, `colony_attempts`, `schema_migration_events`).
3. **Soft Flags & Monotonic Upserts Over Destructive Deletes**: Current-state and hot tables (`spy_targets`, `manual_queue`, `game_messages`, `ui_notifications`, `universe_research_max`, `empire_planets_hot`, `colony_candidates_hot`) use non-destructive lifecycle columns (`is_active`, `is_discarded`, `is_own_planet`, `merged_into`, `status`, `completed_ts`, `canceled_ts`, `deleted_in_game`, `deleted_ts`, `acknowledged_ts`) and monotonic `ON CONFLICT DO UPDATE` upserts rather than `DELETE`.
4. **Automatic Secret Redaction**: Every text/JSON write passes through `redact_sensitive` and finite-number coercion so `sid=` session tokens, `NaN`, and `Infinity` never enter SQLite.

---

## 2. Entity-Relationship Diagram (MER)

> **Relationship Convention**: SQLite coordinates (`coords` / `target_coords` formatted as `GG:SSS:PP`), `planet_id`, `(galaxy, system)`, `research_key`, `msg_uid`, and `queue_id` form the logical entity relationships across the **32 SQLite tables** (`V001..V012`: **15 Hot Operational** + **17 Append-Only History**).

```mermaid
erDiagram
    %% Domain 1: Empire Telemetry & Configuration
    META {
        TEXT key PK
        TEXT value
        REAL updated_ts
    }
    CONFIG_HISTORY {
        INTEGER id PK
        REAL ts
        TEXT config_json
    }
    STRATEGY_CONFIG_STORE {
        INTEGER id PK
        TEXT config_json
        REAL updated_ts
        TEXT source
    }
    STATE_SNAPSHOTS {
        INTEGER id PK
        REAL ts
        TEXT source
        INTEGER universe_id
        INTEGER user_id
        TEXT nickname
        INTEGER active_planet_id
        INTEGER planet_count
        INTEGER planned_action_count
        TEXT state_json
        TEXT plan_json
    }
    UNIVERSE_RESEARCH_MAX {
        TEXT research_key PK
        INTEGER research_id
        TEXT slug
        TEXT name
        INTEGER own_level
        INTEGER spied_max_level
        INTEGER server_record_level
        INTEGER universe_max_level
        INTEGER catalog_max_level
        TEXT holder
        TEXT holder_coords
        REAL updated_ts
    }
    UNIVERSE_RESEARCH_MAX_HISTORY {
        INTEGER id PK
        REAL ts
        TEXT source
        TEXT records_json
    }
    RANK_SNAPSHOTS {
        INTEGER id PK
        REAL ts
        INTEGER universe_id
        TEXT nickname
        INTEGER server_points
        INTEGER server_rank
        REAL estimated_points
        REAL pts_per_hr
        REAL spice_value
        INTEGER planets
    }
    PLANET_SNAPSHOTS {
        INTEGER id PK
        REAL ts
        INTEGER universe_id
        INTEGER planet_id
        TEXT coords
        TEXT name
        TEXT planet_type
        INTEGER temperature
        INTEGER fields_used
        INTEGER fields_max
        INTEGER pig_iron
        INTEGER metal
        INTEGER kryptonite
        INTEGER spice
        INTEGER energy_produced
        INTEGER energy_used
        TEXT buildings_json
        TEXT ships_json
        TEXT defense_json
        TEXT economy_rates_json
        TEXT researches_json
    }
    ECONOMY_RATES_CACHE {
        INTEGER planet_id PK
        TEXT coords
        TEXT rates_json
        REAL updated_ts
    }
    TUTORIAL_PROGRESS {
        INTEGER step PK
        INTEGER total_steps
        TEXT tasks_json
        TEXT reward_text
        INTEGER can_complete
        REAL first_seen_ts
        REAL last_seen_ts
        REAL completed_ts
    }

    %% Domain 2: Action Execution & Operator Manual Queue
    ACTION_HISTORY {
        INTEGER id PK
        REAL ts
        TEXT source
        TEXT command
        TEXT priority
        TEXT kind
        INTEGER planet_id
        TEXT planet_coords
        INTEGER target_id
        TEXT target_name
        INTEGER target_level
        INTEGER executed
        INTEGER dry_run
        TEXT reason
        TEXT message
    }
    MANUAL_QUEUE {
        INTEGER id PK
        REAL created_ts
        REAL updated_ts
        TEXT status
        TEXT priority
        INTEGER sort_rank
        TEXT kind
        INTEGER planet_id
        TEXT planet_coords
        INTEGER target_id
        TEXT target_name
        TEXT target_coords
        INTEGER target_level
        TEXT note
        REAL completed_ts
        REAL canceled_ts
        TEXT result_message
    }
    MANUAL_QUEUE_EVENTS {
        INTEGER id PK
        REAL ts
        INTEGER queue_id FK
        TEXT event_type
        TEXT details_json
    }
    UI_NOTIFICATIONS {
        INTEGER id PK
        REAL created_ts
        REAL updated_ts
        TEXT severity
        TEXT category
        TEXT title
        TEXT message
        TEXT help_prompt
        TEXT action_kind
        TEXT planet_coords
        TEXT target_name
        TEXT dedupe_key
        INTEGER occurrence_count
        TEXT status
        REAL acknowledged_ts
        TEXT details_json
    }

    %% Domain 3: Galaxy Mapping & Colonization Intelligence
    GALAXY_SCANS {
        INTEGER galaxy PK
        INTEGER system PK
        REAL last_scanned_ts
        INTEGER occupied_count
        INTEGER target_count
        INTEGER free_count
    }
    GALAXY_SCAN_HISTORY {
        INTEGER id PK
        REAL ts
        INTEGER galaxy
        INTEGER system
        INTEGER occupied_count
        INTEGER target_count
        INTEGER free_count
        INTEGER debris_count
        INTEGER asteroid_count
    }
    GALAXY_SLOTS {
        TEXT coords PK
        INTEGER galaxy
        INTEGER system
        INTEGER position
        TEXT planet_name
        TEXT owner
        TEXT status_color
        INTEGER is_inactive
        INTEGER is_spacia
        INTEGER has_debris
        REAL last_scanned_ts
    }
    GALAXY_SLOT_HISTORY {
        INTEGER id PK
        REAL ts
        TEXT coords FK
        INTEGER galaxy
        INTEGER system
        INTEGER position
        TEXT planet_name
        TEXT owner
        TEXT status_color
        INTEGER is_inactive
        INTEGER is_spacia
        INTEGER has_debris
        INTEGER has_asteroid
    }

    %% Domain 4: Espionage & Target Registry
    SPY_TARGETS {
        TEXT coords PK
        TEXT owner
        TEXT planet_name
        TEXT status_color
        INTEGER is_inactive
        INTEGER is_spacia
        INTEGER has_debris
        INTEGER points
        INTEGER pig_iron
        INTEGER metal
        INTEGER kryptonite
        INTEGER spice
        TEXT fleet_json
        TEXT defense_json
        INTEGER dug_in
        REAL last_scanned_ts
        REAL last_spied_ts
        REAL blocked_until_ts
        TEXT block_reason
        INTEGER bash_hits
        REAL last_blocked_ts
        INTEGER uninhabited_fields
        INTEGER spy_count
        INTEGER is_own_planet
        TEXT merged_into
    }
    SPY_REPORTS {
        INTEGER id PK
        REAL ts
        TEXT target_coords FK
        TEXT owner
        TEXT planet_name
        INTEGER is_spacia
        INTEGER pig_iron
        INTEGER metal
        INTEGER kryptonite
        INTEGER spice
        REAL loot_spice_eq
        TEXT buildings_json
        TEXT fleet_json
        TEXT defense_json
        TEXT researches_json
        INTEGER dug_in
        INTEGER uninhabited_fields
        TEXT server_msg_id
        TEXT msg_date_text
        TEXT raw_text
    }

    %% Domain 5: In-Game Inbox, Combat, Bash Regulation & Recycling
    GAME_MESSAGES {
        INTEGER id PK
        REAL ingested_ts
        TEXT msg_uid UK
        TEXT server_msg_id
        TEXT folder_type
        TEXT category
        TEXT msg_date_text
        TEXT sender
        TEXT subject
        TEXT coords
        TEXT body_text
        TEXT raw_html
        TEXT parsed_json
        INTEGER deleted_in_game
        REAL deleted_ts
    }
    RAID_HISTORY {
        INTEGER id PK
        TEXT target_coords FK
        REAL attacked_ts
        INTEGER is_exempt
        TEXT outcome
        TEXT loot_json
    }
    BASH_BLOCKS {
        INTEGER id PK
        TEXT target_coords FK
        REAL blocked_ts
        REAL blocked_until_ts
        REAL cooldown_seconds
        INTEGER is_bash
        TEXT reason
    }
    BATTLE_REPORTS {
        INTEGER id PK
        REAL ts
        TEXT msg_uid UK
        TEXT server_msg_id
        TEXT battle_id
        TEXT msg_date_text
        TEXT target_coords FK
        TEXT attacker
        TEXT defender
        TEXT outcome
        INTEGER rounds
        INTEGER pig_iron
        INTEGER metal
        INTEGER kryptonite
        INTEGER spice
        REAL loot_spice_eq
        INTEGER attacker_lost_spice
        INTEGER defender_lost_spice
        INTEGER debris_pig_iron
        INTEGER debris_metal
        REAL debris_spice_eq
        TEXT raw_text
    }
    RECYCLE_REPORTS {
        INTEGER id PK
        REAL ts
        TEXT msg_uid UK
        TEXT server_msg_id
        TEXT msg_date_text
        TEXT coords FK
        INTEGER recycler_count
        INTEGER cargo_capacity
        INTEGER debris_avail_pig
        INTEGER debris_avail_metal
        INTEGER harvested_pig_iron
        INTEGER harvested_metal
        INTEGER harvested_kryptonite
        INTEGER harvested_spice
        REAL harvested_spice_eq
        TEXT raw_text
    }

    %% Domain 6: Materialized Hot Projections & Colony Attempt Ledger (V011..V012)
    LATEST_STATE_HOT {
        INTEGER id PK
        REAL ts
        TEXT source
        INTEGER universe_id
        INTEGER user_id
        TEXT nickname
        INTEGER active_planet_id
        INTEGER planet_count
        INTEGER planned_action_count
        TEXT state_json
        TEXT plan_json
    }
    EMPIRE_PLANETS_HOT {
        INTEGER planet_id PK
        TEXT coords
        TEXT name
        TEXT planet_type
        INTEGER temperature
        INTEGER fields_used
        INTEGER fields_max
        INTEGER pig_iron
        INTEGER metal
        INTEGER kryptonite
        INTEGER spice
        INTEGER energy_produced
        INTEGER energy_used
        TEXT buildings_json
        TEXT ships_json
        TEXT defense_json
        TEXT economy_rates_json
        TEXT researches_json
        REAL updated_ts
        INTEGER is_active
    }
    GALAXY_SECTOR_HOT {
        INTEGER galaxy PK
        INTEGER system PK
        REAL last_scanned_ts
        INTEGER scan_count
        INTEGER occupied_count
        INTEGER free_count
        INTEGER prime_colony_free_count
        INTEGER debris_count
        INTEGER inactive_count
        INTEGER spacia_count
        INTEGER target_count
        TEXT owners_json
        TEXT owner_planet_counts_json
        TEXT coord_owners_json
        TEXT spacia_coords_json
        REAL updated_ts
    }
    COLONY_CANDIDATES_HOT {
        TEXT coords PK
        INTEGER galaxy
        INTEGER system
        INTEGER position
        INTEGER uninhabited_fields
        INTEGER colonize_min_fields
        INTEGER colonize_max_fields
        INTEGER colonize_temp
        REAL last_scanned_ts
        REAL last_spied_ts
        REAL blocked_until_ts
        TEXT block_reason
        INTEGER is_discarded
        INTEGER is_active
        REAL updated_ts
    }
    COLONY_ATTEMPTS {
        INTEGER id PK
        REAL ts
        TEXT target_coords FK
        TEXT origin_coords
        INTEGER planet_id
        TEXT event_type
        INTEGER fields_rolled
        INTEGER min_fields_target
        INTEGER expected_min_fields
        INTEGER expected_max_fields
        INTEGER temperature
        TEXT grade
        INTEGER score
        TEXT status_message
        TEXT details_json
    }

    %% Logical Relationships
    ECONOMY_RATES_CACHE ||--o{ PLANET_SNAPSHOTS : "planet_id"
    ECONOMY_RATES_CACHE ||--|| EMPIRE_PLANETS_HOT : "planet_id"
    ECONOMY_RATES_CACHE ||--o{ ACTION_HISTORY : "planet_id"
    ECONOMY_RATES_CACHE ||--o{ MANUAL_QUEUE : "planet_id"
    MANUAL_QUEUE ||--o{ MANUAL_QUEUE_EVENTS : "queue_id"
    STATE_SNAPSHOTS }o--|| LATEST_STATE_HOT : "latest snapshot"

    GALAXY_SCANS ||--o{ GALAXY_SCAN_HISTORY : "galaxy, system"
    GALAXY_SCANS ||--|| GALAXY_SECTOR_HOT : "galaxy, system"
    GALAXY_SCANS ||--o{ GALAXY_SLOTS : "galaxy, system"
    GALAXY_SLOTS ||--o{ GALAXY_SLOT_HISTORY : "coords"
    GALAXY_SLOTS ||--o| SPY_TARGETS : "coords"
    GALAXY_SLOTS ||--o| COLONY_CANDIDATES_HOT : "coords"
    GALAXY_SLOTS ||--o{ COLONY_ATTEMPTS : "target_coords"

    SPY_TARGETS ||--o{ SPY_REPORTS : "target_coords"
    SPY_TARGETS ||--o{ RAID_HISTORY : "target_coords"
    SPY_TARGETS ||--o{ BASH_BLOCKS : "target_coords"
    SPY_TARGETS ||--o{ BATTLE_REPORTS : "target_coords"
    GALAXY_SLOTS ||--o{ RECYCLE_REPORTS : "coords"

    GAME_MESSAGES ||--o| BATTLE_REPORTS : "msg_uid"
    GAME_MESSAGES ||--o| RECYCLE_REPORTS : "msg_uid"
    GAME_MESSAGES ||--o{ SPY_REPORTS : "server_msg_id"
    SPY_REPORTS }o--o{ UNIVERSE_RESEARCH_MAX : "researches_json"
    UNIVERSE_RESEARCH_MAX ||--o{ UNIVERSE_RESEARCH_MAX_HISTORY : "records_json"
```

---

## 3. Complete Table Dictionary (32 Tables: 15 Hot / 17 History)

### 3.1 Empire State, Economy, Configuration & Universe Research Records

| Table | Tier | Pattern | Primary Key | Description & JSON Columns |
|---|---|---|---|---|
| `meta` | **Hot** | Upsert KV | `key` | Schema version (`schema_version`, `schema_version_tag`, `schema_checksum`), `discarded_colony_coords`, and runtime state keys. |
| `latest_state_hot` | **Hot** (`V012`) | Single-row Upsert | `id` (`CHECK id = 1`) | Materialized O(1) latest `GameState` + planned actions projection (`state_json`, `plan_json`, `active_planet_id`, `planet_count`), synced automatically from `record_full_state_snapshot`. |
| `empire_planets_hot` | **Hot** (`V012`) | Upsert + Soft flag | `planet_id` | Current per-colony snapshot (`coords`, resources, energy, `buildings_json`, `ships_json`, `defense_json`, `economy_rates_json`, `researches_json`, `is_active`), avoiding full scans over append-only `planet_snapshots`. |
| `strategy_config_store` | **Hot** (`V008`) | Single-row Upsert | `id` (`CHECK id = 1`) | Authoritative persisted `StrategyConfig` (`config_json`, `updated_ts`, `source`), retiring legacy `config.yaml` sidecars into SQLite. |
| `config_history` | **History** | Append-only | `id` | Immutable audit log of every `StrategyConfig` snapshot (`config_json`). |
| `rank_snapshots` | **History** | Append-only | `id` | Time-series account progression: server points/rank, estimated build+tech+fleet points, `pts_per_hr`, total `spice_value`, and colony count. |
| `planet_snapshots` | **History** | Append-only | `id` | Time-series colony snapshots: resources, energy (`energy_produced`, `energy_used`), field usage, `buildings_json`, `ships_json`, `defense_json`, `economy_rates_json`, and `researches_json`. |
| `state_snapshots` | **History** (`V007`) | Append-only | `id` | Full `GameState` + planned-action snapshots (`source`, `universe_id`, `user_id`, `nickname`, `active_planet_id`, `planet_count`, `planned_action_count`, `state_json`, `plan_json`), consolidating legacy `state/snapshot.json` into SQLite. |
| `economy_rates_cache` | **Hot** | Upsert State | `planet_id` | Latest persisted `internalEconomy` slider percentages (`resource1..resource6` in `rates_json`) per owned colony. |
| `tutorial_progress` | **Hot** | Upsert State | `step` | Tutorial steps `1..11`, task checklist (`tasks_json`), reward text, claim readiness, and completion timestamps. |
| `universe_research_max` | **Hot** (`V009`) | Monotonic Upsert | `research_key` | Persisted max observed research levels across the universe (`r1`–`r15` + `urplasma-extraction`): `research_id`, `slug`, `name`, `own_level`, `spied_max_level`, `server_record_level`, `universe_max_level`, `catalog_max_level`, `holder`, `holder_coords`, and `updated_ts`. Refreshed via `refresh_universe_research_max` (`#btn-refresh-universe-research` in `#view-config` / `POST /command {"command": "universe_research_max", "sync": true}`). |
| `universe_research_max_history` | **History** (`V009`) | Append-only | `id` | Immutable audit log of every universe max research refresh (`ts`, `source`, `records_json`). |

### 3.2 Action Audit Log, Operator Notifications & Manual Queue

| Table | Tier | Pattern | Primary Key | Description & Lifecycle |
|---|---|---|---|---|
| `action_history` | **History** | Append-only | `id` | Every live or dry-run command attempted by `commander.py` or the Operator UI (`source`, `command`, `priority`, `kind`, `planet_id`, `target_id`, `target_name`, `target_level`, `executed`, `dry_run`, `reason`, `message`). |
| `ui_notifications` | **Hot** (`V008`) | Soft-state | `id` | Persistent action-validation and target-protection alerts (`severity`, `category`, `title`, `message`, `help_prompt`, `dedupe_key`, `occurrence_count`, `status` in `unread` / `acknowledged`, `acknowledged_ts`, `details_json`). |
| `manual_queue` | **Hot** | Soft-state | `id` | Operator-enqueued manual commands (`status` in `pending` / `completed` / `canceled`, `priority` `P0`..`P3`, `sort_rank`). `P0`/`P1` pending items reserve colony resources and execute ahead of automated plans. |
| `manual_queue_events` | **History** | Append-only | `id` | Immutable audit trail of queue mutations (`enqueued`, `updated`, `reordered`, `completed`, `canceled`) linked via `queue_id -> manual_queue.id`. |

### 3.3 Galaxy Cartography & Colonization

| Table | Tier | Pattern | Primary Key | Description |
|---|---|---|---|---|
| `galaxy_scans` | **Hot** | Upsert State | `(galaxy, system)` | Latest scan timestamp and slot counts (`occupied_count`, `target_count`, `free_count`) per solar system so `commander.py` prioritizes unscanned/stale systems. |
| `galaxy_sector_hot` | **Hot** (`V012`) | Materialized Upsert | `(galaxy, system)` | Pre-aggregated per-sector operational summary (`scan_count`, `occupied_count`, `free_count`, `prime_colony_free_count`, `debris_count`, `inactive_count`, `spacia_count`, `target_count`, `owners_json`, `owner_planet_counts_json`, `coord_owners_json`, `spacia_coords_json`). Powers `get_galaxy_sector_intel` (`#view-sectors`) without full-scanning 46k+ `galaxy_slots` + `galaxy_scan_history` rows. |
| `galaxy_scan_history` | **History** | Append-only | `id` | Historical log of every solar-system scan including `debris_count` and `asteroid_count`. |
| `galaxy_slots` | **Hot** | Upsert State | `coords` | Latest known state of each `GG:SSS:PP` orbital slot (`free`, `yellow` inactive, `blue` protected, `red` banned/vacation, `is_spacia`, `has_debris`, `V010` `colonize_min_fields`/`colonize_max_fields`/`colonize_temp`). |
| `galaxy_slot_history` | **History** | Append-only | `id` | Longitudinal history of orbital slot ownership, inactivity transitions, debris appearance, and asteroids. |
| `colony_candidates_hot` | **Hot** (`V012`) | Materialized Upsert + Soft flags | `coords` | Pre-joined free orbital slot projection (`galaxy`, `system`, `position`, `uninhabited_fields`, `colonize_min_fields`, `colonize_max_fields`, `colonize_temp`, `blocked_until_ts`, `block_reason`, `is_discarded`, `is_active`). Powers `get_colony_candidates` (`#view-colonies`) via exact arithmetic pre-ranking + `heapq.nsmallest`. |
| `colony_attempts` | **History** (`V011`) | Append-only | `id` | Longitudinal colonization ledger recording every Colony ship dispatch, settled planet, kept `>= min_fields` roll, deleted undersized roll, and dispatch failure (`target_coords`, `origin_coords`, `planet_id`, `event_type`, `fields_rolled`, `min_fields_target`, `expected_min_fields`, `expected_max_fields`, `temperature`, `grade`, `score`, `status_message`, `details_json`). |

### 3.4 Espionage & Target Registry

| Table | Tier | Pattern | Primary Key | Description & Non-Destructive Flags |
|---|---|---|---|---|
| `spy_targets` | **Hot** | Upsert State | `coords` | Master target table merging galaxy scan status with latest espionage intelligence (`pig_iron..spice`, `fleet_json`, `defense_json`, `dug_in`, `uninhabited_fields`, `spy_count`), bash cooldowns (`blocked_until_ts`, `block_reason`, `bash_hits`), and soft flags (`is_own_planet`, `merged_into`). |
| `spy_reports` | **History** | Append-only | `id` | Every individual espionage report ever ingested (`target_coords`, 4 resources + `loot_spice_eq`, `buildings_json`, `fleet_json`, `defense_json`, `researches_json`, `uninhabited_fields`, `server_msg_id`, `msg_date_text`). Powers colony size grading (`S`/`A+`/`A` for `300+` fields) and farm depletion curves. |

### 3.5 In-Game Messages, Combat, Bash Blocks & Recycling

| Table | Tier | Pattern | Primary Key / Unique | Description |
|---|---|---|---|---|
| `game_messages` | **History** | Append-only + Soft flag | `id` (`msg_uid` `UNIQUE`) | Full archive of every `internalMessages` item (`spy_report`, `battle_report`, `recycle_report`, `fleet_return`, `other`) with `body_text`, sanitized `raw_html`, structured `parsed_json`, and `deleted_in_game` / `deleted_ts` tracking after in-game inbox cleanup. |
| `raid_history` | **History** | Append-only | `id` | Dispatched attack log (`target_coords`, `attacked_ts`, `is_exempt`, `outcome`, `loot_json`), feeding `BashTracker` (`3/24h` active-player bash limit) and `ga_nn` historical win-rate priors. |
| `bash_blocks` | **History** | Append-only | `id` | Server-enforced mission blocks parsed from `internalSendFleet` banners (e.g. `Attack-Bash-Regulations: 3 attacks in 24 hours • Reset in HH:MM:SS`), overriding `is_spacia` / `is_inactive` exemptions until `blocked_until_ts`. |
| `battle_reports` | **History** | Append-only | `id` (`msg_uid` `UNIQUE`) | Resolved combat outcomes (`win` / `loss`, `rounds`), exact 4-resource plunder (`pig_iron..spice`, `loot_spice_eq`), fleet losses (`attacker_lost_spice`, `defender_lost_spice`), and spawned orbital rubble (`debris_pig_iron`, `debris_metal`, `debris_spice_eq`). |
| `recycle_reports` | **History** | Append-only | `id` (`msg_uid` `UNIQUE`) | Resolved `Recycler` (`s11`) harvests (`coords`, `recycler_count`, `debris_avail_pig`, `debris_avail_metal`, 4 harvested resources + `harvested_spice_eq`). |

---

## 4. Secondary Index Catalog (34 Indexes)

| Index Name | Table & Columns | Query Accelerated |
|---|---|---|
| `idx_rank_snapshots_ts` | `rank_snapshots(ts)` | Ranking evolution charts & pts/hr trend windowing |
| `idx_planet_snapshots_pid_ts` | `planet_snapshots(planet_id, ts)` | Per-colony resource/infrastructure time-series lookups |
| `idx_action_history_ts` | `action_history(ts)` | Recent executed actions & hourly rate-cap audit |
| `idx_spy_reports_coords_ts` | `spy_reports(target_coords, ts)` | Per-target espionage history & latest `uninhabited_fields` |
| `idx_game_messages_ts` | `game_messages(ingested_ts)` | Chronological inbox feed in Operator Console |
| `idx_game_messages_coords` | `game_messages(coords, ingested_ts)` | Coordinate-filtered message history |
| `idx_game_messages_cat` | `game_messages(category, ingested_ts)` | Category tabs (`spy_report`, `battle_report`, `recycle_report`) |
| `idx_galaxy_scans_ts` | `galaxy_scans(last_scanned_ts)` | Oldest-scanned solar-system selection |
| `idx_galaxy_scan_history_sys_ts` | `galaxy_scan_history(galaxy, system, ts)` | System activity trend analysis |
| `idx_galaxy_slots_sys` | `galaxy_slots(galaxy, system, position)` | Solar-system map reconstruction & colony slot search |
| `idx_galaxy_slot_history_coords_ts` | `galaxy_slot_history(coords, ts)` | Player inactivity transition detection |
| `idx_config_history_ts` | `config_history(ts)` | Configuration change timeline |
| `idx_raid_history_coords_ts` | `raid_history(target_coords, attacked_ts)` | Rolling 24h `BashTracker` window & per-target raid stats |
| `idx_raid_history_ts` | `raid_history(attacked_ts)` | Global chronological raid throughput (`V006`) |
| `idx_bash_blocks_coords_ts` | `bash_blocks(target_coords, blocked_ts)` | Per-target server bash-cooldown history |
| `idx_bash_blocks_until_ts` | `bash_blocks(blocked_until_ts)` | Active unexpired target cooldown scan (`V006`) |
| `idx_battle_reports_coords_ts` | `battle_reports(target_coords, ts)` | Per-target win rate & loot mean/std in `full_planner.ipynb` |
| `idx_recycle_reports_coords_ts` | `recycle_reports(coords, ts)` | Debris-field harvest history |
| `idx_manual_queue_status_prio_rank` | `manual_queue(status, priority, sort_rank, id)` | Pending manual queue frontier ordering (`V004`) |
| `idx_manual_queue_events_qid_ts` | `manual_queue_events(queue_id, ts)` | Per-queue-item audit trail (`V004`) |
| `idx_schema_migration_events_ts` | `schema_migration_events(ts)` | Chronological schema upgrade & backup audit trail (`V005`) |
| `idx_spy_targets_colony_filter` | `spy_targets(is_own_planet, merged_into, is_spacia, is_inactive)` | Colony candidate & active raid target filtering (`V005`) |
| `idx_battle_reports_ts` | `battle_reports(ts)` | Chronological attack result feeds (`V005`) |
| `idx_recycle_reports_ts` | `recycle_reports(ts)` | Chronological recycler harvest feeds (`V005`) |
| `idx_state_snapshots_ts` | `state_snapshots(ts)` | Chronological full-state & plan snapshot lookups (`V007`) |
| `idx_ui_notifications_status_ts` | `ui_notifications(status, updated_ts)` | Unread operator notification banner & modal feed (`V008`) |
| `idx_ui_notifications_dedupe` | `ui_notifications(dedupe_key, status)` | Idempotent validation-alert deduplication (`V008`) |
| `idx_universe_research_max_hist_ts` | `universe_research_max_history(ts)` | Chronological universe max research refresh audit (`V009`) |
| `idx_colony_attempts_coords_ts` | `colony_attempts(target_coords, ts)` | Per-coordinate colonization trial lookup (`V011`) |
| `idx_colony_attempts_ts` | `colony_attempts(ts)` | Chronological colonization attempts feed (`V011`) |
| `idx_empire_planets_hot_coords` | `empire_planets_hot(coords)` | Active owned-colony coordinate lookup (`V012`) |
| `idx_galaxy_sector_hot_ts` | `galaxy_sector_hot(last_scanned_ts)` | Hot sector freshness & staleness scans (`V012`) |
| `idx_colony_candidates_hot_rank` | `colony_candidates_hot(is_active, is_discarded, uninhabited_fields DESC, colonize_max_fields DESC)` | Fast hot colony candidate ranking (`V012`) |
| `idx_colony_candidates_hot_sys` | `colony_candidates_hot(galaxy, system, position)` | System-scoped hot colony candidate maintenance (`V012`) |

---

## 5. Schema Versioning & Forward-Only Migrations (`spaceinvasion/db/migrations.py`)

All SQLite databases (`state/spaceinvasion.db` plus any auxiliary `state/*.db` stores) are versioned and migrated non-destructively via `MigrationRunner` and `DatabaseMigrationRegistry` (`spaceinvasion/db/migrations.py`, CLI `scripts/db_migrate.py`):

| Ledger Table | Tier | Pattern | Primary Key | Description |
|---|---|---|---|---|
| `schema_migrations` | **Hot** | Append-only | `version` | Authoritative ledger of applied forward migrations (`V001..V012`), storing `version_tag`, `name`, `description`, deterministic SHA-256 DDL `checksum`, `applied_ts`, `duration_ms`, and `details_json` (e.g. `added_columns`, backfill counts). |
| `schema_migration_events` | **History** | Append-only | `id` | Immutable audit log of every `migration_applied` and `backup_created` event (`from_version`, `to_version`, `backup_path`, `details_json`). |

### Ordered Migration Catalog (`V001..V012`)

| Version | Name | Summary of Non-Destructive Schema Additions |
|---|---|---|
| `V001` | `v001_core_empire_telemetry` | Core empire telemetry (`meta`, `rank_snapshots`, `planet_snapshots`, `economy_rates_cache`, `tutorial_progress`, `action_history`, `config_history`). |
| `V002` | `v002_espionage_galaxy_and_raid_ledger` | Espionage & galaxy cartography (`spy_targets`, `spy_reports`, `galaxy_scans`, `galaxy_scan_history`, `galaxy_slots`, `galaxy_slot_history`, `raid_history`, `bash_blocks`). |
| `V003` | `v003_inbox_battle_and_recycle_reports` | Archived inbox & combat/rubble ledgers (`game_messages`, `battle_reports`, `recycle_reports`). |
| `V004` | `v004_manual_command_queue` | Operator manual queue & event log (`manual_queue`, `manual_queue_events`). |
| `V005` | `v005_migration_ledger_and_analytics_indexes` | Schema migration ledger (`schema_migrations`, `schema_migration_events`) and analytics indexes. |
| `V006` | `v006_absorb_legacy_attack_json_and_retire_mirror` | Retires `data/attack_history.json` into SQLite and adds `idx_raid_history_ts` + `idx_bash_blocks_until_ts`. |
| `V007` | `v007_consolidate_rank_and_state_snapshots_in_sqlite` | Adds `state_snapshots` (`idx_state_snapshots_ts`) and retires `state/rank_history.json` & `state/snapshot.json`. |
| `V008` | `v008_db_strategy_config_and_ui_notifications` | Adds `strategy_config_store` and `ui_notifications` (`idx_ui_notifications_status_ts`, `idx_ui_notifications_dedupe`). |
| `V009` | `v009_universe_research_max_ledger` | Adds persisted `universe_research_max` (`research_key` PK) and append-only `universe_research_max_history` (`idx_universe_research_max_hist_ts`) for universe-wide max technology tracking. |
| `V010` | `v010_colony_ecology_fields` | Adds live galaxy JSON colonization ecology columns (`colonize_min_fields`, `colonize_max_fields`, `colonize_temp`) to `galaxy_slots` and `galaxy_slot_history`. |
| `V011` | `v011_colony_attempts_ledger` | Adds append-only `colony_attempts` ledger (`idx_colony_attempts_coords_ts`, `idx_colony_attempts_ts`) tracking colony dispatches, kept rolls, undersized deletions, and failures. |
| `V012` | `v012_hot_and_history_table_division` | Formalizes the **Hot (15) vs. History (17)** table division, adds materialized hot projections (`latest_state_hot`, `empire_planets_hot`, `galaxy_sector_hot`, `colony_candidates_hot`) with 4 targeted indexes, and backfills them non-destructively via `backfill_v012_hot_tables`. |

- **Triple-Synced Version Markers**: Every applied migration synchronizes `schema_migrations.version`, `meta` (`schema_version`, `schema_version_tag`, `schema_checksum`), and SQLite's binary header `PRAGMA user_version = 12`.
- **Savepoint-Isolated Execution & Online Backups**: Individual migrations execute via `conn.execute(...)` inside per-version SQLite `SAVEPOINT`s (preserving transactional DDL rollback on failure) and automatically create hot WAL-consistent backups in `state/backups/` via `sqlite3.Connection.backup` before upgrading `state/spaceinvasion.db`.
- **CLI & HTTP API Commands**:
  - CLI: `.venv/bin/python3 scripts/db_migrate.py {status,migrate,verify,backup,history} [--all] [--dry-run] [--backup] [--json]`
  - HTTP `POST /command`: `db_migrations`, `db_migrate`, `db_backup`, `db_verify`, `state_snapshots`.

---

## 6. Legacy JSON Sidecar Retirement & Safe Offline Access

- **SQLite-Only Persistence (`V006` & `V007`)**: All three legacy JSON sidecars (`data/attack_history.json`, `state/rank_history.json`, and `state/snapshot.json`) have been retired into SQLite (`raid_history`, `bash_blocks`, `rank_snapshots`, `state_snapshots`, and `planet_snapshots`, with audit metadata recorded in `meta`). `spaceinvasion/optimizer.py` (`load_attack_history_stats`) and `spaceinvasion/ranking.py` (`load_rank_history`) read directly from isolated temporary copies of `state/spaceinvasion.db`.
- **Read-Only Notebook Pattern**: When inspecting `state/spaceinvasion.db` from external scripts or Jupyter notebooks (`notebooks/optimizer_experimentation.ipynb`, `notebooks/full_planner.ipynb`), always copy `spaceinvasion.db` plus `-wal`, `-shm`, and `-journal` sidecars into a `tempfile.TemporaryDirectory()` before opening SQLite so the live `GameApiBridge` WAL writer is never blocked or mutated.
