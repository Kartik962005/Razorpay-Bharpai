"""The simulated world: a clock, bank outages, NPCI peaks and payday.

The world is deterministic given a seed, and it hands out *per-purpose* random streams. That
matters more than it sounds: if two policies reach the same decision point on the same case, they
must draw the same luck, or a comparison between them measures noise instead of judgement.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from wapsi.config import IST

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def load_config(path: Path | str = CONFIG_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":")
    return int(hour), int(minute)


@dataclass
class Downtime:
    """One bank or PSP outage window."""

    method: str
    bank: str
    start: datetime
    end: datetime

    def covers(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


@dataclass
class World:
    config: dict[str, Any]
    seed: int = 42
    start: datetime = field(init=False)
    end: datetime = field(init=False)
    tick: timedelta = field(init=False)
    downtimes: list[Downtime] = field(default_factory=list)
    behaviour_scale: float = 1.0

    def __post_init__(self) -> None:
        clock = self.config["clock"]
        self.start = datetime.strptime(clock["start"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        self.end = self.start + timedelta(days=int(clock["horizon_days"]))
        self.tick = timedelta(minutes=int(clock["tick_minutes"]))
        self._generate_downtimes()

    # -- randomness ---------------------------------------------------------------------------

    def rng(self, *parts: Any) -> random.Random:
        """A stable stream keyed by the seed and a purpose.

        Deriving from a hash rather than a global generator means the draw for
        ``(case_17, "retry", 2)`` is the same no matter which policy is running, or in what order
        the cases were processed.
        """

        key = "|".join(str(p) for p in (self.seed, *parts))
        digest = hashlib.sha256(key.encode()).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))

    # -- outages ------------------------------------------------------------------------------

    def _generate_downtimes(self) -> None:
        spec = self.config["world"]["downtime"]
        rng = self.rng("downtime")
        days = (self.end - self.start).days
        count = max(1, round(spec["episodes_per_week"] * days / 7))
        methods = ["upi", "card", "netbanking", "upi_autopay"]
        for index in range(count):
            offset_minutes = rng.randrange(0, max(1, days * 24 * 60))
            start = self.start + timedelta(minutes=offset_minutes)
            duration = rng.randint(int(spec["min_minutes"]), int(spec["max_minutes"]))
            self.downtimes.append(
                Downtime(
                    method=rng.choice(methods),
                    bank=rng.choice(spec["banks"]),
                    start=start,
                    end=start + timedelta(minutes=duration),
                )
            )
        self.downtimes.sort(key=lambda d: d.start)

    def downtime_active(self, moment: datetime, method: str | None = None) -> bool:
        for downtime in self.downtimes:
            if downtime.covers(moment) and (method is None or downtime.method == method):
                return True
        return False

    def downtime_ends_at(self, moment: datetime, method: str | None = None) -> datetime | None:
        for downtime in self.downtimes:
            if downtime.covers(moment) and (method is None or downtime.method == method):
                return downtime.end
        return None

    # -- calendar -----------------------------------------------------------------------------

    def in_npci_peak(self, moment: datetime) -> bool:
        peak = self.config["world"]["npci_peak"]
        start_h, start_m = _parse_hhmm(peak["start"])
        end_h, end_m = _parse_hhmm(peak["end"])
        local = moment.astimezone(IST)
        minutes = local.hour * 60 + local.minute
        return start_h * 60 + start_m <= minutes < end_h * 60 + end_m

    @property
    def npci_peak_failure_probability(self) -> float:
        return float(self.config["world"]["npci_peak"]["failure_probability"])

    def is_salary_window(self, moment: datetime) -> bool:
        return moment.astimezone(IST).day in set(self.config["world"]["salary_days"])

    def in_waking_hours(self, moment: datetime) -> bool:
        spec = self.config["replies"]["waking_hours"]
        start_h, start_m = _parse_hhmm(spec["start"])
        end_h, end_m = _parse_hhmm(spec["end"])
        local = moment.astimezone(IST)
        minutes = local.hour * 60 + local.minute
        return start_h * 60 + start_m <= minutes < end_h * 60 + end_m

    def next_waking_moment(self, moment: datetime) -> datetime:
        if self.in_waking_hours(moment):
            return moment
        spec = self.config["replies"]["waking_hours"]
        start_h, start_m = _parse_hhmm(spec["start"])
        local = moment.astimezone(IST)
        candidate = local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        if candidate <= local:
            candidate += timedelta(days=1)
        return candidate

    def ticks(self):
        moment = self.start
        while moment <= self.end:
            yield moment
            moment += self.tick
