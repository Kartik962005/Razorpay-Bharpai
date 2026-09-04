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
