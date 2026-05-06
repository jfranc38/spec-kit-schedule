"""Edge-case invariant tests pinning rarely-exercised branches.

Covers:
- ``replan_fixed_missing`` — bad indices into the internal ``solve``
- faster-agent replan honours the prior duration
- empty-residual replan (all tasks completed)
- library-bypass overflow on cost-aware int64 headroom
- ``story_priority`` drives ordering between independent tasks
- κ enforcement at the boundary (n_tasks=k+1, n_agents=1, kappa=k)
"""

from __future__ import annotations

import pytest

from solver.model.build import _COST_INT64_HEADROOM
from solver.replan import replan
from solver.scheduler import (
    _parse_input,
    build_file_conflict_groups,
    compute_compatible_agents,
    compute_durations,
    compute_min_durations,
    solve,
    solve_from_json,
    solve_with_fixed,
)
from solver.validation import ScheduleInputError
from solver.warnings_collector import WarningCollector
from tests._helpers import (
    TERMINAL_STATUSES,
    make_agent,
    make_chain_problem,
    make_solver_input,
    make_task,
)

# ── replan_fixed_missing branch ──────────────────────────────────────────


def _prepared_minimum() -> tuple:
    """Build (tasks, edges, agents, compat, p, min_dur, file_conflicts, config)."""
    data = make_solver_input(
        tasks=[make_task("T001", file_paths=["a.py"])],
        agents=[make_agent("A0")],
    )
    tasks, edges, agents, config = _parse_input(data)
    compat = compute_compatible_agents(tasks, agents)
    p = compute_durations(tasks, agents, config.token_unit, config.stochastic_quantile)
    min_dur = compute_min_durations(len(tasks), compat, p)
    file_conflicts = build_file_conflict_groups(tasks)
    return tasks, edges, agents, compat, p, min_dur, file_conflicts, config


def test_solve_replan_fixed_missing_bad_task_index():
    tasks, edges, agents, compat, p, min_dur, file_conflicts, config = _prepared_minimum()
    bad = {99: (0, 0, 1)}  # task index 99 ≥ len(tasks)
    with pytest.raises(ScheduleInputError):
        solve(
            tasks,
            edges,
            agents,
            compat,
            p,
            min_dur,
            file_conflicts,
            config,
            WarningCollector(),
            fixed_constraints=bad,
        )


def test_solve_replan_fixed_missing_bad_agent_index():
    tasks, edges, agents, compat, p, min_dur, file_conflicts, config = _prepared_minimum()
    bad = {0: (99, 0, 1)}  # agent index 99 ≥ len(agents)
    with pytest.raises(ScheduleInputError):
        solve(
            tasks,
            edges,
            agents,
            compat,
            p,
            min_dur,
            file_conflicts,
            config,
            WarningCollector(),
            fixed_constraints=bad,
        )


# ── Faster-agent replan ──────────────────────────────────────────────────


def test_faster_agent_replan_honours_frozen_duration():
    """Speed-factor change between solve and replan must NOT alter dur."""
    si = make_solver_input(
        tasks=[
            make_task("T001", file_paths=["a.py"], estimated_tokens=1_000),
            make_task("T002", file_paths=["b.py"], estimated_tokens=1_000),
        ],
        agents=[make_agent("A0", speed_factor=1.0)],
        config={"time_limit": 5},
    )
    prior = solve_from_json(si)
    assert prior["status"] in TERMINAL_STATUSES
    prior_t1 = next(a for a in prior["assignments"] if a["task_id"] == "T001")
    prior_dur = prior_t1["duration"]

    # Bump speed; duration would shrink if the channel-vs-pin contradiction
    # weren't resolved. With B1 fix p[i,a] is realigned to d_fixed, so the
    # frozen task keeps prior_dur exactly.
    si["agents"][0]["speed_factor"] = 2.0
    fixed = {
        "T001": {
            "agent_id": prior_t1["agent_id"],
            "start": prior_t1["start"],
            "duration": prior_dur,
        }
    }
    result = solve_with_fixed(si, fixed)
    assert result["status"] in TERMINAL_STATUSES
    new_t1 = next(a for a in result["assignments"] if a["task_id"] == "T001")
    assert new_t1["duration"] == prior_dur


# ── Empty residual replan ────────────────────────────────────────────────


def test_empty_residual_replan_terminates_cleanly():
    """All-completed replan must return cleanly, not crash on empty model."""
    si = make_chain_problem(n_tasks=3, n_agents=1)
    prior = solve_from_json(si)
    assert prior["status"] in TERMINAL_STATUSES
    completed = {a["task_id"] for a in prior["assignments"]}
    try:
        result = replan(prior, si, completed_ids=completed)
    except ScheduleInputError:
        # Documented exit: the validator may raise on empty tasks.
        return
    # Otherwise we expect terminal status with empty assignments.
    assert result["status"] in TERMINAL_STATUSES | {"INFEASIBLE", "UNKNOWN"}
    assert result["assignments"] == []


