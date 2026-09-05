"""Zero-config portfolio: N identical workers.

When ``schedule-config.yml`` declares no ``agents:`` block (or the file
does not exist at all), the scheduler runs against ``workers`` identical
lanes. Each lane is one subagent of the user's AI assistant: same model,
any task (wildcard skill), no context-budget cap. The only optional knob
is ``max_tasks_per_worker`` — a cardinality cap that keeps every
subagent's context small; when it is too small for the task count the
worker count is raised (never lowered) so the plan is always feasible.
"""

from __future__ import annotations

__all__ = ["WILDCARD_SKILL", "agent_covers", "is_synthesized_worker", "synthesize_workers"]

import math
from typing import Any

from .defaults import (
    MAX_TASKS_PER_WORKER_DEFAULT,
    SUBAGENT_MODEL,
    WILDCARD_SKILL,
    WORKERS_DEFAULT,
)
from .i18n import t
from .i18n_catalog import WARN_WORKERS_RAISED
from .warnings_collector import WarningCollector


def agent_covers(skills: list[str], required: str) -> bool:
    """True when an agent with *skills* may run a task requiring *required*."""
    return WILDCARD_SKILL in skills or required in skills


def is_synthesized_worker(skills: list[str], model: str) -> bool:
    """True for a lane produced by :func:`synthesize_workers` (zero-config)."""
    return model == SUBAGENT_MODEL and WILDCARD_SKILL in skills


def synthesize_workers(
    n_tasks: int,
    total_tokens: int,
    *,
    workers: int = WORKERS_DEFAULT,
    max_tasks_per_worker: int = MAX_TASKS_PER_WORKER_DEFAULT,
    warnings: WarningCollector | None = None,
) -> list[dict[str, Any]]:
    """Return ``workers`` identical agent dicts in the parser's output shape.

    ``kappa`` is ``max_tasks_per_worker`` when set, otherwise ``n_tasks``
    (no cap). ``context_budget`` is the total token estimate of the
    feature — every lane could absorb the whole feature, so the budget
    constraint can never bind. If ``workers × cap < n_tasks`` the worker
    count is raised to ``ceil(n_tasks / cap)`` and a warning is recorded.
    """
    if workers < 1:
        raise ValueError("workers must be >= 1")
    if max_tasks_per_worker < 0:
        raise ValueError("max_tasks_per_worker must be >= 0")

    cap = max_tasks_per_worker or max(1, n_tasks)
    needed = math.ceil(n_tasks / cap) if n_tasks else 1
    if needed > workers:
        if warnings is not None:
            warnings.add(
                WARN_WORKERS_RAISED,
                t(
                    WARN_WORKERS_RAISED,
                    requested=workers,
                    cap=cap,
                    n_tasks=n_tasks,
                    workers=needed,
                ),
                requested=workers,
                workers=needed,
                cap=cap,
                n_tasks=n_tasks,
            )
        workers = needed

    # Budget in raw tokens (the parser multiplies kilotokens by 1000 for
    # declared agents; synthesized workers are already in raw tokens).
    budget = max(1, total_tokens)
    width = len(str(workers))
    return [
        {
            "id": f"worker-{i:0{width}d}",
            "model": SUBAGENT_MODEL,
            "skills": [WILDCARD_SKILL],
            "kappa": cap,
            "context_budget": budget,
            "speed_factor": 1.0,
            "price_per_1k_tokens": 0.0,
        }
        for i in range(1, workers + 1)
    ]
