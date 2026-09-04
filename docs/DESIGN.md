# Wapsi — bounded revenue-recovery agent for Razorpay merchants

> *wapsi (वापसी): return, comeback.* Working name; easy to change.

**One line:** Wapsi watches a Razorpay merchant's failed payments, abandoned checkouts, halted
subscriptions and overdue invoices; diagnoses *why* each one failed from Razorpay's own error
signals; picks the one intervention that fits that cause; executes it within hard regulatory and
economic bounds; and reports the rupees it recovered on a batch — alongside the cases it gave up
on and why.

**What it solves:** Razorpay's built-in recovery is calendar-based (retry T+1/T+2/T+3, reminder
every N days). It treats "bank was down for 20 minutes" the same as "customer's card is blocked"
and "customer typed the wrong CVV". Cause-blind retries burn NPCI attempt limits, annoy customers,
and leave recoverable money on the table. Wapsi is cause-aware, bounded, and auditable.

---

## 1. How the judges' bar maps to the design

| Bar (verbatim) | Design answer |
|---|---|
| "Don't just identify the problem" | Every case ends in an *executed* action with a verified outcome, not a dashboard flag |
| "measured money recovered across a batch" | 500-case synthetic batch, four policies compared (do-nothing, naive, rules, agent), ₹ recovered net of cost, per-cause breakdown, sensitivity analysis |
| "compliant escalation" | Explicit escalation triggers → human queue with an LLM-written brief; RBI/TRAI/NPCI windows enforced in code |
| "stopping rules" | Hard stops (paid, opt-out, dispute, refund, risk-decline, merchant-config), count caps, age caps, economic stop (EV ≤ 0) |
| "audit trail" | Append-only JSONL: every observation, LLM proposal, policy verdict (allow/deny + rule), action, Razorpay entity IDs, outcome |

Plus the meta-bar from every track: *explainable, bounded, gated, honest.*

---

## 2. Scope: four revenue-at-risk scenarios (all Razorpay-native)

| # | Scenario | Trigger (webhook / poll) | Razorpay entity |
|---|---|---|---|
| A | One-off payment failure | `payment.failed` | Order + Payment |
| B | Checkout abandonment | Order `created` with no payment attempt after 20 min | Order |
| C | Subscription charge failure | `subscription.pending` / `subscription.halted` | Subscription + Invoice |
| D | Overdue receivable | Invoice / Payment Link unpaid past due, or `invoice.expired` | Invoice / Payment Link |

Everything else (checkout UX, fraud, refunds) is out of scope on purpose.

---

## 3. The loop

```
DETECT ──► DIAGNOSE ──► DECIDE ──► ACT ──► VERIFY ──► (loop | STOP | ESCALATE)
 webhook    taxonomy     policy     adapter  re-fetch    audit every step
 or poll    (+LLM expl.) (+LLM adv.) (idempotent) status
```

1. **Detect** — ingest Razorpay events (webhook endpoint with signature check, or a poller —
   both supported; poller is the default because it needs no tunnel).
2. **Diagnose** — map `error_reason` × `error_source` × `error_step` × `method` × amount ×
   history → a **root cause class** with a recoverability prior. Deterministic table; the LLM
   only writes the plain-English explanation.
3. **Decide** — the **policy engine** computes the allowed action set for this case *right now*
   (bounds, windows, caps, stops). A deterministic planner picks the best allowed action by
   expected value. Optionally an **LLM advisor** proposes an action + reason; the policy engine
   can veto it. Both modes are measured.
4. **Act** — execute through an adapter: real Razorpay test-mode API (payment link, notify,
   subscription resume), a messaging stub with per-channel cost, or the human queue. Every action
   is idempotent: **re-check payment status before acting** (never nudge someone who just paid,
   never double-charge).
5. **Verify** — poll/receive outcome; update case; compute realised recovery.
6. **Stop / escalate** — per the rules in §6.

---

## 4. Root-cause taxonomy (from Razorpay's error vocabulary)

