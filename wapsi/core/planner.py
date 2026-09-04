"""Choosing what to do, and when.

The planner's one interesting idea is that *when* is part of the decision. A balance failure
retried immediately is worth little; the same retry on payday is worth several times more. So
instead of scoring actions, it scores (action, time) pairs across a short horizon, snaps each to
the earliest moment the policy would permit it, and takes the best. Waiting is a real move.

It can only choose from what :class:`~wapsi.core.policy.PolicyEngine` allows. It cannot invent an
action, override a denial, or reach a customer the rules protect.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from wapsi.core import taxonomy
from wapsi.core.models import (
    CONTACT_ACTIONS,
    Action,
    ActionType,
    Case,
    CaseStatus,
    Outcome,
    RootCause,
)
from wapsi.core.policy import PolicyContext, PolicyEngine
from wapsi.core.timing import is_salary_window

#: How far ahead to look. Beyond three days the age decay dominates and waiting never wins.
HORIZONS = (
    timedelta(0),
    timedelta(hours=6),
    timedelta(hours=24),
    timedelta(hours=48),
    timedelta(hours=72),
)

#: Money later is worth less than money now, and a case can die of old age while we wait.
DELAY_DISCOUNT_PER_DAY = 0.97


@dataclass
class Decision:
    action: Action
    rule_ids: list[str]
    rationale: str


class RulesPlanner:
    """Deterministic planner. No model, no randomness — the baseline Wapsi has to beat."""

    name = "rules"

    def __init__(self, policy: PolicyEngine):
        self.policy = policy

    # -- scoring ------------------------------------------------------------------------------

    def _legal_at(
        self, case: Case, action: Action, moment: datetime, ctx: PolicyContext
    ) -> datetime | None:
        """Earliest time at or after ``moment`` this action is permitted, if ever."""

        # Satisfying one rule can breach another — waiting out a nudge gap can land after the
        # messaging window closes — so snap repeatedly until the time is clean or stops moving.
        current = moment
        for _ in range(4):
            denials = self.policy.check_all(action, case, current, ctx)
            if not denials:
                return current
            earliest = [d.earliest_at for d in denials if d.earliest_at is not None]
            if len(earliest) != len(denials):
                # One denial has no future remedy: a hard stop, a spent cap, a blocked instrument.
                return None
            snapped = max(earliest)
            if snapped <= current:
                return None
            current = snapped
        return None

    def _score(self, case: Case, action: Action, moment: datetime, now: datetime) -> float:
        probability = taxonomy.prior(
            case,
            action.type,
            moment,
            salary_window=is_salary_window(moment),
            # Outages are short; by any future horizon they have resolved.
            downtime_active=False,
            hours_since_failure=(moment - case.created_at).total_seconds() / 3600,
        )
        value = probability * case.amount_paise - action.cost_paise
        days_ahead = max(0.0, (moment - now).total_seconds() / 86400)
        return value * (DELAY_DISCOUNT_PER_DAY**days_ahead)

    def _best(
        self, case: Case, now: datetime, ctx: PolicyContext
    ) -> tuple[Action, datetime, float] | None:
        best: tuple[Action, datetime, float] | None = None
        deadline = case.created_at + timedelta(days=self.policy.max_age_days(case))

        for action_type in taxonomy.candidate_actions(case):
            for horizon in HORIZONS:
                moment = now + horizon
                if moment > deadline:
                    continue
                action = Action(
                    case_id=case.id,
                    type=action_type,
                    params=self.policy.default_params(case, action_type),
                )
                action.cost_paise = self.policy.cost_of(action)
                legal_at = self._legal_at(case, action, moment, ctx)
                if legal_at is None or legal_at > deadline:
                    continue
                value = self._score(case, action, legal_at, now)
                if value <= 0:
                    continue
                if best is None or value > best[2]:
                    best = (action, legal_at, value)
        return best

    # -- planning -----------------------------------------------------------------------------

    def plan(self, case: Case, now: datetime, ctx: PolicyContext) -> Decision:
        # Two causes need one specific action before the case can close, and neither of them
        # involves the customer.
        if case.root_cause is RootCause.RISK_DECLINE and case.status is not CaseStatus.escalated:
            return Decision(
                Action(case_id=case.id, type=ActionType.ESCALATE_HUMAN, scheduled_at=now),
                ["R05"],
                "risk declined this payment; a human must review it and no retry is permitted",
            )
        if case.root_cause is RootCause.MERCHANT_CONFIG and not case.merchant_alerted:  # noqa: SIM102
            return Decision(
                Action(case_id=case.id, type=ActionType.ALERT_MERCHANT, scheduled_at=now),
                ["R06"],
                "the merchant's own configuration caused this; the customer cannot fix it",
            )

        stops = self.policy.hard_stops(case, ctx, now)
        if stops:
            outcome, rule_id = stops[0]
            return Decision(
                Action(
                    case_id=case.id,
                    type=ActionType.CLOSE,
                    params={"outcome": outcome.value},
                    scheduled_at=now,
                ),
                [rule_id],
                f"closing as {outcome.value}: {_rule_text(rule_id)}",
            )

        triggers = self.policy.escalation_triggers(case, ctx)
        if triggers:
            return Decision(
                Action(case_id=case.id, type=ActionType.ESCALATE_HUMAN, scheduled_at=now),
                triggers,
                "escalating to a human: " + "; ".join(_rule_text(r) for r in triggers),
            )

        best = self._best(case, now, ctx)
        if best is None:
            return Decision(
                Action(
                    case_id=case.id,
                    type=ActionType.CLOSE,
                    params={"outcome": Outcome.gave_up.value},
                    scheduled_at=now,
                ),
                ["R24"],
                "no permitted action is worth more than it costs",
            )

        action, moment, value = best
        action.expected_value_paise = value
        if moment > now:
            reason = (
                "acting later is worth more"
                if is_salary_window(moment) or case.root_cause is RootCause.INSUFFICIENT_FUNDS
                else "the earliest permitted moment for this action"
            )
            return Decision(
                Action(
                    case_id=case.id,
                    type=ActionType.WAIT,
                    params={"until": moment.isoformat(), "then": action.type.value},
                    scheduled_at=moment,
                ),
                [],
                f"waiting until {moment:%d %b %H:%M} to {action.type.value}: {reason} "
                f"(expected ₹{value / 100:,.0f})",
            )

        action.scheduled_at = now
        action.rationale = (
            f"best permitted action now, expected ₹{value / 100:,.0f} "
            f"against a cost of ₹{action.cost_paise / 100:,.2f}"
        )
        return Decision(action, [], action.rationale)


def _rule_text(rule_id: str) -> str:
    from wapsi.core.policy import RULE_TEXT

    return RULE_TEXT.get(rule_id, rule_id)
