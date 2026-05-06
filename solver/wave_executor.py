#!/usr/bin/env python3
"""spec-kit-schedule: schedule.md Parser and Wave Executor Bridge.

Parses a rendered schedule.md into an ExecutionPlan and can emit that plan
as JSON, a POSIX shell script, or a plain-text table.

Usage:
    python -m solver.wave_executor schedule.md [--format json|shell|table]
"""

from __future__ import annotations

__all__ = [
    "AgentSpec",
    "TaskSpec",
    "Wave",
    "ExecutionPlan",
    "parse_schedule_md",
    "main",
]

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "solver"  # noqa: A001

from .i18n import t
from .validation import ScheduleInputError

# ── Compiled regex constants ──────────────────────────────────────────────────

# Matches: "# Schedule — <name>" (em-dash or ASCII hyphen)
_HEADING_RE = re.compile(r"^#\s+Schedule\s+[—\-]+\s+(.+)$", re.MULTILINE)

# Matches: "> Status: **X** | Makespan: **N** ... | Waves: **N** | Agents: **N**"
_META_RE = re.compile(
    r"Makespan:\s*\*\*(\d+)\*\*.*?Waves:\s*\*\*(\d+)\*\*.*?Agents:\s*\*\*(\d+)\*\*"
)

# Matches: "### Wave N (t=X) — <phase>"  (em-dash or hyphen)
_WAVE_HEADING_RE = re.compile(
    r"^###\s+Wave\s+(\d+)\s+\(t=(\d+)\)\s+[—\-]+\s+(.+)$", re.MULTILINE
)

# Matches a markdown table data row: | col | col | ... |
# Allows tabs and varying amounts of whitespace around pipes.
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")

# Matches: "### <agent_id> (<model>[· <provider>]) — ..."
# The separator between model and provider is either "·" (U+00B7) or "·" variants,
# or a plain dot. Provider is optional.
_AGENT_HEADING_RE = re.compile(
    r"^###\s+(\S+)\s+\(([^)·]+?)(?:\s*[·•]\s*([^)]+?))?\)\s+[—\-]",
    re.MULTILINE,
)

# Strip backtick file references like `src/foo.py` → src/foo.py
_BACKTICK_RE = re.compile(r"`([^`]+)`")

# ── Data model ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgentSpec:
    """Identity and provider metadata for one agent."""

    id: str
    model: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class TaskSpec:
    """A single scheduled task with its timing and file list."""

    task_id: str
    agent_id: str
    start: int
    end: int
    duration: int
    files: tuple[str, ...] = ()
    phase: str | None = None


@dataclass(frozen=True)
class Wave:
    """A barrier-separated group of concurrently executable tasks."""

    index: int          # 1-based
    start_time: int
    tasks: tuple[TaskSpec, ...]


@dataclass(frozen=True)
class ExecutionPlan:
    """Complete parsed representation of a schedule.md file."""

    feature_name: str
    makespan: int
    waves: tuple[Wave, ...]
    agents: tuple[AgentSpec, ...]
    metadata: dict[str, str] = field(default_factory=dict)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _strip_files(cell: str) -> tuple[str, ...]:
    """Extract file paths from a table cell, stripping backtick markup."""
    paths = _BACKTICK_RE.findall(cell)
    if not paths:
        stripped = cell.strip()
        if stripped and stripped != "-":
            return (stripped,)
        return ()
    return tuple(p.strip() for p in paths if p.strip())


def _parse_table_rows(lines: list[str]) -> list[dict[str, str]]:
    """Parse markdown table rows between the heading and the next section.

    Skips header and separator rows; returns a list of column-name → value
    dicts where column names are lower-cased.
    """
    headers: list[str] = []
    rows: list[dict[str, str]] = []
    for line in lines:
        m = _TABLE_ROW_RE.match(line)
        if not m:
            continue
        raw_cells = [c.strip() for c in m.group(1).split("|")]
        if not headers:
            # First matching row is the header.
            headers = [h.lower().strip() for h in raw_cells]
            continue
        # Separator row: all cells match "---" patterns.
        if all(re.fullmatch(r":?-+:?", c) for c in raw_cells if c):
            continue
        if len(raw_cells) != len(headers):
            continue
        rows.append(dict(zip(headers, raw_cells, strict=False)))
    return rows


