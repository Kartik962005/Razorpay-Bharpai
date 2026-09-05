"""Fire a real test-mode event: create a Rs.10 payment link, then cancel it (-> payment_link.cancelled)."""
import os
import time

import razorpay
from dotenv import load_dotenv

load_dotenv()
client = razorpay.Client(auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"]))
link = client.payment_link.create(
    {
        "amount": 1000,
        "currency": "INR",
        "description": "Bharpai webhook probe",
        "reference_id": f"bharpai-probe-{int(time.time())}",
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "notes": {"bharpai": "webhook-probe"},
    }
)
print("created payment link:", link["id"], link.get("short_url"))
cancelled = client.payment_link.cancel(link["id"])
print("cancelled ->", cancelled.get("status"))
