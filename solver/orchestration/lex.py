"""Lexicographic two-phase solve: lex(makespan, max_load).

Phase 1 minimises makespan (anytime-capable). Phase 2 freezes the optimal
makespan and minimises max-load (fairness). On Phase 2 timeout we keep the
Phase 1 result and emit ``WARN_PHASE2_FALLBACK``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ortools.sat.python import cp_model

from ..i18n_catalog import WARN_PHASE2_FALLBACK
from ..model.types import Agent, SolverConfig, Task
from ..warnings_collector import WarningCollector
from . import runner

if TYPE_CHECKING:
    from ..model.build import ModelBundle


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
    solver1, status1, elapsed1 = runner._solve_phase1_makespan(
        bundle, config, stats, warnings
    )

    if status1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return runner._phase1_infeasible_envelope(
            solver1,
            status1,
            horizon=bundle.horizon,
            stats=stats,
            warnings=warnings,
        )

    # Phase 2: freeze makespan (== for tighter bound propagation), minimise max load.
    # ``_run_phase`` handles clear-objective / rehint / minimise / run /
    # record / fallback; lex only needs to post the freezing constraint.
    solver_final, final_status, elapsed2 = runner._freeze_makespan_and_run_phase2(
        bundle,
        solver1,
        status1,
        config,
        minimize_expr=bundle.max_load,
        fallback_warning_code=WARN_PHASE2_FALLBACK,
        stats=stats,
        warnings=warnings,
        elapsed1=elapsed1,
        tasks=tasks,
        compat=compat,
    )

    return runner._finalize_with_total_time(
        solver_final,
        bundle,
        tasks,
        edges,
        agents,
        compat,
        stats,
        final_status,
        warnings,
        elapsed_total=elapsed1 + elapsed2,
    )
