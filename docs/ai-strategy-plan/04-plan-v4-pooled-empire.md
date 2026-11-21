# Plan v4: the empire as one pool

**Status: built; verdict pending.** Live since **07:51:42 UTC on 2026-11-20** (commit `d233335`, refined in `676949b`), behind the same switch as plans v2 and v3. It cannot be judged before 07:52 on 2026-11-21.

The operator asked the assistant to review everything, to implement what would give the best results, and not to ask for decisions. Every decision below was taken by the assistant. Each is listed with how to reverse it.

## Why

[Plan v3](03-plan-v3-real-income.md) had run for twelve hours. Measured on 2026-11-20 between 06:15 and 07:30:

| Finding | Measured |
|---|---|
| Builders were not busier | 1.76 of 11 busy over 11.18 running hours. Before any change: 2.10 |
| Colonies still advanced faster | 9.3 building levels per hour, against 5.1 before |
| The empire was limited by income, not by builders | About 92% of the Pig iron, 80% of the Metal and all of the Kryptonite earned in 12.5 hours had been spent. One Development center level 10 cost about an hour of Metal income and kept a builder busy for 36 minutes |
| Metal was what colonies waited for | 61% of colony time waiting for Metal |
| Furnaces ran below what they could make | 245,400 Metal per hour on average, of up to 427,100 |
| Why | Each colony smelted only for its own next step. The rule that let a colony smelt for the others needed a transport on that colony and was skipped whenever the colony had a need of its own |
| Every colony saved alone | At 06:40 seven colonies sat idle, 2.3 to 7.2 hours from their step. The empire's stock, pooled, covered all seven steps at once |
| The plan's order did not hold between colonies | One colony held 398,000 Metal for step 718 of 724 while four earlier steps waited for Metal elsewhere |
| Transports were on raids | 61 of 88 were flying raids and 9 were parked. A raid takes every parked transport of its colony |
| Raids return full | Four raids on Spacia planets came back carrying exactly their cargo capacity |

The simulator treats the empire's stock as one pool. Live, each colony was an island.

## What the assistant had wrong before this plan

- **"The transport fleet is over-bought."** Its utilization read 3%, but that figure counts shipments between colonies only. The hulls were raiding and coming back full.
- **"Keep the plan's Large transports out of raids."** Each brings back 30,000 resources per trip and costs 14,000. That change would have lowered income, and it was dropped.
- **The mark of 4.2 builders busy** proposed for plan v3 was more than the empire's income could pay for.

## What changed from plan v3

| # | Rule | Detail |
|---|---|---|
| E1 | **Furnaces smelt for the empire.** | A colony whose own steps do not ask for smelting smelts while the waiting steps of all colonies cost more Metal than the empire holds, cargo in flight included. A furnace that is on stays on until the pooled Metal is 10% above that cost. Not when Pig iron is the scarcer resource, and not without the energy. A transport on the colony is no longer required |
| E2 | **Fill rule.** | A colony whose builder is free while its next step waits starts the first later step of its plan that is ready and that it can pay for now. The fill may hold the waiting step back by at most `patience` times its own duration, where `patience` is the plan's tuned value kept between 0.25 and 0.75. It spends only resources of which the empire's stock covers every colony's next step. At most one per colony, and the waiting step keeps its place |
| E3 | **Loans in plan order.** | Shipments between colonies serve the plan's steps in plan order. A colony that cannot yet pay for its own step lends what it is saving to a waiting step that comes earlier. A loan flies when it pays for the whole need, or carries at least 20,000 and a quarter of it. It does not fly for an amount the borrower makes itself before the cargo lands |
| E4 | **The transport target leaves out raiding hulls.** | Hulls on an attack flight no longer count toward the fleet the plan sizes for shipments. Plan-sized orders stop when the whole fleet reaches twice the target, or 900,000 cargo above it, whichever is lower |

Two refinements came 40 minutes after the first landing (`676949b`): the 900,000 bound in E4, after eight Large transports had been ordered in ten minutes, and the last condition of E3.

## Decisions taken for the operator

