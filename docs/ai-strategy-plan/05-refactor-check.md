# Check after the refactor

On 2026-11-20 and 21 the whole system was refactored: 36 Python modules became 468, the database layer moved to PostgreSQL only, an unused planner mode was removed, and the package was renamed to `backend/`. The operator's statement was that the decision logic stayed the same, and asked for a review. This page records what was compared, what was found, and what was not covered.

Compared: commit `9ee3597`, the last one before the refactor, against commit `86720e7`, `main` at 01:26 UTC on 2026-11-21. Between them: 150 commits and 970 changed files.

## Result

**The planner decides the same.** Given the same empire state, the same saved plan and the same settings, the planner code before and after the refactor returns the same actions. That held for every one of 252 states tried and for full planner and optimizer runs. No difference was found in the rules of plans v2 to v4.

Three differences were found that are not part of the planner's decisions, and one maintenance trap. They are listed under [Differences found](#differences-found). Four areas were screened by tools and by the tests but not read line by line. They are listed under [Not covered](#not-covered).

## What was done

### 1. The planner replayed on both versions

One script imported the planner functions from the old tree and from the new tree in separate processes, gave both the same inputs, and compared every output. Nothing touched the game, the bridge or the database; the inputs were cached reads saved earlier.

Inputs: four snapshots of the live empire (07:28, 08:25 and 23:05 on 2026-11-20, 02:25 on 2026-11-21). Each was used as it was with three settings variants (live, switch off, rule-based mode), and in 60 altered copies with different stocks, furnace settings, free builders, hulls, building levels and queues. That gives 252 states.

For each state, 27 outputs: the saved plan reconciled with the state, the fill rule, shipments with and without the plan's needs, stationing, furnace orders in three variants, the transport targets, the ship budgets, the fleet plan, the cycle's merged and ordered plan, the idle diagnosis, the rule-based planner, its graph, and the economy estimate of each colony. For each snapshot also: the settings parsed from 300 damaged copies, the attack telemetry in three variants, the plan export, and the manual queue merged in four variants.

| Outputs compared | 8,072 |
|---|---|
| Identical | 6,604 |
| Identical apart from the three settings of the removed planner mode | 1,396 |
| Different | 72, all of them settings parsed from damaged copies (see below) |

What the replay exercised, counted on the new tree: 3,139 plan steps reconciled, 337 fill steps, 1,037 shipments of which 189 loans, 788 furnace orders of which 46 for the empire and 107 overflow pauses or resumes, 1,396 attacks, 1,628 recycling runs, 2,720 ship orders, 96 field conflicts, and 34,781 actions of the rule-based planner.

### 2. Full planner and optimizer runs

On two snapshots: the AI-Planner with the live parameters, the AI-Planner from a cold start with another seed, the Config Optimizer from scratch, and the Config Optimizer continuing the live result. All eight results are identical between the versions, among them plans of 531, 529, 509 and 501 steps. The exported plan text and the reconciliation of the fresh plan are identical too.

### 3. The suites on the new code

Run in a private network namespace with a disposable database, so that nothing could reach the live stack.

| Suite | Result |
|---|---|
| Unit, reference outputs, integration, invariants | 771 passed |
| End to end | 159 passed |
| Notebook tests | 40 passed |
| Frontend tests | 113 passed in 22 files |
| Type check and build of the console | Clean |
| Static gates (`scripts/check.py --lint-only`) and generated docs | Pass |

### 4. Reference outputs recorded before the refactor

140 reference files were recorded on the old code before the refactor started (commit `18ef241`): the responses of 94 API commands against a fake game, plus planner, graph and parser cases. The new code reproduces 125 of them byte for byte. The other 15 differ only by the three removed settings and, in two files, by the counters of the old migration ledger.

These scenarios run with the adaptive switch off, so they cover the baseline and not plans v2 to v4.

### 5. The tests' expectations

A test suite that changes together with the code proves little, so the assertions were compared too. 719 tests have the same name before and after.

| Assertions in those tests before the refactor | 8,746 |
|---|---|
| Unchanged | 8,197 |
| Changed only by a renamed function or attribute | 343 |
| Dropped | 206 |
| Added | 94 |

The dropped ones were sorted by cause, and the ones no keyword explained were read. They concern the removed planner mode, the old database and its migration ledger, the configuration file, console checks that moved to the frontend suite, a file mirror of the attack history that no longer exists, and helper functions the tests no longer call directly. None concerns plans v2 to v4: the 100 assertions of `test_adaptive_v4.py` are unchanged, and the 134 of `test_adaptive_followup.py` are unchanged or renamed.

