# Plan v1: adaptive MPC

**Status: dropped before any code was written.** Proposed and reviewed on 2026-11-19 between 06:39 and 07:45 UTC. Replaced by [plan v2](02-plan-v2-execution-follows-the-plan.md).

## Why a plan was needed

The operator reported that the AI-Planner behaved well but the buildings on the planets were not advancing. Most colonies sat idle most of the time.

Asked which bottleneck to fix, the operator set the goal that every later plan kept: not to fix one bottleneck, but to have planners that solve such situations by themselves, adapt, and advance the colonies as fast as possible.

## What it proposed

The plan was called "adaptive MPC" (model-predictive control): look at the live state every cycle and let the planner's own decision logic choose again, instead of replaying a saved step list.

The assistant interviewed the operator on six points, with a recommended option for each. The operator chose the recommended option every time.

| # | Point | Chosen |
|---|---|---|
| 1 | How the planner adapts | Two tiers. Tier 1: every cycle, re-run the planner's decision step on the live state with the saved genes (quoted as under 100 ms, no page views). Tier 2: re-tune the genes in the background when the state drifts |
| 2 | How to keep builders busy | An idle colony may build another affordable step if that does not delay the waiting one beyond the planner's patience. Queue a second build when the first finishes within 180 seconds |
| 3 | Furnaces | Hybrid: every furnace on when the empire is short of Metal, plus a single colony's furnace on when its own step needs Metal that no transport is bringing. Still 100% or 0%, with the overflow exception |
| 4 | Logistics | Hold shipyard orders only for missions of the same role, build and station transports where resources pile up, and reserve one fleet slot on donor colonies for the planner's shipments |
| 5 | When to re-tune | Plan 4 hours old, or 15 steps completed, or loot moved by 25%, or the Config Optimizer result changed. At most once in 30 minutes |
| 6 | Controls | No new settings. A compact status strip in the AI-Planner tab |

## The review

The operator asked for a review of the plan before building it. The assistant tested its central mechanism on the live state without touching the game: cached reads of the local bridge, and the planner code run offline in a separate process.

**Verdict: do not build it as written.**

| Finding | Measured |
|---|---|
| Tier 1 would not fill idle builders | On one live snapshot 9 of 11 builders were free and 8 colonies could pay for 44 steps of the target. The decision step started 1 build. The result was the same at patience 0, 0.25 and 1.0 |
| The furnaces never followed the plan | The saved plan said to turn all 11 furnaces on at once. All 11 were off at every check. Waiting steps lacked 474,712 Metal on 7 colonies. Each of those colonies had a furnace and the Pig iron to close its own gap in 60 to 186 minutes |
| Why the furnaces stayed off | The rule compared the first waiting step with the Metal of all colonies added together. Most of that Metal sat on colonies that could not ship it |
| The simulator assumes transports that did not exist | It ships a shortfall of any size after a lead time. The empire owned 14 Small transports, 84,000 cargo. At one check all 14 were in flight |
| Fewer builders were busy than the plan said | 2.76 of 11 over three hours. The plan's "7 of 11" was the best of three spot checks |

The review also corrected four claims the assistant had made while writing the plan.

| Claim | What was true |
|---|---|
| A research step was permanently deadlocked | It started on its own within half an hour. It was a delay: research offers were only trusted when read on the laboratory colony |
| Hold shipyard orders by mission role, and never hold transports when none are idle | Transports are normally all in flight, so that rule would order more every time. The cause was that ships in flight were not counted as owned |
| Long recycling flights take the last fleet slot | Not observed. The limit was the number of hulls. The slot reservation was recommended without evidence |
| Re-tune after 15 completed steps | The saved plan schedules 21 steps in its first 30 minutes, so that trigger would fire at every cooldown |

What held: the furnace diagnosis and the hybrid control of point 3, the cost of tier 1 (measured at about 56 ms), and the fact that queueing a second build pays for it at once.

## Decisions

| Who | Decision |
|---|---|
| Operator | The six choices above, and the request to review the plan |
| Assistant | The verdict, and the re-scope: first make the live empire do what the plan says (furnaces per colony, research, ship counts), then give it transport capacity, then decide the rest with data |
| Assistant | Two safeguards the plan lacked: put new behaviour behind a switch that is off by default, because the live stack reloads code on every save; and define a success measure |
| Operator | Accepted the re-scope ("do it") and asked for the plan to be rewritten |

Two questions were put to the operator with the review: how to get transport capacity (grow the fleet, teach the simulator a cargo limit, or both), and whether an idle colony should build another affordable step. The recommendation was "both, fleet first" for the first and "decide after the furnaces are fixed" for the second. Plan v2 answers both in its steps 4a, 4b and 5.

## What survived into plan v2

| v1 point | In plan v2 |
|---|---|
| 1. Two tiers | The background re-tune stayed, with other triggers. Live re-evaluation was kept only as an option, because it is not what fills builders |
| 2. Fill idle colonies, second build slot | Deferred behind a measurement: build it only if the recorded data asks for it |
| 3. Hybrid furnace control | Kept. The per-colony part first, the empire part after the fleet has grown |
| 4. Logistics | The hold was replaced by counting ships in flight. Transports are built on demand. The slot reservation was dropped |
| 5. Re-tune on drift | Kept, without the "15 steps" trigger |
| 6. Status strip | Kept. It shows why each colony is idle. One switch was added |
