"""Subagent briefs — the executable half of a schedule.

A brief is a self-contained markdown instruction sheet for ONE subagent
during ONE round: its ordered task segment, the files its tasks name,
the files other lanes own this round (the hard "must NOT edit" rule),
the read-only context documents, the ground rules, and the exact report
format the orchestrator parses afterwards. Editing other existing files
is allowed — spec-kit tasks are often path-less ("Add logging …") — but
every touched path must be reported so the orchestrator can detect
collisions between lanes.

``tasks.md`` has a single writer — the orchestrator — so briefs never
ask a subagent to tick checkboxes; they ask for a ``DONE:`` /
``FAILED:`` / ``TOUCHED:`` report instead (see ``commands/implement.md``).

Pure module: dict in, markdown out.
"""

from __future__ import annotations

__all__ = [
    "BriefContext",
    "lane_files",
    "parse_report",
    "render_brief",
    "render_round",
]

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ._render_helpers import lane_files, task_index

REPORT_DONE = "DONE:"
REPORT_FAILED = "FAILED:"
REPORT_TOUCHED = "TOUCHED:"

_TASK_ID_RE = re.compile(r"\bT\d{3,4}\b")


@dataclass(frozen=True)
class BriefContext:
    """Where the feature documents live; every path is optional."""

    feature_name: str = ""
    spec_path: str | None = None
    plan_path: str | None = None
    tasks_path: str | None = None


def _task_line(task: Mapping[str, Any]) -> str:
    label = task.get("story_id") or task.get("phase") or ""
    desc = str(task.get("description") or "").strip() or "(no description)"
    tag = f"  `[{label}]`" if label else ""
    return f"**{task['id']}** — {desc}{tag}"


def render_brief(
    plan: Mapping[str, Any],
    round_block: Mapping[str, Any],
    lane: Mapping[str, Any],
    *,
    context: BriefContext | None = None,
    total_rounds: int | None = None,
) -> str:
    """Markdown brief for ``lane`` of ``round_block``.

    ``round_block`` / ``lane`` use the ``rounds`` JSON shape
    (``{"round": k, "lanes": [{"agent_id", "tasks"}]}``).
    """
    ctx = context or BriefContext()
    by_id = task_index(plan)
    agent_id = str(lane["agent_id"])
    task_ids = [str(t) for t in lane["tasks"]]
    lanes = round_block.get("lanes", []) or []
    n_lanes = len(lanes)
    round_no = int(round_block.get("round", 1))
    of = f" of {total_rounds}" if total_rounds else ""
    feature = f" — {ctx.feature_name}" if ctx.feature_name else ""

    own = lane_files(plan, task_ids)
    own_set = set(own)
    others: dict[str, str] = {}
    for other in lanes:
        if str(other["agent_id"]) == agent_id:
            continue
        for fp in lane_files(plan, [str(t) for t in other["tasks"]]):
            if fp not in own_set:
                others.setdefault(fp, str(other["agent_id"]))

    lines: list[str] = [
        f"# Subagent brief{feature}: lane `{agent_id}`, round {round_no}{of}",
        "",
        (
            f"You are one of {n_lanes} subagents working **in parallel** on this feature. "
            "Complete ONLY the tasks below, in the order given. Other subagents are "
            "editing other files right now — the file lists keep you from colliding."
            if n_lanes > 1
            else "Complete ONLY the tasks below, in the order given."
        ),
        "",
        "## Context (read-only)",
        "",
    ]
    docs = [
        ("Spec", ctx.spec_path, ""),
        ("Plan", ctx.plan_path, ""),
        ("Tasks", ctx.tasks_path, "  (do NOT edit — the orchestrator marks tasks done)"),
    ]
    any_doc = False
    for label, path, note in docs:
        if path:
            any_doc = True
            lines.append(f"- {label}: `{path}`{note}")
    if not any_doc:
        lines.append("- Read the feature's `spec.md` and `plan.md` before starting.")
    lines += ["", "## Your tasks (in order)", ""]
    for i, tid in enumerate(task_ids, 1):
        task = by_id.get(tid, {"id": tid})
        lines.append(f"{i}. {_task_line(task)}")
        files = task.get("file_paths") or []
        if files:
            lines.append(f"   - files: {', '.join(f'`{f}`' for f in files)}")
    lines += ["", "## Files these tasks name", ""]
    if own:
        lines += [f"- `{f}`" for f in own]
    else:
        lines.append(
            "- (none named — the tasks say what to change; every file you create or "
            "edit must appear in TOUCHED)"
        )
    if others:
        lines += ["", "## Files you must NOT edit (owned by other lanes this round)", ""]
        lines += [f"- `{f}`  ({owner})" for f, owner in others.items()]
    lines += [
        "",
        "## Rules",
        "",
        "- Never edit a file listed under \"must NOT edit\" — another subagent is "
        "changing it right now. If a task truly needs one of them, stop that task "
        "and report it as FAILED with the reason.",
        "- Creating new files and editing other existing files is fine, but list "
        "**every** path you created or edited in TOUCHED — the orchestrator uses it "
        "to detect collisions between lanes.",
        "- Do not edit `tasks.md` and do not run `git commit` — the orchestrator does both.",
        "- Follow `plan.md` conventions; keep each change minimal and self-contained.",
        "- If a task is a test task, write the test and run it; report whether it "
        "fails/passes as the task intends.",
        "- Work through the tasks strictly in order; a later task may depend on an earlier one.",
        "",
        "## Report (the LAST lines of your reply, exactly this format)",
        "",
        "```",
        f"{REPORT_DONE} {' '.join(task_ids)}",
        f"{REPORT_FAILED} <task id> <one-line reason>   (omit this line if nothing failed)",
        f"{REPORT_TOUCHED} <space-separated paths you created or edited>",
        "NOTES: <anything the orchestrator should know, or '-'>",
        "```",
        "",
    ]
    return "\n".join(lines)


