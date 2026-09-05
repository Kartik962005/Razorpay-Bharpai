"""Turning a run into numbers, including the unflattering ones.

Three things here exist specifically to make the result harder to believe rather than easier:
the violation count (which the baselines fail and Bharpai must not), the false-nudge count
(customers we contacted who would have paid on their own), and the strict recovery figure that
refuses credit for any case the simulation says would have resolved itself anyway.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bharpai.core.models import Outcome, RootCause
from bharpai.core.policy import PolicyEngine


@dataclass
class PolicyMetrics:
    policy: str
    cases: int = 0
    recovered: int = 0
    recovered_by_agent: int = 0
    recovered_organic: int = 0
    recovered_via_human: int = 0
    at_risk_paise: int = 0
    recovered_paise: int = 0
    strict_recovered_paise: int = 0
    cost_paise: int = 0
    contacts: int = 0
    retries: int = 0
    escalations: int = 0
    merchant_alerts: int = 0
    opt_outs: int = 0
    disputes: int = 0
    violations: int = 0
    violation_rules: dict[str, int] = field(default_factory=dict)
    false_nudges: int = 0
    hours_to_recovery: list[float] = field(default_factory=list)
    by_cause: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def recovery_rate(self) -> float:
        return self.recovered / self.cases if self.cases else 0.0

    @property
    def net_paise(self) -> int:
        return self.recovered_paise - self.cost_paise

    @property
    def strict_net_paise(self) -> int:
        return self.strict_recovered_paise - self.cost_paise

    @property
    def contacts_per_recovery(self) -> float:
        return self.contacts / self.recovered if self.recovered else float("inf")

    @property
    def median_hours_to_recovery(self) -> float:
        return statistics.median(self.hours_to_recovery) if self.hours_to_recovery else 0.0

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("hours_to_recovery")
        data.update(
            recovery_rate=round(self.recovery_rate, 4),
            net_paise=self.net_paise,
            strict_net_paise=self.strict_net_paise,
            contacts_per_recovery=(
                round(self.contacts_per_recovery, 2)
                if self.recovered
                else None
            ),
            median_hours_to_recovery=round(self.median_hours_to_recovery, 1),
        )
        return data


def compute(result: Any) -> PolicyMetrics:
    """Summarise one policy's run over the batch."""

    metrics = PolicyMetrics(policy=result.policy_name)
    population = result.population

    per_cause: dict[str, dict[str, float]] = {}

    for case in result.cases:
        metrics.cases += 1
        metrics.at_risk_paise += case.amount_paise
        metrics.cost_paise += case.cost_paise
        metrics.retries += case.retries
        metrics.contacts += case.nudges

        cause = (case.root_cause or RootCause.UNKNOWN).value
        bucket = per_cause.setdefault(
            cause, {"cases": 0, "recovered": 0, "at_risk_paise": 0, "recovered_paise": 0}
        )
        bucket["cases"] += 1
        bucket["at_risk_paise"] += case.amount_paise

        hidden = population.hidden(case.id)
        organic_possible = hidden.organic_pay_at is not None

        if case.outcome in (Outcome.recovered, Outcome.recovered_via_human):
            metrics.recovered += 1
            metrics.recovered_paise += case.recovered_paise
            bucket["recovered"] += 1
            bucket["recovered_paise"] += case.recovered_paise
            if "recovered_organic" in case.tags:
                metrics.recovered_organic += 1
            elif case.outcome is Outcome.recovered_via_human:
                metrics.recovered_via_human += 1
            else:
                metrics.recovered_by_agent += 1
            # Strict credit: only money that would not have arrived on its own.
            if not organic_possible:
                metrics.strict_recovered_paise += case.recovered_paise
            if case.recovered_at and case.created_at:
                metrics.hours_to_recovery.append(
                    (case.recovered_at - case.created_at).total_seconds() / 3600
                )

        # A nudge sent to someone who was going to pay anyway is a cost with no benefit, and a
        # small tax on the customer's patience. Counted against ourselves.
        if hidden.contacted and organic_possible:
            metrics.false_nudges += 1

        if case.outcome is Outcome.opted_out or case.opted_out:
            metrics.opt_outs += 1
        if case.outcome is Outcome.disputed or case.disputed:
            metrics.disputes += 1

    for ticket in result.queue.tickets:
        if ticket.kind == "merchant_alert":
            metrics.merchant_alerts += 1
        else:
            metrics.escalations += 1

    for violation in PolicyEngine.violations(result.audit.entries):
        metrics.violations += 1
        metrics.violation_rules[violation.rule_id] = (
            metrics.violation_rules.get(violation.rule_id, 0) + 1
        )

    # Platform retries belong to Razorpay's own ladder, not to any policy we are scoring.
    platform_violations = sum(
        len(entry.payload.get("violations") or [])
        for entry in result.audit.entries
        if entry.kind == "action" and entry.payload.get("platform")
    )
    metrics.violations -= platform_violations

    metrics.by_cause = {
        cause: {
            **values,
            "recovery_rate": round(values["recovered"] / values["cases"], 3)
            if values["cases"]
            else 0.0,
        }
        for cause, values in sorted(per_cause.items())
    }
    return metrics


