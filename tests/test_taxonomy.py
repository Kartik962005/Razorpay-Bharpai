"""The diagnosis has to be right before anything downstream matters."""

from __future__ import annotations

import pytest

from wapsi.core.models import ActionType, ErrorTriple, Method, RootCause, Scenario
from wapsi.core.taxonomy import (
    BASE_PRIORS,
    CANDIDATES,
    CAUSE_SUMMARY,
    REASON_TO_CAUSE,
    candidate_actions,
    classify,
    diagnose,
    prior,
)


def test_every_mapped_reason_resolves_to_a_known_cause():
    for reason, cause in REASON_TO_CAUSE.items():
        got, _ = classify(ErrorTriple(reason=reason), Scenario.A)
        assert got is cause, reason


def test_every_cause_has_a_summary_and_candidate_list():
    for cause in RootCause:
        assert cause in CAUSE_SUMMARY
        assert cause in CANDIDATES


def test_candidate_actions_all_have_priors():
    for cause, actions in CANDIDATES.items():
        for action in actions:
            assert action in BASE_PRIORS[cause], (cause, action)


@pytest.mark.parametrize(
    "reason,source,step,expected",
    [
        ("insufficient_funds", "customer", "payment_authorization", RootCause.INSUFFICIENT_FUNDS),
        ("payment_risk_check_failed", "gateway", None, RootCause.RISK_DECLINE),
        ("payment_method_not_enabled", "business", None, RootCause.MERCHANT_CONFIG),
        ("bank_not_available", "gateway", None, RootCause.TRANSIENT_TECH),
        ("debit_instrument_blocked", "gateway", None, RootCause.INSTRUMENT_BLOCKED),
        ("mandate_creation_declined", "customer", "mandate_creation", RootCause.MANDATE_ISSUE),
    ],
)
def test_representative_reasons(reason, source, step, expected):
    cause, _ = classify(ErrorTriple(reason=reason, source=source, step=step), Scenario.A)
    assert cause is expected


def test_unmapped_reason_falls_back_to_source():
    # A reason Razorpay adds tomorrow must not silently become someone else's problem.
    cause, _ = classify(ErrorTriple(reason="brand_new_reason", source="business"), Scenario.A)
    assert cause is RootCause.MERCHANT_CONFIG

    cause, _ = classify(ErrorTriple(reason="brand_new_reason", source="gateway"), Scenario.A)
    assert cause is RootCause.TRANSIENT_TECH

    cause, _ = classify(
        ErrorTriple(reason="brand_new_reason", source="customer", step="payment_authentication"),
        Scenario.A,
    )
    assert cause is RootCause.CUSTOMER_INPUT


def test_completely_unknown_error_is_unknown():
    cause, _ = classify(ErrorTriple(reason="???", source="???"), Scenario.A)
    assert cause is RootCause.UNKNOWN

    cause, _ = classify(None, Scenario.A)
    assert cause is RootCause.UNKNOWN


def test_scenario_overrides_error_for_checkout_and_receivables():
    cause, _ = classify(ErrorTriple(reason="insufficient_funds"), Scenario.B)
    assert cause is RootCause.ABANDONED_CHECKOUT

    cause, _ = classify(ErrorTriple(reason="insufficient_funds"), Scenario.D)
    assert cause is RootCause.OVERDUE_RECEIVABLE


def test_high_value_recurring_is_tagged_for_reauthentication():
    _, tags = classify(ErrorTriple(reason="insufficient_funds"), Scenario.C, amount_paise=1_800_000)
    assert "afa_required" in tags

    _, tags = classify(ErrorTriple(reason="insufficient_funds"), Scenario.C, amount_paise=1_499_900)
    assert "afa_required" not in tags


def test_risk_decline_offers_no_recovery_action(make_case):
    assert candidate_actions(make_case(root_cause=RootCause.RISK_DECLINE)) == ()


def test_a_subscription_must_be_noticed_before_it_can_be_retried(make_case):
    """Without the pre-debit notice the retry branch is dead, so the notice is itself a move."""

    from wapsi.core.models import Method

    unnotified = make_case(
        scenario=Scenario.C, method=Method.upi_autopay, root_cause=RootCause.INSUFFICIENT_FUNDS
    )
    assert ActionType.SEND_PREDEBIT_NOTICE in candidate_actions(unnotified)

    from tests.conftest import NOW

    notified = make_case(
        scenario=Scenario.C,
        method=Method.upi_autopay,
        root_cause=RootCause.INSUFFICIENT_FUNDS,
        predebit_notice_at=NOW,
    )
    assert ActionType.SEND_PREDEBIT_NOTICE not in candidate_actions(notified)

    # Above the AFA threshold no notice can make an auto-retry lawful, so none is offered.
    afa = make_case(
        scenario=Scenario.C,
        method=Method.upi_autopay,
        root_cause=RootCause.INSUFFICIENT_FUNDS,
        amount_paise=1_800_000,
        tags=["afa_required"],
    )
    assert ActionType.SEND_PREDEBIT_NOTICE not in candidate_actions(afa)


def test_the_notice_is_valued_by_the_retry_it_unlocks(make_case):
    from tests.conftest import NOW
    from wapsi.core.models import Method

    case = make_case(
        scenario=Scenario.C, method=Method.upi_autopay, root_cause=RootCause.INSUFFICIENT_FUNDS
    )
    notice = prior(case, ActionType.SEND_PREDEBIT_NOTICE, NOW)
    assert notice > 0


def test_diagnosis_text_names_the_amount_and_reason(make_case):
    case = make_case()
    cause, _tags, text = diagnose(case)
    assert cause is RootCause.INSUFFICIENT_FUNDS
    assert "1,299" in text
    assert "insufficient_funds" in text


def test_prior_rewards_waiting_for_funds(make_case):
    from tests.conftest import NOW

    case = make_case(root_cause=RootCause.INSUFFICIENT_FUNDS)
    immediate = prior(case, ActionType.RETRY_CHARGE, NOW, hours_since_failure=1)
    next_day = prior(case, ActionType.RETRY_CHARGE, NOW, hours_since_failure=30)
    payday = prior(case, ActionType.RETRY_CHARGE, NOW, hours_since_failure=30, salary_window=True)
    assert immediate < next_day < payday


def test_prior_punishes_retrying_into_an_outage(make_case):
    from tests.conftest import NOW

    case = make_case(root_cause=RootCause.TRANSIENT_TECH)
    during = prior(case, ActionType.RETRY_CHARGE, NOW, downtime_active=True)
    after = prior(case, ActionType.RETRY_CHARGE, NOW, downtime_active=False)
    assert during < 0.1 < after


def test_prior_decays_with_repeated_attempts(make_case):
    from tests.conftest import NOW

    first = make_case(root_cause=RootCause.CUSTOMER_INPUT)
    later = make_case(
        root_cause=RootCause.CUSTOMER_INPUT,
        attempts_by_action={ActionType.SEND_PAYMENT_LINK.value: 2},
    )
    assert prior(later, ActionType.SEND_PAYMENT_LINK, NOW) < prior(
        first, ActionType.SEND_PAYMENT_LINK, NOW
    )


def test_method_switch_moves_away_from_the_failing_rail(make_case):
    from wapsi.core.taxonomy import preferred_switch_method

    assert preferred_switch_method(make_case(method=Method.card)) is Method.upi
    assert preferred_switch_method(make_case(method=Method.upi)) is Method.card
