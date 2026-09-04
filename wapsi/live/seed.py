"""Create the entities the live demo needs, in the merchant's own test account.

Everything here is real: real customers, real payment links, a real invoice, a real subscription.
What cannot be made real from a script is a *failure* — test mode has no way to force one — so the
last step is a person opening a link and paying with ``failure@razorpay``. That constraint is the
reason the measured results come from the simulation and the live side is the demonstration.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from wapsi.config import IST

DEMO_CUSTOMERS = [
    {"name": "Aarav Sharma", "email": "aarav@example.test", "contact": "+919999900001"},
    {"name": "Diya Nair", "email": "diya@example.test", "contact": "+919999900002"},
    {"name": "Kabir Rao", "email": "kabir@example.test", "contact": "+919999900003"},
]

#: Three ordinary Indian basket sizes, one per scenario the demo walks through.
DEMO_LINKS = [
    {"amount": 49_900, "description": "StreamBox monthly top-up"},
    {"amount": 129_900, "description": "Chai Point bulk order"},
    {"amount": 249_900, "description": "FitClub quarterly plan"},
]


def _safe(call, label: str, results: list[str]):
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - one unavailable product must not stop the rest
        results.append(f"{label}: skipped ({exc})")
        return None


def seed(client: Any, now: datetime | None = None) -> dict[str, Any]:
    """Create the demo entities and return what was made, for ``.live/seed.json``."""

    now = now or datetime.now(IST)
    notes: list[str] = []
    out: dict[str, Any] = {"created_at": now.isoformat(), "customers": [], "payment_links": []}

    for spec in DEMO_CUSTOMERS:
        customer = _safe(
            lambda s=spec: client.customer.create({**s, "fail_existing": 0}),
            f"customer {spec['name']}",
            notes,
        )
        if customer:
            out["customers"].append({"id": customer["id"], "name": spec["name"]})

    for index, spec in enumerate(DEMO_LINKS):
        person = DEMO_CUSTOMERS[index % len(DEMO_CUSTOMERS)]
        link = _safe(
            lambda s=spec, p=person: client.payment_link.create(
                {
                    "amount": s["amount"],
                    "currency": "INR",
                    "description": s["description"],
                    "reference_id": f"wapsi-seed-{int(now.timestamp())}-{s['amount']}",
                    "customer": {"name": p["name"], "email": p["email"], "contact": p["contact"]},
                    "notify": {"sms": False, "email": False},
                    "reminder_enable": False,
                    "expire_by": int((now + timedelta(days=3)).timestamp()),
                    "notes": {"wapsi": "seed"},
                }
            ),
            f"payment link {spec['amount']}",
            notes,
        )
        if link:
            out["payment_links"].append(
                {
                    "id": link["id"],
                    "short_url": link["short_url"],
                    "amount": spec["amount"],
                    "description": spec["description"],
                }
            )

    # An invoice that is already overdue, for the receivables scenario.
    invoice = _safe(
        lambda: client.invoice.create(
            {
                "type": "invoice",
                "description": "Nova Supplies — August consignment",
                "customer": {
                    "name": DEMO_CUSTOMERS[2]["name"],
                    "email": DEMO_CUSTOMERS[2]["email"],
                    "contact": DEMO_CUSTOMERS[2]["contact"],
                },
                "line_items": [
                    {"name": "Consignment #4412", "amount": 1_500_000, "currency": "INR", "quantity": 1}
                ],
                "sms_notify": 0,
                "email_notify": 0,
                "expire_by": int((now + timedelta(days=20)).timestamp()),
            }
        ),
        "invoice",
        notes,
    )
    if invoice:
        out["invoice"] = {
            "id": invoice["id"],
            "short_url": invoice.get("short_url"),
            "amount": invoice.get("amount"),
            "status": invoice.get("status"),
        }

    # A subscription, so the mandate path can be demonstrated. Its first charge is authorised by
    # opening the short_url; failures are then produced from the dashboard.
    plan = _safe(
        lambda: client.plan.create(
            {
                "period": "monthly",
                "interval": 1,
                "item": {
                    "name": "StreamBox Premium",
                    "amount": 29_900,
                    "currency": "INR",
                    "description": "monthly subscription",
                },
                "notes": {"wapsi": "seed"},
            }
        ),
        "plan",
        notes,
    )
    if plan:
        out["plan"] = {"id": plan["id"], "amount": 29_900}
        subscription = _safe(
            lambda: client.subscription.create(
                {
                    "plan_id": plan["id"],
                    "total_count": 12,
                    "quantity": 1,
                    "customer_notify": 0,
                    "notes": {"wapsi": "seed"},
                }
            ),
            "subscription",
            notes,
        )
        if subscription:
            out["subscription"] = {
                "id": subscription["id"],
                "short_url": subscription.get("short_url"),
                "status": subscription.get("status"),
            }

    out["notes"] = notes
    return out


def next_steps(seeded: dict[str, Any]) -> list[str]:
    """What a person has to do by hand, because test mode cannot do it from a script."""

    steps = [
        "Open one of the payment links below, choose UPI, and enter failure@razorpay.",
        "That produces a real payment.failed with a real error code for the agent to diagnose.",
        "Run `wapsi live watch` — it will pick the failure up, diagnose it, and create a "
        "recovery link, printing the URL.",
        "Pay the recovery link with success@razorpay. The next poll closes the case as recovered.",
    ]
    if seeded.get("subscription", {}).get("short_url"):
        steps.append(
            "For the subscription path: authorise it at its short_url with test card "
            "4718 6091 0820 4366, then use the dashboard's 'Charge this now' → Failure."
        )
    return steps
