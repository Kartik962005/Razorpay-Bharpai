# Research notes — Track 3, AI Revenue Recovery

Everything the design depends on, with sources. Numbers here seed the simulator priors
(`sim/config.yaml`) and the policy bounds (`policy.yaml`). If a number is wrong, fix it there
and re-run — nothing is hard-coded.

## 1. Razorpay platform facts

### Error schema on a failed payment
Every failed payment carries `error_code`, `error_description`, `error_source`, `error_step`,
`error_reason`. `source` says *who must act*, `step` says *where in the flow it died*,
`reason` is the specific cause. This is the backbone of our root-cause classifier.

- Source values (by method): `customer`, `business`, `internal`, `gateway`, `issuer_bank`,
  `customer_psp`, `network`, `beneficiary_bank`, `bank`, `issuer`.
- Step values (cards): `payment_initiation` → `card_enrollment_check` → `payment_authentication`
  → `payment_authorization` → `payment_capture`. UPI has a 15-step intent/collect flow
  (`mandate_creation`, `payment_authentication`, `payment_debit_request`, ... `payment_response`).
- Reason values (subset that matters for recovery):
  - transient / bank-side: `bank_not_available`, `bank_technical_error`, `gateway_technical_error`,
    `issuer_technical_error`, `upi_app_technical_error`, `psp_not_available`, `psp_app_not_available`,
    `request_timed_out`, `payment_declined_due_to_high_traffic`, `bank_cutoff_in_progress`,
    `server_error`, `invalid_response_from_gateway`, `vpa_resolution_failed`
  - funds / limits: `insufficient_funds`, `credit_limit_exceeded`, `transaction_daily_limit_exceeded`,
    `transaction_limit_exceeded`, `transaction_frequency_limit_exceeded`,
    `transaction_daily_count_exceeded`, `mcc_amount_limit_exceeded`
  - customer abandoned / timed out: `payment_timed_out`, `payment_cancelled`, `otp_expired`,
    `payment_session_expired`, `payment_collect_request_expired`, `otp_attempts_exceeded`,
    `pin_attempts_exceeded`
  - customer input error: `incorrect_cvv`, `incorrect_otp`, `incorrect_pin`, `invalid_vpa`,
    `card_expired`, `incorrect_card_details`, `incorrect_card_expiry_date`, `authentication_failed`
  - instrument blocked / declined: `debit_instrument_blocked`, `card_declined`, `debit_declined`,
    `payment_declined`, `transaction_on_vpa_restricted`, `international_transaction_not_allowed`,
    `user_not_eligible`, `debit_instrument_inactive`, `bank_account_invalid`
  - mandate: `mandate_creation_declined/expired/failed/timeout`, `funds_blocked_by_mandate`,
    `reqauth_mandate_not_acknowledged`, `upi_autopay_not_supported_on_psp`
  - risk: `payment_risk_check_failed`
  - merchant-side config (source=business): `payment_method_not_enabled`, `bank_not_enabled`,
    `card_network_not_enabled`, `invalid_order_id`, `order_amount_mismatch`, `order_already_paid`,
    `recurring_payment_not_enabled`, `merchant_not_activated`, `input_validation_failed`

Sources: https://razorpay.com/docs/errors/payments/list/ ,
https://razorpay.com/docs/errors/payments/payment-methods-error-parameters/

### Subscriptions: retries and states
- Razorpay's own retry ladder for a failed auto-charge: **T+1, T+2, T+3 days** (cards and UPI).
  After the 4th consecutive failure the subscription goes `pending` → `halted`.
- Emandate retries only after bank confirmation/rejection (can exceed 24h); holiday shifting T-1 / T-3.
- States: created → authenticated → active → (pending → halted) → active again on a successful
  charge or new instrument; cancelled / expired / completed / paused.
- Webhooks: `subscription.pending`, `subscription.halted`, `subscription.charged`,
  `subscription.activated`, `subscription.resumed`, `subscription.cancelled`.
- In `halted`, invoices keep being generated but nothing is auto-charged — this is the revenue leak.
- Recovery levers Razorpay exposes: manual invoice charge (non-domestic cards only), hosted
  "update payment method" link, dashboard filters.
