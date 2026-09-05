"""Diagnosis: Razorpay's failure vocabulary mapped to recoverable root causes.

This is deliberately a lookup table, not a model. The reasons come from Razorpay's published
error list, so guessing adds nothing and hides mistakes. Anything unmapped becomes ``UNKNOWN``
and is handled conservatively rather than optimistically.

Sources:
  https://razorpay.com/docs/errors/payments/list/
  https://razorpay.com/docs/errors/payments/payment-methods-error-parameters/
"""

from __future__ import annotations

from datetime import datetime, timedelta

from bharpai.core.models import ActionType, Case, ErrorTriple, Method, RootCause, Scenario
from bharpai.core.timing import is_salary_window as is_salary_window_at

# --------------------------------------------------------------------------------------------
# reason -> root cause
# --------------------------------------------------------------------------------------------

REASON_TO_CAUSE: dict[str, RootCause] = {
    # Bank, PSP, gateway or Razorpay had a bad moment. The instrument is fine; time fixes it.
    "bank_not_available": RootCause.TRANSIENT_TECH,
    "bank_technical_error": RootCause.TRANSIENT_TECH,
    "gateway_technical_error": RootCause.TRANSIENT_TECH,
    "issuer_technical_error": RootCause.TRANSIENT_TECH,
    "upi_app_technical_error": RootCause.TRANSIENT_TECH,
    "psp_not_available": RootCause.TRANSIENT_TECH,
    "psp_app_not_available": RootCause.TRANSIENT_TECH,
    "request_timed_out": RootCause.TRANSIENT_TECH,
    "payment_declined_due_to_high_traffic": RootCause.TRANSIENT_TECH,
    "bank_cutoff_in_progress": RootCause.TRANSIENT_TECH,
    "server_error": RootCause.TRANSIENT_TECH,
    "invalid_response_from_gateway": RootCause.TRANSIENT_TECH,
    "vpa_resolution_failed": RootCause.TRANSIENT_TECH,
    "collect_request_pending": RootCause.TRANSIENT_TECH,
    "duplicate_rrn_found": RootCause.TRANSIENT_TECH,
    "verification_failed": RootCause.TRANSIENT_TECH,
    "payment_pending": RootCause.TRANSIENT_TECH,
    "credit_failed": RootCause.TRANSIENT_TECH,
    # No money in the account right now. Recoverable, but only later.
    "insufficient_funds": RootCause.INSUFFICIENT_FUNDS,
    "credit_limit_exceeded": RootCause.INSUFFICIENT_FUNDS,
    # A ceiling was hit. Another instrument works immediately; the same one works tomorrow.
    "transaction_daily_limit_exceeded": RootCause.LIMIT_EXCEEDED,
    "transaction_limit_exceeded": RootCause.LIMIT_EXCEEDED,
    "transaction_frequency_limit_exceeded": RootCause.LIMIT_EXCEEDED,
    "transaction_daily_count_exceeded": RootCause.LIMIT_EXCEEDED,
    "mcc_amount_limit_exceeded": RootCause.LIMIT_EXCEEDED,
    "amount_less_than_minimum_amount": RootCause.LIMIT_EXCEEDED,
    # The customer walked away or ran out of time. Intent may still be there, briefly.
    "payment_timed_out": RootCause.CUSTOMER_ABANDON,
    "payment_cancelled": RootCause.CUSTOMER_ABANDON,
    "otp_expired": RootCause.CUSTOMER_ABANDON,
    "payment_session_expired": RootCause.CUSTOMER_ABANDON,
    "payment_collect_request_expired": RootCause.CUSTOMER_ABANDON,
    "otp_attempts_exceeded": RootCause.CUSTOMER_ABANDON,
    "pin_attempts_exceeded": RootCause.CUSTOMER_ABANDON,
    # Fixable mistake at the keyboard. A link with the right hint converts well.
    "incorrect_cvv": RootCause.CUSTOMER_INPUT,
    "incorrect_otp": RootCause.CUSTOMER_INPUT,
    "incorrect_pin": RootCause.CUSTOMER_INPUT,
    "incorrect_atm_pin": RootCause.CUSTOMER_INPUT,
    "invalid_vpa": RootCause.CUSTOMER_INPUT,
    "card_expired": RootCause.CUSTOMER_INPUT,
    "incorrect_card_details": RootCause.CUSTOMER_INPUT,
    "incorrect_card_expiry_date": RootCause.CUSTOMER_INPUT,
    "incorrect_cardholder_name": RootCause.CUSTOMER_INPUT,
    "card_number_invalid": RootCause.CUSTOMER_INPUT,
    "card_type_invalid": RootCause.CUSTOMER_INPUT,
    "authentication_failed": RootCause.CUSTOMER_INPUT,
    "invalid_mobile_number": RootCause.CUSTOMER_INPUT,
    "invalid_user_details": RootCause.CUSTOMER_INPUT,
    "bank_account_validation_failed": RootCause.CUSTOMER_INPUT,
    "pin_not_set": RootCause.CUSTOMER_INPUT,
    "invalid_device": RootCause.CUSTOMER_INPUT,
    # The instrument itself is refusing. Retrying it is close to free money for nobody.
    "debit_instrument_blocked": RootCause.INSTRUMENT_BLOCKED,
    "debit_instrument_inactive": RootCause.INSTRUMENT_BLOCKED,
    "card_declined": RootCause.INSTRUMENT_BLOCKED,
    "debit_declined": RootCause.INSTRUMENT_BLOCKED,
    "payment_declined": RootCause.INSTRUMENT_BLOCKED,
    "authorisation_declined_by_psp": RootCause.INSTRUMENT_BLOCKED,
    "transaction_on_vpa_restricted": RootCause.INSTRUMENT_BLOCKED,
    "international_transaction_not_allowed": RootCause.INSTRUMENT_BLOCKED,
    "user_not_eligible": RootCause.INSTRUMENT_BLOCKED,
    "bank_account_invalid": RootCause.INSTRUMENT_BLOCKED,
    "card_not_enrolled": RootCause.INSTRUMENT_BLOCKED,
    "credit_not_permitted": RootCause.INSTRUMENT_BLOCKED,
    "credit_limit_expired": RootCause.INSTRUMENT_BLOCKED,
    "credit_limit_inactive": RootCause.INSTRUMENT_BLOCKED,
    "credit_limit_not_approved": RootCause.INSTRUMENT_BLOCKED,
    "psp_app_not_supported": RootCause.INSTRUMENT_BLOCKED,
    "psp_not_registered": RootCause.INSTRUMENT_BLOCKED,
    "user_not_registered_for_netbanking": RootCause.INSTRUMENT_BLOCKED,
    "beneficiary_account_dormant": RootCause.INSTRUMENT_BLOCKED,
    "beneficiary_account_does_not_exist": RootCause.INSTRUMENT_BLOCKED,
    "collect_on_mcc_blocked": RootCause.INSTRUMENT_BLOCKED,
    "emi_plan_unavailable": RootCause.INSTRUMENT_BLOCKED,
    "emi_greater_than_max_amount": RootCause.INSTRUMENT_BLOCKED,
    # A mandate needs the customer's explicit consent again. Regulation forbids quiet retries.
    "mandate_creation_declined": RootCause.MANDATE_ISSUE,
    "mandate_creation_expired": RootCause.MANDATE_ISSUE,
    "mandate_creation_failed": RootCause.MANDATE_ISSUE,
    "mandate_creation_timeout": RootCause.MANDATE_ISSUE,
    "funds_blocked_by_mandate": RootCause.MANDATE_ISSUE,
    "reqauth_mandate_not_acknowledged": RootCause.MANDATE_ISSUE,
    "upi_autopay_not_supported_on_psp": RootCause.MANDATE_ISSUE,
    # Risk said no. We stop entirely: no retry, no message, hand it to a human.
    "payment_risk_check_failed": RootCause.RISK_DECLINE,
    "compliance_violation": RootCause.RISK_DECLINE,
    "payment_amount_tampered": RootCause.RISK_DECLINE,
    "deemed_transaction": RootCause.RISK_DECLINE,
    # The merchant's own configuration is broken. Contacting the customer would be rude and useless.
    "payment_method_not_enabled": RootCause.MERCHANT_CONFIG,
    "bank_not_enabled": RootCause.MERCHANT_CONFIG,
    "card_network_not_enabled": RootCause.MERCHANT_CONFIG,
    "invalid_order_id": RootCause.MERCHANT_CONFIG,
    "order_amount_mismatch": RootCause.MERCHANT_CONFIG,
    "order_payment_method_mismatch": RootCause.MERCHANT_CONFIG,
    "order_already_paid": RootCause.MERCHANT_CONFIG,
    "recurring_payment_not_enabled": RootCause.MERCHANT_CONFIG,
    "merchant_not_activated": RootCause.MERCHANT_CONFIG,
    "input_validation_failed": RootCause.MERCHANT_CONFIG,
    "invalid_amount": RootCause.MERCHANT_CONFIG,
    "invalid_currency": RootCause.MERCHANT_CONFIG,
    "invalid_request": RootCause.MERCHANT_CONFIG,
    "invalid_email": RootCause.MERCHANT_CONFIG,
    "mobile_number_invalid": RootCause.MERCHANT_CONFIG,
    "live_mode_not_enabled": RootCause.MERCHANT_CONFIG,
    "upi_collect_not_enabled": RootCause.MERCHANT_CONFIG,
    "upi_intent_not_enabled": RootCause.MERCHANT_CONFIG,
    "duplicate_request": RootCause.MERCHANT_CONFIG,
    "duplicate_refund_id": RootCause.MERCHANT_CONFIG,
    "mismatch_in_transaction_details": RootCause.MERCHANT_CONFIG,
    "record_not_found": RootCause.MERCHANT_CONFIG,
    "capture_failed": RootCause.MERCHANT_CONFIG,
    "refund_limit_crossed": RootCause.MERCHANT_CONFIG,
    "payment_pending_approval": RootCause.MERCHANT_CONFIG,
}

