"""Symmetry-breaking constraints among interchangeable agents."""

from __future__ import annotations

from solver.scheduler import solve_from_json
from tests.conftest import TERMINAL_STATUSES, make_agent, make_solver_input, make_task

_NUM_TASKS = 6
_NUM_AGENTS = 3


def _identical_agents_problem(*, symmetry_breaking: bool) -> dict:
    tasks = [
        make_task(f"T{i:03d}", file_paths=[f"f{i}.py"])
        for i in range(_NUM_TASKS)
    ]
    agents = [
        make_agent(f"A{j}", price_per_1k_tokens=1.0)
        for j in range(_NUM_AGENTS)
    ]
    return make_solver_input(
        tasks,
        agents,
        config={
            "time_limit": 5,
            "symmetry_breaking": symmetry_breaking,
            "warm_start": False,
        },
    )


class TestSymmetryBreakingPreservesOptimum:
    def test_both_modes_feasible(self) -> None:
        on = solve_from_json(_identical_agents_problem(symmetry_breaking=True))
        off = solve_from_json(_identical_agents_problem(symmetry_breaking=False))
        assert on["status"] in TERMINAL_STATUSES
        assert off["status"] in TERMINAL_STATUSES

    def test_makespan_invariant(self) -> None:
        on = solve_from_json(_identical_agents_problem(symmetry_breaking=True))
        off = solve_from_json(_identical_agents_problem(symmetry_breaking=False))
        assert on["stats"]["makespan"] == off["stats"]["makespan"]

    def test_max_load_invariant(self) -> None:
        on = solve_from_json(_identical_agents_problem(symmetry_breaking=True))
        off = solve_from_json(_identical_agents_problem(symmetry_breaking=False))
        assert on["stats"]["max_load"] == off["stats"]["max_load"]

    def test_symmetry_breaking_orders_loads(self) -> None:
        """When the flag is on, identical-agent loads are non-increasing by index.

        That is exactly the constraint the model adds; this check verifies
        the constraint is wired up rather than being a dead config flag.
        """
        result = solve_from_json(_identical_agents_problem(symmetry_breaking=True))
        loads = [row["total_load"] for row in result["agent_summary"]]
        for a, b in zip(loads, loads[1:], strict=False):
            assert a >= b, f"loads not non-increasing under symmetry_breaking: {loads}"


class TestSymmetryBreakingWalltime:
    def test_walltime_not_worse_with_symmetry_breaking(self) -> None:
        # Informational: on a 6-task instance both runs finish in well under
        # a second and timing variance dwarfs the algorithmic effect, so we
        # only assert the walltime is not catastrophically worse with the
        # flag on. The optimum-invariance assertions above are the hard
        # contract; this is a smoke check for regression on bigger
        # instances.
        on = solve_from_json(_identical_agents_problem(symmetry_breaking=True))
        off = solve_from_json(_identical_agents_problem(symmetry_breaking=False))
        t_on = on["stats"].get("phase1_time", 0.0)
        t_off = off["stats"].get("phase1_time", 0.0)
        # Allow a 1s ceiling above the off-baseline; tiny instances are noisy.
        assert t_on <= t_off + 1.0
