# Plan 0: the AI-Planner and its furnace control

**Status: built.** The planner architecture was agreed on 2026-11-18. The furnace control was agreed on 2026-11-19 between 04:04 and 04:42 UTC. This is the state the adaptive plans (v1 to v4) started from.

## What it set out to do

Before this plan the live bot ran one rule-based planner. Two notebooks existed offline: `notebooks/config_optimizer.ipynb` works out which building and research levels each colony should end at, and `notebooks/full_planner.ipynb` works out in which order to build them.

The plan ported both notebooks into the runtime as pure-Python engines that run as background jobs, and gave the Action Graph four tabs.

| Tab | Planner | What it decides |
|---|---|---|
| 1. Heuristics | Rule-based planner, mode `priority` | The next actions by fixed priority rules (P0 to P3). Active until every colony is founded, or when the operator selects it |
| 2. Config Optimization | Config Optimizer | Target building levels for each colony and target research levels |
| 3. AI-Planner | AI-Planner, mode `ai_planner` | The order and timing of the steps toward that target over a horizon of weeks (26 by default). A genetic algorithm tunes eight genes against a discounted reward |
| 4. Fleet / Attack | Fleet planner | Escape flights, raids, recycling, spying, galaxy scans, transports between colonies, stationing, and ship building inside a spare budget |

## Decisions of the architecture

These were agreed with the operator in an interview on 2026-11-18.

| # | Decision | Detail |
|---|---|---|
| 1 | Two live planner modes | `priority` and `ai_planner`. A third, older mode (a genetic-algorithm planner) was no longer offered in the console. The refactor of 2026-11-20 removed its code, and a saved setting that still names it is read as `priority` |
| 2 | All colonies first | The AI-Planner acts live only once every colony slot is founded (11 of 11). Before that the tabs show previews and the rule-based planner acts |
| 3 | Built levels are floors | A target is never below what is already built. The bot does not demolish on its own: when a full planet blocks a planned step, the operator is asked to demolish or to keep the building and reserve its level |
| 4 | Two planners at once | In `ai_planner` mode the AI-Planner drives buildings and research, and the fleet planner runs alongside it |
| 5 | Soft reserve and spare budget | The cost of the next AI-Planner step is reserved first. Ship building may spend a spare share of hourly income (10% by default). A hull type the empire has none of can always be replaced |
| 6 | Notebooks stay standalone | The ported engines are checked against the notebooks by parity tests. The notebooks are not changed |

Decision 3 has changed since, outside these plans: a plan may now carry demolitions when the operator allows them (`allow_demolition`). Live, a demolition is offered only when the plan allows it, the planet has no free field, and the planet's energy stays non-negative without the building.

## How a plan is carried out live

This is the behaviour plans v2 to v4 later changed.

1. A background job runs the planner and stores a step list: builds and research with planned start times.
2. Every planning cycle the saved list is compared with the live state. Steps already satisfied are skipped. Each colony is offered its earliest step whose prerequisites are met, and the empire is offered one research step.
3. A step the colony cannot pay for yet is shown as waiting. The transport planner sends surplus from other colonies toward it.
4. The plan's raid income came from a model: the median loot of one raid and the median round trip, with every fleet slot raiding back to back.

## Furnace control (2026-11-19)

The operator asked that only the AI-Planner may switch a Blast furnace on or off. The single exception: a Metal warehouse that is full while Pig iron still has room, and production must come back on its own once there is room again.

The assistant asked four questions with a recommended answer for each.

| # | Question | The operator chose | Recommended? |
|---|---|---|---|
| 1 | Where does the rule apply? | In `ai_planner` mode only. The rule-based planner keeps its own furnace throttling when it is the active mode | No. The recommendation was to apply it everywhere |
| 2 | Which settings may the furnace take? | 100% or 0% only, decided by the AI-Planner and the overflow exception. Every other automatic override was dropped. A throttle command given by hand still works | Yes |
| 3 | When does the overflow exception pause and resume? | Pause at once when Metal reaches 100% of the warehouse while Pig iron has room. Resume at once when Metal falls below 95%, to 100% if the AI-Planner wants the furnace on | Yes |
| 4 | When does the AI-Planner want the furnace on? | The same rule as the simulator: every furnace on while the first waiting step of the plan is short of Metal, counting the stock of all colonies and friendly cargo in flight. Every furnace off once it is covered | Yes |

The operator then asked whether the planner takes the furnaces' real on or off state as its starting point. That was added the same night (commit `3442591`).

## What the later plans changed

| Baseline behaviour | Changed by | To |
|---|---|---|
| One empire-wide furnace switch that follows the first waiting step (furnace decision 4) | [Plan v2](02-plan-v2-execution-follows-the-plan.md), step 1 | Each colony decides for itself. [Plan v4](04-plan-v4-pooled-empire.md) added smelting for the empire |
| Raid income from a model | [Plan v3](03-plan-v3-real-income.md), D1 | The loot and recycling received in the last 24 hours |
| The spare budget is allowed again on every planning call | Plan v3, D3 | An hourly rate with a ledger |
| Each colony waits for its earliest ready step | Plan v4, E2 | A free builder may start a later step of its plan |
| Transports move surplus only | Plan v4, E3 | A colony lends what it saves to a step that comes earlier in the plan |

All of these changes sit behind `ai_adaptive_execution`. With the setting off, the behaviour on this page is what runs.
