# Batch results

60 synthetic cases, seed 42, behaviour scale 1.0. Every policy ran over the identical batch with the same random draws, so the differences below are decisions, not luck.

## Recovery by policy

| policy | recovered | rate | ₹ recovered | ₹ cost | ₹ net | vs do-nothing | contacts | per recovery | median h | escalated | opt-outs | disputes | violations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| rules | 34 | 56.7% | ₹342,593 | ₹1,018 | ₹341,575 | ₹0 | 94 | 2.76 | 26.0 | 4 | 4 | 2 | 0 |
| agent | 33 | 55.0% | ₹342,094 | ₹48 | ₹342,045 | ₹0 | 96 | 2.91 | 26.1 | 5 | 4 | 0 | 0 |

`violations` counts actions that broke a rule in `policy.yaml`, judged by the same engine for every policy. Razorpay's own subscription retry ladder runs under `do_nothing` and is excluded from its count, since it is the platform's behaviour and not a policy we are scoring.

## Recovery rate by root cause

| root cause | cases | rules | agent |
|---|---|---|---|
| ABANDONED_CHECKOUT | 9 | 11% | 11% |
| CUSTOMER_ABANDON | 4 | 25% | 25% |
| CUSTOMER_INPUT | 4 | 25% | 25% |
| INSTRUMENT_BLOCKED | 3 | 33% | 33% |
| INSUFFICIENT_FUNDS | 14 | 71% | 64% |
| LIMIT_EXCEEDED | 3 | 67% | 67% |
| MERCHANT_CONFIG | 3 | 100% | 100% |
| OVERDUE_RECEIVABLE | 12 | 67% | 67% |
| TRANSIENT_TECH | 5 | 100% | 100% |
| UNKNOWN | 3 | 67% | 67% |

## Where the deterministic planner beat the model-advised one

1 case(s) — cases the rules planner recovered and the model-advised planner did not — the comparison that decides whether the model earns its place.

| case | cause | amount | rules | agent |
|---|---|---|---|---|
| case_0028 | INSUFFICIENT_FUNDS | ₹499 | recovered | escalated_unresolved |

## Where the model-advised planner beat the deterministic one

0 case(s) — the same comparison in the other direction.

## Reading customer replies — rules

- 33 replies read, 93.9% matched what the customer meant
- 33 read by the language model, 0 by pattern alone
- opt-outs and disputes caught: 4/4 — these are matched by pattern as well as by model, so a model error cannot keep someone in a sequence they asked to leave

## Reading customer replies — agent

- 33 replies read, 93.9% matched what the customer meant
- 33 read by the language model, 0 by pattern alone
- opt-outs and disputes caught: 2/2 — these are matched by pattern as well as by model, so a model error cannot keep someone in a sequence they asked to leave

- model calls: 386 (37 served from cache, 17 failed)
- budget exhausted mid-run: False
