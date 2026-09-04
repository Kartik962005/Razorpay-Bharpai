"""Command line: ``wapsi simulate``, ``wapsi report``, ``wapsi case``.

Reconfigures stdout to UTF-8 on start-up. Every number this tool prints is a rupee amount, and
the default Windows console encoding cannot represent the symbol.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from wapsi.config import RESULTS_DIR
from wapsi.core import metrics as metrics_mod
from wapsi.core.audit import AuditLog, format_timeline
from wapsi.core.policy import PolicyEngine

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(add_completion=False, help="Cause-aware revenue recovery for Razorpay merchants.")
console = Console()

DEFAULT_POLICIES = "do_nothing,naive,rules"
ALL_POLICIES = "do_nothing,naive,rules,agent"

#: The console gets the columns that carry the argument; the markdown report gets everything.
CONSOLE_COLUMNS = [
    ("policy", "policy"),
    ("recovered", "recovered"),
    ("recovery_rate", "rate"),
    ("recovered_value", "₹ recovered"),
    ("cost", "₹ cost"),
    ("net", "₹ net"),
    ("vs_baseline", "vs nothing"),
    ("contacts", "msgs"),
    ("opt_outs", "opt-out"),
    ("disputes", "disputes"),
    ("violations", "violations"),
]


def _render_table(rows: list[dict], columns: list[tuple[str, str]], title: str) -> Table:
    table = Table(title=title, header_style="bold", title_style="bold")
    for _, label in columns:
        table.add_column(label, justify="right" if label != "policy" else "left")
    for row in rows:
        table.add_row(*[str(row[key]) for key, _ in columns])
    return table


@app.command()
def simulate(
    n: int = typer.Option(500, help="Number of cases in the batch."),
    seed: int = typer.Option(42, help="Batch seed. Every policy sees the same cases."),
    policies: str = typer.Option(DEFAULT_POLICIES, help="Comma-separated policies to compare."),
    out: Path = typer.Option(RESULTS_DIR, help="Where to write the report and audit logs."),
    scale: float = typer.Option(1.0, help="Scale all behaviour priors (used by sensitivity runs)."),
    advisor_sample: float = typer.Option(
        1.0, help="Fraction of cases the planner model advises on, for the agent policy."
    ),
    quiet: bool = typer.Option(False, help="Suppress the tables; still writes results."),
) -> None:
    """Run every policy over one identical batch and report what each recovered."""

    from wapsi.sim.generator import generate
    from wapsi.sim.runner import Runner
    from wapsi.sim.world import World, load_config

    names = [p.strip() for p in policies.split(",") if p.strip()]

    llm = None
    if "agent" in names:
        from wapsi.adapters.llm import LLM

        llm = LLM()
        if not llm.enabled and not quiet:
            console.print(
                "[yellow]no language model configured; the agent policy will fall back to "
                "templates and rules throughout[/yellow]"
            )

    policy = PolicyEngine.load()
    world = World(config=load_config(), seed=seed)
    world.behaviour_scale = scale
    cases, population = generate(world, n=n)

    out.mkdir(parents=True, exist_ok=True)
    runner = Runner(
        world=world,
        cases=cases,
        population=population,
        policy=policy,
        results_dir=out,
        llm=llm,
        advisor_sample=advisor_sample,
    )

    results = {}
    collected = []
    for name in names:
        if not quiet:
            console.print(f"[dim]running {name}...[/dim]")
        result = runner.run(name)
        results[name] = result
        collected.append(metrics_mod.compute(result))

    rows = metrics_mod.comparison_rows(collected)
    if not quiet:
        console.print()
        console.print(
            _render_table(
                rows,
                CONSOLE_COLUMNS,
                f"{n} cases, seed {seed} — identical batch for every policy",
            )
        )
        console.print()

    report = _build_report(n, seed, scale, rows, collected, results)
    (out / "report.md").write_text(report, encoding="utf-8")
    metrics_mod.write_summary(
        out / "summary.json",
        collected,
        {"n": n, "seed": seed, "scale": scale, "policies": names},
    )
    if not quiet:
        console.print(f"[green]wrote[/green] {out / 'report.md'} and {out / 'summary.json'}")


def _build_report(n, seed, scale, rows, collected, results) -> str:
    lines = [
        "# Batch results",
        "",
        f"{n} synthetic cases, seed {seed}, behaviour scale {scale}. Every policy ran over the "
        "identical batch with the same random draws, so the differences below are decisions, "
        "not luck.",
        "",
        "## Recovery by policy",
        "",
        metrics_mod.markdown_table(rows, metrics_mod.MAIN_COLUMNS),
        "",
        "`violations` counts actions that broke a rule in `policy.yaml`, judged by the same "
        "engine for every policy. Razorpay's own subscription retry ladder runs under "
        "`do_nothing` and is excluded from its count, since it is the platform's behaviour and "
        "not a policy we are scoring.",
        "",
        "## Recovery rate by root cause",
        "",
        metrics_mod.by_cause_markdown(collected),
        "",
    ]

    for winner, loser, title, note in (
        (
            "naive",
            "rules",
            "Where the cause-blind policy beat the agent",
            "cases the naive policy recovered and Wapsi did not, usually because Wapsi declined "
            "to contact someone the rules protect",
        ),
        (
            "rules",
            "agent",
            "Where the deterministic planner beat the model-advised one",
            "cases the rules planner recovered and the model-advised planner did not — the "
            "comparison that decides whether the model earns its place",
        ),
        (
            "agent",
            "rules",
            "Where the model-advised planner beat the deterministic one",
            "the same comparison in the other direction",
        ),
    ):
        if winner not in results or loser not in results:
            continue
        lost = metrics_mod.where_agent_lost(results, winner, loser)
        lines += [f"## {title}", "", f"{len(lost)} case(s) — {note}.", ""]
        if lost:
            lines.append(
                metrics_mod.markdown_table(
                    lost[:15],
                    [
                        ("case", "case"),
                        ("cause", "cause"),
                        ("amount", "amount"),
                        (f"{winner}_outcome", winner),
                        (f"{loser}_outcome", loser),
                    ],
                )
            )
            lines.append("")

    lines += _model_section(results)
    return "\n".join(lines)


def _model_section(results: dict) -> list[str]:
    """What the language model actually did, and how well it read people.

    Reply reading is scored against what the simulated customer meant, which is the one place the
    hidden state is allowed to be used — after the fact, to mark the agent's homework.
    """

    lines: list[str] = []
    for name, result in results.items():
        stats = result.llm_stats or {}
        replies = stats.get("replies") or {}
        if not replies.get("read"):
            continue
        accuracy = replies["correct"] / replies["read"]
        caught = replies.get("hard_stops_caught", 0)
        total_hard = replies.get("hard_stops", 0)
        lines += [
            f"## Reading customer replies — {name}",
            "",
            f"- {replies['read']} replies read, {accuracy:.1%} matched what the customer meant",
            f"- {replies['by_model']} read by the language model, "
            f"{replies['read'] - replies['by_model']} by pattern alone",
            f"- opt-outs and disputes caught: {caught}/{total_hard}"
            + (
                " — these are matched by pattern as well as by model, so a model error cannot "
                "keep someone in a sequence they asked to leave"
                if total_hard
                else ""
            ),
            "",
        ]
        if stats.get("calls"):
            lines += [
                f"- model calls: {stats['calls']} ({stats.get('cache_hits', 0)} served from cache, "
                f"{stats.get('failures', 0)} failed)",
                f"- budget exhausted mid-run: {bool(stats.get('budget_exhausted'))}",
                "",
            ]
        lines += _writing_section(name, result)
    return lines


#: Roman-script Hindi function words. Three or more in a message is Hinglish; fewer is English
#: with a Hindi sign-off, which is the failure mode this metric exists to catch.
_HINDI = re.compile(
    r"\b(ka|ki|ke|nahi|hua|karein|kar|abhi|aapka|aapke|hai|bhejein|liye|se|par|ho|gaya|"
    r"dijiye|kripya|yahan|kyunki|dobara|kal|aaj|paise|paisa)\b",
    re.IGNORECASE,
)


def _writing_section(name: str, result) -> list[str]:
    """How much the model wrote, how often the guardrails refused it, and whether Hinglish was
    actually Hinglish. Measured on the messages that were sent, not on a hand-picked sample."""

    from wapsi.core.models import Language

    sent = list(result.messenger.sent)
    if not sent:
        return []
    written = [m for m in sent if m.llm_written]
    if not written:
        return []
    rejected = sum(
        1
        for e in result.audit.entries
        if e.kind == "verdict" and "R40" in e.rule_ids
    )
    hinglish_asked = [m for m in written if m.language is Language.hinglish]
    hinglish_real = sum(1 for m in hinglish_asked if len(_HINDI.findall(m.text)) >= 3)
    lines = [
        f"## Writing messages — {name}",
        "",
        f"- {len(sent)} messages sent; {len(written)} written by the model, "
        f"{len(sent) - len(written)} from templates",
        f"- guardrail rejections (R40), replaced by a template: {rejected}",
    ]
    if hinglish_asked:
        line = (
            f"- asked for Hinglish {len(hinglish_asked)} times; genuinely Hinglish "
            f"{hinglish_real} ({hinglish_real / len(hinglish_asked):.0%})"
        )
        if hinglish_real < len(hinglish_asked):
            line += " — the rest were English with a Hindi sign-off"
        lines.append(line)
    lines.append("")
    return lines


@app.command()
def sensitivity(
    n: int = typer.Option(500, help="Number of cases in the batch."),
    seed: int = typer.Option(42, help="Batch seed."),
    factors: str = typer.Option("0.7,1.0,1.3", help="Multipliers applied to every behaviour prior."),
    policies: str = typer.Option(DEFAULT_POLICIES, help="Policies to compare."),
    out: Path = typer.Option(RESULTS_DIR, help="Where to write sensitivity.md."),
) -> None:
    """Re-run the batch with the behaviour priors scaled up and down.

    The priors are estimates from published dunning figures, not measurements of this merchant.
    If the ranking of the policies survives a 30% error in either direction, the conclusion does
    not depend on those estimates being right. If it does not survive, the report says so.
    """

    from wapsi.core.metrics import compute
    from wapsi.sim.generator import generate
    from wapsi.sim.runner import Runner
    from wapsi.sim.world import World, load_config

    from wapsi.sim.world import HOSTILE_ASSUMPTIONS, apply_overrides

    names = [p.strip() for p in policies.split(",") if p.strip()]
    scales = [float(f) for f in factors.split(",") if f.strip()]
    body = [
        "# Sensitivity",
        "",
        f"{n} cases, seed {seed}. Two questions, each answered by re-running the whole batch.",
        "",
    ]

    # 1. Uniform scaling: are the published priors merely wrong by a constant?
    # 2. Hostile assumptions: are the penalties for bad behaviour what makes the compliant
    #    policy win? Every penalty that flatters it is turned down hard, and the dispute fee
    #    is set to zero. If the ranking holds here, it holds for a reason other than the
    #    simulation being harsh on the baselines.
    for label, overrides, dispute_fee in (
        ("default assumptions", {}, None),
        ("hostile assumptions", HOSTILE_ASSUMPTIONS, 0),
    ):
        grid: dict[float, dict[str, int]] = {}
        orders: dict[float, list[str]] = {}
        for scale in scales:
            console.print(f"[dim]{label}, behaviour scale {scale}...[/dim]")
            policy = PolicyEngine.load()
            if dispute_fee is not None:
                policy.economics["dispute_cost_paise"] = dispute_fee
            world = World(config=apply_overrides(load_config(), overrides), seed=seed)
            world.behaviour_scale = scale
            cases, population = generate(world, n=n)
            runner = Runner(world=world, cases=cases, population=population, policy=policy)
            grid[scale] = {name: compute(runner.run(name)).net_paise for name in names}
            orders[scale] = sorted(names, key=lambda p: grid[scale][p], reverse=True)

        reference = orders[scales[0]]
        stable = all(orders[s] == reference for s in scales)
        rows = [
            {"policy": name, **{f"x{s}": metrics_mod.rupees(grid[s][name]) for s in scales}}
            for name in names
        ]
        columns = [("policy", "policy")] + [(f"x{s}", f"priors x{s}") for s in scales]

        console.print(_render_table(rows, columns, f"net recovered — {label}"))
        console.print(
            f"[{'green' if stable else 'yellow'}]ranking "
            f"{'holds' if stable else 'CHANGES'} across the range "
            f"({' > '.join(reference)})[/]\n"
        )

        body += [
            f"## {label}",
            "",
            (
                "Every behaviour prior in `sim/config.yaml` multiplied by each factor."
                if not overrides
                else "As above, but with every assumption that punishes careless recovery turned "
                "down: night-time contact barely annoys anyone and never triggers a dispute, "
                "customers tolerate twice as many messages before complaining or opting out, "
                "retrying a risk-declined payment never causes a chargeback, and the chargeback "
                "fee is zero."
            ),
            "",
            metrics_mod.markdown_table(rows, columns),
            "",
            (
                f"Ranking unchanged across the range: {' > '.join(reference)}."
                if stable
                else "**Ranking changes across the range:** "
                + "; ".join(f"x{s}: {' > '.join(orders[s])}" for s in scales)
            ),
            "",
        ]

    out.mkdir(parents=True, exist_ok=True)
    (out / "sensitivity.md").write_text("\n".join(body), encoding="utf-8")
    console.print(f"[green]wrote[/green] {out / 'sensitivity.md'}")


@app.command()
def case(
    case_id: str = typer.Argument(..., help="Case id, e.g. case_0007."),
    policy: str = typer.Option("rules", help="Which policy's audit log to read."),
    out: Path = typer.Option(RESULTS_DIR, help="Where the audit logs live."),
) -> None:
    """Print one case's full audit timeline."""

    path = out / f"audit_{policy}.jsonl"
    if not path.exists():
        # Audit logs are large and regenerated deterministically, so they are not committed.
        # Say how to produce them rather than just reporting their absence.
        console.print(
            f"[yellow]No audit log at {path}.[/yellow]\n"
            "Audit logs are generated, not committed. Produce them with:\n\n"
            "  [bold]wapsi simulate[/bold]        (about 40 seconds, no keys needed)\n\n"
            "A worked example of one case is committed at "
            "[bold]results/case_0134.md[/bold] if you would rather just read one."
        )
        raise typer.Exit(1)
    entries = [e for e in AuditLog.read(path) if e.case_id == case_id]
    if not entries:
        known = sorted({e.case_id for e in AuditLog.read(path)})[:5]
        console.print(
            f"[yellow]No entries for {case_id} in {path}.[/yellow]\n"
            f"Cases in this log look like: {', '.join(known)}"
        )
        raise typer.Exit(1)
    console.print(format_timeline(entries))


