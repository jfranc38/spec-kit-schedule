"""Tests for solver.render_html."""

from __future__ import annotations

import pytest

pytest.importorskip("plotly")

from solver.render_html import render  # noqa: E402


def _minimal_data() -> dict:
    return {
        "status": "OPTIMAL",
        "stats": {
            "status": "OPTIMAL",
            "makespan": 10,
            "max_load": 10,
            "min_load": 5,
            "total_tasks": 2,
            "total_agents": 1,
            "total_waves": 2,
            "horizon": 20,
            "phase1_time": 0.1,
            "phase1_status": "OPTIMAL",
            "phase2_time": 0.1,
            "phase2_status": "OPTIMAL",
        },
        "assignments": [
            {
                "task_id": "T001",
                "task_index": 0,
                "agent_id": "backend",
                "agent_index": 0,
                "start": 0,
                "end": 5,
                "duration": 5,
                "phase": "Setup",
                "story_id": None,
                "story_priority": 99,
                "file_paths": ["src/a.py"],
                "tokens": 500,
                "required_skill": "backend",
            },
            {
                "task_id": "T002",
                "task_index": 1,
                "agent_id": "backend",
                "agent_index": 0,
                "start": 5,
                "end": 10,
                "duration": 5,
                "phase": "Setup",
                "story_id": None,
                "story_priority": 99,
                "file_paths": ["src/b.py"],
                "tokens": 500,
                "required_skill": "backend",
            },
        ],
        "waves": [
            {"wave": 1, "start_time": 0, "tasks": []},
            {"wave": 2, "start_time": 5, "tasks": []},
        ],
        "agent_summary": [
            {
                "agent_id": "backend",
                "model": "test-model",
                "task_count": 2,
                "total_tokens": 1000,
                "budget_utilization": 10.0,
                "total_load": 10,
                "kappa_utilization": 20.0,
                "tasks": ["T001", "T002"],
            }
        ],
        "edges": [["T001", "T002"]],
        "resource_edges": [],
        "critical_path": [],
        "critical_path_edges": [],
        "warnings": [],
    }


def test_render_returns_nonempty_string():
    result = render(_minimal_data(), "x")
    assert isinstance(result, str)
    assert len(result) > 0


def test_render_contains_html_tags():
    result = render(_minimal_data(), "x")
    assert "<html" in result
    assert "</html>" in result


def test_render_references_plotly():
    result = render(_minimal_data(), "x")
    # Either CDN reference or inline plotly
    assert "plotly" in result.lower()


def test_render_contains_newplot():
    result = render(_minimal_data(), "x")
    assert "Plotly.newPlot" in result or "plotly" in result.lower()


def test_render_contains_feature_name():
    result = render(_minimal_data(), "my-feature")
    assert "my-feature" in result


def test_render_contains_makespan():
    result = render(_minimal_data(), "feat")
    assert "10" in result


def test_render_contains_status():
    result = render(_minimal_data(), "feat")
    assert "OPTIMAL" in result


def test_render_with_critical_path():
    data = _minimal_data()
    data["critical_path"] = ["T001", "T002"]
    data["critical_path_edges"] = [["T001", "T002"]]
    result = render(data, "feat")
    assert "T001" in result
    assert "T002" in result
    assert "Critical Path" in result


def test_render_critical_task_ids_in_html():
    data = _minimal_data()
    data["critical_path"] = ["T001", "T002"]
    data["critical_path_edges"] = [["T001", "T002"]]
    result = render(data, "feat")
    assert "T001" in result
    assert "T002" in result


def test_render_with_warnings():
    data = _minimal_data()
    data["warnings"] = [
        {"code": "phase2_fallback", "message": "timed out", "context": {"k": 1}}
    ]
    result = render(data, "feat")
    assert "Warnings" in result
    assert "phase2_fallback" in result
    assert "timed out" in result


def test_render_no_warnings_section_when_empty():
    result = render(_minimal_data(), "feat")
    assert "Warnings" not in result


def test_render_agent_section():
    result = render(_minimal_data(), "feat")
    assert "Agent Assignments" in result
    assert "backend" in result


def test_render_waves_section():
    data = _minimal_data()
    data["waves"][0]["tasks"] = [data["assignments"][0]]
    result = render(data, "feat")
    assert "Execution Waves" in result
    assert "Wave 1" in result


def test_render_stats_section():
    result = render(_minimal_data(), "feat")
    assert "Solver Statistics" in result
    assert "Makespan" in result


def test_render_gantt_section_present():
    result = render(_minimal_data(), "feat")
    assert "Gantt" in result


def test_render_dag_section_present():
    result = render(_minimal_data(), "feat")
    assert "DAG" in result


def test_render_image_prefix_ignored():
    """image_prefix is accepted for API parity but has no effect in HTML."""
    r1 = render(_minimal_data(), "feat")
    r2 = render(_minimal_data(), "feat", image_prefix="images/test")
    assert r1 == r2


def test_render_two_agents():
    data = _minimal_data()
    data["assignments"].append({
        "task_id": "T003",
        "task_index": 2,
        "agent_id": "frontend",
        "agent_index": 1,
        "start": 0,
        "end": 4,
        "duration": 4,
        "phase": "Setup",
        "story_id": None,
        "story_priority": 99,
        "file_paths": ["src/c.py"],
        "tokens": 400,
        "required_skill": "frontend",
    })
    data["agent_summary"].append({
        "agent_id": "frontend",
        "model": "test",
        "task_count": 1,
        "total_tokens": 400,
        "budget_utilization": 5.0,
        "total_load": 4,
        "kappa_utilization": 10.0,
        "tasks": ["T003"],
    })
    result = render(data, "feat")
    assert "backend" in result
    assert "frontend" in result


def test_render_resource_edges_accepted():
    data = _minimal_data()
    data["resource_edges"] = [["T001", "T002"]]
    result = render(data, "feat")
    assert "<html" in result


def test_render_empty_assignments():
    data = _minimal_data()
    data["assignments"] = []
    data["agent_summary"] = []
    data["waves"] = []
    result = render(data, "feat")
    assert "<html" in result
    assert "feat" in result
