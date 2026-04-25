"""Tests for cost_aware objective."""

from __future__ import annotations

from solver.scheduler import solve_from_json


def _base_data(*, price_a=0.0, price_b=0.0, objective="lexicographic") -> dict:
    return {
        "tasks": [
            {
                "id": "T001",
                "phase": "Setup",
                "story_id": None,
                "story_priority": 1,
                "parallel_flag": False,
                "file_paths": ["a.py"],
                "required_skill": "backend",
                "estimated_tokens": 500,
                "action_verb": "implement",
            },
            {
                "id": "T002",
                "phase": "Setup",
                "story_id": None,
                "story_priority": 1,
                "parallel_flag": False,
                "file_paths": ["b.py"],
                "required_skill": "backend",
                "estimated_tokens": 500,
                "action_verb": "implement",
            },
        ],
        "edges": [],
        "agents": [
            {
                "id": "A0",
                "model": "test",
                "skills": ["backend"],
                "kappa": 10,
                "context_budget": 20_000,
                "speed_factor": 1.0,
                "price_per_1k_tokens": price_a,
            },
            {
                "id": "A1",
                "model": "test",
                "skills": ["backend"],
                "kappa": 10,
                "context_budget": 20_000,
                "speed_factor": 1.0,
                "price_per_1k_tokens": price_b,
            },
        ],
        "config": {
            "objective": objective,
            "time_limit": 10,
            "num_workers": 1,
        },
    }


class TestCostAwareObjective:
    def test_total_cost_in_stats(self):
        data = _base_data(price_a=2.0, price_b=2.0, objective="cost_aware")
        result = solve_from_json(data)
        assert result["status"] in {"OPTIMAL", "FEASIBLE"}
        assert "total_cost" in result["stats"]
        assert result["stats"]["total_cost"] >= 0.0

    def test_agent_summary_has_cost(self):
        data = _base_data(price_a=2.0, price_b=1.0, objective="cost_aware")
        result = solve_from_json(data)
        for row in result["agent_summary"]:
            assert "cost" in row
            assert isinstance(row["cost"], float)

    def test_cost_computed_from_tokens_and_price(self):
        data = _base_data(price_a=2.0, price_b=0.0, objective="cost_aware")
        result = solve_from_json(data)
        by_agent = {r["agent_id"]: r for r in result["agent_summary"]}
        cost_a0 = by_agent["A0"]["cost"]
        tokens_a0 = by_agent["A0"]["total_tokens"]
        assert abs(cost_a0 - tokens_a0 * 2.0 / 1000) < 1e-6

    def test_equal_prices_same_makespan_as_lexicographic(self):
        data_lex = _base_data(price_a=1.0, price_b=1.0, objective="lexicographic")
        data_cost = _base_data(price_a=1.0, price_b=1.0, objective="cost_aware")
        r_lex = solve_from_json(data_lex)
        r_cost = solve_from_json(data_cost)
        assert r_lex["stats"]["makespan"] == r_cost["stats"]["makespan"]

    def test_cost_aware_prefers_cheaper_agent(self):
        data = {
            "tasks": [
                {
                    "id": "T001",
                    "phase": "Setup",
                    "story_id": None,
                    "story_priority": 1,
                    "parallel_flag": False,
                    "file_paths": ["a.py"],
                    "required_skill": "backend",
                    "estimated_tokens": 1000,
                    "action_verb": "implement",
                }
            ],
            "edges": [],
            "agents": [
                {
                    "id": "cheap",
                    "model": "test",
                    "skills": ["backend"],
                    "kappa": 10,
                    "context_budget": 20_000,
                    "speed_factor": 1.0,
                    "price_per_1k_tokens": 0.5,
                },
                {
                    "id": "expensive",
                    "model": "test",
                    "skills": ["backend"],
                    "kappa": 10,
                    "context_budget": 20_000,
                    "speed_factor": 1.0,
                    "price_per_1k_tokens": 5.0,
                },
            ],
            "config": {
                "objective": "cost_aware",
                "time_limit": 10,
                "num_workers": 1,
            },
        }
        result = solve_from_json(data)
        assert result["status"] in {"OPTIMAL", "FEASIBLE"}
        assignment = result["assignments"][0]
        assert assignment["agent_id"] == "cheap"

    def test_cost_aware_symmetry_keeps_cheaper_later_agent_reachable(self):
        data = {
            "tasks": [
                {
                    "id": "T001",
                    "phase": "Setup",
                    "story_id": None,
                    "story_priority": 1,
                    "parallel_flag": False,
                    "file_paths": ["a.py"],
                    "required_skill": "backend",
                    "estimated_tokens": 1000,
                    "action_verb": "implement",
                }
            ],
            "edges": [],
            "agents": [
                {
                    "id": "expensive",
                    "model": "test",
                    "skills": ["backend"],
                    "kappa": 10,
                    "context_budget": 20_000,
                    "speed_factor": 1.0,
                    "price_per_1k_tokens": 5.0,
                },
                {
                    "id": "cheap",
                    "model": "test",
                    "skills": ["backend"],
                    "kappa": 10,
                    "context_budget": 20_000,
                    "speed_factor": 1.0,
                    "price_per_1k_tokens": 0.5,
                },
            ],
            "config": {
                "objective": "cost_aware",
                "time_limit": 10,
                "num_workers": 1,
                "symmetry_breaking": True,
            },
        }
        result = solve_from_json(data)
        assert result["assignments"][0]["agent_id"] == "cheap"

    def test_total_cost_zero_when_no_pricing(self):
        data = _base_data(price_a=0.0, price_b=0.0, objective="cost_aware")
        result = solve_from_json(data)
        assert result["stats"]["total_cost"] == 0.0

    def test_total_cost_emitted_for_lexicographic_too(self):
        data = _base_data(price_a=1.0, price_b=1.0, objective="lexicographic")
        result = solve_from_json(data)
        assert "total_cost" in result["stats"]
