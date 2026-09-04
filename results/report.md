# Batch results

500 synthetic cases, seed 42, behaviour scale 1.0. Every policy ran over the identical batch with the same random draws, so the differences below are decisions, not luck.

## Recovery by policy

| policy | recovered | rate | ₹ recovered | ₹ cost | ₹ net | vs do-nothing | contacts | per recovery | median h | escalated | opt-outs | disputes | violations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| do_nothing | 111 | 22.2% | ₹453,210 | ₹13 | ₹453,197 | ₹0 | 0 | 0.00 | 24.0 | 0 | 0 | 0 | 0 |
| naive | 148 | 29.6% | ₹1,644,185 | ₹63,141 | ₹1,581,044 | ₹1,127,847 | 750 | 5.07 | 22.6 | 0 | 104 | 126 | 2256 |
| rules | 233 | 46.6% | ₹1,724,498 | ₹659 | ₹1,723,840 | ₹1,270,643 | 816 | 3.50 | 25.3 | 50 | 62 | 1 | 0 |
| agent | 234 | 46.8% | ₹1,764,020 | ₹662 | ₹1,763,358 | ₹1,310,161 | 817 | 3.49 | 25.3 | 50 | 61 | 1 | 0 |

`violations` counts actions that broke a rule in `policy.yaml`, judged by the same engine for every policy. Razorpay's own subscription retry ladder runs under `do_nothing` and is excluded from its count, since it is the platform's behaviour and not a policy we are scoring.

## Recovery rate by root cause

| root cause | cases | do_nothing | naive | rules | agent |
|---|---|---|---|---|---|
| ABANDONED_CHECKOUT | 91 | 9% | 9% | 12% | 12% |
| CUSTOMER_ABANDON | 54 | 9% | 9% | 22% | 22% |
| CUSTOMER_INPUT | 29 | 17% | 17% | 31% | 31% |
| INSTRUMENT_BLOCKED | 22 | 27% | 27% | 36% | 36% |
| INSUFFICIENT_FUNDS | 98 | 41% | 47% | 57% | 57% |
| LIMIT_EXCEEDED | 28 | 29% | 18% | 82% | 82% |
| MANDATE_ISSUE | 24 | 12% | 12% | 29% | 29% |
| MERCHANT_CONFIG | 10 | 10% | 10% | 100% | 100% |
| OVERDUE_RECEIVABLE | 79 | 14% | 66% | 58% | 60% |
| RISK_DECLINE | 4 | 25% | 25% | 25% | 25% |
| TRANSIENT_TECH | 50 | 42% | 16% | 86% | 86% |
| UNKNOWN | 11 | 18% | 73% | 64% | 64% |

## Where the cause-blind policy beat the agent

19 case(s) — cases the naive policy recovered and Wapsi did not, usually because Wapsi declined to contact someone the rules protect.

| case | cause | amount | naive | rules |
|---|---|---|---|---|
| case_0024 | UNKNOWN | ₹325 | recovered | escalated_unresolved |
| case_0079 | OVERDUE_RECEIVABLE | ₹15,006 | recovered | opted_out |
| case_0082 | INSUFFICIENT_FUNDS | ₹18,000 | recovered | gave_up |
| case_0083 | MANDATE_ISSUE | ₹299 | recovered | gave_up |
| case_0118 | INSUFFICIENT_FUNDS | ₹1,499 | recovered | opted_out |
| case_0134 | OVERDUE_RECEIVABLE | ₹39,522 | recovered | opted_out |
| case_0152 | INSUFFICIENT_FUNDS | ₹199 | recovered | opted_out |
| case_0198 | OVERDUE_RECEIVABLE | ₹23,730 | recovered | gave_up |
| case_0207 | INSUFFICIENT_FUNDS | ₹1,499 | recovered | opted_out |
| case_0240 | INSUFFICIENT_FUNDS | ₹299 | recovered | gave_up |
| case_0261 | INSUFFICIENT_FUNDS | ₹999 | recovered | opted_out |
| case_0283 | OVERDUE_RECEIVABLE | ₹14,616 | recovered | opted_out |
| case_0288 | OVERDUE_RECEIVABLE | ₹58,830 | recovered | escalated_unresolved |
| case_0293 | OVERDUE_RECEIVABLE | ₹20,761 | recovered | escalated_unresolved |
| case_0309 | OVERDUE_RECEIVABLE | ₹17,900 | recovered | gave_up |

## Where the deterministic planner beat the model-advised one

0 case(s) — cases the rules planner recovered and the model-advised planner did not — the comparison that decides whether the model earns its place.

## Where the model-advised planner beat the deterministic one

1 case(s) — the same comparison in the other direction.

| case | cause | amount | agent | rules |
|---|---|---|---|---|
| case_0134 | OVERDUE_RECEIVABLE | ₹39,522 | recovered | opted_out |

## Reading customer replies — naive

- 481 replies read, 75.3% matched what the customer meant
- 0 read by the language model, 481 by pattern alone
- opt-outs and disputes caught: 152/198 — these are matched by pattern as well as by model, so a model error cannot keep someone in a sequence they asked to leave

## Reading customer replies — rules

- 298 replies read, 91.6% matched what the customer meant
- 0 read by the language model, 298 by pattern alone
- opt-outs and disputes caught: 38/39 — these are matched by pattern as well as by model, so a model error cannot keep someone in a sequence they asked to leave

## Reading customer replies — agent

- 297 replies read, 92.3% matched what the customer meant
- 284 read by the language model, 13 by pattern alone
- opt-outs and disputes caught: 39/39 — these are matched by pattern as well as by model, so a model error cannot keep someone in a sequence they asked to leave

- model calls: 604 (298 served from cache, 114 failed)
- budget exhausted mid-run: True
