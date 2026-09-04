"""The baselines have to be fair, or the comparison is worthless.

The platform baseline in particular is what a reviewer should demand: not a caricature of bad
automation, but Razorpay's own defaults. If Wapsi cannot beat that, the rest is noise.
"""

from __future__ import annotations

from datetime import timedelta

from wapsi.core.metrics import compute
from wapsi.core.models import ActionType, Method, RootCause, Scenario
from wapsi.core.policy import PolicyContext, PolicyEngine
from wapsi.sim.baselines import PLATFORM_LADDER, PLATFORM_REMINDERS, PlatformPlanner
from wapsi.sim.generator import generate
from wapsi.sim.runner import Runner
from wapsi.sim.world import HOSTILE_ASSUMPTIONS, World, apply_overrides, load_config

from tests.conftest import NOW


def test_the_platform_never_retries_a_one_off_payment(engine, make_case):
    """Razorpay does not re-attempt a failed one-off payment on its own."""

    case = make_case(root_cause=RootCause.TRANSIENT_TECH)
    for hours in (0, 1, 24, 72):
        decision = PlatformPlanner(engine).plan(case, NOW + timedelta(hours=hours), PolicyContext())
        assert decision.action.type is not ActionType.RETRY_CHARGE


def test_the_platform_runs_its_subscription_ladder(engine, make_case):
    case = make_case(scenario=Scenario.C, method=Method.upi_autopay, root_cause=RootCause.INSUFFICIENT_FUNDS)
    planner = PlatformPlanner(engine)

    too_early = planner.plan(case, case.created_at + timedelta(hours=6), PolicyContext())
    assert too_early.action.type is ActionType.WAIT

    due = planner.plan(case, case.created_at + PLATFORM_LADDER[0], PolicyContext())
    assert due.action.type is ActionType.RETRY_CHARGE
    assert due.action.params.get("platform") is True


def test_platform_reminders_are_generic_and_sent_in_daytime(engine, make_case):
    case = make_case(root_cause=RootCause.CUSTOMER_INPUT)
    planner = PlatformPlanner(engine)

    first = planner.plan(case, case.created_at + timedelta(minutes=5), PolicyContext())
    assert first.action.type is ActionType.WAIT
    assert first.action.scheduled_at.hour == 11

    sent = planner.plan(case, case.created_at + PLATFORM_REMINDERS[0] + timedelta(hours=12), PolicyContext())
    assert sent.action.type is ActionType.SEND_REMINDER
    # It never diagnosed anything, so it has nothing specific to say.
    assert sent.action.params.get("generic") is True
    assert sent.action.params.get("platform") is True


def _batch(n=150, overrides=None):
    world = World(config=apply_overrides(load_config(), overrides or {}), seed=42)
    cases, population = generate(world, n=n)
    return world, cases, population


def test_the_platform_sits_between_nothing_and_the_agent():
    world, cases, population = _batch()
    engine = PolicyEngine.load()
    runner = Runner(world=world, cases=cases, population=population, policy=engine)

    nothing = compute(runner.run("do_nothing"))
    platform = compute(runner.run("platform"))
    rules = compute(runner.run("rules"))

    assert platform.recovered >= nothing.recovered
    assert rules.net_paise > platform.net_paise, "if the agent cannot beat the platform's own defaults there is nothing to submit"


def test_platform_actions_are_not_scored_as_merchant_violations():
    world, cases, population = _batch()
    runner = Runner(world=world, cases=cases, population=population, policy=PolicyEngine.load())
    assert compute(runner.run("platform")).violations == 0


def test_the_agent_still_wins_when_the_simulation_stops_punishing_carelessness():
    """Strip every assumption that flatters the compliant policy. The ranking must survive."""

    world, cases, population = _batch(overrides=HOSTILE_ASSUMPTIONS)
    engine = PolicyEngine.load()
    engine.economics["dispute_cost_paise"] = 0
    runner = Runner(world=world, cases=cases, population=population, policy=engine)

    naive = compute(runner.run("naive"))
    rules = compute(runner.run("rules"))
    assert rules.net_paise > naive.net_paise


def test_overrides_do_not_mutate_the_source_config():
    base = load_config()
    changed = apply_overrides(base, HOSTILE_ASSUMPTIONS)
    assert base["modifiers"]["out_of_hours"]["annoyance"] == 3.0
    assert changed["modifiers"]["out_of_hours"]["annoyance"] == 1.0
