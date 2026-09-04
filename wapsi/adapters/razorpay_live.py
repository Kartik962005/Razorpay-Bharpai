"""The real Razorpay gateway, in test mode.

Same interface as the simulated one, so the planner, policy engine and executor cannot tell which
of the two they are driving. What differs is what the API can and cannot do:

* Creating payment links, invoices and notifications is fully supported, and everything the agent
  creates is visible in the merchant's dashboard.
* **A charge cannot be initiated from the server.** Test mode has no endpoint to re-attempt a
  payment without the customer authenticating it, so ``retry_charge`` reports that honestly
  rather than pretending. The agent then does what it would do for any unavailable action: falls
  through to the next best one, which is a recovery link.

Every call is logged with its latency so the demo can show what was really sent.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from wapsi.core.models import Case, Method

#: Which entity decides whether this case has been paid. Checked newest-first, because a
#: recovery link the agent created supersedes whatever failed originally.
PAID_STATUSES = {"paid", "captured", "active", "completed"}


class LiveGateway:
    """Wraps the Razorpay SDK. Read-only except for creating links and sending notifications."""

    supports_retry = False

    def __init__(self, client: Any, *, dry_run: bool = False):
        self.client = client
        self.dry_run = dry_run
        self.calls: list[dict[str, Any]] = []
        #: False when the last listing was truncated or errored, so the caller knows not to
        #: advance a cursor past events it has not actually read.
        self.last_fetch_complete = True

    def _record(self, name: str, ok: bool, started: float, detail: str = "") -> None:
        self.calls.append(
            {
                "call": name,
                "ok": ok,
                "ms": round((time.perf_counter() - started) * 1000),
                "detail": detail,
            }
        )

    def _fetch(self, resource: str, entity_id: str) -> dict[str, Any] | None:
        started = time.perf_counter()
        try:
            entity = getattr(self.client, resource).fetch(entity_id)
            self._record(f"{resource}.fetch", True, started, entity_id)
            return entity
        except Exception as exc:  # noqa: BLE001 - a polling failure must not end the run
            self._record(f"{resource}.fetch", False, started, f"{entity_id}: {exc}")
            return None

    # -- gateway interface --------------------------------------------------------------------

    def refresh(self, case: Case, now: datetime) -> dict[str, Any]:
        """Has this money arrived, by *any* route?

        The order is checked as well as the links, and this matters more than it looks. A case
        born from a failed payment carries an order id but no link id; if the customer then pays
        the original link, or retries checkout themselves, the order settles and no link we know
        about changes. Checking only our own recovery link would leave us chasing someone who has
        already paid — the exact failure this system exists to prevent.
        """

        checks = [
            # Newest first: a link the agent created supersedes whatever failed originally.
            ("payment_link", case.razorpay.get("recovery_link_id")),
            # The order is the authoritative unit of payment for a one-off purchase.
            ("order", case.razorpay.get("order_id")),
            ("payment_link", case.razorpay.get("payment_link_id")),
            ("invoice", case.razorpay.get("invoice_id")),
            ("subscription", case.razorpay.get("subscription_id")),
        ]
        for resource, entity_id in checks:
            if not entity_id:
                continue
            entity = self._fetch(resource, entity_id)
            if entity is None:
                continue
            status = str(entity.get("status", "")).lower()
            if status in PAID_STATUSES:
                return {"paid": True, "status": status, "entity": entity_id}
            # An order that has been paid partially or fully reports it as an amount, not only
            # as a status, depending on how it was created.
            if resource == "order" and entity.get("amount_paid"):
                return {"paid": True, "status": "paid", "entity": entity_id}
        return {"paid": False, "status": "unpaid"}

    def create_payment_link(
        self,
        case: Case,
        now: datetime,
        *,
        description: str = "",
        expire_by: datetime | None = None,
        method_hint: Method | None = None,
    ) -> dict[str, Any]:
        """Create a real recovery link, tagged so it can be traced back to this case."""

        payload: dict[str, Any] = {
            "amount": case.amount_paise,
            "currency": case.currency,
            "description": (description or f"{case.merchant_name} payment")[:255],
            "reference_id": f"wapsi-{case.id}-{int(now.timestamp())}",
            "customer": {
                "name": case.customer_first_name,
                "email": case.customer_email or None,
                "contact": case.customer_contact or None,
            },
            # Wapsi owns the reminder schedule; the platform's own cadence would double up on it
            # and break the caps the policy engine enforces.
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": {
                "wapsi_case_id": case.id,
                "root_cause": (case.root_cause.value if case.root_cause else "UNKNOWN"),
                "method_hint": method_hint.value if method_hint else "",
            },
        }
        if expire_by is not None:
            # Razorpay requires at least 15 minutes in the future.
            payload["expire_by"] = int(expire_by.timestamp())

        if self.dry_run:
            return {"id": "plink_dryrun", "short_url": "https://rzp.io/i/dryrun"}

        started = time.perf_counter()
        try:
            link = self.client.payment_link.create(payload)
            self._record("payment_link.create", True, started, link.get("id", ""))
            return link
        except Exception as exc:  # noqa: BLE001
            self._record("payment_link.create", False, started, str(exc))
            raise

    def notify(self, entity_type: str, entity_id: str, medium: str) -> dict[str, Any]:
        """Ask Razorpay to deliver its own notification.

        The SDK spells this differently for the two entities that support it — ``notifyBy`` on a
        payment link, ``notify_by`` on an invoice — which is worth wrapping once here.
        """

        if self.dry_run:
            return {"success": True, "dry_run": True}

        started = time.perf_counter()
        try:
            if entity_type == "payment_link":
                result = self.client.payment_link.notifyBy(entity_id, medium)
            elif entity_type == "invoice":
                result = self.client.invoice.notify_by(entity_id, medium)
            else:
                return {"success": False, "reason": f"cannot notify on {entity_type}"}
            self._record(f"{entity_type}.notify", True, started, f"{entity_id} via {medium}")
            return {"success": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            self._record(f"{entity_type}.notify", False, started, str(exc))
            return {"success": False, "reason": str(exc)}

    def retry_charge(self, case: Case, now: datetime) -> dict[str, Any]:
        """Not possible in test mode, and said so plainly rather than faked.

        Re-attempting a payment needs the customer to authenticate it; there is no server-side
        endpoint for it. The agent treats this like any other unavailable action and moves to the
        next best one, which is why the live demo shows recovery links rather than silent retries.
        """

        self.calls.append(
            {"call": "retry_charge", "ok": False, "ms": 0, "detail": "unavailable in test mode"}
        )
        return {
            "attempted": False,
            "success": False,
            "reason": "test mode cannot initiate a charge without customer authentication",
        }

    # -- polling helpers ----------------------------------------------------------------------

    def failed_payments_since(
        self, since: datetime, count: int = 100, max_pages: int = 20
    ) -> list[dict[str, Any]]:
        """Every failed payment since ``since``, following pagination to the end.

        A single page would be a silent data-loss bug rather than a limitation: the caller
        advances its cursor past the whole window afterwards, so anything beyond the first page
        would never be seen again. A busy merchant, or a first run looking back six hours, would
        simply lose cases. ``max_pages`` bounds the work; hitting it is reported so the caller can
        decline to advance its cursor.
        """

        started = time.perf_counter()
        collected: list[dict[str, Any]] = []
        self.last_fetch_complete = True
        try:
            for page_number in range(max_pages):
                page = self.client.payment.all(
                    {
                        "from": int(since.timestamp()),
                        "count": count,
                        "skip": page_number * count,
                    }
                )
                items = page.get("items", [])
                collected.extend(items)
                if len(items) < count:
                    break
            else:
                # Fell out of the loop without a short page: there is more we have not read.
                self.last_fetch_complete = False
            self._record(
                "payment.all",
                True,
                started,
                f"{len(collected)} payments, complete={self.last_fetch_complete}",
            )
            return [p for p in collected if p.get("status") == "failed"]
        except Exception as exc:  # noqa: BLE001
            self.last_fetch_complete = False
            self._record("payment.all", False, started, str(exc))
            return []

    def fetch_entity(self, resource: str, entity_id: str) -> dict[str, Any] | None:
        return self._fetch(resource, entity_id)
