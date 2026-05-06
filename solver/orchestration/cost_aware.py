"""Cost-aware lexicographic three-phase solve: lex(makespan, cost, max_load).

Phase 1 minimises makespan. Phase 2 freezes makespan, minimises total cost.
Phase 3 freezes cost, minimises max-load. Each frozen phase emits its own
fallback warning when the next phase fails to find a solution in time.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ortools.sat.python import cp_model

from ..i18n import t
from ..i18n_catalog import (
    WARN_COST_SCALE_UNDERFLOW,
    WARN_PHASE2_FALLBACK,
    WARN_PHASE3_FALLBACK,
)
from ..model.types import Agent, SolverConfig, Task
from ..result.extract import _finalize_result
from ..warnings_collector import WarningCollector
from . import runner

if TYPE_CHECKING:
    from ..model.build import ModelBundle

log = logging.getLogger(__name__)


def _solve_cost_aware(
    bundle: ModelBundle,
    tasks: list[Task],
    edges: list[tuple[int, int]],
    agents: list[Agent],
    compat: dict[int, list[int]],
    config: SolverConfig,
    stats: dict[str, Any],
    warnings: WarningCollector,
) -> dict[str, Any]:
    """Lexicographic lex(makespan, cost, max_load) three-phase solve."""
    from ..model.build import _cost_signals_underflowed

    assert bundle.total_cost is not None

    if _cost_signals_underflowed(tasks, agents, compat):
        warnings.add(WARN_COST_SCALE_UNDERFLOW, t(WARN_COST_SCALE_UNDERFLOW))

    solver1, status1, _cb = runner._solve_phase1_makespan(bundle, config, stats, warnings)
    log.info(
        "phase=1 status=%s elapsed=%.2fs",
        solver1.status_name(status1),
        stats.get("phase1_time", 0.0),
    )

    if status1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return runner._phase1_infeasible_envelope(
            solver1,
            status1,
            horizon=bundle.horizon,
            stats=stats,
            warnings=warnings,
        )

    ms_star = solver1.value(bundle.makespan)
    stats["makespan_phase1"] = ms_star

    # Phase 2: freeze makespan (== for tighter bound propagation), minimise cost.
    bundle.model.add(bundle.makespan == ms_star)
    elapsed1 = float(stats.get("phase1_time", 0.0))
    solver2, status2, elapsed2 = runner._run_phase(
        bundle,
        config,
        phase=2,
        minimize_expr=bundle.total_cost,
        fallback_solver=solver1,
        fallback_status=status1,
        stats=stats,
        warnings=warnings,
        fallback_warning_code=WARN_PHASE2_FALLBACK,
        elapsed_so_far=elapsed1,
        tasks=tasks,
        compat=compat,
    )

    # If Phase 2 fell back to Phase 1, ``solver2`` is actually ``solver1``;
    # the cost objective never converged, so there's no point posting
    # ``total_cost == cost_star`` for Phase 3 — finalise here instead.
    if status2 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        stats["total_solve_time"] = round(elapsed1 + elapsed2, 2)
        return _finalize_result(
            solver2, bundle, tasks, edges, agents, compat, stats, status2, warnings
        )

    cost_star = solver2.value(bundle.total_cost)

    # Phase 3: freeze cost (== for tighter bound propagation), minimise max_load.
    bundle.model.add(bundle.total_cost == cost_star)
    solver_final, final_status, elapsed3 = runner._run_phase(
        bundle,
        config,
        phase=3,
        minimize_expr=bundle.max_load,
        fallback_solver=solver2,
        fallback_status=status2,
        stats=stats,
        warnings=warnings,
        fallback_warning_code=WARN_PHASE3_FALLBACK,
        elapsed_so_far=elapsed1 + elapsed2,
        tasks=tasks,
        compat=compat,
    )
    stats["total_solve_time"] = round(elapsed1 + elapsed2 + elapsed3, 2)

    return _finalize_result(
        solver_final,
        bundle,
        tasks,
        edges,
        agents,
        compat,
        stats,
        final_status,
        warnings,
    )
