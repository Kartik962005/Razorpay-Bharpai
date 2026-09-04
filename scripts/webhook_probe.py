"""Minimal webhook receiver used to verify tunnel + signature before the real app exists."""
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request

load_dotenv()
SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
LOG = Path(__file__).with_name("webhook_probe.log")
app = FastAPI()


@app.get("/health")
def health() -> dict:
    return {"ok": True, "secret_configured": bool(SECRET), "version": 2}


@app.post("/")
@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request) -> dict:
    body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    event_id = request.headers.get("x-razorpay-event-id", "")
    expected = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    signature_ok = bool(SECRET) and hmac.compare_digest(expected, signature)
    try:
        event = json.loads(body).get("event")
    except Exception:  # noqa: BLE001
        event = None
    line = json.dumps(
        {"ts": time.time(), "event": event, "event_id": event_id, "signature_ok": signature_ok, "bytes": len(body)}
    )
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)
    return {"received": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
