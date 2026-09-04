"""Persistence for live mode.

A JSON file rather than a database, because the live side exists to demonstrate the agent against
a real account, and a file that a person can open and read is worth more here than a schema. The
simulation keeps its own state in memory; nothing is shared between them but the case model.

Every path is resolved from :data:`STATE_DIR` **at call time**, deliberately. Resolving them once
at import time meant a test could isolate some files and miss others, and adding a new file later
silently escaped the isolation — which happened twice, once with cases and once with messages.
Redirecting ``STATE_DIR`` now redirects everything, including files that do not exist yet.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from wapsi.config import REPO_ROOT
from wapsi.core.models import Case, Message

STATE_DIR = REPO_ROOT / ".live"

SEED_FILE = "seed.json"
CASES_FILE = "cases.json"
CURSOR_FILE = "cursor.json"
MESSAGES_FILE = "messages.jsonl"
AUDIT_FILE = "audit.jsonl"


def path(name: str) -> Path:
    """Resolve a state file against the current ``STATE_DIR``, creating the directory."""

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / name


def save_seed(data: dict[str, Any]) -> Path:
    target = path(SEED_FILE)
    target.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return target


def load_seed() -> dict[str, Any]:
    target = path(SEED_FILE)
    return json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}


def save_cases(cases: dict[str, Case]) -> None:
    path(CASES_FILE).write_text(
        json.dumps({cid: json.loads(c.model_dump_json()) for cid, c in cases.items()}, indent=2),
        encoding="utf-8",
    )


def load_cases() -> dict[str, Case]:
    target = path(CASES_FILE)
    if not target.exists():
        return {}
    return {cid: Case(**data) for cid, data in json.loads(target.read_text(encoding="utf-8")).items()}


def save_messages(messages: list[Message]) -> None:
    """Persist what has been sent, and to whom.

    The per-customer weekly cap (R23) is derived from this. Holding it only in memory means a
    restart silently re-opens the budget and the same person can be messaged again — a promise
    broken by an implementation detail rather than by a decision.
    """

    path(MESSAGES_FILE).write_text(
        "\n".join(m.model_dump_json() for m in messages), encoding="utf-8"
    )


def load_messages() -> list[Message]:
    target = path(MESSAGES_FILE)
    if not target.exists():
        return []
    return [
        Message(**json.loads(line))
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def save_cursor(seen_payments: list[str], last_poll: datetime) -> None:
    path(CURSOR_FILE).write_text(
        json.dumps({"seen_payments": seen_payments, "last_poll": last_poll.isoformat()}, indent=2),
        encoding="utf-8",
    )


def load_cursor() -> tuple[set[str], datetime | None]:
    target = path(CURSOR_FILE)
    if not target.exists():
        return set(), None
    raw = json.loads(target.read_text(encoding="utf-8"))
    last = raw.get("last_poll")
    return set(raw.get("seen_payments", [])), (datetime.fromisoformat(last) if last else None)


def audit_path() -> Path:
    return path(AUDIT_FILE)
