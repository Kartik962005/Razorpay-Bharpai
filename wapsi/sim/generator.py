"""Builds the batch: cases the agent can see, plus the hidden state it cannot.

Stratified rather than uniform, because the mix is the point. A batch of mostly technical
declines would flatter any retry loop; a realistic Indian mix is dominated by balance failures
and abandonment, where retrying blindly is exactly the wrong move.

Customers are drawn from a smaller pool than the case count, so some people carry two or three
cases at once. That is what makes the per-customer messaging cap (R23) mean anything.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from wapsi.core.models import Case, Channel, ErrorTriple, Language, Method, RootCause, Scenario
from wapsi.core.taxonomy import REASON_TO_CAUSE
from wapsi.sim.customer import HiddenCase, HiddenCustomer, Population
from wapsi.sim.world import World

FIRST_NAMES = [
    "Aarav", "Diya", "Vihaan", "Ananya", "Arjun", "Ishita", "Kabir", "Meera", "Rohan", "Sara",
    "Aditya", "Priya", "Karan", "Nisha", "Rahul", "Tara", "Vikram", "Zoya", "Aman", "Kavya",
    "Dev", "Riya", "Sahil", "Anjali", "Nikhil", "Pooja", "Yash", "Sneha", "Rajat", "Isha",
]

MERCHANTS = [
    ("merch_chai", "Chai Point"),
    ("merch_kirana", "Daily Kirana"),
    ("merch_stream", "StreamBox"),
    ("merch_fit", "FitClub"),
    ("merch_supply", "Nova Supplies"),
]

#: Which party Razorpay names as the source, per cause. Keeps generated errors self-consistent.
CAUSE_SOURCE: dict[RootCause, str] = {
    RootCause.TRANSIENT_TECH: "gateway",
    RootCause.INSUFFICIENT_FUNDS: "customer",
    RootCause.LIMIT_EXCEEDED: "customer",
    RootCause.CUSTOMER_ABANDON: "customer",
    RootCause.CUSTOMER_INPUT: "customer",
    RootCause.INSTRUMENT_BLOCKED: "gateway",
    RootCause.MANDATE_ISSUE: "customer",
    RootCause.RISK_DECLINE: "gateway",
    RootCause.MERCHANT_CONFIG: "business",
    RootCause.UNKNOWN: "gateway",
}

METHOD_STEPS: dict[Method, list[str]] = {
    Method.card: ["payment_authentication", "payment_authorization", "card_enrollment_check"],
    Method.upi: ["payment_authentication", "payment_debit_request", "payment_response"],
    Method.upi_autopay: ["mandate_creation", "payment_debit_request", "payment_authentication"],
    Method.netbanking: ["payment_authentication", "payment_authorization"],
    Method.wallet: ["payment_eligibility_check", "payment_authentication"],
    Method.emandate: ["payment_authentication", "payment_authorization"],
}

#: Codes the taxonomy has never seen, from a source it does not recognise. Razorpay adds error
#: reasons over time, so a share of real traffic will always be unclassifiable — and the agent
#: has to behave conservatively when it is, rather than guessing.
UNKNOWN_REASONS = [
    "acquirer_unspecified_decline",
    "processor_response_unmapped",
    "network_advice_pending",
]
UNKNOWN_SOURCE = "acquirer"

CAUSE_REASONS: dict[RootCause, list[str]] = {}
for _reason, _cause in REASON_TO_CAUSE.items():
    CAUSE_REASONS.setdefault(_cause, []).append(_reason)


def _weighted_choice(rng, weights: dict[str, float]) -> str:
    keys = list(weights)
    values = [float(weights[k]) for k in keys]
    return rng.choices(keys, weights=values, k=1)[0]


def _lognormal(rng, spec: dict[str, Any]) -> int:
    value = float(spec["median"]) * math.exp(float(spec["sigma"]) * rng.gauss(0, 1))
    return int(max(float(spec["min"]), min(float(spec["max"]), value)))


def _amount(rng, spec: dict[str, Any]) -> int:
    if spec["kind"] == "choice":
        return int(rng.choices(spec["values"], weights=spec["weights"], k=1)[0])
    return _lognormal(rng, spec)


def _build_customers(world: World, count: int) -> dict[str, HiddenCustomer]:
    spec = world.config["customers"]
    customers: dict[str, HiddenCustomer] = {}
    for index in range(count):
        customer_id = f"cust_{index:04d}"
        rng = world.rng("customer", customer_id)
        thresholds = {int(k): float(v) for k, v in spec["opt_out_threshold"].items()}
        customers[customer_id] = HiddenCustomer(
            customer_id=customer_id,
            liquidity=_weighted_choice(rng, spec["liquidity"]),
            intent=rng.betavariate(*spec["intent_beta"]),
            channel_pref=Channel(_weighted_choice(rng, spec["channel"])),
            language_pref=Language(_weighted_choice(rng, spec["language"])),
            opt_out_threshold=int(
                rng.choices(list(thresholds), weights=list(thresholds.values()), k=1)[0]
            ),
            promise_reliability=rng.betavariate(*spec["promise_reliability_beta"]),
        )
    return customers


def generate(world: World, n: int = 500) -> tuple[list[Case], Population]:
    """Produce ``n`` cases and the hidden state behind them."""

    config = world.config
    mix = config["mix"]
    arrival_window = timedelta(days=int(config["clock"]["arrival_days"]))

    customers = _build_customers(world, max(1, int(n * 0.76)))
    population = Population(config=config, customers=customers)
    customer_ids = list(customers)

    cases: list[Case] = []
    for index in range(n):
        case_id = f"case_{index:04d}"
        rng = world.rng("case", case_id)

        scenario = Scenario(_weighted_choice(rng, mix["scenarios"]))
        method = Method(_weighted_choice(rng, mix["methods"][scenario.value]))
        amount = _amount(rng, config["amounts"][scenario.value])

        if scenario is Scenario.B:
            cause = RootCause.ABANDONED_CHECKOUT
        elif scenario is Scenario.D:
            cause = RootCause.OVERDUE_RECEIVABLE
        else:
            cause = RootCause[_weighted_choice(rng, mix["causes"][scenario.value])]

        error = None
        if cause not in (RootCause.ABANDONED_CHECKOUT, RootCause.OVERDUE_RECEIVABLE):
            if cause is RootCause.UNKNOWN:
                reason, source = rng.choice(UNKNOWN_REASONS), UNKNOWN_SOURCE
            else:
                reason = rng.choice(CAUSE_REASONS[cause])
                source = CAUSE_SOURCE.get(cause, "gateway")
            error = ErrorTriple(
                code="BAD_REQUEST_ERROR",
                reason=reason,
                source=source,
                step=rng.choice(METHOD_STEPS[method]),
                description=None,
            )

        created_at = world.start + timedelta(
            seconds=rng.random() * arrival_window.total_seconds()
        )
        customer_id = rng.choice(customer_ids)
        first_name = FIRST_NAMES[int(customer_id.split("_")[1]) % len(FIRST_NAMES)]
        merchant_id, merchant_name = rng.choice(MERCHANTS)

        due_at = created_at - timedelta(days=rng.randint(0, 10)) if scenario is Scenario.D else None

        case = Case(
            id=case_id,
            merchant_id=merchant_id,
            merchant_name=merchant_name,
            customer_id=customer_id,
            customer_first_name=first_name,
            customer_contact=f"+9199999{int(customer_id.split('_')[1]):05d}",
            customer_email=f"{first_name.lower()}@example.test",
            scenario=scenario,
            method=method,
            amount_paise=amount,
            error=error,
            created_at=created_at,
            due_at=due_at,
        )
        if scenario is Scenario.C and amount > 1_500_000:
            case.tags.append("afa_required")

        cases.append(case)
        population.cases[case_id] = _hidden_case(world, case, cause)

        if cause is RootCause.TRANSIENT_TECH:
            # A transient failure *is* an outage. Generating the cause independently of the
            # world's downtime schedule would quietly reward retrying into a dead bank, and
            # would make waiting for recovery look pointless when it is the whole point.
            _add_outage(world, case, rng)

    return cases, population


def _add_outage(world: World, case: Case, rng) -> None:
    from wapsi.sim.world import Downtime

    spec = world.config["world"]["downtime"]
    began = case.created_at - timedelta(minutes=rng.randint(1, 15))
    minutes = rng.randint(int(spec["min_minutes"]), int(spec["max_minutes"]))
    world.downtimes.append(
        Downtime(
            method=case.method.value,
            bank=rng.choice(spec["banks"]),
            start=began,
            end=began + timedelta(minutes=minutes),
        )
    )
    world.downtimes.sort(key=lambda d: d.start)


def _hidden_case(world: World, case: Case, cause: RootCause) -> HiddenCase:
    """Draw the things the agent must never see: organic payment, and merchant repair time."""

    rng = world.rng("hidden", case.id)
    organic = world.config["customers"]["organic"][case.scenario.value]
    organic_pay_at = None
    if rng.random() < float(organic["p"]):
        organic_pay_at = case.created_at + timedelta(
            hours=rng.random() * float(organic["within_hours"])
        )

    merchant_fix_at = None
    if cause is RootCause.MERCHANT_CONFIG:
        low, high = world.config["behaviour"]["MERCHANT_CONFIG"]["merchant_fix_hours"]
        merchant_fix_at = case.created_at + timedelta(hours=rng.uniform(float(low), float(high)))

    return HiddenCase(
        case_id=case.id, organic_pay_at=organic_pay_at, merchant_fix_at=merchant_fix_at
    )
