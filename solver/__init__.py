"""spec-kit-schedule solver package.

This module exposes the supported library surface. Symbols outside ``__all__``
are internal and may change without notice; importers that reach into private
helpers do so at their own risk.
"""

from typing import TYPE_CHECKING, Any

__version__ = "0.6.2"

__all__ = [
    "Agent",
    "ScheduleInputError",
    "SolverConfig",
    "Task",
    "WARN_ANYTIME_TIMEOUT",
    "WARN_COST_SCALE_UNDERFLOW",
    "WARN_PARALLEL_WRITE_CONFLICT",
    "WARN_PHASE2_FALLBACK",
    "WARN_PHASE3_FALLBACK",
    "__version__",
    "parse_tasks_md",
    "replan",
    "solve_from_json",
    "solve_with_fixed",
]


def __getattr__(name: str) -> Any:
    """Lazy-import public API on first access to avoid eager submodule loading.

    This pattern (PEP 562) keeps ``python -m solver.<submodule>`` from emitting
    "found in sys.modules after import of package 'solver'" RuntimeWarnings,
    while still letting ``from solver import X`` work transparently.
    """
    if name in {"Task", "Agent", "SolverConfig"}:
        from solver.model.types import Agent, SolverConfig, Task

        return {"Task": Task, "Agent": Agent, "SolverConfig": SolverConfig}[name]
    if name == "ScheduleInputError":
        from solver.validation import ScheduleInputError

        return ScheduleInputError
    if name == "parse_tasks_md":
        from solver.parse_tasks import parse_tasks_md

        return parse_tasks_md
    if name == "replan":
        from solver.replan import replan

        return replan
    if name in {"solve_from_json", "solve_with_fixed"}:
        from solver.scheduler import solve_from_json, solve_with_fixed

        return {
            "solve_from_json": solve_from_json,
            "solve_with_fixed": solve_with_fixed,
        }[name]
    if name.startswith("WARN_"):
        from solver import i18n_catalog

        return getattr(i18n_catalog, name)
    raise AttributeError(f"module 'solver' has no attribute {name!r}")


if TYPE_CHECKING:
    # For type-checkers and IDEs.
    from solver.i18n_catalog import (
        WARN_ANYTIME_TIMEOUT,
        WARN_COST_SCALE_UNDERFLOW,
        WARN_PARALLEL_WRITE_CONFLICT,
        WARN_PHASE2_FALLBACK,
        WARN_PHASE3_FALLBACK,
    )
    from solver.model.types import Agent, SolverConfig, Task
    from solver.parse_tasks import parse_tasks_md
    from solver.replan import replan
    from solver.scheduler import solve_from_json, solve_with_fixed
    from solver.validation import ScheduleInputError
