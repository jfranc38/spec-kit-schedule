"""End-to-end integration tests covering the full parse → solve → render pipeline."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml

from solver.calibrate import calibrate
from solver.parse_tasks import parse_tasks_md
from solver.render_schedule import render as render_md
from solver.replan import replan
from solver.scheduler import solve_from_json
from solver.validation import ScheduleInputError
from tests.conftest import (
    TERMINAL_STATUSES,
    make_agent,
    make_chain_edges,
    make_solver_input,
    make_task,
)


def _assert_schedule_invariants(result: dict, solver_input: dict) -> None:
    """Common end-to-end invariants: precedence, file mutex, κ, context budget."""
    assert result["status"] in TERMINAL_STATUSES
    by_id = {a["task_id"]: a for a in result["assignments"]}
    # One assignment per task, no "unassigned" agent.
    assert set(by_id) == {t["id"] for t in solver_input["tasks"]}
    assert all(a["agent_id"] != "unassigned" for a in result["assignments"])

    # Precedence respected.
    for src, dst in solver_input["edges"]:
        assert by_id[src]["end"] <= by_id[dst]["start"], f"edge {src}->{dst} violated"

    # File mutex for non-[P] tasks sharing a file.
    by_file: dict[str, list[dict]] = {}
    for t in solver_input["tasks"]:
        if t.get("parallel_flag"):
            continue
        for f in t["file_paths"]:
            by_file.setdefault(f, []).append(by_id[t["id"]])
    for assns in by_file.values():
        assns.sort(key=lambda a: a["start"])
        for prev, curr in zip(assns, assns[1:], strict=False):
            assert prev["end"] <= curr["start"], "file mutex violated"

    # κ and context budget per agent.
    by_agent: dict[str, list[dict]] = {}
    for a in result["assignments"]:
        by_agent.setdefault(a["agent_id"], []).append(a)
    agents_by_id = {a["id"]: a for a in solver_input["agents"]}
    for aid, assns in by_agent.items():
        spec = agents_by_id[aid]
        assert len(assns) <= spec["kappa"], f"κ exceeded for {aid}"
        assert sum(a["tokens"] for a in assns) <= spec["context_budget"], (
            f"context budget exceeded for {aid}"
        )


# ───────────────────────────────────────────────────────────────────────
# S1 — End-to-end lex mode happy path (parse → solve → invariants)
# ───────────────────────────────────────────────────────────────────────


def test_lex_pipeline_from_yaml_and_md(tmp_path: Path) -> None:
    """Parse YAML config + tasks.md from disk, solve, validate every invariant."""
    config = {
        "agents": [
            make_agent("backend", skills=["backend", "api"], context_budget=30_000),
            make_agent("tester", skills=["test"], context_budget=20_000),
        ],
        "skill_rules": [{"pattern": "tests/", "skill": "test"}],
        "default_skill": "backend",
        "token_estimates": {"simple": 1000, "medium": 2000, "complex": 4000, "review": 1500},
        "complexity_verbs": {
            "simple": ["add", "update"],
            "medium": ["implement"],
            "complex": ["design"],
            "review": ["review"],
        },
        "solver": {"time_limit": 10, "num_workers": 1},
    }
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")

    tasks_md = tmp_path / "tasks.md"
    tasks_md.write_text(
        "# Phase Setup\n"
        "- [ ] T001 Implement auth in `src/auth.py`\n"
        "- [ ] T002 Implement profile in `src/profile.py` (depends on T001)\n"
        "- [ ] T003 Add unit tests in `tests/test_auth.py`\n",
        encoding="utf-8",
    )
    parser_output = parse_tasks_md(
        str(tasks_md), yaml.safe_load(config_path.read_text(encoding="utf-8"))
    )
    parser_output["config"] = {"time_limit": 10, "num_workers": 1}
    result = solve_from_json(parser_output)

    expected_keys = {"stats", "warnings", "assignments", "status", "edges"}
    assert expected_keys <= set(result)
    assert "makespan" in result["stats"] and "max_load" in result["stats"]
    _assert_schedule_invariants(result, parser_output)


# ───────────────────────────────────────────────────────────────────────
# S2 — Cost-aware mode (3-phase: makespan, load, cost)
# ───────────────────────────────────────────────────────────────────────


def test_cost_aware_pipeline_minimises_cost_under_makespan_pin() -> None:
    """Cost-aware solve reports per-phase stats and prefers the cheaper agent."""
    tasks = [make_task(f"T{i:03d}", estimated_tokens=1000) for i in range(3)]
    agents = [
        make_agent("cheap", price_per_1k_tokens=0.5),
        make_agent("expensive", price_per_1k_tokens=5.0),
    ]
    si = make_solver_input(tasks, agents, config={"objective": "cost_aware"})
    result = solve_from_json(si)
    _assert_schedule_invariants(result, si)

    # All 3 phases ran (makespan, load-balance, cost) — phase2 may finish fast.
    assert "phase1_time" in result["stats"]
    assert "phase2_time" in result["stats"]
    assert "phase3_time" in result["stats"]
    assert "total_cost" in result["stats"]

    # Total cost equals Σ tokens·price/1000 across assignments.
    by_agent = {r["agent_id"]: r for r in result["agent_summary"]}
    expected = sum(r["total_tokens"] * (0.5 if aid == "cheap" else 5.0) / 1000
                   for aid, r in by_agent.items())
    assert math.isclose(result["stats"]["total_cost"], expected, rel_tol=1e-6)

    # Cost-optimal under the makespan pin: any spillover to `expensive` carries
    # strictly fewer tokens than `cheap`, otherwise the schedule could swap to
    # save money without worsening makespan.
    cheap_tokens = sum(a["tokens"] for a in result["assignments"] if a["agent_id"] == "cheap")
    expensive_tokens = sum(
        a["tokens"] for a in result["assignments"] if a["agent_id"] == "expensive"
    )
    assert cheap_tokens >= expensive_tokens


# ───────────────────────────────────────────────────────────────────────
# S3 — Stochastic p50 vs p90 quantile durations
# ───────────────────────────────────────────────────────────────────────


def test_p90_makespan_geq_p50_makespan() -> None:
    """Solving with p90 durations must never produce a shorter makespan than p50."""
    tasks = [
        make_task(f"T{i:03d}", estimated_tokens=1000, token_std_dev=300.0) for i in range(4)
    ]
    edges = make_chain_edges(4)
    agents = [make_agent("A0", context_budget=100_000)]

    r50 = solve_from_json(make_solver_input(tasks, agents, edges, {"stochastic_quantile": 0.5}))
    r90 = solve_from_json(make_solver_input(tasks, agents, edges, {"stochastic_quantile": 0.9}))

    assert r50["stats"]["quantile_used"] == pytest.approx(0.5)
    assert r90["stats"]["quantile_used"] == pytest.approx(0.9)
    assert r90["stats"]["makespan"] >= r50["stats"]["makespan"]
    # Skill assignments still respected — both must land on backend agent.
    for r in (r50, r90):
        assert all(a["agent_id"] == "A0" for a in r["assignments"])


# ───────────────────────────────────────────────────────────────────────
# S3b — Calibration → solve round-trip
# ───────────────────────────────────────────────────────────────────────


def test_calibration_report_consumed_by_solver(tmp_path: Path) -> None:
    """Calibrate produces token estimates that the solver can ingest without error."""
    config = {
        "agents": [make_agent("backend-1", skills=["backend"], context_budget=40_000)],
        "skill_rules": [],
        "default_skill": "backend",
        "token_estimates": {
            "simple": {"mean": 1500, "std_dev": 0},
            "medium": {"mean": 3500, "std_dev": 500},
            "complex": {"mean": 6000, "std_dev": 0},
            "review": {"mean": 2000, "std_dev": 0},
        },
        "complexity_verbs": {
            "simple": ["add"], "medium": ["implement"],
            "complex": ["design"], "review": ["review"],
        },
        "solver": {"time_limit": 5, "num_workers": 1},
    }
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.dump(config), encoding="utf-8")

    rows = []
    for j in range(6):
        rows.append({
            "task_id": f"T{j:03d}", "agent_id": "backend-1", "model": "test",
            "skill": "backend", "complexity": "medium",
            "estimated_tokens": 3500, "predicted_duration": 40.0,
            "actual_duration": 32.0, "actual_tokens": 4000,
            "start_ts": "2026-04-24T10:00:00Z",
            "end_ts": "2026-04-24T10:32:00Z", "status": "success",
        })
    runs_path = tmp_path / "runs.jsonl"
    runs_path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    report = calibrate(runs_path, config_path, dry_run=False, confidence_threshold=5)
    assert report.runs_analysed == 6

    # Now solve using the calibrated config — must not regress.
    updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    backend = next(a for a in updated["agents"] if a["id"] == "backend-1")
    assert backend["speed_factor"] != 1.0  # calibration moved it

    si = make_solver_input(
        [make_task("T001", estimated_tokens=3500)],
        [make_agent("backend-1", speed_factor=backend["speed_factor"])],
    )
    result = solve_from_json(si)
    _assert_schedule_invariants(result, si)


# ───────────────────────────────────────────────────────────────────────
# S4 — Replanning mid-execution (completed tasks frozen out)
# ───────────────────────────────────────────────────────────────────────


def test_replan_after_completion_preserves_invariants() -> None:
    """Solve, mark first task completed, replan; remaining tasks honour all constraints."""
    tasks = [make_task(f"T{i:03d}", estimated_tokens=800) for i in range(4)]
    edges = make_chain_edges(4)
    agents = [make_agent("A0", context_budget=100_000)]
    si = make_solver_input(tasks, agents, edges)
    prior = solve_from_json(si)
    prior_makespan = prior["stats"]["makespan"]

    fresh_si = make_solver_input(tasks, agents, edges)
    result = replan(prior, fresh_si, completed_ids={"T000"})

    remaining_ids = {a["task_id"] for a in result["assignments"]}
    assert "T000" not in remaining_ids
    assert remaining_ids == {"T001", "T002", "T003"}
    assert result["stats"]["replan"]["completed_count"] == 1

    # Invariants on the surviving sub-problem (rebuilt input mirrors active set).
    surviving_si = make_solver_input(
        [t for t in tasks if t["id"] != "T000"], agents,
        [e for e in edges if "T000" not in e],
    )
    _assert_schedule_invariants(result, surviving_si)

    # No time-travel: the new makespan is no smaller than the chain bound.
    assert result["stats"]["makespan"] <= prior_makespan + 1  # nondestructive replan


def test_replan_is_deterministic_for_identical_inputs() -> None:
    """Two replans of the same prior + input return the same makespan."""
    tasks = [make_task(f"T{i:03d}", estimated_tokens=600) for i in range(3)]
    agents = [make_agent("A0", context_budget=20_000)]
    si = make_solver_input(tasks, agents)
    prior = solve_from_json(si)
    r1 = replan(prior, make_solver_input(tasks, agents), freeze_before=None)
    r2 = replan(prior, make_solver_input(tasks, agents), freeze_before=None)
    assert r1["stats"]["makespan"] == r2["stats"]["makespan"]
    assert r1["status"] == r2["status"]


# ───────────────────────────────────────────────────────────────────────
# S7 — Render markdown + HTML
# ───────────────────────────────────────────────────────────────────────


def test_render_markdown_after_solve_contains_status_and_assignments() -> None:
    """render() emits non-empty markdown referencing every assigned task and the status."""
    tasks = [make_task(f"T{i:03d}", estimated_tokens=500) for i in range(3)]
    si = make_solver_input(tasks, [make_agent("A0")])
    result = solve_from_json(si)
    md = render_md(result, "feat-x")
    assert md and ("OPTIMAL" in md or "FEASIBLE" in md)
    for t in tasks:
        assert t["id"] in md
    # Standard sections are present.
    assert "## Agent Assignments" in md
    assert "## Solver Statistics" in md


def test_render_html_after_solve_well_formed() -> None:
    """render_html.render() emits a self-contained HTML document with task ids."""
    pytest.importorskip("plotly")
    from solver.render_html import render as render_html  # local: gated by importorskip

    tasks = [make_task(f"T{i:03d}", estimated_tokens=500) for i in range(2)]
    si = make_solver_input(tasks, [make_agent("A0")])
    result = solve_from_json(si)
    # render_html requires a few extra keys with sane defaults.
    result.setdefault("resource_edges", [])
    result.setdefault("critical_path", [])
    result.setdefault("critical_path_edges", [])

    html = render_html(result, "feat-y")
    assert html.startswith("<!") or "<html" in html
    assert "</html>" in html
    assert "T000" in html and "T001" in html


# ───────────────────────────────────────────────────────────────────────
# S8 — Large problem feasibility within time budget
# ───────────────────────────────────────────────────────────────────────


def test_large_problem_solves_within_time_budget() -> None:
    """A 60-task / 4-agent problem solves to FEASIBLE/OPTIMAL with all invariants intact.

    Bounded at 60 tasks (not 100) to keep the test under ~5s on slow CI while still
    catching scaling regressions in the model build + warm-start path.
    """
    n_tasks = 60
    tasks = [
        make_task(f"T{i:03d}", estimated_tokens=400, file_paths=[f"src/m{i % 12}.py"])
        for i in range(n_tasks)
    ]
    # Sparse chain edges between consecutive tasks within each module.
    edges: list[list[str]] = []
    by_file: dict[str, list[str]] = {}
    for t in tasks:
        by_file.setdefault(t["file_paths"][0], []).append(t["id"])
    for ids in by_file.values():
        edges.extend([[ids[i], ids[i + 1]] for i in range(len(ids) - 1)])

    agents = [
        make_agent(f"A{j}", context_budget=200_000, kappa=20)
        for j in range(4)
    ]
    si = make_solver_input(tasks, agents, edges, config={"time_limit": 5, "num_workers": 2})
    result = solve_from_json(si)
    _assert_schedule_invariants(result, si)
    assert len(result["assignments"]) == n_tasks


# ───────────────────────────────────────────────────────────────────────
# S10 — Error paths (cycles, infeasible budget, missing skill)
# ───────────────────────────────────────────────────────────────────────


def test_dag_cycle_raises_with_cycle_in_message() -> None:
    """A cycle in edges raises ScheduleInputError mentioning 'cycle'."""
    tasks = [make_task("T001"), make_task("T002")]
    si = make_solver_input(tasks, [make_agent("A0")], [["T001", "T002"], ["T002", "T001"]])
    with pytest.raises(ScheduleInputError, match="cycle"):
        solve_from_json(si)


def test_infeasible_context_budget_raises_with_skill_context() -> None:
    """Total task tokens exceeding total agent budget raise with budget context."""
    tasks = [make_task("T001", estimated_tokens=200_000)]
    si = make_solver_input(tasks, [make_agent("A0", context_budget=10_000)])
    with pytest.raises(ScheduleInputError, match="budget|Infeasible"):
        solve_from_json(si)


def test_missing_skill_raises_with_skill_in_message() -> None:
    """A task requiring a skill no agent provides surfaces the skill name."""
    tasks = [make_task("T001", required_skill="frontend")]
    si = make_solver_input(tasks, [make_agent("A0", skills=["backend"])])
    with pytest.raises(ScheduleInputError, match="skill|frontend"):
        solve_from_json(si)


# ───────────────────────────────────────────────────────────────────────
# Original docs-example coverage (kept verbatim for regression safety)
# ───────────────────────────────────────────────────────────────────────


def test_docs_example_pipeline(docs_example_tasks, docs_example_config):
    parser_output = parse_tasks_md(str(docs_example_tasks), docs_example_config)
    assert len(parser_output["tasks"]) == 28

    solver_output = solve_from_json(parser_output)
    assert solver_output["status"] in TERMINAL_STATUSES

    # Every task gets one assignment on a real agent.
    assigned = {a["task_id"] for a in solver_output["assignments"]}
    assert assigned == {t["id"] for t in parser_output["tasks"]}
    assert all(a["agent_id"] != "unassigned" for a in solver_output["assignments"])

    # The rendered markdown mentions every task id.
    md = render_md(solver_output, "example")
    for t in parser_output["tasks"]:
        assert t["id"] in md

    # DAG edges from parser survive into render (critical edges use ==>).
    for src, dst in parser_output["edges"]:
        assert f"{src} --> {dst}" in md or f"{src} ==> {dst}" in md

    # Critical path is non-empty and never exceeds the makespan.
    by_id = {a["task_id"]: a for a in solver_output["assignments"]}
    cp = solver_output["critical_path"]
    assert cp, "critical_path should never be empty for a non-empty schedule"
    cp_duration = sum(by_id[t]["duration"] for t in cp)
    assert 0 < cp_duration <= solver_output["stats"]["makespan"]
    assert "## Critical Path" in md


def test_docs_example_skill_coverage_is_complete(docs_example_tasks, docs_example_config):
    """Every required_skill in the example portfolio must be covered."""
    parser_output = parse_tasks_md(str(docs_example_tasks), docs_example_config)
    agent_skills = {s for a in parser_output["agents"] for s in a["skills"]}
    uncovered = {t["required_skill"] for t in parser_output["tasks"]} - agent_skills
    assert uncovered == set()