def _parse_waves(text: str, wave_indices: list[tuple[int, int, re.Match[str]]]) -> list[Wave]:
    """Extract Wave objects from the Execution Waves section of the document."""
    waves: list[Wave] = []
    for _pos, (start_char, end_char, m) in enumerate(wave_indices):
        wave_idx = int(m.group(1))
        start_time = int(m.group(2))
        section_text = text[start_char:end_char]
        section_lines = section_text.splitlines()

        rows = _parse_table_rows(section_lines)
        tasks: list[TaskSpec] = []
        for row in rows:
            task_id = row.get("task", "").strip()
            agent_id = row.get("agent", "").strip()
            duration_raw = row.get("duration", "0").strip()
            files_raw = row.get("files", "")
            phase_raw = row.get("phase", "").strip() or None

            if not task_id or not agent_id:
                continue
            try:
                duration = int(duration_raw)
            except ValueError:
                duration = 0

            files = _strip_files(files_raw)
            end_time = start_time + duration
            tasks.append(TaskSpec(
                task_id=task_id,
                agent_id=agent_id,
                start=start_time,
                end=end_time,
                duration=duration,
                files=files,
                phase=phase_raw,
            ))
        waves.append(Wave(index=wave_idx, start_time=start_time, tasks=tuple(tasks)))
    return waves


def _parse_agents(text: str) -> list[AgentSpec]:
    """Extract AgentSpec list from the Agent Assignments section."""
    agents: list[AgentSpec] = []
    seen: set[str] = set()
    for m in _AGENT_HEADING_RE.finditer(text):
        agent_id = m.group(1).strip()
        model = m.group(2).strip() if m.group(2) else None
        provider = m.group(3).strip() if m.group(3) else None
        if agent_id not in seen:
            agents.append(AgentSpec(id=agent_id, model=model, provider=provider))
            seen.add(agent_id)
    return agents


# ── Public API ────────────────────────────────────────────────────────────────

