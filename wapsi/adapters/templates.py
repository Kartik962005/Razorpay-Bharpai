"""Deterministic message templates.

These are the fallback whenever a written message fails the guardrails, and they are what runs
when no language model is configured at all. Everything Wapsi claims about recovery must be
reproducible without an API key, so these have to be good enough to stand on their own.

Hinglish here is the register Indian merchants actually use in transactional SMS: Roman script,
everyday Hindi verbs, English nouns for anything financial.
"""

from __future__ import annotations

from dataclasses import dataclass

from wapsi.core.models import Channel, Language, RootCause, Scenario, Tone

OPT_OUT: dict[Language, str] = {
    Language.en: "Reply STOP to opt out.",
    Language.hinglish: "Messages band karne ke liye STOP bhejein.",
}


@dataclass
class MessageContext:
    """Everything a message may reference. Also the contract the guardrails check against."""

    merchant_name: str
    first_name: str
    amount_inr: float
    scenario: Scenario
    root_cause: RootCause
    guidance: str
    link: str
    channel: Channel
    tone: Tone
    language: Language
    char_limit: int
    opt_out_line: str = ""

    def __post_init__(self) -> None:
        if not self.opt_out_line:
            self.opt_out_line = OPT_OUT[self.language]

    @property
    def amount_text(self) -> str:
        return f"₹{self.amount_inr:,.0f}"


# Opening line per scenario, in each language. The tone ladder changes the framing, never the
# facts, and never adds pressure that the RBI fair practices code would object to.
_OPENERS: dict[tuple[Scenario, Language, Tone], str] = {
    (Scenario.A, Language.en, Tone.soft): "your payment of {amount} to {merchant} did not go through",
    (Scenario.A, Language.en, Tone.helpful): "your {amount} payment to {merchant} failed",
    (Scenario.A, Language.en, Tone.firm): "your {amount} payment to {merchant} is still pending",
    (Scenario.A, Language.hinglish, Tone.soft): "{merchant} ka {amount} ka payment complete nahi hua",
    (Scenario.A, Language.hinglish, Tone.helpful): "{merchant} ka {amount} payment fail ho gaya",
    (Scenario.A, Language.hinglish, Tone.firm): "{merchant} ka {amount} payment abhi tak pending hai",
    (Scenario.B, Language.en, Tone.soft): "your order of {amount} at {merchant} is still waiting",
    (Scenario.B, Language.en, Tone.helpful): "your {amount} order at {merchant} is not paid yet",
    (Scenario.B, Language.en, Tone.firm): "your {amount} order at {merchant} is still unpaid",
    (Scenario.B, Language.hinglish, Tone.soft): "{merchant} par aapka {amount} ka order reserve hai",
    (Scenario.B, Language.hinglish, Tone.helpful): "{merchant} ka {amount} order abhi pay nahi hua",
    (Scenario.B, Language.hinglish, Tone.firm): "{merchant} ka {amount} order abhi tak unpaid hai",
    (Scenario.C, Language.en, Tone.soft): "your {amount} subscription renewal at {merchant} did not go through",
    (Scenario.C, Language.en, Tone.helpful): "your {amount} renewal at {merchant} failed",
    (Scenario.C, Language.en, Tone.firm): "your {merchant} subscription of {amount} is unpaid",
    (Scenario.C, Language.hinglish, Tone.soft): "{merchant} ka {amount} subscription renew nahi ho paya",
    (Scenario.C, Language.hinglish, Tone.helpful): "{merchant} ka {amount} renewal fail ho gaya",
    (Scenario.C, Language.hinglish, Tone.firm): "{merchant} ka {amount} subscription pending hai",
    (Scenario.D, Language.en, Tone.soft): "invoice of {amount} from {merchant} is due",
    (Scenario.D, Language.en, Tone.helpful): "invoice of {amount} from {merchant} is past its due date",
    (Scenario.D, Language.en, Tone.firm): "invoice of {amount} from {merchant} remains overdue",
    (Scenario.D, Language.hinglish, Tone.soft): "{merchant} ka {amount} ka invoice due hai",
    (Scenario.D, Language.hinglish, Tone.helpful): "{merchant} ka {amount} invoice due date nikal chuka hai",
    (Scenario.D, Language.hinglish, Tone.firm): "{merchant} ka {amount} invoice abhi tak overdue hai",
}

#: The pre-debit notification. Its job is to state the amount and the date plainly; RBI requires
#: it 24 hours before any mandate debit, and it is the only message here that is not a nudge.
PREDEBIT: dict[Language, str] = {
    Language.en: "Hi {name}, {merchant} will attempt your {amount} auto-pay again tomorrow. Keep your balance topped up, or pay now: {link}",
    Language.hinglish: "Hi {name}, {merchant} kal aapka {amount} ka auto-pay dobara try karega. Balance rakhein, ya abhi pay karein: {link}",
}

_CLOSERS: dict[Language, str] = {
    Language.en: "Pay here: {link}",
    Language.hinglish: "Yahan pay karein: {link}",
}


def render(ctx: MessageContext, *, predebit: bool = False, guidance: bool = True) -> str:
    """Build the message for this context. Always fits the channel's character limit."""

    if predebit:
        text = PREDEBIT[ctx.language].format(
            name=ctx.first_name,
            merchant=ctx.merchant_name,
            amount=ctx.amount_text,
            link=ctx.link,
        )
        return f"{text} {ctx.opt_out_line}"

    key = (ctx.scenario, ctx.language, ctx.tone)
    opener = _OPENERS.get(key) or _OPENERS[(Scenario.A, ctx.language, ctx.tone)]
    greeting = f"Hi {ctx.first_name},"
    body = opener.format(amount=ctx.amount_text, merchant=ctx.merchant_name)
    closer = _CLOSERS[ctx.language].format(link=ctx.link)

    parts = [f"{greeting} {body}."]
    if ctx.guidance and guidance:
        # Only the first letter changes: str.capitalize would turn "UPI" into "upi".
        advice = ctx.guidance[0].upper() + ctx.guidance[1:]
        parts.append(f"{advice}.")
    parts.append(closer)
    parts.append(ctx.opt_out_line)
    text = " ".join(parts)

    if len(text) > ctx.char_limit:
        # Guidance is the first thing to go: the amount, link and opt-out line are required.
        text = " ".join([f"{greeting} {body}.", closer, ctx.opt_out_line])
    return text


def escalation_brief(
    merchant_name: str, amount_inr: float, cause: RootCause, attempts: int, rule_ids: list[str]
) -> str:
    """Fallback brief for the human queue when no language model is available."""

    return (
        f"₹{amount_inr:,.0f} owed to {merchant_name}. Diagnosis: {cause.value}. "
        f"{attempts} automated attempt(s) made, all unsuccessful. "
        f"Escalated under {', '.join(rule_ids) or 'policy'}. "
        "Recommend a human call before writing this off."
    )
