"""Webhook handling: verify, dedupe, normalise.

No network. Payloads are shaped like Razorpay's, and the signatures are computed the way Razorpay
computes them, so the checks here are the same ones a real delivery meets.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from wapsi.live.webhook import Receiver, normalise, verify

SECRET = "a-test-webhook-secret"


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def payment_failed_payload() -> dict:
    """The shape Razorpay sends, with the error fields the whole diagnosis depends on."""

    return {
        "entity": "event",
        "account_id": "acc_test",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_TESTFAILED001",
                    "amount": 129900,
                    "currency": "INR",
                    "status": "failed",
                    "method": "upi",
                    "email": "aarav@example.test",
                    "contact": "+919999900001",
                    "created_at": 1788500000,
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_description": "Your payment was declined due to insufficient balance",
                    "error_source": "customer",
                    "error_step": "payment_authorization",
                    "error_reason": "insufficient_funds",
                }
            }
        },
        "created_at": 1788500001,
    }


def test_a_correct_signature_is_accepted():
    body = json.dumps(payment_failed_payload()).encode()
    assert verify(body, sign(body), SECRET)


def test_a_wrong_signature_is_rejected():
    body = json.dumps(payment_failed_payload()).encode()
    assert not verify(body, sign(body, "the-wrong-secret"), SECRET)
    assert not verify(body, "", SECRET)
    assert not verify(body, sign(body), "")


def test_a_tampered_body_is_rejected():
    """The signature covers the raw bytes, so changing the amount must invalidate it."""

    original = json.dumps(payment_failed_payload()).encode()
    signature = sign(original)
    tampered = original.replace(b"129900", b"999900")
    assert not verify(tampered, signature, SECRET)


def test_an_unsigned_delivery_gets_a_400():
    receiver = Receiver(SECRET)
    body = json.dumps(payment_failed_payload()).encode()
    status, result = receiver.handle(body, "", "evt_1")
    assert status == 400 and not result["ok"]
    assert receiver.stats()["rejected"] == 1


def test_a_valid_delivery_is_accepted_once_and_then_deduplicated():
    """Razorpay retries deliveries. Acting twice on one failure is worse than acting late."""

    receiver = Receiver(SECRET)
    body = json.dumps(payment_failed_payload()).encode()

    status, first = receiver.handle(body, sign(body), "evt_abc")
    assert status == 200 and first["kind"] == "failure"

    status, second = receiver.handle(body, sign(body), "evt_abc")
    assert status == 200 and second.get("duplicate")
    assert receiver.stats() == {"accepted": 1, "duplicates": 1, "rejected": 0, "ignored": 0}


def test_an_unhandled_event_is_acknowledged_not_errored():
    """A 200 with no action beats an error that makes Razorpay retry forever."""

    receiver = Receiver(SECRET)
    body = json.dumps({"event": "settlement.processed", "payload": {}}).encode()
    status, result = receiver.handle(body, sign(body), "evt_settle")
    assert status == 200 and result["ok"] and result["ignored"] == "settlement.processed"


def test_a_malformed_body_is_rejected():
    receiver = Receiver(SECRET)
    body = b"{not json at all"
    status, _ = receiver.handle(body, sign(body), "evt_bad")
    assert status == 400


def test_normalising_a_failure_keeps_the_error_triple():
    result = normalise(payment_failed_payload())
    assert result["kind"] == "failure"
    assert result["error"] == {
        "code": "BAD_REQUEST_ERROR",
        "reason": "insufficient_funds",
        "source": "customer",
        "step": "payment_authorization",
        "description": "Your payment was declined due to insufficient balance",
    }


@pytest.mark.parametrize(
    "event,kind",
    [
        ("payment.captured", "paid"),
        ("payment_link.paid", "paid"),
        ("invoice.paid", "paid"),
        ("subscription.halted", "subscription_at_risk"),
        ("subscription.pending", "subscription_at_risk"),
        ("payment.dispute.created", "disputed"),
        ("refund.created", "refunded"),
        ("payment.downtime.started", "downtime_started"),
    ],
)
def test_every_subscribed_event_maps_to_something_actionable(event, kind):
    assert normalise({"event": event, "payload": {}})["kind"] == kind


def test_an_unknown_event_normalises_to_nothing():
    assert normalise({"event": "engage.rewards.enabled", "payload": {}}) is None


def test_the_endpoint_accepts_both_paths():
    """A pasted webhook URL can lose its path. That silently dropped every delivery once."""

    from fastapi.testclient import TestClient

    from wapsi.api.app import create_app

    app = create_app()
    app.state.receiver = Receiver(SECRET)
    client = TestClient(app)

    body = json.dumps(payment_failed_payload()).encode()
    headers = {"X-Razorpay-Signature": sign(body), "X-Razorpay-Event-Id": "evt_path"}

    assert client.post("/webhooks/razorpay", content=body, headers=headers).status_code == 200
    headers["X-Razorpay-Event-Id"] = "evt_path_root"
    assert client.post("/", content=body, headers=headers).status_code == 200


def test_health_reports_a_fingerprint_not_the_secret():
    from fastapi.testclient import TestClient

    from wapsi.api.app import create_app

    client = TestClient(create_app())
    body = client.get("/health").json()
    assert body["ok"]
    assert len(body["webhook_secret"]) in (8, 5)  # eight hex chars, or the word "unset"
