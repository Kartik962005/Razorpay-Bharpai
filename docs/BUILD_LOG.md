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
