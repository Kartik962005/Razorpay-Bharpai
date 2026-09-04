"""The payment-gateway seam.

The agent talks to this interface and nothing else, so the identical planner, policy engine and
executor drive both the simulation and a real Razorpay test account. Only the implementation
behind this Protocol changes between them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from wapsi.core.models import Case, Method


class Gateway(Protocol):
    """Everything Wapsi needs from a payment processor."""

    def refresh(self, case: Case, now: datetime) -> dict[str, Any]:
        """Current truth about the money: ``{"paid": bool, "status": str}``.

        Called before every action. This is the idempotency guard that stops the agent from
        chasing someone who has already paid.
        """

    def create_payment_link(
        self,
        case: Case,
        now: datetime,
        *,
        description: str,
        expire_by: datetime,
        method_hint: Method | None = None,
    ) -> dict[str, Any]:
        """Create a fresh link for this case: ``{"id": str, "short_url": str}``."""

    def notify(self, entity_type: str, entity_id: str, medium: str) -> dict[str, Any]:
        """Ask the gateway to deliver its own notification for an invoice or link."""

    def retry_charge(self, case: Case, now: datetime) -> dict[str, Any]:
        """Attempt the charge again: ``{"attempted": bool, "success": bool, "reason": str}``."""
