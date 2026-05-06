"""Schema invariants for the top-level :class:`ScheduleResult` envelope.

The renderers consume the same JSON dict that ``solve_from_json`` returns, but
programmatic users frequently pull scalar metrics straight off the result
without reaching into ``stats``. These tests pin the contract documented in
:mod:`solver.model.result_types` so the mirrored fields stay in sync with
``stats`` on every solve path.
"""

from __future__ import annotations

from solver.scheduler import solve_from_json
from tests._helpers import (
    TERMINAL_STATUSES,
    make_agent,
    make_solver_input,
    make_task,
)


def test_result_top_level_has_makespan_and_max_load() -> None:
    """Programmatic API surface: makespan + max_load mirrored at top level."""
    data = make_solver_input(
        tasks=[make_task("T001"), make_task("T002")],
        agents=[make_agent("A0")],
    )
    result = solve_from_json(data)

    assert result["status"] in TERMINAL_STATUSES
    assert "makespan" in result
    assert result["makespan"] == result["stats"]["makespan"]
    assert "max_load" in result
    assert result["max_load"] == result["stats"]["max_load"]


def test_cost_aware_total_cost_at_top_level() -> None:
    """Programmatic API surface: total_cost mirrored when cost-aware mode runs."""
    data = make_solver_input(
        tasks=[
            make_task("T001", estimated_tokens=500),
            make_task("T002", estimated_tokens=500),
        ],
        agents=[
            make_agent("A0", price_per_1k_tokens=2.0),
            make_agent("A1", price_per_1k_tokens=1.0),
        ],
        config={"objective": "cost_aware"},
    )
    result = solve_from_json(data)

    assert result["status"] in TERMINAL_STATUSES
    assert "total_cost" in result
    assert result["total_cost"] == result["stats"]["total_cost"]


def test_total_cost_mirrored_under_default_objective() -> None:
    """``total_cost`` is always derived from agent_summary, not just cost-aware."""
    data = make_solver_input(
        tasks=[make_task("T001")],
        agents=[make_agent("A0", price_per_1k_tokens=1.5)],
    )
    result = solve_from_json(data)

    assert result["status"] in TERMINAL_STATUSES
    assert "total_cost" in result
    assert result["total_cost"] == result["stats"]["total_cost"]
