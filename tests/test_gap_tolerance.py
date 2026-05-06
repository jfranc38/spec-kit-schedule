"""MIP-gap reporting via anytime mode."""

from __future__ import annotations

from solver.scheduler import solve_from_json
from tests._helpers import TERMINAL_STATUSES, make_chain_problem


def _problem(n_tasks: int = 8, **config_overrides: object) -> dict:
    """Chain DAG of independent files: small enough to solve to OPTIMAL fast."""
    config = {"time_limit": 5, "anytime": True}
    config.update(config_overrides)
    return make_chain_problem(
        n_tasks=n_tasks,
        n_agents=2,
        task_overrides={"estimated_tokens": 300},
        agent_overrides={"kappa": n_tasks, "context_budget": 100_000},
        config=config,
    )


class TestGapReporting:
    def test_intermediate_entries_carry_gap(self) -> None:
        result = solve_from_json(_problem(anytime=True))
        intermediates = result["stats"].get("intermediate", [])
        assert intermediates, "anytime mode must record at least one incumbent"
        for entry in intermediates:
            assert "gap" in entry
            assert isinstance(entry["gap"], int | float)
            assert 0.0 <= entry["gap"] <= 1.0 + 1e-6

    def test_optimal_solve_final_gap_is_zero(self) -> None:
        result = solve_from_json(_problem(anytime=True))
        if result["status"] != "OPTIMAL":
            return  # Only meaningful when the solver proves optimality.
        last = result["stats"]["intermediate"][-1]
        assert last["gap"] == 0.0

    def test_gap_absent_when_anytime_off(self) -> None:
        result = solve_from_json(_problem(anytime=False))
        assert "intermediate" not in result["stats"]
        # final_gap is only set when anytime hits the timeout path.
        assert "final_gap" not in result["stats"]


class TestTightBudgetIsGraceful:
    """Pair a generous budget for the optimum with a tight one to confirm that
    the solver never raises and always returns a status string.
    """

    def test_tight_time_limit_returns_envelope(self) -> None:
        # 1s is the minimum allowed by the schema. Combined with anytime=True
        # we exercise the timeout-friendly code path even if the optimum is
        # found in milliseconds.
        result = solve_from_json(_problem(time_limit=1, anytime=True))
        assert isinstance(result, dict)
        assert result["status"] in {"OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"}
        assert "stats" in result

    def test_tight_then_loose_same_or_better_optimum(self) -> None:
        """Loosening the budget never produces a worse makespan — sanity check
        that gap reporting doesn't perturb the objective.
        """
        tight = solve_from_json(_problem(time_limit=1, anytime=True))
        loose = solve_from_json(_problem(time_limit=5, anytime=True))
        if tight["status"] in TERMINAL_STATUSES and loose["status"] in TERMINAL_STATUSES:
            assert loose["stats"]["makespan"] <= tight["stats"]["makespan"]
