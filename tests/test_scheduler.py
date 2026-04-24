"""Unit tests for solver.scheduler."""
from __future__ import annotations

import pytest

from solver.scheduler import (
    Agent,
    Task,
    build_file_conflict_groups,
    compute_compatible_agents,
    compute_durations,
    critical_path_bound,
    list_schedule_heuristic,
    solve_from_json,
)
from solver.validation import ScheduleInputError


def _task(i: int, skill: str = "backend", tokens: int = 500, files=(), parallel=False) -> Task:
    return Task(
        id=f"T{i:03d}", phase="Setup", story_id=None, story_priority=99,
        parallel_flag=parallel, file_paths=list(files),
        required_skill=skill, estimated_tokens=tokens, action_verb="implement",
        index=i,
    )


def _agent(j: int, skills=("backend",), kappa=10, budget=100_000, speed=1.0) -> Agent:
    return Agent(
        id=f"A{j}", model="test", skills=list(skills),
        kappa=kappa, context_budget=budget, speed_factor=speed, index=j,
    )


class TestCompat:
    def test_match(self):
        tasks = [_task(0, "backend")]
        agents = [_agent(0)]
        assert compute_compatible_agents(tasks, agents) == {0: [0]}

    def test_no_match_raises(self):
        tasks = [_task(0, "unicorn")]
        agents = [_agent(0)]
        with pytest.raises(ScheduleInputError):
            compute_compatible_agents(tasks, agents)


class TestDurations:
    def test_ceil_rounding(self):
        tasks = [_task(0, tokens=150)]
        agents = [_agent(0)]
        p = compute_durations(tasks, agents, token_unit=100)
        assert p[(0, 0)] == 2  # ceil(150/100) = 2

    def test_speed_factor_applied(self):
        tasks = [_task(0, tokens=1000)]
        agents = [_agent(0, speed=2.0)]
        p = compute_durations(tasks, agents, token_unit=100)
        assert p[(0, 0)] == 5  # ceil(10 / 2.0) = 5


class TestFileConflicts:
    def test_two_non_parallel_tasks_same_file(self):
        tasks = [
            _task(0, files=["x.py"]),
            _task(1, files=["x.py"]),
        ]
        groups = build_file_conflict_groups(tasks)
        assert groups == {"x.py": [0, 1]}

    def test_parallel_flag_exempts(self):
        tasks = [
            _task(0, files=["x.py"], parallel=True),
            _task(1, files=["x.py"], parallel=True),
        ]
        assert build_file_conflict_groups(tasks) == {}


class TestCriticalPath:
    def test_chain(self):
        min_dur = dict.fromkeys(range(3), 5)
        assert critical_path_bound(3, [(0, 1), (1, 2)], min_dur) == 15

    def test_parallel_paths(self):
        # 0→1, 0→2 → bound = 10 + max(5,5) = 15
        min_dur = {0: 10, 1: 5, 2: 5}
        assert critical_path_bound(3, [(0, 1), (0, 2)], min_dur) == 15


class TestListSchedule:
    def test_respects_precedence(self):
        tasks = [_task(i) for i in range(2)]
        agents = [_agent(0)]
        compat = {0: [0], 1: [0]}
        p = {(0, 0): 5, (1, 0): 3}
        min_dur = {0: 5, 1: 3}
        result = list_schedule_heuristic(
            tasks, [(0, 1)], agents, compat, p, min_dur, file_conflicts={}
        )
        assert result[1][1] >= result[0][1] + 5

    def test_respects_file_mutex(self):
        tasks = [
            _task(0, files=["x.py"]),
            _task(1, files=["x.py"]),
        ]
        agents = [_agent(0), _agent(1)]
        compat = {0: [0, 1], 1: [0, 1]}
        p = {(i, j): 3 for i in range(2) for j in range(2)}
        min_dur = {0: 3, 1: 3}
        file_conflicts = {"x.py": [0, 1]}
        result = list_schedule_heuristic(
            tasks, [], agents, compat, p, min_dur, file_conflicts=file_conflicts
        )
        s0 = result[0][1]
        s1 = result[1][1]
        assert abs(s0 - s1) >= 3


