# Application form — drafts

Twelve fields. The last one is the one they read first, so it gets the most care. Edit these into
your own voice before pasting; they are drafts, not a script.

---

## About you

| field | answer |
|---|---|
| Full name | Kartik Gupta |
| College | *(yours)* |
| Graduation year | *(yours)* |
| In-person from September | *(yes / no)* |
| 6 or 12 months | *(your pick)* |
| Resume | *(file)* |

## About the build

**Track** — 03, AI Revenue Recovery

**Project name** — Bharpai

**GitHub repo** — https://github.com/Kartik962005/Razorpay-Bharpai

**Pitch video** — *(unlisted link)*

---

### What it solves

*(~150 words)*

Razorpay merchants lose recoverable money to recovery that ignores why a payment failed. The
default everywhere is a fixed retry ladder and a fixed reminder cadence, which treats a
twenty-minute bank outage, a blocked card and a mistyped CVV as the same event. Retrying into an
outage is measurably worse than doing nothing.

Bharpai reads Razorpay's own failure signals — `error_reason`, `error_source`, `error_step` — and
maps roughly 150 documented reasons onto 12 root causes. It then picks the intervention that fits
that cause and the moment to take it, inside hard bounds: TRAI messaging hours, RBI's fair
practices window, NPCI execution windows, the ₹15,000 authentication threshold, per-customer
frequency caps, and an expected-value floor. Every allow and deny cites a numbered rule in an
append-only audit log.

On 500 cases it recovers ₹17.2 lakh net against ₹14.5 lakh for Razorpay's own defaults and ₹4.5
lakh for doing nothing — with a third fewer messages than the defaults, less than half the
opt-outs, zero rule violations, and one dispute. The ranking survives scaling every behaviour
assumption ±30% and, separately, stripping every penalty for careless recovery.

---

### What broke at 2 a.m., and how you got out

*(~330 words. This one is literal — it happened at 01:36 on the last night. The full log is
`docs/BUILD_LOG.md`.)*

At about half past one in the morning I was reading the agent's live state, waiting for the
messaging window to open so I could film the loop closing, and I noticed a case id that had no
business being there: `live_pay_TESTFAILED001`. That id only exists in a test fixture. A test had
written into the real state directory — when the webhook endpoint gained the ability to create
cases, an older test of that endpoint silently gained the side effect, and it had no isolation
because when it was written the endpoint only logged.

Annoying, easy to fix. But it made me read the other three cases properly instead of glancing at
them, and there I found something much worse.

The guard that stops the agent chasing someone who has already paid did not work on the live path.
A case created from a failed payment carries an *order* id; the check looked only at payment links,
invoices and subscriptions. So if a customer went back and paid the original link, the order would
settle and Bharpai would keep messaging them. That is precisely the failure this project exists to
prevent, sitting in my own code, on the only path that touches real money.

What actually unsettled me is that my test suite could never have caught it. In the simulation the
gateway answers from the case object itself, so it always agreed with whatever the agent already
believed. Five hundred cases and a hundred and ninety tests, and the thing was invisible to all of
them. I only found it by reading real Razorpay ids at half past one. A simulation tests the
questions you thought to ask it, and nothing else.

The fix checks the order too — by status and by `amount_paid`, because Razorpay reports partial
settlement as an amount rather than a status. Three tests pin it. Then I went looking for the rest
of that family and found three more: a weekly messaging cap that reset on restart, a payment
listing that read one page and advanced the cursor past everything after it, and the isolation
fixture recurring inside its own fix because I had added a new state file to it.

Eight hours later the agent recovered a real ₹1,299 payment on the test account, and the last line
of that trail is `[R01] payment already received; stopping before acting` — the fixed guard,
working, on a real payment. That is in `results/live_recovery.md`.

---

### Alternative answer, if you want the design story instead

**Option A — the bug that shaped the design.**

The agent kept abandoning invoices it should have recovered. It would send one reminder, then a
second later close the case with "no permitted action is worth more than it costs" — on a ₹3,130
invoice that had barely been tried.

I read the audit log rather than the code, which is how I found it. The rule doing the closing was
R23, the cap on how many messages one customer can receive in a week. That cap is real and I want
it. But it is a *rolling* cap: it lifts as older messages age out of the window. The policy engine
was reporting it to the planner as a bare refusal with no expiry, so the planner could not tell
"blocked until Tuesday" from "blocked permanently" — and its rule was that an action with no
future is a dead end, so it gave up on the case entirely.

The fix was to make every temporary denial carry the moment it lifts. Once it did, the planner
waited instead of quitting, and the same bug class turned up immediately in a second place: the
planner snapped a blocked action forward only once, so waiting out a nudge gap could land it
outside the messaging window and be silently discarded. It now snaps repeatedly until the time is
clean.

What it cost me was about two hours, and what it taught me was worth more. A policy engine that
only answers "no" is not enough for anything that plans; it has to answer "not until". I went back
through all 26 rules and separated the genuinely permanent bars — a blocked instrument, a spent
budget, an opt-out — from the temporary ones, and made the temporary ones say when. That
distinction is now what the planner is built around, and it came out of a bug rather than a design
session.

**Option B — the one I would want to be told about.** The system's loudest promise is *never chase
someone who has already paid*. On the live path it could not keep that promise: a case born from a
failed payment carries an order id, and the idempotency check looked only at payment links,
invoices and subscriptions. If the customer went back and paid the original link, the order settled
and the agent kept chasing. The batch would never have caught it — the simulated gateway answers
from the case object itself, so it agreed with whatever the agent believed. Only reading the real
Razorpay ids in live state did. Fixed by checking the order, by status and by `amount_paid`, since
partial settlement is reported as an amount rather than a status. The lesson is that a simulation
can only test the questions you thought to ask it.