- Test mode: dashboard "Charge this now" → success or failure. 4 failures ⇒ halted. Card tokens
  in test mode are valid for only 3 days.

Sources: https://razorpay.com/docs/payments/subscriptions/payment-retries/ ,
https://razorpay.com/docs/payments/subscriptions/states/ ,
https://razorpay.com/docs/us/payments/subscriptions/test/

### Webhooks we consume
`payment.failed`, `payment.captured`, `order.paid`, `payment_link.paid`, `payment_link.expired`,
`invoice.paid`, `invoice.partially_paid`, `invoice.expired`, `subscription.pending`,
`subscription.halted`, `subscription.charged`, `payment.dispute.created` (hard stop),
`refund.created` (hard stop), `payment.downtime.started/updated/resolved` (retry timing signal).

Source: https://razorpay.com/docs/webhooks/all/

### Test-mode tools
- UPI: `success@razorpay` → success, `failure@razorpay` → failure.
- Cards: `4718 6091 0820 4366` (Visa), `5104 0155 5555 5558` (MC) — mock OTP page has
  Success / Failure buttons.
- Caveat: "In test mode, payment cancellation will result in a successful payment."
- There is **no public API to force a payment failure**; failures come only from checkout UI or
  the dashboard's subscription "Charge this now → failure".

Sources: https://razorpay.com/docs/payments/payments/test-upi-details/ ,
https://razorpay.com/docs/us/payments/subscriptions/test/

### APIs we call (test mode, real)
- Orders: `POST /v1/orders`, `GET /v1/orders/:id/payments`
- Payments: `GET /v1/payments/:id`, `GET /v1/payments`
- Payment Links: `POST /v1/payment_links` (`amount, currency, accept_partial, expire_by,
  reference_id, description, customer{name,email,contact}, notify{sms,email},
  reminder_enable, notes, callback_url, callback_method`), `GET /v1/payment_links/:id`,
  `POST /v1/payment_links/:id/notify_by/:medium`, `POST /v1/payment_links/:id/cancel`
- Invoices: `POST /v1/invoices`, `POST /v1/invoices/:id/issue`,
  `POST /v1/invoices/:id/notify_by/:medium`, `POST /v1/invoices/:id/cancel`
- Subscriptions: `POST /v1/plans`, `POST /v1/subscriptions`, `GET /v1/subscriptions/:id`,
  `POST /v1/subscriptions/:id/resume`
- Customers: `POST /v1/customers`
- Python SDK: `pip install razorpay`; `razorpay.Client(auth=(key, secret))`; webhook
  signature verification utility included.

Sources: https://razorpay.com/docs/api/payments/payment-links/ ,
https://razorpay.com/docs/api/payments/invoices/ , https://github.com/razorpay/razorpay-python

### What Razorpay already ships (so we position against it, not rebuild it)
- **Magic Checkout**: prefilled 1-click checkout, abandoned-cart data capture, WhatsApp payment
  link re-engagement, in-place retry of failed payments.
- **Subscriptions**: fixed T+1/T+2/T+3 retry ladder, failure email with update link.
- **Payment Links / Invoices**: `reminder_enable` sends fixed-cadence reminders.
- **MCP server** (Apr 2025): 50+ tools (`create_payment_link`, `send_payment_link`,
  `fetch_payment`, `create_order`, `create_refund`, settlements, ...).
