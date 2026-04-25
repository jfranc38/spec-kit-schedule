"""Tests for the benchmark harness."""

from __future__ import annotations

import pytest

from benchmarks.greedy_baseline import greedy_solve
from benchmarks.problems import REAL_WORLD_SHAPES, SIZES, generate

# ---------------------------------------------------------------------------
# Problem generator tests
# ---------------------------------------------------------------------------

def test_generate_tiny_returns_correct_shape():
    data = generate(size="tiny")
    assert len(data["tasks"]) == SIZES["tiny"]["n_tasks"]
    assert len(data["agents"]) == SIZES["tiny"]["n_agents"]


def test_generate_small():
    data = generate(size="small")
    assert len(data["tasks"]) == SIZES["small"]["n_tasks"]


def test_generate_is_deterministic():
    d1 = generate(size="tiny", seed=42)
    d2 = generate(size="tiny", seed=42)
    assert d1["tasks"] == d2["tasks"]
    assert d1["edges"] == d2["edges"]
    assert d1["agents"] == d2["agents"]


def test_generate_different_seeds_differ():
    d1 = generate(size="tiny", seed=1)
    d2 = generate(size="tiny", seed=2)
    # They should differ in at least some respect
    assert d1["tasks"] != d2["tasks"] or d1["edges"] != d2["edges"]


def test_generate_edges_are_acyclic():
    """All generated edge lists must form a DAG."""
    from benchmarks.greedy_baseline import _topological_sort

    data = generate(size="small")
    task_ids = [t["id"] for t in data["tasks"]]
    # Topological sort raises ValueError on cycle; should not raise
    order = _topological_sort(task_ids, data["edges"])
    assert len(order) == len(task_ids)


def test_generate_all_sizes():
    for size in SIZES:
        data = generate(size=size)
        assert len(data["tasks"]) > 0
        assert len(data["agents"]) > 0


def test_generate_real_world_shapes():
    for shape in REAL_WORLD_SHAPES:
        data = generate(size=shape)
        assert len(data["tasks"]) > 0
        assert len(data["agents"]) > 0


def test_generate_unknown_size_raises():
    with pytest.raises(ValueError, match="Unknown size"):
        generate(size="gigantic")


def test_generate_task_ids_unique():
    data = generate(size="medium")
    ids = [t["id"] for t in data["tasks"]]
    assert len(ids) == len(set(ids))


def test_generate_tasks_have_required_fields():
    data = generate(size="tiny")
    for task in data["tasks"]:
        assert "id" in task
        assert "required_skill" in task
        assert "estimated_tokens" in task
        assert task["estimated_tokens"] > 0


def test_generate_agents_have_required_fields():
    data = generate(size="tiny")
    for agent in data["agents"]:
        assert "id" in agent
        assert "skills" in agent
        assert "kappa" in agent
        assert "context_budget" in agent


# ---------------------------------------------------------------------------
# Greedy baseline tests
# ---------------------------------------------------------------------------

def test_greedy_returns_feasible():
    data = generate(size="tiny")
    result = greedy_solve(data)
    assert result["status"] in ("GREEDY_FEASIBLE", "GREEDY_INFEASIBLE")


def test_greedy_assignments_cover_all_tasks():
    data = generate(size="tiny")
    result = greedy_solve(data)
    task_ids = {t["id"] for t in data["tasks"]}
    assigned_ids = {a["task_id"] for a in result["assignments"]}
    assert task_ids == assigned_ids


def test_greedy_makespan_positive():
    data = generate(size="tiny")
    result = greedy_solve(data)
    assert result["stats"]["makespan"] > 0


def test_greedy_respects_precedence():
    """No task should start before its predecessors finish."""
    data = generate(size="small")
    result = greedy_solve(data)
    end_by_id = {a["task_id"]: a["end"] for a in result["assignments"]}
    start_by_id = {a["task_id"]: a["start"] for a in result["assignments"]}
    for src, dst in data["edges"]:
        assert end_by_id.get(src, 0) <= start_by_id.get(dst, 0), (
            f"Precedence violated: {src} ends at {end_by_id.get(src)}, "
            f"{dst} starts at {start_by_id.get(dst)}"
        )


def test_greedy_output_schema():
    """Result must have all required top-level keys."""
    data = generate(size="tiny")
    result = greedy_solve(data)
    for key in (
        "status", "assignments", "waves", "agent_summary",
        "critical_path", "critical_path_edges", "resource_edges",
        "edges", "stats", "warnings",
    ):
        assert key in result, f"Missing key: {key}"


def test_greedy_stats_keys():
    data = generate(size="tiny")
    result = greedy_solve(data)
    stats = result["stats"]
    for key in ("makespan", "max_load", "min_load", "total_tasks", "total_agents", "total_waves"):
        assert key in stats, f"Missing stats key: {key}"


def test_greedy_empty_data():
    result = greedy_solve({"tasks": [], "agents": [], "edges": [], "config": {}})
    assert result["status"] == "GREEDY_INFEASIBLE"


# ---------------------------------------------------------------------------
# Smoke test: CP-SAT makespan <= greedy makespan on tiny problem
# ---------------------------------------------------------------------------

def test_cpsat_beats_or_matches_greedy_on_tiny():
    """On a tiny problem, CP-SAT should match or beat the greedy makespan."""
    data = generate(size="tiny", seed=42)
    greedy_result = greedy_solve(data)
    greedy_ms = greedy_result["stats"]["makespan"]

    from solver.scheduler import solve_from_json

    cfg = {**data.get("config", {}), "num_workers": 1, "time_limit": 10}
    cpsat_result = solve_from_json({**data, "config": cfg})
    cpsat_ms = cpsat_result["stats"]["makespan"]

    assert cpsat_ms <= greedy_ms, (
        f"CP-SAT makespan ({cpsat_ms}) > greedy makespan ({greedy_ms})"
    )
