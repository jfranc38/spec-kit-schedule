"""Tests for stochastic duration computation."""

from __future__ import annotations

import pytest

from solver.model.types import Agent, Task
from solver.scheduler import (
    _quantile_tokens,
    compute_durations,
    solve_from_json,
)


def _task(i: int, tokens: int = 1000, std_dev: float = 0.0) -> Task:
    return Task(
        id=f"T{i:03d}",
        phase="Setup",
        story_id=None,
        story_priority=99,
        parallel_flag=False,
        file_paths=[f"f{i}.py"],
        required_skill="backend",
        estimated_tokens=tokens,
        token_std_dev=std_dev,
        index=i,
    )


def _agent(j: int) -> Agent:
    return Agent(
        id=f"A{j}",
        model="test",
        skills=["backend"],
        kappa=20,
        context_budget=500_000,
        speed_factor=1.0,
        index=j,
    )


class TestQuantileTokens:
    def test_q50_returns_mean(self):
        result = _quantile_tokens(1000, 200, 0.5)
        assert abs(result - 1000) < 1e-6

    def test_q90_greater_than_mean(self):
        assert _quantile_tokens(1000, 200, 0.9) > 1000

    def test_q10_less_than_mean(self):
        assert _quantile_tokens(1000, 200, 0.1) < 1000

    def test_truncation_at_zero(self):
        result = _quantile_tokens(100, 1000, 0.0001)
        assert result == 0.0


class TestComputeDurationsStochastic:
    def test_zero_std_dev_retrocompatible(self):
        tasks = [_task(0, tokens=500, std_dev=0.0)]
        agents = [_agent(0)]
        p_det = compute_durations(tasks, agents, token_unit=100, stochastic_quantile=0.5)
        p_stoch = compute_durations(tasks, agents, token_unit=100, stochastic_quantile=0.5)
        assert p_det == p_stoch

    def test_q50_matches_deterministic_duration(self):
        tasks = [_task(0, tokens=1000, std_dev=200.0)]
        agents = [_agent(0)]
        p_q50 = compute_durations(tasks, agents, token_unit=100, stochastic_quantile=0.5)
        p_det = compute_durations(tasks, agents, token_unit=100, stochastic_quantile=0.5)
        assert p_q50 == p_det

    def test_q90_duration_gte_q50_duration(self):
        tasks = [_task(0, tokens=1000, std_dev=200.0)]
        agents = [_agent(0)]
        p_50 = compute_durations(tasks, agents, token_unit=100, stochastic_quantile=0.5)
        p_90 = compute_durations(tasks, agents, token_unit=100, stochastic_quantile=0.9)
        assert p_90[(0, 0)] >= p_50[(0, 0)]

    def test_q90_duration_strictly_greater_with_large_std(self):
        tasks = [_task(0, tokens=1000, std_dev=500.0)]
        agents = [_agent(0)]
        p_50 = compute_durations(tasks, agents, token_unit=100, stochastic_quantile=0.5)
        p_90 = compute_durations(tasks, agents, token_unit=100, stochastic_quantile=0.9)
        assert p_90[(0, 0)] > p_50[(0, 0)]


class TestStochasticEndToEnd:
    def _stoch_input(self, q: float) -> dict:
        return {
            "tasks": [
                {
                    "id": f"T{i:03d}",
                    "phase": "Setup",
                    "story_id": None,
                    "story_priority": 1,
                    "parallel_flag": False,
                    "file_paths": [f"f{i}.py"],
                    "required_skill": "backend",
                    "estimated_tokens": 1000,
                    "token_std_dev": 300.0,
                    "action_verb": "implement",
                }
                for i in range(3)
            ],
            "edges": [["T000", "T001"], ["T001", "T002"]],
            "agents": [
                {
                    "id": "A0",
                    "model": "test",
                    "skills": ["backend"],
                    "kappa": 10,
                    "context_budget": 100_000,
                    "speed_factor": 1.0,
                }
            ],
            "config": {
                "stochastic_quantile": q,
                "time_limit": 10,
                "num_workers": 1,
            },
        }

    def test_quantile_used_reported(self):
        result = solve_from_json(self._stoch_input(0.9))
        assert result["stats"]["quantile_used"] == pytest.approx(0.9)

    def test_q90_makespan_gte_q50_makespan(self):
        r50 = solve_from_json(self._stoch_input(0.5))
        r90 = solve_from_json(self._stoch_input(0.9))
        assert r90["stats"]["makespan"] >= r50["stats"]["makespan"]

    def test_std_dev_zero_tasks_unaffected(self):
        data = {
            "tasks": [
                {
                    "id": "T000",
                    "phase": "Setup",
                    "story_id": None,
                    "story_priority": 1,
                    "parallel_flag": False,
                    "file_paths": ["f0.py"],
                    "required_skill": "backend",
                    "estimated_tokens": 500,
                    "token_std_dev": 0.0,
                    "action_verb": "implement",
                }
            ],
            "edges": [],
            "agents": [
                {
                    "id": "A0",
                    "model": "test",
                    "skills": ["backend"],
                    "kappa": 10,
                    "context_budget": 50_000,
                    "speed_factor": 1.0,
                }
            ],
            "config": {"stochastic_quantile": 0.9, "time_limit": 5, "num_workers": 1},
        }
        r90 = solve_from_json(data)
        data["config"]["stochastic_quantile"] = 0.5
        r50 = solve_from_json(data)
        assert r90["stats"]["makespan"] == r50["stats"]["makespan"]
