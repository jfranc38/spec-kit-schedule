"""schedule.md and inline summary: Execution Rounds, speedup, workers mode."""

from __future__ import annotations

from pathlib import Path

import pytest

from solver.parse_tasks import parse_tasks_md
from solver.render_schedule import render
from solver.result.summary import format_inline_summary
from solver.scheduler import solve_from_json
from solver.wave_executor import parse_schedule_md

FIXTURE = Path(__file__).parent / "fixtures" / "tasks-speckit-0.16.md"


@pytest.fixture(scope="module")
def zero_config_result() -> dict:
    parsed = parse_tasks_md(str(FIXTURE), {"solver": {"time_limit": 3, "num_workers": 1}})
    return solve_from_json(parsed)


@pytest.fixture(scope="module")
def advanced_result() -> dict:
    cfg = {
        "agents": [
            {"id": "backend", "model": "m", "skills": ["*"], "kappa": 30, "context_budget": 200},
            {"id": "tester", "model": "m", "skills": ["*"], "kappa": 30, "context_budget": 200},
        ],
        "solver": {"time_limit": 10, "num_workers": 1},
    }
    return solve_from_json(parse_tasks_md(str(FIXTURE), cfg))


class TestScheduleMarkdown:
    def test_rounds_section_precedes_assignments(self, zero_config_result: dict) -> None:
        out = render(zero_config_result, "demo")
        assert "## Execution Rounds" in out
        assert out.index("## Execution Rounds") < out.index("## Agent Assignments")
        assert "/speckit-schedule-implement" in out
        for rnd in zero_config_result["rounds"]:
            assert f"### Round {rnd['round']} — " in out
            for lane in rnd["lanes"]:
                assert f"| {lane['agent_id']} | {' → '.join(lane['tasks'])} |" in out

    def test_header_carries_speedup(self, zero_config_result: dict) -> None:
        out = render(zero_config_result, "demo")
        stats = zero_config_result["stats"]
        assert f"Rounds: **{stats['total_rounds']}**" in out
        assert f"Speedup: **{stats['speedup']}×**" in out
        assert "| Parallel speedup |" in out

    def test_workers_mode_hides_budget_utilisation(self, zero_config_result: dict) -> None:
        out = render(zero_config_result, "demo")
        assert zero_config_result["stats"]["portfolio_mode"] == "workers"
        assert "% budget" not in out
        assert "### worker-1 (subagent) — " in out

    def test_advanced_mode_keeps_budget_utilisation(self, advanced_result: dict) -> None:
        out = render(advanced_result, "demo")
        assert advanced_result["stats"]["portfolio_mode"] == "agents"
        assert "% budget" in out

    def test_critical_path_table_has_descriptions(self, zero_config_result: dict) -> None:
        out = render(zero_config_result, "demo")
        first = zero_config_result["critical_path"][0]
        desc = next(t["description"] for t in zero_config_result["tasks"] if t["id"] == first)
        assert "| Description |" in out
        assert desc in out

    def test_wave_executor_still_parses(self, zero_config_result: dict, tmp_path: Path) -> None:
        path = tmp_path / "schedule.md"
        path.write_text(render(zero_config_result, "demo"), encoding="utf-8")
        plan = parse_schedule_md(path)
        assert plan.feature_name == "demo"
        assert {a.id for a in plan.agents} == {"worker-1", "worker-2", "worker-3"}

    def test_legacy_result_without_rounds_still_renders(self) -> None:
        data = {
            "status": "OPTIMAL",
            "stats": {"status": "OPTIMAL", "makespan": 5, "total_waves": 1, "total_agents": 1},
            "assignments": [
                {"task_id": "T001", "agent_id": "a", "start": 0, "end": 5, "duration": 5,
                 "phase": "Setup", "file_paths": [], "tokens": 100}
            ],
            "waves": [{"wave": 1, "start_time": 0, "tasks": [
                {"task_id": "T001", "agent_id": "a", "duration": 5, "phase": "Setup",
                 "file_paths": []}]}],
            "agent_summary": [{"agent_id": "a", "model": "m", "task_count": 1,
                               "total_tokens": 100}],
        }
        out = render(data, "legacy")
        assert "## Execution Rounds" not in out
        assert "## Agent Assignments" in out


class TestInlineSummary:
    def test_headline_has_rounds_speedup_and_critical_path(
        self, zero_config_result: dict
    ) -> None:
        out = format_inline_summary(zero_config_result, feature_name="demo")
        stats = zero_config_result["stats"]
        assert f"Rounds:    {stats['total_rounds']} parallel rounds × 3 workers" in out
        assert f"Speedup:   {stats['speedup']}×" in out
        assert "Critical path (" in out
        assert " → ".join(zero_config_result["critical_path"][:2]) in out
        assert "Next: /speckit-schedule-implement" in out
        assert "**" not in out

    def test_agents_word_in_advanced_mode(self, advanced_result: dict) -> None:
        out = format_inline_summary(advanced_result)
        assert "× 2 agents" in out

    def test_summary_without_rounds_omits_lines(self) -> None:
        out = format_inline_summary({"status": "OPTIMAL", "stats": {"status": "OPTIMAL"}})
        assert "Rounds:" not in out
        assert "Speedup:" not in out
        assert "Critical path (" not in out
