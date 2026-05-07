"""Capture solver plans and observed actuals for the calibration feedback loop.

This module owns the disk format consumed by
:func:`solver.calibrate.calibrate_from_runs`. Two artefacts per run:

* ``<run_id>-plan.json``   — written immediately after a successful solve
  by :func:`record_plan`. A SUBSET of the result envelope: only the
  fields needed for downstream calibration (per-task expected
  duration + agent assignment + summary stats). Kept small on purpose
  so the runs directory stays cheap to scan.
* ``<run_id>-actual.jsonl`` — appended one-line-per-task by
  :func:`append_actual` once the user records observed wall-clock
  durations. JSONL keeps writes append-only, atomic per line, and
  cheap for partially-completed runs.

``run_id`` is ``<ISO8601 timestamp>-<short-uuid>`` so concurrent solves
in the same second still produce unique files.

Public API
----------
- :func:`record_plan` — best-effort plan persistence after a solve.
  NEVER raises into the caller; the result envelope is returned
  whether the write succeeded or not.
- :func:`append_actual` — record one task's observed duration.
- :func:`new_run_id` — exposed for tests / advanced callers that need
  the canonical id format.
- ``main`` / CLI subcommand ``append-actual`` — manual recording from
  the command line.

Usage (library)::

    from solver import run_log
    plan_path = run_log.record_plan(result, project_root=Path("."))
    run_log.append_actual(
        task_id="T001",
        agent_id="opus",
        actual_duration=92,
        run_id="2026-05-07T12:34:56Z-abc123",
        project_root=Path("."),
    )

Usage (CLI)::

    python -m solver.run_log append-actual \\
        --run-id 2026-05-07T12:34:56Z-abc123 \\
        --task T001 --agent opus --duration 92
"""

from __future__ import annotations

__all__ = [
    "RUN_LOG_SCHEMA_VERSION",
    "append_actual",
    "main",
    "new_run_id",
    "record_plan",
]

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import _paths
from ._paths import runs_dir

log = logging.getLogger(__name__)

RUN_LOG_SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return current UTC time as ``YYYY-MM-DDTHH:MM:SSZ`` (no microseconds)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(now: str | None = None) -> str:
    """Mint a fresh ``run_id`` of the form ``<ISO8601>-<short-uuid>``.

    Parameters
    ----------
    now:
        Override the timestamp portion (used by tests for determinism).
        Should already be in ``YYYY-MM-DDTHH:MM:SSZ`` form.
    """
    ts = now or _utc_now_iso()
    short = uuid.uuid4().hex[:8]
    return f"{ts}-{short}"


def _plan_path(run_id: str, project_root: Path | None = None) -> Path:
    return runs_dir(project_root) / f"{run_id}-plan.json"


def _actual_path(run_id: str, project_root: Path | None = None) -> Path:
    return runs_dir(project_root) / f"{run_id}-actual.jsonl"


