"""Append-only audit log.

Every observation, verdict, action and outcome lands here with the rule ids that produced it.
The rest of the system is allowed to keep state; this is the record that must be sufficient on
its own to explain any decision to someone who was not there.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from wapsi.core.models import AuditEntry


class AuditLog:
    """Holds entries in memory and, optionally, mirrors them to a JSONL file."""

    def __init__(self, path: Path | str | None = None, *, truncate: bool = True):
        self.path = Path(path) if path else None
        self._entries: list[AuditEntry] = []
        self._seq: dict[str, int] = defaultdict(int)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if truncate or not self.path.exists():
                self.path.write_text("", encoding="utf-8")
            else:
                # Several processes may share the live log: the poller and the webhook
                # endpoint both append to it, and neither may wipe the other's history.
                for entry in self.read(self.path):
                    self._entries.append(entry)
                    self._seq[entry.case_id] = max(self._seq[entry.case_id], entry.seq)

    def record(
        self,
        *,
        ts: datetime,
        case_id: str,
        kind: str,
        actor: str,
        summary: str,
        rule_ids: Iterable[str] = (),
        payload: dict[str, Any] | None = None,
    ) -> AuditEntry:
        self._seq[case_id] += 1
        entry = AuditEntry(
            ts=ts,
            case_id=case_id,
            seq=self._seq[case_id],
            kind=kind,  # type: ignore[arg-type]
            actor=actor,  # type: ignore[arg-type]
            rule_ids=list(rule_ids),
            summary=summary,
            payload=payload or {},
        )
        self._entries.append(entry)
        if self.path:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(entry.model_dump_json() + "\n")
        return entry

    @property
    def entries(self) -> list[AuditEntry]:
        return self._entries

    def for_case(self, case_id: str) -> list[AuditEntry]:
        return [e for e in self._entries if e.case_id == case_id]

    @classmethod
    def read(cls, path: Path | str) -> list[AuditEntry]:
        with open(path, encoding="utf-8") as handle:
            return [AuditEntry(**json.loads(line)) for line in handle if line.strip()]


def format_timeline(entries: list[AuditEntry]) -> str:
    """Human-readable case history, used by ``wapsi case`` and the dashboard drawer."""

    lines = []
    for entry in entries:
        rules = f" [{', '.join(entry.rule_ids)}]" if entry.rule_ids else ""
        lines.append(
            f"{entry.ts:%d %b %H:%M}  {entry.actor:<8} {entry.kind:<11} {entry.summary}{rules}"
        )
    return "\n".join(lines)
