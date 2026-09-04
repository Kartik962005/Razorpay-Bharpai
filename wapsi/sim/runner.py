"""The event loop that runs one policy over the whole batch.

Time is driven by an event queue rather than a fixed tick, so a plan scheduled for 03:12 happens
at 03:12. Four kinds of thing can wake a case: the agent deciding, a customer reacting, a promise
falling due, and a human closing an escalation. Money arriving on its own is the fifth, and it
happens to every policy equally — including the one that does nothing.
"""

from __future__ import annotations

import heapq
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from wapsi.adapters.composer import Composer, TemplateComposer
from wapsi.adapters.humanqueue import HumanQueue
from wapsi.adapters.messaging import Messenger
from wapsi.adapters.razorpay_fake import FakeGateway
from wapsi.core.audit import AuditLog
from wapsi.core.executor import Executor
from wapsi.core.models import Action, ActionType, Case, CaseStatus, Outcome, ReplyIntent, RootCause
from wapsi.core.planner import RulesPlanner
from wapsi.core.policy import PolicyContext, PolicyEngine
from wapsi.core.taxonomy import diagnose
from wapsi.core.timing import is_salary_window
from wapsi.sim.baselines import DoNothingPlanner, NaivePlanner
from wapsi.sim.customer import CustomerModel, Population, Reaction
from wapsi.sim.world import World

#: Give the planner a moment to reconsider after every action, so it can schedule the next one
#: precisely instead of being pinned to a coarse tick.
REPLAN_GAP = timedelta(minutes=1)

#: A planner that only ever waits would run forever. In practice the age decay ends this long
#: before the guard fires; it exists so a bug cannot hang a run.
MAX_CONSECUTIVE_WAITS = 10

PLANNERS: dict[str, Any] = {
    "do_nothing": DoNothingPlanner,
    "naive": NaivePlanner,
    "rules": RulesPlanner,
}


@dataclass
class RunResult:
    policy_name: str
    cases: list[Case]
    audit: AuditLog
    messenger: Messenger
    queue: HumanQueue
    world: World
    population: Population
    seed: int
    wall_seconds: float = 0.0
    events_processed: int = 0
    llm_stats: dict[str, Any] = field(default_factory=dict)


