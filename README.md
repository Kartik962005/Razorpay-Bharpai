# Bharpai

[![tests](https://github.com/Kartik962005/Razorpay-Bharpai/actions/workflows/tests.yml/badge.svg)](https://github.com/Kartik962005/Razorpay-Bharpai/actions/workflows/tests.yml)

**Cause-aware revenue recovery for Razorpay merchants.** Razorpay AI Buildathon — Track 3.

*bharpai (भरपाई): making good a loss.*

A payment fails. Most recovery systems then do the same thing regardless of *why* it failed:
retry on a fixed ladder, send a reminder on a fixed cadence. That treats "the bank was down for
twenty minutes" identically to "this card is blocked" and to "the customer typed the wrong CVV" —
three failures with completely different answers. The retries that cannot work burn NPCI attempt
limits, the reminders that arrive at the wrong hour cost goodwill, and the money that was
genuinely recoverable is left on the table.

Bharpai reads Razorpay's own failure signals, works out *why* each payment failed, picks the one
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
| **Bharpai** | **233** | **46.6%** | ₹17,24,498 | ₹659 | **₹17,23,840** | 816 | 62 | 1 | **0** |
| Bharpai, model-advised | 232 | 46.4% | ₹17,24,199 | ₹676 | ₹17,23,524 | 818 | 72 | 1 | **0** |

**₹2.8 lakh more than Razorpay's own defaults recover, with a third fewer messages and less than
half the opt-outs. ₹12.7 lakh more than doing nothing. Zero rule violations.**

The fair comparison is the second row — what a merchant gets from the platform unprompted — and
that is the claim: Bharpai recovers 65% more cases than the defaults while sending 1,196 → 816
messages, because it sends the *right* message rather than three generic ones. The defaults lose
140 customers to opt-outs, 8.5 messages per recovery; Bharpai loses 62.

The naive row is what untuned merchant automation actually does, and it is worth keeping because it
shows what the rules are for. It spends 95× more than Bharpai and 126 of its recoveries turn into
chargebacks. Judged by the same policy engine Bharpai obeys, it breaks rules 2,256 times — including
**112 messages sent to people who had already said stop** and 16 retries of payments that risk had
declined. (The defaults row is not scored for violations: those are the platform's actions, not a
merchant's policy.)

Recovery rate by root cause — this is where cause-awareness shows up:

| root cause | cases | do nothing | naive | **Bharpai** |
|---|---|---|---|---|
| merchant misconfiguration | 10 | 10% | 10% | **100%** |
| bank/PSP outage | 50 | 42% | 16% | **86%** |
| daily limit hit | 28 | 29% | 18% | **82%** |
| overdue receivable | 79 | 14% | **66%** | 58% |
| insufficient funds | 98 | 41% | 47% | **57%** |
| blocked instrument | 22 | 27% | 27% | **36%** |
| customer input error | 29 | 17% | 17% | **31%** |
| abandoned checkout | 91 | 9% | 9% | 12% |

Retrying into an outage is worse than doing nothing — the naive policy proves it, scoring 16%
against a 42% baseline on exactly the class of failure that retrying is supposed to fix.

Two rows go the other way and are worth naming. **Overdue receivables** are the one class where
the naive policy beats Bharpai, 66% against 58% — it gets there by emailing every three days
forever, which is most of where its 2,256 rule violations come from. And **abandoned checkouts**
are the weakest class outright, 12% against a 9% baseline: the TRAI window costs the golden first
hour after someone leaves a cart, and the alternative is messaging them at 3 a.m.

**Sensitivity, two ways.** First, every behaviour prior scaled by 0.7 and 1.3: the ranking is
unchanged. Second — the test a sceptical reviewer should demand — every assumption that *flatters*
the compliant policy turned down hard: night-time messages barely annoy anyone and never trigger a
dispute, customers tolerate twice as many messages before opting out, retrying a risk-declined
payment never causes a chargeback, and the chargeback fee is zero. **Bharpai still wins: ₹20.1L
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
system is reconstructible from that log alone — which is the point of `bharpai case <id>`.

Full detail in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### 1. Diagnose — from Razorpay's own vocabulary

Every failed Razorpay payment carries `error_reason`, `error_source` and `error_step`. `source`
says who must act, `step` says where in the flow it died. Roughly 150 documented reasons map onto
12 root causes in [`bharpai/core/taxonomy.py`](bharpai/core/taxonomy.py). It is a lookup table, not a
model: the vocabulary is published, so guessing would only add error. Anything unmapped falls back
on `source`, and failing that becomes `UNKNOWN` and is handled conservatively.

### 2. Decide — bounded, with a rule id for every answer

[`policy.yaml`](policy.yaml) holds every bound in one readable file — 26 rules, with ids
running R01 to R41 so that related rules sit in the same decade and new ones can be added without
renumbering. Nothing is duplicated in code; change a number there and the whole system changes
with it. `bharpai rules` prints them all.

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

### What the model was actually worth: nothing measurable

This is the result I did not expect, and it is the one worth reading.

The model writes the messages and reads the replies. On **recovery outcome it makes no
difference** — 232 cases against the deterministic planner's 233, ₹17,23,524 against ₹17,23,840.
Within noise, and if anything fractionally behind.

That conclusion took two attempts to earn. On a first run, where a rate limit meant the model
wrote only 106 of 898 messages, the model-advised policy came out **₹39,518 ahead** — and the
entire difference was **one case**, where a customer wrote *"paisa Friday ko bhej dunga"* and only
the model resolved the date correctly. Both trails are committed in
[`results/case_0134.md`](results/case_0134.md). It would have been easy to report that as the
model's contribution and stop.

Running it again on a different provider with **539 of 898 messages model-written** — five times
the coverage — the advantage disappeared and reversed. One case in five hundred was noise.

What the model does contribute is quality the recovery number cannot see: asked for Hinglish 406
times, it produced genuine Hinglish 406 times, against templates that are correct but identical
every time. Whether that matters is not something this batch can measure, and putting a number on
it would be inventing one.

**On reading replies it is slightly worse than the regular expressions** — 88.3% against 91.6% on
exact match. Almost every miss on both sides is the same one: a customer writing *"bahut messages
aa rahe hain, band karo"* — literally *too many messages, stop it* — which the simulation labels a
complaint and both readers call an opt-out. Stopping there is the right call and the label is the
debatable half, which is why the report also counts misreadings by direction: **34 erred toward
contacting less, exactly 1 erred toward contacting more, 99.7% were safe in that sense.**

The honest summary: the rules recover the money. The model is a writing and reading layer whose
contribution to recovery is not measurable on this batch, and the answer to "where is the AI" is
that it was deliberately kept out of the decisions that move money.

---

## Run it

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # .venv\Scripts\pip on Windows
bharpai simulate --n 500
```

No keys needed. That prints the comparison table and writes `results/report.md`,
`results/summary.json` and a full audit log per policy. Note that it *overwrites* the committed
results with whatever policies you ran — `git checkout -- results/` puts them back.

```bash
bharpai rules                     # every bound, with its rule id
bharpai case case_0134            # one case's complete audit timeline
bharpai serve                     # dashboard on :8000, plus the webhook endpoint

# The exact commands behind the two committed result files:
bharpai simulate --n 500 --policies do_nothing,platform,naive,rules
bharpai sensitivity --policies do_nothing,platform,naive,rules

# And the model-advised row, the only one that needs a key. Any OpenAI-compatible
# endpoint; `advisor_sample` is recorded in summary.json so a rerun matches.
bharpai simulate --n 500 --policies do_nothing,platform,naive,rules,agent --advisor-sample 0.05
```

### Live mode, against a real Razorpay test account

```bash
cp .env.example .env            # add test-mode keys; a model key is optional
bharpai live doctor               # keys, model, and whether the webhook endpoint answers
bharpai live seed                 # creates customers, payment links, an invoice, a subscription
bharpai live watch                # polls, diagnoses, and runs the recovery loop
```

Polling is the default because it needs no public URL. Webhooks are supported and cut the latency
— point one at `/webhooks/razorpay` with the secret from your `.env` — but nothing depends on them.

**To produce a failure you have to click.** Test mode has no API to fail a payment. Open a seeded
link, choose netbanking, and press **Failure** on the mock bank page. (Every guide tells you to use
UPI with `failure@razorpay`; that only works if UPI is enabled on your account, and on ours it was
not.) `bharpai live watch` then picks up the real decline, diagnoses it from the real error code, and
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
- **Recovery is reported twice.** ₹17.2L is the gross figure. Excluding every case the simulation
  says would have resolved itself anyway, the strict figure is **₹13.9L** — against ₹83k for doing
  nothing. Both are in `results/summary.json`.
- **We count our own false nudges.** 51 customers were contacted who would have paid unprompted.
  That is a cost, and it is in the report.
- **The model is rate-limited and budgeted.** In the final batch 183 of 1,401 calls were refused by
  a free tier that meters 1,000 requests per three hours — and 131 more were served from a cache
  rather than spent. 539 of the 898 messages were model-written and 359 fell back to templates. The
  run completed either way, three model-written messages were rejected by the guardrails and
  replaced, and all 406 asked for in Hinglish were genuinely Hinglish. That is what the budget and
  the fallback are for, and the report prints these numbers for every run rather than leaving them
  to be discovered.
- **Retries cannot be demonstrated live.** Test mode has no server-side charge endpoint, so
  `retry_charge` reports that plainly instead of faking it, the planner stops proposing retries
  once the gateway says it cannot make them, and live mode shows recovery links where the batch
  shows silent retries.
- **Live messages are composed, priced and logged — not delivered.** There is no SMS or WhatsApp
  provider wired in. The recovery link is real and appears in the Razorpay dashboard; the message
  that would carry it is written, guardrail-checked, costed and put in the audit log. Sending it
  is one adapter, and it is the one adapter this repo does not have.
- **Live mode has no inbound channel.** Replies are read in the batch, where the simulated
  customer writes back. Nothing live can receive an opt-out, a promise to pay or a complaint, so
  the rules that depend on reading a reply (R02, R32, R41) are exercised by the batch and the
  tests, never by the live loop.
- **The seeded invoice is overdue by construction.** Test mode will not create an invoice with a
  due date in the past, so the poller marks the seeded one overdue when it ingests it. The
  receivables *rules* are then real — the 19:00 window, the 72-hour spacing — but the overdue
  condition that triggers them is arranged rather than observed.
- **Two classes go the other way, and they are named in the results above** rather than buried
  here: abandoned checkouts are the weakest outright at 12%, and overdue receivables are the one
  class the naive policy actually wins, 66% to 58%.

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
bharpai/core/       models · taxonomy · policy · planner · executor · audit · metrics · validator
bharpai/adapters/   razorpay_live · razorpay_fake · llm · composer · templates · messaging
bharpai/sim/        world · customer · generator · runner · baselines · config.yaml
bharpai/live/       seed · poller · webhook · state
bharpai/api/        FastAPI app + dashboard
policy.yaml       every bound, in one file
tests/            216 tests
```