#: When the reason is missing or unmapped, ``source`` still tells us who has to act.
SOURCE_FALLBACK: dict[str, RootCause] = {
    "business": RootCause.MERCHANT_CONFIG,
    "gateway": RootCause.TRANSIENT_TECH,
    "network": RootCause.TRANSIENT_TECH,
    "issuer_bank": RootCause.TRANSIENT_TECH,
    "beneficiary_bank": RootCause.TRANSIENT_TECH,
    "customer_psp": RootCause.TRANSIENT_TECH,
    "internal": RootCause.TRANSIENT_TECH,
    "razorpay": RootCause.TRANSIENT_TECH,
    "bank": RootCause.TRANSIENT_TECH,
    "issuer": RootCause.TRANSIENT_TECH,
}

#: What a human should be told, in one line, before the explanation is dressed up.
CAUSE_SUMMARY: dict[RootCause, str] = {
    RootCause.TRANSIENT_TECH: "the bank or payment app failed momentarily, not the customer",
    RootCause.INSUFFICIENT_FUNDS: "the account did not have enough balance at that moment",
    RootCause.LIMIT_EXCEEDED: "a per-day or per-transaction ceiling was hit",
    RootCause.CUSTOMER_ABANDON: "the customer started paying but did not finish in time",
    RootCause.CUSTOMER_INPUT: "a correctable mistake was made while entering payment details",
    RootCause.INSTRUMENT_BLOCKED: "the card or UPI handle itself is blocked or not permitted",
    RootCause.MANDATE_ISSUE: "the recurring mandate needs the customer's consent again",
    RootCause.RISK_DECLINE: "the payment was stopped by a risk or compliance check",
    RootCause.MERCHANT_CONFIG: "the merchant's own payment configuration rejected the request",
    RootCause.ABANDONED_CHECKOUT: "the customer reached checkout and left without paying",
    RootCause.OVERDUE_RECEIVABLE: "an issued invoice has passed its due date unpaid",
    RootCause.UNKNOWN: "the failure signal does not match any known pattern",
}

