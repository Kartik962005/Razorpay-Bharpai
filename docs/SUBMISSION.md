# Application form

Four fields. Paste-ready below; edit into your own voice before submitting.

---

## 1. GitHub Repository URL

```
https://github.com/Kartik962005/Razorpay-Bharpai
```

## 2. 5-min Pitch Video Link

*(unlisted link, once recorded — see `docs/VIDEO.md`)*

---

## 3. Project Objectives — what does it solve?

Razorpay merchants lose recoverable money to recovery that ignores *why* a payment failed. The
default everywhere is a fixed retry ladder and a fixed reminder cadence, which treats a
twenty-minute bank outage, a blocked card and a mistyped CVV as the same event. Retrying into an
outage is measurably worse than doing nothing.

Bharpai reads Razorpay's own failure signals — `error_reason`, `error_source`, `error_step` —
mapping roughly 150 documented reasons onto 12 root causes. It picks the intervention that fits the
cause, and the moment to take it, inside hard bounds: TRAI messaging hours, RBI's fair practices
window, NPCI execution windows, the ₹15,000 authentication threshold, per-customer frequency caps,
and an expected-value floor. Every allow and deny cites a numbered rule in an append-only audit
log, so any decision can be reconstructed from the log alone.

On 500 cases it recovers ₹17.2 lakh net against ₹14.5 lakh for Razorpay's own defaults — with a
third fewer messages, less than half the opt-outs, and zero rule violations. It also closes the
loop on a real Razorpay test account: a failed ₹1,299 payment detected at 23:24, refused at 23:25
because TRAI forbids messaging at that hour, acted on at 10:07 when the window opened, and stopped
at 10:13 when the money arrived.

---

## 4. Build Challenges & Technical Obstacles

Three worth naming, because they were different kinds of problem.

**The bug that mattered.** Late on the last night I was reading the agent's live state and noticed
a case id that only exists in a test fixture — a test had been writing into real state. Fixing that
made me read the other live cases properly, and I found something far worse. The guard that stops
the agent chasing someone who has already paid did not work on the live path: a case created from a
failed payment carries an *order* id, and my check looked only at payment links, invoices and
subscriptions. If a customer went back and paid the original link, the order would settle and
Bharpai would keep messaging them — precisely the failure this project exists to prevent, in my own
code, on the only path that touches real money. What unsettled me is that my test suite could never
have caught it: in the simulation the fake gateway answers from the case object, so it always
agreed with whatever the agent already believed. I found it by reading real Razorpay ids, not by
running anything. Fixed by checking the order too, by status and by `amount_paid`, since partial
settlement is reported as an amount rather than a status. Three tests pin it, and the last line of
the live recovery trail is that guard firing on a real payment.

**A design bug the audit log exposed.** The agent kept abandoning invoices seconds after the first
reminder. The rule doing it was the weekly per-customer message cap — which is real and I want it —
but the policy engine reported it as a bare refusal with no expiry, so the planner could not tell
"blocked until Tuesday" from "blocked forever" and treated the case as a dead end. Every temporary
denial now carries the moment it lifts. A policy engine that only answers "no" is not enough for
anything that plans; it has to answer "not until", and that distinction is what the planner is now
built around.

**A measurement bug that cost me my headline.** Two policies returned byte-identical results, which
turned out to be the language model leaking into the deterministic baseline through shared
reply-reading. Scoping it properly dropped the rules policy from ₹18.3L to ₹17.2L — the honest
price of imperfect comprehension. Then running the model-advised policy at five times the coverage
made its apparent ₹39,518 advantage disappear: 232 cases recovered against 233. One case in five
hundred had been noise. The README now says the language model contributes nothing measurable to
recovery, which is a smaller claim and a truer one.

---

*Fuller account of every failure and fix, kept in real time, is in [`BUILD_LOG.md`](BUILD_LOG.md).*
