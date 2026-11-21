# Plan v2: the empire does what the plan says

**Status: built.** Written on 2026-11-19 after the review of [plan v1](01-plan-v1-adaptive-mpc.md). The code reached `main` between 12:56 and 14:11 UTC (commits `3442591..292edc2`) with the switch off. The switch was turned on at **14:36:33**. [Plan v3](03-plan-v3-real-income.md) corrected it four hours later.

## Goal

Colonies advance as fast as the planner intends, without hand fixes. The planner can only adapt to what its model sees, so the work went in this order:

1. Make the live empire do what the plan already says: furnaces, research, ship counts.
2. Give the empire the cargo capacity the plan assumes.
3. Decide with data what else the planner must learn.

## What changed from plan v1

| Plan v1 | Plan v2 | Why |
|---|---|---|
| Re-run the planner's decision step every cycle | Not built. Kept as an option for later | Tested: it started 1 build while 8 colonies could pay for more |
| Fill idle builders and queue a second build now | Decide after 12 hours of data, with thresholds | The planner had never scored such a rule, and fixing the furnaces might make the question smaller |
| One furnace rule for the empire, plus a local one | The local rule first. The empire rule once there are hulls to move the Metal | Every colony has its own furnace and Pig iron |
| Hold shipyard orders by mission role | Count ships in flight as owned | The hold existed only because ships in flight were not counted |
| Reserve a fleet slot for shipments | Dropped | Nothing observed showed a shipment waiting for a slot |
| Re-tune after 15 completed steps | Dropped | It would fire at every cooldown |
| No switch, no success measure | One switch, off by default, and a recorded measure | The live stack reloads code on every save |

## Ground rules

- **One switch, off by default:** `ai_adaptive_execution`. With it off the bot behaves as before. It is also the way to turn everything off.
- **Success is measured** on the recorded number of busy builders, before against after. It is a comparison, not a controlled test.
- **Kept as they were:** the AI-Planner is the only furnace controller, at 100% or 0%, with the Metal-overflow exception. No demolitions. The notebooks and the simulator's results stay the same when the new options are off. The page-view limit and every anti-bot safeguard are not touched.

## Steps

| Step | Plan | Built as |
|---|---|---|
| 0 | Switch and measurement | The setting, a toggle in the AI-Planner tab, up to 24 hours of the per-minute builder series, and for each colony the reason it is idle, counted in 10-minute buckets |
| 1 | Furnaces follow the planner, per colony | A colony's furnace goes on when its waiting steps need more Metal than it holds plus friendly cargo flying to it, and the queue will be free by the time smelting closes the gap. It stays on while short and goes off otherwise |
| 1, second half | Smelt on donor colonies | A colony that is covered, has a transport and ample Pig iron also smelts while the empire's waiting steps need more Metal than exists. Planned for after step 4a; built at once |
| 2 | Research starts when affordable | Research offers read on another colony are no longer trusted. The start is allowed when the laboratory colony can pay and nothing blocks it. The executor still checks the live button |
| 3 | Ships in flight count as owned | Owned ships are parked plus in flight. The old hold stays only when the flight details are missing or cut off |
| 4a | Transport fleet sized by the plan | Target cargo = planned cargo per hour over 24 hours, divided by round trips per hour, divided by utilization. The shortfall becomes Large transport orders. They are paid from their own allowance: 25% of hourly Metal and Kryptonite income |
| 4b | Cargo limit in the simulator | Not built. Only if delivered cargo stays below 80% of planned after 24 hours |
| 5 | Fill idle builders; second build slot | Not built. Gates: more than 15% of colony time idle with an affordable step; more than 5% ready but not started |
| 6 | Background re-tune | Re-tunes when the Config Optimizer result or the colony count changes, loot moves by 25%, or the plan is 4 hours old. At most once in 30 minutes, starting from the saved genes. Live re-evaluation every cycle was not built |

Where each rule lives now is in the [code map](README.md#where-the-rules-live-in-the-code).

## Decisions

Taken by the assistant in the plan and approved by the operator with it:

| Decision | Value | Reason |
|---|---|---|
| Order | Execution first, capacity second, new logic last | The planner cannot adapt to what its model does not see |
| Furnace rule | Per colony, replacing "follow the first waiting step of the empire" | Six of nine idle colonies waited for Metal their own furnace could make in 1 to 3 hours |
| Research rule | The one fleet research already used | Smallest change that removes the delay |
| Fleet size | From the plan's own shipments, with utilization starting at 0.5 | 858,000 cargo per hour planned against a fleet of 84,000 |
| Transport funding | Outside the 10% spare budget, capped at 25% of income | The transports are the plan's infrastructure, and buildings must not be starved |
| Step 4b only if needed | Differs from the "both" the operator had approved | Large transports are cheap next to the cargo they move, and the simulator does not model ship building |
| Gates for step 5 | 15% and 5% | Proposed by the assistant; the operator was to decide at the gate |

Taken by the operator:

- Approved the plan and had it built by a team of agents (12:33).
- Asked for validation (14:27), then for the rest to be done (14:35). The assistant turned the switch on through the local API at 14:36:33.

One thing went differently from the plan: the commits were made per component, not one per step.

## What happened

| Measure | Before | Plan v2 alone, 14:36 to 18:26 |
|---|---|---|
| Builders busy while the bot ran | 2.10 of 11 over 6.24 running hours | 3.07 of 11 over 2.43 running hours |
| Metal made per hour while the bot ran | 50,600 | 202,400 |
| Furnaces on | 0 of 11 | 4 to 9 of 11 |
| Transports | 14 Small, 84,000 cargo | 27 Small and 3 Large, 252,000 cargo, all in flight at 18:25 |
| Colony time idle with an affordable step | 81.8% | 71.3% |

The first reading of builders, 5.06 after 40 minutes, did not hold. It followed a long stop and two planner runs by hand.

An anti-bot gate fired 107 seconds after the switch was turned on, and the bot stood still until 16:05. Whether the switch caused it is not known; gates had fired five times before it that day.

## What it got wrong

Four hours of running showed these. [Plan v3](03-plan-v3-real-income.md) addresses each.

| Finding | Measured |
|---|---|
| The planner's raid income was a model, not a fact | About 1,000,000 Metal per hour in the plan, about 50,000 received |
| So the plan asked for almost no smelting | Raid income paid for 94% to 112% of all the plan's spending. Furnaces smelted 3% of the planned time |
| The 10% spare budget was not a budget | 124,000 Metal allowed on every planning call, with no memory of earlier spending |
| Step 3 removed the only brake on shipyards | The old hold had kept them idle. Without it combat ships were ordered whenever a call allowed it |
| A fleet that looked idle raised its own target | Measured utilization of 0.2 took the target from 54 to 135 Large transports |
| The re-tune chased noise | It fired because the median loot of one raid moved 25%, which moved 33% again within 25 minutes |
| Ship orders were starved of page views | One hull per order, after every fleet mission |

## Tests

As reported by the verifying agent at commit `292edc2`: 664 unit tests with the same 11 failures as before the change, 62 frontend tests passing, and identical actions from old and new code on a live snapshot with the switch off.
