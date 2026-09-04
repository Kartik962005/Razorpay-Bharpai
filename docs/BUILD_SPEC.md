# Wapsi — build specification

This is the executable plan. Read `DESIGN.md` for the *why*; this file is the *what and how*,
precise enough to build from top to bottom without re-deriving decisions. When something here
conflicts with reality (an API behaves differently, a rate limit bites), fix it, note it in
`BUILD_LOG.md`, and keep going.

## 0. Ground rules

- Repo: `https://github.com/Kartik962005/Razorpay-Wapsi-` (branch `main`, solo).
- The language model inside the product is any OpenAI-compatible endpoint. The model id lives
  only in `.env`, never in code, because model catalogues rotate (see BUILD_LOG).
- Secrets only in `.env` (gitignored). `.env.example` has placeholders only. Before every push:
  `git diff --cached --name-only | grep -x .env` must print nothing.
- Commit small and often, imperative mood ("Add policy engine", "Fix NPCI window check").
  Push at the end of every session.
- Windows quirks: the venv interpreter is `.venv/Scripts/python.exe`; Git Bash heredocs corrupt
  files past ~8 KB and eat backslash escapes, so write source files with an editor rather than
  the shell; use `zoneinfo.ZoneInfo("Asia/Kolkata")` explicitly — never rely on the `TZ` env var,
  which Windows ignores.
- Amounts are integers in **paise** everywhere (Razorpay convention); format as ₹ only at the edges.
- Every decision the system makes must be reconstructible from the audit log alone.

## 1. Repository layout

```
Razorpay-Wapsi-/
  README.md                     # pitch, results table, run-in-3-commands, honesty section
  pyproject.toml                # package `wapsi`, console script `wapsi = wapsi.cli:app`
  policy.yaml                   # every bound and stopping rule, with rule ids
  .env.example  .gitignore
  wapsi/
    __init__.py
    config.py                   # pydantic-settings: env, paths, IST tz
    core/
      models.py                 # enums + Case, Action, AuditEntry, Message, Reply, Ticket
      taxonomy.py               # reason/source/step -> RootCause; priors
      policy.py                 # PolicyEngine: hard stops, windows, caps, EV, escalation; rule ids
      planner.py                # RulesPlanner (EV) and AgentPlanner (LLM advisor, policy veto)
      executor.py               # runs actions through adapters; idempotency pre-check
      audit.py                  # append-only JSONL writer + reader
      metrics.py                # batch metrics, per-class tables, violations, "where agent lost"
      validator.py              # message guardrails (R40)
    adapters/
      gateway.py                # RazorpayGateway Protocol (interface)
      razorpay_live.py          # real SDK calls, test mode
      razorpay_fake.py          # in-memory, driven by sim world
      messaging.py              # Messenger stub: channels, costs, delivery log
      humanqueue.py             # escalation tickets (JSONL)
      llm.py                    # OpenAI-compatible client, JSON mode, cache, budget, fallback
      templates.py              # deterministic message templates (en / hinglish), used as fallback
    sim/
      config.yaml               # mixes, amounts, behaviour priors (cited), costs
      world.py                  # clock, downtime episodes, NPCI peak, salary days, RNG streams
      customer.py               # hidden state + behaviour model + reply generation
      generator.py              # stratified 500-case batch
      runner.py                 # runs a policy over the batch; produces audit + outcomes
      baselines.py              # DoNothingPlanner, NaivePlanner
    api/
      app.py                    # FastAPI: webhook, cases, metrics, simulate, dashboard
      static/index.html         # single-page dashboard (vanilla JS, no build step)
    live/
      seed.py                   # creates test-mode entities, writes live_state.json
      poller.py                 # polls Razorpay APIs, emits events
      webhook.py                # signature verify + dedupe + normalise -> events
    cli.py                      # typer app
  tests/                        # pytest, see §13
  docs/                         # DESIGN, RESEARCH, BUILD_LOG, BUILD_SPEC, ARCHITECTURE
  results/                      # report.md, summary.json, audit_<policy>.jsonl, sensitivity.md
```

## 2. Data model (`core/models.py`, pydantic v2)

