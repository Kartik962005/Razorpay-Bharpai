"""Conditions that end a case outright. Getting these wrong is how an agent harms someone."""

from __future__ import annotations

from datetime import timedelta

from wapsi.core.models import ActionType, Outcome, RootCause, Scenario
from wapsi.core.policy import PolicyContext

from tests.conftest import NOW


def stop_ids(engine, case, ctx=None, now=NOW):
    return [rule for _, rule in engine.hard_stops(case, ctx or PolicyContext(), now)]


def test_paid_case_closes_as_recovered(engine, make_case, ctx):
    case = make_case(paid=True)
    stops = engine.hard_stops(case, ctx, NOW)
    assert stops[0] == (Outcome.recovered, "R01")


def test_refetch_showing_payment_stops_the_case_even_if_our_copy_is_stale(engine, make_case):
    case = make_case()
    fresh = PolicyContext(paid=True)
    assert ("R01") in stop_ids(engine, case, fresh)


def test_opt_out_dispute_and_refund_all_stop_the_case(engine, make_case, ctx):
    assert "R02" in stop_ids(engine, make_case(opted_out=True))
    assert "R03" in stop_ids(engine, make_case(disputed=True))
    assert "R04" in stop_ids(engine, make_case(refunded=True))


def test_risk_decline_blocks_every_recovery_action(make_case, denials):
    case = make_case(root_cause=RootCause.RISK_DECLINE)
    assert "R05" in denials(case, ActionType.RETRY_CHARGE)
    assert "R05" in denials(case, ActionType.SEND_PAYMENT_LINK)
    assert "R05" in denials(case, ActionType.OFFER_METHOD_SWITCH)


def test_risk_decline_can_still_be_escalated_to_a_human(make_case, denials):
    case = make_case(root_cause=RootCause.RISK_DECLINE)
    assert denials(case, ActionType.ESCALATE_HUMAN) == []


def test_merchant_config_never_reaches_the_customer(make_case, denials):
    case = make_case(root_cause=RootCause.MERCHANT_CONFIG)
    assert "R06" in denials(case, ActionType.SEND_PAYMENT_LINK)
    assert "R06" in denials(case, ActionType.RETRY_CHARGE)
    # Telling the merchant is the whole point, so that one action stays open.
    assert denials(case, ActionType.ALERT_MERCHANT) == []


def test_merchant_config_closes_once_the_merchant_has_been_told(engine, make_case, ctx):
    case = make_case(root_cause=RootCause.MERCHANT_CONFIG, merchant_alerted=True)
    assert (Outcome.merchant_issue, "R06") in engine.hard_stops(case, ctx, NOW)


def test_customer_cancellation_stops_subscription_recovery(engine, make_case):
    case = make_case(scenario=Scenario.C, cancelled_by_customer=True)
    assert "R07" in stop_ids(engine, case)


def test_cases_expire_after_their_recovery_window(engine, make_case, ctx):
    fresh = make_case(created_at=NOW - timedelta(days=13))
    stale = make_case(created_at=NOW - timedelta(days=15))
    assert "R22" not in stop_ids(engine, fresh)
    assert "R22" in stop_ids(engine, stale)


def test_receivables_get_a_longer_window(engine, make_case):
    invoice = make_case(
        scenario=Scenario.D,
        root_cause=RootCause.OVERDUE_RECEIVABLE,
        created_at=NOW - timedelta(days=20),
    )
    assert "R22" not in stop_ids(engine, invoice)

    ancient = make_case(
        scenario=Scenario.D,
        root_cause=RootCause.OVERDUE_RECEIVABLE,
        created_at=NOW - timedelta(days=31),
    )
    assert "R22" in stop_ids(engine, ancient)


def test_no_action_survives_a_hard_stop(make_case, denials):
    for override in ({"paid": True}, {"opted_out": True}, {"disputed": True}, {"refunded": True}):
        case = make_case(**override)
        assert denials(case, ActionType.SEND_PAYMENT_LINK), override
        assert denials(case, ActionType.RETRY_CHARGE), override


def test_escalation_triggers_fire_on_the_documented_conditions(engine, make_case, ctx):
    high_value = make_case(amount_paise=3_000_000, nudges=2)
    assert "R30" in engine.escalation_triggers(high_value, ctx)

    unreliable = make_case(promises_broken=2)
    assert "R31" in engine.escalation_triggers(unreliable, ctx)

    angry = make_case(complaint=True)
    assert "R32" in engine.escalation_triggers(angry, ctx)

    baffling = make_case(root_cause=RootCause.UNKNOWN, retries=2)
    assert "R33" in engine.escalation_triggers(baffling, ctx)

    argumentative = make_case(llm_denials=2)
    assert "R34" in engine.escalation_triggers(argumentative, ctx)


def test_an_ordinary_case_triggers_no_escalation(engine, make_case, ctx):
    assert engine.escalation_triggers(make_case(), ctx) == []
