"""Tests for solver.status — installation self-diagnose command.

The status command is the answer to "is this extension broken, or just
not yet bootstrapped?". The tests anchor each of the three terminal
states (``healthy``, ``first-run-pending``, ``needs-attention``)
against a synthetic project tree on disk. Pure-function ``collect_status``
must never raise and must distinguish ``missing`` (real problem) from
``expected-missing`` (auto-bootstraps on first run).
"""

from __future__ import annotations

__all__: list[str] = []

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from solver import status
from solver._paths import (
    encapsulated_venv_python,
    extension_code_dir,
    runs_dir,
    schedule_config_path,
)

# ---------------------------------------------------------------------------
# Synthetic-state builders
# ---------------------------------------------------------------------------


def _make_specify(tmp_path: Path) -> Path:
    """Create a minimal ``.specify/`` so ``project_root`` resolves to ``tmp_path``."""
    (tmp_path / ".specify").mkdir()
    return tmp_path


def _install_extension_files(root: Path, version: str = "0.6.1") -> None:
    """Drop a fake ``extension.yml`` under ``.specify/extensions/schedule/``."""
    code_dir = extension_code_dir(root)
    code_dir.mkdir(parents=True, exist_ok=True)
    (code_dir / "extension.yml").write_text(
        f"schema_version: \"1.0\"\n"
        f"extension:\n"
        f"  id: \"schedule\"\n"
        f"  version: \"{version}\"\n",
        encoding="utf-8",
    )


def _register_hook(root: Path) -> None:
    """Drop a minimal ``.specify/extensions.yml`` with the schedule hook."""
    (root / ".specify" / "extensions.yml").write_text(
        "hooks:\n"
        "  after_tasks:\n"
        "    - extension: schedule\n"
        "      command: speckit.schedule.run\n",
        encoding="utf-8",
    )


def _install_venv(root: Path) -> None:
    """Symlink the host's interpreter into the encapsulated venv path.

    We do not build a full venv (slow + flaky in CI); we only need the
    file to exist and be executable. Since ``_probe_python`` shells
    out, we reuse the live ``sys.executable`` so the probe succeeds.
    """
    import os
    import sys

    py = encapsulated_venv_python(root)
    py.parent.mkdir(parents=True, exist_ok=True)
    # Use a symlink when available; fall back to copying.
    try:
        os.symlink(sys.executable, py)
    except OSError:  # pragma: no cover  — fallback for restrictive FS
        import shutil

        shutil.copy2(sys.executable, py)
        py.chmod(0o755)


def _write_portfolio(root: Path, agents: int = 2) -> None:
    """Create a minimal ``schedule-config.yml`` with ``agents`` entries."""
    cfg = schedule_config_path(root)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    body = "agents:\n"
    for idx in range(agents):
        body += f"  - id: a{idx}\n    model: test\n    skills: [py]\n"
    cfg.write_text(body, encoding="utf-8")


