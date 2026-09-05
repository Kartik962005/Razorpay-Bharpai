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
from wapsi.core.planner import AgentPlanner, RulesPlanner
from wapsi.core.replies import interpret
from wapsi.core.policy import PolicyContext, PolicyEngine
from wapsi.core.taxonomy import diagnose
from wapsi.core.timing import is_salary_window
from wapsi.sim.baselines import DoNothingPlanner, NaivePlanner, PlatformPlanner
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
    "platform": PlatformPlanner,
    "naive": NaivePlanner,
    "rules": RulesPlanner,
}


@dataclass
class _Session:
    """The collaborators one run hands between its steps.

    Bundled so the event loop can read as a sequence of named moves rather than a single long
    function threading a dozen locals through itself.
    """

    cases: dict[str, Case]
    audit: AuditLog
    executor: Executor
    planner: Any
    customers: CustomerModel
    push: Any
    reader: Any | None
    message_log: list[tuple[str, datetime]] = field(default_factory=list)
    waits: dict[str, int] = field(default_factory=dict)
    reply_stats: dict[str, int] = field(default_factory=dict)


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
        llm: Any | None = None,
        advisor_sample: float = 1.0,
    ):
        self.world = world
        self.base_cases = cases
        self.population = population
        self.policy = policy
        self.composer = composer
        self.results_dir = results_dir
        self.llm = llm
        self.advisor_sample = advisor_sample

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
        uses_model = policy_name == "agent" and self.llm is not None
        composer = self.composer
        if composer is None:
            if uses_model:
                from wapsi.adapters.composer import LLMComposer

                composer = LLMComposer(self.policy.policy, self.llm)
            else:
                composer = TemplateComposer(self.policy.policy)
        executor = Executor(
            gateway=gateway,
            messenger=messenger,
            queue=queue,
            policy=self.policy,
            audit=audit,
            composer=composer,
            llm=self.llm if uses_model else None,
        )
        if planner is None:
            if policy_name == "agent":
                planner = AgentPlanner(self.policy, self.llm, sample=self.advisor_sample)
            else:
                planner = PLANNERS[policy_name](self.policy)

        # How well the model reads customer replies is measured, not assumed.
        reply_stats = {"read": 0, "correct": 0, "by_model": 0, "hard_stops": 0, "hard_stops_caught": 0}
        # Only the agent policy gets the model, for reading replies as well as for planning.
        reader = self.llm if uses_model else None

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

        session = _Session(
            cases=cases,
            audit=audit,
            executor=executor,
            planner=planner,
            customers=customers,
            push=push,
            reader=reader,
            message_log=message_log,
            waits=waits,
            reply_stats=reply_stats,
        )

        events = 0
        while queue_:
            when, _, kind, case_id, payload = heapq.heappop(queue_)
            if when > self.world.end:
                continue
            case = cases[case_id]
            if case.status is CaseStatus.closed:
                continue
            events += 1
            self._dispatch(kind, case, when, payload, session)

        # Anything still open when the horizon ends simply ran out of time.
        for case in cases.values():
            if case.status is not CaseStatus.closed:
                # Read the status before overwriting it: a case still sitting with a human at
                # the horizon is unresolved, not expired, and the two are different outcomes.
                unresolved = case.status is CaseStatus.escalated
                case.status = CaseStatus.closed
                case.outcome = (
                    Outcome.escalated_unresolved if unresolved else Outcome.expired
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
            llm_stats={**getattr(composer, "stats", dict)(), "replies": reply_stats},
        )

    # -- the loop, one event at a time --------------------------------------------------------

    def _dispatch(self, kind: str, case: Case, when: datetime, payload: Any, s: _Session) -> None:
        """Route one event. Four things can wake a case; the fifth is the agent deciding."""

        if kind == "organic":
            self._organic_payment(case, when, s.audit)
        elif kind == "reaction":
            self._apply_reaction(
                case, when, payload, s.audit, s.push, s.executor, s.reply_stats, s.reader
            )
        elif kind == "promise":
            self._resolve_promise(case, when, s.customers, s.audit, s.push)
        elif kind == "human":
            self._resolve_human(case, when, payload, s.audit)
        elif case.status is not CaseStatus.escalated:
            # A case with a human on it is theirs until they close it.
            self._plan_and_act(case, when, s)

    def _plan_and_act(self, case: Case, when: datetime, s: _Session) -> None:
        ctx = self._context(case, when, s.message_log)
        decision = s.planner.plan(case, when, ctx)

        if decision.action.type is ActionType.WAIT:
            self._wait(case, when, decision, ctx, s)
            return

        s.waits[case.id] = 0
        if decision.proposal is not None:
            self._record_proposal(case, when, decision, s.audit)

        result = s.executor.execute(
            decision.action,
            case,
            when,
            ctx,
            rule_ids=decision.rule_ids,
            rationale=decision.rationale,
            platform=bool(decision.action.params.get("platform")),
        )

        if result.message is not None:
            self._await_customer(case, when, decision.action, result, s)

        if result.escalated:
            self._await_human(case, when, s)
            return

        if case.status is not CaseStatus.closed:
            # Re-plan almost immediately, so the next action is scheduled to the minute rather
            # than pinned to a coarse tick.
            s.push(when + REPLAN_GAP, "plan", case.id)

    def _wait(self, case: Case, when: datetime, decision, ctx: PolicyContext, s: _Session) -> None:
        s.waits[case.id] = s.waits.get(case.id, 0) + 1
        if s.waits[case.id] > MAX_CONSECUTIVE_WAITS:
            # A planner that only ever waits would run forever. The age decay ends this long
            # before the guard fires; it exists so a bug cannot hang a run.
            s.executor.execute(
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
            return

        until = decision.action.scheduled_at or (when + timedelta(hours=1))
        if until <= when:
            until = when + timedelta(hours=1)
        s.executor.execute(
            Action(case_id=case.id, type=ActionType.WAIT, scheduled_at=until),
            case,
            when,
            ctx,
            rule_ids=decision.rule_ids,
            rationale=decision.rationale,
        )
        s.push(until, "plan", case.id)

    @staticmethod
    def _record_proposal(case: Case, when: datetime, decision, audit: AuditLog) -> None:
        """Write down what the model proposed and what the policy engine did with it.

        Both lines, always. An override that is only visible in its result is not auditable.
        """

        audit.record(
            ts=when,
            case_id=case.id,
            kind="proposal",
            actor="llm",
            summary=str(decision.proposal.get("reason", ""))[:200],
            payload={"proposal": decision.proposal},
        )
        audit.record(
            ts=when,
            case_id=case.id,
            kind="verdict",
            actor="policy",
            summary=decision.verdict or "",
            rule_ids=["R34"] if decision.verdict and "denied" in decision.verdict else [],
            payload={"accepted": decision.verdict == "allowed"},
        )

    def _await_customer(self, case: Case, when: datetime, action: Action, result, s: _Session) -> None:
        s.message_log.append((case.customer_id, when))
        self.population.hidden(case.id).contacted = True
        reaction = s.customers.react(
            case,
            action.type,
            when,
            channel=result.message.channel,
            tone=result.message.tone,
            language=result.message.language,
            guidance=not action.params.get("generic"),
        )
        s.push(when + reaction.delay, "reaction", case.id, reaction)

    @staticmethod
    def _await_human(case: Case, when: datetime, s: _Session) -> None:
        resolves, delay = s.customers.human_resolves(case)
        if case.root_cause is RootCause.RISK_DECLINE:
            # A risk review confirms the decline; it is not a collections call.
            resolves = False
        s.push(when + delay, "human", case.id, resolves)

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
        self,
        case: Case,
        now: datetime,
        reaction: Reaction,
        audit: AuditLog,
        push,
        executor,
        reply_stats: dict[str, int],
        reader: Any | None,
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

        if reaction.reply_intent is not None and reaction.reply_text:
            # The agent only ever sees the text. What the customer meant is the world's
            # business, and is used solely to score the reading afterwards.
            case.reply_texts.append(reaction.reply_text)
            read, promise_at, confidence, by_model = interpret(reaction.reply_text, now, reader)
            reply_stats["read"] += 1
            reply_stats["by_model"] += int(by_model)
            reply_stats["correct"] += int(read is reaction.reply_intent)
            if reaction.reply_intent in (ReplyIntent.opt_out, ReplyIntent.dispute):
                reply_stats["hard_stops"] += 1
                reply_stats["hard_stops_caught"] += int(read is reaction.reply_intent)

            audit.record(
                ts=now,
                case_id=case.id,
                kind="reply",
                actor="customer",
                summary=f"read as {read.value} ({confidence:.0%} confident): {reaction.reply_text!r}",
                payload={
                    "text": reaction.reply_text,
                    "read_as": read.value,
                    "actual": reaction.reply_intent.value,
                    "by_model": by_model,
                },
            )

            if read is ReplyIntent.opt_out:
                case.opted_out = True
            elif read is ReplyIntent.dispute:
                executor.mark_disputed(case, now, "the customer disputed the charge")
            elif read is ReplyIntent.complaint:
                case.complaint = True
            elif read is ReplyIntent.promise_to_pay and promise_at is not None:
                case.promise_at = promise_at

        # What the customer actually does happens regardless of how well we read them.
        if reaction.opted_out:
            case.opted_out = True
        if reaction.disputed:
            executor.mark_disputed(case, now, "contacted once too often, or at the wrong hour")
        if reaction.complained:
            case.complaint = True
        if reaction.promise_at is not None:
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
