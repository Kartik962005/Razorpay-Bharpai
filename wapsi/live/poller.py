"""Turning a real Razorpay account into cases, and running the agent over them.

Polling rather than webhooks is the default here for a reason: it needs no public URL, no tunnel
and no dashboard configuration, so anyone who clones this repo and adds a test key can watch the
whole loop work. Webhooks are supported too (``wapsi/live/webhook.py``) and reduce latency, but
nothing depends on them.

The agent core is untouched — the same planner, policy engine and executor the batch uses. Only
the gateway underneath is different.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from wapsi.adapters.composer import LLMComposer, TemplateComposer
from wapsi.adapters.humanqueue import HumanQueue
from wapsi.adapters.messaging import Messenger
from wapsi.adapters.razorpay_live import LiveGateway
from wapsi.config import IST
from wapsi.core.audit import AuditLog
from wapsi.core.executor import Executor
from wapsi.core.models import (
    Action,
    ActionType,
    Case,
    CaseStatus,
    ErrorTriple,
    Method,
    Outcome,
    Scenario,
)
from wapsi.core.planner import AgentPlanner, RulesPlanner
from wapsi.core.policy import PolicyContext, PolicyEngine
from wapsi.core.taxonomy import diagnose
from wapsi.core.timing import is_salary_window
from wapsi.live import state

#: How far back to look on the first poll, so a failure produced a minute ago is not missed.
FIRST_LOOK_BACK = timedelta(hours=6)

METHOD_MAP = {
    "upi": Method.upi,
    "card": Method.card,
    "netbanking": Method.netbanking,
    "wallet": Method.wallet,
    "emandate": Method.emandate,
    "upi_autopay": Method.upi_autopay,
}


def case_from_payment(payment: dict[str, Any], merchant_name: str = "Demo Store") -> Case:
    """Build a case from a real failed payment, using Razorpay's own error fields."""

    created = datetime.fromtimestamp(payment.get("created_at", 0), tz=IST)
    contact = str(payment.get("contact") or "")
    email = str(payment.get("email") or "")
    return Case(
        id=f"live_{payment['id']}",
        merchant_name=merchant_name,
        customer_id=contact or email or payment["id"],
        customer_first_name=(email.split("@")[0] or "there").title(),
        customer_contact=contact,
        customer_email=email,
        scenario=Scenario.A,
        method=METHOD_MAP.get(str(payment.get("method", "")).lower(), Method.upi),
        amount_paise=int(payment.get("amount", 0)),
        error=ErrorTriple(
            code=payment.get("error_code"),
            reason=payment.get("error_reason"),
            source=payment.get("error_source"),
            step=payment.get("error_step"),
            description=payment.get("error_description"),
        ),
        razorpay={
            k: v
            for k, v in {
                "payment_id": payment.get("id"),
                "order_id": payment.get("order_id"),
                "payment_link_id": (payment.get("notes") or {}).get("payment_link_id"),
            }.items()
            if v
        },
        created_at=created,
    )