| Decision | Value | To reverse |
|---|---|---|
| Everything in plan v4 | Behind `ai_adaptive_execution` | Turn the switch off. There is no switch for plan v4 alone |
| Furnace hold band and Pig iron reserve | 10% and 50,000 | `EMPIRE_SMELT_HOLD_RATIO`, `EMPIRE_SMELT_MIN_SPARE_PIG` in `backend/planner/heuristics/_types.py` |
| Fill patience bounds | 0.25 to 0.75 | `AI_FILL_MIN_PATIENCE`, `AI_FILL_MAX_PATIENCE` in `backend/planner/ai/live_fill.py` |
| Lead time before a loan can land | 20 minutes | `AI_FILL_POOL_LEAD_HOURS` in `backend/planner/ai/_fill_helpers.py` |
| Smallest loan | 20,000 cargo and a quarter of the need, unless it pays for all of it | `PLAN_ORDER_LOAN_MIN_CARGO`, `PLAN_ORDER_LOAN_MIN_SHARE` in `backend/planner/heuristics/_types.py` |
| Fleet ceiling | Twice the target, and at most 900,000 cargo above it | `INFRA_TRANSPORT_FLEET_CAP_RATIO`, `INFRA_TRANSPORT_RAID_ALLOWANCE_CARGO` in the same file |
| Transports are not reserved from raids | Raids take every parked hull, as before | Not built |
| Pass marks | See below | Constants `V4_*` in `scripts/_validate_adaptive_sections.py` |
| The questions plan v3 left open | 25% transport share and 4-hour saving window kept; fill rule built; tie-break not built | |

Until the refactor of 2026-11-20, reverting the two plan v4 commits was a second way back. It no longer applies cleanly, because the files those commits changed were split into packages.

Left as it was: the operator's 10% spare ship budget, how raids pick targets and ships, the planner's simulator and score, the page-view limit and every anti-bot safeguard.

## Not built

| Item | Why not |
|---|---|
| **Raids.** A raid takes whatever is parked, so most carry little. A target seems to accept about three raids a day. One Small transport makes the whole fleet almost twice as slow | The largest lever found. Choosing a raid's ships means changing the raid dispatch in three places, and another work session was editing those files |
| Keeping some transports home for shipments | Same files |
| Shipping resources so that a fill becomes affordable | Fills use what the colony already holds. Loans serve the waiting steps first |
| Second build slot | Its gate did not trip: 1.84% of colony time ready but not started, against a gate of 5% |
| Cargo limit in the simulator | Its gate did trip: delivered cargo was 14% of planned, against a gate of 80%. It was still not built. Most hulls were on raids, which a cargo limit does not change, and the simulator was left alone while another work session was changing the planner |
| A tie-break that prefers the busier of two plans with nearly the same score | It changes what the planner optimizes |

## How it will be judged

After 07:52 on 2026-11-21, with at least 12 running hours, run `python3 scripts/validate_adaptive_execution.py`. It judges the period since the landing against four marks. The assistant set the marks; the operator has not confirmed them.

| # | Measure | Plan v3 | Mark |
|---|---|---|---|
| 1 | Metal made while the bot ran, as a share of what the furnaces could make at the time | 245,400 per hour | 80% or more |
| 2 | Builders busy while the bot ran | 1.76 of 11 | 3.0 or more. Below 2.3 means no effect |
| 3 | Building levels completed per hour | 9.3 | 12 or more |
| 4 | Colony time idle with an affordable step, last 24 hours | 78.6% | 50% or less |

Mark 1 was computed differently for the first hour: the period's mean divided by the capacity at the moment of the run. Plan v4 builds furnace levels, so that figure fell as the plan worked, and it read 79% while every reading had the furnaces at 87% to 94% of their capacity. The assistant changed the computation after seeing that reading and said so. The script now compares each recorded point with the capacity at its own time. The mark itself stayed at 80%.

