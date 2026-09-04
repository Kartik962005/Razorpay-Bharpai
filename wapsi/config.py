"""Runtime configuration.

Every secret comes from the environment (``.env`` locally, real env vars in CI). Nothing in
this module ever prints a secret; :func:`fingerprint` exists so the operator can confirm which
value a process loaded without revealing it.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
RESULTS_DIR = REPO_ROOT / "results"
POLICY_PATH = REPO_ROOT / "policy.yaml"


def _india_timezone():
    """Asia/Kolkata, with a fixed-offset fallback.

    Windows ships no IANA database, so ``ZoneInfo`` fails there unless the ``tzdata`` package is
    installed (it is a dependency). The fallback keeps the system runnable regardless: India has
    observed a constant UTC+05:30 since 1945, so a fixed offset is not an approximation.
    """

    try:
        return ZoneInfo("Asia/Kolkata")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=5, minutes=30), "IST")


# Resolved explicitly rather than through the TZ environment variable, which Windows ignores.
IST = _india_timezone()


class Settings(BaseSettings):
    """Environment-backed settings. Absent values disable the feature, never crash the run."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_model_fast: str = ""
    llm_max_calls: int = 600

    wapsi_mode: str = "sim"

    @property
    def razorpay_configured(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_model_fast)


def fingerprint(secret: str) -> str:
    """Short, non-reversible tag for a secret, so a stale process is visible in logs."""
    if not secret:
        return "unset"
    return hashlib.sha256(secret.encode()).hexdigest()[:8]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
