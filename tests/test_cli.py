"""Every command runs.

The unit tests exercise the engine directly and never invoke the CLI, which is how a rename left
a dead reference on the exit path of `bharpai live watch` — invisible to 177 passing tests and
fatal the moment someone ran it. These are cheap smoke tests against that whole class of miss.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from bharpai.cli import app

runner = CliRunner()


@pytest.mark.parametrize(
    "command",
    [
        ["--help"],
        ["simulate", "--help"],
        ["sensitivity", "--help"],
        ["case", "--help"],
        ["serve", "--help"],
        ["rules"],
        ["live", "--help"],
        ["live", "doctor", "--help"],
        ["live", "seed", "--help"],
        ["live", "watch", "--help"],
        ["live", "reset", "--help"],
    ],
)
def test_every_command_is_reachable(command):
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output


def test_rules_prints_every_bound_with_its_id():
    result = runner.invoke(app, ["rules"])
    assert result.exit_code == 0
    for rule in ("R01", "R10", "R14", "R23", "R41"):
        assert rule in result.output


def test_a_small_batch_runs_end_to_end(tmp_path):
    """The headline command, with no keys, writing real output."""

    result = runner.invoke(
        app,
        ["simulate", "--n", "40", "--policies", "do_nothing,platform,rules",
         "--out", str(tmp_path), "--quiet"],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "summary.json").exists()

    import json

    policies = {p["policy"] for p in json.loads((tmp_path / "summary.json").read_text())["policies"]}
    assert policies == {"do_nothing", "platform", "rules"}


def test_case_explains_how_to_produce_a_missing_log(tmp_path):
    result = runner.invoke(app, ["case", "case_0001", "--out", str(tmp_path)])
    assert result.exit_code == 1
    assert "bharpai simulate" in result.output


def test_live_reset_touches_only_local_state(isolated_state):
    (isolated_state / "cases.json").write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["live", "reset"])
    assert result.exit_code == 0
    # The seeded entities still exist in Razorpay; only local tracking is cleared.
    assert "seed.json kept" in result.output


def test_a_live_case_id_reads_the_live_trail(isolated_state):
    """One command shows either kind of case. The demo depends on it."""

    from datetime import datetime

    from bharpai.config import IST
    from bharpai.core.audit import AuditLog

    audit = AuditLog(isolated_state / "audit.jsonl")
    audit.record(
        ts=datetime.now(IST),
        case_id="live_pay_ABC123",
        kind="verdict",
        actor="planner",
        summary="waiting until 10:00 to SEND_PAYMENT_LINK",
        rule_ids=["R10"],
    )

    result = runner.invoke(app, ["case", "live_pay_ABC123"])
    assert result.exit_code == 0, result.output
    assert "R10" in result.output


def test_an_unknown_live_case_says_how_to_produce_one(isolated_state):
    result = runner.invoke(app, ["case", "live_pay_NOPE"])
    assert result.exit_code == 1
    assert "bharpai live watch" in result.output


def test_the_results_table_never_truncates_the_numbers_it_exists_to_show():
    """A table squeezed into a narrow terminal renders "₹1,7…", which tells a reader nothing.

    This is the one visual the whole comparison rests on, so it sheds columns rather than
    characters. Everything dropped is still in report.md and summary.json.
    """

    from bharpai.cli import CONSOLE_COLUMNS, _fit

    rows = [
        {
            "policy": "do_nothing", "recovered": 111, "recovery_rate": "22.2%",
            "recovered_value": "₹453,210", "cost": "₹13", "net": "₹453,197",
            "vs_baseline": "₹0", "contacts": 0, "opt_outs": 0, "disputes": 0, "violations": 0,
        },
        {
            "policy": "rules", "recovered": 233, "recovery_rate": "46.6%",
            "recovered_value": "₹1,724,498", "cost": "₹659", "net": "₹1,723,840",
            "vs_baseline": "₹1,270,643", "contacts": 816, "opt_outs": 62,
            "disputes": 1, "violations": 0,
        },
    ]

    for width in (60, 80, 100, 120, 200):
        kept = _fit(CONSOLE_COLUMNS, rows, width)
        rendered = sum(max(len(label), *(len(str(r[key])) for r in rows)) + 3 for key, label in kept) + 1
        assert rendered <= width or len(kept) == 5, f"{width} columns still overflows: {rendered}"

        # The comparison itself is never surrendered, however narrow the terminal.
        keys = [key for key, _ in kept]
        assert "policy" in keys and "net" in keys and "violations" in keys
        assert "recovered" in keys

    # A wide terminal keeps everything.
    assert _fit(CONSOLE_COLUMNS, rows, 200) == CONSOLE_COLUMNS


def test_the_committed_results_are_the_ones_the_readme_quotes():
    """Guard the evidence against a stray `simulate` overwriting it.

    `bharpai simulate` rewrites `results/` with whatever policies were asked for, so running it
    with a smaller batch — or without the model-advised row — silently replaces the figures the
    README, the report and the video script all quote. That has happened twice. A reviewer would
    see a README claiming numbers its own `summary.json` does not contain, which is worse than any
    bug in the code.
    """

    import json

    from bharpai.config import RESULTS_DIR

    summary = RESULTS_DIR / "summary.json"
    if not summary.exists():
        pytest.skip("no batch has been run in this checkout")

    data = json.loads(summary.read_text(encoding="utf-8"))
    policies = [p["policy"] for p in data["policies"]]

    assert data["meta"]["n"] == 500, (
        f"committed results are a {data['meta']['n']}-case batch; the README quotes 500. "
        "Restore with: git checkout -- results/"
    )
    for expected in ("do_nothing", "platform", "naive", "rules", "agent"):
        assert expected in policies, (
            f"committed results are missing the {expected!r} row. "
            "Restore with: git checkout -- results/"
        )
