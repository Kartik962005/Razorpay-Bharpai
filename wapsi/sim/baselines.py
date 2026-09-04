"""The two policies Wapsi has to beat, implemented honestly.

``do_nothing`` is what happens with no agent at all. For one-off payments that means nothing
happens; for subscriptions it means Razorpay's own T+1/T+2/T+3 retry ladder still runs, because
it would. Those platform retries are tagged so the report does not blame the platform for
breaking rules that apply to us.

``naive`` is what a merchant's own automation usually looks like: retry three times immediately,
then send one SMS an hour later, and chase invoices by email forever. It ignores causes, hours,
opt-outs and risk declines — which is exactly why it is worth measuring.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from wapsi.core.models import Action, ActionType, Case, Channel, Language, Outcome, Tone
from wapsi.core.planner import Decision
from wapsi.core.policy import PolicyContext, PolicyEngine

#: Razorpay retries a failed subscription charge on the next three days.
PLATFORM_LADDER = (timedelta(days=1), timedelta(days=2), timedelta(days=3))

#: What untuned merchant automation does: hammer, then text.
NAIVE_RETRY_OFFSETS = (timedelta(0), timedelta(minutes=5), timedelta(minutes=15))
NAIVE_SMS_DELAY = timedelta(hours=1)
NAIVE_INVOICE_INTERVAL = timedelta(days=3)


def _close(case: Case, now: datetime, outcome: Outcome, reason: str) -> Decision:
    return Decision(
        Action(
            case_id=case.id,
            type=ActionType.CLOSE,
            params={"outcome": outcome.value},
            scheduled_at=now,
        ),
        [],
        reason,
    )


def _wait(case: Case, until: datetime, reason: str) -> Decision:
    return Decision(
        Action(case_id=case.id, type=ActionType.WAIT, scheduled_at=until, params={}),
        [],
        reason,
    )


class DoNothingPlanner:
    """No recovery effort. The baseline every other number is measured against."""

    name = "do_nothing"

    def __init__(self, policy: PolicyEngine):
        self.policy = policy

    def plan(self, case: Case, now: datetime, ctx: PolicyContext) -> Decision:
        deadline = case.created_at + timedelta(days=self.policy.max_age_days(case))

        from wapsi.core.models import Scenario

        if case.scenario is Scenario.C and case.retries < len(PLATFORM_LADDER):
            due = case.created_at + PLATFORM_LADDER[case.retries]
            if now >= due:
                action = Action(
                    case_id=case.id,
                    type=ActionType.RETRY_CHARGE,
                    scheduled_at=now,
                    params={"platform": True},
                )
                action.cost_paise = self.policy.cost_of(action)
                return Decision(action, [], "platform retry ladder (T+%d)" % (case.retries + 1))
            return _wait(case, due, "waiting for the platform's next scheduled retry")

        if now >= deadline:
            return _close(case, now, Outcome.gave_up, "no recovery attempted")
        return _wait(case, deadline, "no recovery attempted")


class NaivePlanner:
    """Cause-blind, clock-blind, consent-blind. Common, and expensive."""

    name = "naive"

    def __init__(self, policy: PolicyEngine):
        self.policy = policy

    def plan(self, case: Case, now: datetime, ctx: PolicyContext) -> Decision:
        from wapsi.core.models import Scenario

        deadline = case.created_at + timedelta(days=self.policy.max_age_days(case))

        if case.scenario is Scenario.D:
            due = (
                case.last_contact_at + NAIVE_INVOICE_INTERVAL
                if case.last_contact_at
                else case.created_at
            )
            if now >= deadline:
                return _close(case, now, Outcome.gave_up, "invoice chase gave up at the deadline")
            if now >= due:
                return Decision(self._reminder(case, now, Channel.email), [], "scheduled invoice chase")
            return _wait(case, due, "next invoice chase")

        if case.retries < len(NAIVE_RETRY_OFFSETS):
            due = case.created_at + NAIVE_RETRY_OFFSETS[case.retries]
            if now >= due:
                action = Action(case_id=case.id, type=ActionType.RETRY_CHARGE, scheduled_at=now)
                action.cost_paise = self.policy.cost_of(action)
                return Decision(action, [], f"retry {case.retries + 1} of 3, same instrument")
            return _wait(case, due, "next fixed retry")

        if case.nudges < 1:
            due = case.created_at + NAIVE_SMS_DELAY
            if now >= due:
                return Decision(self._reminder(case, now, Channel.sms), [], "one SMS after an hour")
            return _wait(case, due, "waiting an hour before the SMS")

        if now >= deadline:
            return _close(case, now, Outcome.gave_up, "gave up at the deadline")
        return _wait(case, deadline, "nothing further planned")

    def _reminder(self, case: Case, now: datetime, channel: Channel) -> Action:
        action = Action(
            case_id=case.id,
            type=ActionType.SEND_REMINDER,
            scheduled_at=now,
            params={
                "channel": channel.value,
                "tone": Tone.firm.value,
                "language": Language.en.value,
                "generic": True,
            },
        )
        action.cost_paise = self.policy.cost_of(action)
        return action
