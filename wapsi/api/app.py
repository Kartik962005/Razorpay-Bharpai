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

from wapsi.config import IST, RESULTS_DIR, fingerprint, get_settings
from wapsi.core.audit import AuditLog
from wapsi.live import state
from wapsi.live.webhook import Receiver

STATIC = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Wapsi", docs_url=None, redoc_url=None)
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
            return "<h1>Wapsi</h1><p>Dashboard not built.</p>"
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
        live_log = RESULTS_DIR.parent / ".live" / "audit.jsonl"
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
        return JSONResponse(result, status_code=status)

    return app


app = create_app()
