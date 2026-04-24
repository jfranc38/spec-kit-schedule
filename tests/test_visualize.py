"""Smoke tests for the optional matplotlib visualiser.

The module is skipped entirely if matplotlib is unavailable — keeps the
core test suite green in `dev` environments without the `viz` extra.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("matplotlib")

from solver.visualize import render_dag, render_gantt  # noqa: E402


def _sample(tmp_path: Path) -> dict:
    return {
        "status": "OPTIMAL",
        "stats": {"makespan": 10, "total_agents": 2},
        "edges": [["T001", "T002"]],
        "assignments": [
            {"task_id": "T001", "task_index": 0, "agent_id": "a",
             "agent_index": 0, "start": 0, "end": 5, "duration": 5,
             "phase": "Setup", "story_id": None, "story_priority": 99,
             "file_paths": ["x.py"], "tokens": 500, "required_skill": "backend"},
            {"task_id": "T002", "task_index": 1, "agent_id": "b",
             "agent_index": 1, "start": 5, "end": 10, "duration": 5,
             "phase": "Setup", "story_id": None, "story_priority": 99,
             "file_paths": ["y.py"], "tokens": 500, "required_skill": "backend"},
        ],
        "critical_path": ["T001", "T002"],
    }


def test_render_dag_writes_image(tmp_path):
    out = tmp_path / "dag.png"
    render_dag(_sample(tmp_path), out, dpi=72)
    assert out.exists()
    assert out.stat().st_size > 0


def test_render_gantt_writes_image(tmp_path):
    out = tmp_path / "gantt.png"
    render_gantt(_sample(tmp_path), out, dpi=72)
    assert out.exists()
    assert out.stat().st_size > 0


def test_agent_palette_excludes_critical_color():
    """Agent fill must never collide with the critical-path red.

    If AGENT_COLORS contains CRITICAL_COLOR, a critical bar on that
    agent would be indistinguishable from a non-critical bar because
    fill and border share the same colour. Guard against accidental
    re-introduction of the clash.
    """
    from solver.defaults import AGENT_COLORS, CRITICAL_COLOR
    assert CRITICAL_COLOR not in AGENT_COLORS
