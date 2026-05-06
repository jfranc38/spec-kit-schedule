"""spec-kit-schedule solver package.

This module exposes the supported library surface. Symbols outside ``__all__``
are internal and may change without notice; importers that reach into private
helpers do so at their own risk.
"""

from .i18n_catalog import (
    WARN_ANYTIME_TIMEOUT,
    WARN_COST_SCALE_UNDERFLOW,
    WARN_PARALLEL_WRITE_CONFLICT,
    WARN_PHASE2_FALLBACK,
    WARN_PHASE3_FALLBACK,
)
from .model.types import Agent, SolverConfig, Task
from .parse_tasks import parse_tasks_md
from .replan import replan
from .scheduler import solve_from_json, solve_with_fixed
from .validation import ScheduleInputError

__version__ = "0.5.1"

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
