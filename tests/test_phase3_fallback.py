"""Phase 3 fallback emits the distinct ``phase3_fallback`` warning code."""

from __future__ import annotations

from ortools.sat.python import cp_model

from solver import scheduler
from solver.i18n_catalog import WARN_PHASE2_FALLBACK, WARN_PHASE3_FALLBACK
from solver.scheduler import solve_from_json
from tests.conftest import make_agent, make_solver_input, make_task


def _cost_aware_data() -> dict:
    return make_solver_input(
        tasks=[
            make_task("T001", file_paths=["a.py"], estimated_tokens=500),
            make_task("T002", file_paths=["b.py"], estimated_tokens=500),
        ],
        agents=[
            make_agent("A0", context_budget=20_000, price_per_1k_tokens=1.0),
            make_agent("A1", context_budget=20_000, price_per_1k_tokens=2.0),
        ],
        config={"objective": "cost_aware", "time_limit": 10, "num_workers": 1},
    )


def test_phase3_fallback_emits_distinct_warning_code(monkeypatch):
    real_run_solver = scheduler._run_solver
    call_count = {"n": 0}

    def fake_run_solver(model, config, callback=None):
        call_count["n"] += 1
        if call_count["n"] == 3:
            solver = cp_model.CpSolver()
            solver.parameters.num_workers = 1
            solver.parameters.max_time_in_seconds = 0.0001
            return solver, cp_model.UNKNOWN, 0.0001
        return real_run_solver(model, config, callback)

    monkeypatch.setattr(scheduler, "_run_solver", fake_run_solver)

    result = solve_from_json(_cost_aware_data())
    codes = [w["code"] for w in result["warnings"]]
    assert WARN_PHASE3_FALLBACK in codes
    assert WARN_PHASE2_FALLBACK not in codes


def test_no_phase3_fallback_under_normal_solve():
    result = solve_from_json(_cost_aware_data())
    codes = [w["code"] for w in result["warnings"]]
    assert WARN_PHASE3_FALLBACK not in codes
    assert WARN_PHASE2_FALLBACK not in codes
