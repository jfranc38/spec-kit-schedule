"""Lightweight, config-free access to ``tasks.md`` checkboxes.

``tasks.md`` is the single source of execution state: a task is done when
its line reads ``- [x] T###``. The orchestrator (``python -m solver
mark``) is the only writer; subagents report ids instead of editing the
file, so concurrent lanes never race on it.

Both helpers are deliberately tiny: one regex over the lines, no parser.
"""

from __future__ import annotations

__all__ = ["mark_tasks", "scan_checkboxes", "unfenced_lines"]

import contextlib
import os
import re
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .defaults import TASK_ID_PATTERN
from .i18n import t
from .validation import ScheduleInputError

# A task line as the parser sees it: a top-level ``- [ ] T###`` bullet; the
# checkbox state is the single character inside the brackets.
_CHECKBOX_RE = re.compile(
    rf"^(?P<lead>-\s+\[)(?P<state>[ xX]?)(?P<rest>\]\s+(?P<id>{TASK_ID_PATTERN})\b)"
)


def unfenced_lines(lines: Iterable[str]) -> tuple[list[tuple[int, str]], int | None]:
    """``(kept, unclosed)``: ``(1-based line number, line)`` pairs outside ``` fences.

    Fence lines are dropped. ``unclosed`` is the line number of a fence that
    never closes (everything after it was skipped), else ``None``.
    """
    kept: list[tuple[int, str]] = []
    opened: int | None = None
    for num, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            opened = None if opened is not None else num
            continue
        if opened is None:
            kept.append((num, line))
    return kept, opened


def scan_checkboxes(path: str | Path) -> dict[str, bool]:
    """Return ``{task_id: done}`` in file order (first occurrence wins)."""
    out: dict[str, bool] = {}
    kept, _ = unfenced_lines(Path(path).read_text(encoding="utf-8").splitlines())
    for _num, line in kept:
        m = _CHECKBOX_RE.match(line)
        if m is None:
            continue
        tid = m.group("id")
        if tid not in out:
            out[tid] = m.group("state").lower() == "x"
    return out


def mark_tasks(path: str | Path, task_ids: Iterable[str], *, done: bool = True) -> int:
    """Flip the checkbox of every id in *task_ids*; return how many lines changed.

    Validates every id before touching the file (unknown ids raise
    :class:`ScheduleInputError`, nothing is written), then rewrites the
    file atomically. Idempotent: lines already in the requested state are
    left alone and not counted.
    """
    p = Path(path)
    # Tolerate "T001 T002" / "T001,T002" handed over as one argument (an
    # orchestrator pasting the DONE line) as well as separate arguments.
    tokens = [tok for t_id in task_ids for tok in re.split(r"[\s,]+", str(t_id)) if tok]
    wanted = list(dict.fromkeys(tokens))
    with p.open(encoding="utf-8", newline="") as fh:
        text = fh.read()
    known = scan_checkboxes(p)
    unknown = [tid for tid in wanted if tid not in known]
    if unknown:
        raise ScheduleInputError(t("mark_unknown_task", ids=", ".join(unknown), path=p))
    if not wanted:
        return 0

    target = "x" if done else " "
    remaining = set(wanted)
    changed = 0
    lines = text.split("\n")
    kept, _ = unfenced_lines(lines)
    for num, line in kept:
        m = _CHECKBOX_RE.match(line)
        if m is None or m.group("id") not in remaining:
            continue
        remaining.discard(m.group("id"))
        if (m.group("state") or " ").lower() == target:
            continue
        lines[num - 1] = f"{m.group('lead')}{target}{line[m.end('state'):]}"
        changed += 1
    if not changed:
        return 0

    tmp_fd, tmp_path = tempfile.mkstemp(dir=p.parent, prefix=".tasks_md_")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="") as fh:
            fh.write("\n".join(lines))
        shutil.copymode(p, tmp_path)
        os.replace(tmp_path, p)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
    return changed
