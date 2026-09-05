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


def _venv_dir(tmp_path: Path, python_script: str) -> Path:
    """A venv-shaped directory whose ``bin/python`` is *python_script*."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /nonexistent\n")
    fake = venv / "bin" / "python"
    fake.write_text(python_script)
    fake.chmod(0o755)
    return venv


def _run(venv: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "SPECKIT_SCHEDULE_VENV": str(venv), "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        ["bash", str(WRAPPER), *args], capture_output=True, text=True, env=env, timeout=120,
    )


def test_healthy_override_venv_runs_help(tmp_path: Path) -> None:
    # The test interpreter stands in for the venv's python; the sentinel lands in tmp_path.
    venv = _venv_dir(tmp_path, f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n')
    proc = _run(venv, "--help")
    assert proc.returncode == 0, proc.stderr
    assert "{plan,next,mark,status}" in proc.stdout
    assert (venv / ".deps-ok-cli").read_text().strip() == str(venv)


def test_broken_override_venv_is_refused_not_deleted(tmp_path: Path) -> None:
    venv = _venv_dir(tmp_path, "#!/usr/bin/env bash\nexit 127\n")
    proc = _run(venv, "--help")
    assert proc.returncode == 1
    assert "does not run" in proc.stderr
    assert (venv / "bin" / "python").exists(), "wrapper must never delete an override venv"
