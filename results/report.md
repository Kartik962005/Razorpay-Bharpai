# Batch results

500 synthetic cases, seed 42, behaviour scale 1.0. Every policy ran over the identical batch with the same random draws, so the differences below are decisions, not luck.

## Recovery by policy

| policy | recovered | rate | ₹ recovered | ₹ cost | ₹ net | vs do-nothing | contacts | per recovery | median h | escalated | opt-outs | disputes | violations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| do_nothing | 111 | 22.2% | ₹453,210 | ₹13 | ₹453,197 | ₹0 | 0 | 0.00 | 24.0 | 0 | 0 | 0 | 0 |
| naive | 148 | 29.6% | ₹1,644,185 | ₹63,141 | ₹1,581,044 | ₹1,127,847 | 750 | 5.07 | 22.6 | 0 | 36 | 126 | 2220 |
| rules | 249 | 49.8% | ₹1,835,576 | ₹658 | ₹1,834,917 | ₹1,381,720 | 811 | 3.26 | 26.4 | 71 | 38 | 1 | 0 |

`violations` counts actions that broke a rule in `policy.yaml`, judged by the same engine for every policy. Razorpay's own subscription retry ladder runs under `do_nothing` and is excluded from its count, since it is the platform's behaviour and not a policy we are scoring.

## Recovery rate by root cause

| root cause | cases | do_nothing | naive | rules |
|---|---|---|---|---|
| ABANDONED_CHECKOUT | 91 | 9% | 9% | 13% |
| CUSTOMER_ABANDON | 54 | 9% | 9% | 26% |
| CUSTOMER_INPUT | 29 | 17% | 17% | 38% |
| INSTRUMENT_BLOCKED | 22 | 27% | 27% | 50% |
| INSUFFICIENT_FUNDS | 98 | 41% | 47% | 60% |
| LIMIT_EXCEEDED | 28 | 29% | 18% | 82% |
| MANDATE_ISSUE | 24 | 12% | 12% | 29% |
| MERCHANT_CONFIG | 10 | 10% | 10% | 100% |
| OVERDUE_RECEIVABLE | 79 | 14% | 66% | 65% |
| RISK_DECLINE | 4 | 25% | 25% | 25% |
| TRANSIENT_TECH | 50 | 42% | 16% | 86% |
| UNKNOWN | 11 | 18% | 73% | 64% |

## Where the naive policy beat the agent

15 case(s). These are cases the cause-blind policy recovered and Wapsi did not, usually because Wapsi declined to contact someone the rules protect.

| case | cause | amount | naive | wapsi |
|---|---|---|---|---|
| case_0024 | UNKNOWN | ₹325 | recovered | escalated_unresolved |
| case_0079 | OVERDUE_RECEIVABLE | ₹15,006 | recovered | opted_out |
| case_0082 | INSUFFICIENT_FUNDS | ₹18,000 | recovered | gave_up |
| case_0083 | MANDATE_ISSUE | ₹299 | recovered | gave_up |
| case_0152 | INSUFFICIENT_FUNDS | ₹199 | recovered | opted_out |
| case_0198 | OVERDUE_RECEIVABLE | ₹23,730 | recovered | gave_up |
| case_0207 | INSUFFICIENT_FUNDS | ₹1,499 | recovered | escalated_unresolved |
| case_0240 | INSUFFICIENT_FUNDS | ₹299 | recovered | gave_up |
| case_0283 | OVERDUE_RECEIVABLE | ₹14,616 | recovered | opted_out |
| case_0288 | OVERDUE_RECEIVABLE | ₹58,830 | recovered | escalated_unresolved |
| case_0293 | OVERDUE_RECEIVABLE | ₹20,761 | recovered | escalated_unresolved |
| case_0309 | OVERDUE_RECEIVABLE | ₹17,900 | recovered | gave_up |
| case_0400 | OVERDUE_RECEIVABLE | ₹3,366 | recovered | escalated_unresolved |
| case_0411 | OVERDUE_RECEIVABLE | ₹13,219 | recovered | escalated_unresolved |
| case_0492 | OVERDUE_RECEIVABLE | ₹29,358 | recovered | escalated_unresolved |
