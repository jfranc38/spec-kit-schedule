"""``bin/speckit-schedule`` fast paths (no bootstrap): healthy venv, broken override."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WRAPPER = ROOT / "bin" / "speckit-schedule"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="bash wrapper",
)


def _run(env_venv: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SPECKIT_SCHEDULE_VENV": str(env_venv)}
    return subprocess.run(
        ["bash", str(WRAPPER), *args], capture_output=True, text=True, env=env, timeout=120,
    )


def test_healthy_override_venv_runs_help() -> None:
    venv = Path(sys.prefix)
    if not (venv / "pyvenv.cfg").is_file():
        pytest.skip("tests not running inside a venv")
    proc = _run(venv, "--help")
    assert proc.returncode == 0, proc.stderr
    assert "plan" in proc.stdout and "next" in proc.stdout
    assert (venv / ".deps-ok-cli").read_text().strip() == str(venv)


def test_broken_override_venv_is_refused_not_deleted(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /nonexistent\n")
    fake = venv / "bin" / "python"
    fake.write_text("#!/usr/bin/env bash\nexit 127\n")
    fake.chmod(0o755)
    proc = _run(venv, "--help")
    assert proc.returncode == 1
    assert "does not run" in proc.stderr
    assert fake.exists(), "wrapper must never delete an override venv"
