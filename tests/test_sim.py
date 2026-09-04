"""The batch itself: reproducible, realistically mixed, and fair between policies."""

from __future__ import annotations

import collections

import pytest

from wapsi.core.metrics import compute
from wapsi.core.models import Outcome, RootCause, Scenario
from wapsi.core.policy import PolicyEngine
from wapsi.sim.generator import generate
from wapsi.sim.runner import Runner
from wapsi.sim.world import World, load_config


def build_world(seed: int = 42) -> World:
    return World(config=load_config(), seed=seed)


@pytest.fixture(scope="module")
def batch():
    world = build_world()
    cases, population = generate(world, n=200)
    return world, cases, population


def test_the_same_seed_produces_the_same_batch():
    a_cases, _ = generate(build_world(7), n=50)
    b_cases, _ = generate(build_world(7), n=50)
    assert [c.model_dump_json() for c in a_cases] == [c.model_dump_json() for c in b_cases]


def test_a_different_seed_produces_a_different_batch():
    a_cases, _ = generate(build_world(7), n=50)
    b_cases, _ = generate(build_world(8), n=50)
    assert [c.id for c in a_cases] == [c.id for c in b_cases]
    assert [c.amount_paise for c in a_cases] != [c.amount_paise for c in b_cases]


def test_the_scenario_mix_matches_the_configured_shape(batch):
    _, cases, _ = batch
    counts = collections.Counter(c.scenario for c in cases)
    assert counts[Scenario.A] / len(cases) == pytest.approx(0.40, abs=0.08)
    assert counts[Scenario.B] / len(cases) == pytest.approx(0.20, abs=0.08)
    assert counts[Scenario.C] / len(cases) == pytest.approx(0.25, abs=0.08)
    assert counts[Scenario.D] / len(cases) == pytest.approx(0.15, abs=0.08)


def test_generated_errors_are_self_consistent(batch):
    """A generated failure must diagnose back to the cause it was drawn from."""

    from wapsi.core.taxonomy import REASON_TO_CAUSE, classify

    _, cases, _ = batch
    for case in cases:
        cause, _ = classify(case.error, case.scenario, case.amount_paise)
        assert cause is not None
        if case.scenario in (Scenario.A, Scenario.C):
            assert case.error is not None and case.error.reason
            if cause is RootCause.UNKNOWN:
                # Unknown must mean genuinely unmappable, not merely unmapped by accident.
                assert case.error.reason not in REASON_TO_CAUSE
            else:
                assert REASON_TO_CAUSE.get(case.error.reason) is cause


def test_the_batch_contains_cases_the_taxonomy_cannot_place(batch):
    """Real error vocabularies grow. A batch with no unknowns would not test the fallback."""

    from wapsi.core.taxonomy import classify

    _, cases, _ = batch
    unknown = [c for c in cases if classify(c.error, c.scenario, c.amount_paise)[0] is RootCause.UNKNOWN]
    assert unknown


def test_some_customers_carry_several_cases(batch):
    """Otherwise the per-customer messaging cap could never bind on anything."""

    _, cases, _ = batch
    counts = collections.Counter(c.customer_id for c in cases)
    assert max(counts.values()) > 1


def test_transient_failures_coincide_with_a_real_outage(batch):
    """A transient decline that happens while the bank is up is not a transient decline."""

    world, cases, _ = batch
    from wapsi.core.taxonomy import classify

    transient = [
        c for c in cases if classify(c.error, c.scenario, c.amount_paise)[0] is RootCause.TRANSIENT_TECH
    ]
    assert transient
    covered = sum(1 for c in transient if world.downtime_active(c.created_at, c.method.value))
    assert covered == len(transient)


def test_hidden_state_is_never_exposed_on_the_case(batch):
    """The agent must not be able to read the answer key off the object it plans against."""

    _, cases, _ = batch
    fields = set(cases[0].model_dump())
    for leak in ("organic_pay_at", "liquidity", "intent", "opt_out_threshold", "promise_reliability"):
        assert leak not in fields


def _run(policy_name: str, world, cases, population):
    engine = PolicyEngine.load()
    runner = Runner(world=world, cases=cases, population=population, policy=engine)
    return runner.run(policy_name)


def test_a_run_is_reproducible(batch):
    world, cases, population = batch
    first = compute(_run("rules", world, cases, population))
    second = compute(_run("rules", world, cases, population))
    assert first.as_dict() == second.as_dict()


def test_every_case_reaches_a_terminal_outcome(batch):
    world, cases, population = batch
    result = _run("rules", world, cases, population)
    assert all(case.outcome is not None for case in result.cases)
    assert len(result.cases) == len(cases)


def test_the_agent_breaks_no_rules_and_the_naive_policy_breaks_many(batch):
    world, cases, population = batch
    rules = compute(_run("rules", world, cases, population))
    naive = compute(_run("naive", world, cases, population))

    assert rules.violations == 0
    assert naive.violations > 0
    # And the rules it breaks are the ones that protect people, not bookkeeping.
    assert {"R10", "R05", "R15"} & set(naive.violation_rules)


def test_the_agent_recovers_more_than_doing_nothing(batch):
    world, cases, population = batch
    nothing = compute(_run("do_nothing", world, cases, population))
    rules = compute(_run("rules", world, cases, population))

    assert rules.recovered > nothing.recovered
    assert rules.net_paise > nothing.net_paise


def test_the_agent_never_contacts_a_customer_it_must_not(batch):
    """Risk declines and merchant faults must produce zero customer messages, always."""

    world, cases, population = batch
    result = _run("rules", world, cases, population)
    protected = {
        c.id for c in result.cases if c.root_cause in (RootCause.RISK_DECLINE, RootCause.MERCHANT_CONFIG)
    }
    assert protected
    assert not [m for m in result.messenger.sent if m.case_id in protected]


def test_opted_out_customers_are_left_alone(batch):
    world, cases, population = batch
    result = _run("rules", world, cases, population)
    for case in result.cases:
        if case.outcome is Outcome.opted_out:
            sent_after = [
                m
                for m in result.messenger.sent
                if m.case_id == case.id and case.closed_at and m.sent_at > case.closed_at
            ]
            assert not sent_after
