"""Self-diagnose installation state for /speckit.schedule.status.

The five-check report distinguishes ``missing`` (real problem the user
must fix) from ``expected-missing`` (state that bootstraps automatically
on the first ``/speckit.schedule.run``). Audit agents that flag a
missing ``schedule-config.yml`` post-install often see the latter case;
this module is the answer to "broken? or just not-yet-bootstrapped?".

Public surface:

* :func:`collect_status` — pure-function gather; never raises.
* :func:`format_status` — render plain-text report (terminal-safe).
* :func:`main` — CLI entry; ``python -m solver.status``.

Implementation notes
--------------------
* The five checks are ordered by dependency: extension files →
  hook → venv → portfolio → run history. ``format_status`` preserves
  this order so the surfaced "Next" hint targets the earliest-broken
  link in the chain.
* ``collect_status`` swallows every filesystem exception. The status
  command must be the LAST tool a user reaches for when nothing else
  works, so its own failure modes have to be effectively zero.
* No ``solver.config_schema`` import: status must work even when
  Pydantic / PyYAML imports break.
"""

from __future__ import annotations

__all__ = [
    "Overall",
    "State",
    "StatusItem",
    "StatusReport",
    "collect_status",
    "format_status",
    "main",
]

import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from . import __version__ as PACKAGE_VERSION
from ._paths import (
    encapsulated_venv_python,
    extension_code_dir,
    project_root,
    runs_dir,
    schedule_config_path,
)

log = logging.getLogger(__name__)


State = Literal["ok", "missing", "expected-missing", "stale", "unknown"]
Overall = Literal["healthy", "needs-attention", "first-run-pending"]


# ICON glyphs are restricted to a small terminal-safe palette. ``—`` is
# em-dash U+2014; the others are checkmark / warning / heavy ballot X.
# Audit tools that scrape stdout treat these as printable characters,
# not control sequences.
_ICONS: dict[State, str] = {
    "ok": "✓",  # ✓
    "missing": "✗",  # ✗
    "expected-missing": "—",  # —
    "stale": "⚠",  # ⚠
    "unknown": "⚠",  # ⚠
}


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class StatusItem:
    """A single check's outcome.

    ``state`` and ``detail`` always populated. ``hint`` is empty when no
    next-step is available (e.g. for ``ok`` items).
    """

    name: str
    state: State
    detail: str
    hint: str = ""


@dataclass
class StatusReport:
    """Full diagnostic output of :func:`collect_status`."""

    project_root: Path
    extension_version: str | None
    items: list[StatusItem] = field(default_factory=list)
    overall: Overall = "healthy"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_read_text(path: Path) -> str | None:
    """Read ``path`` as utf-8; return None on any IO/decoding error."""
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.debug("could not read %s: %s", path, exc)
        return None


_EXT_VERSION_RE = re.compile(r"^\s*version\s*:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", re.MULTILINE)


def _parse_extension_version(raw: str) -> str | None:
    """Pull ``version: '...'`` out of an ``extension.yml`` payload.

    A regex is intentional — we want this to keep working even if the
    PyYAML dependency is unavailable (status must run from a half-
    bootstrapped venv).
    """
    match = _EXT_VERSION_RE.search(raw)
    if match is None:
        return None
    value = match.group(1).strip()
    return value or None


def _check_extension_files(root: Path) -> tuple[StatusItem, str | None]:
    """Check ``.specify/extensions/schedule/extension.yml`` exists.

    Returns the status item plus the parsed extension version (or None
    when unparseable / missing) so the report header can surface it.
    """
    code_dir = extension_code_dir(root)
    manifest = code_dir / "extension.yml"
    raw = _safe_read_text(manifest)
    if raw is None:
        item = StatusItem(
            name="Extension files installed",
            state="missing",
            detail="extension.yml not found",
            hint=(
                "run `specify extension add schedule "
                "--from <release-zip-url>` to install"
            ),
        )
        return item, None
    version = _parse_extension_version(raw)
    if version is None:
        # File exists but unparseable: half-installed or hand-edited.
        item = StatusItem(
            name="Extension files installed",
            state="unknown",
            detail=f"{manifest.relative_to(root)} present but version unparseable",
            hint="reinstall the extension to restore a clean manifest",
        )
        return item, None
    item = StatusItem(
        name="Extension files installed",
        state="ok",
        detail=f"v{version} at {code_dir.relative_to(root)}/",
    )
    return item, version


_HOOK_LINE_RE = re.compile(
    # Accept either a list-of-mappings entry (``- extension: schedule``)
    # or a flat key-value (``extension: schedule``). Both are valid YAML
    # shapes spec-kit might emit when aggregating extension hooks.
    r"^\s*(?:-\s*)?extension\s*:\s*['\"]?schedule['\"]?\s*$",
    re.MULTILINE,
)
_AFTER_TASKS_RE = re.compile(r"^\s*after_tasks\s*:", re.MULTILINE)