def _build_plan_payload(
    result: dict[str, Any],
    *,
    run_id: str,
    config_path: str | None,
    tasks_md_path: str | None,
    objective: str | None,
) -> dict[str, Any]:
    """Reduce the full solver result to the small subset persisted on disk.

    Only the fields required by ``calibrate_from_runs`` plus enough
    metadata for humans to sanity-check the file are kept. Anything
    that is expensive to serialise (graph fragments, etc.) is dropped.
    """
    stats = result.get("stats") or {}
    assignments = []
    for raw in result.get("assignments") or []:
        # Keep only the calibration-relevant fields. ``expected_duration``
        # is the canonical name used by ``calibrate_from_runs``.
        # ``estimated_tokens`` is the bucket key the runs-mode
        # calibrator uses to attribute observed durations to a
        # complexity tier (the assignment dict from
        # ``solver.result.extract`` calls this field ``tokens``).
        assignments.append(
            {
                "task_id": raw.get("task_id"),
                "agent_id": raw.get("agent_id"),
                "expected_duration": raw.get("duration"),
                "expected_start": raw.get("start"),
                "expected_end": raw.get("end"),
                "estimated_tokens": raw.get("tokens"),
            }
        )

    payload: dict[str, Any] = {
        "schema_version": RUN_LOG_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": _utc_now_iso(),
        "config_path": config_path,
        "tasks_md_path": tasks_md_path,
        "objective": objective,
        "status": result.get("status"),
        "makespan": result.get("makespan", stats.get("makespan")),
        "max_load": result.get("max_load", stats.get("max_load")),
        "total_cost": result.get("total_cost", stats.get("total_cost")),
        "assignments": assignments,
    }
    return payload


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_plan(
    result: dict[str, Any],
    *,
    project_root: Path | None = None,
    run_id: str | None = None,
    config_path: str | None = None,
    tasks_md_path: str | None = None,
    objective: str | None = None,
) -> Path | None:
    """Persist the post-solve plan to ``<runs_dir>/<run_id>-plan.json``.

    Best-effort: any filesystem error is logged at WARNING level and
    swallowed. The function never raises into the caller — the
    contract with ``_finalize_result`` is "the solver result is the
    source of truth; calibration capture is an opportunistic side-
    channel, not a fail-stop".

    Parameters
    ----------
    result:
        The full solver result envelope (as returned by
        ``_finalize_result``).
    project_root:
        Directory under which ``.specify/schedule/runs/`` lives. When
        ``None`` the path is resolved from ``cwd`` via
        :func:`solver._paths.runs_dir`.
    run_id:
        Optional pre-allocated run id. Defaults to
        :func:`new_run_id`.
    config_path / tasks_md_path / objective:
        Optional metadata recorded into the plan for humans/tests.

    Returns
    -------
    Path | None
        The path that was written, or ``None`` if the write failed
        for any reason.
    """
    rid = run_id or new_run_id()
    try:
        # ``project_root(start)`` falls back to ``start`` when no
        # ``.specify/`` ancestor exists. Treat that case as "no
        # capture target available" so synthetic test invocations and
        # ad-hoc CLI runs in ``examples/`` do not leak ``.specify/``
        # directories into unrelated trees.
        root = _paths.project_root(project_root)
        if not (root / ".specify").is_dir():
            log.debug(
                "record_plan: no .specify/ ancestor under %s; skipping capture",
                root,
            )
            return None
        target = _plan_path(rid, project_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = _build_plan_payload(
            result,
            run_id=rid,
            config_path=config_path,
            tasks_md_path=tasks_md_path,
            objective=objective,
        )
        # Indent for human inspection; runs are small so the cost is
        # negligible.
        target.write_text(
            json.dumps(payload, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        log.info("recorded plan to %s", target)
        return target
    except Exception as exc:  # noqa: BLE001  — best-effort capture
        # Any write failure (read-only fs, disk full, permission denied,
        # serialisation glitch) is non-fatal: the solver result is
        # already in memory and downstream callers do not depend on
        # the side-effect succeeding.
        log.warning("record_plan: failed to write run log (%s); continuing", exc)
        return None


def append_actual(
    task_id: str,
    agent_id: str,
    actual_duration: float,
    run_id: str,
    *,
    project_root: Path | None = None,
    notes: str | None = None,
    completed_at: str | None = None,
) -> Path:
    """Append one observed-duration record to ``<run_id>-actual.jsonl``.

    Single-user assumption: no file locking. The caller is expected
    to issue one ``append_actual`` per completed task, in any order.
    Multiple records for the same ``task_id`` are allowed and are
    treated as overrides by ``calibrate_from_runs`` (last-write-wins).

    Parameters
    ----------
    task_id, agent_id:
        Must match the ids used in the corresponding plan file.
    actual_duration:
        Observed wall-clock duration. Same time unit as the plan's
        ``expected_duration`` (typically minutes).
    run_id:
        The id returned by :func:`record_plan`.
    project_root:
        Override for the runs directory root.
    notes:
        Optional free-form annotation (e.g. "task failed mid-way and
        was restarted").
    completed_at:
        ISO8601 completion timestamp; defaults to "now (UTC)".

    Returns
    -------
    Path
        The actuals file that was written to.
    """
    target = _actual_path(run_id, project_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "task_id": task_id,
        "agent_id": agent_id,
        "actual_duration": actual_duration,
        "completed_at": completed_at or _utc_now_iso(),
        "notes": notes,
    }
    # Append-only: no read-modify-write to avoid race conditions on
    # repeated invocations.
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=False) + "\n")
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m solver.run_log",
        description=(
            "Manual run-log helpers: record observed durations against a "
            "previously-captured plan."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    aa = sub.add_parser(
        "append-actual",
        help="Append one observed-duration record to the run's actuals file.",
    )
    aa.add_argument("--run-id", required=True, help="Run id from record_plan().")
    aa.add_argument("--task", required=True, help="Task identifier (e.g. T001).")
    aa.add_argument("--agent", required=True, help="Agent identifier from the portfolio.")
    aa.add_argument(
        "--duration",
        required=True,
        type=float,
        help="Observed wall-clock duration (same units as expected_duration).",
    )
    aa.add_argument("--notes", default=None, help="Optional free-form annotation.")
    aa.add_argument(
        "--completed-at",
        default=None,
        help="Override completion timestamp (defaults to now, UTC ISO 8601).",
    )
    aa.add_argument(
        "--project-root",
        default=None,
        type=Path,
        help="Override project root (defaults to cwd's nearest .specify/ ancestor).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.cmd == "append-actual":
        path = append_actual(
            task_id=args.task,
            agent_id=args.agent,
            actual_duration=args.duration,
            run_id=args.run_id,
            project_root=args.project_root,
            notes=args.notes,
            completed_at=args.completed_at,
        )
        print(f"appended to {path}")
        return 0
    return 2  # pragma: no cover  — argparse ``required=True`` rejects this branch


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
