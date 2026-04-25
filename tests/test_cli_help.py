"""Regression: every CLI entry-point module responds to --help without error."""

import subprocess
import sys

import pytest

MODULES = [
    "solver.parse_tasks",
    "solver.scheduler",
    "solver.render_schedule",
    "solver.visualize",
    "solver.render_html",
]


@pytest.mark.parametrize("module", MODULES)
def test_help_exits_zero(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"'{module} --help' exited {result.returncode}\n"
        f"stdout: {result.stdout[:500]}\n"
        f"stderr: {result.stderr[:500]}"
    )
    assert result.stdout.strip(), f"'{module} --help' produced no output"