class Runner:
    def __init__(
        self,
        *,
        world: World,
        cases: list[Case],
        population: Population,
        policy: PolicyEngine,
        composer: Composer | None = None,
        results_dir: Path | None = None,
    ):
        self.world = world
        self.base_cases = cases
        self.population = population
        self.policy = policy
        self.composer = composer
        self.results_dir = results_dir

    def run(self, policy_name: str, planner: Any | None = None) -> RunResult:
        started = time.perf_counter()

        # Each policy sees an identical, untouched copy of the batch.
        cases = {case.id: case.model_copy(deep=True) for case in self.base_cases}
        self.population.reset_dynamic_state()

        for case in cases.values():
            cause, tags, text = diagnose(case)
            case.root_cause = cause
            case.diagnosis_text = text
            for tag in tags:
                if tag not in case.tags:
                    case.tags.append(tag)

        audit = AuditLog(
            self.results_dir / f"audit_{policy_name}.jsonl" if self.results_dir else None
        )
        messenger = Messenger(self.policy.economics["channel_cost_paise"])
        queue = HumanQueue()
        customers = CustomerModel(self.population, self.world)
        gateway = FakeGateway(customers)
        composer = self.composer or TemplateComposer(self.policy.policy)
        executor = Executor(
            gateway=gateway,
            messenger=messenger,
            queue=queue,
            policy=self.policy,
            audit=audit,
            composer=composer,
        )
        if planner is None:
            planner = PLANNERS[policy_name](self.policy)

        queue_: list[tuple[datetime, int, str, str, Any]] = []
        counter = 0

        def push(when: datetime, kind: str, case_id: str, payload: Any = None) -> None:
            nonlocal counter
            counter += 1
            heapq.heappush(queue_, (when, counter, kind, case_id, payload))

        message_log: list[tuple[str, datetime]] = []
        waits: dict[str, int] = {}

        for case in cases.values():
            audit.record(
                ts=case.created_at,
                case_id=case.id,
                kind="observation",
                actor="system",
                summary=f"detected: {case.scenario.value}, ₹{case.amount_inr:,.0f} on {case.method.value}",
                payload={
                    "error_reason": case.error.reason if case.error else None,
                    "error_source": case.error.source if case.error else None,
                    "error_step": case.error.step if case.error else None,
                },
            )
            audit.record(
                ts=case.created_at,
                case_id=case.id,
                kind="diagnosis",
                actor="system",
                summary=case.diagnosis_text or "",
                payload={"root_cause": case.root_cause.value if case.root_cause else None},
            )
            push(case.created_at, "plan", case.id)
            hidden = self.population.hidden(case.id)
            if hidden.organic_pay_at is not None:
                push(hidden.organic_pay_at, "organic", case.id)

        events = 0
        while queue_:
            when, _, kind, case_id, payload = heapq.heappop(queue_)
            if when > self.world.end:
                continue
            case = cases[case_id]
            if case.status is CaseStatus.closed:
                continue
            events += 1

            if kind == "organic":
                self._organic_payment(case, when, audit)
                continue
            if kind == "reaction":
                self._apply_reaction(case, when, payload, audit, push, executor)
                continue
            if kind == "promise":
                self._resolve_promise(case, when, customers, audit, push)
                continue
            if kind == "human":
                self._resolve_human(case, when, payload, audit)
                continue

            if case.status is CaseStatus.escalated:
                continue

            ctx = self._context(case, when, message_log)
            decision = planner.plan(case, when, ctx)
            action = decision.action

            if action.type is ActionType.WAIT:
                waits[case.id] = waits.get(case.id, 0) + 1
                if waits[case.id] > MAX_CONSECUTIVE_WAITS:
                    executor.execute(
                        Action(
                            case_id=case.id,
                            type=ActionType.CLOSE,
                            params={"outcome": Outcome.gave_up.value},
                            scheduled_at=when,
                        ),
                        case,
                        when,
                        ctx,
                        rationale="stopped waiting: no action ever became worthwhile",
                    )
                    continue
                until = action.scheduled_at or (when + timedelta(hours=1))
                if until <= when:
                    until = when + timedelta(hours=1)
                executor.execute(
                    Action(case_id=case.id, type=ActionType.WAIT, scheduled_at=until),
                    case,
                    when,
                    ctx,
                    rule_ids=decision.rule_ids,
                    rationale=decision.rationale,
                )
                push(until, "plan", case.id)
                continue

            waits[case.id] = 0
            result = executor.execute(
                action,
                case,
                when,
                ctx,
                rule_ids=decision.rule_ids,
                rationale=decision.rationale,
                platform=bool(action.params.get("platform")),
            )

            if result.message is not None:
                message_log.append((case.customer_id, when))
                self.population.hidden(case.id).contacted = True
                reaction = customers.react(
                    case,
                    action.type,
                    when,
                    channel=result.message.channel,
                    tone=result.message.tone,
                    language=result.message.language,
                    guidance=not action.params.get("generic"),
                )
                push(when + reaction.delay, "reaction", case.id, reaction)

            if result.escalated:
                resolves, delay = customers.human_resolves(case)
                if case.root_cause is RootCause.RISK_DECLINE:
                    # A risk review confirms the decline; it is not a collections call.
                    resolves = False
                push(when + delay, "human", case.id, resolves)
                continue

            if case.status is not CaseStatus.closed:
                push(when + REPLAN_GAP, "plan", case.id)

        # Anything still open when the horizon ends simply ran out of time.
        for case in cases.values():
            if case.status is not CaseStatus.closed:
                case.status = CaseStatus.closed
                case.outcome = (
                    Outcome.escalated_unresolved
                    if case.status is CaseStatus.escalated
                    else Outcome.expired
                )
                case.closed_at = self.world.end

        return RunResult(
            policy_name=policy_name,
            cases=list(cases.values()),
            audit=audit,
            messenger=messenger,
            queue=queue,
            world=self.world,
            population=self.population,
            seed=self.world.seed,
            wall_seconds=time.perf_counter() - started,
            events_processed=events,
            llm_stats=getattr(composer, "stats", lambda: {})(),
        )

    # -- world events -------------------------------------------------------------------------

    def _context(
        self, case: Case, now: datetime, message_log: list[tuple[str, datetime]]
    ) -> PolicyContext:
        cutoff = now - timedelta(days=7)
        recent = [
            ts for customer_id, ts in message_log if customer_id == case.customer_id and ts >= cutoff
        ]
        return PolicyContext(
            downtime_active=self.world.downtime_active(now, case.method.value),
            customer_messages_last_7d=len(recent),
            customer_message_times=recent,
            salary_window=is_salary_window(now),
            paid=case.paid,
        )

    def _organic_payment(self, case: Case, now: datetime, audit: AuditLog) -> None:
        """The customer pays with no prompting. Happens under every policy, including ours."""

        case.paid = True
        case.recovered_paise = case.amount_paise
        case.recovered_at = now
        case.status = CaseStatus.closed
        case.outcome = Outcome.recovered
        case.closed_at = now
        case.tags.append("recovered_organic")
        audit.record(
            ts=now,
            case_id=case.id,
            kind="outcome",
            actor="customer",
            summary="customer paid without any intervention",
            payload={"outcome": Outcome.recovered.value, "organic": True},
        )

    def _apply_reaction(
        self, case: Case, now: datetime, reaction: Reaction, audit: AuditLog, push, executor
    ) -> None:
        if reaction.paid and not case.paid:
            case.paid = True
            case.recovered_paise = case.amount_paise
            case.recovered_at = now
            case.status = CaseStatus.closed
            case.outcome = Outcome.recovered
            case.closed_at = now
            case.tags.append("recovered_via_nudge")
            audit.record(
                ts=now,
                case_id=case.id,
                kind="outcome",
                actor="customer",
                summary="customer paid after the message",
                payload={"outcome": Outcome.recovered.value},
            )
            return

        if reaction.reply_intent is not None:
            audit.record(
                ts=now,
                case_id=case.id,
                kind="reply",
                actor="customer",
                summary=f"reply understood as {reaction.reply_intent.value}: {reaction.reply_text!r}",
                payload={"intent": reaction.reply_intent.value, "text": reaction.reply_text},
            )

        if reaction.opted_out:
            case.opted_out = True
        if reaction.disputed:
            executor.mark_disputed(case, now, "contacted once too often, or at the wrong hour")
        if reaction.complained:
            case.complaint = True
        if reaction.promise_at is not None:
            case.promise_at = reaction.promise_at
            push(reaction.promise_at, "promise", case.id)
            return

        push(now, "plan", case.id)

    def _resolve_promise(
        self, case: Case, now: datetime, customers: CustomerModel, audit: AuditLog, push
    ) -> None:
        if customers.keeps_promise(case):
            case.paid = True
            case.recovered_paise = case.amount_paise
            case.recovered_at = now
            case.status = CaseStatus.closed
            case.outcome = Outcome.recovered
            case.closed_at = now
            case.tags.append("recovered_via_promise")
            audit.record(
                ts=now,
                case_id=case.id,
                kind="outcome",
                actor="customer",
                summary="customer kept their promise to pay",
                payload={"outcome": Outcome.recovered.value},
            )
            return

        case.promises_broken += 1
        case.promise_at = None
        audit.record(
            ts=now,
            case_id=case.id,
            kind="observation",
            actor="system",
            summary=f"promise to pay was not kept (broken promises: {case.promises_broken})",
            payload={"promises_broken": case.promises_broken},
        )
        push(now, "plan", case.id)

    def _resolve_human(self, case: Case, now: datetime, resolved: bool, audit: AuditLog) -> None:
        case.status = CaseStatus.closed
        case.closed_at = now
        if resolved:
            case.paid = True
            case.recovered_paise = case.amount_paise
            case.recovered_at = now
            case.outcome = Outcome.recovered_via_human
            case.tags.append("recovered_via_human")
            summary = "a human closed the case and the money came in"
        elif case.root_cause is RootCause.RISK_DECLINE:
            case.outcome = Outcome.risk_blocked
            summary = "a human reviewed the risk decline and upheld it"
        else:
            case.outcome = Outcome.escalated_unresolved
            summary = "a human worked the case but it was not recovered"
        audit.record(
            ts=now,
            case_id=case.id,
            kind="outcome",
            actor="human",
            summary=summary,
            payload={"outcome": case.outcome.value if case.outcome else None},
        )
