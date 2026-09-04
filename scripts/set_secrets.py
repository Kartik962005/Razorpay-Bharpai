"""Fill the secret values in .env from your own terminal. Values are never echoed or logged."""
import getpass
from pathlib import Path

ENV = Path(__file__).resolve().parent.parent / ".env"
SECRETS = ["RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET", "LLM_API_KEY"]

lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
current = {}
for line in lines:
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        current[k.strip()] = v.strip()

print(f"Editing {ENV}\nPress Enter to keep an existing value; typing is hidden.\n")
for key in SECRETS:
    status = "set" if current.get(key) else "EMPTY"
    value = getpass.getpass(f"{key} [{status}]: ").strip()
    if value:
        current[key] = value

out = []
seen = set()
for line in lines:
    if "=" in line and not line.startswith("#"):
        k = line.split("=", 1)[0].strip()
        if k in current:
            out.append(f"{k}={current[k]}")
            seen.add(k)
            continue
    out.append(line)
for k, v in current.items():
    if k not in seen:
        out.append(f"{k}={v}")
ENV.write_text("\n".join(out) + "\n", encoding="utf-8")

print("\nSaved. Status:")
for key in SECRETS:
    print(f"  {key}: {'set' if current.get(key) else 'EMPTY'}")