#: Cause-specific guidance included in the customer's message. This is why a diagnosed nudge
#: outperforms a generic one: it tells the customer what to actually do differently.
CAUSE_GUIDANCE: dict[RootCause, str] = {
    RootCause.TRANSIENT_TECH: "the bank was briefly down, it should go through now",
    RootCause.INSUFFICIENT_FUNDS: "you can also pay by UPI from another account",
    RootCause.LIMIT_EXCEEDED: "your daily limit was reached, another method will work",
    RootCause.CUSTOMER_ABANDON: "your payment page timed out, here is a fresh link",
    RootCause.CUSTOMER_INPUT: "the card details did not match, UPI is quicker",
    RootCause.INSTRUMENT_BLOCKED: "that card was declined by the bank, try UPI instead",
    RootCause.MANDATE_ISSUE: "your auto-pay needs approving once more",
    RootCause.ABANDONED_CHECKOUT: "your order is still reserved",
    RootCause.OVERDUE_RECEIVABLE: "the invoice is past its due date",
    RootCause.UNKNOWN: "the payment did not go through",
}

#: Text for the regulatory notice that precedes any mandate debit.
PREDEBIT_GUIDANCE = "your auto-pay will be attempted again tomorrow"

#: Actions worth considering per cause. The policy engine narrows this further; the planner
#: never invents an action outside it.
CANDIDATES: dict[RootCause, tuple[ActionType, ...]] = {
    RootCause.TRANSIENT_TECH: (
        ActionType.RETRY_CHARGE,
        ActionType.SEND_PAYMENT_LINK,
        ActionType.OFFER_METHOD_SWITCH,
    ),
    RootCause.INSUFFICIENT_FUNDS: (
        ActionType.RETRY_CHARGE,
        ActionType.SEND_PAYMENT_LINK,
        ActionType.OFFER_METHOD_SWITCH,
    ),
    RootCause.LIMIT_EXCEEDED: (
        ActionType.RETRY_CHARGE,
        ActionType.SEND_PAYMENT_LINK,
        ActionType.OFFER_METHOD_SWITCH,
    ),
    RootCause.CUSTOMER_ABANDON: (ActionType.SEND_PAYMENT_LINK, ActionType.OFFER_METHOD_SWITCH),
    RootCause.CUSTOMER_INPUT: (ActionType.SEND_PAYMENT_LINK, ActionType.OFFER_METHOD_SWITCH),
    RootCause.INSTRUMENT_BLOCKED: (
        ActionType.OFFER_METHOD_SWITCH,
        ActionType.SEND_PAYMENT_LINK,
        ActionType.RETRY_CHARGE,
    ),
    RootCause.MANDATE_ISSUE: (
        ActionType.REQUEST_REAUTH,
        ActionType.OFFER_METHOD_SWITCH,
        ActionType.SEND_PAYMENT_LINK,
    ),
    RootCause.RISK_DECLINE: (),
    RootCause.MERCHANT_CONFIG: (ActionType.ALERT_MERCHANT, ActionType.RETRY_CHARGE),
    RootCause.ABANDONED_CHECKOUT: (ActionType.SEND_PAYMENT_LINK,),
    RootCause.OVERDUE_RECEIVABLE: (ActionType.SEND_REMINDER, ActionType.SEND_PAYMENT_LINK),
    RootCause.UNKNOWN: (
        ActionType.RETRY_CHARGE,
        ActionType.SEND_PAYMENT_LINK,
        ActionType.OFFER_METHOD_SWITCH,
    ),
}

