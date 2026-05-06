"""Lexicographic two-phase solve: lex(makespan, max_load).

Phase 1 minimises makespan (anytime-capable). Phase 2 freezes the optimal
makespan and minimises max-load (fairness). On Phase 2 timeout we keep the
Phase 1 result and emit ``WARN_PHASE2_FALLBACK``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ortools.sat.python import cp_model

from ..i18n_catalog import WARN_PHASE2_FALLBACK
from ..model.types import Agent, SolverConfig, Task
from ..result.extract import _finalize_result
from ..warnings_collector import WarningCollector
from . import runner

if TYPE_CHECKING:
    from ..model.build import ModelBundle

log = logging.getLogger(__name__)


def _solve_lexicographic(
    bundle: ModelBundle,
    tasks: list[Task],
    edges: list[tuple[int, int]],
    agents: list[Agent],
    compat: dict[int, list[int]],
    config: SolverConfig,
    stats: dict[str, Any],
    warnings: WarningCollector,
) -> dict[str, Any]:
    solver1, status1, _cb = runner._solve_phase1_makespan(bundle, config, stats, warnings)
    log.info(
        "phase=1 status=%s elapsed=%.2fs gap=%.6f",
        solver1.status_name(status1),
        stats.get("phase1_time", 0.0),
        runner._compute_gap(solver1)
        if status1 in (cp_model.OPTIMAL, cp_model.FEASIBLE)
        else 0.0,
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

    # Phase 2: freeze makespan (== for tighter bound propagation), minimise max load.
    # ``_run_phase`` handles clear-objective / rehint / minimise / run /
    # record / fallback; lex only needs to post the freezing constraint.
    bundle.model.add(bundle.makespan == ms_star)
    elapsed1 = float(stats.get("phase1_time", 0.0))
    solver_final, final_status, elapsed2 = runner._run_phase(
        bundle,
        config,
        phase=2,
        minimize_expr=bundle.max_load,
        fallback_solver=solver1,
        fallback_status=status1,
        stats=stats,
        warnings=warnings,
        fallback_warning_code=WARN_PHASE2_FALLBACK,
        elapsed_so_far=elapsed1,
        tasks=tasks,
        compat=compat,
    )
    stats["total_solve_time"] = round(elapsed1 + elapsed2, 2)

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