def _check_hook_registered(root: Path) -> StatusItem:
    """Inspect ``.specify/extensions.yml`` for the schedule hook.

    The exact spec-kit format is "hooks.after_tasks" with an
    ``extension: schedule`` entry. We scan textually rather than parse
    YAML so this works without PyYAML.
    """
    extensions_yml = root / ".specify" / "extensions.yml"
    raw = _safe_read_text(extensions_yml)
    if raw is None:
        # No extensions.yml at all — could be a brand-new project that
        # never had any hook-using extension installed. Treat as
        # expected-missing rather than "broken".
        return StatusItem(
            name="Hook registered",
            state="expected-missing",
            detail=".specify/extensions.yml not present",
            hint=(
                "spec-kit writes this file when the first hook-using extension "
                "registers; reinstall schedule to seed it"
            ),
        )
    has_after_tasks = _AFTER_TASKS_RE.search(raw) is not None
    has_schedule_ref = _HOOK_LINE_RE.search(raw) is not None
    if has_after_tasks and has_schedule_ref:
        return StatusItem(
            name="Hook registered",
            state="ok",
            detail="after_tasks -> speckit.schedule.run",
        )
    return StatusItem(
        name="Hook registered",
        state="missing",
        detail=".specify/extensions.yml has no schedule hook",
        hint=(
            "reinstall with `specify extension remove schedule && "
            "specify extension add schedule --from <url>`"
        ),
    )


