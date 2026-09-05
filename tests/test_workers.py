"""Zero-config workers mode: identical lanes, wildcard skill, never infeasible."""

from __future__ import annotations

from pathlib import Path

import pytest

from solver.parse_tasks import parse_tasks_md
from solver.scheduler import solve_from_json
from solver.warnings_collector import WarningCollector
from solver.workers import WILDCARD_SKILL, agent_covers, synthesize_workers
from tests._helpers import make_agent, make_solver_input, make_task

FIXTURE = Path(__file__).parent / "fixtures" / "tasks-speckit-0.16.md"


class TestSynthesizeWorkers:
    def test_shape_and_defaults(self) -> None:
        agents = synthesize_workers(10, 35_000)
        assert [a["id"] for a in agents] == ["worker-1", "worker-2", "worker-3"]
        for a in agents:
            assert a["skills"] == [WILDCARD_SKILL]
            assert a["kappa"] == 10  # no cap → n_tasks
            assert a["context_budget"] == 35_000
            assert a["speed_factor"] == 1.0
            assert a["price_per_1k_tokens"] == 0.0

    def test_cap_sets_kappa(self) -> None:
        agents = synthesize_workers(10, 1000, workers=2, max_tasks_per_worker=5)
        assert len(agents) == 2
        assert all(a["kappa"] == 5 for a in agents)

    def test_workers_raised_when_cap_too_small(self) -> None:
        warnings = WarningCollector()
        agents = synthesize_workers(
            10, 1000, workers=2, max_tasks_per_worker=3, warnings=warnings
        )
        assert len(agents) == 4  # ceil(10 / 3)
        assert [w["code"] for w in warnings.as_list()] == ["workers_raised"]
        assert warnings.as_list()[0]["context"]["requested"] == 2
        assert warnings.as_list()[0]["context"]["workers"] == 4

    def test_ids_zero_padded_past_nine(self) -> None:
        agents = synthesize_workers(30, 1000, workers=12)
        assert agents[0]["id"] == "worker-01"
        assert agents[-1]["id"] == "worker-12"

    def test_invalid_arguments(self) -> None:
        with pytest.raises(ValueError):
            synthesize_workers(1, 1, workers=0)
        with pytest.raises(ValueError):
            synthesize_workers(1, 1, max_tasks_per_worker=-1)

    def test_zero_tasks_still_returns_workers(self) -> None:
        assert len(synthesize_workers(0, 0, workers=2)) == 2


class TestAgentCovers:
    def test_wildcard_and_exact(self) -> None:
        assert agent_covers(["*"], "anything")
        assert agent_covers(["backend", "test"], "test")
        assert not agent_covers(["backend"], "test")


class TestParserZeroConfig:
    def test_no_config_synthesises_workers_and_default_rules(self) -> None:
        result = parse_tasks_md(str(FIXTURE), {})
        assert [a["id"] for a in result["agents"]] == ["worker-1", "worker-2", "worker-3"]
        by_id = {t["id"]: t for t in result["tasks"]}
        # Default skill rules apply: test files are recognised (TDD ordering).
        assert by_id["T009"]["required_skill"] == "test"
        assert by_id["T011"]["required_skill"] == "schema"
        total = sum(t["estimated_tokens"] for t in result["tasks"])
        assert result["agents"][0]["context_budget"] == total
        assert result["agents"][0]["kappa"] == len(result["tasks"])

    def test_workers_and_cap_from_config(self) -> None:
        result = parse_tasks_md(
            str(FIXTURE), {"workers": 2, "max_tasks_per_worker": 8}
        )
        # 25 tasks / 8 per worker → 4 lanes, raised from 2 with a warning.
        assert len(result["agents"]) == 4
        assert {w["code"] for w in result["warnings"]} == {"workers_raised"}

    def test_zero_config_solver_defaults(self) -> None:
        result = parse_tasks_md(str(FIXTURE), {})
        cfg = result["config"]
        assert cfg["time_limit"] == 10
        assert cfg["anytime"] is True
        assert 1 <= cfg["num_workers"] <= 8

    def test_user_solver_overrides_win(self) -> None:
        result = parse_tasks_md(str(FIXTURE), {"solver": {"time_limit": 5, "anytime": False}})
        assert result["config"]["time_limit"] == 5
        assert result["config"]["anytime"] is False

    def test_declared_agents_keep_kilotoken_budget(self) -> None:
        cfg = {
            "agents": [
                {"id": "a", "model": "m", "skills": ["*"], "kappa": 50, "context_budget": 16}
            ]
        }
        result = parse_tasks_md(str(FIXTURE), cfg)
        assert result["agents"][0]["context_budget"] == 16_000
        assert "_raw_budget" not in result["agents"][0]


class TestSolveZeroConfig:
    def test_fixture_solves_end_to_end(self) -> None:
        parsed = parse_tasks_md(str(FIXTURE), {"solver": {"time_limit": 3, "num_workers": 1}})
        result = solve_from_json(parsed)
        assert result["status"] in ("OPTIMAL", "FEASIBLE")
        assigned = {a["task_id"] for a in result["assignments"]}
        assert assigned == {t["id"] for t in parsed["tasks"]}
        lanes = {a["agent_id"] for a in result["assignments"]}
        assert lanes <= {"worker-1", "worker-2", "worker-3"}
        assert len(lanes) > 1, "identical workers should share the load"

    def test_wildcard_agent_covers_every_skill(self) -> None:
        tasks = [
            make_task("T001", required_skill="frontend"),
            make_task("T002", required_skill="schema"),
            make_task("T003", required_skill="anything-else"),
        ]
        data = make_solver_input(tasks, [make_agent("w", skills=["*"], kappa=3)])
        result = solve_from_json(data)
        assert result["status"] == "OPTIMAL"
        assert {a["agent_id"] for a in result["assignments"]} == {"w"}

    def test_preflight_counts_wildcard_capacity_per_skill(self) -> None:
        # Two skills, each needing 2 slots; a single wildcard agent with κ=4
        # covers both buckets — no false "kappa exceeded" error.
        tasks = [
            make_task("T001", required_skill="a"),
            make_task("T002", required_skill="a"),
            make_task("T003", required_skill="b"),
            make_task("T004", required_skill="b"),
        ]
        data = make_solver_input(tasks, [make_agent("w", skills=["*"], kappa=4)])
        assert solve_from_json(data)["status"] == "OPTIMAL"