```python
class Scenario(str, Enum):        A="payment_failed"; B="checkout_abandoned"; C="subscription_failed"; D="overdue_receivable"
class Method(str, Enum):          upi, card, netbanking, wallet, emandate, upi_autopay
class RootCause(str, Enum):       TRANSIENT_TECH, INSUFFICIENT_FUNDS, LIMIT_EXCEEDED, CUSTOMER_ABANDON,
                                  CUSTOMER_INPUT, INSTRUMENT_BLOCKED, MANDATE_ISSUE, RISK_DECLINE,
                                  MERCHANT_CONFIG, ABANDONED_CHECKOUT, OVERDUE_RECEIVABLE, UNKNOWN
class ActionType(str, Enum):      WAIT, RETRY_CHARGE, SEND_PAYMENT_LINK, SEND_REMINDER, OFFER_METHOD_SWITCH,
                                  REQUEST_REAUTH, ESCALATE_HUMAN, ALERT_MERCHANT, CLOSE
class Channel(str, Enum):         sms, whatsapp, email, voice_stub
class Tone(str, Enum):            soft, helpful, firm
class Language(str, Enum):        en, hinglish
class CaseStatus(str, Enum):      open, waiting, escalated, closed
class Outcome(str, Enum):         recovered, gave_up, opted_out, disputed, refunded, merchant_issue,
                                  risk_blocked, expired, escalated_unresolved

class ErrorTriple(BaseModel):     code: str|None; source: str|None; step: str|None; reason: str|None; description: str|None

class Case(BaseModel):
    id: str; merchant_id: str; customer_id: str; customer_first_name: str
    scenario: Scenario; method: Method; amount_paise: int; currency: str = "INR"
    error: ErrorTriple | None; root_cause: RootCause | None; diagnosis_text: str | None
    razorpay: dict[str, str]           # order_id, payment_id, payment_link_id, invoice_id, subscription_id
    created_at: datetime; due_at: datetime | None
    status: CaseStatus = open; outcome: Outcome | None
    retries: int = 0; nudges: int = 0; actions: int = 0
    last_contact_at: datetime | None; next_action_at: datetime | None; predebit_notice_at: datetime | None
    recovered_paise: int = 0; cost_paise: int = 0; recovered_at: datetime | None; closed_at: datetime | None
    opted_out: bool = False; disputed: bool = False; refunded: bool = False
    promise_at: datetime | None; promises_broken: int = 0; llm_denials: int = 0
    tags: list[str] = []

class Action(BaseModel):
    id: str; case_id: str; type: ActionType; params: dict; scheduled_at: datetime
    executed_at: datetime | None; result: dict | None; cost_paise: int = 0

class AuditEntry(BaseModel):
    ts: datetime; case_id: str; seq: int
    kind: Literal["observation","diagnosis","proposal","verdict","action","result","reply","outcome","escalation"]
    actor: Literal["system","policy","planner","llm","adapter","customer","human"]
    rule_ids: list[str] = []; summary: str; payload: dict = {}
```

Persistence: in `sim` mode everything is in memory + JSONL audit files. In `live` mode use SQLite
via SQLModel (`wapsi.db`) for cases/actions and the same JSONL audit. Do not over-engineer.

## 3. Taxonomy (`core/taxonomy.py`)

`REASON_TO_CAUSE: dict[str, RootCause]` — exact keys from Razorpay's error list:

| RootCause | reasons |
|---|---|
| TRANSIENT_TECH | bank_not_available, bank_technical_error, gateway_technical_error, issuer_technical_error, upi_app_technical_error, psp_not_available, psp_app_not_available, request_timed_out, payment_declined_due_to_high_traffic, bank_cutoff_in_progress, server_error, invalid_response_from_gateway, vpa_resolution_failed, collect_request_pending, duplicate_rrn_found, verification_failed, payment_pending |
| INSUFFICIENT_FUNDS | insufficient_funds, credit_limit_exceeded |
| LIMIT_EXCEEDED | transaction_daily_limit_exceeded, transaction_limit_exceeded, transaction_frequency_limit_exceeded, transaction_daily_count_exceeded, mcc_amount_limit_exceeded, amount_less_than_minimum_amount |
| CUSTOMER_ABANDON | payment_timed_out, payment_cancelled, otp_expired, payment_session_expired, payment_collect_request_expired, otp_attempts_exceeded, pin_attempts_exceeded |
| CUSTOMER_INPUT | incorrect_cvv, incorrect_otp, incorrect_pin, incorrect_atm_pin, invalid_vpa, card_expired, incorrect_card_details, incorrect_card_expiry_date, incorrect_cardholder_name, card_number_invalid, authentication_failed, invalid_mobile_number, invalid_user_details |
| INSTRUMENT_BLOCKED | debit_instrument_blocked, debit_instrument_inactive, card_declined, debit_declined, payment_declined, authorisation_declined_by_psp, transaction_on_vpa_restricted, international_transaction_not_allowed, user_not_eligible, bank_account_invalid, card_not_enrolled, credit_not_permitted, psp_app_not_supported, user_not_registered_for_netbanking |
| MANDATE_ISSUE | mandate_creation_declined, mandate_creation_expired, mandate_creation_failed, mandate_creation_timeout, funds_blocked_by_mandate, reqauth_mandate_not_acknowledged, upi_autopay_not_supported_on_psp |
| RISK_DECLINE | payment_risk_check_failed, compliance_violation, payment_amount_tampered |
| MERCHANT_CONFIG | payment_method_not_enabled, bank_not_enabled, card_network_not_enabled, invalid_order_id, order_amount_mismatch, order_payment_method_mismatch, order_already_paid, recurring_payment_not_enabled, merchant_not_activated, input_validation_failed, invalid_amount, invalid_currency, invalid_request, live_mode_not_enabled, upi_collect_not_enabled, upi_intent_not_enabled, duplicate_request |

