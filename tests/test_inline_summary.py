"""Tests for the inline schedule summary renderer.

The module is a pure function over the result envelope returned by
``solve_from_json``. Coverage targets the contract enumerated in the
v0.6.x build-3a spec: optimal / cost-aware / infeasible / anytime-gap
shapes, defensive handling of missing fields, column alignment under
long agent ids, and the terminal-safe (no-markdown) constraint.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from solver.result.summary import format_inline_summary

REPO_ROOT = Path(__file__).resolve().parents[1]


# ───────────────────────────────────────────────────────────────────────
# Fixture builders
# ───────────────────────────────────────────────────────────────────────


def _example_result(name: str) -> dict[str, Any]:
    """Load one of the canonical example out.json files."""
    path = REPO_ROOT / "examples" / name / "expected" / "out.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _minimal_optimal_result() -> dict[str, Any]:
    """Hand-crafted minimal OPTIMAL envelope for the alignment / gap tests."""
    return {
        "status": "OPTIMAL",
        "stats": {
            "status": "OPTIMAL",
            "makespan": 100,
            "total_tasks": 2,
            "total_agents": 2,
            "total_waves": 2,
            "total_cost": 1.5,
            "total_solve_time": 0.05,
            "phase1_status": "OPTIMAL",
            "phase2_status": "OPTIMAL",
        },
        "agent_summary": [
            {
                "agent_id": "alpha",
                "model": "test",
                "provider": "anthropic",
                "task_count": 2,
                "total_tokens": 1000,
                "kappa_utilization": 50.0,
                "cost": 1.5,
            },
            {
                "agent_id": "beta",
                "model": "test",
                "task_count": 0,
                "total_tokens": 0,
                "kappa_utilization": 0.0,
                "cost": 0.0,
            },
        ],
        "waves": [
            {
                "wave": 1,
                "start_time": 0,
                "tasks": [
                    {
                        "task_id": "T001",
                        "duration": 50,
                        "required_skill": "backend",
                    }
                ],
            },
            {
                "wave": 2,
                "start_time": 50,
                "tasks": [
                    {
                        "task_id": "T002",
                        "duration": 50,
                        "required_skill": "test",
                    }
                ],
            },
        ],
        "critical_path": ["T001", "T002"],
        "makespan": 100,
        "total_cost": 1.5,
    }


# ───────────────────────────────────────────────────────────────────────
# Section-presence tests
# ───────────────────────────────────────────────────────────────────────


def test_optimal_lex_result_has_all_sections() -> None:
    out = format_inline_summary(_minimal_optimal_result(), feature_name="demo")
    assert "Schedule — demo" in out
    assert "OPTIMAL" in out
    assert "Makespan:" in out
    assert "Waves:" in out
    assert "Agents:" in out
    assert "Critical-path waves" in out
    assert "Agent utilization" in out
    assert "Full report: schedule.md" in out


def test_cost_aware_shows_cost_split() -> None:
    result = _example_result("02-cost-aware")
    out = format_inline_summary(result, feature_name="cost-aware-demo")
    assert "Cost split:" in out
    assert "cost-aware mode on" in out
    # Both contributors should appear in the split.
    assert "cheap" in out
    assert "balanced" in out


def test_lex_mode_marks_cost_aware_off() -> None:
    out = format_inline_summary(_minimal_optimal_result())
    assert "cost-aware mode off" in out
    # Lex output omits the explicit Cost split line.
    assert "Cost split:" not in out


def test_infeasible_shows_diagnostic() -> None:
    result = {
        "status": "INFEASIBLE",
        "message": "Phase-1 model proven infeasible at horizon=120.",
        "stats": {"horizon": 120},
    }
    out = format_inline_summary(result, feature_name="oops")
    assert "INFEASIBLE" in out
    assert "Phase-1 model proven infeasible at horizon=120." in out
    assert "Common fixes" in out


def test_anytime_gap_displayed() -> None:
    base = _minimal_optimal_result()
    base["status"] = "FEASIBLE"
    base["stats"]["status"] = "FEASIBLE"
    base["stats"]["final_gap"] = 0.042
    out = format_inline_summary(base)
    assert "Gap:" in out
    assert "4.2%" in out
    assert "anytime mode" in out


def test_zero_gap_is_not_rendered() -> None:
    base = _minimal_optimal_result()
    base["stats"]["final_gap"] = 0.0
    out = format_inline_summary(base)
    assert "Gap:" not in out


def test_idle_agents_listed_with_zero_utilization() -> None:
    result = _example_result("02-cost-aware")
    out = format_inline_summary(result)
    # The "premium" agent has zero tasks in this fixture and must still appear.
    assert "premium" in out
    assert "0 tasks" in out


def test_missing_fields_does_not_raise() -> None:
    out = format_inline_summary({"status": "OPTIMAL"})
    assert isinstance(out, str)
    assert out
    # Header still rendered; sections degrade gracefully.
    assert "OPTIMAL" in out


def test_unknown_status_returns_string() -> None:
    out = format_inline_summary({"status": "UNKNOWN"})
    assert "UNKNOWN" in out
    assert "No assignments produced" in out


def test_summary_is_under_40_lines() -> None:
    out = format_inline_summary(_example_result("04-multi-provider"), feature_name="mp")
    assert len(out.splitlines()) < 40


def test_columns_align_with_long_agent_ids() -> None:
    base = _minimal_optimal_result()
    base["agent_summary"][0]["agent_id"] = "x" * 30
    out = format_inline_summary(base)
    util_lines = [
        line
        for line in out.splitlines()
        if "tasks" in line and "tok" in line and "$" in line
    ]
    assert util_lines, "expected at least one utilisation line"
    # All utilisation lines should share the same column for the ' tasks |' marker.
    positions = {line.index(" tasks |") for line in util_lines}
    assert len(positions) == 1, f"misaligned columns: {positions}"


def test_summary_uses_no_markdown() -> None:
    out = format_inline_summary(_example_result("04-multi-provider"), feature_name="mp")
    assert "**" not in out
    # Hash is an extremely common markdown header marker; the summary must
    # avoid leading "# " headings entirely.
    for line in out.splitlines():
        assert not line.lstrip().startswith("# ")


def test_multi_provider_top_three_critical_waves() -> None:
    result = _example_result("04-multi-provider")
    out = format_inline_summary(result, feature_name="mp")
    section = out.split("Critical-path waves")[1]
    # Section block ends at the next blank line; restrict counting to that block.
    block = section.split("\n\n", 1)[0]
    wave_lines = [line for line in block.splitlines() if line.strip().startswith("Wave ")]
    assert len(wave_lines) == 3


def test_cost_split_skipped_when_total_zero() -> None:
    result = _example_result("02-cost-aware")
    # Force a cost-aware-shaped result with zero cost: split should not appear.
    for ag in result["agent_summary"]:
        ag["cost"] = 0.0
    result["stats"]["total_cost"] = 0.0
    result["total_cost"] = 0.0
    out = format_inline_summary(result)
    assert "Cost split:" not in out
    # Mode flag is still surfaced because phase3 ran.
    assert "cost-aware mode on" in out


@pytest.mark.parametrize("example", ["02-cost-aware", "04-multi-provider"])
def test_known_example_fixtures_render(example: str) -> None:
    out = format_inline_summary(_example_result(example), feature_name=example)
    assert "OPTIMAL" in out
    assert "Full report: schedule.md" in out
