"""The human queue.

Escalation is a real outcome, not an admission of failure: some cases must not be handled by
software, and knowing which ones is most of the value. Each ticket carries enough context for a
person to act without reading the audit log.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from bharpai.core.models import Case, Ticket


class HumanQueue:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else None
        self.tickets: list[Ticket] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def create(
        self,
        case: Case,
        now: datetime,
        *,
        kind: Literal["escalation", "merchant_alert", "risk_review"] = "escalation",
        brief: str = "",
        rule_ids: list[str] | None = None,
    ) -> Ticket:
        ticket = Ticket(
            id=f"tkt_{len(self.tickets) + 1:05d}",
            case_id=case.id,
            kind=kind,
            created_at=now,
            reason_rule_ids=rule_ids or [],
            brief=brief,
            amount_paise=case.amount_paise,
        )
        self.tickets.append(ticket)
        if self.path:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(ticket.model_dump_json() + "\n")
        return ticket

    def by_kind(self, kind: str) -> list[Ticket]:
        return [t for t in self.tickets if t.kind == kind]

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for ticket in self.tickets:
            counts[ticket.kind] = counts.get(ticket.kind, 0) + 1
        counts["total"] = len(self.tickets)
        return counts