def parse_schedule_md(path: str | Path) -> ExecutionPlan:
    """Parse a rendered schedule.md into an ExecutionPlan.

    Raises ScheduleInputError for malformed or missing required structure:
    missing heading, no metadata line, no waves, or orphaned task rows.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScheduleInputError(
            t("cannot_read_file", file_kind="schedule", path_suffix=f" {p}", error=exc)
        ) from exc

    # Feature name.
    heading_m = _HEADING_RE.search(text)
    if not heading_m:
        raise ScheduleInputError(t("schedule_file_no_heading", path=p))
    feature_name = heading_m.group(1).strip()

    # Metadata line.
    meta_m = _META_RE.search(text)
    if not meta_m:
        raise ScheduleInputError(t("schedule_file_no_metadata", path=p))
    makespan = int(meta_m.group(1))
    declared_waves = int(meta_m.group(2))

    # Locate all wave sections and their text spans.
    wave_matches = list(_WAVE_HEADING_RE.finditer(text))
    if not wave_matches:
        raise ScheduleInputError(t("schedule_file_no_waves", path=p))

    # Build (start_char, end_char, match) spans.
    spans: list[tuple[int, int, re.Match[str]]] = []
    for i, m in enumerate(wave_matches):
        start_char = m.start()
        end_char = wave_matches[i + 1].start() if i + 1 < len(wave_matches) else len(text)
        spans.append((start_char, end_char, m))

    waves = _parse_waves(text, spans)

    # Validate: every wave must have at least one task.
    for wave in waves:
        if not wave.tasks:
            raise ScheduleInputError(
                t(
                    "wave_exec_no_tasks_in_wave",
                    index=wave.index,
                    start_time=wave.start_time,
                    path=p,
                )
            )

    # Warn (via error) if wave count mismatches declared metadata.
    if len(waves) != declared_waves:
        raise ScheduleInputError(
            t(
                "wave_exec_wave_count_mismatch",
                declared=declared_waves,
                parsed=len(waves),
                path=p,
            )
        )

    agents = _parse_agents(text)
    if not agents:
        raise ScheduleInputError(t("wave_exec_no_agents", path=p))

    # Cross-check: every task's agent_id must appear in the agent list.
    agent_ids = {a.id for a in agents}
    for wave in waves:
        for task in wave.tasks:
            if task.agent_id not in agent_ids:
                raise ScheduleInputError(
                    t(
                        "wave_exec_unknown_agent",
                        task_id=task.task_id,
                        agent_id=task.agent_id,
                    )
                )

    return ExecutionPlan(
        feature_name=feature_name,
        makespan=makespan,
        waves=tuple(waves),
        agents=tuple(agents),
        metadata={},
    )


# ── Serialisation helpers ─────────────────────────────────────────────────────

def _plan_to_dict(plan: ExecutionPlan) -> dict[str, Any]:
    return {
        "feature_name": plan.feature_name,
        "makespan": plan.makespan,
        "waves": [
            {
                "index": w.index,
                "start_time": w.start_time,
                "tasks": [
                    {
                        "task_id": t.task_id,
                        "agent_id": t.agent_id,
                        "start": t.start,
                        "end": t.end,
                        "duration": t.duration,
                        "files": list(t.files),
                        "phase": t.phase,
                    }
                    for t in w.tasks
                ],
            }
            for w in plan.waves
        ],
        "agents": [
            {"id": a.id, "model": a.model, "provider": a.provider}
            for a in plan.agents
        ],
        "metadata": plan.metadata,
    }


def _emit_json(plan: ExecutionPlan) -> str:
    return json.dumps(_plan_to_dict(plan), indent=2)


def _emit_shell(plan: ExecutionPlan) -> str:
    """Emit a POSIX shell script that executes waves with barrier waits."""
    lines: list[str] = [
        "#!/usr/bin/env sh",
        "# Generated by solver.wave_executor — do not edit.",
        "# Set RUNNER to the command that accepts (agent_id task_id) arguments.",
        "# Example: RUNNER='my-impl-runner' sh plan.sh",
        "set -euo pipefail",
        "",
        f"# Feature: {plan.feature_name}",
        f"# Makespan: {plan.makespan}",
        f"# Waves: {len(plan.waves)}",
        "",
        ': "${RUNNER:?RUNNER env var must be set to the agent runner command}"',
        "",
    ]

    for wave in plan.waves:
        lines.append(f"# ── Wave {wave.index} (t={wave.start_time}) ──")
        lines.append(f"echo 'Starting wave {wave.index} (t={wave.start_time})...'")
        for task in wave.tasks:
            lines.append(
                f'$RUNNER {task.agent_id} {task.task_id} &'
            )
        lines.append("wait")
        lines.append(f"echo 'Wave {wave.index} complete.'")
        lines.append("")

    lines.append("echo 'All waves complete.'")
    return "\n".join(lines)


def _col_widths(rows: list[tuple[str, ...]], headers: tuple[str, ...]) -> list[int]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    return widths


def _emit_table(plan: ExecutionPlan) -> str:
    """Emit a plain-text aligned table: Wave, Time, Agent, Task, Files."""
    headers = ("Wave", "Time", "Agent", "Task", "Files")
    rows: list[tuple[str, ...]] = []
    for wave in plan.waves:
        for task in wave.tasks:
            files_str = ", ".join(task.files) if task.files else "-"
            rows.append((
                str(wave.index),
                str(wave.start_time),
                task.agent_id,
                task.task_id,
                files_str,
            ))

    widths = _col_widths(rows, headers)
    sep = "  ".join("-" * w for w in widths)

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=False))

    lines = [fmt(headers), sep]
    lines.extend(fmt(row) for row in rows)
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

_Format = Literal["json", "shell", "table"]

# Callable[..., str] keeps mypy happy at the call site; ``object`` here
# would force a ``# type: ignore[operator]`` because mypy cannot prove
# each entry is callable. The narrower type is also more honest about
# what the dispatcher dict actually holds.
_Formatter = Callable[[ExecutionPlan], str]

_FORMATTERS: dict[str, _Formatter] = {
    "json": _emit_json,
    "shell": _emit_shell,
    "table": _emit_table,
}


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="wave_executor",
        description="Parse schedule.md and emit an execution plan.",
    )
    ap.add_argument("schedule", help="Path to schedule.md")
    ap.add_argument(
        "--format",
        choices=list(_FORMATTERS),
        default="json",
        dest="fmt",
        help="Output format: json (default), shell, or table.",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for both `python -m solver.wave_executor` and direct invocation."""
    args = _build_argparser().parse_args(argv)
    try:
        plan = parse_schedule_md(args.schedule)
    except ScheduleInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    formatter = _FORMATTERS[args.fmt]
    output = formatter(plan)
    sys.stdout.write(output)
    if not output.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
