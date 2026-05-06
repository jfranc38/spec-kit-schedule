"""Typed shape of the solver result envelope.

The renderers (markdown, HTML, image) consume the same JSON dict that
:func:`solver.scheduler.solve_from_json` produces. ``ScheduleResult`` codifies
that contract so renderer entry points cannot drift from the solver schema
and so future renderer authors have one place to look up the keys.

Documentation-only: implementations across the package return
``dict[str, Any]`` because mypy's TypedDict-vs-``dict[str, Any]`` variance
rules reject the literal envelopes built inside ``_finalize_result`` (the
inner ``stats`` is a plain dict and the ``Stats`` TypedDict slot does not
accept it). Treat ``ScheduleResult`` as the schema reference; the runtime
annotation stays as ``dict[str, Any]`` end-to-end.

Defined as a :class:`TypedDict` (``total=False``) so legacy callers that build
the dict piecemeal keep type-checking; the keys listed here document what
every renderer is allowed to read.
"""

from __future__ import annotations

from typing import Any, TypedDict

__all__ = [
    "Assignment",
    "AgentSummary",
    "ScheduleResult",
    "Stats",
    "WaveBlock",
    "WarningRecord",
]


class Assignment(TypedDict, total=False):
    """One scheduled task as emitted by ``_extract_assignments``."""

    task_id: str
    task_index: int
    agent_id: str
    agent_index: int | None
    start: int
    end: int
    duration: int
    phase: str
    story_id: str | None
    story_priority: int
    file_paths: list[str]
    tokens: int
    required_skill: str


class AgentSummary(TypedDict, total=False):
    """Per-agent rollup as emitted by ``_build_agent_summary``."""

    agent_id: str
    model: str
    provider: str
    task_count: int
    total_tokens: int
    budget_utilization: float
    total_load: int
    kappa_utilization: float
    cost: float
    tasks: list[str]


class WaveBlock(TypedDict):
    """One barrier-separated wave in the schedule."""

    wave: int
    start_time: int
    tasks: list[Assignment]


class WarningRecord(TypedDict, total=False):
    """Structured warning surfaced by the solver."""

    code: str
    message: str
    context: dict[str, Any]


class Stats(TypedDict, total=False):
    """Solver statistics block.

    Marked ``total=False`` because not every key is populated on every
    solve path (e.g. ``phase3_time`` only appears in cost-aware mode and
    ``intermediate`` only in anytime mode).
    """

    status: str
    makespan: int
    max_load: int
    min_load: int
    total_tasks: int
    total_agents: int
    total_waves: int
    total_cost: float
    horizon: int
    quantile_used: float
    phase1_time: float
    phase1_status: str
    phase1_status_code: int
    phase2_time: float
    phase2_status: str
    phase2_status_code: int
    phase3_time: float
    phase3_status: str
    phase3_status_code: int
    total_solve_time: float
    makespan_phase1: int
    heuristic_makespan: int
    final_gap: float
    intermediate: list[dict[str, Any]]
    replan: dict[str, Any]


class ScheduleResult(TypedDict, total=False):
    """Top-level schedule result envelope consumed by the renderers.

    ``total=False`` because the INFEASIBLE path skips many keys; the
    happy path always populates ``status``, ``assignments``, ``waves``,
    ``agent_summary``, ``stats``, and ``warnings``.

    ``makespan``, ``max_load``, and ``total_cost`` are mirrored at the
    top level from ``stats`` for the convenience of programmatic
    consumers; ``stats`` remains the canonical per-phase block. See
    :func:`solver.result.extract._finalize_result`.
    """

    status: str
    message: str
    assignments: list[Assignment]
    waves: list[WaveBlock]
    agent_summary: list[AgentSummary]
    critical_path: list[str]
    critical_path_edges: list[list[str]]
    resource_edges: list[list[str]]
    stats: Stats
    warnings: list[WarningRecord]
    edges: list[list[str]]
    tasks: list[dict[str, Any]]
    agents: list[dict[str, Any]]
    makespan: int
    max_load: int
    total_cost: float
