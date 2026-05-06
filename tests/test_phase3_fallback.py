"""Phase 2 / Phase 3 fallback emit distinct ``phaseN_fallback`` warning codes.

Extends the original Phase 3 fallback coverage to also force a Phase 2
fallback in both the lex (2-phase) and cost-aware (3-phase) paths.

The failure-branch wrapper invokes the real ``_run_solver`` and replaces
only the returned status, keeping the genuine solver instance intact so
``_finalize_result`` extracts a real Phase-1 schedule.
"""

from __future__ import annotations

from ortools.sat.python import cp_model

from solver.i18n_catalog import WARN_PHASE2_FALLBACK, WARN_PHASE3_FALLBACK
from solver.orchestration import runner
from solver.scheduler import solve_from_json
from tests._helpers import make_agent, make_solver_input, make_task


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


def _lex_data() -> dict:
    return make_solver_input(
        tasks=[
            make_task("T001", file_paths=["a.py"]),
            make_task("T002", file_paths=["b.py"]),
        ],
        agents=[make_agent("A0"), make_agent("A1")],
        config={"objective": "lexicographic", "time_limit": 5, "num_workers": 1},
    )


def _patch_run_solver(monkeypatch, *, force_unknown_on_call: int) -> None:
    """Replace ``runner._run_solver`` so that the n-th call returns UNKNOWN.

    The real solver still runs; we only swap the returned status. This keeps
    the solver values usable by ``_finalize_result`` for fallback paths and
    avoids constructing a synthetic solver instance.
    """
    real_run_solver = runner._run_solver
    call_count = {"n": 0}

    def fake_run_solver(model, config, callback=None, **kwargs):
        call_count["n"] += 1
        solver, status, elapsed = real_run_solver(model, config, callback, **kwargs)
        if call_count["n"] == force_unknown_on_call:
            return solver, cp_model.UNKNOWN, elapsed
        return solver, status, elapsed

    monkeypatch.setattr(runner, "_run_solver", fake_run_solver)


# ── Phase 3 fallback (cost-aware only) ───────────────────────────────────


def test_phase3_fallback_emits_distinct_warning_code(monkeypatch):
    _patch_run_solver(monkeypatch, force_unknown_on_call=3)
    result = solve_from_json(_cost_aware_data())
    codes = [w["code"] for w in result["warnings"]]
    assert WARN_PHASE3_FALLBACK in codes
    assert WARN_PHASE2_FALLBACK not in codes


def test_no_phase3_fallback_under_normal_solve():
    result = solve_from_json(_cost_aware_data())
    codes = [w["code"] for w in result["warnings"]]
    assert WARN_PHASE3_FALLBACK not in codes
    assert WARN_PHASE2_FALLBACK not in codes


# ── Phase 2 fallback (lex + cost-aware) ──────────────────────────────────


def test_phase2_fallback_lex(monkeypatch):
    _patch_run_solver(monkeypatch, force_unknown_on_call=2)
    result = solve_from_json(_lex_data())
    codes = [w["code"] for w in result["warnings"]]
    assert WARN_PHASE2_FALLBACK in codes
    assert WARN_PHASE3_FALLBACK not in codes


def test_phase2_fallback_cost_aware(monkeypatch):
    _patch_run_solver(monkeypatch, force_unknown_on_call=2)
    result = solve_from_json(_cost_aware_data())
    codes = [w["code"] for w in result["warnings"]]
    assert WARN_PHASE2_FALLBACK in codes
    assert WARN_PHASE3_FALLBACK not in codes