Before concluding, check what else changed in the window. The measurement record lists it: [Before concluding](../notes/adaptive-execution-plan-v2.md#before-concluding).

## Readings

### First hour (not evidence)

Read at 09:01 on 2026-11-20, over 1.06 running hours: 4.69 of 11 builders busy, none just before the landing; 22 building levels, 10 of them Blast furnace levels; furnaces on at 10 of 11 colonies; 411,200 Metal per hour.

### After 18.8 hours (not a verdict)

Read at 02:41 on 2026-11-21, over 17.95 running hours.

| # | Measure | Reading | Against the mark |
|---|---|---|---|
| 1 | Metal made as a share of what the furnaces could make | 320,400 per hour, 48% (between 45% and 48%) | Below 80% |
| 2 | Builders busy | 3.36 of 11. 3.90 over the last 6 hours | Above 3.0 |
| 3 | Building levels per hour | 11.58 (218 levels) | Just below 12 |
| 4 | Idle with an affordable step, last 24 hours | 49.0% | At 50%. The window still holds five hours of plan v3 |

Other readings at 02:41:

| Measure | Plan v3, or the day before | At 02:41 |
|---|---|---|
| Colony time waiting for Metal, last 24 hours | 60.9% | 30.2% |
| Blast furnace levels, all colonies | 174 at the landing | 197 |
| What the furnaces can make, Metal per hour | 427,100 at the landing | 719,300 |
| Cargo delivered between colonies per hour | 140,800 | 577,400 |
| Loot of the last 24 hours | 1.08 million from 26 raids, 41,500 per raid | 6.19 million from 30 raids, 206,400 per raid |
| Transport fleet | 48 Small and 40 Large, 1,488,000 cargo | 47 Small, 56 Large and 7 Transmitters, 3,778,000 cargo |

**Mark 1 is on course to fail, and part of the reason is the mark.** It asks for Metal output near furnace capacity. It was written when Metal was what every waiting step lacked. That changed during the period. At 23:21 on 2026-11-20 and again at 02:41 all eleven furnaces were off, and between 00:04 and 02:52 Metal output averaged 36,800 per hour. At 02:41 no next step of the plan was short of Metal (648,000 in stock against 608,000 needed); the next steps were short of Kryptonite instead. Turning the furnaces off in that state is what rule E1 is meant to do, since smelting takes two Pig iron for each Metal. A furnace that is off for that reason still counts against the mark. The mark was already changed once after a failing reading, so it is left as it is. The verdict should read it next to the share of colony time spent waiting for Metal.

Mark 3 falls on its own as the empire matures: the levels being built now take longer. One of the builds running at 23:21 was a Pig iron mine level 25 with 2 hours 18 minutes left.

The fleet is above the ceiling of rule E4 (2,661,000 cargo at 02:41), so no plan-sized order is pending. How it got above was not traced. The target moves with every re-tune and the ceiling moves with it, and hulls bought toward the operator's own shipyard targets are not bound by the ceiling.

### What else changed in the window

Period E is not plan v4 alone.

| Time (UTC) | Event |
|---|---|
| 2026-11-20 11:22 | First part of the whole-system refactor reached `main` |
| 2026-11-20 14:40 | Second part: removal of unused code, the older genetic-algorithm planner among it |
| 2026-11-20 15:44 | Anti-bot gate. The bot stood still until 16:05 |
| 2026-11-20 22:11 | The refactored backend reached `main`, with fixes until 23:43 |
| 2026-11-21 00:04 | The package was renamed to `backend/` and the stack restarted |
| 2026-11-21 00:29 | Database schema changes and console polish reached `main` |

The recorded series shows 26 stops of 5 to 22 minutes since the landing. Every restart of the stack costs one.

Builders busy by stretch, from the recorded series read at 02:52:

| Stretch | Builders busy | Running hours | Metal per hour |
|---|---|---|---|
| 07:51 to 11:22, plan v4 as it landed | 3.91 | 3.35 | 523,400 |
| 11:22 to 14:40 | 2.83 | 3.07 | 445,200 |
| 14:40 to 22:11 | 2.97 | 7.00 | 375,400 |
| 22:11 to 00:04, refactored backend | 5.30 | 1.86 | 186,400 |
| 00:04 to 02:52, renamed package | 2.91 | 2.79 | 36,800 |

These differences are not evidence about the refactor. The [check after the refactor](05-refactor-check.md) found the planner's decisions unchanged, and the empire's state moved on over the same hours: what the plan waits for went from Metal to Kryptonite and Pig iron.

## Known problems

- **Builders stay partly idle.** Income decides how many steps can be paid for. Plan v4 uses the income better; it does not create more.
- **Raids take every parked transport.** Loans and other shipments happen in the minutes a colony's hulls are home.
- **A fill can leave its colony short of Kryptonite or Pig iron for the waiting step.** The rule allows it when the colony earns the amount back in time or the empire holds a surplus.
- **The fill rule fails quietly.** An error inside it is caught so that the plan's own steps still run. Since the refactor it is logged as a warning. The sign would be no fills over a long time.
- **Executed fills and loans cannot be counted afterwards.** The action log keeps the result message, not the reason.
- **The console's fleet line counts every hull.** It can say the target is met while plan-sized orders continue.

## Tests

- 30 tests in `tests/unit/planner/test_adaptive_v4.py`. The furnace test in `tests/unit/planner/test_strategy.py` was changed to pin rule E1.
- The full unit suite on the final code of plan v4: 705 tests, with the same 3 failures as without the change.
- Old and new code on the same live snapshot, without touching the game: the old code gave 0 fills, 2 shipments and no furnace change; the new code gave 4 fills, 2 loans that each pay for a whole step, and 2 furnaces turned on.
- After the refactor every suite passes. See the [check after the refactor](05-refactor-check.md).