def _add_runs(root: Path, count: int = 1) -> None:
    """Drop ``count`` synthetic ``*-plan.json`` files under runs/."""
    rdir = runs_dir(root)
    rdir.mkdir(parents=True, exist_ok=True)
    for idx in range(count):
        (rdir / f"2026-05-07T12:0{idx}:00Z-fake{idx:04d}-plan.json").write_text(
            "{}\n", encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# collect_status — pure function tests
# ---------------------------------------------------------------------------


class TestCollectStatusEmpty:
    def test_empty_dir_reports_needs_attention(self, tmp_path: Path) -> None:
        """No ``.specify/`` at all → all checks miss; verdict needs-attention.

        The earliest-broken item (extension files) carries the missing
        state; the rest are expected-missing because the project has
        not been initialised at all.
        """
        report = status.collect_status(tmp_path)
        assert report.overall == "needs-attention"
        # Every item produced.
        assert len(report.items) == 5
        # The extension-files check is the gate.
        ext_item = next(i for i in report.items if i.name == "Extension files installed")
        assert ext_item.state == "missing"
        assert "specify extension add" in ext_item.hint


class TestCollectStatusFirstRunPending:
    """The "quantkit case": extension installed but never run yet.

    Files + hook + venv all there, but the user has not yet invoked
    ``/speckit.schedule.run``, so the portfolio config and run logs
    are absent. The verdict must be ``first-run-pending``, NOT
    ``needs-attention`` — those two missing items are expected.
    """

    def test_extension_installed_but_unused(self, tmp_path: Path) -> None:
        root = _make_specify(tmp_path)
        _install_extension_files(root)
        _register_hook(root)
        _install_venv(root)
        # No portfolio, no runs.
        report = status.collect_status(root)
        assert report.overall == "first-run-pending"

        states = {item.name: item.state for item in report.items}
        assert states["Extension files installed"] == "ok"
        assert states["Hook registered"] == "ok"
        assert states["Solver deps bootstrapped"] == "ok"
        assert states["Portfolio configured"] == "expected-missing"
        assert states["Run history"] == "expected-missing"


class TestCollectStatusHealthy:
    def test_full_install_reports_healthy(self, tmp_path: Path) -> None:
        root = _make_specify(tmp_path)
        _install_extension_files(root)
        _register_hook(root)
        _install_venv(root)
        _write_portfolio(root, agents=3)
        _add_runs(root, count=3)

        report = status.collect_status(root)
        assert report.overall == "healthy"
        assert all(item.state == "ok" for item in report.items)
        # The portfolio item details the agent count.
        portfolio = next(i for i in report.items if i.name == "Portfolio configured")
        assert "3 agents" in portfolio.detail
        # The run-history item details the latest run.
        history = next(i for i in report.items if i.name == "Run history")
        assert "3 solves" in history.detail


class TestCollectStatusHookMissing:
    def test_post_install_hook_missing_is_needs_attention(self, tmp_path: Path) -> None:
        root = _make_specify(tmp_path)
        _install_extension_files(root)
        # Drop a malformed extensions.yml with no schedule hook.
        (root / ".specify" / "extensions.yml").write_text(
            "hooks: {}\n", encoding="utf-8"
        )
        _install_venv(root)

        report = status.collect_status(root)
        # A post-install hook miss IS a real problem.
        hook_item = next(i for i in report.items if i.name == "Hook registered")
        assert hook_item.state == "missing"
        assert "specify extension remove" in hook_item.hint
        assert report.overall == "needs-attention"


class TestCollectStatusUnparseableManifest:
    def test_corrupt_extension_yml_returns_unknown(self, tmp_path: Path) -> None:
        root = _make_specify(tmp_path)
        code_dir = extension_code_dir(root)
        code_dir.mkdir(parents=True, exist_ok=True)
        # File present but no version line.
        (code_dir / "extension.yml").write_text(
            "schema_version: '1.0'\nextension:\n  id: schedule\n", encoding="utf-8"
        )

        report = status.collect_status(root)
        ext_item = next(i for i in report.items if i.name == "Extension files installed")
        assert ext_item.state == "unknown"
        # The "reinstall" hint is surfaced.
        assert "reinstall" in ext_item.hint
        assert report.overall == "needs-attention"


class TestCollectStatusVenvBroken:
    def test_venv_executable_present_but_unprobeable(self, tmp_path: Path) -> None:
        """A venv binary that fails to launch is ``stale``, not ``missing``.

        Distinguishing these matters: ``missing`` means run ``install.sh``;
        ``stale`` means *something* exists but we cannot trust it.
        """
        root = _make_specify(tmp_path)
        _install_extension_files(root)
        _register_hook(root)
        # Plant a sentinel file at the python path that is not executable.
        py = encapsulated_venv_python(root)
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        py.chmod(0o755)

        report = status.collect_status(root)
        venv_item = next(i for i in report.items if i.name == "Solver deps bootstrapped")
        assert venv_item.state == "stale"
        assert report.overall == "needs-attention"


class TestCollectStatusReturnsTypedReport:
    def test_report_shape(self, tmp_path: Path) -> None:
        report = status.collect_status(tmp_path)
        assert isinstance(report, status.StatusReport)
        assert isinstance(report.items, list)
        assert all(isinstance(item, status.StatusItem) for item in report.items)
        assert report.overall in ("healthy", "needs-attention", "first-run-pending")
        # One item per check, in the documented order.
        names = [item.name for item in report.items]
        assert names == [
            "Extension files installed",
            "Hook registered",
            "Solver deps bootstrapped",
            "Portfolio configured",
            "Run history",
        ]

    def test_does_not_raise_on_unwritable_root(self, tmp_path: Path) -> None:
        """Even with a hostile FS the gather must produce a report.

        We call ``collect_status`` on a non-existent ``project`` argument
        and confirm the function still returns a valid report (the
        ``project_root`` helper falls back to the resolved path).
        """
        non_existent = tmp_path / "does-not-exist"
        report = status.collect_status(non_existent)
        assert isinstance(report, status.StatusReport)
        # Every item still produced.
        assert len(report.items) == 5


class TestCollectStatusPortfolioCornerCases:
    def test_portfolio_with_zero_agents_is_unknown(self, tmp_path: Path) -> None:
        root = _make_specify(tmp_path)
        _install_extension_files(root)
        _register_hook(root)
        _install_venv(root)
        cfg = schedule_config_path(root)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        # File exists but has no `- id:` lines.
        cfg.write_text("# empty config\nagents: []\n", encoding="utf-8")

        report = status.collect_status(root)
        portfolio = next(i for i in report.items if i.name == "Portfolio configured")
        assert portfolio.state == "unknown"
        assert report.overall == "needs-attention"

    def test_portfolio_agent_count_singular(self, tmp_path: Path) -> None:
        root = _make_specify(tmp_path)
        _write_portfolio(root, agents=1)
        report = status.collect_status(root)
        portfolio = next(i for i in report.items if i.name == "Portfolio configured")
        # "1 agent configured" — singular form.
        assert portfolio.state == "ok"
        assert portfolio.detail.startswith("1 agent ")
        assert " agents " not in portfolio.detail


# ---------------------------------------------------------------------------
# format_status — output shape tests
# ---------------------------------------------------------------------------


class TestFormatStatus:
    def test_no_markdown(self, tmp_path: Path) -> None:
        """Output must be terminal-readable, not markdown.

        Confirms the audit / log-scrape consumers don't have to strip
        ``**`` or leading ``#`` to get a clean read.
        """
        report = status.collect_status(tmp_path)
        out = status.format_status(report)
        assert "**" not in out
        # No markdown headers.
        for line in out.splitlines():
            assert not line.lstrip().startswith("#")

    def test_terminal_safe_glyphs(self, tmp_path: Path) -> None:
        """Exit-state glyphs are the documented small palette."""
        report = status.collect_status(tmp_path)
        out = status.format_status(report)
        # Every line is printable: no control chars below 0x20 except \n / \t.
        for ch in out:
            if ch in "\n\t":
                continue
            assert ord(ch) >= 0x20

    def test_includes_overall_status_line(self, tmp_path: Path) -> None:
        report = status.collect_status(tmp_path)
        out = status.format_status(report)
        assert "Status: needs-attention" in out

    def test_first_run_pending_friendly_message(self, tmp_path: Path) -> None:
        root = _make_specify(tmp_path)
        _install_extension_files(root)
        _register_hook(root)
        _install_venv(root)
        report = status.collect_status(root)
        out = status.format_status(report)
        assert report.overall == "first-run-pending"
        # Message reassures the user this is normal pre-first-run.
        assert "Status: first-run-pending" in out
        assert "/speckit.schedule.run" in out
        # No scary "needs-attention" hint shown.
        assert "Address the following" not in out

    def test_healthy_summary_is_short(self, tmp_path: Path) -> None:
        root = _make_specify(tmp_path)
        _install_extension_files(root)
        _register_hook(root)
        _install_venv(root)
        _write_portfolio(root)
        _add_runs(root, count=2)
        report = status.collect_status(root)
        out = status.format_status(report)
        assert "Status: healthy" in out
        assert "All checks pass" in out

    def test_needs_attention_lists_each_hint(self, tmp_path: Path) -> None:
        # Fully empty project → multiple actionable hints.
        out = status.format_status(status.collect_status(tmp_path))
        # The dependency-ordered hint section is present.
        assert "Address the following" in out
        # Extension files (the gate) appears as a hint line.
        assert "- Extension files installed:" in out

    def test_header_carries_extension_version_when_known(self, tmp_path: Path) -> None:
        root = _make_specify(tmp_path)
        _install_extension_files(root, version="9.9.9")
        out = status.format_status(status.collect_status(root))
        # The header surfaces the parsed version.
        assert "v9.9.9" in out

    def test_header_falls_back_to_package_version(self, tmp_path: Path) -> None:
        # No extension.yml → header falls back to ``solver.__version__``.
        out = status.format_status(status.collect_status(tmp_path))
        from solver import __version__

        assert f"v{__version__}" in out


# ---------------------------------------------------------------------------
# main() CLI entry
# ---------------------------------------------------------------------------


class TestMainCli:
    def test_exit_0_on_healthy(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_specify(tmp_path)
        _install_extension_files(root)
        _register_hook(root)
        _install_venv(root)
        _write_portfolio(root)
        _add_runs(root)
        monkeypatch.chdir(root)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = status.main()
        assert rc == 0
        assert "Status: healthy" in buf.getvalue()

    def test_exit_1_on_needs_attention(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_specify(tmp_path)
        # Extension files NOT installed → guaranteed needs-attention.
        monkeypatch.chdir(root)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = status.main()
        assert rc == 1
        assert "Status: needs-attention" in buf.getvalue()

    def test_exit_0_on_first_run_pending(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The quantkit case — informational, not a failure."""
        root = _make_specify(tmp_path)
        _install_extension_files(root)
        _register_hook(root)
        _install_venv(root)
        # No portfolio, no runs.
        monkeypatch.chdir(root)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = status.main()
        assert rc == 0
        assert "Status: first-run-pending" in buf.getvalue()
