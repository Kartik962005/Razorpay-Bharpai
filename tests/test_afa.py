"""RBI additional-factor authentication: above the threshold, only the customer can approve."""

from __future__ import annotations

from datetime import timedelta

from wapsi.core.models import ActionType, Method, RootCause, Scenario

from tests.conftest import NOW


def high_value_mandate(make_case, amount_paise: int):
    return make_case(
        scenario=Scenario.C,
        method=Method.upi_autopay,
        root_cause=RootCause.MANDATE_ISSUE,
        amount_paise=amount_paise,
        predebit_notice_at=NOW - timedelta(hours=30),
        tags=["afa_required"] if amount_paise > 1_500_000 else [],
    )


def test_large_recurring_charges_are_never_auto_retried(make_case, denials):
    case = high_value_mandate(make_case, 1_800_000)
    assert "R14" in denials(case, ActionType.RETRY_CHARGE)


def test_small_recurring_charges_may_be_retried(make_case, denials):
    case = high_value_mandate(make_case, 49_900)
    assert "R14" not in denials(case, ActionType.RETRY_CHARGE)


def test_the_answer_to_afa_is_asking_for_re_authentication(make_case, denials):
    case = high_value_mandate(make_case, 1_800_000)
    assert denials(case, ActionType.REQUEST_REAUTH) == []


def test_a_mandate_issue_offers_reauthentication_and_no_charge(engine, make_case, ctx):
    case = high_value_mandate(make_case, 1_800_000)
    allowed, _ = engine.allowed(case, NOW, ctx)
    types = {a.type for a in allowed}

    assert ActionType.REQUEST_REAUTH in types
    assert ActionType.RETRY_CHARGE not in types


def test_afa_blocks_the_retry_a_funds_failure_would_otherwise_invite(engine, make_case, ctx):
    """The rule earns its keep here: a big subscription failing on balance looks retryable."""

    case = make_case(
        scenario=Scenario.C,
        method=Method.upi_autopay,
        root_cause=RootCause.INSUFFICIENT_FUNDS,
        amount_paise=1_800_000,
        tags=["afa_required"],
        predebit_notice_at=NOW - timedelta(hours=30),
    )
    allowed, denied = engine.allowed(case, NOW, ctx)

    assert ActionType.RETRY_CHARGE not in {a.type for a in allowed}
    assert "R14" in {d.rule_id for d in denied}


def test_the_threshold_is_read_from_policy(engine):
    assert engine.afa_threshold_paise == 1_500_000
