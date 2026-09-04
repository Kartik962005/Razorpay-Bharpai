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

**Project name** — Wapsi

**GitHub repo** — https://github.com/Kartik962005/Razorpay-Wapsi-

**Pitch video** — *(unlisted link)*

---

### What it solves

*(~150 words)*

Razorpay merchants lose recoverable money to recovery that ignores why a payment failed. The
default everywhere is a fixed retry ladder and a fixed reminder cadence, which treats a
twenty-minute bank outage, a blocked card and a mistyped CVV as the same event. Retrying into an
outage is measurably worse than doing nothing.

Wapsi reads Razorpay's own failure signals — `error_reason`, `error_source`, `error_step` — and
maps roughly 150 documented reasons onto 12 root causes. It then picks the intervention that fits
that cause and the moment to take it, inside hard bounds: TRAI messaging hours, RBI's fair
practices window, NPCI execution windows, the ₹15,000 authentication threshold, per-customer
frequency caps, and an expected-value floor. Every allow and deny cites a numbered rule in an
append-only audit log.

On 500 cases it recovers ₹17.6 lakh net against ₹14.5 lakh for Razorpay's own defaults and ₹4.5
lakh for doing nothing — with a third fewer messages than the defaults, less than half the
opt-outs, zero rule violations, and one dispute. The ranking survives scaling every behaviour
assumption ±30% and, separately, stripping every penalty for careless recovery.

---

### What broke, and how you got out

*(~300 words. The full log is `docs/BUILD_LOG.md`; this is the one worth telling.)*

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
outside the messaging window and it would be silently discarded. It now snaps repeatedly until the
time is clean.

What it cost me was about two hours, and what it taught me was worth more than the two hours. A
policy engine that only answers "no" is not enough for anything that has to plan; it has to answer
"not until". I went back through all 41 rules and separated the genuinely permanent bars — a
blocked instrument, a spent budget, an opt-out — from the temporary ones, and made the temporary
ones say when. That distinction is now the thing the planner is actually built around, and it came
out of a bug rather than a design session.
