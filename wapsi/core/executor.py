"""Doing the thing, and writing down that it was done.

Two responsibilities beyond dispatch, both non-negotiable:

* **Idempotency.** Every action refetches payment status first. Nobody gets chased for money they
  have already sent, and no charge is attempted twice on the same order.
* **Scoring.** The policy engine is consulted on every action *whatever policy is driving*, and
  the verdict is written to the audit log. That is what lets a baseline that ignores the rules be
  measured against them fairly, instead of being taken at its own word.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from wapsi.adapters.composer import Composer, build_context
from wapsi.adapters.humanqueue import HumanQueue
from wapsi.adapters.messaging import Messenger
from wapsi.adapters.templates import escalation_brief
from wapsi.core.audit import AuditLog
from wapsi.core.models import (
    Action,
    ActionType,
    Case,
    CaseStatus,
    Channel,
    Message,
    Outcome,
    RootCause,
)
from wapsi.core.policy import PolicyContext, PolicyEngine
from wapsi.core.validator import validate

LINK_VALIDITY = timedelta(days=3)


@dataclass
class ExecutionResult:
    """What happened, in terms the runner can act on."""

    executed: bool = False
    paid: bool = False
    closed: bool = False
    message: Message | None = None
    charge: dict[str, Any] | None = None
    escalated: bool = False
    violations: list[str] = field(default_factory=list)


class Executor:
    def __init__(
        self,
        *,
        gateway: Any,
        messenger: Messenger,
        queue: HumanQueue,
        policy: PolicyEngine,
        audit: AuditLog,
        composer: Composer,
        llm: Any | None = None,
    ):
        self.gateway = gateway
        self.messenger = messenger
        self.queue = queue
        self.policy = policy
        self.audit = audit
        self.composer = composer
        self.llm = llm

    def execute(
        self,
        action: Action,
        case: Case,
        now: datetime,
        ctx: PolicyContext,
        *,
        rule_ids: list[str] | None = None,
        rationale: str = "",
        platform: bool = False,
    ) -> ExecutionResult:
        result = ExecutionResult()

        # 1. Never act on stale money. This is the single most important line in the system.
        status = self.gateway.refresh(case, now)
        if status.get("paid") and not case.paid:
            self._mark_paid(case, now, via="external")
            self.audit.record(
                ts=now,
                case_id=case.id,
                kind="outcome",
                actor="adapter",
                summary="payment already received; stopping before acting",
                rule_ids=["R01"],
                payload={"outcome": Outcome.recovered.value},
            )
            result.paid = True
            result.closed = True
            return result

        if action.type is ActionType.WAIT:
            case.next_action_at = action.scheduled_at
            case.status = CaseStatus.waiting
            case.wait_reason = rationale or None
            self.audit.record(
                ts=now,
                case_id=case.id,
                kind="verdict",
                actor="planner",
                summary=rationale or "waiting",
                rule_ids=rule_ids or [],
                payload={"until": action.scheduled_at.isoformat() if action.scheduled_at else None},
            )
            return result

        # 2. Judge the action against the rules, whoever chose it.
        violations = [d.rule_id for d in self.policy.check_all(action, case, now, ctx)]
        result.violations = violations

        if action.type is ActionType.CLOSE:
            outcome = Outcome(action.params.get("outcome", Outcome.gave_up.value))
            self._close(case, now, outcome)
            self.audit.record(
                ts=now,
                case_id=case.id,
                kind="outcome",
                actor="planner",
                summary=rationale or f"closed as {outcome.value}",
                rule_ids=rule_ids or [],
                payload={"outcome": outcome.value},
            )
            result.closed = True
            return result

        self.audit.record(
            ts=now,
            case_id=case.id,
            kind="action",
            actor="planner",
            summary=rationale or f"{action.type.value}",
            rule_ids=rule_ids or [],
            payload={
                "action_type": action.type.value,
                "params": {k: str(v) for k, v in action.params.items()},
                "cost_paise": action.cost_paise,
                "expected_value_paise": round(action.expected_value_paise),
                "violations": violations,
                "platform": platform,
            },
        )

        handlers = {
            ActionType.RETRY_CHARGE: self._retry,
            ActionType.SEND_PAYMENT_LINK: self._contact,
            ActionType.SEND_REMINDER: self._contact,
            ActionType.SEND_PREDEBIT_NOTICE: self._contact,
            ActionType.OFFER_METHOD_SWITCH: self._contact,
            ActionType.REQUEST_REAUTH: self._contact,
            ActionType.ESCALATE_HUMAN: self._escalate,
            ActionType.ALERT_MERCHANT: self._alert_merchant,
        }
        handler = handlers.get(action.type)
        if handler is None:
            return result

        handler(action, case, now, result, rule_ids or [])

        case.actions += 1
        case.attempts_by_action[action.type.value] = case.attempts_of(action.type) + 1
        case.cost_paise += action.cost_paise
        result.executed = True
        return result

    # -- handlers -----------------------------------------------------------------------------

    def _retry(self, action, case: Case, now, result: ExecutionResult, rule_ids) -> None:
        outcome = self.gateway.retry_charge(case, now)
        case.retries += 1
        case.last_retry_at = now
        case.retry_times.append(now)
        result.charge = outcome

        if outcome.get("success"):
            self._mark_paid(case, now, via="retry")
            result.paid = True
            result.closed = True
        if outcome.get("disputed"):
            self.mark_disputed(case, now, "retried a payment that risk had already declined")

        self.audit.record(
            ts=now,
            case_id=case.id,
            kind="result",
            actor="adapter",
            summary=(
                "retry succeeded, payment recovered"
                if outcome.get("success")
                else f"retry failed ({outcome.get('reason')})"
            ),
            payload={"success": bool(outcome.get("success")), "attempt": case.retries},
        )

    def _contact(self, action, case: Case, now, result: ExecutionResult, rule_ids) -> None:
        from wapsi.core.taxonomy import preferred_switch_method

        method_hint = (
            preferred_switch_method(case) if action.type is ActionType.OFFER_METHOD_SWITCH else None
        )
        link = self.gateway.create_payment_link(
            case,
            now,
            description=f"{case.merchant_name} · {case.scenario.value}",
            expire_by=now + LINK_VALIDITY,
            method_hint=method_hint,
        )
        # Recorded under its own key rather than overwriting whatever link the payment
        # originally came from: `refresh` checks the recovery link first precisely because it
        # supersedes the original, and it can only do that if both ids survive.
        case.razorpay["recovery_link_id"] = link["id"]
        case.razorpay["recovery_link_url"] = link["short_url"]

        composed = self.composer.compose(case, action, link["short_url"], now)
        ctx = build_context(case, action, link["short_url"], self.policy.policy)
        check = validate(composed.text, ctx, self.policy.policy)
        if not check.ok:
            # A message that fails the guardrails is never sent; the template replaces it.
            from wapsi.adapters.templates import render

            self.audit.record(
                ts=now,
                case_id=case.id,
                kind="verdict",
                actor="policy",
                summary="message rejected by guardrails, using template instead",
                rule_ids=["R40"],
                # The text is kept because a rejection is only diagnosable with it: three
                # rejections once turned out to be the validator's own false positives, and
                # that was invisible from the failure list alone.
                payload={"failures": check.failures, "rejected_text": composed.text},
            )
            composed.text = render(
                ctx,
                predebit=action.type is ActionType.SEND_PREDEBIT_NOTICE,
                guidance=not action.params.get("generic"),
            )
            composed.llm_written = False
            composed.fell_back = True

        channel = ctx.channel
        recipient = case.customer_email if channel is Channel.email else case.customer_contact
        message = self.messenger.send(
            case_id=case.id,
            channel=channel,
            to=recipient,
            text=composed.text,
            language=ctx.language,
            tone=ctx.tone,
            now=now,
            template_id=composed.template_id,
            llm_written=composed.llm_written,
        )
        if action.type is ActionType.SEND_PREDEBIT_NOTICE:
            # This is what unlocks a lawful retry 24 hours from now.
            case.predebit_notice_at = now
        else:
            case.nudges += 1
        case.last_contact_at = now
        result.message = message

        self.audit.record(
            ts=now,
            case_id=case.id,
            kind="result",
            actor="adapter",
            summary=f"sent {channel.value} in {ctx.language.value}, tone {ctx.tone.value}",
            payload={
                "text": composed.text,
                "link": link["short_url"],
                "cost_paise": message.cost_paise,
                "llm_written": composed.llm_written,
                "fell_back": composed.fell_back,
            },
        )

    def _escalate(self, action, case: Case, now, result: ExecutionResult, rule_ids) -> None:
        brief = action.params.get("brief")
        if not brief and self.llm is not None and getattr(self.llm, "enabled", False):
            brief = self.llm.write_brief(case, rule_ids, case.reply_texts)
        if not brief:
            brief = escalation_brief(
                case.merchant_name,
                case.amount_inr,
                case.root_cause or RootCause.UNKNOWN,
                case.actions,
                rule_ids,
            )
        kind = "risk_review" if case.root_cause is RootCause.RISK_DECLINE else "escalation"
        ticket = self.queue.create(case, now, kind=kind, brief=brief, rule_ids=rule_ids)
        case.status = CaseStatus.escalated
        result.escalated = True

        self.audit.record(
            ts=now,
            case_id=case.id,
            kind="escalation",
            actor="system",
            summary=f"handed to a human ({kind})",
            rule_ids=rule_ids,
            payload={"ticket": ticket.id, "brief": brief},
        )

    def _alert_merchant(self, action, case: Case, now, result: ExecutionResult, rule_ids) -> None:
        reason = case.error.reason if case.error else "configuration error"
        ticket = self.queue.create(
            case,
            now,
            kind="merchant_alert",
            brief=(
                f"{case.merchant_name}: payments are failing with '{reason}'. "
                "This is a configuration problem on the account, not a customer problem. "
                "No customer has been contacted about it."
            ),
            rule_ids=rule_ids,
        )
        case.merchant_alerted = True

        self.audit.record(
            ts=now,
            case_id=case.id,
            kind="escalation",
            actor="system",
            summary="merchant alerted; customer deliberately not contacted",
            rule_ids=rule_ids,
            payload={"ticket": ticket.id, "reason": reason},
        )

    # -- state --------------------------------------------------------------------------------

    def mark_disputed(self, case: Case, now: datetime, reason: str) -> None:
        """A dispute is a loss twice over: the sale, and the chargeback fee."""

        if case.disputed:
            return
        case.disputed = True
        fee = int(self.policy.economics.get("dispute_cost_paise", 0))
        case.cost_paise += fee
        self.audit.record(
            ts=now,
            case_id=case.id,
            kind="observation",
            actor="customer",
            summary=f"customer raised a dispute: {reason}",
            rule_ids=["R03"],
            payload={"chargeback_fee_paise": fee},
        )

    def _mark_paid(self, case: Case, now: datetime, *, via: str) -> None:
        case.paid = True
        case.recovered_paise = case.amount_paise
        case.recovered_at = now
        case.tags.append(f"recovered_via_{via}")
        self._close(case, now, Outcome.recovered)

    def _close(self, case: Case, now: datetime, outcome: Outcome) -> None:
        case.status = CaseStatus.closed
        case.outcome = outcome
        case.closed_at = now
        case.next_action_at = None
