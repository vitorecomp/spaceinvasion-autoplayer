# R2 Diagnosis & Fix Report: Universe / Galaxy View Stalls, Page-View Throttling, and Attack / Exploration Reliability

**Scope:** Milestone `M2-r2-galaxy-throttle-fix` (`spaceinvasion/client.py`, `spaceinvasion/fleet.py`, `spaceinvasion/storage.py`, `spaceinvasion/executor.py`, `spaceinvasion/strategy.py`, `spaceinvasion/api_server.py`, `commander.py`).

---

## 1. Executive Summary

Opening the SpaceInvasion Classic Universe/Galaxy view (`internalGalaxy`) and running exploration/attack flows stalled or failed from **five interacting defects**:

1. **Missing `F_Navi` link selector (`a[href*="action=internalGalaxy"]`) & click interception**: SpaceInvasion Classic renders the Galaxy menu item as `<a href="#" onclick="p_galaxy()">` bordered by `<td class="rahmen"></td>`, causing `NoSuchElementException` / `ElementClickInterceptedException` (HTTP `500`) until replaced with `SI_TPL.menu.sid` + `p_galaxy()` fallback.
2. **Per-instance pacing reset & unbudgeted page-view bursts (`~17–25` views/action tick)**: `GameApiBridge` constructed a fresh `GameBrowserClient` (`_last_nav_at = 0.0`) on every HTTP call with no shared rolling 60-second page-view cap, no pacing on `read_planets_overview(refresh=True)` or `ActionExecutor` submits, redundant `read_full_snapshot(include_buildings=True)` + forced `navigate_menu("internalHome")` after `scan_galaxy`, and unconditional `include_shipyard=True`/`include_research=True` polls.
3. **Energy-coupled Blast furnace (`b2`) `0% <-> 100%` oscillation**: Throttling `b2` L11 to `0%` freed `102` energy (`energy_used: 783 -> 681`), hiding `P1 Fusion plant L14` and surfacing `P2 Development center L5` (missing Metal), which immediately resumed `b2` to `100%` (`energy_used -> 783`), repeating every `~40s` and compounding the per-minute page-view burst.
4. **Missing cooldown handling & retry storm**: `RateLimitError` inherited from `SpaceInvasionAuthError` without cooldown state, causing `api_server.py` to return generic HTTP `503` (or flip `in_game=False` / trigger re-login) while `commander.py` slept `3.0s` instead of `>= 45s` and re-hit the throttled server.
5. **Stale frame waits, parser gaps, & unsafe `internalFleet` selectors**: `_wait_frame_ready()` lacked a pre-navigation DOM token (`window.__siNavToken`) and skipped `_check_page_guards(html)` when `expected_action` was absent from `location.href`; `parse_galaxy_system_html()` broke on bracketed `[04]` slots, shifted icon cells, and `#ff0` matching inside `#ff0000`; exploration had no `galaxy_scans` spiral memory; and `ActionExecutor._execute_attack` risked touching `internalFleet`'s target-favourites `<select name="planet">`.

---

## 2. Verified Incident Timeline (`logs/` & `state/spaceinvasion.db`)

| Timestamp (UTC) | Source | Event / Error |
|---|---|---|
| `2026-11-15T01:36:24.363425Z`, `01:41:41.969222Z`, `03:12:56.210764Z` | `logs/game_api.log:6, 15, 170` | `POST /command -> 503 \| Timed out waiting for frame F_Hauptframe (internalBuildings).` |
| `2026-11-15T01:50:33.084333Z`, `03:44:53.717618Z`, `03:46:32.024646Z` | `logs/game_api.log:35, 206, 233` | `POST /command -> 500 \| no such element: Unable to locate element: {"method":"css selector","selector":"a[href*="action=internalGalaxy"]"}` |
| `2026-11-15T01:51:35.491350Z`, `01:51:37.772492Z` | `logs/game_api.log:65, 92` | `POST /command -> 500 \| element click intercepted ... Other element would receive the click: <td class="rahmen"></td>` |
| `2026-11-15T03:35:53Z` – `03:56:09Z` | `logs/game_api.log:188–274`, `state/spaceinvasion.db` `action_history:14–37` | `17+` consecutive live `THROTTLE_BUILDING` flips (`Blast furnace 0% <-> 100%`) at `~36–46s` intervals (`03:41:02Z`–`03:56:02Z` DB rows `14..37`). |
| `2026-11-15T03:48:18Z` + `03:49:03Z` -> `03:49:22.072392Z` & `03:49:33.625300Z` | `logs/game_api.log:261, 263, 264, 266` | Two consecutive live `THROTTLE_BUILDING` bursts (~34–38 server views in `<60s`) tripped `POST /command -> 503 \| SpaceInvasion per-minute page-view throttle hit; waiting before next read.` on `navigate internalGalaxy`. |
| `2026-11-15T03:57:09.898949Z` + `03:57:54.162071Z` -> `03:59:34.193020Z` | `logs/game_api.log:275–277`, `logs/commander.log:595–610` | `BUILD` (~18 views) + `RESEARCH` (~18 views) + 3 idle polls (~15 views) -> `POST /command -> 503 \| actual=scan_galaxy \| SpaceInvasion per-minute page-view throttle hit`. |