Fallbacks when `reason` is missing/unmapped: `source == "business"` → MERCHANT_CONFIG;
`source in {gateway, network, issuer_bank, beneficiary_bank, customer_psp, internal, razorpay, bank}`
→ TRANSIENT_TECH; `source == "customer" and step == "payment_authentication"` → CUSTOMER_INPUT;
else UNKNOWN. Scenario B → ABANDONED_CHECKOUT, D → OVERDUE_RECEIVABLE regardless of error.
Special: scenario C with `amount_paise > 1_500_000` (₹15,000) → tag `afa_required`.

`classify(case) -> (RootCause, explanation_template)`; `PRIORS[RootCause][ActionType] -> float`
are the **planner's beliefs** (coarse, from published numbers) and are deliberately different from
the simulator's hidden truth tables:

| RootCause | RETRY_CHARGE | SEND_PAYMENT_LINK | OFFER_METHOD_SWITCH | REQUEST_REAUTH | SEND_REMINDER |
|---|---|---|---|---|---|
| TRANSIENT_TECH | 0.75 | 0.35 | 0.30 | – | – |
| INSUFFICIENT_FUNDS | 0.10 now / 0.35 at 24–72 h / 0.50 in salary window (1st–7th) | 0.30 | 0.25 | – | – |
| LIMIT_EXCEEDED | 0.65 next day | 0.30 | 0.55 | – | – |
| CUSTOMER_ABANDON | – | 0.30 (≤ 1 h) / 0.15 later | 0.10 | – | – |
| CUSTOMER_INPUT | – | 0.40 | 0.35 | – | – |
| INSTRUMENT_BLOCKED | 0.02 (denied anyway) | 0.20 | 0.45 | – | – |
| MANDATE_ISSUE | – | 0.15 | 0.20 | 0.40 | – |
| ABANDONED_CHECKOUT | – | 0.20 first / 0.08 second | – | – | – |
| OVERDUE_RECEIVABLE | – | 0.10 | – | – | 0.25 soft / 0.20 helpful / 0.20 firm |
| UNKNOWN | 0.30 | 0.20 | 0.15 | – | – |

Decay: multiply by 0.6 per previous failed attempt of the same action type on the case, and by
0.9 per day of case age.

## 4. Policy (`policy.yaml` + `core/policy.py`)

`policy.yaml` is the single source of truth; the engine loads it, tests read it, README renders it.

```yaml
timezone: Asia/Kolkata
windows:
  customer_messaging: {start: "10:00", end: "21:00"}        # R10  TRAI
  receivables_messaging: {start: "10:00", end: "19:00"}     # R11  RBI FPC ∩ TRAI
  auto_debit: [["00:00","10:00"],["13:00","17:00"],["21:30","24:00"]]   # R12 NPCI non-peak
  predebit_notice_hours: 24                                 # R13 RBI e-mandate
afa_threshold_paise: 1500000                                # R14
transient_retry: {min_gap_minutes: 30, max_per_24h: 3, wait_for_downtime_resolved: true}   # R16
caps:
  nudges_per_case: 3            # R20
  nudge_gap_hours: 24           # R20
  first_abandon_nudge_delay_minutes: 30
  actions_per_case: 5           # R21
  max_age_days: {A: 14, B: 14, C: 14, D: 30}   # R22
  customer_messages_per_7d: 5   # R23
economics:                      # R24
  min_amount_for_nudge_paise: 5000
  channel_cost_paise: {sms: 20, whatsapp: 80, email: 2, voice_stub: 300}
  retry_cost_paise: 5
  human_escalation_cost_paise: 5000
escalation:
  high_value_paise: 2500000     # R30  ≥ ₹25,000 with ≥2 failed nudges
  promises_broken: 2            # R31
  on_complaint: true            # R32
  unknown_after_failed_retries: 1   # R33
  llm_denials: 2                # R34
promise_grace_days: 1           # R41
validator:                      # R40
  banned_patterns: ["legal action","police","court","fir ","credit score","cibil","blacklist","last warning","or else","consequences","fraud"]
  required: ["merchant_name","amount","link","opt_out_line"]
  max_chars: {sms: 320, whatsapp: 600, email: 2000}
```

Engine API:

```python
class Denial(NamedTuple): action: ActionType; rule_id: str; reason: str; earliest_at: datetime | None
class PolicyEngine:
    def hard_stops(case, ctx) -> list[tuple[Outcome, rule_id]]          # R01–R07
    def escalation_triggers(case, ctx) -> list[rule_id]                  # R30–R34
    def check(action: Action, case, now, ctx) -> Denial | None           # R10–R24, R41
    def allowed(case, now, ctx) -> tuple[list[Action], list[Denial]]     # enumerates candidates from taxonomy defaults
    def violations(audit_entries) -> list[Violation]                     # re-checks any policy's log, used to score baselines
```

