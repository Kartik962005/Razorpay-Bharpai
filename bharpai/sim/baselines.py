"""The two policies Bharpai has to beat, implemented honestly.

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

from bharpai.core.models import Action, ActionType, Case, Channel, Language, Outcome, Tone
from bharpai.core.planner import Decision
from bharpai.core.policy import PolicyContext, PolicyEngine

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

        from bharpai.core.models import Scenario

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
                return Decision(action, [], f"platform retry ladder (T+{case.retries + 1})")
            return _wait(case, due, "waiting for the platform's next scheduled retry")

        if now >= deadline:
            return _close(case, now, Outcome.gave_up, "no recovery attempted")
        return _wait(case, deadline, "no recovery attempted")


#: Razorpay's own reminder cadence for links and invoices when `reminder_enable` is on: a few
#: notifications at fixed offsets, sent in daytime batches, on both channels the customer gave.
PLATFORM_REMINDERS = (timedelta(days=1), timedelta(days=3), timedelta(days=6))
PLATFORM_SEND_HOUR = 11


class PlatformPlanner:
    """What the merchant gets by using Razorpay's defaults and nothing else.

    This is the fair comparison, and the one a reviewer should demand. The naive baseline is
    what untuned merchant automation does; this is what the platform does unprompted — the
    subscription retry ladder, and `reminder_enable` on links and invoices. It is cause-blind but
    it is not reckless: reminders go out in daytime batches, one-off payments are never retried,
    and nothing is sent after the money arrives. Its actions are tagged as the platform's so its
    rule verdicts are recorded but not held against any merchant policy.
    """

    name = "platform"

    def __init__(self, policy: PolicyEngine):
        self.policy = policy

    def plan(self, case: Case, now: datetime, ctx: PolicyContext) -> Decision:
        from bharpai.core.models import Scenario

        deadline = case.created_at + timedelta(days=self.policy.max_age_days(case))

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
                return Decision(action, [], f"platform retry ladder (T+{case.retries + 1})")
            return _wait(case, due, "waiting for the platform's next scheduled retry")

        if case.nudges < len(PLATFORM_REMINDERS):
            due = case.created_at + PLATFORM_REMINDERS[case.nudges]
            # Notifications go out in a daytime batch, whatever time the link was created.
            due = due.replace(hour=PLATFORM_SEND_HOUR, minute=0, second=0, microsecond=0)
            if due <= case.created_at:
                due += timedelta(days=1)
            if now >= due:
                channel = Channel.email if case.scenario is Scenario.D else Channel.sms
                action = Action(
                    case_id=case.id,
                    type=ActionType.SEND_REMINDER,
                    scheduled_at=now,
                    params={
                        "channel": channel.value,
                        "tone": Tone.helpful.value,
                        "language": Language.en.value,
                        "generic": True,
                        "platform": True,
                    },
                )
                action.cost_paise = self.policy.cost_of(action)
                return Decision(action, [], f"platform reminder {case.nudges + 1} of 3")
            return _wait(case, due, "next platform reminder")

        if now >= deadline:
            return _close(case, now, Outcome.gave_up, "platform reminders exhausted")
        return _wait(case, deadline, "platform reminders exhausted")


class NaivePlanner:
    """Cause-blind, clock-blind, consent-blind. Common, and expensive."""

    name = "naive"

    def __init__(self, policy: PolicyEngine):
        self.policy = policy

    def plan(self, case: Case, now: datetime, ctx: PolicyContext) -> Decision:
        from bharpai.core.models import Scenario

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
