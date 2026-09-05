# Rationale — the questions a reviewer will have

The code shows *what* this does. This is the *why*, in the order the questions usually come. Every
answer is a decision that was actually made and can be checked against the repository; where an
answer contains a number, that number is in `results/report.md`.

---

### "Why a lookup table for diagnosis and not a model?"

Because the vocabulary is published. Razorpay documents about 150 `error_reason` values, and each
one already says who must act and where the flow died. A model would be guessing at something the
API states outright, and it would be wrong some fraction of the time on a decision that is upstream
of everything else. The table is deterministic, testable, and when Razorpay adds a reason next
year it falls back on `error_source` and, failing that, becomes `UNKNOWN` and is handled
conservatively. *Where* the model belongs is downstream: writing, reading, briefing.

### "You built the simulation and the agent. Of course the agent wins."

Four answers, all checkable in the repository.

1. The agent's beliefs (`core/taxonomy.py`) and the simulation's truth (`sim/config.yaml`) are
   different shapes, from different reasoning. A test asserts the planner cannot reach any hidden
   field.
2. Every policy is judged by the same engine, including the baselines that never consult it.
3. Sensitivity in two directions: scale every prior ±30%, and separately turn down every penalty
   that flatters the compliant policy — night contact barely annoys, opt-outs take twice as many
   messages, chargebacks are free. The ranking holds under both. That is `results/sensitivity.md`.
4. The fair baseline is not the naive one — it is Razorpay's own defaults, the `platform` row.
   Bharpai has to beat that, and does.

The simulation is still the weakest part, and the next month of work would go into replacing
published priors with a merchant's own recovery history. The architecture isolates them in one
file for exactly that reason.

### "Where's the AI? This looks like a rules engine."

The rules *are* the intelligence, and the model is deliberately kept
where it earns its place — writing Hinglish that sounds like a person, reading replies that
patterns mishandle, and briefing a human. It advises on actions but cannot widen the legal set,
and the policy engine vetoes it.

The measured answer, which is more useful than an inflated one: **on recovery outcome the model
contributes nothing measurable.** 232 cases against the deterministic planner's 233, with 539 of
898 messages model-written. An earlier, throttled run showed it ₹39,518 ahead on the strength of a
single case; five times the coverage made that disappear, so it was noise. What the model does
give is Hinglish that reads like a person wrote it — 406 of 406 requests genuinely Hinglish —
which the recovery number cannot see and which this batch cannot value.

Knowing where *not* to put the model is the argument.

### "Why does the planner score times, not just actions?"

Because *when* is most of the value. A balance failure retried in the first hour converts at a
tenth of the rate of the same retry on payday. So every candidate action is scored at several
future moments, snapped to the earliest the policy permits, and discounted for delay. `WAIT` is a
first-class decision with a reason and a target time. This is also how a pre-debit notification —
which recovers nothing itself — gets priced at the value of the retry it makes lawful 24 hours
later, and stops every subscription in the batch from being unchargeable.

### "What does the agent refuse to do?"

Retry a blocked instrument (it cannot work). Retry a risk decline (that is how you earn a
chargeback). Auto-retry a recurring charge over ₹15,000 (RBI requires the customer to authenticate).
Debit a mandate without 24 hours' notice. Message before 10:00 or after 21:00, or after 19:00 for
an invoice. Contact anyone who said stop, disputed, or was refunded. Chase a customer for the
merchant's own misconfiguration. Message five times in a week. Act when the expected value is
below the cost. Each is a numbered rule, each denial is logged with its rule, and `bharpai rules`
prints them.

### "Show me the audit trail for one case."

`bharpai case case_0134`, or read the committed copy at `results/case_0134.md`: detection, diagnosis, the planner's verdict with an
expected value, the policy engine's denial with a rule id and the time it lifts, the action, the
customer's reply as read, the outcome. Nothing in it was written afterwards.

### "What broke?"

The most instructive one is R23. The agent abandoned recoverable invoices seconds after the first reminder because
the weekly message cap — which is temporary, it lifts as old messages age out — was reported with
no expiry, so the planner could not tell "blocked until Tuesday" from "blocked forever". Every
temporary denial now carries the moment it lifts. A policy engine that only says "no" is not
enough for anything that plans; it must say "not until", and that distinction is what the planner
is built around.

