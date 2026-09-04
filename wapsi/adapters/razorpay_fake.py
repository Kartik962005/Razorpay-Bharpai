"""A Razorpay stand-in backed by the simulated world.

This exists so the executor, planner and policy engine never learn whether they are running
against a simulation or a real test account. Everything that differs between the two is behind
this class and its live counterpart.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from wapsi.core.models import Case, Method
from wapsi.sim.customer import CustomerModel


class FakeGateway:
    def __init__(self, customer_model: CustomerModel):
        self.customers = customer_model
        self.links: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self._counter = 0

    def refresh(self, case: Case, now: datetime) -> dict[str, Any]:
        self.calls.append(("refresh", case.id))
        return {"paid": case.paid, "status": "paid" if case.paid else "failed"}

    def create_payment_link(
        self,
        case: Case,
        now: datetime,
        *,
        description: str = "",
        expire_by: datetime | None = None,
        method_hint: Method | None = None,
    ) -> dict[str, Any]:
        self._counter += 1
        link_id = f"plink_sim{self._counter:06d}"
        record = {
            "id": link_id,
            "short_url": f"https://rzp.io/i/{link_id[-6:]}",
            "amount": case.amount_paise,
            "case_id": case.id,
            "created_at": now,
            "expire_by": expire_by,
            "method_hint": method_hint.value if method_hint else None,
            "description": description,
        }
        self.links[link_id] = record
        self.calls.append(("create_payment_link", case.id))
        return record

    def notify(self, entity_type: str, entity_id: str, medium: str) -> dict[str, Any]:
        self.calls.append(("notify", entity_id))
        return {"success": True, "medium": medium}

    def retry_charge(self, case: Case, now: datetime) -> dict[str, Any]:
        """Re-attempt the charge. The hidden customer model decides what happens."""

        from wapsi.core.models import ActionType

        self.calls.append(("retry_charge", case.id))
        reaction = self.customers.react(case, ActionType.RETRY_CHARGE, now)
        return {
            "attempted": True,
            "success": reaction.paid,
            "disputed": reaction.disputed,
            "reason": None if reaction.paid else (case.error.reason if case.error else "unknown"),
        }
