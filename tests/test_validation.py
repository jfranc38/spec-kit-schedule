"""Unit tests for solver.validation."""

from __future__ import annotations

import pytest

from solver.validation import (
    ScheduleInputError,
    find_cycle,
    normalize_path,
    validate_agent_config,
    validate_solver_config,
    validate_solver_input,
)


class TestNormalizePath:
    def test_dot_slash_stripped(self):
        assert normalize_path("./src/a.py") == "src/a.py"

    def test_double_slash_collapsed(self):
        assert normalize_path("src//a.py") == "src/a.py"

    def test_posix_form_preserved(self):
        assert normalize_path("src/a.py") == "src/a.py"

    def test_parent_collapsed(self):
        assert normalize_path("src/foo/../a.py") == "src/a.py"

    def test_empty_roundtrips(self):
        assert normalize_path("") == ""


class TestFindCycle:
    def test_acyclic_chain(self):
        assert find_cycle(3, [(0, 1), (1, 2)]) is None

    def test_direct_cycle(self):
        cycle = find_cycle(2, [(0, 1), (1, 0)])
        assert cycle is not None
        assert cycle[0] == cycle[-1]

    def test_three_cycle(self):
        cycle = find_cycle(3, [(0, 1), (1, 2), (2, 0)])
        assert cycle is not None
        assert set(cycle[:-1]) == {0, 1, 2}

    def test_disconnected_acyclic(self):
        assert find_cycle(4, [(0, 1), (2, 3)]) is None


class TestAgentConfig:
    def test_minimal_valid_agent_passes(self):
        validate_agent_config(
            {
                "id": "a",
                "skills": ["backend"],
                "kappa": 5,
                "context_budget": 16,
                "speed_factor": 1.0,
            }
        )

    def test_missing_id_raises(self):
        with pytest.raises(ScheduleInputError):
            validate_agent_config({"skills": ["x"]})

    def test_empty_skills_raises(self):
        with pytest.raises(ScheduleInputError):
            validate_agent_config({"id": "a", "skills": []})

    @pytest.mark.parametrize("field", ["kappa", "context_budget", "speed_factor"])
    def test_non_positive_raises(self, field):
        agent = {"id": "a", "skills": ["x"], "kappa": 5, "context_budget": 16, "speed_factor": 1.0}
        agent[field] = 0
        with pytest.raises(ScheduleInputError):
            validate_agent_config(agent)


class TestSolverConfig:
    def test_unknown_objective_raises(self):
        with pytest.raises(ScheduleInputError):
            validate_solver_config({"objective": "random"})

    def test_negative_time_limit_raises(self):
        with pytest.raises(ScheduleInputError):
            validate_solver_config({"time_limit": -1})

    def test_all_defaults_accepted(self):
        validate_solver_config({})


class TestSolverInput:
    def _base(self):
        return {
            "tasks": [{"id": "T001", "estimated_tokens": 500}],
            "edges": [],
            "agents": [
                {
                    "id": "a",
                    "skills": ["backend"],
                    "kappa": 1,
                    "context_budget": 1000,
                    "speed_factor": 1.0,
                }
            ],
            "config": {},
        }

    def test_complete_input_passes_validation(self):
        validate_solver_input(self._base())

    def test_missing_top_level_key_raises(self):
        data = self._base()
        del data["tasks"]
        with pytest.raises(ScheduleInputError):
            validate_solver_input(data)

    def test_duplicate_task_id_raises(self):
        data = self._base()
        data["tasks"].append({"id": "T001", "estimated_tokens": 100})
        with pytest.raises(ScheduleInputError):
            validate_solver_input(data)

    def test_unknown_edge_endpoint_raises(self):
        data = self._base()
        data["edges"].append(["T001", "T999"])
        with pytest.raises(ScheduleInputError):
            validate_solver_input(data)

    def test_empty_agents_raises(self):
        data = self._base()
        data["agents"] = []
        with pytest.raises(ScheduleInputError):
            validate_solver_input(data)