#: The planner's beliefs about what works. These are coarse, drawn from published dunning and
#: decline statistics (docs/RESEARCH.md section 3), and are deliberately *not* the simulator's
#: hidden truth: the agent is not allowed to know the answer key.
BASE_PRIORS: dict[RootCause, dict[ActionType, float]] = {
    RootCause.TRANSIENT_TECH: {
        ActionType.RETRY_CHARGE: 0.75,
        ActionType.SEND_PAYMENT_LINK: 0.35,
        ActionType.OFFER_METHOD_SWITCH: 0.30,
    },
    RootCause.INSUFFICIENT_FUNDS: {
        ActionType.RETRY_CHARGE: 0.10,
        ActionType.SEND_PAYMENT_LINK: 0.30,
        ActionType.OFFER_METHOD_SWITCH: 0.25,
    },
    RootCause.LIMIT_EXCEEDED: {
        ActionType.RETRY_CHARGE: 0.20,
        ActionType.SEND_PAYMENT_LINK: 0.30,
        ActionType.OFFER_METHOD_SWITCH: 0.55,
    },
    RootCause.CUSTOMER_ABANDON: {
        ActionType.SEND_PAYMENT_LINK: 0.30,
        ActionType.OFFER_METHOD_SWITCH: 0.10,
    },
    RootCause.CUSTOMER_INPUT: {
        ActionType.SEND_PAYMENT_LINK: 0.40,
        ActionType.OFFER_METHOD_SWITCH: 0.35,
    },
    RootCause.INSTRUMENT_BLOCKED: {
        ActionType.RETRY_CHARGE: 0.02,
        ActionType.SEND_PAYMENT_LINK: 0.20,
        ActionType.OFFER_METHOD_SWITCH: 0.45,
    },
    RootCause.MANDATE_ISSUE: {
        ActionType.REQUEST_REAUTH: 0.40,
        ActionType.OFFER_METHOD_SWITCH: 0.20,
        ActionType.SEND_PAYMENT_LINK: 0.15,
    },
    RootCause.RISK_DECLINE: {},
    RootCause.MERCHANT_CONFIG: {ActionType.ALERT_MERCHANT: 0.85, ActionType.RETRY_CHARGE: 0.30},
    RootCause.ABANDONED_CHECKOUT: {ActionType.SEND_PAYMENT_LINK: 0.20},
    RootCause.OVERDUE_RECEIVABLE: {
        ActionType.SEND_REMINDER: 0.25,
        ActionType.SEND_PAYMENT_LINK: 0.10,
    },
    RootCause.UNKNOWN: {
        ActionType.RETRY_CHARGE: 0.30,
        ActionType.SEND_PAYMENT_LINK: 0.20,
        ActionType.OFFER_METHOD_SWITCH: 0.15,
    },
}

