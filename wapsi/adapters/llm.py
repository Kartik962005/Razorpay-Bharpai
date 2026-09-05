"""The language-model adapter.

Three properties matter more than which model sits behind it:

* **Optional.** With no API key every function returns ``None`` and the caller uses a template or
  a regex. The batch numbers, the tests and the live demo all run keyless, so nobody has to take
  the model's contribution on trust to reproduce the result.
* **Bounded.** A call budget, a disk cache and exponential backoff. When the budget is spent the
  system degrades to templates rather than stalling, and the report says how many messages the
  model actually wrote.
* **Untrusted.** Every output passes through something deterministic before it reaches a customer
  or a decision — the message validator, or the policy engine.

Any OpenAI-compatible endpoint works; the model id lives only in the environment.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from wapsi.adapters import prompts
from wapsi.config import REPO_ROOT, Settings, get_settings
from wapsi.core.taxonomy import CAUSE_SUMMARY

CACHE_PATH = REPO_ROOT / ".cache" / "llm.sqlite"

#: These are reasoning models: they spend output tokens thinking before they answer, so a small
#: ceiling returns an empty message rather than a short one.
MIN_OUTPUT_TOKENS = 400

#: Three retries, each of which waits for the window the provider named rather than a fixed
#: guess — the zeros are placeholders, since the real pause is computed from the headers.
BACKOFF_SECONDS = (0, 0, 0)

#: Default floor between calls, overridable by ``LLM_MIN_INTERVAL``. Where a provider reports
#: its remaining headroom this is only a burst guard; where it reports nothing, it is the whole
#: of the pacing and must be set to match the tier.
MIN_INTERVAL_SECONDS = 1.0

#: Below this many tokens left in the current window, wait for the window to reset rather than
#: spend the next call on a refusal. Roughly one call's worth of prompt plus completion.
TOKEN_HEADROOM = 1400

#: Leave this many requests in the provider's allowance untouched, so a later run (or the live
#: poller) is not left with nothing.
REQUEST_HEADROOM = 20

#: Longer than this and a window is not worth waiting for inside a batch — fall back instead.
MAX_WAIT_SECONDS = 120


def _parse_duration(value: Any) -> float:
    """Seconds from a rate-limit reset header such as ``1m26.4s``, ``7.66s`` or ``2m``."""

    if not value:
        return 60.0
    match = re.fullmatch(
        r"(?:(?P<h>[\d.]+)h)?(?:(?P<m>[\d.]+)m)?(?:(?P<s>[\d.]+)s)?", str(value).strip()
    )
    if not match or not any(match.groupdict().values()):
        return 60.0
    parts = {k: float(v) if v else 0.0 for k, v in match.groupdict().items()}
    return parts["h"] * 3600 + parts["m"] * 60 + parts["s"]


@dataclass
class LLMStats:
    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    fallbacks: int = 0
    #: Our own call budget ran out.
    budget_exhausted: bool = False
    #: The provider's allowance ran out — a different thing, and worth reporting separately,
    #: because it is a property of the account rather than a choice this system made.
    provider_budget_exhausted: bool = False
    #: Time spent deliberately waiting for the provider's window to reset, rather than
    #: spending calls on refusals.
    throttled_seconds: float = 0.0
    by_task: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "failures": self.failures,
            "fallbacks": self.fallbacks,
            "budget_exhausted": self.budget_exhausted,
            "provider_budget_exhausted": self.provider_budget_exhausted,
            "throttled_seconds": round(self.throttled_seconds),
            "by_task": dict(self.by_task),
        }


class _Cache:
    """Content-addressed response cache, so a repeated run costs nothing and stays reproducible."""

    def __init__(self, path: Path = CACHE_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, value TEXT)")
        self.conn.commit()

    @staticmethod
    def key(model: str, system: str, user: str) -> str:
        return hashlib.sha256(f"{model}\x00{system}\x00{user}".encode()).hexdigest()

    def get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM responses WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO responses VALUES (?, ?)", (key, value))
        self.conn.commit()


class LLM:
    """A thin, budgeted, cached wrapper. Every method returns ``None`` rather than raising."""

    def __init__(self, settings: Settings | None = None, *, cache: bool = True):
        self.settings = settings or get_settings()
        self.stats = LLMStats()
        self.cache = _Cache() if cache else None
        self._client = None

        # Reasoning models spend output tokens thinking, and capping that keeps replies short
        # enough to fit a window. Providers that do not know the parameter reject the whole
        # request, so it is sent only where it is understood.
        self._extra: dict[str, Any] = (
            {"extra_body": {"reasoning_effort": "low"}}
            if "groq" in (settings or get_settings()).llm_base_url.lower()
            else {}
        )
        self._min_interval = max(0.0, getattr(self.settings, "llm_min_interval", MIN_INTERVAL_SECONDS))
        self._last_call = 0.0
        self._tokens_left: int | None = None
        self._requests_left: int | None = None
        self._window_resets_at = 0.0
        self.enabled = self.settings.llm_configured
        if self.enabled:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    base_url=self.settings.llm_base_url,
                    api_key=self.settings.llm_api_key,
                    timeout=30.0,
                    max_retries=0,
                )
            except Exception:  # noqa: BLE001 - a missing client must never break a run
                self.enabled = False

    @property
    def budget_left(self) -> int:
        return max(0, self.settings.llm_max_calls - self.stats.calls)

    @property
    def available(self) -> bool:
        """Whether a call is worth attempting at all.

        Checked before each task builds its prompt, so an exhausted account costs nothing but a
        comparison and every caller falls straight through to its template.
        """

        return (
            self.enabled
            and not self.stats.provider_budget_exhausted
            and self.budget_left > 0
        )

    # -- pacing -------------------------------------------------------------------------------

    def _pace(self) -> None:
        """Wait until the provider can actually serve the next call.

        Free tiers meter *tokens* per minute, not requests, and pacing by request count is how a
        batch ends up with hundreds of refusals: 28 calls a minute is well inside a 1,000-request
        allowance and roughly four times an 8,000-token one. Every response reports how much of
        the window is left, so the adapter reads that and waits for the reset rather than
        spending the next call discovering it is empty.
        """

        if self._tokens_left is not None and self._tokens_left < TOKEN_HEADROOM:
            sleep_for = max(0.0, self._window_resets_at - time.monotonic()) + 0.4
            if sleep_for > 0:
                self.stats.throttled_seconds += sleep_for
                time.sleep(sleep_for)
            self._tokens_left = None

        gap = self._min_interval - (time.monotonic() - self._last_call)
        if gap > 0:
            time.sleep(gap)
        self._last_call = time.monotonic()

    def _note_headroom(self, headers: Any) -> None:
        """Record what the provider says is left, in both of its windows.

        There are two, and they behave nothing alike. Tokens are metered per minute and refill
        continuously — a short wait fixes that. Requests are metered over hours, and when they
        run out no amount of waiting helps inside a batch. Treating the second like the first is
        how a run produces hundreds of refusals instead of stopping and using templates.
        """

        def _int(name: str) -> int | None:
            raw = headers.get(name)
            try:
                return int(float(raw)) if raw is not None else None
            except (TypeError, ValueError):
                return None

        tokens = _int("x-ratelimit-remaining-tokens")
        if tokens is not None:
            self._tokens_left = tokens
            self._window_resets_at = time.monotonic() + _parse_duration(
                headers.get("x-ratelimit-reset-tokens")
            )

        requests_left = _int("x-ratelimit-remaining-requests")
        if requests_left is not None:
            self._requests_left = requests_left
            reset = _parse_duration(headers.get("x-ratelimit-reset-requests"))
            # A request allowance that takes hours to refill cannot be waited out mid-batch.
            # Stop asking, record it, and let every caller fall back to a template.
            if requests_left <= REQUEST_HEADROOM and reset > MAX_WAIT_SECONDS:
                self.stats.provider_budget_exhausted = True

    # -- transport ----------------------------------------------------------------------------

    def _chat(self, task: str, model: str, system: str, user: str) -> dict[str, Any] | None:
        if not self.enabled or self._client is None:
            return None

        key = _Cache.key(model, system, user) if self.cache else None
        if key is not None:
            cached = self.cache.get(key)
            if cached is not None:
                self.stats.cache_hits += 1
                try:
                    return json.loads(cached)
                except json.JSONDecodeError:
                    pass

        if self.budget_left <= 0:
            self.stats.budget_exhausted = True
            return None
        if self.stats.provider_budget_exhausted:
            # The account's allowance is spent for hours. Every caller has a template.
            return None

        for attempt, pause in enumerate((0, *BACKOFF_SECONDS)):
            if pause:
                time.sleep(pause)
            self._pace()
            try:
                self.stats.calls += 1
                self.stats.by_task[task] = self.stats.by_task.get(task, 0) + 1
                raw = self._client.chat.completions.with_raw_response.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.3,
                    max_tokens=MIN_OUTPUT_TOKENS,
                    **self._extra,
                )
                self._note_headroom(raw.headers)
                response = raw.parse()
                content = (response.choices[0].message.content or "").strip()
                if not content:
                    raise ValueError("empty response")
                parsed = json.loads(content)
                if key is not None:
                    self.cache.put(key, content)
                return parsed
            except Exception as exc:  # noqa: BLE001
                # A refusal reports the same headroom a success does. Reading it is the
                # difference between waiting once and failing repeatedly.
                headers = getattr(getattr(exc, "response", None), "headers", None)
                if headers is not None:
                    try:
                        self._note_headroom(headers)
                    except Exception:  # noqa: BLE001
                        pass
                retryable = "429" in str(exc) or "rate" in str(exc).lower()
                if retryable and attempt < len(BACKOFF_SECONDS):
                    # Wait for the window the provider just told us about, not a fixed guess.
                    wait = min(MAX_WAIT_SECONDS, max(0.0, self._window_resets_at - time.monotonic()))
                    if wait > 0:
                        self.stats.throttled_seconds += wait
                        time.sleep(wait)
                        self._tokens_left = None
                    continue
                if not retryable or attempt == len(BACKOFF_SECONDS):
                    self.stats.failures += 1
                    return None
        return None

    # -- tasks --------------------------------------------------------------------------------
    #
    # Each one bails before touching its arguments when no model is configured, so a caller can
    # hold a disabled LLM and never special-case it.

    def compose_message(self, ctx: Any) -> str | None:
        """Write one customer message. Returns ``None`` to mean "use the template"."""

        if not self.available:
            return None

        result = self._chat(
            "compose",
            self.settings.llm_model_fast,
            prompts.COMPOSE_SYSTEM,
            prompts.COMPOSE_USER.format(
                channel=ctx.channel.value,
                language=ctx.language.value,
                tone=ctx.tone.value,
                char_limit=ctx.char_limit,
                merchant=ctx.merchant_name,
                first_name=ctx.first_name,
                amount=ctx.amount_text,
                situation=ctx.scenario.value.replace("_", " "),
                cause=CAUSE_SUMMARY.get(ctx.root_cause, ctx.guidance),
                guidance=ctx.guidance or "complete the payment",
                link=ctx.link,
                opt_out=ctx.opt_out_line,
            ),
        )
        if not result:
            return None
        text = result.get("text")
        return text.strip() if isinstance(text, str) and text.strip() else None

    def parse_reply(self, text: str, today: str) -> dict[str, Any] | None:
        if not self.available:
            return None

        result = self._chat(
            "parse_reply",
            self.settings.llm_model_fast,
            prompts.PARSE_SYSTEM.format(today=today),
            prompts.PARSE_USER.format(text=text),
        )
        if not result or "intent" not in result:
            return None
        return result

    def explain_diagnosis(self, case: Any) -> str | None:
        if not self.available:
            return None

        error = case.error
        result = self._chat(
            "explain",
            self.settings.llm_model_fast,
            prompts.EXPLAIN_SYSTEM,
            prompts.EXPLAIN_USER.format(
                amount=f"₹{case.amount_inr:,.0f}",
                method=case.method.value,
                reason=(error.reason if error else None),
                source=(error.source if error else None),
                step=(error.step if error else None),
                cause=case.root_cause.value if case.root_cause else "UNKNOWN",
            ),
        )
        if not result:
            return None
        explanation = result.get("explanation")
        return explanation.strip() if isinstance(explanation, str) else None

    def write_brief(self, case: Any, reasons: list[str], replies: list[str]) -> str | None:
        if not self.available:
            return None

        result = self._chat(
            "brief",
            self.settings.llm_model,
            prompts.BRIEF_SYSTEM,
            prompts.BRIEF_USER.format(
                merchant=case.merchant_name,
                amount=f"₹{case.amount_inr:,.0f}",
                diagnosis=case.diagnosis_text or "",
                age_days=case.age_days(case.closed_at or case.created_at),
                attempts=case.actions,
                attempt_detail=", ".join(
                    f"{k} x{v}" for k, v in sorted(case.attempts_by_action.items())
                )
                or "none",
                replies="; ".join(replies) or "none",
                reasons=", ".join(reasons) or "policy",
            ),
        )
        if not result:
            return None
        brief = result.get("brief")
        return brief.strip() if isinstance(brief, str) else None

    def advise_action(
        self, case: Any, allowed: list[str], denied: list[str], replies: list[str]
    ) -> dict[str, Any] | None:
        if not self.available:
            return None

        result = self._chat(
            "advise",
            self.settings.llm_model,
            prompts.ADVISE_SYSTEM,
            prompts.ADVISE_USER.format(
                merchant=case.merchant_name,
                amount=f"₹{case.amount_inr:,.0f}",
                diagnosis=case.diagnosis_text or "",
                age_days=case.age_days(case.created_at),
                nudges=case.nudges,
                retries=case.retries,
                replies="; ".join(replies) or "none",
                allowed="\n".join(allowed) or "none",
                denied="\n".join(denied) or "none",
            ),
        )
        if not result or "action" not in result:
            return None
        return result