`ctx` provides: `now`, `downtime_active(method, bank)`, `customer_messages_last_7d(customer_id)`,
`payment_status(case)` (refetch), `is_salary_window(now)`.

Hard-stop rule ids: R01 already paid (always refetch first) · R02 opted out · R03 dispute ·
R04 refund · R05 RISK_DECLINE (→ escalate to risk, no retry, no contact) · R06 MERCHANT_CONFIG
(→ ALERT_MERCHANT, never contact customer) · R07 subscription cancelled by customer.
R15: INSTRUMENT_BLOCKED forbids RETRY_CHARGE on the same instrument.

## 5. Planners (`core/planner.py`, `sim/baselines.py`)

**RulesPlanner.plan(case, now, ctx) -> Action**
1. hard stops → `CLOSE(outcome)` or `ESCALATE_HUMAN` (R05) / `ALERT_MERCHANT` (R06).
2. escalation triggers → `ESCALATE_HUMAN(brief)`.
3. `allowed, denied = policy.allowed(case, now, ctx)`.
4. score each allowed action: `EV = prior(case, action) × amount − cost(action)`; pick max if `EV > 0`.
5. else if any denial has `earliest_at` (window/gap rules) → `WAIT(until=min earliest_at)`.
6. else `CLOSE(gave_up)` with the denial reasons in the audit entry.
Defaults for channel/tone/language: sms for A/B/C, email+whatsapp for D; tone ladder by nudge
count (soft → helpful → firm); language from customer preference if known else hinglish for
amounts < ₹5,000, en otherwise.

**AgentPlanner** = RulesPlanner steps 1–3, then `llm.advise_action(case_summary, allowed_with_EV,
denied_with_reasons)` returns `{action_id, channel?, tone?, language?, schedule_hint?, rationale}`.
Validate: `action_id ∈ allowed`; overrides must pass `policy.check` again. If invalid → log
`verdict: denied (R34 counter++)` and use the rules choice. Log proposal, verdict, final. The LLM
can never widen the action set — only pick within it and shape the message.

**DoNothingPlanner**: never acts, except scenario C where the platform's own T+1/T+2/T+3 retry
ladder runs (that is what happens without Wapsi; say so in the README).
**NaivePlanner** (what most merchants do): on failure retry the same instrument 3× at +0, +5 min,
+15 min regardless of cause or window; then one SMS reminder at +1 h regardless of hour; for D,
an email every 3 days forever. No stops. Its audit log is scored by `policy.violations()`.

## 6. Executor and adapters

`Executor.run(action, case, ctx)`:
1. `status = gateway.refresh(case)`; if paid → record R01, close recovered, return (idempotency).
2. dispatch by type:
   - RETRY_CHARGE → `gateway.retry_charge(case)` (fake: sim decides; live: for subscriptions log
     the intent and create a recovery link because test mode cannot charge programmatically —
     document this honestly).
   - SEND_PAYMENT_LINK / OFFER_METHOD_SWITCH / REQUEST_REAUTH → `gateway.create_payment_link(...)`
     with `notes={"wapsi_case_id": id, "root_cause": ...}`, `reminder_enable=False` (Wapsi owns
     reminders), `expire_by = now + 3 days`; then `messenger.send(channel, text, cost)`.
   - SEND_REMINDER → live: `notify_by/{sms|email}` on the invoice/link; fake: messenger.
   - ESCALATE_HUMAN → `humanqueue.create(case, brief)`; case → escalated.
   - ALERT_MERCHANT → humanqueue with `kind=merchant_alert`.
   - WAIT → set `next_action_at`. CLOSE → set outcome.
3. append audit `action` + `result`; add cost.

`RazorpayGateway` Protocol: `refresh(case)`, `create_payment_link(amount, customer, description,
notes, expire_by, method_hint) -> {id, short_url}`, `notify(entity, medium)`, `fetch_subscription(id)`,
`fetch_invoice(id)`, `list_failed_payments(since)`, `list_link_states(ids)`, `downtime_active(method, bank)`.
`razorpay_live.py` wraps the `razorpay` SDK with `enable_retry(True)`; every call logged.
`razorpay_fake.py` is backed by the sim world.

`Messenger.send(channel, to, text, lang, case_id) -> DeliveryReceipt` — stub with per-channel
cost from policy.yaml, delivery log JSONL, and (in sim) a hook so the customer model can react.

## 7. LLM adapter (`adapters/llm.py`) — Groq via OpenAI-compatible API

- Client: `openai.OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)`. Models on this account
  (verified 2026-09-04 via `GET /models`): `LLM_MODEL=openai/gpt-oss-120b` for `advise_action`
  and `write_brief`; `LLM_MODEL_FAST=openai/gpt-oss-20b` for `compose_message`, `parse_reply`,
  `explain_diagnosis`. Both are reasoning models: always pass `max_tokens >= 300` and
  `extra_body={"reasoning_effort": "low"}`, otherwise the reply content comes back empty. JSON
  mode (`response_format={"type": "json_object"}`) is confirmed working. Llama 3.x ids are gone
  from the catalogue; never hard-code a model id outside `.env`.
