"""The live adapter, driven by a stub account.

What matters here is that a real Razorpay payment entity turns into the same kind of case the
simulation produces, that the idempotency check reads the right entity, and that the parts test
mode cannot do are reported honestly rather than faked.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from wapsi.adapters.razorpay_live import LiveGateway
from wapsi.config import IST
from wapsi.core.models import Method, RootCause, Scenario
from wapsi.core.taxonomy import diagnose
from wapsi.live.poller import case_from_payment

FAILED_PAYMENT = {
    "id": "pay_LIVE001",
    "amount": 129900,
    "currency": "INR",
    "status": "failed",
    "method": "upi",
    "email": "aarav@example.test",
    "contact": "+919999900001",
    "created_at": 1788500000,
    "order_id": "order_LIVE001",
    "error_code": "BAD_REQUEST_ERROR",
    "error_description": "Payment failed",
    "error_source": "gateway",
    "error_step": "payment_authorization",
    "error_reason": "bank_not_available",
    "notes": {},
}


class StubResource:
    def __init__(self, entities: dict[str, Any], page: dict[str, Any] | None = None):
        self.entities = entities
        self.page = page or {"items": []}
        self.created: list[dict[str, Any]] = []
        self.notified: list[tuple[str, str]] = []

    def fetch(self, entity_id):
        if entity_id not in self.entities:
            raise KeyError(entity_id)
        return self.entities[entity_id]

    def all(self, data=None):
        return self.page

    def create(self, payload):
        self.created.append(payload)
        return {"id": "plink_created", "short_url": "https://rzp.io/i/new01", **payload}

    def notifyBy(self, entity_id, medium):  # noqa: N802 - the SDK spells it this way
        self.notified.append((entity_id, medium))
        return True

    def notify_by(self, entity_id, medium):
        self.notified.append((entity_id, medium))
        return True


class StubClient:
    def __init__(self, links=None, invoices=None, subscriptions=None, payments=None, orders=None):
        self.payment_link = StubResource(links or {})
        self.invoice = StubResource(invoices or {})
        self.subscription = StubResource(subscriptions or {})
        self.order = StubResource(orders or {})
        self.payment = StubResource({}, page={"items": payments or []})


def test_a_real_failed_payment_becomes_a_diagnosable_case():
    case = case_from_payment(FAILED_PAYMENT, "Chai Point")

    assert case.scenario is Scenario.A
    assert case.method is Method.upi
    assert case.amount_paise == 129900
    assert case.customer_contact == "+919999900001"
    assert case.razorpay["payment_id"] == "pay_LIVE001"
    assert case.razorpay["order_id"] == "order_LIVE001"

    # The whole point: Razorpay's own error fields drive the diagnosis, unchanged.
    cause, _, text = diagnose(case)
    assert cause is RootCause.TRANSIENT_TECH
    assert "bank_not_available" in text


def test_an_unfamiliar_error_reason_is_still_handled_conservatively():
    payment = {**FAILED_PAYMENT, "error_reason": "some_code_added_next_year", "error_source": "acquirer"}
    cause, _, _ = diagnose(case_from_payment(payment))
    assert cause is RootCause.UNKNOWN


def test_refresh_prefers_the_recovery_link_over_the_original():
    """A link the agent created supersedes whatever failed first, so it is checked first."""

    client = StubClient(
        links={
            "plink_original": {"id": "plink_original", "status": "expired"},
            "plink_recovery": {"id": "plink_recovery", "status": "paid"},
        }
    )
    gateway = LiveGateway(client)
    case = case_from_payment(FAILED_PAYMENT)
    case.razorpay["payment_link_id"] = "plink_original"
    case.razorpay["recovery_link_id"] = "plink_recovery"

    result = gateway.refresh(case, datetime.now(IST))
    assert result["paid"] and result["entity"] == "plink_recovery"


def test_refresh_reports_unpaid_when_nothing_has_settled():
    client = StubClient(links={"plink_original": {"id": "plink_original", "status": "created"}})
    gateway = LiveGateway(client)
    case = case_from_payment(FAILED_PAYMENT)
    case.razorpay["payment_link_id"] = "plink_original"
    assert not gateway.refresh(case, datetime.now(IST))["paid"]


def test_a_missing_entity_does_not_break_the_poll():
    """A fetch that fails mid-poll must be survivable; the next poll tries again."""

    gateway = LiveGateway(StubClient())
    case = case_from_payment(FAILED_PAYMENT)
    case.razorpay["payment_link_id"] = "plink_gone"
    assert not gateway.refresh(case, datetime.now(IST))["paid"]
    assert any(not call["ok"] for call in gateway.calls)


def test_a_recovery_link_is_tagged_back_to_its_case():
    client = StubClient()
    gateway = LiveGateway(client)
    case = case_from_payment(FAILED_PAYMENT)
    case.root_cause = RootCause.TRANSIENT_TECH
    now = datetime.now(IST)

    gateway.create_payment_link(case, now, description="retry", expire_by=now + timedelta(days=3))

    payload = client.payment_link.created[0]
    assert payload["notes"]["wapsi_case_id"] == case.id
    assert payload["notes"]["root_cause"] == "TRANSIENT_TECH"
    assert payload["amount"] == case.amount_paise
    # Wapsi owns the reminder cadence; the platform's own would break the policy caps.
    assert payload["reminder_enable"] is False
    assert payload["notify"] == {"sms": False, "email": False}


def test_notifications_use_the_right_spelling_per_entity():
    """The SDK is camelCase on payment links and snake_case on invoices. Wrapped once, here."""

    client = StubClient()
    gateway = LiveGateway(client)

    assert gateway.notify("payment_link", "plink_1", "sms")["success"]
    assert gateway.notify("invoice", "inv_1", "email")["success"]
    assert client.payment_link.notified == [("plink_1", "sms")]
    assert client.invoice.notified == [("inv_1", "email")]


def test_notifying_an_entity_that_cannot_be_notified_fails_softly():
    gateway = LiveGateway(StubClient())
    result = gateway.notify("subscription", "sub_1", "sms")
    assert not result["success"] and "cannot notify" in result["reason"]


def test_retrying_a_charge_is_reported_as_unavailable_not_faked():
    """Test mode has no server-side charge. Saying so is the honest option, and the agent then
    falls through to the action it can actually take."""

    gateway = LiveGateway(StubClient())
    result = gateway.retry_charge(case_from_payment(FAILED_PAYMENT), datetime.now(IST))

    assert result["attempted"] is False
    assert result["success"] is False
    assert "test mode" in result["reason"]
    assert LiveGateway.supports_retry is False


def test_only_failed_payments_are_picked_up():
    client = StubClient(
        payments=[
            FAILED_PAYMENT,
            {**FAILED_PAYMENT, "id": "pay_OK", "status": "captured"},
        ]
    )
    gateway = LiveGateway(client)
    found = gateway.failed_payments_since(datetime.now(IST) - timedelta(hours=1))
    assert [p["id"] for p in found] == ["pay_LIVE001"]


def test_dry_run_touches_no_account():
    gateway = LiveGateway(StubClient(), dry_run=True)
    case = case_from_payment(FAILED_PAYMENT)
    link = gateway.create_payment_link(case, datetime.now(IST))
    assert link["id"] == "plink_dryrun"
    assert gateway.notify("payment_link", "plink_1", "sms")["dry_run"]


def test_a_customer_who_pays_the_original_order_is_not_chased():
    """The case carries an order id and no link id. If the order settles by any route — the
    original link, a fresh checkout, a phone call to the merchant — we must notice, or we chase
    somebody who has already paid. That is the one thing this system must never do."""

    client = StubClient(orders={"order_LIVE001": {"id": "order_LIVE001", "status": "paid"}})
    gateway = LiveGateway(client)
    case = case_from_payment(FAILED_PAYMENT)
    assert case.razorpay.get("order_id") == "order_LIVE001"
    assert not case.razorpay.get("payment_link_id"), "this case has no link, only an order"

    result = gateway.refresh(case, datetime.now(IST))
    assert result["paid"] and result["entity"] == "order_LIVE001"


def test_a_partially_paid_order_counts_as_paid():
    client = StubClient(
        orders={"order_LIVE001": {"id": "order_LIVE001", "status": "attempted", "amount_paid": 50000}}
    )
    gateway = LiveGateway(client)
    assert gateway.refresh(case_from_payment(FAILED_PAYMENT), datetime.now(IST))["paid"]


def test_an_unpaid_order_does_not_stop_recovery():
    client = StubClient(
        orders={"order_LIVE001": {"id": "order_LIVE001", "status": "attempted", "amount_paid": 0}}
    )
    gateway = LiveGateway(client)
    assert not gateway.refresh(case_from_payment(FAILED_PAYMENT), datetime.now(IST))["paid"]


def test_the_recovery_link_is_still_checked_before_the_order():
    """Both can be paid; the agent's own link is the more specific answer."""

    client = StubClient(
        links={"plink_recovery": {"id": "plink_recovery", "status": "paid"}},
        orders={"order_LIVE001": {"id": "order_LIVE001", "status": "paid"}},
    )
    gateway = LiveGateway(client)
    case = case_from_payment(FAILED_PAYMENT)
    case.razorpay["recovery_link_id"] = "plink_recovery"
    assert gateway.refresh(case, datetime.now(IST))["entity"] == "plink_recovery"


