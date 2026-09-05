# Wapsi

[![tests](https://github.com/Kartik962005/Razorpay-Wapsi-/actions/workflows/tests.yml/badge.svg)](https://github.com/Kartik962005/Razorpay-Wapsi-/actions/workflows/tests.yml)

**Cause-aware revenue recovery for Razorpay merchants.** Razorpay AI Buildathon — Track 3.

*wapsi (वापसी): return, comeback.*

A payment fails. Most recovery systems then do the same thing regardless of *why* it failed:
retry on a fixed ladder, send a reminder on a fixed cadence. That treats "the bank was down for
twenty minutes" identically to "this card is blocked" and to "the customer typed the wrong CVV" —
three failures with completely different answers. The retries that cannot work burn NPCI attempt
limits, the reminders that arrive at the wrong hour cost goodwill, and the money that was
genuinely recoverable is left on the table.

Wapsi reads Razorpay's own failure signals, works out *why* each payment failed, picks the one
intervention that fits that cause, and executes it inside hard regulatory and economic bounds —
then reports what it recovered, net of what it cost, alongside everything it deliberately gave up
on.

---

## Results

500 synthetic cases. Every policy ran over the **identical batch with the same random draws**, so
the differences below are decisions, not luck.

| policy | recovered | rate | ₹ recovered | ₹ cost | **₹ net** | messages | opt-outs | disputes | **rule violations** |
|---|---|---|---|---|---|---|---|---|---|
| do nothing | 111 | 22.2% | ₹4,53,210 | ₹13 | ₹4,53,197 | 0 | 0 | 0 | 0 |
| **Razorpay defaults** (retry ladder + `reminder_enable`) | 141 | 28.2% | ₹14,54,810 | ₹6,715 | ₹14,48,095 | 1,196 | 140 | 13 | — |
| naive (retry ×3, then text) | 148 | 29.6% | ₹16,44,185 | ₹63,141 | ₹15,81,044 | 750 | 104 | 126 | **2,256** |
| **Wapsi** | **233** | **46.6%** | ₹17,24,498 | ₹659 | **₹17,23,840** | 816 | 62 | 1 | **0** |
| Wapsi, model-advised | 232 | 46.4% | ₹17,24,199 | ₹676 | ₹17,23,524 | 818 | 72 | 1 | **0** |

**₹2.8 lakh more than Razorpay's own defaults recover, with a third fewer messages and less than
half the opt-outs. ₹12.7 lakh more than doing nothing. Zero rule violations.**

The fair comparison is the second row — what a merchant gets from the platform unprompted — and
that is the claim: Wapsi recovers 65% more cases than the defaults while sending 1,196 → 816
messages, because it sends the *right* message rather than three generic ones. The defaults lose
140 customers to opt-outs, 8.5 messages per recovery; Wapsi loses 62.

The naive row is what untuned merchant automation actually does, and it is worth keeping because it
shows what the rules are for. It spends 95× more than Wapsi and 126 of its recoveries turn into
chargebacks. Judged by the same policy engine Wapsi obeys, it breaks rules 2,256 times — including
**112 messages sent to people who had already said stop** and 16 retries of payments that risk had
declined. (The defaults row is not scored for violations: those are the platform's actions, not a
merchant's policy.)

Recovery rate by root cause — this is where cause-awareness shows up:

| root cause | cases | do nothing | naive | **Wapsi** |
|---|---|---|---|---|
| bank/PSP outage | 50 | 42% | 16% | **86%** |
| daily limit hit | 28 | 29% | 18% | **82%** |
| merchant misconfiguration | 10 | 10% | 10% | **100%** |
| insufficient funds | 98 | 41% | 47% | **60%** |
| blocked instrument | 22 | 27% | 27% | **50%** |
| customer input error | 29 | 17% | 17% | **38%** |
| abandoned checkout | 91 | 9% | 9% | 13% |
| overdue receivable | 79 | 14% | **66%** | 65% |

Retrying into an outage is worse than doing nothing — the naive policy proves it, scoring 16%
against a 42% baseline on exactly the class of failure that retrying is supposed to fix.

**Sensitivity, two ways.** First, every behaviour prior scaled by 0.7 and 1.3: the ranking is
unchanged. Second — the test a sceptical reviewer should demand — every assumption that *flatters*
the compliant policy turned down hard: night-time messages barely annoy anyone and never trigger a
dispute, customers tolerate twice as many messages before opting out, retrying a risk-declined
payment never causes a chargeback, and the chargeback fee is zero. **Wapsi still wins: ₹20.1L
against ₹18.0L for the naive policy and ₹15.4L for Razorpay's own defaults.** The result does not
depend on the simulation punishing carelessness. See [`results/sensitivity.md`](results/sensitivity.md).

### It also works on a real account

Not the simulation — a genuine payment on a Razorpay test-mode account, failed by hand and
recovered by the agent fourteen hours later. Four lines from
[`results/live_recovery.md`](results/live_recovery.md):

```
04 Sep 23:24  observation  detected on Razorpay: payment_failed, ₹1,299 on netbanking
04 Sep 23:25  verdict      waiting until 05 Sep 10:00 to SEND_PAYMENT_LINK (expected ₹428)
05 Sep 10:07  action       sent whatsapp in en, tone helpful
05 Sep 10:13  outcome      payment already received; stopping before acting  [R01]
```

The second line is the one to read twice. The agent knows the payment is worth ₹428 and refuses to
send anything, because it is 23:25 and TRAI permits customer messaging between 10:00 and 21:00. It
waits, acts the moment the window opens, and when the money arrives it refetches before acting
again and stops. Eighty paise, one message, ₹1,299 recovered.

---

## How it works

```
   Razorpay                                                    the money
   ─────────                                                   ─────────
  payment.failed  ┐                                                 ▲
  order created   │                                                 │
  subscription    ├──► DETECT ──► DIAGNOSE ──► DECIDE ──► ACT ──► VERIFY
  invoice overdue ┘   webhook     error_reason  policy    payment   refetch
                      or poll     × source      engine    link,     status
                                  × step        ▼         notice,   │
                                     │       ┌──────────┐ escalate  │
                                     │       │  R01-R41 │           │
                                     ▼       │  allow / │           ▼
                              12 root causes │  deny +  │      STOP · ESCALATE
                              ─────────────  │  when    │      · LOOP
                              transient tech └──────────┘
                              insufficient funds  ▲
                              limit exceeded      │ can veto, never widen
                              customer abandon    │
                              customer input   ┌──┴───────────────┐
                              instrument blocked│  language model │
                              mandate issue     │  advises, writes│
                              risk decline      │  reads replies  │
                              merchant config   └─────────────────┘
                              ...
```

Every arrow writes to an append-only audit log with the rule ids that produced it. The whole
system is reconstructible from that log alone — which is the point of `wapsi case <id>`.

Full detail in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### 1. Diagnose — from Razorpay's own vocabulary

Every failed Razorpay payment carries `error_reason`, `error_source` and `error_step`. `source`
says who must act, `step` says where in the flow it died. Roughly 150 documented reasons map onto
12 root causes in [`wapsi/core/taxonomy.py`](wapsi/core/taxonomy.py). It is a lookup table, not a
model: the vocabulary is published, so guessing would only add error. Anything unmapped falls back
on `source`, and failing that becomes `UNKNOWN` and is handled conservatively.

### 2. Decide — bounded, with a rule id for every answer

[`policy.yaml`](policy.yaml) holds every bound in one readable file. Nothing is duplicated in
code; change a number there and the whole system changes with it.

| | rule | source |
|---|---|---|
| Messages only 10:00–21:00 IST | R10 | TRAI TCCCPR |
| Receivables chased only 10:00–19:00 | R11 | RBI Fair Practices Code ∩ TRAI |
| Auto-debits only in NPCI non-peak windows | R12 | NPCI 2026 execution windows |
| No mandate debit without 24h notice | R13 | RBI e-mandate framework |
| Never auto-retry a recurring charge above ₹15,000 | R14 | RBI additional-factor authentication |
| Never retry a blocked instrument | R15 | — it cannot work |
| Retries spaced ≥30 min, ≤3 per day, and never into a live outage | R16 | NPCI frequency limits |
| Hard stops: paid · opted out · disputed · refunded · risk-declined · merchant fault | R01–R07 | — |
| Caps per scenario, per case and per customer per week | R20–R23 | — |
| Act only when expected value exceeds cost | R24 | — |
| Escalate: high value, broken promises, complaints, unknown causes, repeated model vetoes | R30–R34 | — |
| No threats, no legal or credit-bureau claims, no internal jargon | R40 | RBI Fair Practices Code |
| A promise to pay buys silence until it lapses | R41 | — |

`wapsi rules` prints them all.

### 3. Decide *when*, not just what

The planner scores **(action, time) pairs** across a three-day horizon, snaps each to the earliest
moment the policy permits it, and takes the best. Waiting is a real move:

- a balance failure retried immediately is worth little; the same retry on payday is worth several
  times more, so it waits
- a merchant misconfiguration cannot be fixed by anyone but the merchant, so it alerts them and
  retries quietly a day later — recovering 100% of them without ever messaging a customer who
  could not have helped
- a transient failure waits for the outage to clear rather than burning attempts against a dead
  bank

### 4. Act — and never on stale money

Every action refetches payment status first. Nobody is chased for money they have already sent.

---

## Where the language model is used, and where it is not

The honest summary: the rules are what recover the money, and the model is a writing and reading
layer whose contribution to recovery is not measurable on this batch. The table below is what it
is allowed to touch, and the answer to "where's the AI" is that it was deliberately kept out of
the decisions that move money.

| task | model? | what stops it going wrong |
|---|---|---|
| Classifying the failure | **No** | deterministic table from Razorpay's docs |
| Choosing the action | Advisory only | the policy engine re-checks every proposal and vetoes it; two vetoes escalate the case to a human |
| Writing the message | Yes | validator rejects threats, legal claims, missing opt-out lines, internal labels and over-length text — a rejected message falls back to a template |
| Reading replies | Yes | opt-out and dispute are **also** matched by pattern, and the pattern wins, so no model error can keep someone in a sequence they asked to leave |
| Briefing a human | Yes | it is prose for a person, not an action |

**The whole system runs with no API key.** Set none and every message comes from a template and
every reply is read by regular expression. The batch numbers, the tests and the live demo all work
that way, so nothing here has to be taken on trust to reproduce.

### What the model was actually worth

Measured, not asserted: **₹39,518 — and all of it is one case.**

On case_0134 a customer replied *"paisa Friday ko bhej dunga"*. Both planners read it as a promise
to pay; only the model resolved the date correctly. The pattern-matching planner worked out a date
that had already passed, nudged again three days later, received *"bahut messages aa rahe hain,
band karo"*, and lost ₹39,522 to an opt-out. The model-advised planner stayed quiet and the
customer paid.

Both audit trails are committed side by side in [`results/case_0134.md`](results/case_0134.md),
so the claim can be checked without running anything.

One case in five hundred, entirely explained by reading a Hinglish date. The report prints the
comparison in both directions; the deterministic planner beat the model on zero cases. On reading
replies overall the model scores 92.3% against the regular expressions' 91.6% — a much smaller gap
than expected, because these replies are generated from templates and patterns have an unfair
advantage over them.

---

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -e .     # .venv\Scripts\pip on Windows
wapsi simulate --n 500
```

No keys needed. That prints the comparison table and writes `results/report.md`,
`results/summary.json` and a full audit log per policy.

```bash
wapsi rules                     # every bound, with its rule id
wapsi case case_0134            # one case's complete audit timeline
wapsi sensitivity               # re-run with priors scaled ±30%
wapsi serve                     # dashboard on :8000, plus the webhook endpoint
```

### Live mode, against a real Razorpay test account

```bash
cp .env.example .env            # add test-mode keys; a model key is optional
wapsi live doctor               # checks keys, models, and reads your webhook config back
wapsi live seed                 # creates customers, payment links, an invoice, a subscription
wapsi live watch                # polls, diagnoses, and runs the recovery loop
```

Polling is the default because it needs no public URL. Webhooks are supported and cut the latency
— point one at `/webhooks/razorpay` with the secret from your `.env` — but nothing depends on them.

**To produce a failure you have to click.** Test mode has no API to fail a payment. Open a seeded
link, choose netbanking, and press **Failure** on the mock bank page. (Every guide tells you to use
UPI with `failure@razorpay`; that only works if UPI is enabled on your account, and on ours it was
not.) `wapsi live watch` then picks up the real decline, diagnoses it from the real error code, and
creates a real recovery link you can see in your dashboard.

---

## Honest limits

- **The measured results come from a simulation.** Test mode cannot produce 500 failures, so the
  batch is synthetic. The behaviour priors are published dunning and decline figures, cited in
  [`docs/RESEARCH.md`](docs/RESEARCH.md), and the sensitivity run shows the ranking survives a 30%
  error in either direction. They are not measurements of any particular merchant.
- **The agent does not hold the answer key.** Its beliefs live in `core/taxonomy.py`; the
  simulation's truth lives in `sim/config.yaml`; the two are deliberately different shapes. A test
  asserts no hidden field is reachable from the object the planner plans against.
- **Recovery is reported twice.** ₹17.6L is the gross figure. Excluding every case the simulation
  says would have resolved itself anyway, the strict figure is **₹14.3L** — against ₹83k for doing
  nothing. Both are in `results/summary.json`.
- **We count our own false nudges.** 51 customers were contacted who would have paid unprompted.
  That is a cost, and it is in the report.
- **The model is rate-limited and budgeted.** In the final batch 292 of 600 calls failed — a free
  tier's daily quota, after a day of runs — and the budget ran out partway through, so 850 of the
  897 messages came from templates and 47 from the model. The run completed anyway, every
  model-written message passed the guardrails but one, and all 36 asked for in Hinglish were
  genuinely Hinglish. That is what the budget and the fallback are for, and the report prints these
  numbers for every run rather than leaving them to be discovered.
- **Retries cannot be demonstrated live.** Test mode has no server-side charge endpoint, so
  `retry_charge` reports that plainly instead of faking it, and live mode shows recovery links
  where the batch shows silent retries.
- **Abandoned checkouts are our weakest class** (13% against a 9% baseline). The TRAI window costs
  us the golden first hour after abandonment, and we accept that rather than message at 03:00.
- **Overdue receivables are a tie with the naive policy** (65% vs 66%) — which reaches that number
  by breaking rules 2,256 times.

---

## Why it is built this way

The design questions a reader will have — why diagnosis is a lookup table and not a model, why the
planner scores *times* rather than actions, why the policy engine holds a veto over the model, what
the agent refuses to do and why — are answered in [`docs/RATIONALE.md`](docs/RATIONALE.md).

## What broke, and how we got out

Kept in real time in [`docs/BUILD_LOG.md`](docs/BUILD_LOG.md) — the failures behind the design,
including the weekly cap that silently abandoned recoverable cases, the simulated outages that
made retrying look smart, the missing pre-debit notification that made every subscription
unchargeable, and the moment two policies returned identical results because the model had leaked
into the baseline.

## What this becomes inside Razorpay

- Consume `payment.downtime.*` directly for retry timing, instead of inferring outages
- Hand abandoned checkouts back to Magic Checkout rather than re-creating a link
- Send through Razorpay's own WhatsApp channel, so delivery and cost are real numbers rather than
  a priced stub
- Learn the priors per merchant from their own recovery history, replacing the published averages
  with measurements — the architecture already isolates them in one file for exactly this

## Layout

```
wapsi/core/       models · taxonomy · policy · planner · executor · audit · metrics · validator
wapsi/adapters/   razorpay_live · razorpay_fake · llm · composer · templates · messaging
wapsi/sim/        world · customer · generator · runner · baselines · config.yaml
wapsi/live/       seed · poller · webhook · state
wapsi/api/        FastAPI app + dashboard
policy.yaml       every bound, in one file
tests/            194 tests
```
