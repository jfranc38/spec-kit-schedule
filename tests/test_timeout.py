"""Time-limit behaviour: solver returns a result envelope and respects the budget."""

from __future__ import annotations

from ortools.sat.python import cp_model

from solver.i18n_catalog import WARN_ANYTIME_TIMEOUT
from solver.orchestration import runner
from solver.scheduler import solve_from_json
from tests._helpers import make_chain_problem

# Slack on top of the configured budget. CP-SAT can overshoot the wall
# limit by a small amount while finishing the current restart.
_TIME_LIMIT_SLACK_SECONDS = 5.0


def _build_problem(*, n_tasks: int = 12, n_agents: int = 3, **config_overrides: object) -> dict:
    """Parametric chain DAG; each task has its own file so file-mutex is inert."""
    config = {"time_limit": 1}
    config.update(config_overrides)
    return make_chain_problem(
        n_tasks=n_tasks,
        n_agents=n_agents,
        task_overrides={"estimated_tokens": 400},
        agent_overrides={
            "kappa": n_tasks,
            "context_budget": 200_000,
            "price_per_1k_tokens": 1.0,
        },
        config=config,
    )


class TestTimeLimitRespected:
    def test_lex_returns_dict_under_tight_budget(self) -> None:
        result = solve_from_json(_build_problem(time_limit=1))
        # The contract is "no exception" + structured result envelope.
        assert isinstance(result, dict)
        assert result["status"] in {"OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"}
        assert "stats" in result

    def test_phase1_elapsed_bounded(self) -> None:
        budget = 1
        result = solve_from_json(_build_problem(time_limit=budget))
        elapsed = result["stats"].get("phase1_time")
        assert elapsed is not None
        # CP-SAT may run slightly past the deadline; tolerate generous slack.
        assert elapsed <= budget + _TIME_LIMIT_SLACK_SECONDS

    def test_cost_aware_returns_dict_under_tight_budget(self) -> None:
        result = solve_from_json(_build_problem(time_limit=1, objective="cost_aware"))
        assert isinstance(result, dict)
        assert result["status"] in {"OPTIMAL", "FEASIBLE", "INFEASIBLE", "UNKNOWN"}
        # cost_aware records phase1_time even if later phases short-circuit.
        assert "phase1_time" in result["stats"]


class TestAnytimeTimeoutPath:
    """anytime=True is the documented timeout-friendly mode.

    When the solver does not prove optimality within the budget, status is
    FEASIBLE, ``final_gap`` is recorded, and the ``anytime_timeout`` warning
    is emitted. On a small instance CP-SAT usually finishes optimally in
    well under a second, so the only assertion we can make deterministically
    is that the result is well-formed and ``intermediate`` exists.
    """

    def test_intermediate_recorded(self) -> None:
        result = solve_from_json(_build_problem(time_limit=1, anytime=True))
        assert "intermediate" in result["stats"]
        assert isinstance(result["stats"]["intermediate"], list)

    def test_anytime_warning_emitted_on_forced_feasible(self, monkeypatch) -> None:
        """Deterministic — force Phase 1 to return FEASIBLE.

        The branch in ``_solve_phase1_makespan`` that emits
        ``WARN_ANYTIME_TIMEOUT`` only triggers when status is FEASIBLE
        (not OPTIMAL). Wrap ``_run_solver`` so we always get FEASIBLE
        on the first call regardless of how fast CP-SAT actually finishes.
        """
        real_run = runner._run_solver

        def fake_run(model, config, callback=None, **kwargs):
            solver, status, elapsed = real_run(model, config, callback, **kwargs)
            if status == cp_model.OPTIMAL:
                return solver, cp_model.FEASIBLE, elapsed
            return solver, status, elapsed

        monkeypatch.setattr(runner, "_run_solver", fake_run)
        result = solve_from_json(_build_problem(time_limit=1, anytime=True))
        warning_codes = {w.get("code") for w in result.get("warnings", [])}
        assert WARN_ANYTIME_TIMEOUT in warning_codes
        assert "final_gap" in result["stats"]
