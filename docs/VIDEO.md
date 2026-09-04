# Five-minute pitch — shot list

Record after **10:00 IST**, so the live agent is inside its messaging window and the loop closes on
camera. Before starting: `wapsi serve` running, dashboard open at `localhost:8000`, a second window
with the Razorpay test dashboard, and a terminal.

Total 5:00. The numbers below are the current ones in `results/report.md` — re-read them if the
batch is re-run.

---

## 0:00–0:35 · The leak

> "A ₹1,299 payment fails at 9:40 at night. Most recovery systems now do the same thing whatever
> the reason: retry three times, then text an hour later. So the customer gets a message at 10:40
> pm — outside the hours TRAI allows — and the retries fire against a bank that is still down."

Show the naive row: **148 recovered, ₹63,141 spent, 126 disputes, 2,256 rule violations.**

> "That's not a strawman. It's what untuned merchant automation does, and 112 of those violations
> are messages sent to people who had already replied STOP."

## 0:35–1:05 · Why cause-blindness is the problem

> "Razorpay already tells you why a payment failed — `error_reason`, `error_source`, `error_step`.
> A bank outage, a blocked card and a mistyped CVV arrive with different codes and need different
> answers. Retrying is right for the first, useless for the second, and irrelevant for the third."

Point at the per-cause table: naive scores **16% on bank outages against a 42% do-nothing
baseline**. Retrying into an outage is worse than doing nothing.

## 1:05–1:50 · The architecture, one slide

`docs/ARCHITECTURE.md` diagram on screen.

> "Detect, diagnose, decide, act, verify. Diagnosis is a lookup table over Razorpay's own error
> vocabulary — about 150 reasons onto 12 root causes. No model, because the vocabulary is
> published and guessing would only add error.
>
> Every decision goes through a policy engine with 41 numbered rules in one YAML file: TRAI
> messaging hours, RBI's fair practices window, NPCI's execution windows, the ₹15,000
> authentication threshold. The language model can reorder the actions the engine has already
> approved and write the message. It cannot add an action, revive a refused one, or move a
> deadline."

## 1:50–3:20 · Live, on a real Razorpay test account

Terminal: `wapsi live watch`

> "This is my Razorpay test account. I failed a ₹1,299 netbanking payment a minute ago."

Show the ingest line, then the diagnosis, then — this is the moment — **the refusal**:

```
23:24  observation  detected on Razorpay: payment_failed, ₹1,299 on netbanking
23:24  diagnosis    the bank rejected it at the authorization step; transient, recoverable
23:25  verdict      waiting until 05 Sep 10:00 to SEND_PAYMENT_LINK (expected ₹428)
```

> "It knows this is worth ₹428 and it refuses to send anything, because it's 11:25 at night. It
> schedules for ten in the morning, and it records which rule stopped it."

Then, inside the window, run it again: the agent creates a **real payment link**, visible in the
Razorpay dashboard. Pay it with the mock bank's Success button. Next poll:

```
10:02  live_pay_…: RECOVERED ₹1,299
```

Click the case in the dashboard and scroll the audit trail.

> "Every line has the rule ids that produced it. Nothing here is a summary written afterwards —
> it's the log the agent wrote as it went."

## 3:20–4:20 · The numbers

> "One demo proves nothing, so: 500 cases, four policies, the same batch and the same random
> draws."

| | recovered | ₹ net | disputes | violations |
|---|---|---|---|---|
| do nothing | 111 | ₹4,53,197 | 0 | 0 |
| naive | 148 | ₹15,81,044 | 126 | 2,256 |
| **Wapsi** | **234** | **₹17,63,358** | **1** | **0** |

> "₹13.1 lakh above doing nothing, for ₹662 of cost, and zero rule violations — judged by the same
> engine that scores the baseline's 2,256.
>
> Three things I'd rather you heard from me than found yourselves. The measured results are a
> simulation, because test mode can't produce 500 failures — the priors are published figures, and
> the ranking survives scaling them ±30%. Excluding every case that would have resolved itself
> anyway, the strict figure is ₹14.3 lakh, not ₹17.6. And we contacted 51 people who'd have paid
> without us; that's counted against us in the report."

## 4:20–4:45 · What broke

Pick one — the strongest is the weekly cap:

> "The agent kept abandoning recoverable invoices seconds after the first reminder. The rule doing
> it was the per-customer weekly message cap — which is temporary, it lifts as messages age out.
> But it was reported to the planner with no expiry time, so the planner couldn't tell 'blocked
> until Tuesday' from 'blocked forever' and closed the case. Every temporary rule now returns the
> moment it lifts."

Or the honest one about the model:

> "I measured what the model was worth and it was ₹39,518 — all of it one case out of five hundred,
> where a customer wrote 'paisa Friday ko bhej dunga' and only the model got the date right. The
> regex nudged too early and lost them to an opt-out. That's the real size of it, and the report
> prints the comparison in both directions."

## 4:45–5:00 · Next

> "Inside Razorpay this consumes `payment.downtime` directly instead of inferring outages, hands
> abandoned checkouts to Magic Checkout, and learns the priors per merchant from their own history
> — the architecture already isolates them in one file for exactly that."

---

## Recording notes

- Terminal at a large font; the audit lines are the point and they must be readable.
- Don't narrate the code. Narrate decisions and show their consequences.
- The refusal at 23:25 is the single most persuasive fifteen seconds. Don't rush it.
- If the live loop misbehaves on the day, `wapsi simulate --n 60` gives the same story in ten
  seconds and is a legitimate fallback — say which one you're showing.
