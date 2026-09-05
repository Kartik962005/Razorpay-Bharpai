"""Execution: idempotency, guardrails, and scoring every action against the rules."""

from __future__ import annotations

from datetime import datetime, timedelta

from bharpai.adapters.composer import TemplateComposer
from bharpai.adapters.humanqueue import HumanQueue
from bharpai.adapters.messaging import Messenger
from bharpai.config import IST
from bharpai.core.audit import AuditLog
from bharpai.core.executor import Executor
from bharpai.core.models import Action, ActionType, Channel, Method, Outcome, RootCause, Scenario

from tests.conftest import NOW


class StubGateway:
    """A gateway whose payment status we control, to test the idempotency guard."""

    def __init__(self, paid: bool = False, retry_succeeds: bool = False):
        self.paid = paid
        self.retry_succeeds = retry_succeeds
        self.calls: list[str] = []

    def refresh(self, case, now):
        self.calls.append("refresh")
        return {"paid": self.paid, "status": "paid" if self.paid else "failed"}

    def create_payment_link(self, case, now, **kwargs):
        self.calls.append("create_payment_link")
        return {"id": "plink_test", "short_url": "https://rzp.io/i/test01"}

    def notify(self, entity_type, entity_id, medium):
        self.calls.append("notify")
        return {"success": True}

    def retry_charge(self, case, now):
        self.calls.append("retry_charge")
        return {"attempted": True, "success": self.retry_succeeds, "disputed": False}


def build(engine, gateway):
    audit = AuditLog()
    messenger = Messenger(engine.economics["channel_cost_paise"])
    queue = HumanQueue()
    executor = Executor(
        gateway=gateway,
        messenger=messenger,
        queue=queue,
        policy=engine,
        audit=audit,
        composer=TemplateComposer(engine.policy),
    )
    return executor, audit, messenger, queue


def test_it_never_contacts_someone_who_has_already_paid(engine, make_case, ctx):
    gateway = StubGateway(paid=True)
    executor, audit, messenger, _ = build(engine, gateway)
    case = make_case()

    result = executor.execute(
        Action(case_id=case.id, type=ActionType.SEND_PAYMENT_LINK, params={"channel": "sms"}),
        case,
        NOW,
        ctx,
    )

    assert result.paid and result.closed
    assert messenger.sent == [], "a paid case must not be messaged"
    assert "create_payment_link" not in gateway.calls
    assert case.outcome is Outcome.recovered
    assert any("R01" in entry.rule_ids for entry in audit.entries)


def test_it_never_recharges_an_order_that_is_already_paid(engine, make_case, ctx):
    gateway = StubGateway(paid=True)
    executor, _, _, _ = build(engine, gateway)
    case = make_case()

    executor.execute(Action(case_id=case.id, type=ActionType.RETRY_CHARGE), case, NOW, ctx)
    assert "retry_charge" not in gateway.calls


def test_it_records_a_rule_break_even_when_the_policy_ignores_the_rules(engine, make_case, ctx):
    """This is what makes the baseline comparison fair rather than self-reported."""

    gateway = StubGateway()
    executor, audit, _, _ = build(engine, gateway)
    case = make_case()
    night = datetime(2026, 8, 3, 23, 30, tzinfo=IST)

    result = executor.execute(
        Action(
            case_id=case.id,
            type=ActionType.SEND_REMINDER,
            params={"channel": "sms", "tone": "firm", "language": "en"},
        ),
        case,
        night,
        ctx,
    )

    assert "R10" in result.violations
    action_entry = next(e for e in audit.entries if e.kind == "action")
    assert "R10" in action_entry.payload["violations"]


def test_a_successful_retry_closes_the_case_as_recovered(engine, make_case, ctx):
    gateway = StubGateway(retry_succeeds=True)
    executor, _, _, _ = build(engine, gateway)
    case = make_case(root_cause=RootCause.TRANSIENT_TECH)

    result = executor.execute(Action(case_id=case.id, type=ActionType.RETRY_CHARGE), case, NOW, ctx)

    assert result.paid
    assert case.outcome is Outcome.recovered
    assert case.recovered_paise == case.amount_paise


def test_messages_are_sent_priced_and_logged(engine, make_case, ctx):
    gateway = StubGateway()
    executor, _, messenger, _ = build(engine, gateway)
    case = make_case()

    executor.execute(
        Action(
            case_id=case.id,
            type=ActionType.SEND_PAYMENT_LINK,
            params={"channel": "whatsapp", "tone": "soft", "language": "hinglish"},
        ),
        case,
        NOW,
        ctx,
    )

    assert len(messenger.sent) == 1
    message = messenger.sent[0]
    assert message.channel is Channel.whatsapp
    assert message.cost_paise == 80
    assert "https://rzp.io/i/test01" in message.text
    assert case.nudges == 1


def test_a_pre_debit_notice_unlocks_a_later_retry_without_spending_a_nudge(engine, make_case, ctx):
    gateway = StubGateway()
    executor, _, _, _ = build(engine, gateway)
    case = make_case(
        scenario=Scenario.C, method=Method.upi_autopay, root_cause=RootCause.INSUFFICIENT_FUNDS
    )

    executor.execute(
        Action(
            case_id=case.id,
            type=ActionType.SEND_PREDEBIT_NOTICE,
            params={"channel": "sms", "tone": "soft", "language": "en"},
        ),
        case,
        NOW,
        ctx,
    )

    assert case.predebit_notice_at == NOW
    assert case.nudges == 0, "a regulatory notice is not a dunning nudge"

    # And the retry it exists to permit is legal a day later, but not before.
    retry = Action(case_id=case.id, type=ActionType.RETRY_CHARGE)
    assert "R13" in [d.rule_id for d in engine.check_all(retry, case, NOW, ctx)]
    later = NOW + timedelta(hours=25)
    assert "R13" not in [d.rule_id for d in engine.check_all(retry, case, later, ctx)]


def test_escalation_produces_a_ticket_a_person_can_act_on(engine, make_case, ctx):
    gateway = StubGateway()
    executor, _, _, queue = build(engine, gateway)
    case = make_case(amount_paise=3_000_000)

    executor.execute(
        Action(case_id=case.id, type=ActionType.ESCALATE_HUMAN),
        case,
        NOW,
        ctx,
        rule_ids=["R30"],
    )

    assert len(queue.tickets) == 1
    ticket = queue.tickets[0]
    assert "R30" in ticket.reason_rule_ids
    assert "30,000" in ticket.brief


def test_a_merchant_fault_alerts_the_merchant_and_spares_the_customer(engine, make_case, ctx):
    gateway = StubGateway()
    executor, _, messenger, queue = build(engine, gateway)
    case = make_case(root_cause=RootCause.MERCHANT_CONFIG)

    executor.execute(Action(case_id=case.id, type=ActionType.ALERT_MERCHANT), case, NOW, ctx)

    assert case.merchant_alerted
    assert messenger.sent == []
    assert queue.tickets[0].kind == "merchant_alert"


def test_a_dispute_costs_the_merchant_a_chargeback_fee(engine, make_case, ctx):
    gateway = StubGateway()
    executor, _, _, _ = build(engine, gateway)
    case = make_case()

    executor.mark_disputed(case, NOW, "test")

    assert case.disputed
    assert case.cost_paise == engine.economics["dispute_cost_paise"]
    # Raising the same dispute twice must not charge twice.
    executor.mark_disputed(case, NOW, "test again")
    assert case.cost_paise == engine.economics["dispute_cost_paise"]
