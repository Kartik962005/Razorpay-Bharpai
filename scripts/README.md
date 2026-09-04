# scripts

Two helpers that are not part of the package.

- **`set_secrets.py`** — fills the secret values in `.env` from a hidden prompt, so keys are never
  echoed to a terminal or a shell history. Run it with the project's interpreter.
- **`webhook_trigger.py`** — creates and immediately cancels a ₹10 test-mode payment link, which
  makes Razorpay deliver a real `payment_link.cancelled` webhook. Useful for proving a tunnel and
  a signature end to end without paying for anything.

Everything else that used to live here is now `wapsi live doctor` and `wapsi serve`.