ATTEMPT_DECAY = 0.6
AGE_DECAY_PER_DAY = 0.9


def classify(
    error: ErrorTriple | None, scenario: Scenario, amount_paise: int = 0
) -> tuple[RootCause, list[str]]:
    """Return the root cause and any tags that change how it must be handled.

    Scenario wins where it is unambiguous: an abandoned checkout has no error to read, and an
    overdue invoice is overdue regardless of how the last attempt failed.
    """

    tags: list[str] = []

    if scenario is Scenario.B:
        return RootCause.ABANDONED_CHECKOUT, tags
    if scenario is Scenario.D:
        return RootCause.OVERDUE_RECEIVABLE, tags

    cause = RootCause.UNKNOWN
    if error is not None:
        reason = (error.reason or "").strip().lower()
        if reason in REASON_TO_CAUSE:
            cause = REASON_TO_CAUSE[reason]
        else:
            source = (error.source or "").strip().lower()
            step = (error.step or "").strip().lower()
            if source == "customer" and step == "payment_authentication":
                cause = RootCause.CUSTOMER_INPUT
            elif source in SOURCE_FALLBACK:
                cause = SOURCE_FALLBACK[source]
            if reason and cause is not RootCause.UNKNOWN:
                tags.append("unmapped_reason")

    # Above the RBI additional-factor threshold a recurring charge cannot simply be re-attempted;
    # the customer has to approve it. Tagged here so the policy engine can enforce R14.
    if scenario is Scenario.C and amount_paise > 1_500_000:
        tags.append("afa_required")

    return cause, tags


def diagnose(case: Case) -> tuple[RootCause, list[str], str]:
    """Classify a case and produce the fallback plain-English explanation."""

    cause, tags = classify(case.error, case.scenario, case.amount_paise)
    summary = CAUSE_SUMMARY[cause]
    reason = (case.error.reason if case.error else None) or "no error code"
    text = (
        f"₹{case.amount_inr:,.0f} on {case.method.value} failed with '{reason}': {summary}."
    )
    if "afa_required" in tags:
        text += " Above ₹15,000 this mandate needs re-authentication, so it cannot be auto-retried."
    return cause, tags, text


