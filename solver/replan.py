#!/usr/bin/env python3
"""spec-kit-schedule: Incremental Replanning CLI.

Given a prior solver output, a (possibly modified) tasks.md, and a config,
produces a new schedule that respects frozen assignments from the prior run.

Usage:
    python -m solver.replan prior_output.json tasks.md config.yml [options]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "solver"  # noqa: A001

import yaml  # type: ignore[import-untyped, unused-ignore]

from .i18n import t
from .model.fixed import resolve_fixed_duration
from .parse_tasks import parse_tasks_md
from .scheduler import solve_with_fixed
from .validation import ScheduleInputError

__all__ = ["replan", "main"]

log = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────
# Core helpers
# ───────────────────────────────────────────────────────────────────────


def _reachable_non_completed(
    start: str,
    adj: dict[str, set[str]],
    completed_ids: set[str],
) -> set[str]:
    """Non-completed nodes reachable from *start* by traversing only through completed nodes.

    Used to compute the full transitive neighbour set when a chain of completed
    tasks separates non-completed nodes.
    """
    result: set[str] = set()
    queue = list(adj.get(start, set()))
    visited = {start}
    while queue:
        cur = queue.pop()
        if cur in visited:
            continue
        visited.add(cur)
        if cur not in completed_ids:
            result.add(cur)
        else:
            queue.extend(adj.get(cur, set()))
    return result


def _remove_completed(
    solver_input: dict[str, Any],
    completed_ids: set[str],
) -> tuple[dict[str, Any], int]:
    """Remove completed tasks and maintain transitive precedence.

    For each completed task C, all non-completed ancestors (reachable via
    completed intermediate nodes in reverse) receive a direct edge to every
    non-completed descendant of C.  This handles chains of consecutive
    completed tasks correctly.
    """
    tasks = solver_input["tasks"]
    edges = solver_input["edges"]

    succs: dict[str, set[str]] = defaultdict(set)
    preds: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        src, dst = str(edge[0]), str(edge[1])
        succs[src].add(dst)
        preds[dst].add(src)

    new_transitive: set[tuple[str, str]] = set()
    for task_id in completed_ids:
        non_completed_preds = _reachable_non_completed(task_id, preds, completed_ids)
        non_completed_succs = _reachable_non_completed(task_id, succs, completed_ids)
        for p in non_completed_preds:
            for s in non_completed_succs:
                new_transitive.add((p, s))

    kept_edges = [
        e for e in edges
        if str(e[0]) not in completed_ids and str(e[1]) not in completed_ids
    ]
    existing_edge_set = {(str(e[0]), str(e[1])) for e in kept_edges}
    for p, s in new_transitive:
        if (p, s) not in existing_edge_set:
            kept_edges.append([p, s])
            existing_edge_set.add((p, s))

    kept_tasks = [task for task in tasks if task["id"] not in completed_ids]
    removed = len(tasks) - len(kept_tasks)

    return {**solver_input, "tasks": kept_tasks, "edges": kept_edges}, removed


def _build_fixed_assignments(
    prior_assignments: list[dict[str, Any]],
    freeze_before: int,
    active_task_ids: set[str],
) -> dict[str, dict[str, Any]]:
    """Return tasks from prior output whose start < freeze_before.

    Propagates the prior duration via ``resolve_fixed_duration`` so the
    scheduler can pin the frozen task's duration as well as its start and
    agent — see ``_apply_fixed_constraints``.
    """
    fixed: dict[str, dict[str, Any]] = {}
    for assn in prior_assignments:
        task_id = assn.get("task_id", "")
        if task_id not in active_task_ids:
            continue
        if assn.get("start", 0) < freeze_before:
            entry: dict[str, Any] = {
                "agent_id": assn["agent_id"],
                "start": assn["start"],
            }
            if "duration" in assn or "end" in assn:
                entry["duration"] = resolve_fixed_duration(assn, None, task_id=task_id)
            fixed[task_id] = entry
    return fixed


def _build_prior_hints(
    prior_assignments: list[dict[str, Any]],
    fixed_ids: set[str],
    active_task_ids: set[str],
) -> dict[str, dict[str, Any]]:
    """Return prior assignments as hints for non-fixed active tasks."""
    hints: dict[str, dict[str, Any]] = {}
    for assn in prior_assignments:
        task_id = assn.get("task_id", "")
        if task_id in fixed_ids or task_id not in active_task_ids:
            continue
        hints[task_id] = {
            "agent_id": assn["agent_id"],
            "start": assn["start"],
        }
    return hints


# ───────────────────────────────────────────────────────────────────────
# Public library API
# ───────────────────────────────────────────────────────────────────────


def replan(
    prior_output: dict[str, Any],
    solver_input: dict[str, Any],
    freeze_before: int | None = None,
    completed_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Incrementally replan from a prior solver output.

    Applies completed-task removal and freeze constraints, then delegates
    to solve_with_fixed using prior assignments as warm hints.

    Returns the full result envelope with ``stats["replan"]`` populated.
    The caller is responsible for setting ``stats["replan"]["added_count"]``
    and ``stats["replan"]["reused_from"]`` when additional context is known.
    """
    prior_assignments: list[dict[str, Any]] = prior_output.get("assignments", [])

    completed_count = 0
    if completed_ids:
        solver_input, completed_count = _remove_completed(solver_input, completed_ids)

    active_task_ids = {task["id"] for task in solver_input["tasks"]}

    fixed_assignments: dict[str, dict[str, Any]] = {}
    if freeze_before is not None:
        fixed_assignments = _build_fixed_assignments(
            prior_assignments, freeze_before, active_task_ids
        )

    prior_hints = _build_prior_hints(
        prior_assignments,
        set(fixed_assignments.keys()),
        active_task_ids,
    )

    log.info(
        "replan: fixed=%d completed=%d active=%d",
        len(fixed_assignments),
        completed_count,
        len(active_task_ids),
    )

    result = solve_with_fixed(solver_input, fixed_assignments, prior_hints)
    result["stats"]["replan"] = {
        "fixed_count": len(fixed_assignments),
        "completed_count": completed_count,
        "added_count": 0,
        "reused_from": "",
    }
    return result


