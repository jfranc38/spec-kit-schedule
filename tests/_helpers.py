"""JSON-builder helpers shared across the test suite.

The ``make_task`` / ``make_agent`` / ``make_solver_input`` helpers below build
the exact dict shape consumed by ``solver.scheduler.solve_from_json``. They are
plain functions (not fixtures) so callers can pass per-task overrides.
"""

from __future__ import annotations


def make_task(tid: str, **overrides: object) -> dict:
    """Build a task dict for ``solve_from_json``; overrides replace defaults."""
    task: dict = {
        "id": tid,
        "phase": "Setup",
        "story_id": None,
        "story_priority": 1,
        "parallel_flag": False,
        "file_paths": [f"src/{tid}.py"],
        "required_skill": "backend",
        "estimated_tokens": 500,
        "token_std_dev": 0.0,
        "action_verb": "implement",
    }
    task.update(overrides)
    return task


def make_agent(aid: str, **overrides: object) -> dict:
    """Build an agent dict for ``solve_from_json``; overrides replace defaults."""
    agent: dict = {
        "id": aid,
        "model": "test",
        "skills": ["backend"],
        "kappa": 10,
        "context_budget": 50_000,
        "speed_factor": 1.0,
        "price_per_1k_tokens": 0.0,
    }
    agent.update(overrides)
    return agent


def make_solver_input(
    tasks: list[dict],
    agents: list[dict],
    edges: list[list[str]] | None = None,
    config: dict | None = None,
) -> dict:
    """Assemble the JSON envelope passed to ``solve_from_json``."""
    cfg = {"time_limit": 10, "num_workers": 1, "warm_start": True}
    if config:
        cfg.update(config)
    return {
        "tasks": tasks,
        "edges": edges or [],
        "agents": agents,
        "config": cfg,
    }


def make_chain_tasks(n: int, prefix: str = "T", **task_overrides) -> list[dict]:
    """Build n tasks T000, T001, ... with shared overrides."""
    return [make_task(f"{prefix}{i:03d}", **task_overrides) for i in range(n)]


def make_chain_edges(n: int, prefix: str = "T") -> list[list[str]]:
    """Build a linear-chain DAG over T000 -> T001 -> ... -> T{n-1}."""
    return [[f"{prefix}{i:03d}", f"{prefix}{i + 1:03d}"] for i in range(n - 1)]


def make_chain_problem(
    n_tasks: int = 10,
    n_agents: int = 3,
    *,
    task_overrides: dict | None = None,
    agent_overrides: dict | None = None,
    config: dict | None = None,
) -> dict:
    """Build a solver-input dict for a chain-DAG of n_tasks across n_agents."""
    tasks = make_chain_tasks(n_tasks, **(task_overrides or {}))
    agents = [make_agent(f"A{i}", **(agent_overrides or {})) for i in range(n_agents)]
    edges = make_chain_edges(n_tasks)
    return make_solver_input(tasks, agents, edges=edges, config=config)


TERMINAL_STATUSES = frozenset({"OPTIMAL", "FEASIBLE"})
