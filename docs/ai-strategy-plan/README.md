# AI strategy plans

This folder records the plans made for how the AI-Planner runs the live empire. For each plan it says what the plan set out to do, what it changed from the plan before, which decisions were taken and by whom, and what happened. There is one file per plan.

The plans were made between 2026-11-18 and 2026-11-21 in one working session between the operator and an AI coding assistant. "The assistant" below is that assistant. All times are UTC; the operator's local time is UTC-03:00.

## The plans

| # | Plan | When | Status | In one sentence |
|---|---|---|---|---|
| 0 | [The AI-Planner and its furnace control](00-baseline-ai-planner.md) | 2026-11-18 and 19 | Built | Two offline notebooks became live planners, and only the AI-Planner switches the Blast furnaces |
| 1 | [Plan v1: adaptive MPC](01-plan-v1-adaptive-mpc.md) | 2026-11-19, 06:39 to 07:45 | Dropped before any code | Re-run the planner's decision step on the live state every cycle |
| 2 | [Plan v2: the empire does what the plan says](02-plan-v2-execution-follows-the-plan.md) | 2026-11-19, switched on at 14:36:33 | Built | Furnaces per colony, research on time, ships in flight counted, a transport fleet sized by the plan |
| 3 | [Plan v3: plan on the income that arrives](03-plan-v3-real-income.md) | 2026-11-19, live at 18:26:36 | Built | Raid income is what was received, and ship budgets are hourly rates |
| 4 | [Plan v4: the empire as one pool](04-plan-v4-pooled-empire.md) | 2026-11-20, live at 07:51:42 | Built; verdict pending | Smelt for the empire, fill idle builders, lend savings in plan order |

[Check after the refactor](05-refactor-check.md) records how the rules of plans 2 to 4 were compared before and after the refactor of 2026-11-20 and 21.

## The thread through them

The goal has not changed since plan v1: **colonies advance as fast as the planner intends, without hand fixes.** When asked which bottleneck to fix first, the operator answered that the point was not to fix one bottleneck but to have planners that solve such situations by themselves.

The idea that shaped plans 2 to 4: **the planner can only adapt to what its model sees.** Each plan started from measurements of the plan before it.

| Plan | What the measurements showed | What the plan did about it |
|---|---|---|
| v1 | About 3 of 11 builders busy | Proposed to re-run the planner's decision step every cycle. Tested on a live snapshot before any code: it started 1 build while 8 idle colonies could pay for 44 steps |
| v2 | The plan said every furnace on, and all 11 were off. The simulator ships any amount of cargo, and the empire owned 14 Small transports. Research started late. Shipyards refused to build while a fleet was away | Made the live empire do what the plan already said, gave it the cargo the plan assumes, and measured before deciding more |
| v3 | The plan's raid income was 20 times what the empire received, so the plan needed almost no smelting. Ship budgets had no memory | Planned and budgeted on received income |
| v4 | Builders were not busier (1.76 of 11, against 2.10 before any change). The empire was limited by Metal income, each colony saved alone, furnaces made 245,400 Metal per hour of up to 427,100, and most transports were on raids | Treated the empire as one pool: empire smelting, a fill rule, loans in plan order, a fleet target that leaves out raiding hulls |

## Who decided what

| Plan | The operator | The assistant |
|---|---|---|
| 0 | Chose four furnace-control options in an interview. On the first one the operator chose a narrower scope than the recommended one | Recommended an option for each question |
| 1 | Chose six design options in an interview, each time the recommended one. Then asked for a review of the plan | Wrote the plan, then reviewed it and advised against building it |
| 2 | Approved the re-scope, had it built by a team of agents, and asked for the switch to be turned on | The order of the steps, the single switch, the formulas and thresholds |
| 3 | Asked to fix what could be fixed already and to rewrite the plan | Five decisions (D1 to D5). Five questions were left to the operator |
| 4 | Asked for the best result without being asked for decisions | Every decision. Each one is listed in the plan with how to reverse it |

## What is live

Everything plans 2 to 4 built sits behind one setting, `ai_adaptive_execution`. It is off by default and has been on since 14:36:33 on 2026-11-19.

