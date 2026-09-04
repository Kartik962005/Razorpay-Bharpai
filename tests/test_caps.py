"""Frequency limits. Recovery that ignores these is just harassment with a payment link."""

from __future__ import annotations

from datetime import timedelta

from wapsi.core.models import ActionType, RootCause, Scenario
from wapsi.core.policy import PolicyContext

from tests.conftest import NOW


def test_a_fourth_nudge_is_refused(make_case, denials):
    assert "R20" not in denials(make_case(nudges=2, last_contact_at=NOW - timedelta(days=2)), ActionType.SEND_PAYMENT_LINK)
    assert "R20" in denials(make_case(nudges=3, last_contact_at=NOW - timedelta(days=2)), ActionType.SEND_PAYMENT_LINK)


def test_nudges_must_be_a_day_apart(make_case, denials, earliest):
    too_soon = make_case(nudges=1, last_contact_at=NOW - timedelta(hours=6))
    assert "R20" in denials(too_soon, ActionType.SEND_PAYMENT_LINK)
    assert earliest(too_soon, ActionType.SEND_PAYMENT_LINK, "R20") == NOW + timedelta(hours=18)

    long_enough = make_case(nudges=1, last_contact_at=NOW - timedelta(hours=25))
    assert "R20" not in denials(long_enough, ActionType.SEND_PAYMENT_LINK)


def test_abandoned_checkouts_get_a_grace_period_before_the_first_nudge(make_case, denials):
    just_left = make_case(
        scenario=Scenario.B,
        root_cause=RootCause.ABANDONED_CHECKOUT,
        created_at=NOW - timedelta(minutes=10),
    )
    assert "R20" in denials(just_left, ActionType.SEND_PAYMENT_LINK)

    gone_a_while = make_case(
        scenario=Scenario.B,
        root_cause=RootCause.ABANDONED_CHECKOUT,
        created_at=NOW - timedelta(minutes=45),
    )
    assert "R20" not in denials(gone_a_while, ActionType.SEND_PAYMENT_LINK)


def test_a_case_cannot_consume_unlimited_actions(make_case, denials):
    assert "R21" not in denials(make_case(actions=4), ActionType.SEND_PAYMENT_LINK)
    assert "R21" in denials(make_case(actions=5), ActionType.SEND_PAYMENT_LINK)
    assert "R21" in denials(make_case(actions=5), ActionType.RETRY_CHARGE)


def test_one_customer_cannot_be_messaged_endlessly_across_cases(make_case, denials):
    """Caps are per case, so the customer-level cap is what stops five cases ganging up."""

    case = make_case()
    busy = PolicyContext(customer_messages_last_7d=5)
    quiet = PolicyContext(customer_messages_last_7d=4)
    assert "R23" in denials(case, ActionType.SEND_PAYMENT_LINK, NOW, busy)
    assert "R23" not in denials(case, ActionType.SEND_PAYMENT_LINK, NOW, quiet)


def test_a_promise_to_pay_buys_the_customer_quiet(make_case, denials, earliest):
    promised = make_case(promise_at=NOW + timedelta(days=3))
    assert "R41" in denials(promised, ActionType.SEND_PAYMENT_LINK)
    assert earliest(promised, ActionType.SEND_PAYMENT_LINK, "R41") == NOW + timedelta(days=4)

    lapsed = make_case(promise_at=NOW - timedelta(days=2), nudges=1, last_contact_at=NOW - timedelta(days=3))
    assert "R41" not in denials(lapsed, ActionType.SEND_PAYMENT_LINK)


def test_retries_are_not_counted_as_nudges(make_case, denials):
    """A silent retry costs the customer nothing, so the contact caps must not apply to it."""

    case = make_case(root_cause=RootCause.TRANSIENT_TECH, nudges=3, last_contact_at=NOW - timedelta(hours=1))
    assert "R20" not in denials(case, ActionType.RETRY_CHARGE)