def test_the_listing_follows_pagination_to_the_end():
    """One page would be silent data loss: the caller advances its cursor past the whole window,
    so anything beyond the first page would never be seen again."""

    class Paged(StubResource):
        def __init__(self, total):
            super().__init__({})
            self.total = total
            self.requests = []

        def all(self, data=None):
            data = data or {}
            skip, count = data.get("skip", 0), data.get("count", 100)
            self.requests.append((skip, count))
            items = [
                {**FAILED_PAYMENT, "id": f"pay_{i:04d}"} for i in range(self.total)
            ][skip : skip + count]
            return {"items": items}

    client = StubClient()
    client.payment = Paged(total=250)
    gateway = LiveGateway(client)

    found = gateway.failed_payments_since(datetime.now(IST) - timedelta(hours=6), count=100)
    assert len(found) == 250, "every failed payment must be returned, not just the first page"
    assert gateway.last_fetch_complete is True
    assert [r[0] for r in client.payment.requests] == [0, 100, 200]


def test_a_truncated_listing_is_reported_so_the_cursor_can_be_held():
    class AlwaysFull(StubResource):
        def all(self, data=None):
            count = (data or {}).get("count", 100)
            return {"items": [{**FAILED_PAYMENT, "id": f"pay_x{i}"} for i in range(count)]}

    client = StubClient()
    client.payment = AlwaysFull({})
    gateway = LiveGateway(client)
    gateway.failed_payments_since(datetime.now(IST), count=10, max_pages=3)
    assert gateway.last_fetch_complete is False


def test_a_failed_listing_does_not_claim_completeness():
    class Broken(StubResource):
        def all(self, data=None):
            raise RuntimeError("network")

    client = StubClient()
    client.payment = Broken({})
    gateway = LiveGateway(client)
    assert gateway.failed_payments_since(datetime.now(IST)) == []
    assert gateway.last_fetch_complete is False
