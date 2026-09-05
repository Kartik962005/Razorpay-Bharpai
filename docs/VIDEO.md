# The five-minute video

Two parts. **Part 1 is what you do before recording** — about fifteen minutes, and it is what stops
you discovering a problem on take four. **Part 2 is the recording itself**: what to type, and what
to say while it runs.

They asked for a repo that runs, a video of it running, and what broke at 2 a.m. So this is a
screen recording of a terminal, not a slide deck. Every command is real and none is faked.

---

# Part 1 — Before you record

## Step 1 · Settle the name

If the project is being renamed, do it **before** recording. Every command you type on camera
starts with the package name, so a rename after the fact means recording again.

## Step 2 · Set up the terminal

1. One window, full screen, no other applications, notifications off.
2. Font at **16pt or larger**. The audit lines are the whole point and they must be legible.
3. `cd` into the repo and activate the venv, so the first thing on screen is your first command:
   ```
   .venv\Scripts\activate
   ```
4. **Check the terminal width.** Run this:
   ```
   bharpai simulate --n 60
   ```
   Look at the table it prints. **The `disputes` column must be there.** The table sheds columns
   rather than truncating numbers, so a narrow window silently costs you that column — and "126
   chargebacks" is a line you are going to say. If it is missing, widen the window or drop one
   point of font size until it comes back.
5. Clear the screen. `cls` on Windows.

## Step 3 · Dry-run every command once

Run all six, in this order, and watch that each behaves. This is also how you learn the pacing.

```
bharpai simulate --n 500 --policies do_nothing,platform,naive,rules
bharpai rules
bharpai case case_0134
bharpai live doctor
bharpai case live_pay_TY37K6kpZ1pGmE
bharpai live watch --once
pytest
```

Measured on this machine:

| command | time |
|---|---|
| `bharpai simulate --n 500` (4 policies) | 14s idle, ~30s on a busy machine |
| `bharpai rules` | 1s |
| `bharpai case case_0134` | 1s |
| `bharpai live doctor` | 6s — Razorpay API, the model, and a webhook probe |
| `bharpai case live_pay_…` | 1s |
| `bharpai live watch --once` | 5s |
| `pytest` | 17–24s |

About a minute of machine time inside five minutes of video. Two commands are long enough that you
must talk over them rather than watch in silence: **the batch** and **`pytest` at the close**.

## Step 4 · Put the results back

The dry run overwrote them. `bharpai simulate` rewrites `results/` with only the policies you ran,
which drops the model-advised row and the model statistics from the committed evidence.

```
git checkout -- results/
```

**Do this again after recording.** Set a reminder; it is the easiest thing in this list to forget.

## Step 5 · Know what `live doctor` will say

It will report the webhook as **not reachable**. That is correct and deliberate — the tunnel is
closed, and the command probes the endpoint rather than trusting Razorpay's `active` flag. There
is a line in the script for it. Do not be thrown by red text.

If you would rather it were green: run `bharpai serve` in a second terminal, then
`cloudflared tunnel --url http://localhost:8000`, and paste the new hostname into the Razorpay
dashboard webhook — quick tunnels get a fresh hostname every time, so the registered URL has to be
updated too. Only worth it if you have already done it once today. Nothing in the video depends on
the webhook, and a tunnel is one more thing that can fail on camera.

## Step 6 · Open the fallback in a second tab

`results/live_recovery.md`. If the live account misbehaves, you read the committed trail instead
and say plainly that you are reading the recorded run. Do not improvise against a live API on
camera.

---

# Part 2 — The recording

Seven shots. For each: what to **type**, and what to **say**.

The words are a script, not a transcript — say them in your own voice, but **do not add to them.**
It is 750 words: 5:10 at a normal pace, 4:50 if you are brisk. Five minutes is
tight, and every sentence below is load-bearing.

**If you find yourself running long,** two things can go and nothing else: the optional
`policy.yaml` shot in Shot 3, and the first paragraph of Shot 4 (the audit-log description — the
trail is on screen and speaks for itself). That buys you about twenty seconds. Never cut the 23:25
refusal in Shot 5 or the bug in Shot 6; those two are the submission.

---

## Shot 1 · 0:00 – 0:20 · The problem

**Type:** nothing. Start on the clean prompt.

**Say:**

> "A payment fails. Most recovery systems then do the same thing regardless of why — a fixed retry
> ladder, a fixed reminder cadence. That treats a bank outage, a blocked card and a mistyped CVV as
> one event. Only one can be fixed by retrying. This agent reads Razorpay's own error codes, works
> out which it is, and acts only when it's allowed to."

Do not linger. Get to the terminal.

---

## Shot 2 · 0:20 – 1:25 · The batch, running live

**Type:**

```
bharpai simulate --n 500 --policies do_nothing,platform,naive,rules
```

**While the four policies run (about 14 seconds), say:**

> "Five hundred synthetic cases, four policies, identical batch and identical random draws — so
> any difference is a decision, not luck."

**When the table lands, put your cursor on row two and say:**

> "Row two is Razorpay's own defaults — the retry ladder and `reminder_enable`. That's what a
> merchant gets from the platform for free, and it's the fair comparison, not the do-nothing row.
> ₹14.5 lakh.
>
> Bottom row is mine: ₹17.2 lakh, from a third fewer messages — 816 against 1,196 — losing 62
> customers to opt-outs where the defaults lose 140.
>
> Row three is untuned automation, the thing people actually build. It breaks the rules 2,256 times
> and 126 of its recoveries become chargebacks — scored by the same engine my agent obeys."

---

## Shot 3 · 1:25 – 1:55 · The rules are real, and they are in one file