---

## 3. Root Causes in Detail

### 3.1 Page-View Amplification Math (Before vs. After)

| Flow | Views Before | Root Amplification Drivers | Views After |
|---|---|---|---|
| **Idle Commander tick** (`20s` interval) | **4–6 views/tick** (`12–18/min`) | `get_state(include_buildings=True)` forced `F_planets` reload + `internalHome` + `internalBuildings` + `internalWeaponFactory` + `internalHome` on every 20s poll | **1–2 views/tick** (`~4–6/min`) via light `include_buildings=False` polling (full buildings every `60s` or when `WAIT_RESOURCES` becomes affordable), `return_home=False`, and `60s`/`120s` research/shipyard TTLs |
| **Live Action tick** (`BUILD`/`THROTTLE`/`ATTACK`) | **17–25 views/tick** | `get_state` (`4–5`) + command handler `read_full_snapshot` (`2–5`) + `ActionExecutor` DOM loads (`3–4`) + `_execute_and_summarize` `read_full_snapshot(include_buildings=True)` (`4–5`) + unconditional `navigate_menu("internalHome")` (`1`) + post-exec Commander `get_state` (`4–5`) | **~6–8 views/tick** via snapshot TTL reuse (`<=15s`), narrow `include_*` flags (`include_shipyard=False` for `attack`/`build`), `return_home=False`, and returning `"state"` in `/command` so `commander.py` avoids duplicate `GET /state` |
| **`scan_galaxy` / `spy`** | **6–9 views** + pulled off Galaxy | Unnecessary `read_full_snapshot(include_buildings=True)` + unconditional `navigate_menu("internalHome")` after scan | **1 view** (`open_galaxy` only; reuses `_last_state`, `0` post-scan snapshots, stays on Galaxy) |
| **Cross-request pacing** | `0s` reset every HTTP call | `GameApiBridge` created fresh `GameBrowserClient` (`_last_nav_at = 0.0`) with unpaced `read_planets_overview(refresh=True)` and `ActionExecutor` clicks | Process-wide thread-safe `PageViewLimiter` (`0.9–1.8s` inter-nav gap, `24/min` rolling cap with `6` views reserved for `"interactive"` commands) |

### 3.2 Rate-Limit Cooldown & Retry Storm
- `RateLimitError` previously had no `retry_after` field and triggered no shared cooldown, while all five `except SpaceInvasionAuthError` blocks in `spaceinvasion/api_server.py` (`ensure_debug_browser_ready`, `health`, `dashboard`, `Handler.do_GET`, `Handler.do_POST`) treated rate-limiting as auth loss (`in_game = False`, HTTP `503`, or re-login).
- Meanwhile, `commander.py` fell back to `time.sleep(3.0)` on `GameApiError`, causing continuous retries during active server throttling, and blocking `45s` inside `GameApiBridge._lock` would exceed `ui/app.js`'s `8s` `/dashboard` and `12s` `/command` abort timeouts.
- **Fix:** `PageViewLimiter` records a `45s` cooldown on `RateLimitError` and fails fast (`max_wait = 8.0s` in `GameApiBridge`) without sleeping inside HTTP locks; `api_server.py` catches `RateLimitError` before `SpaceInvasionAuthError`, preserves `in_game=True`, returns HTTP `429` (`Retry-After`, `rate_limited=True`, `retry_after`, descriptive `error`) on `/state` and `/command`, keeps `/dashboard` at HTTP `200` with cached state during cooldown, and `commander.py` uses `compute_rate_limit_backoff()` to sleep `>= max(retry_after, 45.0)s` without calling `ensure_session`.