181 tests exist only before the refactor: 60 for the removed planner mode, 24 console tests that ran in Node, 47 end-to-end tests, and smaller groups for the old migrations, the configuration file and removed entry points. 199 tests exist only after.

### 6. Constants and numbers

- 14 constants kept their name and changed their value. None is a decision threshold: they are text patterns that share a name across modules, the database defaults, the container file name, and the list of planner modes.
- Across the package, leaving out the generated game data, the removed planner and the migrations, 3 distinct numbers disappeared and 8 are new. The three belonged to the removed planner mode. None of the eight is a threshold.

### 7. Read side by side

The parts of the bridge that carry plans v2 to v4, old against new: how one cycle's plan is put together, ordered and de-duplicated; the background re-tune with its triggers, gates and cooldown; the received raid income with its 24-hour window; the live attack telemetry; the gate that decides whether the AI-Planner is active; the 10-minute idle buckets; the two ship ledgers and which orders are debited from which. All the same.

## Differences found

| # | Difference | Effect |
|---|---|---|
| 1 | **Removed on purpose:** the older genetic-algorithm planner mode with its three settings, SQLite, the configuration file, two command-line entry points, and 12 of 72 API commands that nothing called | None on the two planners that run |
| 2 | **Four settings now have an upper bound.** A value above it is cut to the bound: `combat_safety_margin` 1,000; `hot_planet_solar_temp_threshold` 1,000; `min_free_fields_warning` 1,000; `max_actions_per_hour` 100,000. Before, larger values were accepted | None with the live values (1.4, 40, 20 and 2,000). Defaults and lower bounds are unchanged |
| 3 | **The guard around the fill rule is narrower.** Before, any error inside the fill rule was swallowed without a trace. Now a list of error types is caught and logged as a warning | Better in the common case: a failing fill rule is visible in the log. An error of another type, a `RuntimeError` for example, would now fail that planning cycle instead of only dropping the fills |
| 4 | **The background re-tune takes its locks differently** and logs a failure to start instead of ignoring it. The rule that decides when to re-tune is the same | None found |

One maintenance trap, with no effect today: seven constants of the fill rule and of the idle diagnosis are defined twice with the same value. The copies in `backend/planner/ai/_types.py` are imported by nothing. The ones in use are in `backend/planner/ai/live_fill.py`, `_fill_helpers.py` and `diagnostics.py`. Changing a copy in `_types.py` changes nothing.

## Not covered

These were screened by the constant and number comparisons and are exercised by the suites, but they were not read old against new:

- **The commander's tick:** which of the planned actions are executed in a tick, in what order and how many.
- **The execution of an action in the bridge,** apart from the ship ledgers.
- **The session executor:** how an action is carried out in the browser. Only the list of ship types a raid sends was compared; it is the same.
- **The database queries,** apart from the 24-hour income window.

The console was checked only through its own tests.

## The live stack

The refactored backend has run since 22:11 UTC on 2026-11-20, and the renamed package since 00:04 on 2026-11-21. At 02:52 the stack was healthy, the switch was on, and the last 12 recorded actions had all executed. That shows it runs. It is not a comparison: see [what else changed in the window](04-plan-v4-pooled-empire.md#what-else-changed-in-the-window).

## How to repeat it

The scripts are not in the repository. They need the repository's virtual environment and are on the operator's machine under `~/.gemini/jetski/brain/026d1b30-9575-4530-9d39-3a9b581bb0a6/scratch/`: `snap_fetch.py` saves a snapshot through cached reads, `brain_diff.py` replays the planner of one tree on a snapshot, `brain_cmp.py` compares two replays, and `audit_tools.py` holds the assertion, constant and number comparisons.

```bash
git worktree add --detach /tmp/before 9ee3597
git worktree add --detach /tmp/after HEAD
python3 snap_fetch.py snap.json
for t in before after; do
  PYTHONHASHSEED=0 python3 brain_diff.py --tree /tmp/$t --snap snap.json --out $t.json \
    --mutations 60 --seed 7 --config-fuzz 300
done
python3 brain_cmp.py before.json after.json --quiet
```

Add `--only-search` to `brain_diff.py` for the full planner and optimizer runs.