- Free-tier limits are real (roughly 30 req/min; the 70B model has a low daily cap, the 8B model a
  high one). Therefore: `LLM_MAX_CALLS` budget per run (default 600), a semaphore of 4 concurrent
  calls, exponential backoff on 429 (1, 2, 4, 8 s, then give up → fallback), an on-disk cache in
  `.cache/llm.sqlite` keyed by `sha256(model + system + user)`, and `--advisor-sample N` so the
  advisor runs on N random cases while the rest use rules (report says how many).
- JSON mode: `response_format={"type": "json_object"}`; parse with pydantic; on failure one repair
  call ("return only valid JSON matching …"); on second failure → fallback and log.
- No key → `LLM.enabled = False`; every function returns the template/regex result. The batch
  and the live demo must both run keyless.
- Functions and output schemas:
  - `compose_message(ctx) -> {text}`; ctx = merchant_name, first_name, amount_inr, scenario,
    root_cause, guidance (from taxonomy), link, channel, tone, language, char_limit, opt_out_line.
    Then `validator.check(text, ctx)`; on failure use `templates.render(ctx)`.
  - `parse_reply(text) -> {intent: paid_claim|promise_to_pay|opt_out|dispute|question|complaint|other, promise_date: str|null, confidence: float}`.
    Always OR with regex: `\b(stop|unsubscribe|band karo|mat bhejo)\b` → opt_out; `\b(dispute|chargeback|fraud|complaint)\b` → dispute/complaint.
  - `explain_diagnosis(case) -> {explanation}` (2 sentences, plain English, for the audit log).
  - `write_brief(case, history) -> {brief}` (≤ 120 words for a human agent: what happened, what we tried, what we recommend).
  - `advise_action(case_summary, allowed, denied) -> {action_id, channel, tone, language, rationale}`.
- Prompts live in `adapters/prompts.py` as plain strings; system prompt for messages states the
  guardrails verbatim (no threats, no legal claims, include amount + link + opt-out, length).
- Report per run: LLM calls made, cache hits, fallbacks, budget exhausted (bool).

## 8. Simulator (`sim/`)

**Clock**: starts `2026-08-03 09:00 IST` (a Monday); horizon 30 days; tick = 15 min. Cases arrive
uniformly over the first 10 days (so multi-day sequences are observed). Event loop: for each tick,
process due customer responses, then for each open case with `next_action_at <= now` call the
planner and execute.

**World** (`world.py`): downtime episodes — Poisson ~4/week, 20–90 min, on a random (method, bank)
pair from a small list (HDFC, SBI, ICICI, Axis, PhonePe, GPay, Paytm); `downtime_active()` mirrors
Razorpay's `payment.downtime.*` events. NPCI peak 10:00–13:00: auto-debits attempted inside fail
with p = 0.6 (root cause TRANSIENT_TECH). Salary window: days 1–7. **Common random numbers**: one
`random.Random(hash((seed, case_id, purpose)))` per (case, purpose) so every policy sees the same
customer draws where the decision path is the same.

**Generator** (`generator.py`), n = 500, seed = 42, stratified:

| Scenario | share | root-cause mix | methods | amount (paise) |
|---|---|---|---|---|
| A payment_failed | 40 % | TRANSIENT 15, INSUFF 22, LIMIT 8, ABANDON 22, INPUT 15, BLOCKED 8, RISK 3, MERCHANT 3, UNKNOWN 4 | upi 65, card 25, netbanking 7, wallet 3 | lognormal median 89 900, σ 0.9, clip [4 900, 4 999 900] |
| B checkout_abandoned | 20 % | ABANDONED_CHECKOUT 100 | upi 70, card 30 (intended) | lognormal median 129 900, σ 0.8 |
| C subscription_failed | 25 % | INSUFF 45, TRANSIENT 15, MANDATE 20, BLOCKED 10, LIMIT 5, UNKNOWN 5 | upi_autopay 60, card 30, emandate 10 | choice {19 900, 29 900, 49 900, 99 900, 149 900, 499 900, 1 800 000} |
| D overdue_receivable | 15 % | OVERDUE_RECEIVABLE 100 | — | lognormal median 1 800 000, clip [200 000, 25 000 000]; due 0–10 days ago |

Each case gets a realistic `ErrorTriple` (reason from its class; source/step consistent with the
method) so the taxonomy is exercised for real.

**Hidden customer state** (`customer.py`): `liquidity ∈ {tight .35, ok .45, good .20}`,
`intent ~ Beta(4,2)`, `channel_pref`, `language_pref (hinglish .55, en .45)`,
`opt_out_threshold ∈ {2 (10 %), 4 (30 %), 7 (60 %)}` contacts, `annoyance = 0`,
`promise_reliability ~ Beta(3,2)`, `organic_recovery` (whether/when they would have paid with no
intervention — drawn once, used for the false-nudge metric).

