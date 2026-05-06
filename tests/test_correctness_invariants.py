"""Correctness invariants for the scheduler: horizon UB, replan determinism, status reporting, etc."""

from __future__ import annotations

import networkx as nx
import pytest
from ortools.sat.python import cp_model
from pydantic import ValidationError

from solver.config_schema import _MAX_TOKENS, TokenEstimate
from solver.model.types import Agent, Task
from solver.orchestration import runner
from solver.result import extract as _result_extract
from solver.scheduler import (
    list_schedule_heuristic,
    solve_from_json,
    solve_with_fixed,
)
from tests._helpers import TERMINAL_STATUSES, make_agent, make_solver_input, make_task

# ─────────────────────────────────────────────────────────────────────
# Skill-bottleneck horizon
# ─────────────────────────────────────────────────────────────────────


def test_skill_bottleneck_finds_optimum() -> None:
    """3 tasks share a rare skill held only by A0; OPT requires serial run on A0."""
    data = make_solver_input(
        tasks=[
            make_task(f"T00{i + 1}", required_skill="rare", estimated_tokens=1000)
            for i in range(3)
        ],
        agents=[
            make_agent("A0", skills=["rare"], kappa=3, context_budget=50_000),
            make_agent("A1", skills=["common"], kappa=10, context_budget=50_000),
        ],
        config={"time_limit": 5},
    )
    result = solve_from_json(data)
    assert result["status"] == "OPTIMAL", result.get("message", "")
    # Each task is 10 units (1000 tokens / token_unit=100); 3 serialised on A0.
    assert result["stats"]["makespan"] == 30
    agents = {a["task_id"]: a["agent_id"] for a in result["assignments"]}
    assert all(a == "A0" for a in agents.values())
    # Horizon must be at least the serial UB.
    assert result["stats"]["horizon"] >= 30


# ─────────────────────────────────────────────────────────────────────
# Replan duration pin
# ─────────────────────────────────────────────────────────────────────


def test_replan_pins_duration() -> None:
    """``fixed_assignments`` pins ``dur[i] == d_fixed`` so configuration drift
    cannot shift a frozen task off its prior duration.

    A "drifted" duration (different from what the current config would
    derive for the same task/agent) is the canonical replan-after-recalibration
    case: we still expect the replan to honour the supplied duration. The
    solver realigns the channel by treating the frozen pair as having
    ``p[i, a_fixed] = d_fixed``.
    """
    data = make_solver_input(
        tasks=[
            make_task("T001", file_paths=["a.py"], estimated_tokens=1000),
            make_task("T002", file_paths=["b.py"], estimated_tokens=500),
        ],
        agents=[make_agent("A0", speed_factor=1.0)],
        config={"time_limit": 5},
    )
    prior = solve_from_json(data)
    assert prior["status"] in TERMINAL_STATUSES
    prior_t1 = next(a for a in prior["assignments"] if a["task_id"] == "T001")
    prior_duration = prior_t1["duration"]
    base = {"agent_id": prior_t1["agent_id"], "start": prior_t1["start"]}

    # (a) Honoured prior duration: replan succeeds and preserves the value.
    result = solve_with_fixed(data, {"T001": {**base, "duration": prior_duration}})
    assert result["status"] in TERMINAL_STATUSES
    new_t1 = next(a for a in result["assignments"] if a["task_id"] == "T001")
    assert new_t1["duration"] == prior_duration

    # (b) Drifted duration (e.g. recalibration changed speed_factor between
    # runs). The frozen duration must be honoured — NOT INFEASIBLE — and
    # the original ``p[i, a]`` must NOT silently override the supplied value.
    drifted_duration = prior_duration + 1
    drifted = solve_with_fixed(
        data, {"T001": {**base, "duration": drifted_duration}}
    )
    assert drifted["status"] in TERMINAL_STATUSES
    drifted_t1 = next(a for a in drifted["assignments"] if a["task_id"] == "T001")
    assert drifted_t1["duration"] == drifted_duration


# ─────────────────────────────────────────────────────────────────────
# Top-level status downgrade
# ─────────────────────────────────────────────────────────────────────


