"""Guardrails on every outbound message (rule R40).

A language model writes most customer messages, so something deterministic has to stand between
it and the customer. This module is that gate: it does not improve messages, it only refuses
them. A refusal is not a failure mode — the template is used instead and the run continues.

The banned list encodes the RBI fair practices code: no threats, no legal or credit-bureau
claims, no impersonating a recovery agent. The jargon rule exists because a model that is told
the diagnosis will cheerfully print the internal label for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import Any

from wapsi.adapters.templates import MessageContext

#: Internal identifiers: enum members, error codes, rule ids. A customer should never see one.
JARGON = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")


@cache
def _banned(phrase: str) -> re.Pattern[str]:
    """Compile one banned phrase into a whole-word matcher.

    Substring matching read "courtesy" as a threat of "court", and the Hinglish "fir se"
    (*again*) as an FIR — a police complaint. Word boundaries fix the first. The second needs
    case as well, since the threat is an initialism and the ordinary word is not, so an
    all-caps phrase in ``policy.yaml`` is matched case-sensitively and everything else is not.
    """

    phrase = phrase.strip()
    flags = 0 if phrase.isupper() else re.IGNORECASE
    return re.compile(rf"\b{re.escape(phrase)}\b", flags)


@dataclass
class ValidationResult:
    ok: bool
    failures: list[str]

    def __bool__(self) -> bool:
        return self.ok


def _amount_variants(amount_inr: float) -> list[str]:
    plain = f"{amount_inr:,.0f}".replace(",", "")
    return [f"{amount_inr:,.0f}", plain]


def validate(text: str, ctx: MessageContext, policy: dict[str, Any]) -> ValidationResult:
    """Check a message against the content rules. Returns every failure, not just the first."""

    rules = policy["validator"]
    failures: list[str] = []
    lowered = text.lower()

    for pattern in rules["banned_patterns"]:
        if _banned(pattern).search(text):
            failures.append(f"banned phrase: {pattern.strip()!r}")

    required = set(rules["required"])
    if "merchant_name" in required and ctx.merchant_name.lower() not in lowered:
        failures.append("missing merchant name")
    if "amount" in required and not any(v in text for v in _amount_variants(ctx.amount_inr)):
        failures.append("missing amount")
    if "link" in required and ctx.link and ctx.link not in text:
        failures.append("missing payment link")
    if "opt_out_line" in required and "stop" not in lowered:
        failures.append("missing opt-out line")

    leaked = JARGON.findall(text)
    if leaked:
        failures.append(f"internal label in customer text: {leaked[0]}")

    limit = rules["max_chars"].get(ctx.channel.value)
    if limit and len(text) > limit:
        failures.append(f"too long for {ctx.channel.value}: {len(text)} > {limit}")

    # Personal data beyond a first name and the amount has no business in a recovery message.
    if ctx.first_name and text.count(ctx.first_name) > 2:
        failures.append("repeats personal name excessively")

    return ValidationResult(ok=not failures, failures=failures)
