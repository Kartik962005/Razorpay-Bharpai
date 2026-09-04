"""Recovery has to be worth more than it costs, or it is just noise the merchant pays for."""

from __future__ import annotations

from wapsi.core.models import Action, ActionType, Channel, RootCause

from tests.conftest import NOW


def test_tiny_amounts_are_not_worth_a_message(make_case, denials):
    trivial = make_case(amount_paise=3_000)
    assert "R24" in denials(trivial, ActionType.SEND_PAYMENT_LINK)

    worthwhile = make_case(amount_paise=50_000)
    assert "R24" not in denials(worthwhile, ActionType.SEND_PAYMENT_LINK)


def test_tiny_amounts_can_still_be_retried_silently(make_case, denials):
    """A retry costs a fraction of a rupee, so the economic floor should not block it."""

    trivial = make_case(amount_paise=3_000, root_cause=RootCause.TRANSIENT_TECH)
    assert "R24" not in denials(trivial, ActionType.RETRY_CHARGE)


def test_channel_costs_come_from_policy(engine, make_case):
    case = make_case()
    for channel, expected in [
        (Channel.email, 2),
        (Channel.sms, 20),
        (Channel.whatsapp, 80),
        (Channel.voice_stub, 300),
    ]:
        action = Action(
            case_id=case.id,
            type=ActionType.SEND_PAYMENT_LINK,
            params={"channel": channel.value},
        )
        assert engine.cost_of(action) == expected


def test_retries_and_escalations_are_priced(engine, make_case):
    case = make_case()
    retry = Action(case_id=case.id, type=ActionType.RETRY_CHARGE)
    escalate = Action(case_id=case.id, type=ActionType.ESCALATE_HUMAN)
    wait = Action(case_id=case.id, type=ActionType.WAIT)

    assert engine.cost_of(retry) == 5
    assert engine.cost_of(escalate) == 5_000
    assert engine.cost_of(wait) == 0


def test_expected_value_beats_cost_for_a_healthy_case(engine, make_case, ctx):
    """The planner's rule is EV > cost; check the inputs make that possible at all."""

    from wapsi.core.taxonomy import prior

    case = make_case(root_cause=RootCause.CUSTOMER_INPUT, amount_paise=129_900)
    allowed, _ = engine.allowed(case, NOW, ctx)
    assert allowed, "a fixable input error should have at least one legal action"

    best = max(allowed, key=lambda a: prior(case, a.type, NOW) * case.amount_paise - a.cost_paise)
    ev = prior(case, best.type, NOW) * case.amount_paise - best.cost_paise
    assert ev > 0


def test_allowed_actions_are_priced_when_enumerated(engine, make_case, ctx):
    case = make_case(root_cause=RootCause.CUSTOMER_INPUT)
    allowed, _ = engine.allowed(case, NOW, ctx)
    for action in allowed:
        assert action.cost_paise >= 0
        assert action.params.get("channel") in {c.value for c in Channel} or action.type not in (
            ActionType.SEND_PAYMENT_LINK,
            ActionType.SEND_REMINDER,
            ActionType.OFFER_METHOD_SWITCH,
            ActionType.REQUEST_REAUTH,
        )
