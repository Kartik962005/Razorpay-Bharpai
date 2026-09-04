# Defending the build — the panel

If it has signal, they call you in. The panel will not ask you to explain the code; they will ask
you to defend decisions. Every answer below is one you should be able to give without notes,
because each one is a thing you actually chose. Where an answer has a number in it, the number is
in `results/report.md` and you should know it.

---

### "Why a lookup table for diagnosis and not a model?"

Because the vocabulary is published. Razorpay documents about 150 `error_reason` values, and each
one already says who must act and where the flow died. A model would be guessing at something the
API states outright, and it would be wrong some fraction of the time on a decision that is upstream
of everything else. The table is deterministic, testable, and when Razorpay adds a reason next
year it falls back on `error_source` and, failing that, becomes `UNKNOWN` and is handled
conservatively. *Where* the model belongs is downstream: writing, reading, briefing.

### "You built the simulation and the agent. Of course the agent wins."

Four defences, and they are in the repo, not in this answer.

1. The agent's beliefs (`core/taxonomy.py`) and the simulation's truth (`sim/config.yaml`) are
   different shapes, from different reasoning. A test asserts the planner cannot reach any hidden
   field.
2. Every policy is judged by the same engine, including the baselines that never consult it.
3. Sensitivity in two directions: scale every prior ±30%, and separately turn down every penalty
   that flatters the compliant policy — night contact barely annoys, opt-outs take twice as many
   messages, chargebacks are free. The ranking holds under both. That is `results/sensitivity.md`.
4. The fair baseline is not the naive one — it is Razorpay's own defaults, the `platform` row.
   Wapsi has to beat that, and does.

If they push further: "Then the simulation is where I would spend the next month — replacing
published priors with a merchant's own recovery history. The architecture isolates them in one file
for exactly that reason."

### "Where's the AI? This looks like a rules engine."

Say this without flinching: the rules *are* the intelligence, and the model is deliberately kept
where it earns its place — writing Hinglish that sounds like a person, reading replies that
patterns mishandle, and briefing a human. It advises on actions but cannot widen the legal set,
and the policy engine vetoes it.

Then give the measured answer, because it is more persuasive than an inflated one: the model's
contribution was ₹39,518, all of it one case, where it read *"paisa Friday ko bhej dunga"* with
the right date and the regex did not. On this batch the model reads replies 92.3% correctly against
91.6% for patterns — a small gap, because the replies are template-generated and patterns have an
unfair advantage. On real Hinglish that gap would be wider, and that is where the model's value is.

A committee hiring AI builders wants to see someone who knows *where not to use the model*. That is
the whole argument.

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
below the cost. Each is a numbered rule, each denial is logged with its rule, and `wapsi rules`
prints them.

### "Show me the audit trail for one case."

`wapsi case case_0134`. Walk them through it: detection, diagnosis, the planner's verdict with an
expected value, the policy engine's denial with a rule id and the time it lifts, the action, the
customer's reply as read, the outcome. Nothing in it was written afterwards.

### "What broke?"

Lead with R23. The agent abandoned recoverable invoices seconds after the first reminder because
the weekly message cap — which is temporary, it lifts as old messages age out — was reported with
no expiry, so the planner could not tell "blocked until Tuesday" from "blocked forever". The fix
was that every temporary denial must say *when*. Then say what it taught you: a policy engine that
only says "no" is not enough for anything that plans; it must say "not until". That distinction is
now what the planner is built around.

Have two more ready: transient failures that were not tied to outages (retrying looked smart until
the outage was real, then it went from 16% to 86%), and the model leaking the internal label
`INSTRUMENT_BLOCKED` into a customer SMS past every guardrail (fixed with a rule against internal
vocabulary in customer text).

### "What are the weaknesses?"

Volunteer them before they are asked. Abandoned checkouts are the weakest class — 13% against 9% —
because the TRAI window costs the golden first hour, and you chose compliance over the 03:00
message. Overdue receivables are a tie with the naive policy, which gets there by breaking rules
2,256 times. The live demo cannot show a silent retry because test mode has no server-side charge.
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
The model was asked for Hinglish 36 times in the final batch and produced genuine Hinglish 36
times — after a prompt fix, because the first version produced English with a Hindi sign-off,
which the report now measures so it cannot regress silently.

### "Half your model calls failed."

Yes — 292 of 600 in the final batch, a free tier's daily quota after a day of runs. Say it before
they do, then say why it did not matter: every failed call fell back to a template, the run
completed, the numbers are reported, and the batch result does not depend on the model at all — it
runs identically with no key. A system whose correctness survives its model being unavailable half
the time is the design, not an accident.

---

### Numbers to know cold

- 500 cases, seed 42, identical batch for every policy
- do nothing 22.2% · **Razorpay defaults 28.2%** · naive 29.6% · Wapsi 46.8%
- ₹17.6L net · defaults ₹14.5L · naive ₹15.8L · nothing ₹4.5L
- **₹2.8L above the platform's own defaults**, with 816 messages to their 1,196 and 62 opt-outs
  to their 140 — the number to lead with, because it is the fair one
- strict figure ₹14.3L excluding every case that would have resolved itself
- 0 violations against naive's 2,256, of which 112 were messages to people who said stop
- 1 dispute against naive's 126 and the defaults' 13
- hostile assumptions (penalties stripped, chargebacks free): Wapsi ₹20.1L, naive ₹18.0L,
  defaults ₹15.4L — ranking unchanged
- 51 false nudges, counted against ourselves
- 192 tests, no network; CI on Linux and Windows, no keys

### "What's the worst bug you shipped and caught?"

The idempotency guard — the check that stops the agent chasing someone who has already paid — did
not work on the live path. A case created from a failed payment carries an order id and a payment
id; `refresh()` looked only at payment links, invoices and subscriptions. So if a customer went
back and paid the original link, the order settled and Wapsi carried on messaging them.

Two things about it are worth saying. First, it is the failure the whole project claims to
prevent, so finding it in my own code is the most useful thing that happened during the build.
Second, the batch could never have caught it: the simulated gateway answers from the case object,
so it always agreed with whatever the agent already believed. A simulation only tests the
questions you thought to ask it. I found this by reading the real Razorpay ids in live state at
half past one in the morning, not by running anything.

### "Half your live cases are waiting. Is it stuck?"

No — it is 01:36 and the messaging window is shut. The agent has diagnosed all of them and
scheduled the first contact for 10:00, and the audit log shows the rule that stopped it and the
time it defers to. That is the system working. If you want to see it act, the recording is made
inside the window.
