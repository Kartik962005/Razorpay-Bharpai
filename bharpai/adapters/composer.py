"""Turning a decision into words.

Kept behind an interface so a language model can be swapped in without the executor knowing.
The template composer below is not a placeholder: it is the fallback whenever a written message
fails the guardrails, and the whole system runs on it when no model is configured.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from bharpai.adapters.templates import MessageContext, render
from bharpai.core.models import Action, Case, Channel, Language, RootCause, Tone
from bharpai.core.taxonomy import CAUSE_GUIDANCE


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
        from bharpai.core.models import ActionType

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


class LLMComposer(TemplateComposer):
    """Model-written messages, with the template as an always-available floor.

    Two kinds of message are deliberately never model-written: the pre-debit notice, whose
    wording is a regulatory statement rather than persuasion, and the generic reminder used by
    the undiagnosed baseline, which has nothing specific to say by definition.
    """

    name = "llm"

    def __init__(self, policy: dict, llm):
        super().__init__(policy)
        self.llm = llm

    def compose(self, case: Case, action: Action, link: str, now: datetime) -> Composed:
        from bharpai.core.models import ActionType

        if action.type is ActionType.SEND_PREDEBIT_NOTICE or action.params.get("generic"):
            return super().compose(case, action, link, now)

        ctx = build_context(case, action, link, self.policy)
        text = self.llm.compose_message(ctx)
        if text:
            return Composed(text, llm_written=True)

        composed = super().compose(case, action, link, now)
        composed.fell_back = True
        return composed

    def stats(self) -> dict:
        return self.llm.stats.as_dict()