def test_phase1_feasible_downgrades_top_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Phase 1 returns FEASIBLE, the top-level status must be FEASIBLE
    even if Phase 2 proves OPTIMAL on the load-balance subproblem.
    """
    real_run = runner._run_solver
    calls = {"n": 0}

    def fake_run(model, config, callback=None, **kwargs):
        calls["n"] += 1
        solver, status, elapsed = real_run(model, config, callback, **kwargs)
        if calls["n"] == 1 and status == cp_model.OPTIMAL:
            # Pretend Phase 1 didn't prove optimality. Solver still has a
            # valid solution (so values + hints work), only the status is
            # downgraded.
            return solver, cp_model.FEASIBLE, elapsed
        return solver, status, elapsed

    monkeypatch.setattr(runner, "_run_solver", fake_run)

    data = make_solver_input(
        tasks=[
            make_task("T001", file_paths=["a.py"]),
            make_task("T002", file_paths=["b.py"]),
        ],
        agents=[make_agent("A0")],
        config={"time_limit": 5},
    )
    result = solve_from_json(data)
    assert result["stats"]["phase1_status"] == "FEASIBLE"
    assert result["stats"]["phase2_status"] == "OPTIMAL"
    assert result["status"] == "FEASIBLE"


# ─────────────────────────────────────────────────────────────────────
# Heuristic feasibility filter
# ─────────────────────────────────────────────────────────────────────


def _t(i: int, *, skill: str = "X", tokens: int = 100) -> Task:
    return Task(
        id=f"T{i:03d}",
        phase="Setup",
        story_id=None,
        story_priority=99,
        parallel_flag=False,
        file_paths=[f"f{i}.py"],
        required_skill=skill,
        estimated_tokens=tokens,
        action_verb="implement",
        index=i,
    )


def _a(j: int, *, skills=("X",), kappa: int = 2, budget: int = 100_000) -> Agent:
    return Agent(
        id=f"A{j}",
        model="test",
        skills=list(skills),
        kappa=kappa,
        context_budget=budget,
        speed_factor=1.0,
        index=j,
    )


def test_heuristic_drops_infeasible_task() -> None:
    """When all eligible agents are κ-saturated, heuristic omits the task."""
    tasks = [_t(0), _t(1), _t(2)]
    agents = [_a(0, kappa=2)]
    compat = {0: [0], 1: [0], 2: [0]}
    p = {(i, 0): 1 for i in range(3)}
    min_dur = dict.fromkeys(range(3), 1)

    hints = list_schedule_heuristic(
        tasks, [], agents, compat, p, min_dur, file_conflicts={}
    )
    assert len(hints) == 2
    assert 0 in hints and 1 in hints
    assert 2 not in hints


# ─────────────────────────────────────────────────────────────────────
# Phase 2 makespan pin
# ─────────────────────────────────────────────────────────────────────


def test_phase2_pin_preserves_phase1_makespan() -> None:
    """Phase 2's ``makespan == ms_star`` pin must keep the Phase 1 optimum."""
    data = make_solver_input(
        tasks=[
            make_task(f"T00{i + 1}", file_paths=[f"f{i}.py"]) for i in range(4)
        ],
        agents=[make_agent("A0"), make_agent("A1")],
        config={"time_limit": 5},
    )
    result = solve_from_json(data)
    assert result["status"] in TERMINAL_STATUSES
    stats = result["stats"]
    assert stats["makespan"] == stats["makespan_phase1"]


# ─────────────────────────────────────────────────────────────────────
# Critical-path cycle defence
# ─────────────────────────────────────────────────────────────────────


def test_critical_path_cycle_handled_gracefully(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A cycle in the realised-schedule graph yields an empty critical path.

    Asserts the schedule itself survives (assignments non-empty) and the
    cycle is logged via ``logging.warning`` (the result envelope's
    WarningCollector path is reserved for user-facing solver warnings; the
    critical-path cycle is a defensive log).
    """
    def raise_unfeasible(_graph):
        raise nx.NetworkXUnfeasible("synthetic cycle")

    monkeypatch.setattr(
        _result_extract.nx, "lexicographical_topological_sort", raise_unfeasible
    )

    data = make_solver_input(
        tasks=[make_task("T001", file_paths=["a.py"])],
        agents=[make_agent("A0")],
        config={"time_limit": 5},
    )
    with caplog.at_level("WARNING", logger="solver.result.extract"):
        result = solve_from_json(data)
    assert result["status"] in TERMINAL_STATUSES
    assert result["critical_path"] == []
    assert result["critical_path_edges"] == []
    assert result["resource_edges"] == []
    # Schedule itself must still be present.
    assert len(result["assignments"]) == 1
    assert result["assignments"][0]["task_id"] == "T001"
    # Defensive cycle warning must be logged.
    assert any("cycle" in rec.getMessage().lower() for rec in caplog.records)


# ─────────────────────────────────────────────────────────────────────
# Per-task tokens schema cap
# ─────────────────────────────────────────────────────────────────────


def test_max_tokens_boundary_and_overflow() -> None:
    """Schema cap on per-task tokens is 1e8 to keep int64 headroom on cumulative scaled cost."""
    assert _MAX_TOKENS == 100_000_000
    # Boundary: 1e8 accepted.
    te = TokenEstimate(mean=int(1e8))
    assert te.mean == int(1e8)
    # Just-above-cap rejected.
    with pytest.raises(ValidationError):
        TokenEstimate(mean=int(1e8) + 1)
