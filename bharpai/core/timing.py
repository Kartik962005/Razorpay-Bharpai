"""Calendar facts that change how likely a payment is to succeed.

These are properties of the world, not policy, so they are shared by the planner (which uses
them to choose when to act) and the simulator (which uses them to decide what happens).
"""

from __future__ import annotations

from datetime import datetime

from bharpai.config import IST

#: Salaries in India land at the start of the month, and balance-related failures recover with
#: them. Retrying a "no funds" decline on the 3rd is a different bet from retrying on the 27th.
SALARY_DAYS = frozenset(range(1, 8))


def is_salary_window(moment: datetime) -> bool:
    return moment.astimezone(IST).day in SALARY_DAYS


def hours_between(earlier: datetime, later: datetime) -> float:
    return (later - earlier).total_seconds() / 3600
