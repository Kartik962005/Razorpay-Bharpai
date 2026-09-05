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


class HeaderStub:
    """Mimics the rate-limit headers a provider returns, so pacing can be tested offline."""

    def __init__(self, remaining, reset="5s"):
        self._h = {
            "x-ratelimit-remaining-tokens": str(remaining),
            "x-ratelimit-reset-tokens": reset,
        }

    def get(self, key):
        return self._h.get(key)


@pytest.mark.parametrize(
    "value,seconds",
    [("1m26.4s", 86.4), ("7.66s", 7.66), ("2m", 120.0), ("1h30m", 5400.0), ("", 60.0), (None, 60.0)],
)
def test_reset_durations_are_parsed(value, seconds):
    from wapsi.adapters.llm import _parse_duration

    assert _parse_duration(value) == pytest.approx(seconds)


def test_an_unparseable_reset_falls_back_to_a_minute():
    from wapsi.adapters.llm import _parse_duration

    assert _parse_duration("soon") == 60.0


def test_headroom_is_read_from_the_response():
    """Free tiers meter tokens per minute. Pacing by request count is what produced hundreds of
    refusals, so the adapter reads what the provider says is actually left."""

    from wapsi.adapters.llm import LLM
    from wapsi.config import Settings

    llm = LLM(Settings(llm_api_key="k", llm_base_url="http://x", llm_model="m", llm_model_fast="m"),
              cache=False)
    llm._note_headroom(HeaderStub(6200, "9.5s"))
    assert llm._tokens_left == 6200
    assert llm._window_resets_at > 0


def test_pacing_waits_when_the_window_is_nearly_spent(monkeypatch):
    from wapsi.adapters import llm as llm_mod
    from wapsi.adapters.llm import LLM
    from wapsi.config import Settings

    slept = []
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: slept.append(s))

    llm = LLM(Settings(llm_api_key="k", llm_base_url="http://x", llm_model="m", llm_model_fast="m"),
              cache=False)

    llm._note_headroom(HeaderStub(7000))   # plenty left
    llm._pace()
    assert not any(s > 1 for s in slept), "no long wait while there is headroom"

    slept.clear()
    llm._note_headroom(HeaderStub(200, "8s"))   # nearly spent
    llm._pace()
    assert any(s > 1 for s in slept), "must wait for the window rather than spend a call on a 429"
    assert llm.stats.throttled_seconds > 0


def test_missing_headers_do_not_break_pacing():
    from wapsi.adapters.llm import LLM
    from wapsi.config import Settings

    llm = LLM(Settings(llm_api_key="k", llm_base_url="http://x", llm_model="m", llm_model_fast="m"),
              cache=False)

    class Empty:
        def get(self, key):
            return None

    llm._note_headroom(Empty())
    assert llm._tokens_left is None
    llm._pace()  # must not raise