| Class | Razorpay reasons (examples) | Recoverable? | Default intervention |
|---|---|---|---|
| `TRANSIENT_TECH` | bank_not_available, gateway_technical_error, upi_app_technical_error, request_timed_out, payment_declined_due_to_high_traffic, server_error | High | Silent retry after backoff, aligned to downtime-resolved signal / NPCI window; no customer contact on first retry |
| `INSUFFICIENT_FUNDS` | insufficient_funds, credit_limit_exceeded | Medium, time-dependent | Wait 24–72 h; soft nudge with alt method; for subscriptions retry near salary window (1st–7th) |
| `LIMIT_EXCEEDED` | transaction_daily_limit_exceeded, transaction_frequency_limit_exceeded, mcc_amount_limit_exceeded | High | Offer method switch now, or retry next day |
| `CUSTOMER_ABANDON` | payment_timed_out, payment_cancelled, otp_expired, payment_session_expired | Medium | One-tap payment link within 30 min, helpful tone |
| `CUSTOMER_INPUT` | incorrect_cvv, incorrect_otp, invalid_vpa, card_expired, incorrect_card_details | Medium | Link + cause-specific guidance ("card expired — try UPI?") |
| `INSTRUMENT_BLOCKED` | debit_instrument_blocked, card_declined, transaction_on_vpa_restricted, international_transaction_not_allowed | Low on same method | Method switch only; never retry same instrument |
| `MANDATE_ISSUE` | mandate_creation_*, funds_blocked_by_mandate, reqauth_mandate_not_acknowledged, amount > ₹15k AFA | Needs customer | Re-authentication link; **no auto-retry** (RBI) |
| `RISK_DECLINE` | payment_risk_check_failed | — | **Hard stop.** No retry, no nudge. Escalate to risk queue |
| `MERCHANT_CONFIG` | source=business: payment_method_not_enabled, order_amount_mismatch, bank_not_enabled | Merchant must fix | Alert merchant, escalate; **never contact the customer** |
| `ABANDONED_CHECKOUT` | (no payment attempt) | Medium | Reminder + link after 20–60 min, max 2 |
| `OVERDUE_RECEIVABLE` | (invoice past due) | Medium, slow | Reminder ladder soft→firm, promise-to-pay tracking, escalate high-value |
| `UNKNOWN` | anything unmapped | ? | One conservative retry, then escalate |

---

## 5. Action set (closed; the policy engine only ever chooses from this list)

`WAIT(until)` · `RETRY_CHARGE(method)` · `SEND_PAYMENT_LINK(channel, tone, method_hint)` ·
`SEND_REMINDER(channel, tone)` · `OFFER_METHOD_SWITCH(to)` · `REQUEST_REAUTH(link)` ·
`ESCALATE_HUMAN(brief)` · `ALERT_MERCHANT(reason)` · `CLOSE(outcome)`

Channels: `sms`, `whatsapp`, `email`, `voice_stub` — each with a rupee cost and a delivery model.
Tones: `soft` → `helpful` → `firm`. There is no `threatening`.

---

## 6. Bounds and stopping rules (`policy.yaml`, human-readable, tested)

**Time windows (IST)**
- Customer nudges: 10:00–21:00 (TRAI). Receivables chasing: 10:00–19:00 (RBI ∩ TRAI).
- Auto-debit retries: NPCI non-peak windows only (00:00–10:00, 13:00–17:00, 21:30–24:00), and
  never within 24 h of a required pre-debit notification.
- Transient-tech retries: earliest of (downtime resolved, +30 min), max 3 in 24 h.

**Caps**
- Max 3 nudges per case, ≥ 24 h apart (≥ 30 min for the first abandonment nudge).
- Max 5 total actions per case. Max case age: 14 d (A/B/C), 30 d (D).
- Per-customer global cap: 5 messages / 7 days across all cases.

