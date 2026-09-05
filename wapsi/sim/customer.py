"""The hidden truth about customers.

The agent never sees anything in this module. It observes only what Razorpay would expose: an
error code, an amount, a timestamp, and whether money arrived. Everything else — how much cash
someone has, how patient they are, whether they would have paid anyway — lives here and is used
solely to decide what actually happens.

The behaviour numbers come from ``sim/config.yaml`` and are deliberately shaped differently from
the planner's priors in ``core/taxonomy.py``. If the two agreed, the batch would only be proving
that the agent can read its own notes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from wapsi.core.models import (
    ActionType,
    Case,
    Channel,
    Language,
    ReplyIntent,
    RootCause,
    Scenario,
    Tone,
)
from wapsi.sim.world import World

# What a person would call unreasonable hours. Note this is *wider* than the policy's own
# 10:00-21:00 window: the agent gets no credit merely for obeying its own rules, only for
# avoiding contact that genuinely annoys someone.
REASONABLE_START, REASONABLE_END = 8, 22

REPLY_TEXTS: dict[ReplyIntent, dict[Language, list[str]]] = {
    ReplyIntent.paid_claim: {
        Language.en: ["already paid this", "I have paid it just now"],
        Language.hinglish: ["paid kar diya", "abhi payment kar diya hai"],
    },
    ReplyIntent.promise_to_pay: {
        Language.en: ["will pay on Friday", "please give me till next week"],
        Language.hinglish: ["paisa Friday ko bhej dunga", "agle hafte tak kar dunga"],
    },
    ReplyIntent.opt_out: {
        Language.en: ["STOP", "stop messaging me"],
        Language.hinglish: ["STOP", "mat bhejo message band karo"],
    },
    ReplyIntent.dispute: {
        Language.en: ["I am raising a dispute with my bank", "this is a fraudulent charge"],
        Language.hinglish: ["bank me complaint kar raha hoon", "ye charge galat hai"],
    },
    ReplyIntent.question: {
        Language.en: ["which order is this for?", "what is this payment about"],
        Language.hinglish: ["kaunsa order hai ye?", "ye kis cheez ka payment hai"],
    },
    ReplyIntent.complaint: {
        Language.en: ["stop harassing me, I will complain", "this is too many messages"],
        Language.hinglish: ["bahut messages aa rahe hain, band karo", "pareshan mat karo"],
    },
}


@dataclass
class HiddenCustomer:
    """Traits that persist across every case this person has."""

    customer_id: str
    liquidity: str
    intent: float
    channel_pref: Channel
    language_pref: Language
    opt_out_threshold: int
    promise_reliability: float
    contacts: int = 0
    annoyance: float = 0.0
    opted_out: bool = False


@dataclass
class HiddenCase:
    """Truth about one case that the agent cannot observe."""

    case_id: str
    organic_pay_at: datetime | None = None
    merchant_fix_at: datetime | None = None
    contacted: bool = False


@dataclass
class Reaction:
    """What the world does in response to one action."""

    paid: bool = False
    delay: timedelta = timedelta(0)
    reply_intent: ReplyIntent | None = None
    reply_text: str = ""
    promise_at: datetime | None = None
    opted_out: bool = False
    disputed: bool = False
    complained: bool = False


@dataclass
class Population:
    """Every hidden customer and case in the batch."""

    config: dict[str, Any]
    customers: dict[str, HiddenCustomer] = field(default_factory=dict)
    cases: dict[str, HiddenCase] = field(default_factory=dict)

    def customer(self, customer_id: str) -> HiddenCustomer:
        return self.customers[customer_id]

    def hidden(self, case_id: str) -> HiddenCase:
        return self.cases[case_id]

    def reset_dynamic_state(self) -> None:
        """Clear everything a run mutates, so each policy starts from identical conditions."""

        for customer in self.customers.values():
            customer.contacts = 0
            customer.annoyance = 0.0
            customer.opted_out = False
        for hidden in self.cases.values():
            hidden.contacted = False


def _is_out_of_hours(moment: datetime) -> bool:
    hour = moment.hour
    return not (REASONABLE_START <= hour < REASONABLE_END)


class CustomerModel:
    """Decides whether a given action actually gets a given customer to pay."""

    def __init__(self, population: Population, world: World):
        self.population = population
        self.world = world
        self.behaviour = world.config["behaviour"]
        self.modifiers = world.config["modifiers"]
        self.replies = world.config["replies"]

    # -- probability --------------------------------------------------------------------------

    def _base_probability(self, case: Case, action_type: ActionType, now: datetime) -> float:
        cause = (case.root_cause or RootCause.UNKNOWN).value
        table = self.behaviour.get(cause, {})
        entry = table.get(action_type.value)
        if entry is None:
            return 0.0
        if isinstance(entry, (int, float)):
            return float(entry)

        hours = (now - case.created_at).total_seconds() / 3600
        attempts = case.attempts_of(action_type)

        if cause == RootCause.TRANSIENT_TECH.value:
            active = self.world.downtime_active(now, case.method.value)
            return float(entry["during_outage"] if active else entry["base"])
        if cause == RootCause.INSUFFICIENT_FUNDS.value:
            if self.world.is_salary_window(now):
                return float(entry["salary_window"])
            return float(entry["within_12h"] if hours < 12 else entry["within_72h"])
        if cause == RootCause.LIMIT_EXCEEDED.value:
            return float(entry["next_day"] if hours >= 20 else entry["same_day"])
        if cause == RootCause.CUSTOMER_ABANDON.value:
            if hours <= 1:
                return float(entry["within_1h"])
            return float(entry["within_24h"] if hours <= 24 else entry["later"])
        if cause == RootCause.CUSTOMER_INPUT.value:
            # Wapsi always sends cause-specific guidance; the naive baseline never does.
            return float(entry["with_guidance"])
        if cause == RootCause.ABANDONED_CHECKOUT.value:
            if attempts == 0:
                return float(entry["first"])
            return float(entry["second"] if attempts == 1 else entry["later"])
        if cause == RootCause.OVERDUE_RECEIVABLE.value and action_type is ActionType.SEND_REMINDER:
            return float(entry["pay"])
        return 0.0

    def probability(
        self,
        case: Case,
        action_type: ActionType,
        now: datetime,
        *,
        channel: Channel | None = None,
        tone: Tone | None = None,
        language: Language | None = None,
        guidance: bool = True,
    ) -> float:
        """P(this customer pays because of this action, taken now)."""

        customer = self.population.customer(case.customer_id)
        cause = case.root_cause or RootCause.UNKNOWN

        # Nothing aimed at the customer can fix the merchant's own configuration. A retry after
        # they have repaired it, however, simply works.
        if cause is RootCause.MERCHANT_CONFIG:
            hidden = self.population.hidden(case.id)
            fixed = hidden.merchant_fix_at is not None and now >= hidden.merchant_fix_at
            if action_type is not ActionType.RETRY_CHARGE or not fixed:
                return 0.0
            after_fix = float(self.behaviour[cause.value]["retry_after_fix"])
            return max(0.0, min(1.0, after_fix * self.world.behaviour_scale))

        probability = self._base_probability(case, action_type, now) * self.world.behaviour_scale
        if probability <= 0:
            return 0.0
        if cause in (RootCause.INSUFFICIENT_FUNDS, RootCause.LIMIT_EXCEEDED):
            probability *= float(self.modifiers["liquidity"][customer.liquidity])

        if action_type is not ActionType.RETRY_CHARGE:
            probability *= customer.intent
            if channel is not None and channel is customer.channel_pref:
                probability *= float(self.modifiers["channel_match"])
            if language is not None and language is customer.language_pref:
                probability *= float(self.modifiers["language_match"])
            if tone is Tone.firm:
                probability *= float(self.modifiers["tone_firm_pay"])
            if _is_out_of_hours(now):
                probability *= float(self.modifiers["out_of_hours"]["pay"])
            if cause is RootCause.CUSTOMER_INPUT and not guidance:
                entry = self.behaviour[cause.value][ActionType.SEND_PAYMENT_LINK.value]
                probability *= float(entry["without_guidance"]) / float(entry["with_guidance"])

        probability *= float(self.modifiers["attempt_decay"]) ** case.attempts_of(action_type)
        return max(0.0, min(1.0, probability))

    # -- reaction -----------------------------------------------------------------------------

    def react(
        self,
        case: Case,
        action_type: ActionType,
        now: datetime,
        *,
        channel: Channel | None = None,
        tone: Tone | None = None,
        language: Language | None = None,
        guidance: bool = True,
    ) -> Reaction:
        """Resolve one action against the hidden state, and update that state."""

        customer = self.population.customer(case.customer_id)
        attempt = case.attempts_of(action_type)
        rng = self.world.rng(case.id, action_type.value, attempt)

        if action_type is ActionType.RETRY_CHARGE:
            return self._react_retry(case, now, rng)

        return self._react_contact(
            case,
            action_type,
            now,
            rng,
            customer,
            channel=channel,
            tone=tone,
            language=language,
            guidance=guidance,
        )

    def _react_retry(self, case: Case, now: datetime, rng) -> Reaction:
        cause = case.root_cause or RootCause.UNKNOWN

        # NPCI execution windows are enforced by the rails, not by us: a mandate attempted in
        # the peak simply fails, however sensible the attempt looked.
        if (
            case.scenario is Scenario.C
            and self.world.in_npci_peak(now)
            and rng.random() < self.world.npci_peak_failure_probability
        ):
            return Reaction(paid=False)

        probability = self.probability(case, ActionType.RETRY_CHARGE, now)
        if rng.random() < probability:
            return Reaction(paid=True)

        reaction = Reaction(paid=False)
        if cause is RootCause.RISK_DECLINE:
            spec = self.behaviour[RootCause.RISK_DECLINE.value]
            if rng.random() < float(spec["dispute_probability_per_attempt"]):
                # Hammering a risk decline is how a merchant turns a failure into a chargeback.
                reaction.disputed = True
        return reaction

    def _react_contact(
        self,
        case: Case,
        action_type: ActionType,
        now: datetime,
        rng,
        customer: HiddenCustomer,
        *,
        channel: Channel | None,
        tone: Tone | None,
        language: Language | None,
        guidance: bool,
    ) -> Reaction:
        customer.contacts += 1
        if customer.opted_out:
            # They have already told this merchant to stop. Further messages are ignored, which
            # is the cost a policy pays for continuing to send them.
            return Reaction(delay=self._reply_delay(now, rng))

        annoyance_spec = self.modifiers["annoyance"]
        increment = float(annoyance_spec["per_contact"])
        if tone is Tone.firm:
            increment *= float(annoyance_spec["firm_multiplier"])
        if _is_out_of_hours(now):
            increment *= float(self.modifiers["out_of_hours"]["annoyance"])
        customer.annoyance += increment

        reaction = Reaction()
        reaction.delay = self._reply_delay(now, rng)

        probability = self.probability(
            case,
            action_type,
            now,
            channel=channel,
            tone=tone,
            language=language,
            guidance=guidance,
        )
        if rng.random() < probability:
            reaction.paid = True
            return reaction

        # Overdue invoices mostly produce words rather than money.
        if (
            case.root_cause is RootCause.OVERDUE_RECEIVABLE
            and action_type is ActionType.SEND_REMINDER
        ):
            spec = self.behaviour[RootCause.OVERDUE_RECEIVABLE.value][ActionType.SEND_REMINDER.value]
            roll = rng.random()
            if roll < float(spec["promise"]):
                days = rng.randint(2, 7)
                reaction.reply_intent = ReplyIntent.promise_to_pay
                reaction.promise_at = now + timedelta(days=days)
                reaction.reply_text = self._reply_text(ReplyIntent.promise_to_pay, customer, rng)
                return reaction

        if customer.contacts >= customer.opt_out_threshold:
            customer.opted_out = True
            reaction.opted_out = True
            reaction.reply_intent = ReplyIntent.opt_out
            reaction.reply_text = self._reply_text(ReplyIntent.opt_out, customer, rng)
            return reaction

        if customer.annoyance >= float(annoyance_spec["dispute_threshold"]):
            reaction.disputed = True
            reaction.reply_intent = ReplyIntent.dispute
            reaction.reply_text = self._reply_text(ReplyIntent.dispute, customer, rng)
            return reaction

        if customer.annoyance >= float(annoyance_spec["complaint_threshold"]):
            reaction.complained = True
            reaction.reply_intent = ReplyIntent.complaint
            reaction.reply_text = self._reply_text(ReplyIntent.complaint, customer, rng)
            return reaction

        if _is_out_of_hours(now) and rng.random() < float(self.modifiers["out_of_hours"]["dispute"]):
            reaction.disputed = True
            reaction.reply_intent = ReplyIntent.dispute
            reaction.reply_text = self._reply_text(ReplyIntent.dispute, customer, rng)
            return reaction

        if rng.random() < float(self.replies["probability"]):
            reaction.reply_intent = ReplyIntent.question
            reaction.reply_text = self._reply_text(ReplyIntent.question, customer, rng)

        return reaction

    def _reply_text(self, intent: ReplyIntent, customer: HiddenCustomer, rng) -> str:
        return rng.choice(REPLY_TEXTS[intent][customer.language_pref])

    def _reply_delay(self, now: datetime, rng) -> timedelta:
        median = float(self.replies["latency_hours_median"])
        sigma = float(self.replies["latency_sigma"])
        hours = median * math.exp(sigma * rng.gauss(0, 1))
        hours = max(0.25, min(48.0, hours))
        moment = now + timedelta(hours=hours)
        return self.world.next_waking_moment(moment) - now

    def keeps_promise(self, case: Case, rng=None) -> bool:
        customer = self.population.customer(case.customer_id)
        rng = rng or self.world.rng(case.id, "promise", case.promises_broken)
        return rng.random() < customer.promise_reliability

    def human_resolves(self, case: Case) -> tuple[bool, timedelta]:
        """Whether a human agent closes an escalated case, and how long they take."""

        spec = self.behaviour[RootCause.OVERDUE_RECEIVABLE.value]
        rng = self.world.rng(case.id, "human")
        delay = timedelta(hours=float(spec["human_close_delay_hours"]))
        return rng.random() < float(spec["human_close_probability"]), delay
