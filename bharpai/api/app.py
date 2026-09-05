"""HTTP surface: the webhook endpoint and a small read-only dashboard.

The dashboard exists to make the audit trail legible. Any claim this project makes about a case
should be checkable by clicking it and reading what actually happened, in order, with the rule ids
that produced each decision.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from bharpai.config import IST, RESULTS_DIR, fingerprint, get_settings
from bharpai.core.audit import AuditLog
from bharpai.live import state
from bharpai.live.webhook import Receiver

STATIC = Path(__file__).resolve().parent / "static"


def _open_case_from_webhook(payload: dict[str, Any]) -> str | None:
    """Turn a verified ``payment.failed`` into a persisted, diagnosed case for the poller."""

    from bharpai.core.taxonomy import diagnose
    from bharpai.live.poller import case_from_payment
    from bharpai.live.webhook import normalise

    event = normalise(payload)
    if not event or not event.get("payment"):
        return None

    cases = state.load_cases()
    case = case_from_payment(event["payment"])
    if case.id in cases:
        return None

    cause, tags, text = diagnose(case)
    case.root_cause, case.diagnosis_text = cause, text
    case.tags.extend(t for t in tags if t not in case.tags)
    cases[case.id] = case
    state.save_cases(cases)

    audit = AuditLog(state.audit_path(), truncate=False)
    audit.record(
        ts=case.created_at,
        case_id=case.id,
        kind="observation",
        actor="adapter",
        summary=(
            f"delivered by webhook: {case.scenario.value}, ₹{case.amount_inr:,.0f} "
            f"on {case.method.value}"
        ),
        payload={
            "error_reason": case.error.reason if case.error else None,
            "error_source": case.error.source if case.error else None,
            "error_step": case.error.step if case.error else None,
            "via": "webhook",
        },
    )
    audit.record(
        ts=case.created_at,
        case_id=case.id,
        kind="diagnosis",
        actor="system",
        summary=text,
        payload={"root_cause": cause.value},
    )
    return case.id


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Bharpai", docs_url=None, redoc_url=None)
    receiver = Receiver(settings.razorpay_webhook_secret)
    app.state.receiver = receiver
    app.state.events: list[dict[str, Any]] = []

    @app.get("/health")
    def health() -> dict[str, Any]:
        # The secret is reported as a fingerprint so a stale process is obvious without ever
        # printing the value. That mistake cost an hour once; it does not get to happen twice.
        return {
            "ok": True,
            "webhook_secret": fingerprint(settings.razorpay_webhook_secret),
            "razorpay_configured": settings.razorpay_configured,
            "model_configured": settings.llm_configured,
            "webhooks": app.state.receiver.stats(),
        }

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        page = STATIC / "index.html"
        if not page.exists():
            return "<h1>Bharpai</h1><p>Dashboard not built.</p>"
        return page.read_text(encoding="utf-8")

    @app.get("/api/metrics")
    def metrics() -> JSONResponse:
        """Batch results, if a simulation has been run."""

        summary = RESULTS_DIR / "summary.json"
        if not summary.exists():
            return JSONResponse({"policies": [], "meta": {}})
        return JSONResponse(json.loads(summary.read_text(encoding="utf-8")))

    @app.get("/api/cases")
    def cases(source: str = "live") -> JSONResponse:
        """Live cases from the poller, or the batch's own cases if asked."""

        if source == "live":
            live = state.load_cases()
            return JSONResponse(
                {
                    "source": "live",
                    "cases": [
                        {
                            "id": c.id,
                            "scenario": c.scenario.value,
                            "root_cause": c.root_cause.value if c.root_cause else None,
                            "diagnosis": c.diagnosis_text,
                            "amount_inr": c.amount_inr,
                            "status": c.status.value,
                            "outcome": c.outcome.value if c.outcome else None,
                            "nudges": c.nudges,
                            "retries": c.retries,
                            "cost_inr": c.cost_paise / 100,
                            "recovered_inr": c.recovered_paise / 100,
                            "created_at": c.created_at.isoformat(),
                            "razorpay": c.razorpay,
                        }
                        for c in sorted(live.values(), key=lambda x: x.created_at, reverse=True)
                    ],
                }
            )
        return JSONResponse({"source": source, "cases": []})

    @app.get("/api/cases/{case_id}")
    def case_detail(case_id: str, policy: str = "rules") -> JSONResponse:
        """One case's audit timeline, from live state or a batch audit log."""

        entries = []
        live_log = state.audit_path()
        for path in (live_log, RESULTS_DIR / f"audit_{policy}.jsonl"):
            if path.exists():
                entries = [e for e in AuditLog.read(path) if e.case_id == case_id]
                if entries:
                    break
        return JSONResponse(
            {
                "case_id": case_id,
                "timeline": [
                    {
                        "ts": e.ts.isoformat(),
                        "kind": e.kind,
                        "actor": e.actor,
                        "summary": e.summary,
                        "rule_ids": e.rule_ids,
                        "payload": e.payload,
                    }
                    for e in entries
                ],
            }
        )

    @app.get("/api/events")
    def events() -> JSONResponse:
        return JSONResponse(
            {"events": app.state.events[-50:], "stats": app.state.receiver.stats()}
        )

    @app.post("/webhooks/razorpay")
    @app.post("/")
    async def webhook(request: Request) -> JSONResponse:
        """Accepts on both paths, because a pasted URL can lose its path and silently drop every
        delivery. That happened during setup; it is not allowed to happen during a demo."""

        body = await request.body()
        signature = request.headers.get("x-razorpay-signature", "")
        event_id = request.headers.get("x-razorpay-event-id", "")
        # Read from app state rather than the closure, so the receiver can be swapped — by a
        # test, or by a reload that picks up a rotated secret.
        status, result = request.app.state.receiver.handle(body, signature, event_id)
        app.state.events.append(
            {"at": datetime.now(IST).isoformat(), "status": status, **result}
        )
        if status == 200 and result.get("kind") == "failure":
            # This is what a webhook buys over polling: the case exists the moment Razorpay
            # tells us, and the next poll acts on it instead of first having to find it.
            created = _open_case_from_webhook(json.loads(body))
            if created:
                result["case_id"] = created
        return JSONResponse(result, status_code=status)

    return app


app = create_app()