**Hard stops (case closes immediately)**
- Payment succeeded by any path · customer opt-out · dispute/chargeback opened · refund
  requested · `RISK_DECLINE` · `MERCHANT_CONFIG` · subscription cancelled by customer.

**Economic stop**
- `EV = P(recover | class, attempts, age) × amount − cost(action)`; act only if `EV > 0`.
  Amounts < ₹50 get at most one silent retry.

**Escalation triggers**
- Amount ≥ ₹25,000 and 2 failed nudges · promise-to-pay broken twice · reply classified as
  complaint/anger · `UNKNOWN` twice · anything the LLM proposes that policy denies twice.

**Message guardrails** (validator runs on every LLM-written message; failure ⇒ fall back to
template): no threats, no legal/credit-score claims, no false urgency, no PII beyond first name
and amount, must include merchant name, amount, a link, and an opt-out line; ≤ 320 chars for SMS.

---

## 7. Where the LLM is used (and where it is not)

| Task | LLM? | Gate |
|---|---|---|
| Root-cause classification | **No** — deterministic table from Razorpay docs | — |
| Plain-English diagnosis for the audit log | Yes | none needed (text only) |
| Message composition, English + Hinglish, tone-controlled, cause-specific | Yes | validator + template fallback |
| Inbound reply understanding → `{paid_claim, promise_to_pay(date), opt_out, dispute, question, other}` | Yes | opt-out/dispute also matched by regex so a model miss cannot bypass a hard stop |
| Escalation brief for the human queue | Yes | none needed |
| Action selection | **Optional advisor** | policy engine has veto; measured against rules-only |

Provider-agnostic: any OpenAI-compatible endpoint via `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY`.
**With no key set, everything still runs** on templates and rules — the batch metrics are
reproducible by a judge with only a Razorpay test key, or with no keys at all in `sim` mode.

---

## 8. Measurement design (the part that gets us the interview)

**Batch:** 500 synthetic cases, stratified across scenarios A–D and root-cause classes with a
realistic Indian mix (UPI-heavy, business-decline-heavy, small transient-tech tail). Amounts are
log-normal per scenario (₹199 OTT subs to ₹80k B2B invoices). Each case has a hidden
"customer state" (liquidity, intent, channel preference, opt-out propensity).

**Customer behaviour model (`sim/customer.py`):** `P(pay | root cause, intervention, channel,
tone, hours-since-failure, attempt #, day-of-month)` with priors from published dunning numbers
(RESEARCH.md §3). Bank downtime windows and NPCI peak windows exist in the sim clock. The agent
never sees the hidden state — only what Razorpay would expose.

**Policies compared on the identical batch (same seed):**
1. `do_nothing` — baseline leak
2. `naive` — retry 3× immediately + one reminder (what most merchants actually do; also
   demonstrates policy violations: night-time SMS, retry after risk decline)
3. `rules` — Wapsi with deterministic planner, no LLM
4. `agent` — Wapsi with LLM advisor + LLM messaging

**Reported per policy:** cases, recovered, recovery rate, ₹ at risk, ₹ recovered, ₹ spent on
interventions, **net ₹**, contacts per recovery, median hours-to-recovery, escalations, hard-stop
count, **policy violations** (agent must be 0), per-class breakdown, and a "where the agent lost"
table (cases the rules policy recovered and the agent did not).

**Sensitivity:** re-run with all behaviour priors ×0.7 and ×1.3. If the ranking flips, we say so.

**Honesty section in the README:** simulator limitations, what the priors are and are not,
what we could not verify in test mode, and the false-nudge cost (customers we contacted who
would have paid anyway).

---

## 9. Live mode (real Razorpay test-mode APIs, for the video)

`wapsi live seed` creates real customers, orders, payment links, an invoice, a plan and a
subscription in the merchant's test account. Failures are triggered the only way test mode
allows — through checkout with `failure@razorpay` / the test-card Failure button, or
dashboard "Charge this now → Failure". Wapsi ingests them (poller by default; webhook
endpoint if a tunnel is available), diagnoses, and creates **real payment links** and
notifications you can see in the Razorpay dashboard. Paying a link with `success@razorpay`
closes the case as recovered. Same agent code as `sim`; only the adapter differs.

