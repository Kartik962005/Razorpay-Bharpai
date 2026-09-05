"""The policy engine: what Wapsi is allowed to do, and when.

Nothing reaches a customer without passing through here. The engine answers three questions and
cites a rule id for every answer, so an auditor can reconstruct any decision from the log alone:

* :meth:`PolicyEngine.hard_stops` — is this case over, whatever we might want to do?
* :meth:`PolicyEngine.escalation_triggers` — does a human have to take this now?
* :meth:`PolicyEngine.allowed` — of the actions this cause suggests, which are legal right now?

The bounds live in ``policy.yaml``, not in this file. Rule ids are stable and are referenced by
the tests, the audit log and the README.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from wapsi.config import IST, POLICY_PATH
from wapsi.core import taxonomy
from wapsi.core.models import (
    CONTACT_ACTIONS,
    NOTICE_ACTIONS,
    Action,
    ActionType,
    AuditEntry,
    Case,
    Channel,
    Language,
    Method,
    Outcome,
    RootCause,
    Scenario,
    Tone,
    Violation,
)

#: Instruments that are charged automatically under a mandate, and so fall under the NPCI
#: execution windows and the RBI pre-debit notification requirement.
RECURRING_METHODS = frozenset({Method.emandate, Method.upi_autopay})

RULE_TEXT: dict[str, str] = {
    "R01": "the payment has already succeeded",
    "R02": "the customer has opted out of messages",
    "R03": "a dispute or chargeback is open",
    "R04": "a refund has been requested",
    "R05": "a risk or compliance check declined this payment",
    "R06": "the merchant's configuration caused this failure",
    "R07": "the customer cancelled the subscription",
    "R10": "outside the permitted customer messaging window",
    "R11": "outside the permitted receivables contact window",
    "R12": "outside the permitted auto-debit execution window",
    "R13": "no pre-debit notification has been given yet",
    "R14": "recurring charges above the AFA threshold cannot be auto-retried",
    "R15": "the instrument itself is blocked, retrying it cannot work",
    "R16": "too soon after the last retry, or the outage has not cleared",
    "R20": "the nudge limit or minimum gap between nudges is reached",
    "R21": "the action limit for this case is reached",
    "R22": "the case is older than the recovery window",
    "R23": "this customer has reached the messaging limit for the week",
    "R24": "the amount is too small to justify the cost of contact",
    "R30": "high-value case with repeated failed nudges",
    "R31": "the customer has broken repeated promises to pay",
    "R32": "the customer has complained",
    "R33": "the failure cause could not be identified",
    "R34": "the planner's proposals were denied repeatedly",
    "R40": "the message failed the content guardrails",
    "R41": "the customer promised to pay by a date that has not passed",
}


class Denial(NamedTuple):
    """One reason an action may not be taken, and the earliest time it could be."""

    action: ActionType
    rule_id: str
    reason: str
    earliest_at: datetime | None = None


@dataclass
class PolicyContext:
    """Facts the engine cannot derive from the case alone.

    The runner refreshes these each tick; tests set them directly.
    """

    downtime_active: bool = False
    customer_messages_last_7d: int = 0
    #: When those messages were sent. The weekly cap is a rolling window, so knowing the oldest
    #: one is what lets the engine say *when* contact becomes permissible again rather than just
    #: refusing. Without it the planner cannot tell a temporary cap from a permanent one.
    customer_message_times: list[datetime] = field(default_factory=list)
    salary_window: bool = False
    paid: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _to_ist(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=IST)
    return moment.astimezone(IST)


def _minute_of_day(moment: datetime) -> int:
    local = _to_ist(moment)
    return local.hour * 60 + local.minute


def _at_minute(moment: datetime, minute_of_day: int) -> datetime:
    local = _to_ist(moment)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(minutes=minute_of_day)


class PolicyEngine:
    """Loads ``policy.yaml`` and enforces it."""

    def __init__(self, policy: dict[str, Any]):
        self.policy = policy
        self.windows = policy["windows"]
        self.caps = policy["caps"]
        self.economics = policy["economics"]
        self.escalation = policy["escalation"]
        self.transient = policy["transient_retry"]
        self.afa_threshold_paise: int = policy["afa_threshold_paise"]
        self.promise_grace_days: int = policy["promise_grace_days"]

    @classmethod
    def load(cls, path: Path | str = POLICY_PATH) -> PolicyEngine:
        with open(path, encoding="utf-8") as handle:
            return cls(yaml.safe_load(handle))

    # -- windows ------------------------------------------------------------------------------

    def _messaging_window(self, case: Case) -> tuple[str, dict[str, str]]:
        """Receivables chasing is held to the stricter RBI window; everything else to TRAI's."""

        if case.scenario is Scenario.D:
            return "R11", self.windows["receivables_messaging"]
        return "R10", self.windows["customer_messaging"]

    def _check_messaging_window(self, case: Case, now: datetime) -> tuple[str, datetime] | None:
        rule_id, window = self._messaging_window(case)
        start, end = _minutes(window["start"]), _minutes(window["end"])
        current = _minute_of_day(now)
        if start <= current < end:
            return None
        earliest = _at_minute(now, start)
        if current >= end:
            earliest += timedelta(days=1)
        return rule_id, earliest

    def _check_auto_debit_window(self, now: datetime) -> datetime | None:
        """Return the next permitted moment, or ``None`` if we are already inside a window."""

        current = _minute_of_day(now)
        starts: list[int] = []
        for raw_start, raw_end in self.windows["auto_debit"]:
            start, end = _minutes(raw_start), _minutes(raw_end)
            if start <= current < end:
                return None
            starts.append(start)
        upcoming = [s for s in sorted(starts) if s > current]
        if upcoming:
            return _at_minute(now, upcoming[0])
        return _at_minute(now, min(starts)) + timedelta(days=1)

    # -- terminal conditions ------------------------------------------------------------------

    def hard_stops(self, case: Case, ctx: PolicyContext, now: datetime) -> list[tuple[Outcome, str]]:
        """Conditions under which the case closes immediately, whatever the planner wants.

        Order is significant: the first entry is the outcome the case is closed with.
        """

        stops: list[tuple[Outcome, str]] = []
        if case.paid or ctx.paid:
            stops.append((Outcome.recovered, "R01"))
        if case.opted_out:
            stops.append((Outcome.opted_out, "R02"))
        if case.disputed:
            stops.append((Outcome.disputed, "R03"))
        if case.refunded:
            stops.append((Outcome.refunded, "R04"))
        if case.root_cause is RootCause.RISK_DECLINE:
            stops.append((Outcome.risk_blocked, "R05"))
        if (
            case.root_cause is RootCause.MERCHANT_CONFIG
            and case.merchant_alerted
            and case.retries >= 3
        ):
            # Told the merchant, and quietly re-attempted twice while waiting for their fix.
            # Beyond that the money is theirs to recover, not ours.
            stops.append((Outcome.merchant_issue, "R06"))
        if case.cancelled_by_customer:
            stops.append((Outcome.gave_up, "R07"))
        if case.age_days(now) > self.max_age_days(case):
            stops.append((Outcome.expired, "R22"))
        return stops

    def max_age_days(self, case: Case) -> int:
        return int(self.caps["max_age_days"][case.scenario.value])

    def _cap(self, name: str, case: Case) -> int:
        """Read a cap that may be a single number or a per-scenario mapping."""

        value = self.caps[name]
        if isinstance(value, dict):
            return int(value[case.scenario.value])
        return int(value)

    def nudge_cap(self, case: Case) -> int:
        return self._cap("nudges_per_case", case)

    def nudge_gap(self, case: Case) -> timedelta:
        return timedelta(hours=self._cap("nudge_gap_hours", case))

    def action_cap(self, case: Case) -> int:
        return self._cap("actions_per_case", case)

    def escalation_triggers(self, case: Case, ctx: PolicyContext) -> list[str]:
        """Rule ids demanding a human takes over. Any hit means the agent stops deciding."""

        triggers: list[str] = []
        if (
            case.amount_paise >= self.escalation["high_value_paise"]
            and case.nudges >= self.escalation["high_value_min_nudges"]
        ):
            triggers.append("R30")
        if case.promises_broken >= self.escalation["promises_broken"]:
            triggers.append("R31")
        if self.escalation["on_complaint"] and case.complaint:
            triggers.append("R32")
        if (
            case.root_cause is RootCause.UNKNOWN
            and case.retries > self.escalation["unknown_after_failed_retries"]
        ):
            triggers.append("R33")
        if case.llm_denials >= self.escalation["llm_denials"]:
            triggers.append("R34")
        return triggers

    # -- per-action checks --------------------------------------------------------------------

    def cost_of(self, action: Action) -> int:
        if action.type is ActionType.RETRY_CHARGE:
            return int(self.economics["retry_cost_paise"])
        if action.type is ActionType.ESCALATE_HUMAN:
            return int(self.economics["human_escalation_cost_paise"])
        if action.type in CONTACT_ACTIONS:
            channel = action.params.get("channel", Channel.sms.value)
            return int(self.economics["channel_cost_paise"][channel])
        return 0

    def check_all(
        self, action: Action, case: Case, now: datetime, ctx: PolicyContext
    ) -> list[Denial]:
        """Every rule this action would break right now, most blocking first."""

        denials: list[Denial] = []

        def deny(rule_id: str, earliest: datetime | None = None) -> None:
            denials.append(Denial(action.type, rule_id, RULE_TEXT[rule_id], earliest))

        # Terminal conditions forbid everything except recording the outcome. Two exceptions:
        # telling the merchant about their own broken configuration, and handing a risk-declined
        # case to a human, are precisely the right moves for those stops.
        if action.type not in (ActionType.CLOSE, ActionType.WAIT):
            exempt = {
                ActionType.ALERT_MERCHANT: {"R06"},
                # A retry reaches no customer, so the rule protecting customers does not bar it.
                ActionType.RETRY_CHARGE: {"R06"},
                ActionType.ESCALATE_HUMAN: {"R05", "R06"},
            }.get(action.type, set())
            seen: set[str] = set()
            for _, rule_id in self.hard_stops(case, ctx, now):
                if rule_id in exempt or rule_id in seen:
                    continue
                seen.add(rule_id)
                deny(rule_id)
            # These two causes block ordinary recovery from the outset, before the terminal
            # stop fires: the customer is not the person who can fix either of them.
            if (
                case.root_cause is RootCause.MERCHANT_CONFIG
                and "R06" not in exempt
                and "R06" not in seen
            ):
                deny("R06")
            if (
                case.root_cause is RootCause.RISK_DECLINE
                and "R05" not in exempt
                and "R05" not in seen
            ):
                deny("R05")

        if action.type in (ActionType.CLOSE, ActionType.WAIT, ActionType.ESCALATE_HUMAN):
            return denials

        if case.actions >= self.action_cap(case):
            deny("R21")

        if action.type is ActionType.RETRY_CHARGE:
            self._check_retry(action, case, now, ctx, deny)
        elif action.type in CONTACT_ACTIONS:
            self._check_contact(action, case, now, ctx, deny)

        return denials

    def _check_retry(self, action, case: Case, now: datetime, ctx: PolicyContext, deny) -> None:
        if case.root_cause is RootCause.INSTRUMENT_BLOCKED:
            # The bank is refusing this instrument; another attempt on it is guaranteed noise.
            deny("R15")

        if "afa_required" in case.tags or (
            case.scenario is Scenario.C and case.amount_paise > self.afa_threshold_paise
        ):
            deny("R14")

        is_recurring = case.scenario is Scenario.C or case.method in RECURRING_METHODS
        if is_recurring:
            next_window = self._check_auto_debit_window(now)
            if next_window is not None:
                deny("R12", next_window)
            notice_hours = int(self.windows["predebit_notice_hours"])
            if case.predebit_notice_at is None:
                deny("R13", now + timedelta(hours=notice_hours))
            else:
                earliest = case.predebit_notice_at + timedelta(hours=notice_hours)
                if now < earliest:
                    deny("R13", earliest)

        if (
            case.root_cause is RootCause.TRANSIENT_TECH
            and self.transient["wait_for_downtime_resolved"]
            and ctx.downtime_active
        ):
            deny("R16")

        gap = timedelta(minutes=int(self.transient["min_gap_minutes"]))
        if case.last_retry_at is not None and now - case.last_retry_at < gap:
            deny("R16", case.last_retry_at + gap)
        window_start = now - timedelta(hours=24)
        recent = [t for t in case.retry_times if t >= window_start]
        if len(recent) >= int(self.transient["max_per_24h"]):
            deny("R16", min(recent) + timedelta(hours=24))

    def _weekly_cap(self, ctx: PolicyContext, now: datetime) -> tuple[bool, datetime | None]:
        """Is this customer over their weekly message allowance, and when does that lift?

        A rolling cap always expires. Saying when is what lets the planner wait instead of
        abandoning a case it could still recover next week.
        """

        window = timedelta(days=7)
        recent = sorted(t for t in ctx.customer_message_times if now - t < window)
        count = len(recent) if ctx.customer_message_times else ctx.customer_messages_last_7d
        if count < self.caps["customer_messages_per_7d"]:
            return False, None
        return True, (recent[0] + window if recent else None)

    def _check_contact(self, action, case: Case, now: datetime, ctx: PolicyContext, deny) -> None:
        if action.type not in NOTICE_ACTIONS:
            window = self._check_messaging_window(case, now)
            if window is not None:
                deny(window[0], window[1])

        # A pre-debit notice is a legal precondition for charging, not a dunning message, so it
        # is not charged against the nudge budget — otherwise the rules would forbid the very
        # step that makes a compliant retry possible.
        if action.type in NOTICE_ACTIONS:
            over, earliest = self._weekly_cap(ctx, now)
            if over:
                deny("R23", earliest)
            window = self._check_messaging_window(case, now)
            if window is not None:
                deny(window[0], window[1])
            return

        if case.nudges >= self.nudge_cap(case):
            deny("R20")
        gap = self.nudge_gap(case)
        if case.last_contact_at is not None and now - case.last_contact_at < gap:
            deny("R20", case.last_contact_at + gap)

        if case.scenario is Scenario.B and case.nudges == 0:
            delay = timedelta(minutes=int(self.caps["first_abandon_nudge_delay_minutes"]))
            if now - case.created_at < delay:
                # Let the customer finish on their own before interrupting them.
                deny("R20", case.created_at + delay)

        over, earliest = self._weekly_cap(ctx, now)
        if over:
            deny("R23", earliest)

        if case.amount_paise < self.economics["min_amount_for_nudge_paise"]:
            deny("R24")

        if case.promise_at is not None:
            grace = case.promise_at + timedelta(days=self.promise_grace_days)
            if now < grace:
                # They told us a date. Chasing before it arrives is what makes dunning hated.
                deny("R41", grace)

    def check(self, action: Action, case: Case, now: datetime, ctx: PolicyContext) -> Denial | None:
        denials = self.check_all(action, case, now, ctx)
        return denials[0] if denials else None

    # -- enumeration --------------------------------------------------------------------------

    def default_params(self, case: Case, action_type: ActionType) -> dict[str, Any]:
        """Sensible channel, tone and language for an action, before any planner override."""

        params: dict[str, Any] = {}
        if action_type in CONTACT_ACTIONS:
            if case.channel_pref is not None:
                channel = case.channel_pref
            elif case.scenario is Scenario.D:
                channel = Channel.email
            else:
                channel = Channel.sms
            tone = (Tone.soft, Tone.helpful, Tone.firm)[min(case.nudges, 2)]
            language = case.language_pref or (
                Language.hinglish if case.amount_paise < 500_000 else Language.en
            )
            params.update(
                {"channel": channel.value, "tone": tone.value, "language": language.value}
            )
        if action_type is ActionType.OFFER_METHOD_SWITCH:
            params["to_method"] = taxonomy.preferred_switch_method(case).value
        return params

    def allowed(
        self, case: Case, now: datetime, ctx: PolicyContext
    ) -> tuple[list[Action], list[Denial]]:
        """Split this cause's candidate actions into the legal and the blocked."""

        allowed: list[Action] = []
        denied: list[Denial] = []
        for action_type in taxonomy.candidate_actions(case):
            action = Action(
                case_id=case.id,
                type=action_type,
                params=self.default_params(case, action_type),
                scheduled_at=now,
            )
            action.cost_paise = self.cost_of(action)
            denials = self.check_all(action, case, now, ctx)
            if denials:
                denied.extend(denials)
            else:
                allowed.append(action)
        return allowed, denied

    # -- scoring other policies ---------------------------------------------------------------

    @staticmethod
    def violations(entries: list[AuditEntry]) -> list[Violation]:
        """Read a completed run's audit log and list every rule an executed action broke.

        The runner records the engine's verdict on every action, including actions taken by
        baseline policies that never consult it. That is what makes the comparison fair: the
        naive baseline is judged by the same rules Wapsi obeys.
        """

        found: list[Violation] = []
        for entry in entries:
            if entry.kind != "action":
                continue
            broken = entry.payload.get("violations") or []
            for rule_id in broken:
                found.append(
                    Violation(
                        case_id=entry.case_id,
                        ts=entry.ts,
                        rule_id=rule_id,
                        action=ActionType(entry.payload.get("action_type", ActionType.WAIT.value)),
                        detail=RULE_TEXT.get(rule_id, rule_id),
                    )
                )
        return found
