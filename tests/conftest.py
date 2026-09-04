from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from wapsi.config import IST
from wapsi.core.models import Action, ActionType, Case, ErrorTriple, Method, RootCause, Scenario
from wapsi.core.policy import PolicyContext, PolicyEngine

#: A Monday at 12:00 IST — inside the customer messaging window, inside the NPCI morning peak.
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=IST)


@pytest.fixture
def engine() -> PolicyEngine:
    return PolicyEngine.load()


@pytest.fixture
def policy(engine: PolicyEngine) -> dict:
    return engine.policy


@pytest.fixture
def ctx() -> PolicyContext:
    return PolicyContext()


@pytest.fixture
def make_case():
    def _make(**overrides) -> Case:
        defaults = dict(
            id="case_1",
            customer_id="cust_1",
            customer_first_name="Aarav",
            customer_contact="+919999900001",
            merchant_name="Chai Point",
            scenario=Scenario.A,
            method=Method.upi,
            amount_paise=129_900,
            created_at=NOW - timedelta(hours=2),
            root_cause=RootCause.INSUFFICIENT_FUNDS,
            error=ErrorTriple(reason="insufficient_funds", source="customer", step="payment_authorization"),
        )
        defaults.update(overrides)
        return Case(**defaults)

    return _make


@pytest.fixture
def denials(engine: PolicyEngine, ctx: PolicyContext):
    """Rule ids blocking an action right now, with the engine's own default parameters."""

    def _denials(
        case: Case,
        action_type: ActionType,
        now: datetime = NOW,
        context: PolicyContext | None = None,
        **params,
    ) -> list[str]:
        action = Action(
            case_id=case.id,
            type=action_type,
            params={**engine.default_params(case, action_type), **params},
        )
        action.cost_paise = engine.cost_of(action)
        return [d.rule_id for d in engine.check_all(action, case, now, context or ctx)]

    return _denials


@pytest.fixture
def earliest(engine: PolicyEngine, ctx: PolicyContext):
    """When a blocked action first becomes legal, per a given rule."""

    def _earliest(
        case: Case,
        action_type: ActionType,
        rule_id: str,
        now: datetime = NOW,
        context: PolicyContext | None = None,
    ):
        action = Action(
            case_id=case.id,
            type=action_type,
            params=engine.default_params(case, action_type),
        )
        for denial in engine.check_all(action, case, now, context or ctx):
            if denial.rule_id == rule_id:
                return denial.earliest_at
        return None

    return _earliest


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Point all live-mode persistence at a temporary directory.

    Any test that exercises the webhook endpoint needs this: the endpoint creates and saves a
    real case, and without isolation it writes into the developer's own `.live/` state. That
    happened, and a test fixture's case turned up in a live run.
    """

    from wapsi.api import app as app_module
    from wapsi.live import state as live_state

    # One line, because every state path is resolved from STATE_DIR at call time. Patching paths
    # individually is what let a new file escape isolation twice.
    monkeypatch.setattr(live_state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(app_module, "RESULTS_DIR", tmp_path / "results")
    return tmp_path
