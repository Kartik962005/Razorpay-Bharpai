"""Persistence for live mode.

A JSON file rather than a database, because the live side exists to demonstrate the agent against
a real account, and a file that a person can open and read is worth more here than a schema. The
simulation keeps its own state in memory; nothing is shared between them but the case model.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from wapsi.config import REPO_ROOT
from wapsi.core.models import Case

STATE_DIR = REPO_ROOT / ".live"
SEED_PATH = STATE_DIR / "seed.json"
CASES_PATH = STATE_DIR / "cases.json"
CURSOR_PATH = STATE_DIR / "cursor.json"


def _ensure() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def save_seed(data: dict[str, Any]) -> Path:
    _ensure()
    SEED_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return SEED_PATH


def load_seed() -> dict[str, Any]:
    if not SEED_PATH.exists():
        return {}
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def save_cases(cases: dict[str, Case]) -> None:
    _ensure()
    CASES_PATH.write_text(
        json.dumps({cid: json.loads(c.model_dump_json()) for cid, c in cases.items()}, indent=2),
        encoding="utf-8",
    )


def load_cases() -> dict[str, Case]:
    if not CASES_PATH.exists():
        return {}
    raw = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    return {cid: Case(**data) for cid, data in raw.items()}


def save_cursor(seen_payments: list[str], last_poll: datetime) -> None:
    _ensure()
    CURSOR_PATH.write_text(
        json.dumps({"seen_payments": seen_payments, "last_poll": last_poll.isoformat()}, indent=2),
        encoding="utf-8",
    )


def load_cursor() -> tuple[set[str], datetime | None]:
    if not CURSOR_PATH.exists():
        return set(), None
    raw = json.loads(CURSOR_PATH.read_text(encoding="utf-8"))
    last = raw.get("last_poll")
    return set(raw.get("seen_payments", [])), (datetime.fromisoformat(last) if last else None)
