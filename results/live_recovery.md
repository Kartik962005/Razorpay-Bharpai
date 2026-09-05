# The loop closing, on a real Razorpay account

Not the simulation. A real payment on a real test-mode account, failed by hand, recovered by
the agent. Fourteen hours elapsed between the failure and the recovery, and most of that time
the agent was deliberately doing nothing.

```
04 Sep 23:24  adapter  observation detected on Razorpay: payment_failed, ₹1,299 on netbanking
04 Sep 23:24  llm      diagnosis   The payment failed during the bank’s authorization step, meaning the bank rejected the transaction. Because this is a transient technical issue, the money is not yet captured and can be retried or recovered by attempting the payment again.
04 Sep 23:25  planner  verdict     waiting until 05 Sep 10:00 to SEND_PAYMENT_LINK: acting later is worth more (expected ₹428)
05 Sep 10:07  planner  action      advised: SEND_PAYMENT_LINK has the highest expected recovery value and is a low‑cost, direct way to let the customer complete the failed netbanking payment.
05 Sep 10:07  adapter  result      sent whatsapp in en, tone helpful
05 Sep 10:13  adapter  outcome     payment already received; stopping before acting [R01]
```

`outcome=recovered  recovered=₹1,299  cost=₹0.80  messages=1`

## What each line is

**23:24** — a genuine netbanking failure on the merchant's account, ingested from the
Razorpay API with its own error fields: `error_reason=payment_failed`, `source=bank`,
`step=payment_authorization`. Diagnosed as `TRANSIENT_TECH`, which is recoverable.

**23:25 — the line the project exists for.** The agent knows this payment is worth ₹428 in
expected recovery and refuses to send anything, because it is half past eleven at night and
TRAI permits customer messaging between 10:00 and 21:00. It schedules for 10:00 and records
why. An agent that texts at 23:25 because the expected value looks good is precisely what
this design argues against.

**10:07** — the window opens and it acts within seconds, unprompted. It creates a real
payment link in the merchant's account, tagged with the case id, and sends it. The choice of
action and channel came from the planner; the policy engine had already narrowed the options.

**10:13 — the guard.** The payment arrived. On its next pass the agent refetched the status
before doing anything, found the money, and stopped: `[R01] payment already received`. It
closed the case rather than sending a second message.

That last line matters more than the recovery. R01 is the promise that nobody gets chased for
money they have already sent, and on the live path it was broken until the night before this
run: a case born from a failed payment carries an order id, and the check looked only at
links, invoices and subscriptions. `docs/BUILD_LOG.md` has the full account.

Total cost of the recovery: 80 paise, one message.