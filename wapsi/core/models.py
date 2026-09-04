"""Domain model.

Money is always an integer count of paise, matching Razorpay's own convention; rupees appear
only in text meant for humans. Times are timezone-aware and stored in IST.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Scenario(str, Enum):
    """The four ways revenue leaks on a Razorpay account."""

    A = "payment_failed"
    B = "checkout_abandoned"
    C = "subscription_failed"
    D = "overdue_receivable"


class Method(str, Enum):
    upi = "upi"
    card = "card"
    netbanking = "netbanking"
    wallet = "wallet"
    emandate = "emandate"
    upi_autopay = "upi_autopay"


class RootCause(str, Enum):
    TRANSIENT_TECH = "TRANSIENT_TECH"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    CUSTOMER_ABANDON = "CUSTOMER_ABANDON"
    CUSTOMER_INPUT = "CUSTOMER_INPUT"
    INSTRUMENT_BLOCKED = "INSTRUMENT_BLOCKED"
    MANDATE_ISSUE = "MANDATE_ISSUE"
    RISK_DECLINE = "RISK_DECLINE"
    MERCHANT_CONFIG = "MERCHANT_CONFIG"
    ABANDONED_CHECKOUT = "ABANDONED_CHECKOUT"
    OVERDUE_RECEIVABLE = "OVERDUE_RECEIVABLE"
    UNKNOWN = "UNKNOWN"


class ActionType(str, Enum):
    WAIT = "WAIT"
    RETRY_CHARGE = "RETRY_CHARGE"
    SEND_PAYMENT_LINK = "SEND_PAYMENT_LINK"
    SEND_REMINDER = "SEND_REMINDER"
    OFFER_METHOD_SWITCH = "OFFER_METHOD_SWITCH"
    REQUEST_REAUTH = "REQUEST_REAUTH"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    ALERT_MERCHANT = "ALERT_MERCHANT"
    CLOSE = "CLOSE"


#: Actions that put a message in front of a customer. These are the ones the messaging windows,
#: nudge caps and per-customer frequency caps apply to.
CONTACT_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.SEND_PAYMENT_LINK,
        ActionType.SEND_REMINDER,
        ActionType.OFFER_METHOD_SWITCH,
        ActionType.REQUEST_REAUTH,
    }
)


class Channel(str, Enum):
    sms = "sms"
    whatsapp = "whatsapp"
    email = "email"
    voice_stub = "voice_stub"


class Tone(str, Enum):
    soft = "soft"
    helpful = "helpful"
    firm = "firm"


class Language(str, Enum):
    en = "en"
    hinglish = "hinglish"


class CaseStatus(str, Enum):
    open = "open"
    waiting = "waiting"
    escalated = "escalated"
    closed = "closed"


class Outcome(str, Enum):
    recovered = "recovered"
    recovered_via_human = "recovered_via_human"
    gave_up = "gave_up"
    opted_out = "opted_out"
    disputed = "disputed"
    refunded = "refunded"
    merchant_issue = "merchant_issue"
    risk_blocked = "risk_blocked"
    expired = "expired"
    escalated_unresolved = "escalated_unresolved"


class ReplyIntent(str, Enum):
    paid_claim = "paid_claim"
    promise_to_pay = "promise_to_pay"
    opt_out = "opt_out"
    dispute = "dispute"
    question = "question"
    complaint = "complaint"
    other = "other"


class ErrorTriple(BaseModel):
    """The failure signal Razorpay attaches to a payment.

    ``source`` says who must act, ``step`` says where in the flow it died, ``reason`` is the
    specific cause. Together they are the entire basis of our diagnosis.
    """

    code: str | None = None
    source: str | None = None
    step: str | None = None
    reason: str | None = None
    description: str | None = None


class Case(BaseModel):
    """One unit of revenue at risk, tracked from detection to a terminal outcome."""

    id: str
    merchant_id: str = "merch_demo"
    merchant_name: str = "Demo Store"
    customer_id: str
    customer_first_name: str = "there"
    customer_contact: str = ""
    customer_email: str = ""

    scenario: Scenario
    method: Method
    amount_paise: int
    currency: str = "INR"

    error: ErrorTriple | None = None
    root_cause: RootCause | None = None
    diagnosis_text: str | None = None

    razorpay: dict[str, str] = Field(default_factory=dict)

    created_at: datetime
    due_at: datetime | None = None

    status: CaseStatus = CaseStatus.open
    outcome: Outcome | None = None

    retries: int = 0
    nudges: int = 0
    actions: int = 0
    attempts_by_action: dict[str, int] = Field(default_factory=dict)

    last_contact_at: datetime | None = None
    next_action_at: datetime | None = None
    predebit_notice_at: datetime | None = None
    last_retry_at: datetime | None = None
    retry_times: list[datetime] = Field(default_factory=list)

    recovered_paise: int = 0
    cost_paise: int = 0
    recovered_at: datetime | None = None
    closed_at: datetime | None = None

    opted_out: bool = False
    disputed: bool = False
    refunded: bool = False
    paid: bool = False
    cancelled_by_customer: bool = False
    merchant_alerted: bool = False

    promise_at: datetime | None = None
    promises_broken: int = 0
    llm_denials: int = 0
    complaint: bool = False

    language_pref: Language | None = None
    channel_pref: Channel | None = None

    tags: list[str] = Field(default_factory=list)

    @property
    def amount_inr(self) -> float:
        return self.amount_paise / 100

    @property
    def is_terminal(self) -> bool:
        return self.status is CaseStatus.closed

    def age_days(self, now: datetime) -> float:
        return (now - self.created_at).total_seconds() / 86400

    def attempts_of(self, action: ActionType) -> int:
        return self.attempts_by_action.get(action.value, 0)


class Action(BaseModel):
    """A single thing the agent does, or is considering doing."""

    id: str = ""
    case_id: str = ""
    type: ActionType
    params: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: datetime | None = None
    executed_at: datetime | None = None
    result: dict[str, Any] | None = None
    cost_paise: int = 0
    expected_value_paise: float = 0.0
    rationale: str = ""

    @property
    def channel(self) -> Channel | None:
        raw = self.params.get("channel")
        return Channel(raw) if raw else None


class AuditEntry(BaseModel):
    """One immutable line in a case's history. The whole system must be reconstructible from these."""

    ts: datetime
    case_id: str
    seq: int
    kind: Literal[
        "observation",
        "diagnosis",
        "proposal",
        "verdict",
        "action",
        "result",
        "reply",
        "outcome",
        "escalation",
    ]
    actor: Literal["system", "policy", "planner", "llm", "adapter", "customer", "human"]
    rule_ids: list[str] = Field(default_factory=list)
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """Outbound customer contact, priced so recovery can be reported net of cost."""

    case_id: str
    channel: Channel
    to: str
    text: str
    language: Language
    tone: Tone
    sent_at: datetime
    cost_paise: int
    template_id: str | None = None
    llm_written: bool = False


class Reply(BaseModel):
    """Inbound customer message, already classified."""

    case_id: str
    received_at: datetime
    text: str
    intent: ReplyIntent
    promise_date: datetime | None = None
    confidence: float = 1.0
    matched_by_regex: bool = False


class Ticket(BaseModel):
    """A case handed to a human, with everything needed to act without reading the code."""

    id: str
    case_id: str
    kind: Literal["escalation", "merchant_alert", "risk_review"]
    created_at: datetime
    reason_rule_ids: list[str] = Field(default_factory=list)
    brief: str = ""
    amount_paise: int = 0
    resolved: bool = False


class Violation(BaseModel):
    """A rule broken by a policy under test. Wapsi must score zero; the naive baseline will not."""

    case_id: str
    ts: datetime
    rule_id: str
    action: ActionType
    detail: str
