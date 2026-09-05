"""Webhook handling: verify, dedupe, normalise.

No network. Payloads are shaped like Razorpay's, and the signatures are computed the way Razorpay
computes them, so the checks here are the same ones a real delivery meets.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from bharpai.live.webhook import Receiver, normalise, verify

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


def test_the_endpoint_accepts_both_paths(isolated_state):
    """A pasted webhook URL can lose its path. That silently dropped every delivery once."""

    from fastapi.testclient import TestClient

    from bharpai.api.app import create_app

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

    from bharpai.api.app import create_app

    client = TestClient(create_app())
    body = client.get("/health").json()
    assert body["ok"]
    assert len(body["webhook_secret"]) in (8, 5)  # eight hex chars, or the word "unset"


def test_a_verified_failure_becomes_a_case_the_poller_can_act_on(isolated_state):
    """The point of a webhook over polling: the case exists the moment Razorpay says so."""

    from fastapi.testclient import TestClient

    from bharpai.api import app as app_module
    from bharpai.live import state

    app = app_module.create_app()
    app.state.receiver = Receiver(SECRET)
    client = TestClient(app)

    body = json.dumps(payment_failed_payload()).encode()
    headers = {"X-Razorpay-Signature": sign(body), "X-Razorpay-Event-Id": "evt_case"}
    response = client.post("/webhooks/razorpay", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json()["case_id"] == "live_pay_TESTFAILED001"
    cases = state.load_cases()
    assert "live_pay_TESTFAILED001" in cases
    assert cases["live_pay_TESTFAILED001"].root_cause is not None

    # A retried delivery must not create a second case.
    headers["X-Razorpay-Event-Id"] = "evt_case_retry"
    client.post("/webhooks/razorpay", content=body, headers=headers)
    assert len(state.load_cases()) == 1


def test_an_appending_audit_log_keeps_what_was_already_there(tmp_path):
    from datetime import datetime

    from bharpai.config import IST
    from bharpai.core.audit import AuditLog

    path = tmp_path / "audit.jsonl"
    first = AuditLog(path)
    first.record(ts=datetime.now(IST), case_id="c1", kind="observation", actor="system", summary="one")

    second = AuditLog(path, truncate=False)
    second.record(ts=datetime.now(IST), case_id="c1", kind="action", actor="planner", summary="two")

    entries = AuditLog.read(path)
    assert [e.summary for e in entries] == ["one", "two"]
    assert [e.seq for e in entries] == [1, 2]


def test_the_weekly_message_cap_survives_a_restart(isolated_state):
    """R23 is derived from what has been sent. Holding that only in memory means a restart
    silently re-opens the budget and the same person is messaged again."""

    from datetime import datetime, timedelta

    from bharpai.config import IST
    from bharpai.core.models import Channel, Language, Message, Tone
    from bharpai.live import state

    now = datetime.now(IST)
    sent = [
        Message(
            case_id=f"c{i}",
            channel=Channel.sms,
            to="+919999900001",
            text="hi",
            language=Language.en,
            tone=Tone.soft,
            sent_at=now - timedelta(days=i),
            cost_paise=20,
        )
        for i in range(1, 6)
    ]
    state.save_messages(sent)

    restored = state.load_messages()
    assert len(restored) == 5
    assert restored[0].to == "+919999900001"
    assert restored[0].sent_at.tzinfo is not None, "timestamps must survive the round trip"