def _probe_python(executable: Path) -> str | None:
    """Return the interpreter's ``X.Y`` version, or None if unprobeable.

    Uses ``subprocess.run`` with a short timeout. Failure modes
    (missing binary, permission denied, broken venv, hung process) all
    map to ``None`` so the caller can frame this as "deps not yet
    bootstrapped".
    """
    import subprocess  # local import — keep status import cheap

    try:
        proc = subprocess.run(
            [str(executable), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("python probe failed: %s", exc)
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return out or None


def _check_solver_venv(root: Path) -> StatusItem:
    """Verify the encapsulated venv exists and runs."""
    py = encapsulated_venv_python(root)
    if not py.is_file():
        return StatusItem(
            name="Solver deps bootstrapped",
            state="expected-missing",
            detail="encapsulated venv not yet created",
            hint="next /speckit.schedule.run will auto-bootstrap it",
        )
    version = _probe_python(py)
    if version is None:
        return StatusItem(
            name="Solver deps bootstrapped",
            state="stale",
            detail=f"venv at {py.parent.parent.relative_to(root)}/ failed to launch",
            hint=(
                "rerun `bash .specify/extensions/schedule/bin/install.sh "
                "--target .specify/extensions/schedule/.venv`"
            ),
        )
    return StatusItem(
        name="Solver deps bootstrapped",
        state="ok",
        detail=f"Python {version} in {py.parent.parent.relative_to(root)}/",
    )


_AGENT_LINE_RE = re.compile(r"^\s*-\s*id\s*:", re.MULTILINE)


def _count_agents(raw: str) -> int:
    """Count agents from a schedule-config.yml without invoking PyYAML."""
    return len(_AGENT_LINE_RE.findall(raw))


def _check_portfolio(root: Path) -> StatusItem:
    """Check ``.specify/schedule/schedule-config.yml`` presence."""
    cfg = schedule_config_path(root)
    raw = _safe_read_text(cfg)
    if raw is None:
        return StatusItem(
            name="Portfolio configured",
            state="expected-missing",
            detail="not yet configured (auto-bootstraps on first run)",
            hint=(
                "run /speckit.schedule.run (idempotent — auto-creates) "
                "or /speckit.schedule.portfolio (interactive)"
            ),
        )
    n_agents = _count_agents(raw)
    if n_agents == 0:
        return StatusItem(
            name="Portfolio configured",
            state="unknown",
            detail=f"{cfg.relative_to(root)} present but no agents parsed",
            hint="open the config and verify the `agents:` section",
        )
    return StatusItem(
        name="Portfolio configured",
        state="ok",
        detail=f"{n_agents} agent{'s' if n_agents != 1 else ''} configured",
    )


def _check_run_history(root: Path) -> StatusItem:
    """Count plan logs in ``.specify/schedule/runs/``."""
    rdir = runs_dir(root)
    if not rdir.is_dir():
        return StatusItem(
            name="Run history",
            state="expected-missing",
            detail="no solves yet",
            hint="invoke /speckit.schedule.run to record the first plan",
        )
    try:
        plans = sorted(rdir.glob("*-plan.json"))
    except OSError as exc:
        log.debug("could not list runs/: %s", exc)
        return StatusItem(
            name="Run history",
            state="unknown",
            detail="runs/ directory unreadable",
        )
    if not plans:
        return StatusItem(
            name="Run history",
            state="expected-missing",
            detail="no solves yet",
            hint="invoke /speckit.schedule.run to record the first plan",
        )
    latest = plans[-1]
    # Plan filename stem looks like "<ISO8601>-<short-uuid>-plan"; the
    # leading component up to the second-to-last segment is the
    # timestamp. We surface the filename stem rather than parse it
    # because the run id format may evolve.
    return StatusItem(
        name="Run history",
        state="ok",
        detail=f"{len(plans)} solve{'s' if len(plans) != 1 else ''}, latest {latest.stem}",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def collect_status(project: Path | None = None) -> StatusReport:
    """Gather installation/config state. Defensive — never raises.

    Parameters
    ----------
    project:
        Project root override. ``None`` walks up from cwd looking for
        a ``.specify/`` ancestor (see :func:`solver._paths.project_root`).

    Returns
    -------
    StatusReport
        Five ordered :class:`StatusItem` records plus an overall verdict.
        Every error path produces a ``StatusItem`` rather than raising,
        so the CLI can always print a useful page.
    """
    root = project_root(project)
    items: list[StatusItem] = []

    extension_item, ext_version = _check_extension_files(root)
    items.append(extension_item)
    items.append(_check_hook_registered(root))
    items.append(_check_solver_venv(root))
    items.append(_check_portfolio(root))
    items.append(_check_run_history(root))

    overall = _verdict(items)
    return StatusReport(
        project_root=root,
        extension_version=ext_version,
        items=items,
        overall=overall,
    )


def _verdict(items: list[StatusItem]) -> Overall:
    """Aggregate per-item states into a single overall.

    Rules:

    * Any ``missing`` / ``stale`` / ``unknown`` → ``needs-attention``
      (these are real problems the user must address).
    * All ``ok`` → ``healthy``.
    * Mix of ``ok`` and ``expected-missing`` → ``first-run-pending``
      (everything is fine, just not yet bootstrapped).
    """
    if any(item.state in ("missing", "stale", "unknown") for item in items):
        return "needs-attention"
    if all(item.state == "ok" for item in items):
        return "healthy"
    return "first-run-pending"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


_NEXT_STEP_HEADERS: dict[Overall, str] = {
    "healthy": "All checks pass. The extension is fully bootstrapped.",
    "first-run-pending": (
        "Run /speckit.tasks (in your spec-kit workflow), then accept the "
        '"Generate an optimal CP-SAT schedule?" prompt to bootstrap and '
        "solve in one step. Or invoke /speckit.schedule.run directly any "
        "time."
    ),
    "needs-attention": (
        "Address the following item(s) in order — earlier items gate "
        "later ones:"
    ),
}


def format_status(report: StatusReport) -> str:
    """Render a multi-line plain-text report.

    The output uses simple Unicode glyphs (``✓ ⚠ ✗ —``) and no markdown
    so terminals, log scrapers, and audit tools all render it the same
    way. Width-targeted to ~80 columns.
    """
    lines: list[str] = []
    header_version = report.extension_version or PACKAGE_VERSION
    lines.append(f"spec-kit-schedule v{header_version} - installation status")
    lines.append(f"Project root: {report.project_root}")
    lines.append("")

    # Item table — pad the name column so the detail column lines up.
    name_width = max((len(item.name) for item in report.items), default=0)
    for item in report.items:
        icon = _ICONS.get(item.state, "?")
        lines.append(f"  {icon} {item.name.ljust(name_width)}    {item.detail}")

    lines.append("")
    lines.append(f"Status: {report.overall}")
    lines.append("")
    lines.append(_NEXT_STEP_HEADERS[report.overall])

    if report.overall == "needs-attention":
        # Surface every actionable hint, in dependency order.
        for item in report.items:
            if item.state in ("missing", "stale", "unknown") and item.hint:
                lines.append(f"  - {item.name}: {item.hint}")
    elif report.overall == "first-run-pending":
        # No errors, but mention which step bootstraps next so the user
        # knows what to expect.
        pending = [
            item for item in report.items if item.state == "expected-missing" and item.hint
        ]
        if pending:
            lines.append("")
            lines.append("Pending bootstrap steps (handled automatically):")
            for item in pending:
                lines.append(f"  - {item.name}: {item.hint}")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry: print status report and exit with a state-aware code.

    Exit codes:

    * ``0`` — ``healthy`` or ``first-run-pending`` (no user action
      required, or bootstrap will happen automatically).
    * ``1`` — ``needs-attention`` (the user must fix at least one item).

    ``argv`` is currently unused; reserved so future flags
    (``--json``, ``--quiet``) can land without breaking callers.
    """
    del argv  # placeholder for future CLI args
    report = collect_status()
    sys.stdout.write(format_status(report))
    return 0 if report.overall in ("healthy", "first-run-pending") else 1


if __name__ == "__main__":  # pragma: no cover  — executed via -m
    raise SystemExit(main())
