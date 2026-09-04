# Build log — what broke, and how we got out

Kept in real time. Newest at the bottom. This feeds the last question on the application form.

## 2026-09-04 — research and design

**Broke:** Planned to drive the live demo with webhooks. Two problems at once: there is no
public Razorpay API to *force* a failed payment in test mode (failures only come from the checkout
UI with `failure@razorpay` / the test-card Failure button, or the dashboard's "Charge this now →
Failure" for subscriptions), and this machine has no tunnel tool (no ngrok, no cloudflared), so
Razorpay could not reach a local webhook endpoint anyway.

**Got out:** Split the system into two modes sharing one agent core. `sim` mode owns the
metrics (500-case batch, deterministic seed, four policies). `live` mode owns the demo and uses a
**poller** against the Payments / Payment Links / Subscriptions APIs as the default ingestion path,
with the webhook endpoint kept as an optional upgrade. Failures in the live demo are triggered by
hand through checkout, which is honest about what test mode allows.

**Broke:** The Razorpay error docs list `reason` values but the per-method page only lists
`source` and `step` — there is no single official table joining the three.

**Got out:** Built the join ourselves in `core/taxonomy.py` from the two pages, and treat any
unmapped reason as `UNKNOWN` with a conservative policy (one retry, then escalate) rather than
guessing.

## 2026-09-04 — wiring the test account

**Broke:** Registered the webhook, fired a real `payment_link.cancelled` event (create + cancel a
₹10 link via API), and nothing arrived at the receiver in 45 s.

**Got out:** Instead of guessing, read the configuration back with `GET /v1/webhooks` using the
merchant key. The URL on file was the bare tunnel host — the `/webhooks/razorpay` path had been
dropped when pasting. Confirmed by adding a `POST /` alias to the receiver: Razorpay then delivered
both the new event *and* its retry of the earlier one within seconds. Lesson kept in the code:
the real app accepts the webhook on `/` as well as `/webhooks/razorpay`, and `wapsi live doctor`
prints the registered webhook URL/events so this cannot silently recur.

**Broke:** Both LLM model ids from the plan (`llama-3.3-70b-versatile`, `llama-3.1-8b-instant`)
returned 404 from Groq — the catalogue had rotated.

**Got out:** Listed `GET /models` on the account and switched to `openai/gpt-oss-120b` (planner /
briefs) and `openai/gpt-oss-20b` (messages / reply parsing). Two consequences baked into the
adapter: these models spend tokens on reasoning, so a tiny `max_tokens` returns an empty message —
use ≥ 300 and `reasoning_effort: low`; and JSON mode works (a Hinglish "paisa Friday ko bhej
dunga" parsed to `promise_to_pay / Friday / 0.95` on the first try).

**Broke:** Delivered webhooks failed signature verification (`X-Razorpay-Signature` did not match
HMAC-SHA256 of the raw body with the secret in `.env`), although the scheme itself matches the
official SDK's verifier on a dummy secret.

**Got out:** Two layers. First, the value in `.env` (51 chars) was not the value in the dashboard
(a 48-char generated token) — realigned `.env`. Second, the receiver process had loaded the old
secret at start-up, so the very next delivery still failed even though the file was now right;
restarting it fixed that. Re-fired `payment_link.cancelled` → `signature_ok: true`. Lessons kept:
`wapsi live doctor` prints a short fingerprint of the loaded webhook secret (never the value) so
a stale process is obvious, and the app logs the fingerprint it loaded on boot.

## 2026-09-04 — session 1: diagnosis, policy engine, guardrails

**Broke:** The first test run never reached a test. `ZoneInfo("Asia/Kolkata")` raised
`ZoneInfoNotFoundError` at import time: Windows ships no IANA timezone database, and every rule in
this system is a statement about Indian local time.

**Got out:** Added `tzdata` as a platform-conditional dependency *and* a fixed-offset fallback in
`config.py`. India has observed a constant UTC+05:30 since 1945, so the fallback is exact rather
than an approximation, and the package now runs on a machine with no timezone data at all.

**Broke:** A test asserted that a ₹18,000 mandate failure would be denied a retry under R14, and
it failed — no R14 in the denials.

**Got out:** The test was wrong and the code was right, which is the good version of this. A
`MANDATE_ISSUE` never proposes `RETRY_CHARGE` in the first place, so the AFA rule had nothing to
deny. R14 earns its keep on a *different* case: a large subscription failing for
`insufficient_funds`, where a retry looks perfectly reasonable and is still illegal above ₹15,000.
The test now exercises that instead, which is also the case a real merchant would get wrong.

**Broke:** Printing a diagnosis to the Windows console raised `UnicodeEncodeError` on the ₹ sign
(cp1252). Harmless in tests, fatal for a CLI whose entire output is rupee amounts.

**Got out:** Noted for the CLI: reconfigure stdout to UTF-8 on start-up rather than avoiding the
symbol. A recovery tool that cannot print ₹ is not finished.

## 2026-09-04 — session 2: the simulator, and what it exposed

The batch runner was built to measure the agent. It spent most of the session finding faults in
it instead, which is the correct outcome for a measurement tool and the reason to build one
before believing any number.

**Broke:** The agent abandoned recoverable invoices seconds after its first reminder, closing them
with "no permitted action is worth more than it costs". The rule doing it was R23, the
per-customer weekly message cap.

**Got out:** R23 is a *rolling* cap — it lifts as old messages age out — but it was reported to
the planner with no expiry time, so the planner could not tell a temporary block from a permanent
one and treated every one as terminal. The engine now returns the moment the cap lifts, and the
planner waits instead. The same bug class hid in the snapping logic: satisfying a nudge gap could
land the action outside the messaging window, and only one round of snapping was done, so the
action was silently discarded. It now snaps repeatedly until the time is clean.

**Broke:** The naive baseline recovered more money than the agent. Not a bug in the agent — a flaw
in the policy. A single nudge cap of three was being applied to a ₹299 subscription over a
fortnight and to a ₹5,00,000 B2B invoice over a month.

**Got out:** Caps are now per scenario. Chasing an invoice five times in a month at three-day
intervals is ordinary commercial practice; what the fair practices code forbids is harassment, not
periodic invoicing. The agent overtook the baseline on net rupees and never stopped leading on
compliance.

**Broke:** Retrying into a bank outage looked like a fine idea, because transient failures were
generated independently of the world's outage schedule. Most "transient" declines happened while
the bank was perfectly healthy, so hammering worked and waiting looked pointless.

**Got out:** A transient failure now *is* an outage: generating one creates the downtime window it
happened inside. Recovery on that class went from 16% for the hammering baseline to 86% for the
agent. A test asserts the invariant so it cannot drift back.

**Broke:** The agent could never retry a subscription charge at all. Every attempt was refused
under R13, the RBI pre-debit notification rule — correctly, because nothing in the system could
ever send that notification.

**Got out:** Added it as a first-class action. The interesting part was pricing it: a notice
recovers nothing by itself, so its expected value is the value of the retry it unlocks a day
later, discounted. That framing lets a purely regulatory step compete for the planner's attention
on revenue terms. It is exempt from the nudge budget, since refusing to send it would make the
mandate permanently uncollectable.

**Broke:** Merchant-configuration failures recovered 0% under the agent and 10% under doing
nothing — the agent was actively worse. It alerted the merchant and closed the case immediately.

**Got out:** Three faults behind one number. The hard stop was reading "never chase the customer
for the merchant's mistake" as "never touch this case again", when a silent retry chases nobody.
The alert stayed a candidate action after firing, so the merchant was alerted three times in as
many minutes. And the retry-spacing rule had been written to apply only to outage cases, so
retries fired ninety seconds apart. With the gap applied to every retry, the alert fired once, and
the agent's belief about *when* a merchant fixes their settings modelled as a ramp rather than a
constant, the agent now recovers 100% of them — and the naive baseline's rapid-fire retries went
from 1,549 recorded violations to 2,220, because the same rule now catches them too.

**Broke:** Two cases the tests said were bugs were not. A ₹18,000 mandate failure was not denied
under the AFA rule, and abandoned checkouts recovered nothing.

**Got out:** In the first, the code was right and the test was wrong: a mandate failure never
proposes a retry, so there was nothing for the rule to deny. It was rewritten against the case
where the rule actually earns its keep — a large subscription failing for insufficient funds,
where retrying looks reasonable and is still unlawful. In the second, reading the audit log showed
the agent behaving exactly as designed; 0 of 6 was variance. The lesson both times was to read the
trail before changing the code.

## 2026-09-04 — session 3: the language model, and keeping it on a leash

**Broke:** The first model-written message read: *"Your payment of ₹1,299 failed due to
INSTRUMENT_BLOCKED – the card was declined by the bank."* It passed every guardrail.

**Got out:** Two faults. The prompt was handing the model the internal enum name as the
explanation, so it printed it. And the validator had been written to catch threats and missing
opt-out lines, not internal vocabulary, so nothing stopped it. The prompt now passes the
plain-English summary, and the validator rejects any `CAPITALS_WITH_UNDERSCORES` token in customer
text — a rule that will keep catching this class of leak long after this particular prompt
changes.

**Broke:** That new guardrail then failed its own test. The regex matched nothing at all, and the
source line looked perfectly correct in the editor.

**Got out:** `cat -A` showed the file contained literal backspace bytes where `\b` should have
been: the patch had gone through a shell heredoc, which ate the escape and wrote control
characters into the source. Repaired by writing the file directly rather than through the shell,
and a scan of every tracked source file confirmed nothing else was corrupted. The lesson is about
tooling rather than payments, but it cost half an hour of staring at a correct-looking regex.

**Broke:** A test that passed `None` to the model adapter crashed instead of returning `None`.

**Got out:** The disabled check lived inside the transport layer, so every task method built its
prompt — touching its arguments — before discovering there was no model to send it to. Each task
now bails first. This matters more than it sounds: the promise that everything runs without an API
key is only true if the no-model path never touches anything.

**Honest finding, not a break:** on this batch the model reads customer replies no better than the
regular expressions do — 93.9% against 93.0%. The replies are generated from templates, so the
patterns have an unfair advantage; on real Hinglish the gap would look different. Reported as
measured rather than assumed, which is the reason for scoring the reading at all.

**Honest finding:** the model ignored the Hinglish instruction and wrote English messages with a
Hindi sign-off. Adding a worked example of a real Hinglish message to the prompt was the fix.

**Broke:** The model-advised policy and the deterministic one returned byte-identical results —
same 234 recoveries, same rupees, differing only by ₹2 of API cost. A suspiciously perfect tie.

**Got out:** The runner was handing the language model to *every* policy for reading customer
replies, including the one whose entire purpose is to be the model-free baseline. Both policies
were therefore reading replies with the model and planning almost identically, so the comparison
measured nothing. The model is now scoped to the agent policy alone, for reading as well as for
planning. Worth noting what this cost the headline number: once the agent had to act on what it
*read* from replies rather than on what the customer actually meant, the deterministic policy's
net recovery fell from ₹18.3L to ₹17.6L. That gap is the price of imperfect comprehension, and it
belongs in the result rather than being hidden by a leak of ground truth.

**What the model was actually worth.** With the boundary fixed, the model-advised policy recovered
₹17,63,358 net against the deterministic policy's ₹17,23,840 — and the entire ₹39,518 difference
is a single case. On case_0134 a customer replied *"paisa Friday ko bhej dunga"*. Both policies
read it as a promise to pay; only the model resolved the date correctly. The pattern-matching
policy worked out a date that had already passed, nudged again three days later, received
*"bahut messages aa rahe hain, band karo"*, and closed the case as an opt-out — losing ₹39,522.
The model-advised policy stayed quiet and the customer paid on the 11th.

That is the honest size of the model's contribution here: not a sweeping improvement, one case out
of five hundred, entirely explained by reading a date in Hinglish correctly. It is worth saying
plainly, because the reverse table is also in the report — the deterministic planner beat the
model-advised one on zero cases — and a result this narrow would be easy to overstate.

Also measured and reported rather than smoothed over: 114 of the agent's 604 model calls failed
outright under rate limiting, and the call budget ran out partway through the batch, so a large
share of its messages came from templates. The run completed anyway, which is the property the
budget exists to provide.

## 2026-09-04 — session 4: the live account, the webhook and the dashboard

**Broke:** `payment_link.notify_by(...)` raised `AttributeError`.

**Got out:** The Razorpay Python SDK spells the same operation two ways — `notifyBy` on a payment
link, `notify_by` on an invoice. Found by introspecting the client rather than trusting the docs,
which is a habit worth keeping for any SDK. Wrapped once in the gateway so nothing above it has to
know, with a test that pins both spellings.

**Broke, in the sense of "cannot be done at all":** there is no way to re-attempt a charge from
the server in test mode. A retry needs the customer to authenticate it.

**Got out:** `retry_charge` reports `attempted: False` with the reason, rather than returning a
plausible-looking failure. The agent then treats it like any other unavailable action and falls
through to the next best one, which is a recovery link — which is why the live demo shows links
where the batch shows silent retries. Saying this in the code and the README is better than a demo
that quietly implies a capability the sandbox does not have.

**Broke:** A test that swapped the webhook receiver had no effect — the endpoint kept using the
one from `.env` and rejected the test's signatures.

**Got out:** The route had closed over the receiver instead of reading it from application state.
Reading it from `request.app.state` fixed the test and is better regardless: it means a rotated
secret can be picked up by replacing the receiver rather than restarting the process, which is the
same class of stale-state problem that cost an hour during setup.

**Verified end to end, without a real payment:** a genuine Razorpay webhook (created by making and
cancelling a ₹10 payment link through the API) travelled through the Cloudflare tunnel into the
running app, passed HMAC verification against the configured secret, and was acknowledged with a
200. The `payment.failed` path is covered by tests using Razorpay's own payload shape, including
signature failure, body tampering and retry deduplication. The one step that cannot be automated —
paying a link with `failure@razorpay` to produce a real decline — is left to a person, and the
seed command prints the instructions for it.

## 2026-09-04 — session 4, continued: the loop, on a real account

**Broke:** The whole live demo was designed around UPI and the `failure@razorpay` test handle.
The checkout offered Cards, Netbanking and Wallet — no UPI at all. Every published guide to
testing Razorpay failures leads with `failure@razorpay`, and on this account it is unreachable.

**Got out:** UPI is not enabled on the test account's payment methods, and enabling it is a
merchant onboarding step rather than something a script can do. Netbanking works instead: test
mode serves a mock bank page with explicit Success and Failure buttons, which produced a genuine
decline — `error_reason=payment_failed, source=bank, step=payment_authorization`. The demo
instructions now lead with netbanking and mention UPI as the alternative where it is enabled,
which is the reverse of what every guide says and the opposite of what I had written.

**What the run proved.** Two real failed payments and one overdue invoice were ingested from the
account, diagnosed from Razorpay's own error fields, and — at 23:25 IST — the agent declined to
contact anybody, scheduling the first message for 10:00 the next morning under the TRAI messaging
window. Both `payment.failed` webhooks reached the app through the tunnel and passed signature
verification. The recovery half of the loop happens when that window opens.

That refusal is the demonstration. An agent that sends a payment link at 23:25 because the
expected value looks good is the thing this project exists to argue against, and the log shows it
declining to do so on live data with the rule and the deferred time recorded.

## 2026-09-04 — session 5: documentation, and proving the keyless claim

**Checked rather than assumed:** the README claims the whole system runs with no API keys. Hiding
`.env` and re-running proved it — 160 tests pass, `wapsi simulate` produces the full comparison
table, and `wapsi live doctor` degrades to a readable "no keys" report instead of failing. That
claim is the reason a judge can reproduce the numbers, so it needed to be tested rather than
believed.

**Broke, mildly:** with no keys, `live doctor` reported `razorpay auth: failed 1` — an exception
string with no information in it, from attempting an API call that could not possibly work.

**Got out:** it now checks whether keys are configured before trying, and prints the exception
type alongside its message when a call genuinely fails. A diagnostic tool whose failure output is
the single character `1` is worse than no diagnostic tool.

**Cleanup:** `scripts/check_keys.py` and `scripts/webhook_probe.py` were scaffolding written before
the package existed; `wapsi live doctor` and `wapsi serve` do both jobs properly now, so they are
gone. What remains in `scripts/` is documented.

## 2026-09-05 — reviewing it the way a committee would

Sat down with the finished build and read it as a sceptical Razorpay reviewer with a thousand
other submissions to get through. Four things did not survive that reading.

**"Your naive baseline is a strawman."** Fair. Retrying three times in fifteen minutes and then
texting at midnight is what bad automation does, but nobody is choosing between Wapsi and that.
They are choosing between Wapsi and Razorpay's own defaults — the subscription retry ladder and
`reminder_enable` on links and invoices. Added a `platform` policy that does exactly and only that,
with reminders in daytime batches and no retries of one-off payments, and tagged its actions as
the platform's so it is not scored for rule breaks. It nets ₹14.5L on the batch. Wapsi nets ₹17.6L.
That ₹3L gap is the honest claim; the ₹13L gap against doing nothing is true but less interesting.

**"Your sensitivity only scales everything uniformly. Turn down the penalties and naive wins."**
Ran exactly that: night contact barely annoys, opt-outs take twice as many messages, hammering a
risk decline never causes a chargeback, chargebacks are free. Wapsi still leads — ₹20.1L to naive's
₹18.0L — so the ranking does not depend on the simulation being harsh. This was the single most
valuable hour of the review, because I did not know the answer before running it.

**"You say webhooks are supported. Your endpoint appends events to a list."** Also fair. The
signature was verified and the event was logged, and nothing happened. A verified `payment.failed`
now creates, diagnoses and persists the case immediately, so the next poll acts on it rather than
first having to discover it. That required the live audit log to be appendable from two processes,
which it had not been: the constructor truncated the file.

**"The model wrote 4% of the messages."** 38 of 897, because the call budget went mostly to an
advisor that was measured to add nothing. Reallocated: advisor sampling cut to 5%, budget raised,
and the report now states the model-written share, the guardrail rejection count and the Hinglish
adherence rate for every run — so the figure is a measurement in the open rather than something a
reviewer has to count in the audit log.

Also added: continuous integration on Linux and Windows with no secrets, so the keyless claim is
checked on every push rather than once by hand.

**Broke:** The re-run with a bigger model budget crawled — a tenth of the way through the agent
phase after twenty minutes, on course for hours.

**Got out:** The free tier meters requests per minute, and my backoff on a 429 was 1+2+4+8 seconds
before giving up: fifteen seconds of dead time per rate-limited call, and there were hundreds. The
right shape is the opposite — space calls out at just under the limit so they do not fail, retry
once, and otherwise fall back to a template. A throttle of 2.1 seconds between calls made the
runtime predictable instead of hostage to the provider's mood, and turned failed calls from a
runtime problem into the reported statistic they should have been all along.