### 3.3 Blast Furnace (`b2`) Energy-Coupled `0% <-> 100%` Oscillation
- Verified directly against `state/spaceinvasion.db` `planet_snapshots` row 36 (`2026-11-15 03:55:16Z`, `b1=12, b2=11, b3=11, b4=8, b5=13, b7=4, b8=1, b9=2`, `speedup=20x`):
  - `game_data.building_level_stats(2, 11)["energy"] == -102` (matching DB `energy_used = 783` at `100%` vs `681` at `0%`).
  - `game_data.blast_furnace_pig_iron_used(11) * 20.0 == 21,480` Pig iron/h.
  - At `100%` (`net = +31`), `Pig iron mine L13` (`-63` energy) triggered `P1 Fusion plant L14` (Pig-iron bottleneck), so `_plan_blast_furnace_throttle` paused `b2` to `0%`.
  - At `0%` (`net = +133`), `Pig iron mine L13` fit inside spare energy (`133 - 63 = +70 >= 0`), so `P1 Fusion plant L14` disappeared and `P2 Development center L5` (Metal bottleneck) became top blocker — immediately resuming `b2` to `100%` (`net -> +31`), regenerating `P1 Fusion plant L14` on the next tick.
  - Additionally, the resume reserve guard used `max(5000, metal * 2)` (`5,000 < 21,480` Pig iron/h drain at `20x`), creating a threshold inversion, and there was no per-planet temporal cooldown.
- **Fix:** `_plan_blast_furnace_throttle` now enforces (a) a `300s` per-planet cooldown via `throttle_cooldowns` (outside negative-energy emergencies), (b) energy-coupling lookahead (`net_if_resumed` vs planned building starvation + `P1` power Pig-iron cost), and (c) symmetric hysteresis deadbands (`bf_pig_used_full * 1.5` reserve threshold, `storage_warn_ratio * 0.85` Metal ceiling, and `bf_pig_used_full * 0.6` minimum Pig iron for deficit-state Metal resume).

### 3.4 Frame Transitions, `open_galaxy`, Galaxy Parser & Exploration Sweep
- `_wait_frame_ready()` now uses a pre-navigation JS token (`window.__siNavToken` via `_mark_frame_navigation`) so consecutive `internalGalaxy -> internalGalaxy` and `internalFleet -> internalSendFleet` transitions cannot return stale `readyState == "complete"` DOM, and runs `_check_page_guards(html)` unconditionally on every completed frame load (plus inside `ensure_in_game()` on missing `#json-container`).
- `open_galaxy_with_coords()` / `open_galaxy()` normalize `SI_TPL.menu.sid` (with/without `sid=`), search `F_Navi` links on fallback, build same-origin URLs via `urljoin` (safe even from `about:blank`), handle `ElementClickInterceptedException` on `a[onclick*="p_galaxy"]`, and fall back to submitting `galaxy`/`gal` + `system`/`sun` inputs if initial URL params land elsewhere.
- `parse_galaxy_system_html()` adds `extract_galaxy_system_coords()`, supports `[04]`/`4.`/`#4`, shifted icon/action cells, Modern `<div>`/`<li>` slot cards, bounded hex/CSS color regexes (preventing `#ff0` matching inside `#ff0000`), `(i)`/`(I)` inactive suffixes, case-insensitive `spy`/`onclick` extraction, and `parse_spy_messages_html()` for `internalMessages&type=spio`.
- `GameDatabase` adds `galaxy_scans` + `last_spied_ts` on `spy_targets` and fixes `record_spy_report()` so espionage ingestion no longer overwrites `yellow`/`is_inactive` flags into `light_red`. `strategy.py` implements outward spiral `SCAN_GALAXY` exploration (`±15` systems with `1800s` cooldown) and `s10` `SPY` probe planning when probes exist (preserving Tutorial 6 combat behavior when `s10 == 0`).

