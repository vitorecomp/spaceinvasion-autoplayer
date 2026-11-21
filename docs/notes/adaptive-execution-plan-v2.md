# Adaptive AI-Planner execution (Plan v2, v3 and v4): intent, what was built, and how to validate it

This note records why the adaptive execution work was done, what was planned, what actually landed, and the numbers taken before and after. Its main purpose is to let someone later answer one question without the original conversation: **did the plan work?** The procedure for that is in [How to validate](#how-to-validate).

All times are UTC on 2026-11-19 unless a date is given; everything about plan v4 happened on 2026-11-20 and says so. The operator's local time is UTC-03:00.

> [!NOTE]
> Since 2026-11-21 each plan has its own page in [docs/ai-strategy-plan](../ai-strategy-plan/README.md), with what it changed from the plan before and who decided what. This note stays as the measurement record and the validation procedure. It was written before the refactor of 2026-11-20 and 21: the file and function names in its "Where" columns are the old ones, and the [code map](../ai-strategy-plan/README.md#where-the-rules-live-in-the-code) gives the current ones. Commit hashes were updated to the ones on `main`.

## Status on 2026-11-21 at 02:41

- Plan v4 has run for 18.8 hours, 17.95 of them running. **No verdict yet**: it needs 24 hours. The reading and what else changed in those hours are in [plan v4](../ai-strategy-plan/04-plan-v4-pooled-empire.md#after-188-hours-not-a-verdict): builders busy 3.36 of 11, building levels 11.58 per hour, idle with an affordable step 49.0%, and Metal made at 48% of what the furnaces could make.
- The whole system was refactored during those hours. The planner's decisions were compared before and after and found the same: [check after the refactor](../ai-strategy-plan/05-refactor-check.md).
- Turning the switch off is now the only way back. Reverting the plan v4 commits no longer applies, because the files they changed were split up.

## Status on 2026-11-20 at 09:01

- The switch `ai_adaptive_execution` has been on since 14:36:33 on 2026-11-19. Plan v3 was live from 18:26:36 that day. **Plan v4 has been live since 07:51:42 on 2026-11-20** (commit `d233335`; two refinements in `676949b` live since 08:31:51).
- **Plan v3 did not make builders busier.** Over 11.18 running hours it had 1.76 of 11 builders busy, against 2.10 before any change. Colonies did advance faster: building levels went from 5.1 to 9.3 per hour and Metal made per hour from 50,600 to 245,400. 23 of the 111 levels were cheap Blast furnace levels.
- The review on 2026-11-20 found why (see [Review after twelve hours of plan v3](#review-after-twelve-hours-of-plan-v3)):
  - The empire is limited by its income, above all Metal, not by its builders.
  - Furnaces made 245,400 Metal per hour on average while they could make up to 427,000.
  - Every colony saved for its own next step. At 06:40 seven colonies sat idle, each 2 to 7 hours from its step, while the empire's stock covered all seven steps at once.
  - 61 of 88 transports were flying raids, so little could be shipped between colonies.
- Plan v4 changes four rules behind the same switch: furnaces smelt for the empire, a free builder starts a later plan step, colonies lend their savings in plan order, and the transport target leaves out hulls that are away on raids.
- First hour of plan v4, which is not evidence (read at 09:01, 1.06 running hours): 4.69 of 11 builders busy; 22 building levels completed, 19 per hour, 10 of them Blast furnace levels; furnaces on at 10 of 11 colonies; 411,200 Metal made per hour, 89% of what the furnaces could make at the time. The new furnace levels raised what the furnaces can make from 427,100 to 520,800 Metal per hour. Eight Large transports were ordered in the first ten minutes and none since the fleet ceiling was tightened. Loans are planned; whether one has flown cannot be read from the action log.
- Mark 1 is computed differently since 09:01 (see [Read the result](#read-the-result)). The first version divided the period's mean by the capacity at the moment of the run. The fill rule's own furnace levels made that read 79% an hour after the landing, a fail, while the furnaces were making 87% to 94% of their capacity at every reading. The script now compares each recorded point with the capacity at its own time and still prints the old figure.
- **No verdict on plan v4 is possible before 07:52 on 2026-11-21**, and not before 12 running hours.
- Outside this plan: another work session moved the stack into containers and the database to PostgreSQL between 06:56 and 07:22 on 2026-11-20. Since that move the recorded series has no points between 04:54 and 06:48 of that day, so the script now reports plan v3 over fewer hours than the figures above, which were taken at 06:15. The operator committed that work at 08:37 as `3ab9a0c`, on top of the plan v4 commits. Plan v4's code is unchanged in it; the rest of that commit was not reviewed here.
- Four branches named `refactor/p0-*` were created from `3ab9a0c` between 08:38 and 08:49, and two of them had commits by 09:02: the refactor of the whole system that another session planned has started. It will change the files plan v4 lives in during the 24 hours plan v4 is being measured.
- The operator asked for the review and the changes to be made without being asked for decisions. Every decision taken on their behalf is listed in [Decisions taken for the operator](#decisions-taken-for-the-operator).

## Status at the plan v3 hand-off (18:40)

- The switch `ai_adaptive_execution` has been **on since 14:36:33**. The plan v3 follow-up has been **live since 18:26:36** (commit `124bc85`).
- Working as designed:
  - Blast furnaces follow each colony's Metal need. Metal produced per hour while the bot ran went from 50,600 before the switch to 202,400 after it.
  - The planner and the ship budgets now use the raid income actually received. In the active plan, raid income pays for 6% of what the plan spends. Before the follow-up it paid for 112%.
  - Ship spending is capped per hour, and the transport fleet has reached the size the plan asks for.
- Not solved: **colonies still wait.** 71% of colony time is spent idle while some other target step is affordable. The plan computed on real income still waits for each colony's top-ranked step.
- **No verdict is possible yet.** With plan v2 alone the bot ran 2.43 hours and had 3.07 of 11 builders busy, against 2.10 before. With plan v3 it has run 0.13 hours.
- Two things outside this plan change the numbers from about 18:35 on. Another work session raised the page-view limit from 24 to 60 per minute, and it is still editing the API. Neither change is committed or reviewed here.

## Intent

The AI-Planner produces a schedule of builds and research that keeps every colony's builder busy. Live, the colonies were idle most of the time: about 2 of 11 builders were busy on average.

The main idea of the work: **the planner can only adapt to what its model sees.** So the order was:

1. Make the live empire do what the plan already says (furnaces, research, ship counts).
2. Give the empire the cargo capacity the plan assumes.
3. Decide with data what else the planner must learn.

The goal is colonies that advance as fast as the planner intends, without hand fixes.

## What was planned

A first plan (v1) proposed re-running the planner's decision step on live state every cycle. It was tested before any code was written and dropped: on a live snapshot with 9 of 11 builders free and 8 colonies able to pay for 44 steps, that decision step started 1 build, at patience 0, 0.25 and 1.0 alike.

The measurements that shaped the second plan (v2), taken between 06:39 and 07:45:

- The saved plan said to turn every furnace on at once. All 11 were off. Six of nine idle colonies were waiting for Metal their own furnace could make in 1 to 3 hours.
- The simulator ships any amount of cargo after a lead time. The empire owned 14 Small transports, 84,000 cargo in total.
- Research started late: offers were only trusted when read on the lab colony.
- Shipyards refused to build while any fleet was away, because ships in flight were not counted as owned.

Plan v3 keeps the steps of v2 and adds five decisions, taken after four hours of running v2 (see [Why the follow-up](#why-the-follow-up)).

| Step | Plan | Status |
|---|---|---|
| 0 | One switch, off by default, plus measurement | Built (v2) |
| 1 | Furnaces follow the planner, per colony | Built (v2) |
| 2 | Research starts when affordable | Built (v2) |
| 3 | Ships in flight count as owned | Built (v2) |
| 4a | Transport fleet sized by the plan | Built (v2), corrected (v3). Fleet at target since 18:27 |
| 4b | Cargo limit inside the simulator | Not built; decided after 4a has run |
| 5 | Fill idle builders, pre-queue the second build slot | Not built. The data so far points to building the fill rule |
| 6 | Background re-tune on drift | Built (v2), corrected (v3). Live re-evaluation every cycle was not built |
| v3 D1 | Raid income is what was received, not what a model predicts | Built |
| v3 D2 | Re-tune once when that basis changes; stop re-tuning on the median loot of a raid | Built |
| v3 D3 | Ship budgets are hourly rates on real income | Built |
| v3 D4 | Plan-sized transports are ordered in batches, ahead of fleet missions | Built |
| v3 D5 | A fleet measured as idle does not raise its own target | Built |

## What was built

Every behaviour below is inactive while `ai_adaptive_execution` is off.

### Plan v2, commits `3442591..292edc2`

| Step | Behaviour | Where |
|---|---|---|
| 0 | `ai_adaptive_execution` (default off) on the Config page and as a toggle in Action Graph Tab 3 | `core/config.py`, `AiPlannerTab.tsx` |
| 0 | `/dashboard` returns up to 1,440 points of the per-minute builder series (was 120). Points exist only for minutes the bot ran | `hourly_production_history` in `runtime/api_server.py` |
| 0 | Per colony: building, ready not started, waiting for Metal, Pig iron, Kryptonite or Spice, cargo inbound, storage too small, field conflict. Shown in Tab 3 and counted in 10-minute buckets for the last 24 hours | `diagnose_ai_colony_idle_states`, `adaptive_status` in `ai_planner_get` |
| 1 | Furnace on when the colony's waiting AI steps need more Metal than it has plus friendly cargo flying to it, and the step's queue frees no later than smelting needs. Stays on while short. Off otherwise | `_adaptive_colony_smelt_decision`, `plan_ai_blast_furnace_throttles` in `planner/strategy.py` |
| 1 | Donor rule: a covered colony with at least one transport and ample Pig iron also smelts when the empire's waiting steps need more Metal than exists | same |
| 2 | Cached research offers are trusted only when the lab colony is active; otherwise the start is allowed when affordable and unblocked | `reconcile_live_ai_actions` in `planner/ai_planner.py` |
| 3 | Owned ships are parked plus in flight. The old hold stays only when flight details are missing or cut off | `count_in_flight_ships_by_planet` in `core/state.py` |
| 4a | Target cargo = planned cargo per hour over 24 h, divided by round trips per hour, divided by utilization. The shortfall becomes Large transport targets, split across shipyard colonies | `transport_demand` in `run_ai_planner`, `compute_infra_transport_targets` |
| 4a | Those builds are tagged `[Plan infra cargo target]` and paid only from a separate allowance: 25% of hourly Metal and Kryptonite income, with a spend ledger and up to 2 hours of carry-over | `compute_spare_ship_budget`, `filter_shipyard_actions_by_spare_budget` |
| 6 | Re-tune in the background when the Config Optimizer result or colony count changes, loot shifts by 25%, or the plan is 4 hours old. 30-minute cooldown. Starts from the saved genes | `_evaluate_ai_drift_replan_locked` |

### Plan v3 follow-up, commit `124bc85`

| Decision | Behaviour | Where |
|---|---|---|
| D1 | Raid income is the loot and recycling received over the last 24 hours, from the `loot_recycle_hourly` table. Income per fleet slot is that figure divided by the current slots. The old model stays in use when the switch is off or no loot ledger exists | `compute_attack_telemetry(realized_hourly_loot=...)`, `run_ai_planner`, `_get_realized_loot_hourly_locked` |
| D1 | The stored telemetry carries `income_basis` (`realized_24h`, `attacks_per_day`, or absent for the model) and keeps the model's figure as `modelled_hourly_loot` | same |
| D2 | One background re-tune, reason `income_basis_changed`, when live telemetry is on the realized basis and the active plan is not. A plan on the realized basis is not re-tuned when the median loot of a raid moves; the 4-hour age rule covers it | `_evaluate_ai_drift_replan_locked` |
| D3 | Both ship allowances are shares of mine production plus received raid income | `_overlay_realized_loot_locked` |
| D3 | The spare % budget is an hourly rate that can save up for 4 hours, less what it paid for in those 4 hours. It used to be a fresh allowance on every planning call | `compute_spare_ship_budget(spare_spend_in_window=...)`, `_record_spare_ship_spend_locked` |
| D4 | One Weapons factory order for up to 10 plan-sized hulls, shrunk to what the 25% allowance and the colony's stock cover. The whole order is debited | `_plan_planet_shipyard_targets`, `filter_shipyard_actions_by_spare_budget`, `_record_infra_transport_spend_locked` |
| D4 | Among P2 actions, plan-sized transport orders run before spy, transport, station, attack and recycle missions | `_hybrid_subrank`, `_fleet_subrank` |
| D5 | Sizing never uses a utilization below 0.5. Measured utilization can still shrink the target | `compute_infra_transport_targets`, `_build_transport_fleet_summary_locked` |
| D5 | Hulls ordered in the last hour count toward the fleet before they leave the Weapons factory | `_get_infra_cargo_ordered_last_hour_locked` |

### Plan v4, commits `d233335` and `676949b` (2026-11-20)

| Decision | Behaviour | Where |
|---|---|---|
| E1 | **Furnaces smelt for the empire.** A colony whose own steps do not ask for smelting smelts while the waiting steps of all colonies cost more Metal than the empire holds, counting cargo in flight. A furnace that is on stays on until the pooled Metal is 10% above that cost | `_adaptive_colony_smelt_decision`, `plan_ai_blast_furnace_throttles` in `planner/strategy.py` |
| E1 | Not when Pig iron is the scarcer resource: the waiting steps of all colonies cost more Pig iron than the empire holds, or the colony has no Pig iron above the cost of its own steps (50,000 or one hour of smelting to start, anything above zero to continue). Not when the colony lacks the energy for the furnace | same |
| E1 | A transport on the colony is no longer required. A colony whose queue is busy no longer waits for its own step to come up before it smelts for the others | same |
| E2 | **Live fill rule.** A colony whose builder is free while its next plan step waits for resources starts the first later step of its plan that is ready, that it can pay for now, and that passes the two rules below. At most one per colony. The waiting step keeps its place, so transports and furnaces still serve it | `plan_live_fill_actions`, called at the end of `reconcile_live_ai_actions` in `planner/ai_planner.py` |
| E2 | The fill must not hold the waiting step back by more than `patience` times its own duration. The waiting step is expected to start at the earlier of two moments: when the colony can pay for it alone, or when the empire can pay for it in plan order (all stock pooled, earlier steps first, never sooner than 20 minutes). `patience` is the plan's tuned value, kept between 0.25 and 0.75. A research step waiting on the colony gets the same protection. A resource the colony does not produce is never touched if the waiting step lacks it | same |
| E2 | The fill spends nothing the empire is short of: for each resource it costs, the empire's stock plus cargo in flight must cover every colony's next step, the research step, the fills already chosen, and the fill | same |
| E3 | **Plan-order loans.** Shipments between colonies serve AI-Planner needs in plan order. A colony that cannot yet pay for its own next step lends what it holds back for it to a waiting step that comes earlier in the plan. The shipment's reason carries `[Plan order loan]` | `plan_interplanet_transports` in `planner/strategy.py` |
| E3 | A loan is used only when spare stock cannot cover the need. It covers only what the borrower's own building step lacks, not research and not a need that is not due yet. The lender keeps what it holds for a step it can already pay, for research, its cushion and its fuel. A loan is flown when it pays for the whole need, or carries at least 20,000 and a quarter of it. It is not flown for an amount the borrower makes itself before the cargo could land (`676949b`) | same |
| E4 | **The transport target leaves out raiding hulls.** Hulls on an Attack flight, out or back, no longer count toward the plan-sized logistics fleet. The whole fleet, raiding hulls included, stops growing at twice the target, and never more than 900,000 cargo (30 Large transports) above it (`676949b`) | `compute_infra_transport_targets`, `_transport_hulls_on_raids` in `planner/strategy.py` |

Unchanged on purpose: furnaces are 100% or 0% only, with the Metal-overflow pause at 100% storage and resume below 95%; the simulator's maths and the 14-line export header; the page-view limit and every anti-bot safeguard; how raids choose targets and ships. Demolitions were added by another work session and are not part of this plan.

### Tests

- Plan v2, as reported by the verifier agent at `292edc2` and not re-run for this note: 664 unit tests with the same 11 failures as before the change, 62 frontend tests passing, 12 of 12 in two end-to-end files, database integrity check passing. With the switch off, the old and new code produced identical actions on a live snapshot.
- Plan v3, run for this note in a clean worktree of the commit: 675 unit tests, 3 failing, the same 3 that fail without the change (`test_commander_backs_off_at_least_retry_after_on_rate_limit`, `test_notebook_is_saved_without_outputs`, `test_run_stack_and_game_api_main_redaction_reload_sigterm_and_cleanup`). The 11 new tests are in `tests/unit/test_adaptive_followup.py`.
- Not run for v3: the end-to-end suite and the frontend tests. No frontend file was changed.
- Plan v4, run in a clean worktree of commit `16f95a4` plus the change: 703 unit tests, 3 failing, the same 3 as above. The 28 new tests are in `tests/unit/test_adaptive_v4.py`; the donor test in `tests/unit/test_strategy.py` was changed to pin the new furnace rule. The last full run was made just before a three-line guard was added around the fill rule; after it, `test_ai_planner`, `test_adaptive_v4` and `test_adaptive_followup` were run again (64 tests, all passing).
- Plan v4 against the other session's uncommitted code: the patch was applied to a copy of the working tree and `test_adaptive_v4`, `test_adaptive_followup` and `test_strategy` were run there (112 tests, all passing). The full suite was not run on that copy.
- Plan v4 on live data, without touching the game: old and new code were run on the same cached snapshot taken at 07:29 on 2026-11-20. Old code: 12 planner actions, 2 shipments, no furnace change. New code: 4 fills, 2 loans that each pay for a whole step, 2 furnaces turned on.
- Refinements in `676949b`: the full unit suite was run again in a clean worktree, 705 tests, the same 3 failing. `tests/unit/test_adaptive_v4.py` now holds 30 tests. On a live snapshot taken at 08:25 the refinements changed no action.
- Not run for v4: the end-to-end suite and the frontend tests. No frontend file was changed.
- Plan v4 inside the operator's commit `3ab9a0c`, which holds it together with the other sessions' work: the unit suite was run in a clean worktree of that commit at 08:43 on 2026-11-20. 720 tests, 14 failing, none of them caused by plan v4:
  - 8 test `notebooks/optimizer_experimentation.ipynb`, which that commit deletes.
  - 4 are tests of the API and the console: `test_demolish_building_command_guards_cache_invalidation_and_null_planet_offers`, `test_perf_and_stability_regressions_toctou_plan_fastpath_unknown_cmd_and_cmd_reconnect`, `test_r7_api_rejects_unicode_digits_bad_fleetsave_reserve_and_closes_bridge` and `test_favicon_top_bar_notifications_and_auto_help_popup_persistence`. They fail in the same way with both plan v4 commits reverted in that worktree.
  - 2 failed before this work: `test_commander_backs_off_at_least_retry_after_on_rate_limit` and `test_notebook_is_saved_without_outputs`. The third old failure, `test_run_stack_and_game_api_main_redaction_reload_sigterm_and_cleanup`, passes on that commit.
  - One test was left out: `test_postgres_podman_migrations_concurrent_mvcc_sync_and_live_bridge_switch` creates and drops a database on whatever PostgreSQL answers on port 5433, which is the live one.
  - The console tests that run in Node were run a second time after `frontend/node_modules` was linked into the worktree; without it 20 more of them fail on a missing module.
  - `git revert --no-commit 676949b d233335` applied cleanly on `3ab9a0c`, so the way back worked at that commit. It does not after the refactor.
- The new arithmetic of mark 1 in the script was checked on made-up series: a constant capacity, a capacity that doubles between two readings, points outside the readings, and a capacity file with unreadable lines.

### To turn it off

The Tab 3 toggle, or `{"command": "update_config", "config": {"ai_adaptive_execution": false}}`. The previous rules take over on the next planning cycle. A plan already computed on real income stays active until the planner runs again.

## Why the follow-up

Measured between 17:30 and 18:25, with plan v2 running.

| Finding | Measured |
|---|---|
| The planner assumed every fleet slot raids back to back | 989,000 to 1,073,000 Metal per hour of raid income in the plan, from 65 or 66 slots, a 5.6 to 6.0 minute round trip and 1,515 Metal per raid |
| What the empire received | 47,000 to 53,000 Metal per hour over the last 24 hours, from 4 to 7 raids and 80 to 95 recycling runs. About 20 times less |
| The plan was paid for by that income | Raid income covered 94% to 112% of everything the active plan spends in 26 weeks. Furnaces smelted 123 to 141 of 4,368 hours |
| The spare % ship budget was not a budget | 124,000 Metal allowed on each planning call, on the modelled income, with no memory of earlier spending. Furnaces made 122,000 to 240,000 Metal per hour |
| The 25% transport allowance was computed on the same income | Between 165,000 and 607,000 Metal allowed at the five times it was read |
| Step 3 had removed the only brake on shipyards | Before the switch, the "fleet in transit" hold kept shipyards idle. After it, combat ships were ordered whenever a call allowed it |
| An idle-looking fleet raised its own target | Measured utilization of 0.26, then 0.2, took the target from 1.63 million cargo to 3.12 and then 4.07 million, about 135 Large transports |
| The re-tune chased noise | At 17:56 the plan was re-tuned because the median loot of a raid had moved 25%. It moved 33% again within 25 minutes |
| Ship orders were starved of page views | One hull per order, after every fleet mission. One and five ship orders went through in the two rounds that could be seen |

What a dry run of the planner on real income showed, on the cached live state and without touching the game:

| Run | Blast furnace upgrades in the first 60 steps | Steps started in the first hour | In 24 hours | Planner score |
|---|---|---|---|---|
| Model income, 18:14 | at most 1 | 13 | 255 | 9.384 billion |
| Real income, dry run at 18:14 | 25 | 32 | 194 | 7.183 billion |
| Real income, live re-tune at 18:27 | 0 | 6 | 125 | 7.216 billion |
| Real income, dry run at 18:31 | 0 | 6 | 151 | 7.207 billion |

The three plans on real income score within 0.5% of each other. **The planner's score does not separate a plan that keeps builders busy from one that waits.** Which one it returns is close to chance.

## Review after twelve hours of plan v3

Measured on 2026-11-20 between 06:15 and 07:30. Plan v3 had been live for 11.9 hours, 11.18 of them running.

| Finding | Measured |
|---|---|
| Builders were not busier | 1.76 of 11 busy while running (median 1, maximum 8). Before any change: 2.10. With plan v2 alone: 3.07 over 2.43 hours |
| Colonies still advanced faster | 111 building levels in 11.9 hours, 9.3 per hour, against 5.1 before. 23 of them were Blast furnace levels |
| The live empire kept to its plan | 11 steps built of 13 the plan had scheduled to be finished, 1.7 hours into that plan. The plan itself schedules few steps |
| The empire is limited by income, not by builders | Of the income of 12.5 hours, about 92% of the Pig iron, 80% of the Metal and all of the Kryptonite had been spent. A Development center level 10 occupies a builder for 36 minutes and costs 307,200 Metal, about an hour of the empire's Metal income |
| Metal is what colonies wait for | 61% of colony time waiting for Metal; 78.6% idle while some target step is affordable |
| Furnaces ran below what they can make | 245,390 Metal per hour on average while running. With every furnace on they make 427,080 (the furnaces grew during the period, so the average capacity was lower). Readings of the moment: 4 of 11 off at 06:42, 2 of 11 off at 07:29 |
| Why | Each colony smelted only for its own next step, and only once its queue was about to be free. The rule that lets a colony smelt for the others required a transport on that colony, and was skipped whenever the colony had a need of its own |
| Every colony saved for its own step | At 06:40 seven colonies were idle, 2.3 to 7.2 hours from their step. The empire's stock, pooled, covered all seven steps at once and left 80,000 Metal |
| The plan's order did not hold between colonies | One colony held 398,000 Metal for the last step of the plan (number 718 of 724) while steps 17, 22, 23 and 24 waited for Metal elsewhere. A colony gave away nothing its own step needed |
| The transports were on raids | 61 of 88 transports were flying raids and 9 were parked. A raid takes every parked transport of its colony |
| Raids return full | The four raids on Spacia planets seen on their way back each carried exactly their cargo capacity (42,440 to 277,120). The 31 Spacia targets with a spy report held 191 million resources and showed no fleet or defence |
| Raid loot is still a small part of income | 1.08 million stolen in 24 hours by 26 raids, 41,500 per raid. Recycling brought 6.9 million in 114 runs |
| Small transports slow raids down | Round trip from 02:380:08 to 02:340:02 as the planner computes it: 2.08 hours for fighters, 2.36 for Large transports, 3.93 once a Small transport is in the fleet |
| The fleet target ratcheted | The plan's cargo per hour went from 274,653 to 1,022,115 between re-tunes, and the target from 193,058 to 1,102,725 cargo. 37 Large and 22 Small transports were bought in 12 hours |
| Stops | The bot was stopped for 7.8 of 28.8 recorded hours (27%) |

Two conclusions drawn before this review were wrong and were dropped:

- "The fleet is over-bought." Its measured utilization was 3%, but that figure counts shipments between colonies only. The hulls were not idle: they were on raids and came back full.
- "Raids should leave the plan's Large transports at home." Each Large transport on a raid brings back 30,000 resources per trip and costs 14,000. Taking them off raids would have lowered income.

## Plan v4: what was decided and why

| # | Decision | Why |
|---|---|---|
| E1 | Furnaces smelt while the empire is short of Metal, wherever there is Pig iron to spare | Metal is what steps wait for, 12.8 million Pig iron sat in stock, and the empire's waiting steps cost 1.6 million more Metal than it held |
| E2 | A free builder starts a later step of its own plan when that costs the waiting step nothing | Blast furnace levels and mines cost Pig iron and Kryptonite, not Metal. On the 07:29 snapshot four colonies could start one |
| E3 | Colonies lend savings to steps that come earlier in the plan | The plan is computed as if the empire's stock were one pool. Live, each colony was an island |
| E4 | The logistics target counts only hulls that are not on raids, with a ceiling at twice the target | Raids take every parked hull, so the fleet the plan sized for shipments was not there for them. Extra hulls also raid, and raids return full |
| - | Raids keep taking transports | See the two dropped conclusions above |
| - | The operator's 10% spare budget and its 4-hour window stay | They are the operator's setting; ship spending stayed inside them |
| - | The simulator and the planner's score are not changed | Another session has uncommitted changes in the planner, and the live rules now cover what the score does not separate |

### Decisions taken for the operator

The operator asked on 2026-11-20 for the best result without being asked. These were decided by the assistant and can each be reversed.

| Decision | Value | To reverse |
|---|---|---|
| Everything in plan v4 | Behind `ai_adaptive_execution` | Turn the switch off. Reverting commits `676949b` and `d233335` worked until the refactor and no longer applies |
| Pass marks for plan v4 | Listed under [How to validate](#how-to-validate) | Constants `V4_*` in `scripts/validate_adaptive_execution.py` |
| Furnace hold band and Pig iron reserve | 10% and 50,000 | `EMPIRE_SMELT_HOLD_RATIO`, `EMPIRE_SMELT_MIN_SPARE_PIG` in `planner/strategy.py` |
| Fill patience bounds and the pooled lead time | 0.25 to 0.75, 20 minutes | `AI_FILL_MIN_PATIENCE`, `AI_FILL_MAX_PATIENCE`, `AI_FILL_POOL_LEAD_HOURS` in `planner/ai_planner.py` |
| Smallest loan | 20,000 cargo and a quarter of the need, unless it pays for all of it | `PLAN_ORDER_LOAN_MIN_CARGO`, `PLAN_ORDER_LOAN_MIN_SHARE` in `planner/strategy.py` |
| Fleet ceiling | Twice the plan's cargo target, and at most 900,000 cargo above it | `INFRA_TRANSPORT_FLEET_CAP_RATIO`, `INFRA_TRANSPORT_RAID_ALLOWANCE_CARGO` in `planner/strategy.py` |
| Transports are not reserved from raids | Raids take every parked hull, as before | Not built; see [Not done](#not-done) |

## Timeline

| Time (UTC) | Event |
|---|---|
| 06:39 to 07:45 | Diagnosis and review of plan v1 |
| 12:56:58 | First plan v2 code merged into `main`; switch off |
| 14:11 | Last plan v2 fix commit, `292edc2` |
| 14:25 | Planner run by hand |
| **14:36:33** | **Switch turned on** through the local API, at the operator's request |
| 14:37:08 to 14:37:30 | Four furnaces set to 100% |
| 14:38:20 | Anti-bot gate; bot stopped until 16:05 |
| 16:07 | Planner run by hand |
| 16:31 | API restarted with uncommitted changes from another work session (dashboard screens) |
| 16:43:59 | First Large transport ordered, on 02:380:12. It left on a raid 81 seconds later |
| 17:56 | First background re-tune: median loot of a raid had moved 25% |
| **18:26:36** | **Plan v3 follow-up live** (planner files at 18:26:14, API at 18:26:36) |
| 18:26:52 | Background re-tune started on its own, reason `income_basis_changed`. New plan active at 18:27:24 |
| 18:29 to 18:31 | Furnaces set to 100% on four more colonies |
| 18:34 | API restarted after edits from the other work session to `core/config.py` and `session/client.py` |
| 18:35 | The whole stack was restarted, not by this work. Between 18:34 and 18:36 the page-view limit went from 24 to 60 per minute |
| 2026-11-20 03:22 | Anti-bot gate; bot stopped until 03:52 |
| 2026-11-20 06:15 to 07:30 | Review of plan v3 (this note's review section) |
| 2026-11-20 06:56 to 07:22 | Another work session moved the stack into containers with a PostgreSQL database. The recorded series has had no points between 04:54 and 06:48 since |
| **2026-11-20 07:51:42** | **Plan v4 live** (commit `d233335`); API and commander restarted by the file change |
| 2026-11-20 07:53 to 08:05 | Six steps started by the fill rule: a Kryptonite mine on 02:381:07, two Blast furnace levels on 02:379:05 and three on 02:380:08. Two furnaces turned on for the empire. Eight Large transports ordered |
| 2026-11-20 08:02:30 | API restarted by the other session's edits to `db/storage.py` and `runtime/api_server.py` |
| 2026-11-20 08:21 | A shipment from 02:381:08, a lending colony, to 02:381:07 (step 18). The action log does not record whether it was the planned loan |
| 2026-11-20 08:31:51 | Refinements live (commit `676949b`): the fleet allowance and the loan guard |
| 2026-11-20 08:32 to 08:35 | The plan re-tuned itself in the background (its research step moved from number 100 to number 60). Six more steps started in those minutes |
| 2026-11-20 08:37 | The operator committed the other sessions' work as `3ab9a0c`, on top of the plan v4 commits. No file on disk changed, so nothing restarted |
| 2026-11-20 08:38 to 08:49 | Four `refactor/p0-*` branches created from `3ab9a0c` by the session that planned the refactor of the whole system |
| 2026-11-20 09:01 | Mark 1 of the script now compares each point with the furnace capacity at its own time |

## Baseline, before the change

| Measure | Value | How it was taken |
|---|---|---|
| Builders busy while the bot ran | **2.10 of 11** (19%) | Time-weighted over 6.24 running hours, 319 points, 01:26 to 12:57. Median 1, maximum 8 |
| Metal produced per hour while the bot ran | 50,556 | Mean of the 405 points before the switch. Median 0 |
| Colony time waiting for Metal | 81.8% | 9 ten-minute buckets, 99 colony samples, read at 14:28 with the switch still off |
| Colony time idle with an affordable step | 81.8% | same |
| Colony time ready but not started | 0% | same |
| Furnaces on | 0 of 11 | Checked at 06:39, 06:41, 07:07, 07:11, 07:36 |
| Transports | 14 Small, 0 Large, 84,000 cargo | Parsed from live flights |
| Building levels completed, all colonies | 1,057 at 06:44 | Sum of `fields_used`, which equals the sum of building levels on every colony |
| Time the bot was stopped | 5.5 of 13.2 recorded hours (42%) | Three gaps in the series between 01:26 and 14:36, each matching an anti-bot gate alert |

"Running" means consecutive points of the series are at most 5 minutes apart. A longer gap counts as stopped.

## Readings with the switch on

### Plan v2 alone, 14:36:33 to 18:26:36

| Measure | Value |
|---|---|
| Builders busy while the bot ran | 3.07 of 11, over 2.43 running hours and 124 points. Median 2, maximum 7 |
| The same figure as it was read during the period | 5.06 at 16:40 (0.66 h), 4.14 at 17:29 (1.48 h), 3.08 at 18:25 (2.41 h) |
| Metal produced per hour while the bot ran | 202,371. Median 226,600 |
| Furnaces on | Between 4 and 9 of 11 |
| Transports at 18:25 | 27 Small and 3 Large, 252,000 cargo, every hull in flight |
| Building levels completed | 1,117 at 18:22 |
| Colony time idle with an affordable step | 71.3% over 25 buckets, 9 of them from before the switch |

The first reading of 5.06 was a burst after an 87-minute stop and two planner runs by hand. It did not hold.

### Plan v3, first readings at 18:36

| Measure | Value |
|---|---|
| Builders busy while the bot ran | 3.27 of 11, over 0.13 running hours and 7 points. Not evidence |
| Raid income basis | Live telemetry and active plan both `realized_24h` |
| Raid income in the active plan | 52,862 Metal per hour. The model would have said 547,726 |
| Share of the plan's spending paid by raid income | 6% |
| Furnace smelting in the active plan | 406 of 4,368 hours |
| Cargo the plan wants shipped | 274,653 per hour (1,081,614 before) |
| Transport fleet | 264,000 cargo against a target of 193,058. No plan-sized order pending |
| Spare % ship budget | 27,992 Metal per hour. 37,000 Metal spent in the first 10 minutes and 66,000 in the first 20, out of a 4-hour allowance of about 112,000 |
| 25% transport allowance | About 70,000 Metal per hour; 57,979 left for the hour |
| Furnaces on | 7 of 11 |
| Blast furnace levels, all colonies | 150 |
| First steps of the active plan | 6 in the first hour, 31 in 4 hours, 125 in 24 hours. No Blast furnace upgrade before hour 24 |

### Plan v3, read at 06:15 on 2026-11-20

| Measure | Value |
|---|---|
| Builders busy while the bot ran | 1.76 of 11, over 11.18 running hours. Median 1, maximum 8. 2.49 over the last 6 hours |
| Building levels completed | 1,228, up 111 in 11.9 hours: 9.3 per hour |
| Blast furnace levels, all colonies | 173 |
| Metal produced per hour while the bot ran | 245,390. Median 259,780 |
| Colony time | Building 18.6%, waiting for Metal 60.9%, cargo inbound 13.8%, ready but not started 1.9%. Idle with an affordable target step 78.6% |
| Plan adherence | 11 built of 13 scheduled to be finished, 1.7 hours into the plan |
| Raid income | Basis `realized_24h` in telemetry and plan. Received per hour: Pig iron 56,760, Metal 69,710, Kryptonite 142,266, Spice 52,779. It pays for 7% of the plan's spending |
| Stock | Pig iron 10.0 million, Metal 1.32 million, Kryptonite 3.40 million, Spice 2.76 million |
| Transports | 48 Small and 40 Large: 1,488,000 cargo against a target of 1,102,725. Delivered between colonies: 140,803 per hour, 13.8% of the plan |
| Spare ship budget | 128,000 Metal and 65,000 Kryptonite spent in the last 4 hours; nothing left available |
| Stopped | 7.8 of 28.8 recorded hours |

Since the database move of 2026-11-20 the script reports this period as 1.50 builders over 10.96 running hours and 257,460 Metal per hour, because about two hours of its points are no longer in the series.

### Plan v4, first twenty minutes (07:52 to 08:10 on 2026-11-20)

Not evidence. Recorded to show the rules fire.

| Measure | At 07:52 | By 08:10 |
|---|---|---|
| Builders busy | 0 of 11 | 5 of 11 at 08:01, 3 of 11 at 08:10 (the furnace levels take 4 to 10 minutes) |
| Furnaces on | 8 of 11 | 10 of 11 |
| Metal per hour | 308,500 | 391,440. The furnaces could make 427,080 before the new levels |
| Building levels completed | 1,242 | 1,249 |
| Started by the fill rule | | Kryptonite mine level 18 on 02:381:07; Blast furnace levels 18 and 19 on 02:379:05; Blast furnace levels 16, 17 and 18 on 02:380:08 |
| Furnace orders | | 02:381:10 and 02:381:05 turned on for the empire. 02:380:06 turned off because its own step waits for Pig iron |
| Large transports ordered | | 8, on 02:380:12 and 02:380:08 |
| Loans | | Four in the plan at 08:10, none flown yet: their colonies had no free fleet slot. One of them sends 13,765 Metal and 21,155 Kryptonite to step 18, whose colony had spent Kryptonite on a fill |
| Other shipments flown | | 90,000 Pig iron to 02:380:06 for a Pig iron mine, and two more |
| Stock against the cost of every colony's next step | | Metal 1.25 million against 2.85 million. Pig iron 12.8 million against 1.0 million |

### Plan v4, first half hour (read at 08:24 on 2026-11-20)

Not evidence either: half an hour, right after a burst of cheap steps.

| Measure | Value |
|---|---|
| Builders busy while the bot ran | 3.63 of 11 over 0.50 running hours, 22 points. Median 4, maximum 5 |
| Metal per hour | 409,820 at 08:24; 377,490 on average over the period, 81% of what the furnaces could make |
| Building levels completed | 1,252, up 10 in 32 minutes |
| Plan adherence | 27 built of 32 scheduled to be finished; 27 more built ahead of their scheduled start |
| Loans | Three in the plan. A shipment from a lending colony to the colony of step 18 flew at 08:21; the action log does not say whether it was the loan |
| Blast furnace levels built while a Development center step waited | 02:379:05 from level 17 to 20, 02:380:08 from level 15 to 19 |
| Large transports | 47, up from 40 |
| Colony time idle with an affordable step, last 24 hours | 78.25%. The window is still almost all plan v3 |
| Stops and gates | One stop of the series, from 08:13 to 08:19. Its cause was not established. No anti-bot gate |

### Plan v4, first hour (read at 09:01 on 2026-11-20)

Not evidence: one hour, and it began with a burst of cheap steps.

| Measure | Value |
|---|---|
| Builders busy while the bot ran | 4.69 of 11 over 1.06 running hours, 44 points. Median 5, maximum 8 |
| Building levels completed | 1,264, up 22 in 1.2 hours, which is 19 per hour. 10 of the 22 are Blast furnace levels |
| What the furnaces can make, Metal per hour | 427,080 at the landing; 466,600 at 08:23; 476,620 at 08:39; 512,440 at 08:53; 520,800 at 09:01 |
| Metal made per hour | 489,360 at 09:01, with 10 of 11 furnaces on. 411,190 on average over the period: 89% of what the furnaces could make at the time, between 86% and 90%. Against the capacity at 09:01 alone it reads 79% |
| Transports | 48 Small and 48 Large, all flying. 34 Small and 25 Large on raids; 14 Small, 23 Large and the Transmitter on shipments between colonies. At 07:58 ten Large transports were on shipments |
| Fleet size | 1,978,000 cargo. The plan's target fell to 783,000 at the 08:32 re-tune, which puts the ceiling at 1,566,000. Nothing was ordered after the first eight |
| Cargo delivered between colonies, last 24 hours | 191,000 per hour. It was 140,800 after twelve hours of plan v3 |
| Empire stock against the cost of every colony's next step | Metal 1.3 million against 2.9 million. Pig iron 13.0 million against 0.5 million |
| Colony time idle with an affordable step, last 24 hours | 77.4%. The window is still almost all plan v3 |
| Stops and gates | Three stops of the series of 5 to 8 minutes, from 08:13, 08:21 and 08:50. Their causes were not established; no watched file changed after 08:31:51. No anti-bot gate |

## Known problems

Plan v4, as of 2026-11-20 09:01:

- **Builders will stay partly idle.** Income decides how many steps can be paid for. Plan v4 uses the income better; it does not create more.
- **Raids take every transport that is parked.** Loans and other shipments happen only in the minutes a colony's hulls are home. Shipments are planned before raids, so they get the hulls first.
- **A fill can leave its colony short of Kryptonite or Pig iron for the waiting step.** The rule allows it when the colony earns the amount back before the waiting step could start, or when the empire holds a surplus of it. If a loan then arrives early, the step waits for that resource.
- **The transport fleet grew again.** Eight Large transports were ordered in the first ten minutes, 64,000 Metal and 48,000 Kryptonite from the 25% allowance, which the buildings did not get. The ceiling was then tightened to 900,000 cargo above the target. At 09:01 the fleet held 1,978,000 cargo against a ceiling of 1,566,000, so it grows further only if the plan's target rises above about 1.1 million.
- **The measurement overlaps a refactor.** The refactor of the whole system started on branches at about 08:45 on 2026-11-20. When its changes reach `main` they restart the stack and may change what the planner does. Period E is then no longer plan v4 alone. Note what landed and when before judging.
- **Mark 1 depends on capacity readings.** The series does not record what the furnaces could make. The script knows it at the four moments it was run in the first hour (kept in the script), at every later run on this machine (it keeps them in `~/.cache/spaceinvasion-autoplayer/furnace_capacity_readings.jsonl`), and at the run itself. Between two readings it only knows the capacity lay between them. With few readings the range can cross the mark, and the script then says the mark is not settled.
- **The Operator Console shows the old count.** Its fleet line compares all hulls with the target, so it can say the target is met while orders continue.
- **A loan can be small.** One of 938 Metal was planned at 08:24 because it paid for a whole step. The guard only drops a loan the borrower would not need by the time it lands.
- **Executed fills and loans cannot be counted afterwards.** The action log keeps the result message, not the reason. The script counts the ones in the current plan.
- **The fill rule fails silently.** An error inside it is swallowed so that the plan's own steps still run. The sign would be no fills over a long time.
- **A restart can stop the bot for several minutes** while the bridge reads every colony again: about 8 minutes after the restarts at 07:22 and 07:51 on 2026-11-20, under a minute after the one at 08:02. Every edit of a watched file by any session restarts it.
- **A cached furnace state can be stale right after a restart.** At 07:54 an order to turn a furnace on found it already on.
- **Raids are far from what they could bring.** See [Not done](#not-done).

From the plan v3 hand-off. Items 11 and 12 are settled by plan v4: the furnace rule was replaced, and raids keep the transports.

1. **Colonies still wait.** 71% of colony time is idle with an affordable target step. The plan on real income waits for each colony's top-ranked step, and the planner's score does not prefer a busier plan (see the dry runs above). This is what Step 5 was meant to address.
2. **Anti-bot stops dominate.** The bot was stopped for 7.0 of the 17.2 hours recorded (41%). One gate fired 107 seconds after switch-on. Whether the switch caused it is unknown; gates had fired five times before it that day.
3. **The page-view limit changed during the measurement.** It was 24 per minute until about 18:35 and 60 after, through uncommitted changes from another work session (`core/config.py`, `session/client.py`). More actions fit in a minute. The effect on anti-bot gates is unknown.
4. **Other uncommitted changes are running live.** The other session is editing `runtime/api_server.py` and the frontend. They are not part of this plan and were not reviewed.
5. **Ships still take Metal that buildings wait for**, now within a limit: 10% of real income for the spare budget (about 28,000 Metal per hour) and 25% for plan-sized transports while the fleet is below target. The operator has not confirmed the 25% or the 4-hour saving window.
6. **The spare budget ledger has gaps.** Fleet research paid from the spare budget is not debited. Small transports built toward the operator's own shipyard target are debited from the transport ledger instead. At the moment the follow-up landed the ledger was empty, so four hours of allowance were available at once.
7. **Ships in a Weapons factory queue are not counted as owned.** Plan-sized transports are covered by counting the last hour's orders. Other hulls can be ordered again before the first order completes.
8. **Fleet orders abort after being planned as ready**, with "no ship input fields found". Each costs page views. The colony is then held for 180 seconds. Cause not investigated; it predates this work.
9. **The commander's tick log has gaps.** A tick that ends on a page-view rejection skips the tick log, so the log goes quiet while the commander acts.
10. **Delivered cargo is undercounted.** A delivery is recorded only if the bot sees the fleet on its way back, and the hourly average includes stopped time.
11. **The donor rule is active now**, not after the fleet has grown as plan v2 intended. Kept: Pig iron is plentiful (2 colonies were at the warehouse cap) and Metal is what colonies wait for.
12. **New transports can leave on raids.** Kept: nothing reserves them for shipments between colonies.
13. Plan v2 commits were made per component, not one per step.

## How to validate

### When

At least 24 hours after **07:51:42 on 2026-11-20**, with at least 12 running hours recorded under plan v4. Ask the operator whether the switch was turned off at any point, whether they changed settings or queued work by hand, what the page-view limit was, and what the other work session landed in that time.

### Run

```bash
python3 scripts/validate_adaptive_execution.py
```

It reads the cached dashboard, the planner view, the fleet view and the notification list through `http://127.0.0.1:8787`. It makes no browser page views and sends no game actions. It writes one line per run to `~/.cache/spaceinvasion-autoplayer/furnace_capacity_readings.jsonl`, outside the repository: what the furnaces could make at that moment, which mark 1 needs over time. `--no-record` skips that line. Every run between now and the verdict narrows mark 1. The baseline and the readings above came from this script. It reports five periods: A before any code, B code landed with the switch off, C switch on with plan v2 only, D plan v3, E plan v4. Its summary judges period E against the four marks below.

### Read the result

No single number settles it, because the empire also matures: builds get longer, which raises "builders busy" on its own and lowers "levels per hour" on its own. Read these together.

The four marks for plan v4. The assistant set them on 2026-11-20; the operator has not confirmed them. They replace the earlier proposal of 4.2 builders, which the review showed the empire's income cannot pay for.

| # | Question | Measure | Plan v3 | Mark for plan v4 |
|---|---|---|---|---|
| 1 | Are the furnaces used? | Metal made while running in period E, as a share of what the furnaces could make at the time of each recorded point | 245,390 per hour | 80% or more |
| 2 | Are builders busier? | Builders busy while running, period E | 1.76 of 11 (2.10 before any change) | 3.0 or more: worked. Below 2.3: no effect |
| 3 | Are colonies advancing? | Building levels per hour since 07:51:42 on 2026-11-20 (1,242 then) | 9.3 per hour | 12 or more |
| 4 | Do affordable steps still wait? | Colony time idle with an affordable target step, last 24 hours | 78.6% | 50% or less |

Mark 1 was changed at 09:01 on 2026-11-20, after the first version read 79% and failed. That version divided the period's mean by the capacity at the moment of the run. Plan v4 builds furnace levels, 10 in its first 70 minutes, so the capacity at the end of a period is higher than it was during it, and the first version charged the plan for building furnaces. The script now takes the capacity at the time of each point from its readings and gives three figures: an estimate, which draws a straight line between two readings, and the lowest and highest share the readings allow. It prints PASS when the lowest is at or above the mark, FAIL when the highest is below it, and NOT SETTLED otherwise. The old figure is still printed under it. The mark itself, 80%, did not move.

Mark 4 will not reach zero: a step that costs Metal stays affordable on its colony and still waits while the empire is short of Metal.

The questions from plan v2 and v3 still apply:

| Question | Measure | Before | Reading |
|---|---|---|---|
| Are builders busier? | Builders busy while running | 2.10 of 11 before; 3.07 in period C; 1.76 in period D | See mark 2 |
| Is the Metal starvation gone? | Colony time waiting for Metal | 81.8% before; 57% over the window at 18:36 | Should fall sharply |
| Is Metal being made? | Metal per hour while running | 50,556 before; 202,371 in period C | Should hold or rise |
| Is the plan built on real income? | Raid income basis; share of spending paid by raid income | Model; 94% to 112% | `realized_24h`; about the share raids really bring, 6% at hand-off |
| Is the empire keeping to its plan? | Plan adherence | 9 built of 17 scheduled at 16:51, on the model plan | Built steps against steps scheduled to be finished. Should be higher on a plan that assumes real income |
| Are colonies advancing? | Building levels per hour | 5.1 per hour from 06:44 to 18:36 | Compare like with like, and note the stops |
| Are ships within budget? | Spare budget spent in the last 4 hours | No limit before | At most 4 times the hourly rate |
| Is the fleet the right size? | Cargo capacity against target | 84,000 before; 264,000 against 193,058 at hand-off | Near the target, which moves with each re-tune |
| Is cargo arriving? | Delivered against planned cargo per hour | 21% at the v3 hand-off; 13.8% (140,803 per hour) after 12 hours of plan v3 | Should rise with the loans. It will stay far below the plan while raids hold the hulls |
| Is the bot running? | Hours stopped, gate alerts | 42% stopped; gate alerts at 01:04, 04:14, 06:26, 06:37 and 07:58 | Must not get worse |

Decision gates from plan v2:

- Idle with an affordable step above 15%: build the fill rule (Step 5). It was 78.6% after 12 hours of plan v3, and the fill rule is now built (E2).
- Ready but not started above 5%: build the second-slot pre-queue (Step 5).
- Delivered cargo below 80% of planned: add the cargo limit to the simulator (Step 4b).

### Before concluding

- This is a before and after comparison, not a controlled test.
- The series is recorded only while the bot runs. Judge on running hours, and report stopped hours separately.
- The series keeps the last 1,440 points, about a day of running time. Once that much has been recorded, periods A to C drop out of the script's output. The baseline then exists only in this note and as constants in the script.
- The page-view limit was 24 per minute for the baseline and for period C, and 60 from about 18:35. A higher limit lets more actions through on its own.
- The colony-idle buckets cover the last 24 hours by the clock, so a reading taken 24 hours after 07:52 on 2026-11-20 holds only plan v4 data. Before that it mixes both.
- The other work session restarts the stack often. A restart can cost up to about 8 minutes, and its database move removed two hours of recorded points.
- A refactor of the whole system started on 2026-11-20 at about 08:45 on `refactor/p0-*` branches. Check `git log main` for what reached `main` during the window, and read period E on both sides of each landing.
- Furnace capacity grows as Blast furnace levels are built. Mark 1 allows for it only as far as the capacity readings go; read its range, not just its estimate. The readings assume furnace levels are never removed.
- The baseline is 2.10 of 11 colonies. If the number of colonies has changed, compare shares, not counts.
- A planner run by hand releases a burst of builds. Note any in the window.
- Check `git log` and `git status`: other changes may be running.

### If it did not work, look here first

1. Hours stopped at anti-bot gates.
2. Colony time idle with an affordable step, and section 10 of the script: are fills and loans present in the live plan? If none ever appear, the fill rule may be failing silently.
3. The first steps of the active plan: how many start in the first hour, and whether cheap steps such as Blast furnace upgrades are postponed.
4. Whether colonies are still waiting for Metal with their furnace on (smelting too slow) or off (rule not firing).
5. Page-view rejections in the API log, and what kind of order they hit.
6. Ship spending against the hourly rates.

## Not done

- **Raids.** The largest lever found and not touched. Spy reports on 31 Spacia targets showed 238 million resources at 07:58 on 2026-11-20, 78 million of it Metal, and no defence; the empire makes about 9 million Metal a day. Not all of it is reachable: three of those targets refused a spy probe that morning with "This user can't be attacked". Raids return full, so loot is set by cargo and by the number of raids. Three things hold it down: a raid takes whatever is parked, so most carry little (41,500 on average); each target seems to accept about three raids a day before the bot blocks it, and small fleets use them up; and one Small transport makes the whole fleet almost twice as slow. Fixing this means choosing the ships of a raid, which touches `session/executor.py` and `runtime/api_server.py`. Both were being edited by another session.
- Keeping some transports home for shipments between colonies. Same files, same reason.
- Shipping resources so that a fill becomes affordable. Fills use what the colony has.
- Step 4b (a cargo limit in the simulator), the second-slot pre-queue (1.84% against a gate of 5%), and live re-evaluation every cycle.
- A rule that makes the planner prefer the busier of two plans with nearly the same score.
- A recorded measure of how stale the saved step list gets.
- A mention of the switch in `AGENTS.md`.
- Showing the income basis and the ship ledgers in the Operator Console, and making its fleet line count hulls the way plan v4 does.
- A stored count of executed fills and loans.

## Sources on the operator's machine

Under `~/.gemini/jetski/brain/026d1b30-9575-4530-9d39-3a9b581bb0a6/`: `adaptive_mpc_planner_plan.md` (plan v4), `adaptive_mpc_planner_plan_v3_superseded.md` (plan v3), `adaptive_mpc_planner_plan_v2_superseded.md` (plan v2), `adaptive_mpc_planner_plan_review.md` (why v1 was dropped), `adaptive_mpc_planner_plan_v1_superseded.md`. The plan v2 verifier's report is `~/.gemini/jetski/brain/ebfc1d3c-151e-44b0-bd08-4406261c07f0/plan_v2_verification_report.md`.
