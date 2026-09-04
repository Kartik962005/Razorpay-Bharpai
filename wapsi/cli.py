"""Command line: ``wapsi simulate``, ``wapsi report``, ``wapsi case``.

Reconfigures stdout to UTF-8 on start-up. Every number this tool prints is a rupee amount, and
the default Windows console encoding cannot represent the symbol.
"""

from __future__ import annotations

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
    quiet: bool = typer.Option(False, help="Suppress the tables; still writes results."),
) -> None:
    """Run every policy over one identical batch and report what each recovered."""

    from wapsi.sim.generator import generate
    from wapsi.sim.runner import Runner
    from wapsi.sim.world import World, load_config

    policy = PolicyEngine.load()
    world = World(config=load_config(), seed=seed)
    world.behaviour_scale = scale
    cases, population = generate(world, n=n)

    out.mkdir(parents=True, exist_ok=True)
    runner = Runner(
        world=world, cases=cases, population=population, policy=policy, results_dir=out
    )

    names = [p.strip() for p in policies.split(",") if p.strip()]
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

    if "rules" in results and "naive" in results:
        lost = metrics_mod.where_agent_lost(results, "naive", "rules")
        lines += [
            "## Where the naive policy beat the agent",
            "",
            f"{len(lost)} case(s). These are cases the cause-blind policy recovered and Wapsi "
            "did not, usually because Wapsi declined to contact someone the rules protect.",
            "",
        ]
        if lost:
            lines.append(
                metrics_mod.markdown_table(
                    lost[:20],
                    [
                        ("case", "case"),
                        ("cause", "cause"),
                        ("amount", "amount"),
                        ("naive_outcome", "naive"),
                        ("rules_outcome", "wapsi"),
                    ],
                )
            )
            lines.append("")

    return "\n".join(lines)


@app.command()
def case(
    case_id: str = typer.Argument(..., help="Case id, e.g. case_0007."),
    policy: str = typer.Option("rules", help="Which policy's audit log to read."),
    out: Path = typer.Option(RESULTS_DIR, help="Where the audit logs live."),
) -> None:
    """Print one case's full audit timeline."""

    path = out / f"audit_{policy}.jsonl"
    if not path.exists():
        console.print(f"[red]no audit log at {path}; run `wapsi simulate` first[/red]")
        raise typer.Exit(1)
    entries = [e for e in AuditLog.read(path) if e.case_id == case_id]
    if not entries:
        console.print(f"[yellow]no entries for {case_id} in {path}[/yellow]")
        raise typer.Exit(1)
    console.print(format_timeline(entries))


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


if __name__ == "__main__":
    app()
