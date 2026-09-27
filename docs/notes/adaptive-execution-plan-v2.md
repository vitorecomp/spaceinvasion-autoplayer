# Adaptive AI-Planner execution (Plan v2): intent, what was built, and how to validate it

This note records why the adaptive execution work was done, what was planned, what actually landed, and the numbers taken before and right after it was switched on. Its main purpose is to let someone later answer one question without the original conversation: **did the plan work?** The procedure for that is in [How to validate](#how-to-validate).

All times are UTC. The operator's local time is UTC-03:00.

## Status at hand-off (2026-11-19 16:51)

- The switch `ai_adaptive_execution` has been **on since 2026-11-19 14:36:33**.
- Working as designed: Blast furnaces now follow each colony's own Metal need (8 of 11 on, 0 of 11 before).
- Slow to start: the transport fleet sized by the plan. The first Large transport was ordered at 16:43:59, after about 45 minutes of running time. Two existed at 16:51, against a target of about 100.
- **No verdict is possible yet.** The bot ran for only 0.84 of those 2.2 hours; an anti-bot gate stopped it for 87 minutes, 107 seconds after the switch was turned on.

## Intent

The AI-Planner produces a schedule of builds and research that keeps every colony's builder busy. Live, the colonies were idle most of the time: about 2 of 11 builders were busy on average.

The main idea of the work: **the planner can only adapt to what its model sees.** So the order was:

1. Make the live empire do what the plan already says (furnaces, research, ship counts).
2. Give the empire the cargo capacity the plan assumes.
3. Decide with data what else the planner must learn.

The goal is colonies that advance as fast as the planner intends, without hand fixes.

## What was planned

A first plan (v1) proposed re-running the planner's decision step on live state every cycle. It was tested before any code was written and dropped: on a live snapshot with 9 of 11 builders free and 8 colonies able to pay for 44 steps, that decision step started 1 build, at patience 0, 0.25 and 1.0 alike.

The measurements that shaped the second plan (v2), taken on 2026-11-19 between 06:39 and 07:45:

- The saved plan said to turn every furnace on at once. All 11 were off. Six of nine idle colonies were waiting for Metal their own furnace could make in 1 to 3 hours.
- The simulator ships any amount of cargo after a lead time. The empire owned 14 Small transports, 84,000 cargo in total.
- Research started late: offers were only trusted when read on the lab colony.
- Shipyards refused to build while any fleet was away, because ships in flight were not counted as owned.

| Step | Plan v2 | Status |
|---|---|---|
| 0 | One switch, off by default, plus measurement | Built |
| 1 | Furnaces follow the planner, per colony | Built |
| 2 | Research starts when affordable | Built |
| 3 | Ships in flight count as owned | Built |
| 4a | Transport fleet sized by the plan | Built; 2 of about 100 ships at hand-off |
| 4b | Cargo limit inside the simulator | Not built; decided after 4a has run |
| 5 | Fill idle builders, pre-queue the second build slot | Not built; decided on recorded data |
| 6 | Background re-tune on drift | Built. Live re-evaluation every cycle was not built |

## What was built

Code state: commits `6c08e97..1a1a8d3` on `main`, local only. Every behaviour below is inactive while `ai_adaptive_execution` is off.

| Step | Behaviour | Where |
|---|---|---|
| 0 | `ai_adaptive_execution` (default off) on the Config page and as a toggle in Action Graph Tab 3 | `core/config.py`, `AiPlannerTab.tsx` |
| 0 | `/dashboard` returns up to 1,440 points of the per-minute builder series (was 120). Points exist only for minutes the bot ran | `hourly_production_history` in `runtime/api_server.py` |
| 0 | Per colony: building, ready not started, waiting for Metal, Pig iron, Kryptonite or Spice, cargo inbound, storage too small, field conflict. Shown in Tab 3 and counted in 10-minute buckets for the last 24 hours | `diagnose_ai_colony_idle_states`, `adaptive_status` in `ai_planner_get` |
| 1 | Furnace on when the colony's waiting AI steps need more Metal than it has plus friendly cargo flying to it, and the step's queue frees no later than smelting needs. Stays on while short. Off otherwise | `_adaptive_colony_smelt_decision`, `plan_ai_blast_furnace_throttles` in `planner/strategy.py` |
| 1 | Donor rule: a covered colony with at least one transport and ample Pig iron also smelts when the empire's waiting steps need more Metal than exists | same |
| 2 | Cached research offers are trusted only when the lab colony is active; otherwise the start is allowed when affordable and unblocked | `reconcile_live_ai_actions` in `planner/ai_planner.py` |
| 3 | Owned ships are parked plus in flight. The old hold stays only when flight details are missing or cut off | `count_in_flight_ships_by_planet` in `core/state.py` |
| 4a | Target cargo = planned cargo per hour over 24 h, divided by round trips per hour, divided by utilization. Utilization is 0.5 until 3 deliveries over 1 hour exist, then the measured value, kept between 0.2 and 0.9. The shortfall becomes Large transport targets, split across shipyard colonies | `transport_demand` in `run_ai_planner`, `compute_infra_transport_targets` |
| 4a | Those builds are tagged `[Plan infra cargo target]` and paid only from a separate allowance: 25% of hourly Metal and Kryptonite income, with a spend ledger and up to 2 hours of carry-over | `compute_spare_ship_budget`, `filter_shipyard_actions_by_spare_budget` |
| 6 | Re-tune in the background when the Config Optimizer result or colony count changes, loot shifts by 25%, or the plan is 4 hours old. 30-minute cooldown. Starts from the saved genes | `_evaluate_ai_drift_replan_locked` |

Unchanged on purpose: furnaces are 100% or 0% only, with the Metal-overflow pause at 100% storage and resume below 95%; no demolitions; the simulator's maths and the 14-line export header.

Tests, as reported by the verifier agent at `1a1a8d3` and not re-run for this note: 664 unit tests with the same 11 failures as before the change, 62 frontend tests passing, 12 of 12 in two end-to-end files, database integrity check passing. With the switch off, the old and new code produced identical actions on a live snapshot. The full end-to-end suite was not run, and nothing was run live with the switch on before it was enabled.

To turn the behaviour off: the Tab 3 toggle, or `{"command": "update_config", "config": {"ai_adaptive_execution": false}}`. The previous rules take over on the next planning cycle.

## Timeline

| Time (UTC) | Event |
|---|---|
| 2026-11-19 06:39 to 07:45 | Diagnosis and review of plan v1 |
| 12:56:58 | First Plan v2 code merged into `main`; switch off |
| 14:11 | Last fix commit, `1a1a8d3` |
| 14:25 | Planner run by hand (run 11) |
| **14:36:33** | **Switch turned on** through the local API, at the operator's request |
| 14:37:08 to 14:37:30 | Four furnaces set to 100% |
| 14:38:20 | Anti-bot gate; bot stopped until 16:05 |
| 16:07 | Planner run by hand (run 12) |
| 16:31 | API restarted with uncommitted changes from another work session (dashboard screens). Not part of this plan and not reviewed here |
| 16:36 | First ship orders seen since switch-on: 2 Small fighters, 1 Small transport |
| 16:43:59 | First Large transport ordered, on 02:380:12 |
| 16:45:20 | That Large transport sent on a raid with 4 Small fighters |
| 16:47:26 | Second Large transport ordered, on 02:380:08 |
| 16:47 | Third delivery recorded. Measured utilization (0.26) replaces the assumed 0.5 and the cargo target rises from 1,626,900 to 3,115,472 |

## Baseline, before the change

| Measure | Value | How it was taken |
|---|---|---|
| Builders busy while the bot ran | **2.10 of 11** (19%) | Time-weighted over 6.24 running hours, 319 points, 01:26 to 12:57. Median 1, maximum 8 |
| Colony time waiting for Metal | 81.8% | 9 ten-minute buckets, 99 colony samples, read at 14:28 with the switch still off |
| Colony time idle with an affordable step | 81.8% | same |
| Colony time ready but not started | 0% | same |
| Furnaces on | 0 of 11 | Checked at 06:39, 06:41, 07:07, 07:11, 07:36 |
| Transports | 14 Small, 0 Large, 84,000 cargo | Parsed from live flights |
| Building levels completed, all colonies | 1,057 at 06:44 | Sum of `fields_used`, which equals the sum of building levels on every colony |
| Time the bot was stopped | 5.5 of 13.2 recorded hours (42%) | Three gaps in the series between 01:26 and 14:36, each matching an anti-bot gate alert |

"Running" means consecutive points of the series are at most 5 minutes apart. A longer gap counts as stopped.

## First readings with the switch on (16:51, 2.2 hours in)

| Measure | Value |
|---|---|
| Builders busy while the bot ran | 4.98 of 11, over 0.84 running hours and 42 points |
| Furnaces on | 8 of 11. At 16:28 seven had a Metal gap of their own and one (02:379:05) had none, which matches the donor rule |
| Transports | 20 Small, 2 Large, 180,000 cargo against a target of 3,161,484 |
| Cargo delivered | 45,971 per hour, 3.3% of the 1,397,507 planned, from 3 recorded flights |
| Building levels completed | 1,102 |
| Plan adherence | 44 minutes into the current plan: 9 steps built of 17 scheduled to be finished |
| Colony time idle with an affordable step | 67.6% over 16 buckets. At most 6 of them are from after the 16:05 restart |

The builder figure is not evidence yet. Besides the switch, two things changed in that window: the planner was re-run by hand twice, and resources piled up during the 87-minute stop.

## Known problems at hand-off

1. **Anti-bot stops dominate.** Counting the stop after switch-on, the bot was stopped for 7.0 of the 15.4 hours recorded that day (45%). One gate fired 107 seconds after switch-on, right after a burst of actions. Whether the burst caused it is unknown; gates had already fired five times that day.
2. **The page-view limit of 24 per minute ends almost every round.** It was reached about every 2 minutes in the parts of the API log that could be seen between 16:21 and 16:50. A building order was rejected by it at 16:30:37.
3. **Large transports arrive slowly.** Each ship order is for one unit and is followed by a planning call. In each colony Large transports are ordered after Small fighters, Small transports and Starcruisers. In the two complete rounds visible in the log, one and five ship orders went through before the page-view limit ended the round.
4. **The 25% allowance is far larger than intended.** It is 25% of an income figure of 1,231,205 Metal per hour, of which 1,004,605 is modelled raid income and 226,600 is what the furnaces produce. It allowed 606,503 Metal at 16:29, which was all the free Metal in the empire, and 284,801 at 16:44, when 532,535 was free. A quarter of furnace production alone is about 56,650 per hour. Large transports can therefore take Metal that buildings are waiting for.
5. **The cargo target moves.** It doubled at 16:47 when measured utilization replaced the assumed 0.5. The measured value rests on 3 flights.
6. **The first new transport went on a raid** 81 seconds after it was ordered. Whether any rule keeps these ships for shipments between colonies was not checked.
7. **Fleet orders abort after being planned as ready.** Between 16:45 and 16:49 a recycle, an attack, a station order and a transport each ended with "no ship input fields found". Each costs page views. Cause not investigated.
8. **The commander log has no entry after 14:35:48**, although the commander is acting. Not investigated.
9. **The donor rule is active now**, not after the fleet has grown as plan v2 intended.
10. **Combat ships are being ordered while fleets are away**, because the hold was dropped for every hull type. They are paid from the existing 10% spare budget.
11. Commits were made per component, not one per step.

## How to validate

### When

At least 24 hours after 2026-11-19 14:36:33, with at least 12 running hours recorded while the switch was on. Ask the operator whether the switch was turned off at any point, and whether they changed settings or queued work by hand.

### Run

```bash
python3 scripts/validate_adaptive_execution.py
```

It reads the cached dashboard and two database queries through `http://127.0.0.1:8787`. It makes no browser page views, sends no game actions and writes nothing. The baseline and first readings above came from this script.

### Read the result

No single number settles it, because the empire also matures: builds get longer, which raises "builders busy" on its own and lowers "levels per hour" on its own. Read these together.

| Question | Measure | Before | Reading |
|---|---|---|---|
| Is the Metal starvation gone? | Colony time waiting for Metal | 81.8% | Should fall sharply. This is the most direct evidence for Step 1 |
| Are builders busier? | Builders busy while running, period C | 2.10 of 11 | Proposed: worked at 4.2 or more; no effect below 2.6 |
| Is the empire keeping to its plan? | Plan adherence | none | Built steps against steps scheduled to be finished. Higher is better; no earlier value exists |
| Are colonies advancing? | Building levels per hour | 4.5 per hour from 06:44 to 16:51, mostly before the switch | Compare like with like, and note the stops |
| Is the fleet growing? | Large transports, capacity against target | 0 Large, 84,000 cargo | Should rise toward the target. The target itself moves with measured utilization |
| Is cargo arriving? | Delivered against planned cargo per hour | no earlier value; 3.3% at hand-off | Should rise. Below 80% after the fleet has grown is the Step 4b gate |
| Is the bot running? | Hours stopped, gate alerts | 42% stopped; gate alerts at 01:04, 04:14, 06:26, 06:37 and 07:58 | Must not get worse |

The two builder thresholds are the assistant's proposal. The operator has not confirmed them.

Decision gates from plan v2, to read from a 24-hour window recorded entirely with the switch on:

- Idle with an affordable step above 15%: build the fill rule (Step 5).
- Ready but not started above 5%: build the second-slot pre-queue (Step 5).
- Delivered cargo below 80% of planned, after the fleet has had 24 hours to grow: add the cargo limit to the simulator (Step 4b).

### Before concluding

- This is a before and after comparison, not a controlled test.
- The series is recorded only while the bot runs. Judge on running hours, and report stopped hours separately.
- The series keeps the last 1,440 points, about a day of running time. Once that much has been recorded with the switch on, periods A and B drop out of the script's output. The baseline then exists only in this note and as a constant in the script.
- The baseline is 2.10 of 11 colonies. If the number of colonies has changed, compare shares, not counts.
- A planner run by hand releases a burst of builds. Note any in the window.
- Check `git log` and `git status`: other changes may be running.

### If it did not work, look here first

1. Hours stopped at anti-bot gates.
2. Page-view rejections in the API log, and what kind of order they hit.
3. How many Large transports exist, what they are used for, and where Metal is going.
4. Whether transports are dispatched at all: aborted fleet orders in the API log.
5. Whether colonies are still waiting for Metal with their furnace on (smelting too slow) or off (rule not firing).

## Not done

- Step 4b, Step 5, and live re-evaluation every cycle.
- A recorded measure of how stale the saved step list gets.
- A mention of the switch in `AGENTS.md`.

## Sources on the operator's machine

Under `~/.gemini/jetski/brain/026d1b30-9575-4530-9d39-3a9b581bb0a6/`: `adaptive_mpc_planner_plan.md` (plan v2), `adaptive_mpc_planner_plan_review.md` (why v1 was dropped), `adaptive_mpc_planner_plan_v1_superseded.md`. The verifier's report is `~/.gemini/jetski/brain/ebfc1d3c-151e-44b0-bd08-4406261c07f0/plan_v2_verification_report.md`.