**Behaviour** (`P(pay | action)` = base × modifiers, all in `sim/config.yaml`):
- TRANSIENT_TECH: retry during active downtime → 0; after → 0.80. Link → 0.35.
- INSUFFICIENT_FUNDS: retry p by hours since failure: <12 h 0.08, 12–72 h 0.25, salary window 0.45;
  liquidity tight ×0.5, good ×1.4. Link/method-switch 0.20 (+0.1 if tight & UPI offered).
- LIMIT_EXCEEDED: same-day retry 0.05; next day 0.65; method switch 0.55.
- CUSTOMER_ABANDON: link ≤ 1 h 0.32; 1–24 h 0.15; later 0.06. ×intent.
- CUSTOMER_INPUT: link with guidance 0.40; without 0.25.
- INSTRUMENT_BLOCKED: same-instrument retry 0.01 (+annoyance); method switch 0.45.
- MANDATE_ISSUE: reauth link 0.40; anything else 0.05. AFA cases: retry always fails.
- RISK_DECLINE: any retry → 0; each retry adds dispute p 0.15 (punishes naive).
- MERCHANT_CONFIG: nothing works until ALERT_MERCHANT; merchant fixes after 4–48 h; then retry 0.85.
- ABANDONED_CHECKOUT: first link 0.20, second 0.08, third 0.02. ×intent. Organic 0.12 within 24 h.
- OVERDUE_RECEIVABLE: reminder → {pay 0.20, promise 0.35, silence 0.40, other 0.05}; promise kept
  with `promise_reliability`; escalated cases resolve via human with p 0.5 after 2 days (count as
  `recovered_via_human`, reported separately, and cost ₹50).
- Modifiers: channel_pref match ×1.2; language match ×1.15; tone firm ×0.9 pay but ×2 annoyance;
  attempt decay ×0.6; any contact outside 08:00–22:00 ×0.5 pay, ×3 annoyance, +2 % dispute.
- Opt-out when contacts ≥ threshold; complaint reply when annoyance ≥ 5; dispute when annoyance ≥ 8.
- Response latency after a nudge: lognormal median 2 h, only during 08:00–23:00.
- Replies are short template strings (Hinglish/English) so `parse_reply` is exercised:
  "paid kar diya", "will pay Friday", "STOP", "kaunsa order?", "stop messaging me, calling my bank".

**Runner** (`runner.py`): `run(policy_name, batch, seed, llm=None) -> RunResult` with the audit
file, outcomes, costs, violations, LLM stats, wall time. Deterministic given seed + policy (LLM
mode is deterministic only with cache warm; say so).

## 9. Metrics and report (`core/metrics.py`)

`results/report.md` (also printed by the CLI with `rich`):

Table 1 — policies × {cases, recovered, recovery %, ₹ at risk, ₹ recovered, ₹ cost, **₹ net**,
incremental ₹ vs do_nothing, contacts per recovery, median hours to recovery, escalations,
recovered via human, hard stops, **policy violations**, false nudges (contacted but would have paid
organically), LLM calls / cache hits}.
Table 2 — recovery % by root cause × policy.
Table 3 — "where the agent lost": cases recovered by `rules` but not by `agent` (and vice versa),
with the differing decision.
Table 4 — sensitivity: priors ×0.7 / ×1.0 / ×1.3 → net ₹ per policy; flag if ranking changes.
`results/summary.json` with the same numbers for the README and dashboard.

## 10. CLI (`cli.py`, typer)

```
wapsi simulate --n 500 --seed 42 --policies do_nothing,naive,rules,agent --advisor-sample 150 --out results/
wapsi sensitivity --factors 0.7,1.0,1.3
wapsi report                                  # re-render results/report.md from summary.json
wapsi serve --port 8000                       # API + dashboard (+ webhook endpoint)
wapsi live seed                               # create test-mode entities, write live_state.json
wapsi live watch --poll 10                    # poll Razorpay, run the agent on real cases
wapsi case <id>                               # print one case's audit timeline
wapsi live doctor                             # keys OK? models OK? GET /v1/webhooks -> url/events; tunnel reachable?
```

`scripts/` already holds the pieces `live doctor` grows from: `check_keys.py` (Razorpay auth +
LLM models), `webhook_probe.py` (minimal receiver with signature check), `webhook_trigger.py`
(create + cancel a ₹10 payment link to fire `payment_link.cancelled`), `set_secrets.py`
(interactive, hidden-input editor for the three secrets in `.env`).

## 11. API and dashboard (`api/`)

Endpoints: `GET /` (dashboard), `GET /api/metrics`, `GET /api/cases?status=&scenario=`,
`GET /api/cases/{id}` (case + audit timeline), `POST /api/simulate` (runs a small batch, n ≤ 100,
returns the table), `POST /webhooks/razorpay`, `GET /health`.
Dashboard: one `index.html`, vanilla JS, polls every 3 s: KPI cards (open, recovered ₹, cost ₹,
net ₹, escalations, violations), cases table, click → drawer with the audit timeline (each entry
shows actor, rule ids, summary). Light/dark via `prefers-color-scheme`. No frameworks.

