"""Phase-1 / per-phase solver loop and shared diagnostics.

This module owns the OR-Tools driver function (:func:`_run_solver`) and
the small helpers that wrap it: the anytime callback, the gap calculator,
phase-status recording, and the Phase-1 entry that lex / cost_aware share.
The :func:`_run_phase` scaffold drives a "freeze + clear + rehint + minimize
+ run + record + fallback" sequence shared by Phase 2 and Phase 3.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Protocol, cast

from ortools.sat.python import cp_model

from ..i18n import t
from ..i18n_catalog import WARN_ANYTIME_TIMEOUT
from ..model.types import SolverConfig, Task
from ..warnings_collector import WarningCollector

if TYPE_CHECKING:
    from ..model.build import ModelBundle

log = logging.getLogger(__name__)


# Threshold below which the objective is treated as zero for gap computation.
# Objectives this small (after CP-SAT's integer scaling) make ``|obj-bound|/|obj|``
# numerically unstable; reporting the gap as ``0.0`` is the documented behaviour.
_GAP_OBJ_EPSILON = 1e-9


class _GapSource(Protocol):
    """Minimal surface needed to compute a relative gap.

    Both :class:`cp_model.CpSolver` and :class:`cp_model.CpSolverSolutionCallback`
    expose ``objective_value`` and ``best_objective_bound``; this Protocol lets
    :func:`_compute_gap` accept either without bending mypy.
    """

    @property
    def objective_value(self) -> float: ...
    @property
    def best_objective_bound(self) -> float: ...


def _compute_gap(source: _GapSource) -> float:
    """Relative MIP-style gap = |obj - bound| / |obj|, rounded to 6 dp.

    Returns ``0.0`` when ``|obj|`` is below ``_GAP_OBJ_EPSILON`` (avoids a
    division blow-up on tiny objectives) and also when both values are zero.
    """
    obj = source.objective_value
    bound = source.best_objective_bound
    if abs(obj) < _GAP_OBJ_EPSILON:
        return 0.0
    return round(abs(obj - bound) / abs(obj), 6)


class _AnytimeCallback(cp_model.CpSolverSolutionCallback):
    """Records each improving incumbent during an anytime solve."""

    def __init__(self, t0: float) -> None:
        super().__init__()
        self._t0 = t0
        self.intermediates: list[dict[str, Any]] = []

    def on_solution_callback(self) -> None:
        obj = self.objective_value
        elapsed = time.time() - self._t0
        # Single source of gap math — keeps the callback aligned with
        # ``_compute_gap`` so post-solve and per-incumbent gaps agree.
        gap = _compute_gap(cast(_GapSource, self))
        self.intermediates.append(
            {
                "makespan": int(obj),
                "time": round(elapsed, 3),
                "gap": gap,
            }
        )


def _run_solver(
    model: cp_model.CpModel,
    config: SolverConfig,
    callback: cp_model.CpSolverSolutionCallback | None = None,
    *,
    time_limit_override: float | None = None,
) -> tuple[cp_model.CpSolver, cp_model.CpSolverStatus, float]:
    """Run CP-SAT against ``model`` honouring ``config`` parameters.

    ``time_limit_override`` lets the orchestration modules pass each phase the
    *remaining* time budget instead of the full ``config.time_limit``. Without
    it, a 3-phase lexicographic solve would burn ``3 × config.time_limit`` of
    wall-time in the worst case.
    """
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = config.num_workers
    budget = (
        config.time_limit
        if time_limit_override is None
        else max(1.0, float(time_limit_override))
    )
    solver.parameters.max_time_in_seconds = budget
    solver.parameters.random_seed = config.random_seed
    solver.parameters.log_search_progress = bool(config.verbose)
    t0 = time.time()
    status = solver.solve(model, callback) if callback is not None else solver.solve(model)
    elapsed = time.time() - t0
    return solver, status, elapsed


def _record_phase_status(
    stats: dict[str, Any],
    phase: int,
    solver: cp_model.CpSolver,
    status: cp_model.CpSolverStatus,
) -> None:
    """Record both a stringified and integer status for ``phase`` in ``stats``.

    The integer ``phase{N}_status_code`` is the canonical comparison surface;
    the string ``phase{N}_status`` is kept for user-facing reporting.
    """
    stats[f"phase{phase}_status"] = solver.status_name(status)
    stats[f"phase{phase}_status_code"] = int(status)


def _phase1_infeasible_message(
    solver: cp_model.CpSolver,
    status: cp_model.CpSolverStatus,
    *,
    horizon: int,
) -> str:
    """Build a diagnostic message when Phase 1 returns no feasible schedule.

    Distinguishes the two common causes by surfacing the horizon and the
    solver's best lower bound on makespan: if the bound exceeds the horizon
    the model is genuinely infeasible at that horizon, otherwise the solver
    timed out before proving INFEASIBLE/finding a schedule.
    """
    status_name = solver.status_name(status)
    if status == cp_model.INFEASIBLE:
        return t("phase1_infeasible_proven", horizon=horizon)
    bound = solver.best_objective_bound
    if bound > horizon:
        return t(
            "phase1_infeasible_lb_exceeds_horizon",
            status=status_name,
            lb=f"{bound:.0f}",
            horizon=horizon,
        )
    return t(
        "phase1_infeasible_timeout",
        status=status_name,
        horizon=horizon,
        lb=f"{bound:.0f}",
    )


def _phase1_infeasible_envelope(
    solver1: cp_model.CpSolver,
    status1: cp_model.CpSolverStatus,
    *,
    horizon: int,
    stats: dict[str, Any],
    warnings: WarningCollector,
) -> dict[str, Any]:
    """Build the standard INFEASIBLE result envelope after Phase 1 fails.

    Shared by lex.py and cost_aware.py so the two orchestration modules
    return the same shape on early exit (status / message / stats /
    warnings).
    """
    return {
        "status": "INFEASIBLE",
        "message": _phase1_infeasible_message(solver1, status1, horizon=horizon),
        "stats": stats,
        "warnings": warnings.as_list(),
    }


def _solve_phase1_makespan(
    bundle: ModelBundle,
    config: SolverConfig,
    stats: dict[str, Any],
    warnings: WarningCollector,
) -> tuple[cp_model.CpSolver, cp_model.CpSolverStatus, _AnytimeCallback | None]:
    """Phase 1 shared by lexicographic and cost_aware: minimise makespan."""
    bundle.model.minimize(bundle.makespan)
    callback: _AnytimeCallback | None = None
    if config.anytime:
        callback = _AnytimeCallback(time.time())
    solver1, status1, elapsed1 = _run_solver(bundle.model, config, callback=callback)
    stats["phase1_time"] = round(elapsed1, 2)
    _record_phase_status(stats, 1, solver1, status1)
    if callback is not None:
        stats["intermediate"] = callback.intermediates
    if status1 == cp_model.FEASIBLE and callback is not None:
        stats["final_gap"] = _compute_gap(solver1)
        warnings.add(WARN_ANYTIME_TIMEOUT, t(WARN_ANYTIME_TIMEOUT))
    return solver1, status1, callback


def _run_phase(
    bundle: ModelBundle,
    config: SolverConfig,
    *,
    phase: int,
    minimize_expr: Any,
    fallback_solver: cp_model.CpSolver,
    fallback_status: cp_model.CpSolverStatus,
    stats: dict[str, Any],
    warnings: WarningCollector,
    fallback_warning_code: str | None,
    elapsed_so_far: float,
    tasks: list[Task],
    compat: dict[int, list[int]],
) -> tuple[cp_model.CpSolver, cp_model.CpSolverStatus, float]:
    """Run one phase of a multi-phase pinned solve.

    Handles the canonical "clear objective + rehint + minimise + run +
    record + fallback" sequence shared by Phase 2 (load balance under
    pinned makespan) and Phase 3 (load balance under pinned cost). On
    timeout, falls back to ``fallback_solver`` and emits ``fallback_warning_code``
    if provided.

    Returns ``(solver, status, elapsed)`` where the solver is either the
    new phase solver (if it found a solution) or the supplied fallback.
    The phase elapsed time is also recorded onto ``stats[f"phase{phase}_time"]``.
    """
    # Local import sidesteps the runner ↔ result.extract cycle: extract
    # imports nothing from runner, but lex / cost_aware import _rehint_from
    # which lives in extract — bringing the import into the helper keeps
    # runner.py free of result.* coupling at module load.
    from ..model.build import _clear_objective
    from ..result.extract import _rehint_from

    _clear_objective(bundle.model)
    _rehint_from(bundle, fallback_solver, tasks, compat)
    bundle.model.minimize(minimize_expr)

    # Honour the global time budget across phases — without this, a 3-phase
    # cost-aware solve could burn 3 × config.time_limit in the worst case.
    phase_budget = max(1.0, float(config.time_limit) - elapsed_so_far)
    solver_phase, status_phase, elapsed_phase = _run_solver(
        bundle.model, config, time_limit_override=phase_budget
    )
    stats[f"phase{phase}_time"] = round(elapsed_phase, 2)
    _record_phase_status(stats, phase, solver_phase, status_phase)
    log.info(
        "phase=%d status=%s elapsed=%.2fs",
        phase,
        solver_phase.status_name(status_phase),
        elapsed_phase,
    )

    if status_phase in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return solver_phase, status_phase, elapsed_phase

    if fallback_warning_code is not None:
        warnings.add(fallback_warning_code, t(fallback_warning_code))
    log.info("phase=%d fallback to phase=%d result", phase, phase - 1)
    return fallback_solver, fallback_status, elapsed_phase