def render_round(
    plan: Mapping[str, Any],
    round_block: Mapping[str, Any],
    *,
    context: BriefContext | None = None,
    total_rounds: int | None = None,
    remaining_tasks: int | None = None,
) -> str:
    """Orchestrator view of one round: header, lane briefs, blocked lanes."""
    lanes = list(round_block.get("lanes", []) or [])
    round_no = int(round_block.get("round", 1))
    of = f" of {total_rounds}" if total_rounds else ""
    n_tasks = sum(len(lane["tasks"]) for lane in lanes)
    head = [
        f"# Round {round_no}{of} — {len(lanes)} lane{'s' if len(lanes) != 1 else ''}, "
        f"{n_tasks} task{'s' if n_tasks != 1 else ''}"
        + (f", {remaining_tasks} remaining after this round" if remaining_tasks is not None else ""),
        "",
        "Launch one subagent per lane **in parallel** with the brief below, wait "
        "for all of them, then collect their DONE/FAILED/TOUCHED reports.",
        "",
        "| Lane | Tasks | Files |",
        "|------|-------|-------|",
    ]
    for lane in lanes:
        files = ", ".join(f"`{f}`" for f in lane_files(plan, lane["tasks"])) or "-"
        head.append(f"| {lane['agent_id']} | {' → '.join(lane['tasks'])} | {files} |")
    blocked = list(round_block.get("blocked", []) or [])
    if blocked:
        head += ["", "Blocked lanes (their next task waits on another lane):", ""]
        head += [
            f"- {b['agent_id']}: {b['task_id']} waits on {', '.join(b['waiting_on'])}"
            for b in blocked
        ]
    parts = ["\n".join(head)]
    for lane in lanes:
        parts.append("---\n\n" + render_brief(
            plan, round_block, lane, context=context, total_rounds=total_rounds
        ))
    return "\n\n".join(parts).rstrip() + "\n"


def parse_report(text: str) -> dict[str, list[str]]:
    """Extract ``DONE`` / ``FAILED`` task ids and ``TOUCHED`` paths from a reply.

    Tolerant: scans every line, accepts the markers anywhere in the text,
    ignores case, and de-duplicates while preserving order. ``failed``
    lists task ids only (the free-text reason stays in the reply).
    """
    out: dict[str, list[str]] = {"done": [], "failed": [], "touched": []}

    def _add(key: str, items: Iterable[str]) -> None:
        for item in items:
            if item and item not in out[key]:
                out[key].append(item)

    for raw in text.splitlines():
        line = raw.strip().strip("`").strip()
        upper = line.upper()
        if upper.startswith(REPORT_DONE):
            _add("done", _TASK_ID_RE.findall(line[len(REPORT_DONE):]))
        elif upper.startswith(REPORT_FAILED):
            _add("failed", _TASK_ID_RE.findall(line[len(REPORT_FAILED):]))
        elif upper.startswith(REPORT_TOUCHED):
            _add("touched", (p.strip("`,") for p in line[len(REPORT_TOUCHED):].split()))
    # A task cannot be both; FAILED wins so the orchestrator never marks it.
    out["done"] = [t for t in out["done"] if t not in out["failed"]]
    return out