# ───────────────────────────────────────────────────────────────────────
# CLI
# ───────────────────────────────────────────────────────────────────────


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m solver.replan",
        description="Incrementally replan a schedule from a prior solver output.",
    )
    ap.add_argument("prior_output", help="Path to prior solver output JSON.")
    ap.add_argument("tasks_md", help="Path to (possibly modified) tasks.md.")
    ap.add_argument("config_yml", help="Path to schedule-config.yml.")
    ap.add_argument(
        "--freeze-before",
        metavar="T",
        type=int,
        default=None,
        help="Fix start/agent for all tasks where start < T in the prior output.",
    )
    ap.add_argument(
        "--completed",
        metavar="IDS",
        default=None,
        help="Comma-separated task IDs completed since the prior run (e.g. T001,T003).",
    )
    ap.add_argument(
        "--add-task",
        metavar="LINE",
        action="append",
        dest="add_tasks",
        default=[],
        help="Inject a raw task line into tasks.md before parsing (repeatable).",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        with open(args.prior_output, encoding="utf-8") as fh:
            prior_output = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: cannot read prior output: {exc}", file=sys.stderr)
        return 2

    try:
        with open(args.config_yml, encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}
    except OSError as exc:
        print(f"ERROR: cannot read config: {exc}", file=sys.stderr)
        return 2

    added_count = len(args.add_tasks)
    tasks_md_path = args.tasks_md
    tmp_path: str | None = None

    try:
        if args.add_tasks:
            original = Path(args.tasks_md).read_text(encoding="utf-8")
            injected = "\n".join(args.add_tasks)
            modified = original.rstrip("\n") + "\n" + injected + "\n"
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(modified)
                tmp_path = tmp.name
            tasks_md_path = tmp_path

        try:
            solver_input = parse_tasks_md(tasks_md_path, config)
        except ScheduleInputError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    completed_ids: set[str] | None = None
    if args.completed:
        raw_ids = {tid.strip() for tid in args.completed.split(",") if tid.strip()}
        active_ids = {task["id"] for task in solver_input["tasks"]}
        unknown = raw_ids - active_ids
        if unknown:
            print(
                f"ERROR: {t('replan_completed_unknown', ids=sorted(unknown))}",
                file=sys.stderr,
            )
            return 2
        completed_ids = raw_ids

    try:
        result = replan(
            prior_output,
            solver_input,
            freeze_before=args.freeze_before,
            completed_ids=completed_ids,
        )
    except ScheduleInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    result["stats"]["replan"]["added_count"] = added_count
    result["stats"]["replan"]["reused_from"] = str(Path(args.prior_output).resolve())

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