Two more: transient failures that were not tied to outages (retrying looked smart until
the outage was real, then it went from 16% to 86%), and the model leaking the internal label
`INSTRUMENT_BLOCKED` into a customer SMS past every guardrail (fixed with a rule against internal
vocabulary in customer text).

### "What are the weaknesses?"

Abandoned checkouts are the weakest class — 12% against a 9% baseline — because the TRAI window
costs the golden first hour after a cart is left, and the alternative is messaging someone at 3
a.m. Overdue receivables are the one class the naive policy actually wins, 66% against 58%; it
gets there by emailing every three days forever, which is where most of its 2,256 rule violations
come from. The live demo cannot show a silent retry because test mode has no server-side charge.
And the measured results are a simulation, for the reason that test mode cannot produce five
hundred failures.

### "What would you do first inside Razorpay?"

Consume `payment.downtime.*` directly for retry timing. Hand abandoned checkouts to Magic Checkout
rather than re-creating a link. Send through the real WhatsApp channel so cost and delivery are
measurements. And replace the published priors with per-merchant recovery history — the one change
that turns this from a defensible estimate into a measurement.

### "Why Hinglish?"

Because that is what Indian merchants actually send, and a message in the customer's register
converts better than one in the merchant's. The simulation gives a 15% lift for a language match.
The model was asked for Hinglish 406 times in the final batch and produced genuine Hinglish 406
times — after a prompt fix, because the first version produced English with a Hindi sign-off,
which the report now measures so it cannot regress silently.

### "What happens when the model is unavailable?"

It was, for most of the first attempts at this batch — a free tier metering 1,000 requests per
three hours against a run needing roughly 1,200. Every refused call fell back to a template, the
run completed, and the recovery numbers were unchanged. That is not luck: the batch result does
not depend on the model at all, and the whole system runs identically with no key.

The adapter now paces itself against whatever the provider reports — token windows are waited out,
an exhausted request allowance stops the calling entirely — and reports the two separately, since
one is a property of the account and the other a choice this system made.

---

### The numbers, in one place

- 500 cases, seed 42, identical batch for every policy
- do nothing 22.2% · **Razorpay defaults 28.2%** · naive 29.6% · Bharpai 46.6%
- ₹17.2L net · defaults ₹14.5L · naive ₹15.8L · nothing ₹4.5L
- **₹2.8L above the platform's own defaults**, with 816 messages to their 1,196 and 62 opt-outs
  to their 140 — the fair comparison, and the one the README leads with
- strict figure ₹13.9L excluding every case that would have resolved itself
- 0 violations against naive's 2,256, of which 112 were messages to people who said stop
- 1 dispute against naive's 126 and the defaults' 13
- hostile assumptions (penalties stripped, chargebacks free): Bharpai ₹20.1L, naive ₹18.0L,
  defaults ₹15.4L — ranking unchanged
- 51 false nudges, counted against ourselves
- 215 tests, no network; CI on Linux and Windows, no keys

### "What's the worst bug you shipped and caught?"

The idempotency guard — the check that stops the agent chasing someone who has already paid — did
not work on the live path. A case created from a failed payment carries an order id and a payment
id; `refresh()` looked only at payment links, invoices and subscriptions. So if a customer went
back and paid the original link, the order settled and Bharpai carried on messaging them.

Two things about it are worth saying. First, it is the failure the whole project claims to
prevent, so finding it in my own code is the most useful thing that happened during the build.
Second, the batch could never have caught it: the simulated gateway answers from the case object,
so it always agreed with whatever the agent already believed. A simulation only tests the
questions you thought to ask it. I found this by reading the real Razorpay ids in live state at
half past one in the morning, not by running anything.

### "Some live cases sit in `waiting`. Is it stuck?"

No — that is the messaging window. A case detected at 23:25 is diagnosed immediately and
scheduled for 10:00, and the audit log records the rule that stopped it and the time it defers
to. `results/live_recovery.md` shows one such case waiting overnight and then recovering.