def rupees(paise: float) -> str:
    return f"₹{paise / 100:,.0f}"


def comparison_rows(
    metrics: list[PolicyMetrics], baseline: str = "do_nothing"
) -> list[dict[str, Any]]:
    base = next((m for m in metrics if m.policy == baseline), None)
    rows = []
    for m in metrics:
        incremental = m.net_paise - base.net_paise if base else 0
        rows.append(
            {
                "policy": m.policy,
                "cases": m.cases,
                "recovered": m.recovered,
                "recovery_rate": f"{m.recovery_rate:.1%}",
                "at_risk": rupees(m.at_risk_paise),
                "recovered_value": rupees(m.recovered_paise),
                "cost": rupees(m.cost_paise),
                "net": rupees(m.net_paise),
                "vs_baseline": rupees(incremental),
                "contacts": m.contacts,
                "per_recovery": (
                    f"{m.contacts_per_recovery:.2f}" if m.recovered else "-"
                ),
                "median_hours": f"{m.median_hours_to_recovery:.1f}",
                "escalations": m.escalations,
                "opt_outs": m.opt_outs,
                "disputes": m.disputes,
                "violations": m.violations,
                "false_nudges": m.false_nudges,
            }
        )
    return rows


def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    header = "| " + " | ".join(label for _, label in columns) + " |"
    divider = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, divider]
    for row in rows:
        lines.append("| " + " | ".join(str(row[key]) for key, _ in columns) + " |")
    return "\n".join(lines)


MAIN_COLUMNS = [
    ("policy", "policy"),
    ("recovered", "recovered"),
    ("recovery_rate", "rate"),
    ("recovered_value", "₹ recovered"),
    ("cost", "₹ cost"),
    ("net", "₹ net"),
    ("vs_baseline", "vs do-nothing"),
    ("contacts", "contacts"),
    ("per_recovery", "per recovery"),
    ("median_hours", "median h"),
    ("escalations", "escalated"),
    ("opt_outs", "opt-outs"),
    ("disputes", "disputes"),
    ("violations", "violations"),
]


def by_cause_markdown(metrics: list[PolicyMetrics]) -> str:
    causes = sorted({c for m in metrics for c in m.by_cause})
    header = "| root cause | cases | " + " | ".join(m.policy for m in metrics) + " |"
    divider = "|" + "|".join("---" for _ in range(len(metrics) + 2)) + "|"
    lines = [header, divider]
    for cause in causes:
        counts = next((m.by_cause[cause]["cases"] for m in metrics if cause in m.by_cause), 0)
        cells = []
        for m in metrics:
            entry = m.by_cause.get(cause)
            cells.append(f"{entry['recovery_rate']:.0%}" if entry else "-")
        lines.append(f"| {cause} | {int(counts)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def where_agent_lost(results: dict[str, Any], a: str, b: str) -> list[dict[str, Any]]:
    """Cases policy ``a`` recovered and policy ``b`` did not. Honest comparisons need both sides."""

    by_id_a = {c.id: c for c in results[a].cases}
    by_id_b = {c.id: c for c in results[b].cases}
    rows = []
    for case_id, case_a in by_id_a.items():
        case_b = by_id_b.get(case_id)
        if case_b is None:
            continue
        won = case_a.outcome in (Outcome.recovered, Outcome.recovered_via_human)
        lost = case_b.outcome not in (Outcome.recovered, Outcome.recovered_via_human)
        if won and lost and "recovered_organic" not in case_a.tags:
            rows.append(
                {
                    "case": case_id,
                    "cause": (case_a.root_cause or RootCause.UNKNOWN).value,
                    "amount": rupees(case_a.amount_paise),
                    f"{a}_outcome": case_a.outcome.value if case_a.outcome else "-",
                    f"{b}_outcome": case_b.outcome.value if case_b.outcome else "-",
                }
            )
    return rows


def write_summary(path: Path, metrics: list[PolicyMetrics], meta: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "policies": [m.as_dict() for m in metrics]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
