"""Edge-case coverage for the horizon multiplier."""

from __future__ import annotations

from solver.scheduler import solve_from_json
from tests._helpers import (
    TERMINAL_STATUSES,
    make_agent,
    make_chain_edges,
    make_chain_tasks,
    make_solver_input,
    make_task,
)


class TestLongChain:
    """10-task chain where each task depends on the previous one."""

    def _data(self, *, horizon_multiplier: float) -> dict:
        n = 10
        return make_solver_input(
            tasks=make_chain_tasks(n, estimated_tokens=500),
            agents=[make_agent("A0", kappa=50, context_budget=500_000)],
            edges=make_chain_edges(n),
            config={"time_limit": 5, "horizon_multiplier": horizon_multiplier},
        )

    def test_default_horizon_feasible(self) -> None:
        result = solve_from_json(self._data(horizon_multiplier=1.5))
        assert result["status"] in TERMINAL_STATUSES
        # Sanity: 10 chained tasks of 500 tokens / 100 token_unit = 5 each.
        assert result["stats"]["makespan"] == 50

    def test_tight_horizon_still_finds_optimum(self) -> None:
        """1.01 multiplier leaves only ~1% slack — but the bound used is the
        critical path, so feasibility holds and the optimum is preserved.
        """
        result = solve_from_json(self._data(horizon_multiplier=1.01))
        assert result["status"] in TERMINAL_STATUSES
        assert result["stats"]["makespan"] == 50

    def test_loose_horizon_same_optimum(self) -> None:
        result = solve_from_json(self._data(horizon_multiplier=10.0))
        assert result["status"] in TERMINAL_STATUSES
        assert result["stats"]["makespan"] == 50


class TestDominantDuration:
    """One task whose duration dominates the rest combined.

    The horizon's load-bound (sum / num_agents) and critical-path bound
    must accommodate this without inflating combinatorially.
    """

    def _data(self, *, horizon_multiplier: float = 1.5) -> dict:
        # Six small tasks (≈300 tokens) plus one dominant task (≈9000 tokens).
        small = [
            make_task(f"T{i:03d}", file_paths=[f"f{i}.py"], estimated_tokens=300)
            for i in range(6)
        ]
        dominant = make_task(
            "T099", file_paths=["dominant.py"], estimated_tokens=9_000
        )
        return make_solver_input(
            tasks=small + [dominant],
            agents=[
                make_agent("A0", kappa=50, context_budget=200_000),
                make_agent("A1", kappa=50, context_budget=200_000),
            ],
            config={"time_limit": 5, "horizon_multiplier": horizon_multiplier},
        )

    def test_dominant_task_feasible(self) -> None:
        result = solve_from_json(self._data())
        assert result["status"] in TERMINAL_STATUSES
        # Dominant task alone is 90 units (9000 / 100). Makespan must be
        # at least that because no agent can split it.
        assert result["stats"]["makespan"] >= 90

    def test_horizon_accommodates_dominant_task(self) -> None:
        result = solve_from_json(self._data(horizon_multiplier=1.01))
        assert result["status"] in TERMINAL_STATUSES
        # horizon is reported in stats and must be >= makespan.
        assert result["stats"]["horizon"] >= result["stats"]["makespan"]

    def test_loose_horizon_does_not_change_optimum(self) -> None:
        tight = solve_from_json(self._data(horizon_multiplier=1.01))
        loose = solve_from_json(self._data(horizon_multiplier=10.0))
        if tight["status"] in TERMINAL_STATUSES and loose["status"] in TERMINAL_STATUSES:
            assert tight["stats"]["makespan"] == loose["stats"]["makespan"]


class TestHorizonScalesWithMultiplier:
    """The reported horizon should scale (roughly) linearly with the multiplier."""

    def _data(self, *, horizon_multiplier: float) -> dict:
        n = 5
        return make_solver_input(
            tasks=make_chain_tasks(n, estimated_tokens=500),
            agents=[make_agent("A0", kappa=50, context_budget=500_000)],
            edges=make_chain_edges(n),
            config={"time_limit": 5, "horizon_multiplier": horizon_multiplier},
        )

    def test_doubling_multiplier_doubles_horizon(self) -> None:
        small = solve_from_json(self._data(horizon_multiplier=2.0))
        big = solve_from_json(self._data(horizon_multiplier=4.0))
        assert small["status"] in TERMINAL_STATUSES
        assert big["status"] in TERMINAL_STATUSES
        # _horizon ceil-multiplies; a doubling of the multiplier must
        # roughly double the horizon (off-by-one tolerated for ceil).
        h_small = small["stats"]["horizon"]
        h_big = big["stats"]["horizon"]
        assert h_big >= 2 * h_small - 1
        # Precise off-by-one band (was previously a loose 3*h_small bound).
        assert h_big <= 2 * h_small + 1
