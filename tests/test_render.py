"""Unit tests for solver.render_schedule."""
from __future__ import annotations

from solver.render_schedule import render


def _minimal_solver_output() -> dict:
    return {
        "status": "OPTIMAL",
        "stats": {
            "status": "OPTIMAL", "makespan": 10, "max_load": 10,
            "min_load": 5, "load_range": 5, "total_tasks": 2,
            "total_agents": 1, "total_waves": 2, "horizon": 20,
            "phase1_time": 0.1, "phase1_status": "OPTIMAL",
            "phase2_time": 0.1, "phase2_status": "OPTIMAL",
        },
        "assignments": [
            {
                "task_id": "T001", "task_index": 0, "agent_id": "backend",
                "agent_index": 0, "start": 0, "end": 5, "duration": 5,
                "phase": "Setup", "story_id": None, "story_priority": 99,
                "file_paths": ["src/a.py"], "tokens": 500,
                "required_skill": "backend",
            },
            {
                "task_id": "T002", "task_index": 1, "agent_id": "backend",
                "agent_index": 0, "start": 5, "end": 10, "duration": 5,
                "phase": "Setup", "story_id": None, "story_priority": 99,
                "file_paths": ["src/b.py"], "tokens": 500,
                "required_skill": "backend",
            },
        ],
        "waves": [
            {"wave": 1, "start_time": 0, "tasks": []},
            {"wave": 2, "start_time": 5, "tasks": []},
        ],
        "agent_summary": [{
            "agent_id": "backend", "model": "test", "task_count": 2,
            "total_tokens": 1000, "budget_utilization": 10.0,
            "total_load": 10, "kappa_utilization": 20.0,
            "tasks": ["T001", "T002"],
        }],
        "edges": [["T001", "T002"]],
        "warnings": [],
    }


def test_render_contains_required_sections():
    out = render(_minimal_solver_output(), "feat")
    assert "# Schedule — feat" in out
    assert "## Agent Assignments" in out
    assert "## Execution Waves" in out
    assert "## Gantt Chart" in out
    assert "## Dependency DAG" in out
    assert "## Solver Statistics" in out


def test_render_uses_real_edges():
    out = render(_minimal_solver_output(), "feat")
    # Non-critical parser edge.
    assert "T001 --> T002" in out


def test_resource_edges_rendered_as_dotted():
    data = _minimal_solver_output()
    data["resource_edges"] = [["T001", "T002"]]
    data["edges"] = []  # parser has no explicit precedence
    out = render(data, "feat")
    assert "T001 -.-> T002" in out


def test_render_warnings_section_only_when_present():
    data = _minimal_solver_output()
    assert "## ⚠ Warnings" not in render(data, "feat")
    data["warnings"] = [
        {"code": "phase2_fallback", "message": "timed out", "context": {"k": 1}}
    ]
    out = render(data, "feat")
    assert "## ⚠ Warnings" in out
    assert "phase2_fallback" in out
    assert "timed out" in out


def test_critical_path_section_when_present():
    data = _minimal_solver_output()
    data["critical_path"] = ["T001", "T002"]
    data["critical_path_edges"] = [["T001", "T002"]]
    out = render(data, "feat")
    assert "## Critical Path" in out
    # Critical edge rendered with ==>, non-critical with -->.
    assert "T001 ==> T002" in out
    # Critical class defined and applied.
    assert "classDef critical" in out
    assert "class T001,T002 critical" in out
    # Gantt marks critical bars with Mermaid's `crit` tag.
    assert "T001 :crit," in out
    assert "T002 :crit," in out


def test_critical_arcs_drawn_even_when_not_in_parser_edges():
    """A critical arc induced by same-agent or file-mutex must still be drawn.

    If the solver's critical path goes through an arc that is NOT in
    `data["edges"]` (parser edges), the Mermaid DAG must still emit it
    with the `==>` arrow so the user can follow the chain visually.
    """
    data = _minimal_solver_output()
    data["edges"] = []  # no parser edges
    data["resource_edges"] = [["T001", "T002"]]
    data["critical_path"] = ["T001", "T002"]
    data["critical_path_edges"] = [["T001", "T002"]]
    out = render(data, "feat")
    assert "T001 ==> T002" in out


def test_image_prefix_embeds_png_references():
    data = _minimal_solver_output()
    out = render(data, "feat", image_prefix="images/feat")
    assert "![Gantt](images/feat-gantt.png)" in out
    assert "![DAG](images/feat-dag.png)" in out


def test_no_image_prefix_emits_no_png_references():
    out = render(_minimal_solver_output(), "feat")
    assert ".png" not in out


def test_non_critical_gantt_has_no_crit_marker():
    data = _minimal_solver_output()
    data["critical_path"] = ["T001"]  # T002 is not critical
    out = render(data, "feat")
    assert "T001 :crit," in out
    # Ensure the non-critical task's gantt line exists without the crit marker.
    assert any(
        line.lstrip().startswith("T002 :") and ":crit," not in line
        for line in out.splitlines()
    )


def test_no_critical_path_section_when_missing():
    data = _minimal_solver_output()
    out = render(data, "feat")
    assert "## Critical Path" not in out
    assert "T001 --> T002" in out


def test_render_gantt_uses_absolute_end():
    """Mermaid gantt with `dateFormat X` takes `start, end` (Unix seconds).

    The two tasks in the fixture run 0→5 and 5→10, so the emitted pairs
    must be `0, 5` and `5, 10`. Using `duration` here would produce
    `5, 5` (zero-length bar) and render a visually-broken chart.
    """
    out = render(_minimal_solver_output(), "feat")
    assert ", 0, 5" in out
    assert ", 5, 10" in out
    # Paranoia: zero-length / negative-length bars must never appear.
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped.startswith("T00"):
            continue
        # Parse "T00X :tag, start, end"
        parts = stripped.rsplit(",", 2)
        if len(parts) == 3:
            start = int(parts[1].strip())
            end = int(parts[2].strip())
            assert end > start, f"broken gantt bar: {stripped!r}"
