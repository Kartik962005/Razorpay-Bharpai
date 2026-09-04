"""The last gate before a customer reads something a model wrote."""

from __future__ import annotations

from wapsi.adapters.templates import MessageContext, render
from wapsi.core.models import Channel, Language, RootCause, Scenario, Tone
from wapsi.core.validator import validate

LINK = "https://rzp.io/i/abc123"


def context(**overrides) -> MessageContext:
    defaults = dict(
        merchant_name="Chai Point",
        first_name="Aarav",
        amount_inr=1299.0,
        scenario=Scenario.A,
        root_cause=RootCause.INSUFFICIENT_FUNDS,
        guidance="you can also pay by UPI from another account",
        link=LINK,
        channel=Channel.sms,
        tone=Tone.soft,
        language=Language.en,
        char_limit=320,
    )
    defaults.update(overrides)
    return MessageContext(**defaults)


def test_templates_pass_their_own_guardrails(policy):
    for language in Language:
        for tone in Tone:
            for scenario in Scenario:
                ctx = context(language=language, tone=tone, scenario=scenario)
                text = render(ctx)
                result = validate(text, ctx, policy)
                assert result.ok, (language, tone, scenario, result.failures, text)


def test_templates_fit_an_sms(policy):
    for language in Language:
        for scenario in Scenario:
            ctx = context(language=language, scenario=scenario)
            assert len(render(ctx)) <= 320


def test_threatening_language_is_rejected(policy):
    ctx = context()
    text = (
        f"Hi Aarav, Chai Point payment of ₹1,299 is pending. Pay now or we will take legal action. "
        f"{LINK} Reply STOP to opt out."
    )
    result = validate(text, ctx, policy)
    assert not result.ok
    assert any("legal action" in f for f in result.failures)


def test_credit_bureau_threats_are_rejected(policy):
    ctx = context()
    text = f"Hi Aarav, Chai Point ₹1,299 unpaid. This will affect your CIBIL score. {LINK} STOP to opt out."
    assert not validate(text, ctx, policy).ok


def test_missing_opt_out_line_is_rejected(policy):
    ctx = context()
    text = f"Hi Aarav, your Chai Point payment of ₹1,299 failed. Pay here: {LINK}"
    result = validate(text, ctx, policy)
    assert not result.ok
    assert any("opt-out" in f for f in result.failures)


def test_missing_amount_or_link_is_rejected(policy):
    ctx = context()
    no_amount = f"Hi Aarav, your Chai Point payment failed. Pay here: {LINK} Reply STOP to opt out."
    no_link = "Hi Aarav, your Chai Point payment of ₹1,299 failed. Reply STOP to opt out."

    assert any("amount" in f for f in validate(no_amount, ctx, policy).failures)
    assert any("link" in f for f in validate(no_link, ctx, policy).failures)


def test_missing_merchant_name_is_rejected(policy):
    ctx = context()
    text = f"Hi Aarav, your payment of ₹1,299 failed. Pay here: {LINK} Reply STOP to opt out."
    assert any("merchant" in f for f in validate(text, ctx, policy).failures)


def test_overlong_sms_is_rejected(policy):
    ctx = context()
    filler = "Please complete your payment at the earliest convenience. " * 8
    text = f"Hi Aarav, Chai Point ₹1,299. {filler} {LINK} Reply STOP to opt out."
    result = validate(text, ctx, policy)
    assert not result.ok
    assert any("too long" in f for f in result.failures)


def test_a_plain_amount_without_a_comma_is_still_accepted(policy):
    ctx = context()
    text = f"Hi Aarav, your Chai Point payment of Rs 1299 failed. Pay here: {LINK} Reply STOP to opt out."
    assert validate(text, ctx, policy).ok


def test_hinglish_template_reads_like_a_real_message(policy):
    ctx = context(language=Language.hinglish)
    text = render(ctx)
    assert "STOP" in text
    assert "Chai Point" in text
    assert "1,299" in text
