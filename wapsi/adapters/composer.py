"""Turning a decision into words.

Kept behind an interface so a language model can be swapped in without the executor knowing.
The template composer below is not a placeholder: it is the fallback whenever a written message
fails the guardrails, and the whole system runs on it when no model is configured.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from wapsi.adapters.templates import MessageContext, render
from wapsi.core.models import Action, Case, Channel, Language, RootCause, Tone
from wapsi.core.taxonomy import CAUSE_GUIDANCE


class Composed:
    """A message and the story of how it was produced."""

    def __init__(self, text: str, *, llm_written: bool = False, fell_back: bool = False,
                 failures: list[str] | None = None, template_id: str | None = None):
        self.text = text
        self.llm_written = llm_written
        self.fell_back = fell_back
        self.failures = failures or []
        self.template_id = template_id


class Composer(Protocol):
    def compose(self, case: Case, action: Action, link: str, now: datetime) -> Composed: ...


def build_context(case: Case, action: Action, link: str, policy: dict) -> MessageContext:
    channel = Channel(action.params.get("channel", Channel.sms.value))
    tone = Tone(action.params.get("tone", Tone.soft.value))
    language = Language(action.params.get("language", Language.en.value))
    cause = case.root_cause or RootCause.UNKNOWN
    return MessageContext(
        merchant_name=case.merchant_name,
        first_name=case.customer_first_name,
        amount_inr=case.amount_inr,
        scenario=case.scenario,
        root_cause=cause,
        guidance=CAUSE_GUIDANCE.get(cause, ""),
        link=link,
        channel=channel,
        tone=tone,
        language=language,
        char_limit=int(policy["validator"]["max_chars"][channel.value]),
    )


class TemplateComposer:
    """Deterministic, always valid, no network."""

    name = "template"

    def __init__(self, policy: dict):
        self.policy = policy

    def compose(self, case: Case, action: Action, link: str, now: datetime) -> Composed:
        from wapsi.core.models import ActionType

        ctx = build_context(case, action, link, self.policy)
        predebit = action.type is ActionType.SEND_PREDEBIT_NOTICE
        # A policy that never diagnosed the failure has nothing specific to advise, so it sends
        # the generic version. That difference is the whole point of diagnosing.
        guidance = not action.params.get("generic")
        return Composed(
            render(ctx, predebit=predebit, guidance=guidance),
            template_id=(
                "predebit" if predebit
                else f"{ctx.scenario.value}:{ctx.language.value}:{ctx.tone.value}"
            ),
        )
