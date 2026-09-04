"""Smoke-test the credentials in .env without printing any secret."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()

kid = os.environ.get("RAZORPAY_KEY_ID", "")
ksec = os.environ.get("RAZORPAY_KEY_SECRET", "")
print(f"razorpay key id: {kid[:12]}...  secret set: {bool(ksec)}")
try:
    import razorpay

    client = razorpay.Client(auth=(kid, ksec))
    res = client.payment.all({"count": 1})
    print(f"razorpay auth: OK (payments visible in test mode: {res.get('count')})")
except Exception as exc:  # noqa: BLE001
    print(f"razorpay auth: FAILED -> {type(exc).__name__}: {str(exc)[:160]}")

base = os.environ.get("LLM_BASE_URL", "")
key = os.environ.get("LLM_API_KEY", "")
print(f"llm base url: {base}  key set: {bool(key)}")
if key:
    from openai import OpenAI

    llm = OpenAI(base_url=base, api_key=key)
    for model in [os.environ.get("LLM_MODEL"), os.environ.get("LLM_MODEL_FAST")]:
        if not model:
            continue
        try:
            resp = llm.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                max_tokens=5,
                temperature=0,
            )
            print(f"llm {model}: OK -> {resp.choices[0].message.content.strip()!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"llm {model}: FAILED -> {type(exc).__name__}: {str(exc)[:160]}")
else:
    print("llm: skipped (no key)")