## 12. Live mode and webhooks (`live/`)

**Seed** (`wapsi live seed`): 3 customers (`+91 99999 0000{1,2,3}`, test emails), 3 payment links
(₹499, ₹1,299, ₹2,499 — payment links are the vehicle for scenario A because test-mode failures
can only be produced through checkout), 1 invoice ₹15,000 due yesterday (scenario D), 1 plan ₹299
monthly + 1 subscription (scenario C; authorise it once via its `short_url` with test card
`4718 6091 0820 4366`, then use dashboard "Charge this now → Failure"). Write `live_state.json`.

**Trigger a failure** (by hand, on camera): open a link, choose UPI, enter `failure@razorpay`.

**Poller** (`wapsi live watch`): every N seconds — `payment.all(from=last_ts)` filtered
`status=failed` → new case (scenario A, error triple from the payment entity);
`payment_link.fetch` / `invoice.fetch` / `subscription.fetch` for the seeded ids → state changes
(paid → close recovered; `subscription.status in {pending, halted}` → scenario C case). Then run
the planner with the real clock and the live gateway. Recovery links it creates carry
`notes.wapsi_case_id`; the console prints the `short_url` so the demo can pay it with
`success@razorpay` → next poll sees `paid` → case closes `recovered`.

**Webhook endpoint** (`POST /webhooks/razorpay`, with `POST /` as an alias because a pasted URL
can lose its path): read raw body; verify
`X-Razorpay-Signature` with `razorpay.Utility().verify_webhook_signature(body, sig, RAZORPAY_WEBHOOK_SECRET)`;
dedupe on the `x-razorpay-event-id` header (store seen ids); normalise
`payment.failed`, `payment.captured`, `order.paid`, `payment_link.paid`, `payment_link.expired`,
`invoice.paid`, `invoice.expired`, `subscription.pending`, `subscription.halted`,
`subscription.charged`, `payment.dispute.created`, `refund.created`, `payment.downtime.*`
into the same event objects the poller emits. Return 200 fast; process async.

**Exposing the endpoint** (needed only for webhooks; the poller needs nothing):
1. `winget install Cloudflare.cloudflared` (no account needed for a quick tunnel).
2. `wapsi serve --port 8000`, then `cloudflared tunnel --url http://localhost:8000` → copy the
   `https://<random>.trycloudflare.com` URL.
3. Razorpay Dashboard (test mode toggle ON) → Account & Settings → Webhooks → Add New Webhook:
   URL `https://<random>.trycloudflare.com/webhooks/razorpay`, a secret of your choosing, alert
   email, tick the events listed above → Create.
4. Put the same secret in `.env` as `RAZORPAY_WEBHOOK_SECRET`; restart `wapsi serve`.
5. Test: make a failed payment; the dashboard's webhook page shows delivery status; `wapsi serve`
   logs the event. Note: quick-tunnel URLs change every run — update the webhook URL each time.

Account facts (2026-09-04): Subscriptions is activated; the test-mode webhook is created with
`RAZORPAY_WEBHOOK_SECRET` set in `.env` and these 26 events subscribed: `payment.authorized`,
`payment.failed`, `payment.captured`, `payment.dispute.created`, `payment.downtime.started/
updated/resolved`, `order.paid`, `invoice.paid/partially_paid/expired`,
`subscription.authenticated/activated/pending/halted/charged/cancelled/resumed/paused`,
`refund.created`, `payment_link.paid/partially_paid/expired/cancelled`. The tunnel is a
cloudflared quick tunnel (`cloudflared tunnel --url http://localhost:8000`); its hostname changes
on restart, so re-edit the webhook URL before the demo. The poller remains the default path and
must work with webhooks disabled.

## 13. Tests (`tests/`, pytest)

- `test_taxonomy.py`: every reason in the mapping resolves; unmapped + source=business →
  MERCHANT_CONFIG; unmapped gateway → TRANSIENT_TECH; unmapped → UNKNOWN; C > ₹15k → `afa_required`.
- `test_policy_windows.py`: nudge at 21:30 IST denied R10 with `earliest_at` = next 10:00; D
  nudge at 19:30 denied R11; auto-debit at 10:30 denied R12; at 13:30 allowed; auto-debit without
  24 h notice denied R13.
- `test_hard_stops.py`: paid / opted out / dispute / refund / RISK / MERCHANT_CONFIG / cancelled.
- `test_caps.py`: 4th nudge denied R20; nudge 6 h after previous denied R20 (gap); 6th action denied
  R21; day-15 case A closed R22; 6th message to same customer in 7 d denied R23.
