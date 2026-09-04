"""``wapsi live …`` — driving the agent against a real Razorpay test account."""

from __future__ import annotations

import time
from datetime import datetime

import typer
from rich.console import Console
from rich.table import Table

from wapsi.config import IST, fingerprint, get_settings
from wapsi.core.audit import AuditLog
from wapsi.core.policy import PolicyEngine
from wapsi.live import state
from wapsi.live.poller import LivePoller
from wapsi.live.seed import next_steps, seed as seed_account

live = typer.Typer(help="Run the agent against a Razorpay test account.")
console = Console()

# Resolved through the state module so tests and alternate state dirs are honoured.
def _audit_path():
    return state.audit_path()


def _client():
    settings = get_settings()
    if not settings.razorpay_configured:
        console.print("[red]No Razorpay test keys in .env — see .env.example.[/red]")
        raise typer.Exit(1)
    import razorpay

    client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))
    client.set_app_details({"title": "wapsi", "version": "0.1.0"})
    return client


@live.command()
def doctor() -> None:
    """Check the account, the models and the webhook before relying on any of them.

    Written after losing an hour to a webhook that was configured correctly except for its path,
    and to a receiver still holding a secret from before the file was edited.
    """

    settings = get_settings()
    table = Table(title="wapsi live doctor", header_style="bold")
    table.add_column("check")
    table.add_column("result")

    table.add_row(
        "razorpay key id",
        (settings.razorpay_key_id[:14] + "…") if settings.razorpay_key_id else "[red]unset[/red]",
    )
    table.add_row("razorpay secret", "set" if settings.razorpay_key_secret else "[red]unset[/red]")
    table.add_row("webhook secret", fingerprint(settings.razorpay_webhook_secret))

    client = None
    if not settings.razorpay_configured:
        table.add_row("razorpay auth", "[yellow]no keys — copy .env.example to .env[/yellow]")
    else:
        try:
            client = _client()
            payments = client.payment.all({"count": 1})
            table.add_row(
                "razorpay auth", f"[green]ok[/green] ({payments.get('count', 0)} payments visible)"
            )
        except Exception as exc:  # noqa: BLE001
            table.add_row("razorpay auth", f"[red]failed[/red] {type(exc).__name__}: {exc}")

    if client is not None:
        try:
            import requests

            response = requests.get(
                "https://api.razorpay.com/v1/webhooks",
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
                timeout=15,
            )
            for hook in response.json().get("items", []):
                url = hook.get("url", "")
                events = sum(1 for v in (hook.get("events") or {}).values() if v)
                warning = "" if url.rstrip("/").endswith("/webhooks/razorpay") else \
                    "  [yellow](no /webhooks/razorpay path — the app accepts / too, but set it)[/yellow]"
                table.add_row("webhook", f"{url} · {events} events · active={hook.get('active')}{warning}")
        except Exception as exc:  # noqa: BLE001
            table.add_row("webhook", f"[yellow]could not read: {exc}[/yellow]")

    if settings.llm_configured:
        from wapsi.adapters.llm import LLM

        model = LLM()
        probe = model.parse_reply("paisa Friday ko bhej dunga", datetime.now(IST).strftime("%Y-%m-%d"))
        table.add_row(
            "language model",
            f"[green]ok[/green] {settings.llm_model_fast} → {probe}" if probe else "[yellow]unreachable — templates will be used[/yellow]",
        )
    else:
        table.add_row("language model", "not configured — templates and rules only")

    seeded = state.load_seed()
    table.add_row(
        "seeded entities",
        f"{len(seeded.get('payment_links', []))} links, "
        f"invoice={'yes' if seeded.get('invoice') else 'no'}, "
        f"subscription={'yes' if seeded.get('subscription') else 'no'}"
        if seeded
        else "none — run `wapsi live seed`",
    )
    console.print(table)


@live.command()
def seed() -> None:
    """Create the demo customers, payment links, invoice and subscription in test mode."""

    client = _client()
    created = seed_account(client)
    path = state.save_seed(created)

    table = Table(title="created in your Razorpay test account", header_style="bold")
    table.add_column("entity")
    table.add_column("id")
    table.add_column("open")
    for link in created.get("payment_links", []):
        table.add_row(f"payment link ₹{link['amount'] / 100:,.0f}", link["id"], link["short_url"])
    if created.get("invoice"):
        table.add_row("invoice", created["invoice"]["id"], created["invoice"].get("short_url") or "—")
    if created.get("subscription"):
        table.add_row(
            "subscription", created["subscription"]["id"], created["subscription"].get("short_url") or "—"
        )
    console.print(table)

    for note in created.get("notes", []):
        console.print(f"[yellow]{note}[/yellow]")

    console.print(f"\n[green]wrote[/green] {path}\n")
    console.print("[bold]Next, by hand — test mode cannot fail a payment from a script:[/bold]")
    for index, step in enumerate(next_steps(created), 1):
        console.print(f"  {index}. {step}")


@live.command()
def watch(
    poll: int = typer.Option(15, help="Seconds between polls."),
    once: bool = typer.Option(False, help="Poll a single time and exit."),
    minutes: int = typer.Option(20, help="How long to keep watching."),
) -> None:
    """Poll Razorpay, diagnose whatever failed, and run the recovery loop."""

    client = _client()
    settings = get_settings()
    model = None
    if settings.llm_configured:
        from wapsi.adapters.llm import LLM

        model = LLM()

    # Append: the webhook endpoint may already have written cases into this log.
    audit = AuditLog(_audit_path(), truncate=False)
    poller = LivePoller(client, PolicyEngine.load(), llm=model, audit=audit)

    console.print(
        f"[dim]watching (poll {poll}s, model "
        f"{'on' if model and model.enabled else 'off'}) — ctrl-c to stop[/dim]\n"
    )
    deadline = time.time() + minutes * 60
    while True:
        now = datetime.now(IST)
        for line in poller.close_paid(now):
            console.print(f"[green]{now:%H:%M:%S}  {line}[/green]")
        for line in poller.step(now):
            style = "green" if "RECOVERED" in line else ("dim" if "waiting" in line else "")
            console.print(f"[{style}]{now:%H:%M:%S}  {line}[/{style}]" if style else f"{now:%H:%M:%S}  {line}")

        # Surface any link the agent just created, so it can be paid on camera.
        for entry in audit.entries[-12:]:
            url = (entry.payload or {}).get("link")
            if entry.kind == "result" and url:
                console.print(f"          [bold]pay this to close the case:[/bold] {url}")

        if once or time.time() > deadline:
            break
        time.sleep(poll)

    console.print(f"\n[dim]{len(poller.cases)} case(s) tracked · audit at {_audit_path()}[/dim]")


@live.command()
def reset() -> None:
    """Forget locally tracked live cases. Nothing in the Razorpay account is touched."""

    removed = []
    for path in (state.path(state.CASES_FILE), state.path(state.CURSOR_FILE),
                 state.path(state.MESSAGES_FILE), _audit_path()):
        if path.exists():
            path.unlink()
            removed.append(path.name)
    console.print(f"cleared: {', '.join(removed) or 'nothing to clear'}")
    console.print("[dim]seed.json kept — the entities in Razorpay still exist[/dim]")