**Type:**

```
bharpai rules
```

**Say:**

> "Twenty-six numbered rules. TRAI's messaging window, RBI's fair-practices hours, NPCI's
> execution windows, the ₹15,000 authentication threshold, hard stops on opt-out and dispute.
>
> One YAML file, not scattered through the code — and every allow and deny in the audit log cites
> one of these ids."

**Optional, two seconds only,** if the pacing allows:

```
type policy.yaml
```

Just to show it is genuinely one readable file. Skip it if you are behind.

---

## Shot 4 · 1:55 – 2:40 · One case, end to end

**Type:**

```
bharpai case case_0134
```

**Say, over the trail:**

> "Every decision writes to an append-only log with the rule id behind it — diagnosis, expected
> value, the rule behind each step, the reply as it was read, the outcome. Nothing written
> afterwards."

**Then say the thing most submissions will not — this is the most valuable thirty seconds in the
video:**

> "I also measured what the language model is worth. The answer is nothing measurable.
>
> On my first run it looked like the model won a case worth ₹39,000. I ran it again with five times
> the coverage and the advantage vanished — 232 recovered against 233. One case in five hundred was
> noise.
>
> The model writes the Hinglish and reads the replies. The rules recover the money. I'd rather tell
> you that than sell you the first number."

---

## Shot 5 · 2:40 – 3:55 · The live account — the centrepiece

**Type:**

```
bharpai live doctor
```

**Say:**

> "A real Razorpay test account — keys live, model live. Note the webhook row: registered, 24
> events taken, but the tunnel isn't running and the check says so rather than trusting Razorpay's
> `active` flag. Polling is the default precisely so none of this needs a public URL."

*(A preflight that reports its own broken dependency is a better advert than one that prints
green. Say it without apology.)*

**Type:**

```
bharpai case live_pay_TY37K6kpZ1pGmE
```

**Say, walking down the four lines:**

> "Last night I failed a real ₹1,299 netbanking payment on this account.
>
> 23:24 — pulled from the API, diagnosed from Razorpay's own error fields.
>
> **23:25 — it refused to act.** It knows the payment is worth ₹428, and it sends nothing, because
> TRAI permits messaging between 10 a.m. and 9 p.m. It scheduled for 10:00 and logged the rule that
> stopped it.
>
> 10:07 this morning the window opened and it acted within seconds — created a real payment link,
> wrote the message, priced it, logged it. No SMS provider is wired in, so I opened that link from
> the log myself and paid it.
>
> 10:13 — before acting again it refetched, found the money, stopped. Rule R01. Eighty paise, one
> message, ₹1,299 recovered."

**Say the delivery gap out loud.** It is one adapter, a reviewer will find it in the code in under
a minute, and owning it costs nothing next to being caught claiming an SMS that never left.

**Type:**

```
bharpai live watch --once
```

**Say:**

> "And here it is now, declining to chase the ₹15,000 invoice — one reminder already sent, and the
> receivables rule spaces them 72 hours apart. Waiting until the 8th."

A live refusal, unscripted, is worth more than a live send.

---

## Shot 6 · 3:55 – 4:35 · What broke at 2 a.m.

**Type:** nothing. This is the literal answer to their question, and it happened at 01:36.

**Say:**

> "At half one this morning I was reading live state and saw a case id that didn't belong to
> Razorpay — a test fixture had written into real state. Fixing that made me read the other live
> cases properly, and I found something worse.
>
> The guard that stops the agent chasing someone who has already paid didn't work on the live path.
> A case from a failed payment carries an order id, and my check looked only at payment links,
> invoices and subscriptions. If the customer paid the original link, the order settled and the
> agent would have kept messaging them.
>
> That's the exact failure this project exists to prevent, in my own code — and the batch could
> never have caught it. The simulated gateway answers from the case object, so it always agreed
> with whatever the agent already believed. A simulation only tests the questions you thought to
> ask it.
>
> The R01 line you just watched fire on a real payment is that fix working."

---

## Shot 7 · 4:35 – 5:00 · Close

**Type:**

```
pytest
```

**Start it, then say the closing lines over the dots.** It takes about twenty seconds and the count
lands as you finish:

> "215 tests, no network, CI on Linux and Windows with no keys. Clone it, `pip install -e`,
> `bharpai simulate` — no key needed, and it reproduces every number I just showed you. The model is
> optional throughout: without one it runs on templates and pattern matching, and the batch result
> is identical."

**The last frame should be `215 passed`.** End there, with the repo URL on screen.

---

# If something breaks on the day

**The live account misbehaves.** Do not improvise against a live API. Switch to the second tab:

```
type results\live_recovery.md
```

Say plainly that you are reading the recorded run. The committed trail is the same evidence.

**You are running long.** Cut the optional `policy.yaml` shot, and cut the second half of Shot 4 —
but never cut the 23:25 refusal in Shot 5 or the bug in Shot 6. Those two are the submission.

**You need to fill fifteen seconds.** `bharpai simulate --n 60` tells the whole batch story in three
seconds.

---

# After you stop recording

1. `git checkout -- results/` — the recorded `simulate` overwrote them.
2. `git status` — confirm the tree is clean.
3. Upload as **unlisted**, and put the link in the form.

---

# What not to do

- **Don't walk through the code file by file.** They can read it. The video is for what they cannot
  read: that it runs, and that you know where it is weak.
- **Don't claim the model does more than it does.** The measured contribution is nothing, the
  honesty is the differentiator, and overselling it is the one thing that would undo the rest.
- **Don't skip the 23:25 refusal.** The refusal is the argument. Anyone can show a recovery.