# ── Library-bypass overflow ──────────────────────────────────────────────


def test_library_bypass_cost_overflow_raises():
    """Schema is bypassed by calling ``_add_cost_variable`` directly with
    Task/Agent objects whose product exceeds ``_COST_INT64_HEADROOM``.

    ``solve_from_json`` runs Pydantic validation that caps
    ``price_per_1k_tokens``; library callers using the internal model
    builder skip that, so the runtime guard must still fire.
    """
    from ortools.sat.python import cp_model

    from solver.model.build import _add_cost_variable
    from solver.model.types import Agent, Task

    # cost_ia = tokens * price / 1000 * _COST_SCALE. With tokens=1e8 and
    # price=1e10, cost_ia ≈ 1e19, well above 2**62 ≈ 4.61e18.
    tasks = [
        Task(
            id="T001",
            phase="Setup",
            story_id=None,
            story_priority=1,
            parallel_flag=False,
            file_paths=["a.py"],
            required_skill="backend",
            estimated_tokens=100_000_000,
            action_verb="implement",
            index=0,
        ),
    ]
    agents = [
        Agent(
            id="A0",
            model="test",
            skills=["backend"],
            kappa=10,
            context_budget=10**12,
            speed_factor=1.0,
            price_per_1k_tokens=1e10,
            index=0,
        ),
    ]
    model = cp_model.CpModel()
    # Stand up the minimum vars _add_cost_variable references.
    from solver.model.build import _ModelVars
    x_var = model.new_bool_var("x_0_0")
    model.add(x_var == 1)
    vars_ = _ModelVars(
        start={0: model.new_int_var(0, 1, "s_0")},
        end={0: model.new_int_var(0, 1, "e_0")},
        dur={0: model.new_int_var(1, 1, "d_0")},
        x={(0, 0): x_var},
        master_iv={},
    )
    with pytest.raises(ScheduleInputError, match="int64"):
        _add_cost_variable(model, vars_, tasks, agents)


def test_cost_int64_headroom_constant_is_below_int64_max():
    """Sanity: the constant we guard against must comfortably under int64 max."""
    assert _COST_INT64_HEADROOM == 2**62
    assert _COST_INT64_HEADROOM < 2**63  # int64 signed limit


# ── Story priority drives ordering ───────────────────────────────────────


def test_story_priority_orders_independent_tasks():
    """Two independent tasks: lower priority value must start no later."""
    data = make_solver_input(
        tasks=[
            make_task("T001", file_paths=["a.py"], story_priority=2),
            make_task("T002", file_paths=["b.py"], story_priority=1),
        ],
        agents=[make_agent("A0", kappa=2)],
        config={"time_limit": 5},
    )
    result = solve_from_json(data)
    assert result["status"] in TERMINAL_STATUSES
    by_id = {a["task_id"]: a for a in result["assignments"]}
    # The scheduler treats lower numerical priority as more important.
    # On a single agent, the higher-priority task (priority=1) must start
    # at or before the lower-priority one (priority=2).
    # NOTE: the lex objective doesn't optimise for story priority directly,
    # but the warm-start heuristic and tie-breaking should respect it. If
    # the solver doesn't enforce ordering strictly, both tasks may be tied
    # at start=0 / start=duration — accept either.
    assert by_id["T002"]["start"] <= by_id["T001"]["start"]


# ── Kappa boundary INFEASIBLE ────────────────────────────────────────────


def test_kappa_boundary_is_infeasible():
    """n_tasks = k + 1 on n_agents=1 with kappa=k → preflight rejects."""
    k = 4
    data = make_solver_input(
        tasks=[
            make_task(f"T{i:03d}", file_paths=[f"f{i}.py"]) for i in range(k + 1)
        ],
        agents=[make_agent("A0", kappa=k, context_budget=200_000)],
        config={"time_limit": 5},
    )
    with pytest.raises(ScheduleInputError, match="kappa|κ"):
        solve_from_json(data)


def test_kappa_at_boundary_succeeds():
    """n_tasks = k on n_agents=1 with kappa=k → feasible."""
    k = 4
    data = make_solver_input(
        tasks=[
            make_task(f"T{i:03d}", file_paths=[f"f{i}.py"]) for i in range(k)
        ],
        agents=[make_agent("A0", kappa=k, context_budget=200_000)],
        config={"time_limit": 5},
    )
    result = solve_from_json(data)
    assert result["status"] in TERMINAL_STATUSES