class LivePoller:
    """Runs the agent against a real test-mode account."""

    def __init__(
        self,
        client: Any,
        policy: PolicyEngine,
        *,
        llm: Any | None = None,
        audit: AuditLog | None = None,
        merchant_name: str = "Demo Store",
    ):
        self.client = client
        self.policy = policy
        self.llm = llm
        self.merchant_name = merchant_name
        self.gateway = LiveGateway(client)
        self.audit = audit or AuditLog()
        self.messenger = Messenger(policy.economics["channel_cost_paise"])
        # The weekly per-customer cap is derived from this, so it has to outlive the process.
        self.messenger.sent.extend(state.load_messages())
        self.queue = HumanQueue()
        composer = (
            LLMComposer(policy.policy, llm)
            if llm is not None and getattr(llm, "enabled", False)
            else TemplateComposer(policy.policy)
        )
        self.executor = Executor(
            gateway=self.gateway,
            messenger=self.messenger,
            queue=self.queue,
            policy=policy,
            audit=self.audit,
            composer=composer,
            llm=llm,
        )
        self.planner = (
            AgentPlanner(policy, llm)
            if llm is not None and getattr(llm, "enabled", False)
            else RulesPlanner(policy)
        )
        self.cases: dict[str, Case] = state.load_cases()
        self.seen_payments, self.last_poll = state.load_cursor()
        self.seed = state.load_seed()

    # -- ingestion ----------------------------------------------------------------------------

    def ingest(self, now: datetime) -> list[Case]:
        """Find newly failed payments and anything overdue among the seeded entities."""

        discovered: list[Case] = []
        since = self.last_poll or (now - FIRST_LOOK_BACK)

        failed = self.gateway.failed_payments_since(since)
        self.fetch_complete = getattr(self.gateway, "last_fetch_complete", True)
        for payment in failed:
            if payment["id"] in self.seen_payments:
                continue
            self.seen_payments.add(payment["id"])
            case = case_from_payment(payment, self.merchant_name)
            if case.id in self.cases:
                continue
            self._register(case, now)
            discovered.append(case)

        discovered.extend(self._ingest_seeded(now))
        return discovered

    def _ingest_seeded(self, now: datetime) -> list[Case]:
        found: list[Case] = []

        invoice_id = (self.seed.get("invoice") or {}).get("id")
        if invoice_id and f"live_{invoice_id}" not in self.cases:
            invoice = self.gateway.fetch_entity("invoice", invoice_id)
            if invoice and invoice.get("status") in {"issued", "partially_paid", "expired"}:
                case = Case(
                    id=f"live_{invoice_id}",
                    merchant_name=self.merchant_name,
                    customer_id=str(invoice.get("customer_details", {}).get("contact") or invoice_id),
                    customer_first_name=str(
                        invoice.get("customer_details", {}).get("name") or "there"
                    ).split()[0],
                    customer_contact=str(invoice.get("customer_details", {}).get("contact") or ""),
                    customer_email=str(invoice.get("customer_details", {}).get("email") or ""),
                    scenario=Scenario.D,
                    method=Method.netbanking,
                    amount_paise=int(invoice.get("amount", 0)),
                    razorpay={"invoice_id": invoice_id},
                    created_at=datetime.fromtimestamp(invoice.get("created_at", 0), tz=IST),
                    due_at=now - timedelta(days=1),
                )
                self._register(case, now)
                found.append(case)

        subscription_id = (self.seed.get("subscription") or {}).get("id")
        if subscription_id and f"live_{subscription_id}" not in self.cases:
            subscription = self.gateway.fetch_entity("subscription", subscription_id)
            if subscription and subscription.get("status") in {"pending", "halted"}:
                case = Case(
                    id=f"live_{subscription_id}",
                    merchant_name=self.merchant_name,
                    customer_id=subscription_id,
                    scenario=Scenario.C,
                    method=Method.card,
                    amount_paise=int((self.seed.get("plan") or {}).get("amount", 29_900)),
                    error=ErrorTriple(reason="payment_failed", source="gateway"),
                    razorpay={"subscription_id": subscription_id},
                    created_at=now,
                )
                self._register(case, now)
                found.append(case)

        return found

    def _register(self, case: Case, now: datetime) -> None:
        cause, tags, text = diagnose(case)
        case.root_cause = cause
        case.diagnosis_text = text
        case.tags.extend(t for t in tags if t not in case.tags)
        self.cases[case.id] = case

        self.audit.record(
            ts=case.created_at,
            case_id=case.id,
            kind="observation",
            actor="adapter",
            summary=(
                f"detected on Razorpay: {case.scenario.value}, ₹{case.amount_inr:,.0f} "
                f"on {case.method.value}"
            ),
            payload={
                "error_reason": case.error.reason if case.error else None,
                "error_source": case.error.source if case.error else None,
                "error_step": case.error.step if case.error else None,
                "razorpay": case.razorpay,
            },
        )
        explanation = None
        if self.llm is not None and getattr(self.llm, "enabled", False):
            explanation = self.llm.explain_diagnosis(case)
        self.audit.record(
            ts=case.created_at,
            case_id=case.id,
            kind="diagnosis",
            actor="llm" if explanation else "system",
            summary=explanation or text,
            payload={"root_cause": cause.value},
        )

    # -- the loop -----------------------------------------------------------------------------

    def _context(self, case: Case, now: datetime) -> PolicyContext:
        cutoff = now - timedelta(days=7)
        times = [
            m.sent_at
            for m in self.messenger.sent
            if m.to in (case.customer_contact, case.customer_email) and m.sent_at >= cutoff
        ]
        return PolicyContext(
            downtime_active=self._downtime(case),
            customer_messages_last_7d=len(times),
            customer_message_times=times,
            salary_window=is_salary_window(now),
            paid=case.paid,
            # Test mode cannot initiate a charge, so the planner must not spend this case's
            # action budget proposing one. The gateway is asked rather than assumed.
            extra={"retry_available": getattr(self.gateway, "supports_retry", True)},
        )

    def _downtime(self, case: Case) -> bool:
        """Ask Razorpay whether the rail this case used is currently degraded.

        This is the signal that makes "wait for the outage to clear" real rather than a guess,
        and it is one of the few places the live adapter knows something the simulation invents.
        """

        try:
            page = self.client.payment.fetchDownTime()
        except Exception:  # noqa: BLE001
            return False
        for item in (page or {}).get("items", []):
            if item.get("status") in {"started", "scheduled"} and str(
                item.get("method", "")
            ).lower() == case.method.value:
                return True
        return False

    def step(self, now: datetime | None = None) -> list[str]:
        """One pass: ingest, then act on every case that is due. Returns a readable log."""

        now = now or datetime.now(IST)
        lines: list[str] = []

        # The webhook endpoint runs in a different process and may have written cases since this
        # poller started. Adopt them before ingesting, so a case that arrived by webhook is acted
        # on rather than discovered a second time by the API listing.
        for case_id, stored in state.load_cases().items():
            self.cases.setdefault(case_id, stored)

        for case in self.ingest(now):
            lines.append(f"new case {case.id}: {case.diagnosis_text}")

        for case in list(self.cases.values()):
            if case.status is CaseStatus.closed:
                continue
            if case.next_action_at and now < case.next_action_at:
                # Say so rather than skipping silently: a refusal the operator cannot see is
                # indistinguishable from the agent having stopped working. The stored reason
                # already names the time, so it is printed as the planner phrased it.
                lines.append(
                    f"{case.id}: "
                    + (
                        case.wait_reason
                        or f"waiting until {case.next_action_at:%d %b %H:%M} for the next "
                        "permitted action"
                    )
                )
                continue

            ctx = self._context(case, now)
            decision = self.planner.plan(case, now, ctx)
            result = self.executor.execute(
                decision.action,
                case,
                now,
                ctx,
                rule_ids=decision.rule_ids,
                rationale=decision.rationale,
            )

            if decision.action.type is ActionType.WAIT:
                lines.append(f"{case.id}: {decision.rationale}")
            elif result.closed:
                lines.append(f"{case.id}: closed as {case.outcome.value if case.outcome else '?'}")
            elif result.message is not None:
                link = case.razorpay.get("recovery_link_url", "")
                lines.append(
                    f"{case.id}: {decision.action.type.value} via "
                    f"{result.message.channel.value} — {link or 'link created'}"
                )
            else:
                lines.append(f"{case.id}: {decision.action.type.value}")

        # Only move the cursor forward if the whole window was read. Advancing past a
        # truncated listing would lose those cases permanently, and silently.
        if getattr(self, "fetch_complete", True):
            self.last_poll = now
        else:
            lines.append("listing was truncated; holding the cursor and re-reading next poll")
        state.save_cases(self.cases)
        state.save_cursor(sorted(self.seen_payments), self.last_poll or now)
        state.save_messages(self.messenger.sent)
        return lines

    def close_paid(self, now: datetime) -> list[str]:
        """Sweep for cases whose money has arrived by any route."""

        lines = []
        for case in self.cases.values():
            if case.status is CaseStatus.closed:
                continue
            if self.gateway.refresh(case, now).get("paid"):
                self.executor.execute(
                    Action(
                        case_id=case.id,
                        type=ActionType.CLOSE,
                        params={"outcome": Outcome.recovered.value},
                        scheduled_at=now,
                    ),
                    case,
                    now,
                    PolicyContext(paid=True),
                    rule_ids=["R01"],
                    rationale="payment received",
                )
                case.recovered_paise = case.amount_paise
                case.recovered_at = now
                lines.append(f"{case.id}: RECOVERED ₹{case.amount_inr:,.0f}")
        if lines:
            state.save_cases(self.cases)
            state.save_messages(self.messenger.sent)
        return lines
