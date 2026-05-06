"""Hypothesis property-based tests for solve-result invariants.

Generates small random RCPSP instances (n_tasks ≤ 6, n_agents ≤ 3) and
asserts:
- every task is assigned to exactly one agent
- precedence edges are satisfied (start[succ] >= end[pred])
- per-agent κ caps are respected
- per-agent context budgets are respected
- file-mutex respected for non-[P] tasks
- makespan >= the critical-path lower bound

Bounds are tiny so the suite stays fast even with ``max_examples=20``.
"""

from __future__ import annotations

import pytest

# Hypothesis is a dev-only dependency. Skip the whole module on a fresh clone
# without ``--extra dev`` so ``pytest`` doesn't error out at collection time.
pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from solver.scheduler import (  # noqa: E402
    compute_compatible_agents,
    compute_durations,
    compute_min_durations,
    critical_path_bound,
    solve_from_json,
)
from solver.validation import ScheduleInputError  # noqa: E402
from tests._helpers import (  # noqa: E402
    TERMINAL_STATUSES,
    make_agent,
    make_solver_input,
    make_task,
)

# Tight settings so this property suite runs in <30s on CI.
PROPERTY_SETTINGS = settings(
    max_examples=20,
    deadline=2000,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


def _build_chain_dag(n_tasks: int, edge_seed: list[int]) -> list[tuple[int, int]]:
    """Build a small DAG: each task ``j`` may depend on a subset of i<j tasks.

    The element ``edge_seed[j]`` is a bitmask over predecessors in [0, j).
    """
    edges: list[tuple[int, int]] = []
    for j in range(1, n_tasks):
        mask = edge_seed[j]
        for i in range(j):
            if mask & (1 << i):
                edges.append((i, j))
    return edges


@st.composite
def _rcpsp_instance(draw):
    n_tasks = draw(st.integers(min_value=1, max_value=6))
    n_agents = draw(st.integers(min_value=1, max_value=3))
    edge_seed = [
        draw(st.integers(min_value=0, max_value=2**j - 1 if j > 0 else 0))
        for j in range(n_tasks)
    ]
    tokens = [draw(st.integers(min_value=100, max_value=2_000)) for _ in range(n_tasks)]
    kappas = [draw(st.integers(min_value=2, max_value=8)) for _ in range(n_agents)]
    budgets = [draw(st.integers(min_value=10_000, max_value=100_000)) for _ in range(n_agents)]
    parallels = [draw(st.booleans()) for _ in range(n_tasks)]
    # Each task gets its own file, so file-mutex is trivially satisfied
    # except when we deliberately collide on a shared file path.
    file_collide = draw(st.booleans())
    tasks = [
        make_task(
            f"T{i:03d}",
            file_paths=["shared.py"] if file_collide and i < 2 else [f"f{i}.py"],
            estimated_tokens=tokens[i],
            parallel_flag=parallels[i],
        )
        for i in range(n_tasks)
    ]
    edges = _build_chain_dag(n_tasks, edge_seed)
    edge_pairs = [[tasks[s]["id"], tasks[d]["id"]] for s, d in edges]
    agents = [
        make_agent(f"A{j}", kappa=kappas[j], context_budget=budgets[j])
        for j in range(n_agents)
    ]
    # Drop the per-instance solver budget from 5s to 2s so a pathological
    # Hypothesis example cannot burn the entire deadline on one shrink
    # candidate. CI keeps the same property coverage, just with tighter
    # per-instance time pressure.
    return make_solver_input(tasks, agents, edges=edge_pairs, config={"time_limit": 2})


@PROPERTY_SETTINGS
@given(_rcpsp_instance())
def test_solve_invariants_hold(data):
    try:
        result = solve_from_json(data)
    except ScheduleInputError:
        # Preflight rejected the instance (over-budget or κ-exceeded). Valid
        # outcome: the property holds vacuously when the solver refuses.
        return
    if result["status"] not in TERMINAL_STATUSES:
        # UNKNOWN / INFEASIBLE — no schedule to check invariants on.
        return
    assignments = result["assignments"]
    by_id = {a["task_id"]: a for a in assignments}
    n_tasks = len(data["tasks"])

    # 1. Every task is assigned exactly once and to a real agent.
    assert len(by_id) == n_tasks
    agent_ids = {a["id"] for a in data["agents"]}
    for a in assignments:
        assert a["agent_id"] in agent_ids
        assert a["start"] >= 0
        assert a["end"] == a["start"] + a["duration"]

    # 2. Precedence respected.
    for src, dst in data["edges"]:
        assert by_id[src]["end"] <= by_id[dst]["start"], (
            f"precedence violated: {src} ({by_id[src]}) -> {dst} ({by_id[dst]})"
        )

    # 3. κ caps and budgets.
    by_agent: dict[str, list[dict]] = {ag["id"]: [] for ag in data["agents"]}
    for a in assignments:
        by_agent[a["agent_id"]].append(a)
    agent_specs = {ag["id"]: ag for ag in data["agents"]}
    for aid, alist in by_agent.items():
        spec = agent_specs[aid]
        assert len(alist) <= spec["kappa"]
        assert sum(a["tokens"] for a in alist) <= spec["context_budget"]

    # 4. File mutex for non-[P] tasks.
    by_file: dict[str, list[dict]] = {}
    task_specs = {t["id"]: t for t in data["tasks"]}
    for a in assignments:
        spec = task_specs[a["task_id"]]
        if spec["parallel_flag"]:
            continue
        for fp in spec["file_paths"]:
            by_file.setdefault(fp, []).append(a)
    for _fp, group in by_file.items():
        if len(group) <= 1:
            continue
        # Same-file non-[P] tasks must not overlap in time.
        group.sort(key=lambda a: a["start"])
        for prev, curr in zip(group, group[1:], strict=False):
            assert prev["end"] <= curr["start"]

    # 5. Makespan ≥ critical-path lower bound.
    from solver.scheduler import _parse_input
    tasks_obj, edges_obj, agents_obj, config = _parse_input(data)
    compat = compute_compatible_agents(tasks_obj, agents_obj)
    p = compute_durations(tasks_obj, agents_obj, config.token_unit, config.stochastic_quantile)
    min_dur = compute_min_durations(len(tasks_obj), compat, p)
    cp_bound = critical_path_bound(len(tasks_obj), edges_obj, min_dur)
    assert result["stats"]["makespan"] >= cp_bound
