"""Every command runs.

The unit tests exercise the engine directly and never invoke the CLI, which is how a rename left
a dead reference on the exit path of `wapsi live watch` — invisible to 177 passing tests and
fatal the moment someone ran it. These are cheap smoke tests against that whole class of miss.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from wapsi.cli import app

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
    assert "wapsi simulate" in result.output


def test_live_reset_touches_only_local_state(isolated_state):
    (isolated_state / "cases.json").write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["live", "reset"])
    assert result.exit_code == 0
    # The seeded entities still exist in Razorpay; only local tracking is cleared.
    assert "seed.json kept" in result.output
