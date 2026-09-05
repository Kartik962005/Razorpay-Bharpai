# Batch results

60 synthetic cases, seed 42, behaviour scale 1.0. Every policy ran over the identical batch with the same random draws, so the differences below are decisions, not luck.

## Recovery by policy

| policy | recovered | rate | ₹ recovered | ₹ cost | ₹ net | vs do-nothing | contacts | per recovery | median h | escalated | opt-outs | disputes | violations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| do_nothing | 10 | 16.7% | ₹30,489 | ₹1 | ₹30,488 | ₹0 | 0 | 0.00 | 45.2 | 0 | 0 | 0 | 0 |
| naive | 21 | 35.0% | ₹302,360 | ₹6,016 | ₹296,344 | ₹265,856 | 98 | 4.67 | 39.0 | 0 | 12 | 12 | 266 |
| rules | 34 | 56.7% | ₹342,593 | ₹1,018 | ₹341,575 | ₹311,088 | 93 | 2.74 | 26.0 | 4 | 4 | 2 | 0 |

`violations` counts actions that broke a rule in `policy.yaml`, judged by the same engine for every policy. Razorpay's own subscription retry ladder runs under `do_nothing` and is excluded from its count, since it is the platform's behaviour and not a policy we are scoring.

## Recovery rate by root cause

| root cause | cases | do_nothing | naive | rules |
|---|---|---|---|---|
| ABANDONED_CHECKOUT | 9 | 11% | 11% | 11% |
| CUSTOMER_ABANDON | 4 | 0% | 0% | 25% |
| CUSTOMER_INPUT | 4 | 25% | 25% | 25% |
| INSTRUMENT_BLOCKED | 3 | 0% | 0% | 33% |
| INSUFFICIENT_FUNDS | 14 | 43% | 50% | 71% |
| LIMIT_EXCEEDED | 3 | 0% | 0% | 67% |
| MERCHANT_CONFIG | 3 | 0% | 0% | 100% |
| OVERDUE_RECEIVABLE | 12 | 8% | 67% | 67% |
| TRANSIENT_TECH | 5 | 20% | 20% | 100% |
| UNKNOWN | 3 | 0% | 100% | 67% |

## Where the cause-blind policy beat the agent

2 case(s) — cases the naive policy recovered and Bharpai did not, usually because Bharpai declined to contact someone the rules protect.

| case | cause | amount | naive | rules |
|---|---|---|---|---|
| case_0024 | UNKNOWN | ₹325 | recovered | escalated_unresolved |
| case_0055 | OVERDUE_RECEIVABLE | ₹12,318 | recovered | disputed |

## Reading customer replies — naive

- 55 replies read, 78.2% matched what the customer meant
- 0 read by the language model, 55 by pattern alone
- opt-outs and disputes caught: 19/23 — these are matched by pattern as well as by model, so a model error cannot keep someone in a sequence they asked to leave
- of the misreadings, 8 stopped contact more readily than the label required and 4 did the opposite — 92.7% were safe in that sense

## Reading customer replies — rules

- 33 replies read, 90.9% matched what the customer meant
- 0 read by the language model, 33 by pattern alone
- opt-outs and disputes caught: 3/4 — these are matched by pattern as well as by model, so a model error cannot keep someone in a sequence they asked to leave
- of the misreadings, 2 stopped contact more readily than the label required and 1 did the opposite — 97.0% were safe in that sense