- **Agent Studio / Agentic Experience Platform** (FTX'26): agent marketplace for payments ops.

The gap we fill: none of these *diagnose why* a payment failed and choose a *different*
intervention per cause with explicit compliance bounds and a measured recovery number.
Their retries are calendar-based, not cause-based.

Sources: https://razorpay.com/blog/abandoned-cart-recovery-solution/ ,
https://razorpay.com/newsroom/razorpay-becomes-indias-first-payment-gateway-to-launch-mcp-server-for-instant-ai-payment-integration/ ,
https://github.com/razorpay/razorpay-mcp-server , https://razorpay.com/agent-studio/

## 2. Regulatory bounds (these become hard rules in `policy.yaml`)

| Rule | Value we enforce | Source |
|---|---|---|
| RBI e-mandate pre-debit notification | ≥ 24 h before any auto-debit retry we schedule | RBI e-mandate framework; NPCI UPI AutoPay guidelines |
| AFA for recurring > ₹15,000 | never auto-retry; send re-authentication link instead | RBI recurring-payment rules |
| NPCI UPI AutoPay execution windows (2026) | auto-debits only 00:00–10:00, 13:00–17:00, 21:30–24:00 IST | Reported May 2026 (Republic World); configurable |
| TRAI promotional / service-explicit SMS window | customer nudges only 10:00–21:00 IST | TRAI TCCCPR 2025 amendment; SE window from 28 Oct 2025 |
| RBI recovery-contact hours (fair practices) | receivables chasing only 08:00–19:00 IST, so nudges use the intersection **10:00–19:00** | RBI FPC / outsourcing guidelines |
| No harassment / threats / misrepresentation | tone ladder capped at "firm"; message validator rejects threats, legal claims, fake urgency | RBI FPC |
| Customer opt-out | "STOP"/opt-out reply ⇒ permanent hard stop for that customer | TRAI / RBI |
| Dispute or refund opened | hard stop, close case, escalate | common sense + Razorpay dispute webhooks |

Sources: https://upstox.com/learning-center/personal-finance/rbi-s-new-e-mandate-rules/article-1569/ ,
https://www.slickerhq.com/resources/blog/rbi-approval-required-india-payment-declines ,
https://www.republicworld.com/business/upi-autopay-failure-morning-peak-hours-npci-new-rules-2026 ,
https://www.trai.gov.in/sites/default/files/2025-02/Regulation_12022025.pdf ,
https://www.textguru.in/blog/service-explicit-message-delivery-trai-update ,
https://www.credsettle.com/rbi-guidelines-calling-after-7pm

## 3. Industry numbers (simulator priors — cited, then stress-tested with ±30% sensitivity)

| Quantity | Prior | Source |
|---|---|---|
| UPI technical decline (bank/NPCI side) | ~0.7–0.8 % of attempts, target < 1 % | NPCI BD/TD page via productgrowth.in |
| UPI business decline (user side: PIN, balance) | target < 5 %; merchant blended success 92–96 % | productgrowth.in |
| Share of subscription churn that is involuntary | 20–40 % | Baremetrics |
| Naive fixed-schedule dunning recovery | 15–30 % | Slicker, Yuno, finsi |
| Smart (cause- and time-aware) retry recovery | 45–70 % | Solidgate, Slicker, Stripe |
| Reminder within 24 h vs after 30 days | 41 % vs 27 % open rate | digitalapplied |
| Multi-channel vs email-only dunning | up to 34 % less involuntary churn | Baremetrics |
| Stripe Smart Retries | $9 recovered per $1 spent on Billing; best retry time is often days later, keyed on decline code | Stripe engineering blog |

Sources: https://productgrowth.in/insights/fintech/upi-payment-success-rates/ ,
https://baremetrics.com/blog/involuntary-churn , https://www.slickerhq.com/blog/smart-payment-retries-vs-dunning-which-recovers-more-in-2025 ,
https://solidgate.com/blog/smart-retries-for-revenue-recovery/ , https://stripe.com/blog/how-we-built-it-smart-retries ,
https://www.digitalapplied.com/blog/failed-payment-recovery-dunning-playbook-2026

## 4. Buildathon facts
- Applications close **5 September 2026**. Students only. In-person Bangalore, ₹75k/month, 6 or 12 months.
- Submission: public repo, 5-min pitch video (unlisted ok), architecture, "what broke and how you got out".
- Track 3 bar (verbatim): *"Don't just identify the problem. Show measured money recovered across a
  batch, with compliant escalation, stopping rules, and an audit trail."*
- Selection: "if it has signal, we call you in" → panel interview, no aptitude test.

Sources: https://razorpay.com/buildathon/ ,
https://velonx.in/blog/razorpay-ai-buildathon-2026-tracks-eligibility-stipend-selection-process
