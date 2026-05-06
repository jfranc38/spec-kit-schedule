"""Direct exercises of the ``preflight_checks`` branches.

``skill_budget_exceeded`` fires when total demand on a single skill
exceeds the combined ``context_budget`` of agents offering that skill,
even though the global budget is comfortably above total demand.
"""

from __future__ import annotations

import pytest

from solver.scheduler import solve_from_json
from solver.validation import ScheduleInputError
from tests._helpers import make_agent, make_solver_input, make_task


def test_skill_budget_exceeded_raises():
    # Total budget 11_000 > total demand 5_000, so global budget passes.
    # But backend-skilled agent only has budget 1_000 < 5_000 demand → fails.
    data = make_solver_input(
        tasks=[
            make_task(
                f"T{i:03d}", required_skill="backend", estimated_tokens=1_000
            )
            for i in range(5)
        ],
        agents=[
            make_agent("A0", skills=["backend"], context_budget=1_000),
            make_agent("A1", skills=["frontend"], context_budget=10_000),
        ],
    )
    with pytest.raises(ScheduleInputError, match="backend"):
        solve_from_json(data)


def test_skill_budget_message_contains_required_and_have():
    data = make_solver_input(
        tasks=[
            make_task("T001", required_skill="backend", estimated_tokens=2_000),
            make_task("T002", required_skill="backend", estimated_tokens=2_000),
        ],
        agents=[
            make_agent("A0", skills=["backend"], context_budget=500),
            make_agent("A1", skills=["frontend"], context_budget=10_000),
        ],
    )
    with pytest.raises(ScheduleInputError) as excinfo:
        solve_from_json(data)
    msg = str(excinfo.value)
    assert "4000" in msg  # required tokens
    assert "500" in msg  # supply
