"""Cover the three branches of ``_phase1_infeasible_message``.

The function distinguishes (a) proven INFEASIBLE, (b) lower-bound exceeds
horizon, and (c) timeout (UNKNOWN with lb <= horizon). Existing tests only
exercise the i18n keys; here we drive the function via a faked Phase-1
solver tuple and assert each branch's message comes back correctly.
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from solver.i18n import t
from solver.orchestration import runner
from solver.scheduler import solve_from_json
from tests._helpers import make_agent, make_solver_input, make_task


def _data() -> dict:
    return make_solver_input(
        tasks=[make_task("T001", file_paths=["a.py"]), make_task("T002", file_paths=["b.py"])],
        agents=[make_agent("A0")],
        config={"time_limit": 5},
    )


@dataclass
class _FakeSolver:
    """Just enough of cp_model.CpSolver for ``_phase1_infeasible_message``."""

    bound: float
    status_label: str = "UNKNOWN"

    @property
    def best_objective_bound(self) -> float:
        return self.bound

    def status_name(self, _status: cp_model.CpSolverStatus) -> str:
        return self.status_label


def _patch_phase1(monkeypatch, status: cp_model.CpSolverStatus, *, bound: float, label: str) -> None:
    fake = _FakeSolver(bound=bound, status_label=label)

    def fake_run_solver(model, config, callback=None, **kwargs):
        return fake, status, 0.001

    monkeypatch.setattr(runner, "_run_solver", fake_run_solver)


def test_infeasible_proven_branch(monkeypatch):
    _patch_phase1(monkeypatch, cp_model.INFEASIBLE, bound=0, label="INFEASIBLE")
    result = solve_from_json(_data())
    assert result["status"] == "INFEASIBLE"
    expected_fragment = t("phase1_infeasible_proven", horizon=result["stats"]["horizon"])
    assert result["message"] == expected_fragment


def test_lb_exceeds_horizon_branch(monkeypatch):
    _patch_phase1(monkeypatch, cp_model.UNKNOWN, bound=10**9, label="UNKNOWN")
    result = solve_from_json(_data())
    assert result["status"] == "INFEASIBLE"
    horizon = result["stats"]["horizon"]
    expected = t(
        "phase1_infeasible_lb_exceeds_horizon",
        status="UNKNOWN",
        lb=f"{10**9:.0f}",
        horizon=horizon,
    )
    assert result["message"] == expected


def test_timeout_branch(monkeypatch):
    _patch_phase1(monkeypatch, cp_model.UNKNOWN, bound=0, label="UNKNOWN")
    result = solve_from_json(_data())
    assert result["status"] == "INFEASIBLE"
    horizon = result["stats"]["horizon"]
    expected = t(
        "phase1_infeasible_timeout",
        status="UNKNOWN",
        horizon=horizon,
        lb="0",
    )
    assert result["message"] == expected
