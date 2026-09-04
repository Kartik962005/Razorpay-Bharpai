"""Webhook ingestion: verify, dedupe, normalise.

An optional faster path to the same events the poller finds. Three properties matter:

* **Verified.** Every request must carry a valid ``X-Razorpay-Signature`` for the configured
  secret. An unsigned or wrongly-signed request is rejected, not merely logged.
* **Deduplicated.** Razorpay retries deliveries, so the same event id arrives more than once. A
  recovery agent that acts twice on one failure is worse than one that acts late.
* **Normalised.** Events become the same objects the poller produces, so nothing downstream knows
  or cares which path a case arrived by.
"""

from __future__ import annotations

import hashlib
import hmac
from collections import deque
from typing import Any

#: The events worth reacting to. Anything else is acknowledged and ignored, because a 200 with no
#: action is far better than an error that makes Razorpay retry forever.
HANDLED = {
    "payment.failed": "failure",
    "payment.captured": "paid",
    "order.paid": "paid",
    "payment_link.paid": "paid",
    "payment_link.expired": "expired",
    "invoice.paid": "paid",
    "invoice.expired": "expired",
    "invoice.partially_paid": "partial",
    "subscription.pending": "subscription_at_risk",
    "subscription.halted": "subscription_at_risk",
    "subscription.charged": "paid",
    "subscription.cancelled": "cancelled",
    "payment.dispute.created": "disputed",
    "refund.created": "refunded",
    "payment.downtime.started": "downtime_started",
    "payment.downtime.resolved": "downtime_resolved",
}


def verify(body: bytes, signature: str, secret: str) -> bool:
    """Constant-time check of Razorpay's HMAC-SHA256 over the raw request body."""

    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def normalise(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Reduce a webhook payload to the few fields the agent acts on."""

    event = payload.get("event")
    kind = HANDLED.get(str(event))
    if kind is None:
        return None

    entities = payload.get("payload") or {}

    def entity(name: str) -> dict[str, Any]:
        return (entities.get(name) or {}).get("entity") or {}

    payment = entity("payment")
    result: dict[str, Any] = {"event": event, "kind": kind, "payment": payment or None}

    for name in ("payment_link", "invoice", "subscription", "order", "refund", "dispute"):
        found = entity(name)
        if found:
            result[name] = found

    if kind == "failure" and payment:
        result["error"] = {
            "code": payment.get("error_code"),
            "reason": payment.get("error_reason"),
            "source": payment.get("error_source"),
            "step": payment.get("error_step"),
            "description": payment.get("error_description"),
        }
    return result


class Receiver:
    """Holds the recently seen event ids so a retried delivery is not acted on twice."""

    def __init__(self, secret: str, memory: int = 2000):
        self.secret = secret
        self._seen: deque[str] = deque(maxlen=memory)
        self._index: set[str] = set()
        self.accepted = 0
        self.duplicates = 0
        self.rejected = 0
        self.ignored = 0

    def seen(self, event_id: str) -> bool:
        return event_id in self._index

    def remember(self, event_id: str) -> None:
        if len(self._seen) == self._seen.maxlen and self._seen:
            self._index.discard(self._seen[0])
        self._seen.append(event_id)
        self._index.add(event_id)

    def handle(self, body: bytes, signature: str, event_id: str) -> tuple[int, dict[str, Any]]:
        """Return the HTTP status and a small result body for one delivery."""

        if not verify(body, signature, self.secret):
            self.rejected += 1
            return 400, {"ok": False, "reason": "signature mismatch"}

        if event_id and self.seen(event_id):
            self.duplicates += 1
            return 200, {"ok": True, "duplicate": True}
        if event_id:
            self.remember(event_id)

        import json

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.rejected += 1
            return 400, {"ok": False, "reason": "malformed body"}

        normalised = normalise(payload)
        if normalised is None:
            self.ignored += 1
            return 200, {"ok": True, "ignored": payload.get("event")}

        self.accepted += 1
        return 200, {"ok": True, "event": normalised["event"], "kind": normalised["kind"]}

    def stats(self) -> dict[str, int]:
        return {
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "ignored": self.ignored,
        }
