# Plan v3: plan on the income that arrives

**Status: built.** Live since **18:26:36 UTC on 2026-11-19** (commit `124bc85`), behind the same switch as [plan v2](02-plan-v2-execution-follows-the-plan.md). [Plan v4](04-plan-v4-pooled-empire.md) followed twelve hours later.

## Why

Plan v2 had run for four hours. The furnaces now followed the plan, and Metal made per hour had gone from 50,600 to 202,400. But the plan itself was built on an income that did not exist.

| Finding | Measured |
|---|---|
| The planner assumed every fleet slot raids back to back | 989,000 to 1,073,000 Metal per hour of raid income in the plan |
| What the empire received | 47,000 to 53,000 Metal per hour over 24 hours. About 20 times less |
| What that did to the plan | Raid income paid for 94% to 112% of everything the plan spends. Furnaces were planned to smelt 3% of the time |
| The spare ship budget | 124,000 Metal allowed on every planning call, with no memory. The furnaces made 122,000 to 240,000 per hour |
| Shipyards | Plan v2 had removed the hold that kept them idle, so combat ships were ordered whenever a call allowed it |
| Transport target | A fleet measured as idle raised its own target from 54 to 135 Large transports |
| Background re-tune | It fired because the median loot of one raid moved 25%. It moved another 33% within 25 minutes |

## What changed from plan v2

Plan v3 keeps every step of plan v2 and adds five decisions.

| # | Decision | Why | Effect measured ten minutes after it went live |
|---|---|---|---|
| D1 | Raid income is the loot and recycling received in the last 24 hours, per fleet slot | The model was 20 times too high and paid for the whole plan | The plan assumes 52,862 Metal per hour of raid income. It pays for 6% of the plan's spending |
| D2 | Re-tune once when that basis changes. Stop re-tuning when the median loot of one raid moves | A plan on received income does not depend on that median | The planner re-tuned on its own at 18:26:52. The new plan was active 32 seconds later |
| D3 | Ship budgets are hourly rates on received income. The spare budget saves up for 4 hours at most and remembers what it spent | "10%" was a fresh allowance on every call | 27,992 Metal per hour for combat ships |
| D4 | Plan-sized transports are ordered up to 10 per order, ahead of the fleet missions | One hull per order was starved of page views | Not exercised at first: the fleet was at its target |
| D5 | Sizing never assumes a utilization below 0.5, and hulls ordered in the last hour count | An idle fleet is not evidence that more hulls are needed | Target 193,058 cargo against a fleet of 264,000 |

Kept as they were:

| Topic | Decision | Reason |
|---|---|---|
| Donor furnaces | On from the start, not after the fleet has grown | Pig iron was plentiful and Metal was what colonies waited for |
| New transports leaving on raids | Allowed | Nothing reserved them, and every hull was in flight |
| Fleet orders that abort with no ships | Left alone | Older than this work. The colony is held for 180 seconds |
| Anti-bot gates and the page-view limit | Not touched | They are never bypassed |

## Decisions

The operator asked the assistant to fix what could be fixed already and to rewrite the plan with the new decisions. D1 to D5 were taken by the assistant on that basis and were live within the hour.

Five questions were left to the operator in the plan. None was answered directly. The next instruction, on the following morning, was to review everything and to implement what would give the best results without asking for decisions. The assistant then settled them in plan v4.

| Question left open | How it was settled in plan v4 |
|---|---|
| Pass marks: "worked" at 4.2 builders busy, "no effect" below 2.6 | Replaced. 4.2 was more than the empire's income could pay for |
| Transport share of 25% of income | Kept |
| Spare budget saving window of 4 hours | Kept |
| Build the fill rule now, or after 24 hours of data | Built, as a live rule |
| A tie-break in the planner for plans with nearly the same score | Not built |

## What it showed about the planner's score

Four planner runs on the same empire, without touching the game:

| Run | Blast furnace upgrades in the first 60 steps | Steps started in the first hour | In 24 hours | Score |
|---|---|---|---|---|
| Model income | at most 1 | 13 | 255 | 9.384 billion |
| Received income, dry run at 18:14 | 25 | 32 | 194 | 7.183 billion |
| Received income, live re-tune at 18:27 | 0 | 6 | 125 | 7.216 billion |
| Received income, dry run at 18:31 | 0 | 6 | 151 | 7.207 billion |

The three plans on received income score within 0.5% of each other, and one of them starts five times as many steps in the first hour. The planner's score does not separate a plan that keeps builders busy from one that waits. Which one it returns is close to chance.

## What happened

Read at 06:15 on 2026-11-20, after 11.9 hours, 11.18 of them running.

| Measure | Before any change | Plan v3 |
|---|---|---|
| Builders busy while the bot ran | 2.10 of 11 | 1.76 of 11 |
| Building levels completed per hour | 5.1 | 9.3. 23 of the 111 levels were Blast furnace levels |
| Metal made per hour while the bot ran | 50,600 | 245,400 |
| Colony time idle with an affordable step | 81.8% | 78.6% |
| Steps built against steps the plan scheduled | | 11 of 13, 1.7 hours into the plan |
| Raid income in the plan | 94% to 112% of its spending | 7% of its spending |

Builders were not busier. Colonies still advanced faster: the furnaces made Metal, and the empire kept to its plan. But a plan on received income schedules few steps, and each colony waited for its own.

Since the database was moved on 2026-11-20, about two hours of the recorded points of this period are gone. The validation script now reports it as 1.50 builders over 10.96 running hours.

## What it did not solve

Colonies still waited: 78.6% of colony time idle while some step of the target was affordable. The review that led to [plan v4](04-plan-v4-pooled-empire.md) found why.

## Tests

11 tests in `tests/unit/planner/test_adaptive_followup.py`. The full unit suite at the time: 675 tests, with the same 3 failures as without the change. The end-to-end suite and the frontend tests were not run; no frontend file changed.