- `test_economics.py`: ₹30 case gets one retry, no nudge (R24); EV ≤ 0 → no action.
- `test_afa.py`: ₹18,000 recurring → RETRY_CHARGE denied R14, REQUEST_REAUTH allowed.
- `test_planner.py`: rules planner picks max-EV allowed; WAIT when only window-denied; agent
  planner falls back when the LLM proposes a denied action (fake LLM).
- `test_executor.py`: refetch shows paid → no send, R01 logged, outcome recovered.
- `test_validator.py`: threat text rejected; missing opt-out rejected; template passes; length cap.
- `test_sim.py`: same seed → identical summary.json; different seeds → different; 500 cases
  stratified within ±2 %.
- `test_metrics.py`: net = recovered − cost; naive has > 0 violations; rules/agent have 0.
- `test_llm_fallback.py`: no key → all functions return template/regex results; no network.
- `test_webhook.py`: bad signature → 400; good → 200; duplicate event id ignored.

## 14. README skeleton

1. Title + one line. 2. The leak in three sentences. 3. **Results** (Table 1 pasted, with the
sentence "same 500 cases, same seed, four policies"). 4. How it works (ASCII diagram + the loop).
5. Root-cause taxonomy (short table). 6. Bounds and stopping rules (rule ids → plain English →
source). 7. Where the LLM is used and where it is not. 8. Run it: `pip install -e .`,
`wapsi simulate`, `wapsi serve`. 9. Live mode with Razorpay test keys + webhook steps.
10. **Honesty**: simulator limits, priors vs truth separation, what test mode cannot do, false-nudge
cost, where the agent lost. 11. What broke (link to BUILD_LOG). 12. Architecture (link).
13. What we would build next inside Razorpay (downtime-aware retry timing from
`payment.downtime.*`, Magic Checkout hand-off, real WhatsApp via Razorpay's channel).

## 15. Session plan with checkpoints

| # | Build | Checkpoint (commit + push) |
|---|---|---|
| 1 | pyproject, config, models, taxonomy, policy.yaml + engine, validator, templates, tests for all of them | `pytest -q` green (taxonomy/policy/caps/afa/validator) |
| 2 | world, customer, generator, fake gateway, messenger, executor, rules + baselines planners, runner, metrics, CLI simulate | `wapsi simulate --n 500` prints Table 1; naive shows violations, rules shows 0 |
| 3 | llm adapter + prompts + cache + budget, agent planner, reply parsing, audit polish, sensitivity, report.md | agent run completes within Groq limits; Tables 1–4 in results/ |
| 4 | live gateway, seed, poller, webhook, FastAPI, dashboard | one real test-mode case: failed → link → paid → closed, visible in dashboard |
| 5 | README, ARCHITECTURE.md (diagram), BUILD_LOG entries, results committed, cleanup, final test run | fresh clone → `pip install -e .` → `wapsi simulate` works keyless |
| 6 | video (storyboard in DESIGN §12), form answers | submitted before the deadline |

Cut order if behind: sensitivity → agent planner (keep LLM messaging) → webhook (keep poller) →
dashboard (keep CLI). Never cut: tests, Table 1, audit log, README honesty section.

## 16. Form answers (drafts to refine at the end)

**Project name:** Wapsi — cause-aware, bounded revenue recovery for Razorpay merchants.

**What it solves:** Razorpay merchants lose recoverable money to cause-blind retries and
reminders. Wapsi reads Razorpay's own failure signals (`error_reason/source/step`), diagnoses why
each payment, checkout, subscription charge or invoice failed, picks the one intervention that fits
that cause, executes it inside hard RBI/TRAI/NPCI bounds with stopping rules and escalation, and
proves the result on a 500-case batch: ₹ recovered net of cost against do-nothing, naive and
rules-only baselines, with zero policy violations and a full audit trail.

**What broke, and how you got out:** written from `BUILD_LOG.md` at the end — lead with the
most instructive failure, say what the wrong assumption was, what the fix was, and what it cost.

## 17. Video script (5:00)

0:00–0:30 the leak: a failed ₹1,299 UPI payment at 21:40; what most merchants do (retry ×3 at
once, SMS at 22:40 → TRAI violation, annoyed customer). 0:30–1:00 what Razorpay ships today and
why calendar-based isn't cause-based. 1:00–1:45 architecture slide: the loop, the closed action set,
the policy engine with rule ids, where the LLM sits and where it can't reach. 1:45–3:15 live:
dashboard + Razorpay test dashboard side by side; pay a link with `failure@razorpay`; Wapsi shows
diagnosis (`insufficient_funds` → INSUFFICIENT_FUNDS), policy denies immediate retry (R24 EV) and
night SMS (R10), schedules a Hinglish WhatsApp link for 10:00; fast-forward, pay with
`success@razorpay`, case closes recovered, audit timeline scrolls. 3:15–4:15 the numbers: Table 1,
per-cause table, violations (naive N vs 0), false-nudge cost, sensitivity. 4:15–4:45 what broke
(test mode can't fail programmatically; Groq rate limits; whatever else happened) and how we got
out. 4:45–5:00 what this becomes inside Razorpay.