def candidate_actions(case: Case) -> tuple[ActionType, ...]:
    """Actions worth considering for this case.

    A mandate cannot legally be re-debited until the customer has been given 24 hours' notice,
    so for subscriptions that notice is itself a move — and without it the retry branch is dead.
    """

    cause = case.root_cause or RootCause.UNKNOWN
    actions = CANDIDATES.get(cause, ())
    if case.merchant_alerted:
        # Telling them twice adds nothing; what recovers the money now is a patient retry.
        actions = tuple(a for a in actions if a is not ActionType.ALERT_MERCHANT)
    if (
        case.scenario is Scenario.C
        and case.predebit_notice_at is None
        and ActionType.RETRY_CHARGE in actions
        and "afa_required" not in case.tags
    ):
        actions = (ActionType.SEND_PREDEBIT_NOTICE, *actions)
    return actions


def prior(
    case: Case,
    action: ActionType,
    now: datetime,
    *,
    salary_window: bool = False,
    downtime_active: bool = False,
    hours_since_failure: float | None = None,
) -> float:
    """Believed probability that ``action`` recovers ``case`` if taken now.

    Timing matters more than anything else in recovery, so the base rate is adjusted for the
    situations where published data shows a large, well-understood effect.
    """

    cause = case.root_cause or RootCause.UNKNOWN

    if action is ActionType.SEND_PREDEBIT_NOTICE:
        # The notice recovers nothing by itself; it is worth exactly the retry it unlocks a day
        # later, which is how the planner comes to see a compliance step as a revenue step.
        return 0.9 * prior(
            case,
            ActionType.RETRY_CHARGE,
            now + timedelta(hours=24),
            salary_window=is_salary_window_at(now + timedelta(hours=24)),
            downtime_active=False,
        )

    base = BASE_PRIORS.get(cause, {}).get(action)
    if base is None:
        return 0.0

    hours = (
        hours_since_failure
        if hours_since_failure is not None
        else (now - case.created_at).total_seconds() / 3600
    )

    if cause is RootCause.TRANSIENT_TECH and action is ActionType.RETRY_CHARGE:
        # Retrying into an ongoing outage is throwing the attempt away.
        base = 0.05 if downtime_active else 0.75
    elif cause is RootCause.INSUFFICIENT_FUNDS and action is ActionType.RETRY_CHARGE:
        if salary_window:
            base = 0.50
        elif hours >= 24:
            base = 0.35
        else:
            base = 0.10
    elif cause is RootCause.LIMIT_EXCEEDED and action is ActionType.RETRY_CHARGE:
        # Daily ceilings reset overnight.
        base = 0.65 if hours >= 20 else 0.05
    elif cause is RootCause.CUSTOMER_ABANDON and action is ActionType.SEND_PAYMENT_LINK:
        base = 0.30 if hours <= 1 else 0.15
    elif cause is RootCause.ABANDONED_CHECKOUT and action is ActionType.SEND_PAYMENT_LINK:
        base = 0.20 if case.attempts_of(action) == 0 else 0.08
    elif cause is RootCause.MERCHANT_CONFIG and action is ActionType.RETRY_CHARGE:
        # The retry only works once the merchant has fixed their settings, so its value tracks
        # the chance they have got round to it. Modelled as a ramp from a few hours to a couple
        # of days, which is what makes the planner wait a day instead of hammering for an hour.
        fixed_by_now = min(1.0, max(0.0, (hours - 4) / 44))
        base = 0.85 * fixed_by_now

    base *= ATTEMPT_DECAY ** case.attempts_of(action)
    base *= AGE_DECAY_PER_DAY ** max(0.0, case.age_days(now))
    return max(0.0, min(1.0, base))


def preferred_switch_method(case: Case) -> Method:
    """Which instrument to suggest when the current one is not working."""

    if case.method in (Method.card, Method.netbanking, Method.wallet, Method.emandate):
        return Method.upi
    return Method.card
