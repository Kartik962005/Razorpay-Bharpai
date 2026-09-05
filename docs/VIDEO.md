# Five minutes of it working

They asked for a repo that runs, a video of it running, and what broke at 2 a.m. So this is a
screen recording of the terminal, not a slide deck. Every command below is real, and every one is
fast enough to record live — measured on this machine:

| command | time |
|---|---|
| `wapsi simulate --n 500` (4 policies) | 13s idle, ~30s under load |
| `wapsi rules` | under 2s |
| `wapsi case case_0134` | under 2s |
| `wapsi case live_pay_…` | under 2s |
| `wapsi live doctor` | 4–10s (it calls the Razorpay API) |
| `wapsi live watch --once` | 5–8s (same) |

Under a minute of machine time in a five-minute video. Nothing needs cutting or speeding up — but
close other heavy applications first, since the batch is the one command long enough to feel slow
if the machine is busy. Talk while it runs; the pause is not dead air if you are explaining what
it is doing.

## Before you hit record

- Terminal at a large font — 16pt or more. The audit lines are the point and they must be legible.
- Full screen, no notifications, one window.
- `cd` into the repo and activate the venv so the first command is the first thing on screen.
- Have `results/live_recovery.md` open in a second tab as a fallback if the live account misbehaves.
- Optional title and closing cards: the slide deck at the artifact link — use only slide 1 at the
  start and the last slide at the end. The middle is the terminal.

---

## 0:00 – 0:25 · The problem, said once, over a static screen

> "A payment fails. Almost every recovery system then does the same thing regardless of why it
> failed — retry on a fixed ladder, remind on a fixed cadence. That treats a twenty-minute bank
> outage, a blocked card and a mistyped CVV as the same event. One of those three can be fixed by
> retrying. The other two can't. This is an agent that reads Razorpay's own failure codes, works
> out which one it is, and acts only when it's allowed to."

Don't linger. Get to the terminal.

## 0:25 – 1:30 · The batch, running live

```bash
wapsi simulate --n 500 --policies do_nothing,platform,naive,rules
```

Around fifteen seconds. Talk through it as it runs:

> "Five hundred synthetic cases. Four policies over the identical batch with the same random
> draws, so the differences are decisions, not luck."

When the table lands, read the row that matters:

> "Row two is Razorpay's own defaults — the subscription retry ladder and reminder_enable, what a
> merchant gets from the platform unprompted. That's the fair comparison, and it recovers ₹14.5
> lakh. Wapsi recovers ₹17.6 lakh, with a third fewer messages — 816 against 1,196 — and loses 61
> customers to opt-outs instead of 140.
>
> Row three is untuned merchant automation. It breaks the rules 2,256 times, and 126 of its
> recoveries turn into chargebacks. Those violations are scored by the same engine Wapsi obeys."

## 1:30 – 2:05 · The rules are real, and they're in one file

```bash
wapsi rules
```

> "Forty-one numbered rules. TRAI's messaging window, RBI's fair-practices hours for receivables,
> NPCI's execution windows, the ₹15,000 authentication threshold, hard stops on opt-out and
> dispute and risk decline. They live in one YAML file, not scattered through the code — change a
> number there and the whole system changes with it."

Optionally `cat policy.yaml | head -40` for two seconds to show it is genuinely one readable file.

## 2:05 – 2:50 · One case, end to end

```bash
wapsi case case_0134
```

> "Every decision writes to an append-only log with the rule id that produced it. This is the case
> where the language model earned its keep — the only one in five hundred. The customer replied
> *paisa Friday ko bhej dunga*. Both planners read it as a promise to pay; only the model resolved
> the date. The pattern-matching version worked out a date already past, nudged again three days
> later, got *bahut messages aa rahe hain, band karo*, and lost ₹39,522 to an opt-out."

That is the honest size of the model's contribution, and saying so is stronger than inflating it.

## 2:50 – 4:05 · The live account — the centrepiece

```bash
wapsi live doctor
```

> "This is a real Razorpay test account. Keys, model, webhook, all live."

Then the completed loop:

```bash
wapsi case live_pay_TY37K6kpZ1pGmE
```

A live case id reads the live trail automatically — same command as the batch case.

> "Last night I failed a real ₹1,299 netbanking payment on this account. Here is what the agent
> did with it.
>
> 23:24, it ingested the failure from the API and diagnosed it from Razorpay's own error fields.
>
> **23:25 — it refused to act.** It knows this payment is worth ₹428 in expected recovery, and it
> will not send anything, because TRAI permits customer messaging between 10 a.m. and 9 p.m. It
> scheduled for 10:00 and recorded the rule that stopped it.
>
> 10:07 this morning, the window opened and it acted within seconds — created a real payment link
> in the account and sent it. I paid it.
>
> 10:13 — before acting again it refetched the status, found the money, and stopped. Rule R01.
> Eighty paise, one message, ₹1,299 recovered."

Then show the agent still holding a live case:

```bash
wapsi live watch --once
```

> "And here it is right now, declining to chase the ₹15,000 invoice — it already sent one reminder
> today and the receivables rule spaces them 72 hours apart, so it's waiting until the 8th."

A live refusal, unscripted, is worth more than a live send.

## 4:05 – 4:40 · What broke at 2 a.m.

This is the literal answer to their question, and it happened at 01:36.

> "At about half one this morning I was reading the live state, waiting for the messaging window,
> and I noticed a case id that didn't belong to Razorpay — a test fixture had written into real
> state. Fixing that made me look at the other live cases properly, and I found something worse.
>
> The guard that stops the agent chasing someone who has already paid didn't work on the live
> path. A case born from a failed payment carries an order id, and the check looked only at
> payment links, invoices and subscriptions. If the customer went back and paid the original link,
> the order settled and the agent would have kept messaging them.
>
> That's the failure this whole project claims to prevent, sitting in my own code. And the batch
> could never have caught it — the simulated gateway answers from the case object, so it always
> agreed with whatever the agent already believed. A simulation only tests the questions you
> thought to ask it. I found it by reading real Razorpay ids at half past one, not by running
> anything.
>
> Fixed by checking the order too, by status and by amount_paid, since partial settlement is
> reported as an amount rather than a status. Three tests pin it. The R01 line you just saw fire
> on a real payment is that fix working."

## 4:40 – 5:00 · Close

```bash
pytest -q
```

> "194 tests, no network, CI on Linux and Windows with no keys. Clone it, `pip install -e .`,
> `wapsi simulate` — no API key needed, and it reproduces every number I just showed you. The
> language model is optional throughout; without one it runs on templates and pattern matching and
> the batch result is the same."

End on the repo URL.

---

## If the live account misbehaves on the day

Don't improvise against a live API on camera. Fall back to:

```bash
cat results/live_recovery.md
```

The committed trail is the same evidence, and say plainly that you are reading the recorded run.
`wapsi simulate --n 60` gives the whole batch story in three seconds if you need to fill.

## What not to do

- Don't walk through the code file by file. They can read it; the video is for what they can't.
- Don't claim the model does more than it does. The measured figure is one case, and the honesty
  is the differentiator.
- Don't skip the 23:25 refusal to get to the recovery. The refusal is the argument.
