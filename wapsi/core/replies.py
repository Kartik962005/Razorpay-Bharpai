"""Reading what the customer wrote back.

A model does the reading, because these replies are short, Hinglish and ambiguous. But two
intents — "stop messaging me" and "I am disputing this" — are hard stops, and a hard stop that
depends on a model getting it right is not a hard stop. Those are matched by pattern as well, and
the pattern wins: the system may over-detect an opt-out, never under-detect one.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from wapsi.core.models import ReplyIntent

# Patterns are deliberately broad. A false opt-out costs one recoverable payment; a missed one is
# a customer being messaged after they asked us to stop.
HARD_STOP_PATTERNS: dict[ReplyIntent, re.Pattern[str]] = {
    ReplyIntent.opt_out: re.compile(
        r"\b(stop|unsubscribe|opt.?out|band karo|band kar|mat bhejo|na bhejo|"
        r"do ?n[o']?t (contact|message|text)|remove me)\b",
        re.IGNORECASE,
    ),
    ReplyIntent.dispute: re.compile(
        r"\b(dispute|charge ?back|fraud|fraudulent|unauthori[sz]ed|galat charge|"
        r"complaint (with|to) (my )?bank|bank me complaint|raising (a )?dispute)\b",
        re.IGNORECASE,
    ),
}

SOFT_PATTERNS: dict[ReplyIntent, re.Pattern[str]] = {
    ReplyIntent.paid_claim: re.compile(
        r"\b(already paid|have paid|paid (kar )?d(i|iy)a|payment (kar )?d(i|iy)a|ho ?gaya|done)\b",
        re.IGNORECASE,
    ),
    ReplyIntent.promise_to_pay: re.compile(
        r"\b(will pay|i'?ll pay|pay by|kar dunga|kar dungi|bhej dunga|bhej dungi|"
        r"tak|kal|parso|agle hafte|next week|friday|monday|tuesday|wednesday|thursday|"
        r"saturday|sunday|give me (till|until))\b",
        re.IGNORECASE,
    ),
    ReplyIntent.complaint: re.compile(
        r"\b(haras|pareshan|too many messages|bahut messages|spam|stop bothering)\b",
        re.IGNORECASE,
    ),
    ReplyIntent.question: re.compile(
        r"(\?|\bwhich order\b|\bwhat is this\b|\bkaunsa\b|\bkis cheez\b|\bkya hai\b)",
        re.IGNORECASE,
    ),
}

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def hard_stop_intent(text: str) -> ReplyIntent | None:
    """Opt-out and dispute, matched without a model in the loop."""

    for intent, pattern in HARD_STOP_PATTERNS.items():
        if pattern.search(text):
            return intent
    return None


def regex_intent(text: str) -> ReplyIntent:
    """Best-effort classification with no model available."""

    hard = hard_stop_intent(text)
    if hard is not None:
        return hard
    for intent, pattern in SOFT_PATTERNS.items():
        if pattern.search(text):
            return intent
    return ReplyIntent.other


def regex_promise_date(text: str, now: datetime) -> datetime | None:
    """Resolve the handful of date expressions that actually turn up in these replies."""

    lowered = text.lower()
    if re.search(r"\bkal\b|\btomorrow\b", lowered):
        return now + timedelta(days=1)
    if re.search(r"\bparso\b", lowered):
        return now + timedelta(days=2)
    if re.search(r"\bagle hafte\b|\bnext week\b", lowered):
        return now + timedelta(days=7)
    for name, index in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", lowered):
            ahead = (index - now.weekday()) % 7 or 7
            return now + timedelta(days=ahead)
    return None


def interpret(
    text: str, now: datetime, llm: Any | None = None
) -> tuple[ReplyIntent, datetime | None, float, bool]:
    """Classify a reply. Returns ``(intent, promise_date, confidence, used_model)``.

    A hard stop found by pattern overrides whatever the model concluded, so no model error can
    keep a customer in a recovery sequence after they have asked to leave it.
    """

    hard = hard_stop_intent(text)
    intent = ReplyIntent.other
    promise_at: datetime | None = None
    confidence = 0.5
    used_model = False

    if llm is not None and getattr(llm, "enabled", False):
        parsed = llm.parse_reply(text, now.strftime("%Y-%m-%d"))
        if parsed:
            used_model = True
            try:
                intent = ReplyIntent(parsed.get("intent", "other"))
            except ValueError:
                intent = ReplyIntent.other
            confidence = float(parsed.get("confidence") or 0.5)
            raw_date = parsed.get("promise_date")
            if raw_date:
                try:
                    parsed_day = datetime.strptime(str(raw_date), "%Y-%m-%d")
                    promise_at = parsed_day.replace(hour=12, tzinfo=now.tzinfo)
                except ValueError:
                    promise_at = None

    if not used_model:
        intent = regex_intent(text)
        confidence = 0.6 if intent is not ReplyIntent.other else 0.3

    if intent is ReplyIntent.promise_to_pay and promise_at is None:
        promise_at = regex_promise_date(text, now)

    if hard is not None and intent is not hard:
        # The pattern is the authority here, whatever the model decided.
        intent, confidence = hard, 1.0
        promise_at = None

    return intent, promise_at, confidence, used_model
