"""The model is optional, bounded and outranked.

Everything here runs with no network. What is being tested is that the system behaves correctly
when the model is absent, broken, or wrong — which is the only way the batch numbers can be
reproduced by someone who has no API key.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from wapsi.adapters.composer import LLMComposer, TemplateComposer
from wapsi.adapters.templates import MessageContext
from wapsi.config import IST, Settings
from wapsi.core.models import (
    Action,
    ActionType,
    Channel,
    Language,
    RootCause,
    ReplyIntent,
    Scenario,
    Tone,
)
from wapsi.core.planner import AgentPlanner, RulesPlanner
from wapsi.core.policy import PolicyContext
from wapsi.core.replies import hard_stop_intent, interpret, regex_intent
from wapsi.core.validator import validate

from tests.conftest import NOW


class DeadLLM:
    """A configured model that fails every call."""

    enabled = True

    def compose_message(self, ctx):
        return None

    def parse_reply(self, text, today):
        return None

    def advise_action(self, case, allowed, denied, replies):
        return None

    def write_brief(self, case, reasons, replies):
        return None


class RogueLLM:
    """A model that answers confidently and wrongly."""

    enabled = True

    def __init__(self, action="RETRY_CHARGE", intent="other"):
        self.action = action
        self.intent = intent

    def compose_message(self, ctx):
        return "Pay immediately or we will take legal action."

    def parse_reply(self, text, today):
        return {"intent": self.intent, "promise_date": None, "confidence": 0.99}

    def advise_action(self, case, allowed, denied, replies):
        return {"action": self.action, "reason": "because I say so"}

    def write_brief(self, case, reasons, replies):
        return "brief"


def test_no_key_means_no_model(monkeypatch):
    from wapsi.adapters.llm import LLM

    llm = LLM(Settings(llm_api_key="", llm_base_url="", llm_model="", llm_model_fast=""))
    assert not llm.enabled
    assert llm.compose_message(None) is None
    assert llm.parse_reply("STOP", "2026-08-03") is None
    assert llm.advise_action(None, [], [], []) is None


def test_a_dead_model_falls_back_to_the_template(engine, make_case):
    case = make_case()
    action = Action(
        case_id=case.id,
        type=ActionType.SEND_PAYMENT_LINK,
        params={"channel": "sms", "tone": "soft", "language": "en"},
    )
    composed = LLMComposer(engine.policy, DeadLLM()).compose(
        case, action, "https://rzp.io/i/x", NOW
    )
    assert composed.text
    assert not composed.llm_written
    assert composed.fell_back


def test_a_threatening_message_is_rejected_by_the_guardrails(engine, make_case):
    """The model is never trusted to have obeyed its own instructions."""

    ctx = MessageContext(
        merchant_name="Chai Point",
        first_name="Aarav",
        amount_inr=1299.0,
        scenario=Scenario.A,
        root_cause=RootCause.INSUFFICIENT_FUNDS,
        guidance="try UPI",
        link="https://rzp.io/i/x",
        channel=Channel.sms,
        tone=Tone.soft,
        language=Language.en,
        char_limit=320,
    )
    text = RogueLLM().compose_message(ctx)
    assert not validate(text, ctx, engine.policy).ok


def test_the_planner_vetoes_an_illegal_proposal(engine, make_case):
    """A blocked instrument must stay blocked however confidently the model recommends it."""

    case = make_case(root_cause=RootCause.INSTRUMENT_BLOCKED, amount_paise=129_900)
    planner = AgentPlanner(engine, RogueLLM(action="RETRY_CHARGE"))
    decision = planner.plan(case, NOW, PolicyContext())

    assert decision.action.type is not ActionType.RETRY_CHARGE
    assert decision.verdict and "denied" in decision.verdict
    assert case.llm_denials == 1


def test_an_invented_action_is_refused(engine, make_case):
    case = make_case(root_cause=RootCause.CUSTOMER_INPUT)
    planner = AgentPlanner(engine, RogueLLM(action="WIRE_TRANSFER_DEMAND"))
    decision = planner.plan(case, NOW, PolicyContext())

    assert decision.action.type in (ActionType.SEND_PAYMENT_LINK, ActionType.OFFER_METHOD_SWITCH)
    assert case.llm_denials == 1


def test_repeated_vetoes_escalate_to_a_human(engine, make_case):
    case = make_case(root_cause=RootCause.CUSTOMER_INPUT, llm_denials=2)
    planner = AgentPlanner(engine, RogueLLM())
    decision = planner.plan(case, NOW, PolicyContext())
    assert decision.action.type is ActionType.ESCALATE_HUMAN
    assert "R34" in decision.rule_ids


def test_a_dead_model_leaves_the_rules_planner_untouched(engine, make_case):
    case = make_case(root_cause=RootCause.CUSTOMER_INPUT)
    baseline = RulesPlanner(engine).plan(case, NOW, PolicyContext())
    advised = AgentPlanner(engine, DeadLLM()).plan(case, NOW, PolicyContext())
    assert advised.action.type is baseline.action.type


@pytest.mark.parametrize(
    "text,expected",
    [
        ("STOP", ReplyIntent.opt_out),
        ("mat bhejo message band karo", ReplyIntent.opt_out),
        ("I am raising a dispute with my bank", ReplyIntent.dispute),
        ("bank me complaint kar raha hoon", ReplyIntent.dispute),
        ("paid kar diya", ReplyIntent.paid_claim),
        ("paisa Friday ko bhej dunga", ReplyIntent.promise_to_pay),
        ("kaunsa order hai ye?", ReplyIntent.question),
    ],
)
def test_replies_are_readable_without_a_model(text, expected):
    assert regex_intent(text) is expected


def test_a_wrong_model_cannot_override_a_hard_stop():
    """The whole point of the pattern layer: a misread opt-out is not survivable."""

    rogue = RogueLLM(intent="question")
    intent, _, confidence, used_model = interpret("STOP", NOW, rogue)

    assert used_model
    assert intent is ReplyIntent.opt_out
    assert confidence == 1.0

    intent, _, _, _ = interpret("this is a fraudulent charge", NOW, rogue)
    assert intent is ReplyIntent.dispute


def test_a_model_reading_is_used_where_no_hard_stop_applies():
    class Reader:
        enabled = True

        def parse_reply(self, text, today):
            return {"intent": "promise_to_pay", "promise_date": "2026-08-07", "confidence": 0.9}

    intent, promise_at, confidence, used_model = interpret("main dekhta hoon", NOW, Reader())
    assert used_model and intent is ReplyIntent.promise_to_pay
    assert promise_at is not None and promise_at.date().isoformat() == "2026-08-07"


def test_relative_dates_resolve_without_a_model():
    from wapsi.core.replies import regex_promise_date

    monday = datetime(2026, 8, 3, 11, 0, tzinfo=IST)
    assert regex_promise_date("kal bhej dunga", monday) == monday + timedelta(days=1)
    assert regex_promise_date("will pay next week", monday) == monday + timedelta(days=7)
    assert regex_promise_date("will pay on Friday", monday).weekday() == 4
    assert regex_promise_date("no date here", monday) is None


def test_hard_stop_patterns_do_not_fire_on_ordinary_text():
    for benign in ("kaunsa order hai ye?", "will pay tomorrow", "already paid this"):
        assert hard_stop_intent(benign) is None


def test_the_template_composer_needs_nothing(engine, make_case):
    case = make_case()
    action = Action(
        case_id=case.id,
        type=ActionType.SEND_PAYMENT_LINK,
        params={"channel": "sms", "tone": "soft", "language": "hinglish"},
    )
    composed = TemplateComposer(engine.policy).compose(case, action, "https://rzp.io/i/x", NOW)
    assert composed.text and not composed.llm_written