To turn it off: the toggle in the AI-Planner tab of the Action Graph, or the `update_config` command with `{"ai_adaptive_execution": false}`. The behaviour of plan 0 takes over on the next planning cycle. A plan already computed on received income stays active until the planner runs again.

## Where the rules live in the code

Paths are the ones after the refactor. The older note linked at the end of this page uses the names from before it.

| Rule | Plan | Function | File |
|---|---|---|---|
| The switch | v2 step 0 | setting `ai_adaptive_execution` | `backend/core/settings/specs_core.py` |
| Saved plan reconciled with the live state | 0 | `reconcile_live_ai_actions` | `backend/planner/ai/live_reconcile.py` |
| Research starts when affordable | v2 step 2 | `reconcile_research_step` | `backend/planner/ai/_reconcile_steps.py` |
| Why a colony is idle | v2 step 0 | `diagnose_ai_colony_idle_states` | `backend/planner/ai/diagnostics.py`; 10-minute buckets in `backend/runtime/bridge/_ledgers_adaptive.py` |
| Furnaces per colony; furnaces for the empire | v2 step 1, v4 E1 | `plan_ai_blast_furnace_throttles`, `_adaptive_colony_smelt_decision` | `backend/planner/heuristics/blast_furnace.py`, `_furnace_ai_adaptive.py` |
| Ships in flight count as owned | v2 step 3 | `count_in_flight_ships_by_planet` | `backend/core/_inflight.py` |
| Transport fleet sized by the plan, raiding hulls left out, ceiling | v2 step 4a, v3 D5, v4 E4 | `compute_infra_transport_targets`, `_transport_hulls_on_raids` | `backend/planner/heuristics/_cargo_helpers.py` |
| Plan-sized transport orders in batches | v3 D4 | `_plan_planet_shipyard_targets` | `backend/planner/heuristics/_shipyard_targets.py` |
| Ship budgets as hourly rates | v3 D3 | `compute_spare_ship_budget`, `filter_shipyard_actions_by_spare_budget` | `backend/planner/heuristics/_shipyard_budget_calc.py`, `_shipyard_budget_filter.py`; spend ledgers in `backend/runtime/bridge/_ledgers_budgets.py` |
| Raid income as received | v3 D1 | `compute_attack_telemetry`, `get_realized_loot_hourly`, `overlay_realized_loot` | `backend/planner/ai/telemetry.py`, `backend/runtime/bridge/_ledgers_production.py` |
| Background re-tune | v2 step 6, v3 D2 | `evaluate_ai_drift_replan` | `backend/runtime/bridge/_planner_jobs_telemetry.py` |
| Fill rule | v4 E2 | `plan_live_fill_actions` | `backend/planner/ai/live_fill.py`, `_fill_helpers.py` |
| Loans in plan order | v4 E3 | `plan_interplanet_transports` | `backend/planner/heuristics/transport_station.py`, `_transport_dispatch.py`, `_transport_helpers.py` |
| One cycle's plan put together | 0 | `plan_hybrid_ai_actions` | `backend/runtime/bridge/_plan_hybrid.py`, `_plan_helpers.py` |
| Tuning constants | v2 to v4 | | `backend/planner/heuristics/_types.py`, `backend/planner/ai/live_fill.py`, `backend/planner/ai/_fill_helpers.py`, `backend/runtime/bridge/_context_init.py` |
| Tests written for these plans | v3, v4 | | `tests/unit/planner/test_adaptive_followup.py` (11 tests), `tests/unit/planner/test_adaptive_v4.py` (30 tests) |

## How plan v4 will be judged

Not before 07:52 on 2026-11-21, and not with fewer than 12 running hours. Then:

```bash
python3 scripts/validate_adaptive_execution.py
```

The script only reads the local bridge and prints the four pass marks. The marks, what they mean and what to check before concluding are in [plan v4](04-plan-v4-pooled-empire.md#how-it-will-be-judged). The full procedure, the baseline and every reading taken so far are in the measurement record below.

## Related

- [Adaptive execution: measurement record and validation procedure](../notes/adaptive-execution-plan-v2.md). Written while plans 2 to 4 ran. It holds the baseline, the timeline and the readings.
- [Heuristic strategy](../heuristic-strategy.md): the rule-based planner that runs when the AI-Planner is not active.
- [Strategy settings reference](../settings-reference.md): every setting, generated from the code.
