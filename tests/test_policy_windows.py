"""Time-of-day rules: TRAI messaging bands, RBI contact hours, NPCI execution windows.

These are the rules a merchant would break by accident and a regulator would notice.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from wapsi.config import IST
from wapsi.core.models import ActionType, Method, RootCause, Scenario

from tests.conftest import NOW


def at(hour: int, minute: int = 0, day: int = 3) -> datetime:
    return datetime(2026, 8, day, hour, minute, tzinfo=IST)


def test_customer_nudge_is_blocked_late_at_night(make_case, denials):
    case = make_case()
    assert "R10" in denials(case, ActionType.SEND_PAYMENT_LINK, at(21, 30))
    assert "R10" in denials(case, ActionType.SEND_PAYMENT_LINK, at(6, 0))


def test_customer_nudge_is_allowed_inside_the_band(make_case, denials):
    case = make_case()
    assert "R10" not in denials(case, ActionType.SEND_PAYMENT_LINK, at(10, 1))
    assert "R10" not in denials(case, ActionType.SEND_PAYMENT_LINK, at(20, 59))


def test_night_nudge_is_deferred_to_the_next_morning(make_case, earliest):
    case = make_case()
    when = earliest(case, ActionType.SEND_PAYMENT_LINK, "R10", at(21, 30))
    assert when == at(10, 0, day=4)

    # Before the window opens on the same day, the wait is only a few hours.
    when = earliest(case, ActionType.SEND_PAYMENT_LINK, "R10", at(6, 0))
    assert when == at(10, 0, day=3)


def test_receivables_use_the_stricter_evening_cutoff(make_case, denials):
    invoice = make_case(scenario=Scenario.D, root_cause=RootCause.OVERDUE_RECEIVABLE, amount_paise=1_800_000)
    payment = make_case()

    # 19:30 is fine for a failed payment, but not for chasing an overdue invoice.
    assert "R11" in denials(invoice, ActionType.SEND_REMINDER, at(19, 30))
    assert "R10" not in denials(payment, ActionType.SEND_PAYMENT_LINK, at(19, 30))
    assert "R11" not in denials(invoice, ActionType.SEND_REMINDER, at(11, 0))


def recurring(make_case, **overrides):
    defaults = dict(
        scenario=Scenario.C,
        method=Method.upi_autopay,
        root_cause=RootCause.INSUFFICIENT_FUNDS,
        amount_paise=49_900,
        predebit_notice_at=NOW - timedelta(hours=30),
    )
    defaults.update(overrides)
    return make_case(**defaults)


def test_auto_debit_is_blocked_during_the_npci_peak(make_case, denials):
    case = recurring(make_case)
    assert "R12" in denials(case, ActionType.RETRY_CHARGE, at(10, 30))
    assert "R12" in denials(case, ActionType.RETRY_CHARGE, at(12, 0))
    assert "R12" in denials(case, ActionType.RETRY_CHARGE, at(18, 0))


def test_auto_debit_is_allowed_in_the_quiet_windows(make_case, denials):
    case = recurring(make_case)
    assert "R12" not in denials(case, ActionType.RETRY_CHARGE, at(13, 30))
    assert "R12" not in denials(case, ActionType.RETRY_CHARGE, at(22, 0))
    assert "R12" not in denials(case, ActionType.RETRY_CHARGE, at(9, 0))


def test_peak_retry_is_deferred_to_the_next_open_window(make_case, earliest):
    case = recurring(make_case)
    assert earliest(case, ActionType.RETRY_CHARGE, "R12", at(10, 30)) == at(13, 0)
    assert earliest(case, ActionType.RETRY_CHARGE, "R12", at(18, 0)) == at(21, 30)


def test_auto_debit_needs_a_pre_debit_notification(make_case, denials):
    never_told = recurring(make_case, predebit_notice_at=None)
    assert "R13" in denials(never_told, ActionType.RETRY_CHARGE, at(13, 30))

    told_recently = recurring(make_case, predebit_notice_at=at(13, 30) - timedelta(hours=10))
    assert "R13" in denials(told_recently, ActionType.RETRY_CHARGE, at(13, 30))

    told_yesterday = recurring(make_case, predebit_notice_at=at(13, 30) - timedelta(hours=25))
    assert "R13" not in denials(told_yesterday, ActionType.RETRY_CHARGE, at(13, 30))


def test_one_off_upi_payments_are_not_subject_to_mandate_rules(make_case, denials):
    case = make_case(root_cause=RootCause.TRANSIENT_TECH)
    blocked = denials(case, ActionType.RETRY_CHARGE, at(11, 0))
    assert "R12" not in blocked
    assert "R13" not in blocked


def test_transient_retry_waits_for_the_outage_to_clear(make_case, denials, ctx):
    from wapsi.core.policy import PolicyContext

    case = make_case(root_cause=RootCause.TRANSIENT_TECH)
    during = PolicyContext(downtime_active=True)
    assert "R16" in denials(case, ActionType.RETRY_CHARGE, NOW, during)
    assert "R16" not in denials(case, ActionType.RETRY_CHARGE, NOW, ctx)


def test_transient_retries_respect_the_minimum_gap_and_daily_cap(make_case, denials):
    just_tried = make_case(
        root_cause=RootCause.TRANSIENT_TECH,
        last_retry_at=NOW - timedelta(minutes=5),
        retry_times=[NOW - timedelta(minutes=5)],
    )
    assert "R16" in denials(just_tried, ActionType.RETRY_CHARGE, NOW)

    spent = make_case(
        root_cause=RootCause.TRANSIENT_TECH,
        last_retry_at=NOW - timedelta(hours=2),
        retry_times=[NOW - timedelta(hours=h) for h in (2, 5, 9)],
    )
    assert "R16" in denials(spent, ActionType.RETRY_CHARGE, NOW)