@app.command()
def serve(
    port: int = typer.Option(8000, help="Port to listen on."),
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
) -> None:
    """Serve the dashboard and the webhook endpoint."""

    import uvicorn

    from wapsi.config import fingerprint, get_settings

    settings = get_settings()
    console.print(f"[bold]dashboard[/bold]  http://{host}:{port}/")
    console.print(f"[bold]webhook[/bold]    POST /webhooks/razorpay (and / as a fallback)")
    console.print(f"[dim]webhook secret loaded: {fingerprint(settings.razorpay_webhook_secret)}[/dim]")
    if not settings.razorpay_webhook_secret:
        console.print("[yellow]no webhook secret set — deliveries will be rejected[/yellow]")
    uvicorn.run("wapsi.api.app:app", host=host, port=port, log_level="warning")


@app.command()
def rules() -> None:
    """Print every bound the agent operates under, with its rule id."""

    from wapsi.core.policy import RULE_TEXT

    policy = PolicyEngine.load()
    table = Table(title="policy.yaml", header_style="bold")
    table.add_column("rule")
    table.add_column("meaning")
    for rule_id, text in sorted(RULE_TEXT.items()):
        table.add_row(rule_id, text)
    console.print(table)
    console.print(
        f"\nmessaging {policy.windows['customer_messaging']['start']}–"
        f"{policy.windows['customer_messaging']['end']} · "
        f"receivables {policy.windows['receivables_messaging']['start']}–"
        f"{policy.windows['receivables_messaging']['end']} · "
        f"AFA threshold ₹{policy.afa_threshold_paise / 100:,.0f}"
    )


from wapsi.live.cli import live as _live_app  # noqa: E402

app.add_typer(_live_app, name="live")


if __name__ == "__main__":
    app()