Demo money shot: Razorpay dashboard on the left, Wapsi audit trail on the right, one case
going failed → diagnosed → link sent → paid → closed, with every step's reasoning.

---

## 10. Architecture (modules)

```
wapsi/
  core/      models.py  taxonomy.py  policy.py  planner.py  executor.py  audit.py  metrics.py
  adapters/  razorpay_live.py  razorpay_fake.py  messaging.py  llm.py  humanqueue.py
  sim/       generator.py  customer.py  world.py  runner.py  config.yaml
  policies/  do_nothing.py  naive.py  (rules & agent are core.planner modes)
  api/       app.py  (FastAPI: /webhooks/razorpay, /cases, /run, /metrics, dashboard)
  cli.py     (typer: simulate | live seed | live watch | report)
policy.yaml  tests/  docs/  results/
```

Stack: Python 3.12, Pydantic, FastAPI + Uvicorn, SQLite (SQLModel), Typer, Rich, `razorpay`
SDK, `openai` client (pointed at any compatible endpoint), pytest. Dashboard: a single static
HTML page served by FastAPI (no frontend toolchain). One `pip install -e .` and it runs.

---

## 11. Session plan (≈ 6 windows)

| Session | Deliverable | Done when |
|---|---|---|
| 1 (now) | Research, design, repo scaffold, models, taxonomy, policy engine + tests | `pytest` green on policy rules |
| 2 | Sim world, customer model, generator, batch runner, baselines, metrics | `wapsi simulate --n 500` prints a comparison table |
| 3 | LLM adapter + validator, messaging, reply parsing, agent mode, audit log | agent ≥ rules on the batch; violations = 0 |
| 4 | Live adapter on Razorpay test keys, poller, seed script, dashboard | one real case recovered end-to-end in test mode |
| 5 | README, ARCHITECTURE.md with diagram, BUILD_LOG, results/, sensitivity, cleanup | judge can clone → run → see numbers in < 5 min |
| 6 | Video script + recording, final push, form answers | submitted |

Cut list if time runs short (in order): sensitivity analysis → LLM advisor mode (keep rules) →
webhook endpoint (keep poller) → dashboard (keep CLI report).

---

## 12. Five-minute video storyboard

0:00 the leak (30 s) · 0:30 what Razorpay already does and why cause-blind retries fail (30 s) ·
1:00 architecture on one slide (45 s) · 1:45 live demo: real test-mode failure → diagnosis →
bounded action → recovered, audit trail on screen (90 s) · 3:15 the batch numbers: four policies,
net ₹, violations, per-cause, sensitivity (60 s) · 4:15 what broke and how we got out (30 s) ·
4:45 what we'd build next at Razorpay (15 s).

---

## 13. Risks

- Test mode cannot inject failures programmatically → live demo is semi-manual (accepted; sim
  carries the metrics).
- No tunnel on this machine → poller is the default ingestion path; webhooks are optional.
- Simulator can be accused of being tuned → same seed for all policies, priors cited, sensitivity
  run, "where the agent lost" table, false-nudge cost reported.
- LLM provider outage during demo → template fallback is always on.

## 14. Decisions (resolved 2026-09-04)

1. Project name: **Wapsi**. Repo: https://github.com/Kartik962005/Razorpay-Wapsi-
2. LLM provider: **Groq** via the OpenAI-compatible endpoint (`LLM_BASE_URL=https://api.groq.com/openai/v1`); model ids only in `.env`.
3. Dashboard: minimal static HTML page served by FastAPI.
4. Messaging: English + Hinglish.
5. Ingestion: poller by default; webhook endpoint optional via a cloudflared quick tunnel.

The executable plan is `docs/BUILD_SPEC.md`.