class TestSolveFromJson:
    def _base_input(self):
        return {
            "tasks": [
                {"id": "T001", "phase": "Setup", "story_id": None,
                 "story_priority": 99, "parallel_flag": False,
                 "file_paths": ["a.py"], "required_skill": "backend",
                 "estimated_tokens": 500, "action_verb": "implement"},
                {"id": "T002", "phase": "Setup", "story_id": None,
                 "story_priority": 99, "parallel_flag": False,
                 "file_paths": ["b.py"], "required_skill": "backend",
                 "estimated_tokens": 500, "action_verb": "implement"},
            ],
            "edges": [["T001", "T002"]],
            "agents": [{
                "id": "backend", "model": "test", "skills": ["backend"],
                "kappa": 5, "context_budget": 10_000, "speed_factor": 1.0,
            }],
            "config": {"time_limit": 5, "num_workers": 1},
        }

    def test_happy_path(self):
        result = solve_from_json(self._base_input())
        assert result["status"] in {"OPTIMAL", "FEASIBLE"}
        assert len(result["assignments"]) == 2
        # Chain is enforced.
        by_id = {a["task_id"]: a for a in result["assignments"]}
        assert by_id["T001"]["end"] <= by_id["T002"]["start"]

    def test_missing_skill_raises(self):
        data = self._base_input()
        data["tasks"][0]["required_skill"] = "frontend"
        with pytest.raises(ScheduleInputError, match="skill"):
            solve_from_json(data)

    def test_over_budget_raises(self):
        data = self._base_input()
        data["tasks"][0]["estimated_tokens"] = 100_000
        with pytest.raises(ScheduleInputError, match="Infeasible"):
            solve_from_json(data)

    def test_cycle_in_edges_raises(self):
        data = self._base_input()
        data["edges"].append(["T002", "T001"])
        with pytest.raises(ScheduleInputError, match="cycle"):
            solve_from_json(data)

    def test_kappa_exceeded_raises(self):
        data = self._base_input()
        data["agents"][0]["kappa"] = 1  # only 1 allowed but 2 tasks
        with pytest.raises(ScheduleInputError, match="κ"):
            solve_from_json(data)

    def test_output_contains_edges_and_warnings(self):
        result = solve_from_json(self._base_input())
        assert "edges" in result
        assert result["edges"] == [["T001", "T002"]]
        assert "warnings" in result

    def test_critical_path_reported(self):
        data = self._base_input()
        result = solve_from_json(data)
        # T001 → T002 chain → critical path covers both tasks end-to-end.
        assert result["critical_path"] == ["T001", "T002"]
        # New invariants added for the DAG renderer: the output must expose
        # every arc on the chain plus any resource-induced arcs so the
        # visualiser does not have to re-derive them.
        assert ["T001", "T002"] in result["critical_path_edges"]
        assert "resource_edges" in result
        # Every critical-chain arc is reachable as a drawable edge (either
        # a parser edge or a resource-induced edge).
        edge_pool = {tuple(e) for e in result["edges"]} | {
            tuple(e) for e in result["resource_edges"]
        }
        for arc in result["critical_path_edges"]:
            assert tuple(arc) in edge_pool, (
                f"Critical arc {arc} missing from edges + resource_edges"
            )

    def test_critical_path_matches_makespan(self):
        data = self._base_input()
        data["tasks"].append({
            "id": "T003", "phase": "Setup", "story_id": None,
            "story_priority": 99, "parallel_flag": False,
            "file_paths": ["c.py"], "required_skill": "backend",
            "estimated_tokens": 100, "action_verb": "implement",
        })
        result = solve_from_json(data)
        by_id = {a["task_id"]: a for a in result["assignments"]}
        chain_duration = sum(by_id[t]["duration"] for t in result["critical_path"])
        assert chain_duration == result["stats"]["makespan"]

    def test_file_mutex_forces_sequential(self):
        data = self._base_input()
        data["tasks"][0]["file_paths"] = ["shared.py"]
        data["tasks"][1]["file_paths"] = ["shared.py"]
        data["edges"] = []  # drop precedence; file-mutex is the only constraint
        result = solve_from_json(data)
        starts = [(a["start"], a["end"]) for a in result["assignments"]]
        starts.sort()
        # Intervals must not overlap.
        assert starts[0][1] <= starts[1][0]
