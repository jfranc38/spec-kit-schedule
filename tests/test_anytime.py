"""Tests for anytime solve mode."""

from __future__ import annotations

from solver.scheduler import solve_from_json


def _base_data(*, anytime: bool = False, objective: str = "lexicographic") -> dict:
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
                "estimated_tokens": 500,
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
                "context_budget": 50_000,
                "speed_factor": 1.0,
            }
        ],
        "config": {
            "anytime": anytime,
            "objective": objective,
            "time_limit": 10,
            "num_workers": 1,
        },
    }


class TestAnytimeCallback:
    def test_intermediate_present_when_anytime_enabled(self):
        data = _base_data(anytime=True)
        result = solve_from_json(data)
        assert result["status"] in {"OPTIMAL", "FEASIBLE"}
        assert "intermediate" in result["stats"]

    def test_intermediate_absent_when_anytime_disabled(self):
        data = _base_data(anytime=False)
        result = solve_from_json(data)
        assert "intermediate" not in result["stats"]

    def test_callback_records_at_least_one_improvement_on_optimal(self):
        data = _base_data(anytime=True)
        result = solve_from_json(data)
        if result["status"] == "OPTIMAL":
            assert len(result["stats"]["intermediate"]) >= 1

    def test_intermediate_entries_have_required_keys(self):
        data = _base_data(anytime=True)
        result = solve_from_json(data)
        for entry in result["stats"].get("intermediate", []):
            assert "makespan" in entry
            assert "time" in entry
            assert "gap" in entry
            assert isinstance(entry["makespan"], int)
            assert entry["time"] >= 0.0
            assert 0.0 <= entry["gap"] <= 1.0 + 1e-6

    def test_intermediate_makespans_non_increasing(self):
        data = _base_data(anytime=True)
        result = solve_from_json(data)
        intermediates = result["stats"].get("intermediate", [])
        makespans = [e["makespan"] for e in intermediates]
        for a, b in zip(makespans, makespans[1:], strict=False):
            assert b <= a, f"Makespan increased: {a} -> {b}"

    def test_quantile_used_always_present(self):
        for anytime in (True, False):
            data = _base_data(anytime=anytime)
            result = solve_from_json(data)
            assert "quantile_used" in result["stats"]
            assert result["stats"]["quantile_used"] == 0.5
