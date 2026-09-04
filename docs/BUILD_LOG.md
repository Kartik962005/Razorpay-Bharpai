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

**Got out:** (pending) — the secret typed into the dashboard and the one in `.env` differ; realign
and re-fire.
