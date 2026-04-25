"""End-to-end integration tests using the docs example."""

from __future__ import annotations

from solver.parse_tasks import parse_tasks_md
from solver.render_schedule import render
from solver.scheduler import solve_from_json


def test_docs_example_pipeline(docs_example_tasks, docs_example_config):
    parser_output = parse_tasks_md(str(docs_example_tasks), docs_example_config)
    assert len(parser_output["tasks"]) == 28

    solver_output = solve_from_json(parser_output)
    assert solver_output["status"] in {"OPTIMAL", "FEASIBLE"}

    # Every task gets one assignment on a real agent.
    assigned = {a["task_id"] for a in solver_output["assignments"]}
    assert assigned == {t["id"] for t in parser_output["tasks"]}
    assert all(a["agent_id"] != "unassigned" for a in solver_output["assignments"])

    # The rendered markdown mentions every task id.
    md = render(solver_output, "example")
    for t in parser_output["tasks"]:
        assert t["id"] in md

    # DAG edges from parser survive into render (critical edges use ==>).
    for src, dst in parser_output["edges"]:
        assert f"{src} --> {dst}" in md or f"{src} ==> {dst}" in md

    # Critical path is non-empty and never exceeds the makespan; Phase 2
    # load balancing can insert idle gaps, so equality only holds for
    # slack-free single-phase schedules (covered in unit tests).
    by_id = {a["task_id"]: a for a in solver_output["assignments"]}
    cp = solver_output["critical_path"]
    assert cp, "critical_path should never be empty for a non-empty schedule"
    cp_duration = sum(by_id[t]["duration"] for t in cp)
    assert 0 < cp_duration <= solver_output["stats"]["makespan"]
    assert "## Critical Path" in md


def test_docs_example_skill_coverage_is_complete(docs_example_tasks, docs_example_config):
    """Every required_skill in the example portfolio must be covered.

    Guards against a future example config update that silently removes a
    skill an agent needs.
    """
    parser_output = parse_tasks_md(str(docs_example_tasks), docs_example_config)
    agent_skills = {s for a in parser_output["agents"] for s in a["skills"]}
    uncovered = {t["required_skill"] for t in parser_output["tasks"]} - agent_skills
    assert uncovered == set()