### 3.5 Fleet Dispatch (`internalFleet -> internalSendFleet`) & API Safety
- `_navigate_and_select_planet()` inspects HTML with BeautifulSoup first (preserving zero-overhead fast path when already on target planet) and never touches `internalFleet`'s `<select name="planet">` target favourites — switching via `internalBuildings` (`Select.select_by_value` + `__siNavToken`) before entering `internalFleet`.
- `_dispatch_fleet_mission()` unifies `_execute_attack` and `_execute_spy` (`s10`), supports `ship_N` / `ship[N]` / `sN` / `shipN`, targets `form[action*="internalSendFleet"]`, scopes mission radios & launch submit strictly to the dispatch form, waits with `expected_action="internalSendFleet"` + `__siNavToken`, handles `ElementClickInterceptedException`/`StaleElementReferenceException`, exempts read-only `SCAN_GALAXY` from `max_actions_per_hour`, syncs `max_actions_per_hour` on both `_live_executor` and `_dry_executor`, and enforces `BashTracker` (`3/24h`, yellow/Spacia exempt) + `simulate_raid_outcome` in `/command attack`.

---

## 4. Verification Summary

- Verified with full test suite (`.venv/bin/python3 -m unittest discover -s tests -v`), covering:
  - `tests/test_client.py`: `PageViewLimiter` cross-client spacing, 60s rolling cap, reserved interactive headroom, fast-fail `RateLimitError` cooldown, `read_planets_overview` pacing, `ensure_in_game` guards, `__siNavToken` stale-DOM prevention, `open_galaxy` `sid` / same-origin / `p_galaxy()` / form fallbacks.
  - `tests/test_r2_throttle.py`: HTTP `429` + `Retry-After` + `in_game=True` on `RateLimitError`, `commander.py` `>= 45s` backoff, `WATCH_PATHS` `optimizer.py` reload safety, burst cuts & snapshot TTLs, `navigate("internalGalaxy")` coordinates, `update_config` syncing `max_actions_per_hour` + `_dry_executor`, `BashTracker` & `simulate_raid_outcome` gating on `/command attack`, `sync_spy_reports` flag preservation, multi-system spiral sweep, and DB row 36 Blast furnace hysteresis.
  - `tests/test_executor.py` & `tests/test_fleet.py`: `internalBuildings` planet switch without touching fleet favourites, `internalSendFleet` + `ship[N]` + form-scoped mission radio/submit, click-intercepted & stale retries, `SCAN_GALAXY` budget exemption, `_execute_spy`, and hardened Galaxy/Spy HTML parsers.
  - Wave 2 hardening (`Iteration 2`):
    - `RateLimitError.server_throttle` + `_recorded_limiter_id` deduplication prevents local normal-budget hits from locking reserved interactive slots or double-counting/extending cooldown tails; `ActionExecutor(..., max_limiter_wait=8.0)` bounds lock wait time; `ensure_in_game()` and `read_full_snapshot()` reload stale `F_planets`/`F_Hauptframe` throttle DOM after cooldown expiry; `commander.py` exits cleanly at `--max-ticks` across repeated rate-limit ticks and skips building re-fetches when queues are full (`5/5` or `4/4` asteroid).
    - `api_server.py` invalidates `_last_buildings_fetched` when cached offers drop on non-building refreshes, tracks research/shipyard sync signatures so empty syncs stay TTL-paced, gates `in_game` on `debug_port_open`, passes `planets=()` in `simulate_combat`, checks DB/cache targets for `is_exempt` in `record_raid`, and enriches `raid_targets` with F19 full-history stats (`GameDatabase.get_raid_stats()` + `data/attack_history.json`).
    - `fleet.py` eliminates triple-coordinate backtracking & clock/field-ratio shadowing in `extract_galaxy_system_coords()` and splits attribute styling (`style_attrs` incl. `galaxy_dark_red`) from semantic text markers in `_classify_slot_status()`.
    - `executor.py` scopes Step 1 multi-form `gal`/`sun`/`pla` lookups and uses container-aware 2-pass mission radio matching ignoring speed/route radios (`procent`, `geschwindigkeit`, `%`).
    - `strategy.py` extends Blast furnace hysteresis with `top_needs_metal` consistency (`p_acts[:3]`) and non-metal `P1` power-affordability lookahead.
