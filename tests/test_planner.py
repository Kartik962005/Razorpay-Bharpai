"""The planner's judgement: what to do, and when to do it."""

from __future__ import annotations

from datetime import datetime, timedelta

from wapsi.config import IST
from wapsi.core.models import ActionType, Method, Outcome, RootCause, Scenario
from wapsi.core.planner import RulesPlanner
from wapsi.core.policy import PolicyContext

from tests.conftest import NOW


def plan(engine, case, now=NOW, ctx=None):
    return RulesPlanner(engine).plan(case, now, ctx or PolicyContext())


def test_it_picks_the_most_valuable_permitted_action(engine, make_case):
    case = make_case(root_cause=RootCause.CUSTOMER_INPUT, amount_paise=129_900)
    decision = plan(engine, case)
    assert decision.action.type in (ActionType.SEND_PAYMENT_LINK, ActionType.OFFER_METHOD_SWITCH)
    assert "expected" in decision.rationale


def test_a_blocked_instrument_is_never_retried(engine, make_case):
    case = make_case(root_cause=RootCause.INSTRUMENT_BLOCKED)
    decision = plan(engine, case)
    assert decision.action.type is not ActionType.RETRY_CHARGE


def test_it_waits_rather_than_messaging_at_night(engine, make_case):
    night = datetime(2026, 8, 3, 22, 30, tzinfo=IST)
    case = make_case(root_cause=RootCause.CUSTOMER_ABANDON, created_at=night - timedelta(minutes=5))
    decision = plan(engine, case, night)
    assert decision.action.type is ActionType.WAIT
    assert decision.action.scheduled_at.hour == 10


def test_it_prefers_payday_for_a_balance_failure(engine, make_case):
    """The core idea: the same retry is worth several times more a few days later."""

    late_month = datetime(2026, 8, 29, 11, 0, tzinfo=IST)
    case = make_case(
        root_cause=RootCause.INSUFFICIENT_FUNDS,
        amount_paise=250_000,
        created_at=late_month - timedelta(minutes=10),
    )
    decision = plan(engine, case, late_month)
    assert decision.action.type is ActionType.WAIT
    # Waits into the salary window at the start of September rather than retrying into an
    # empty account now.
    assert decision.action.scheduled_at > late_month


def test_risk_declines_go_straight_to_a_human(engine, make_case):
    case = make_case(root_cause=RootCause.RISK_DECLINE)
    decision = plan(engine, case)
    assert decision.action.type is ActionType.ESCALATE_HUMAN
    assert "R05" in decision.rule_ids


def test_merchant_faults_alert_the_merchant_not_the_customer(engine, make_case):
    case = make_case(root_cause=RootCause.MERCHANT_CONFIG)
    decision = plan(engine, case)
    assert decision.action.type is ActionType.ALERT_MERCHANT
    assert "R06" in decision.rule_ids


def test_a_paid_case_closes_without_further_contact(engine, make_case):
    case = make_case(paid=True)
    decision = plan(engine, case)
    assert decision.action.type is ActionType.CLOSE
    assert decision.action.params["outcome"] == Outcome.recovered.value


def test_it_escalates_a_large_case_after_the_ladder_is_spent(engine, make_case):
    case = make_case(amount_paise=3_000_000, nudges=3, last_contact_at=NOW - timedelta(days=2))
    decision = plan(engine, case)
    assert decision.action.type is ActionType.ESCALATE_HUMAN
    assert "R30" in decision.rule_ids


def test_a_weekly_message_cap_defers_rather_than_abandons(engine, make_case):
    """A rolling cap is temporary. Treating it as terminal quietly loses recoverable cases."""

    case = make_case(scenario=Scenario.D, root_cause=RootCause.OVERDUE_RECEIVABLE, amount_paise=313_000)
    # Five messages this week, the oldest almost seven days old: the cap lifts within the hour.
    capped = PolicyContext(
        customer_message_times=[
            NOW - timedelta(days=6, hours=23),
            NOW - timedelta(days=5),
            NOW - timedelta(days=4),
            NOW - timedelta(days=3),
            NOW - timedelta(days=2),
        ],
    )
    decision = plan(engine, case, NOW, capped)
    assert decision.action.type is ActionType.WAIT

    # With no record of when those messages went out there is nothing to wait for.
    blind = PolicyContext(customer_messages_last_7d=5)
    assert plan(engine, case, NOW, blind).action.type is ActionType.CLOSE


def test_a_subscription_gets_its_notice_before_any_retry(engine, make_case):
    case = make_case(
        scenario=Scenario.C,
        method=Method.upi_autopay,
        root_cause=RootCause.INSUFFICIENT_FUNDS,
        amount_paise=49_900,
    )
    decision = plan(engine, case)
    action = decision.action
    if action.type is ActionType.WAIT:
        assert action.params.get("then") == ActionType.SEND_PREDEBIT_NOTICE.value
    else:
        assert action.type is ActionType.SEND_PREDEBIT_NOTICE


def test_it_gives_up_when_nothing_is_left_to_try(engine, make_case):
    spent = make_case(nudges=3, actions=5, last_contact_at=NOW - timedelta(days=2))
    decision = plan(engine, spent)
    assert decision.action.type is ActionType.CLOSE
    assert decision.action.params["outcome"] == Outcome.gave_up.value
