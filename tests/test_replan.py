"""Tests for incremental replanning: solve_with_fixed and the replan CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from solver.parse_tasks import parse_tasks_md
from solver.replan import _build_fixed_assignments, _build_prior_hints, _remove_completed, replan
from solver.scheduler import solve_from_json, solve_with_fixed
from solver.validation import ScheduleInputError

REPO_ROOT = Path(__file__).resolve().parents[1]


# ───────────────────────────────────────────────────────────────────────
# Shared fixtures / helpers
# ───────────────────────────────────────────────────────────────────────


def _make_solver_input(
    n: int = 4,
    *,
    chain: bool = True,
    kappa: int = 10,
    budget: int = 100_000,
    time_limit: int = 10,
) -> dict:
    """n tasks in a chain (or no edges), single backend agent."""
    tasks = [
        {
            "id": f"T{i + 1:03d}",
            "phase": "Setup",
            "story_id": None,
            "story_priority": 99,
            "parallel_flag": False,
            "file_paths": [f"src/f{i}.py"],
            "required_skill": "backend",
            "estimated_tokens": 1000,
            "action_verb": "implement",
        }
        for i in range(n)
    ]
    edges = (
        [[f"T{i + 1:03d}", f"T{i + 2:03d}"] for i in range(n - 1)] if chain else []
    )
    return {
        "tasks": tasks,
        "edges": edges,
        "agents": [
            {
                "id": "A0",
                "model": "test",
                "skills": ["backend"],
                "kappa": kappa,
                "context_budget": budget,
                "speed_factor": 1.0,
            }
        ],
        "config": {"time_limit": time_limit, "num_workers": 1, "warm_start": True},
    }


# ───────────────────────────────────────────────────────────────────────
# Unit tests: _remove_completed
# ───────────────────────────────────────────────────────────────────────


class TestRemoveCompleted:
    def test_removes_task_from_list(self):
        si = _make_solver_input(3, chain=False)
        si_out, removed = _remove_completed(si, {"T002"})
        ids = [t["id"] for t in si_out["tasks"]]
        assert "T002" not in ids
        assert removed == 1

    def test_transitive_edge_added(self):
        si = _make_solver_input(3)
        # Chain: T001→T002→T003; remove T002 → expect T001→T003
        si_out, _ = _remove_completed(si, {"T002"})
        edges_out = {(e[0], e[1]) for e in si_out["edges"]}
        assert ("T001", "T003") in edges_out

    def test_original_edges_through_completed_removed(self):
        si = _make_solver_input(3)
        si_out, _ = _remove_completed(si, {"T002"})
        edges_out = {(e[0], e[1]) for e in si_out["edges"]}
        assert ("T001", "T002") not in edges_out
        assert ("T002", "T003") not in edges_out

    def test_multiple_completed_transitive(self):
        si = _make_solver_input(4)
        # T001→T002→T003→T004; remove T002,T003 → expect T001→T004
        si_out, removed = _remove_completed(si, {"T002", "T003"})
        edges_out = {(e[0], e[1]) for e in si_out["edges"]}
        assert ("T001", "T004") in edges_out
        assert removed == 2

    def test_no_completed_unchanged(self):
        si = _make_solver_input(3)
        si_out, removed = _remove_completed(si, set())
        assert [t["id"] for t in si_out["tasks"]] == ["T001", "T002", "T003"]
        assert removed == 0

    def test_completed_not_in_tasks_ignored(self):
        si = _make_solver_input(2)
        si_out, removed = _remove_completed(si, {"T999"})
        assert removed == 0
        assert len(si_out["tasks"]) == 2


# ───────────────────────────────────────────────────────────────────────
# Unit tests: _build_fixed_assignments
# ───────────────────────────────────────────────────────────────────────


class TestBuildFixedAssignments:
    def _prior(self, n: int) -> list[dict]:
        return [
            {"task_id": f"T{i + 1:03d}", "agent_id": "A0", "start": i * 10}
            for i in range(n)
        ]

    def test_freeze_before_captures_early_tasks(self):
        prior = self._prior(4)
        active = {f"T{i + 1:03d}" for i in range(4)}
        # starts: T001=0, T002=10, T003=20, T004=30  →  freeze_before=15 freezes T001,T002
        fixed = _build_fixed_assignments(prior, 15, active)
        assert "T001" in fixed
        assert "T002" in fixed
        assert "T003" not in fixed
        assert "T004" not in fixed

    def test_skips_tasks_not_in_active(self):
        prior = self._prior(3)
        fixed = _build_fixed_assignments(prior, 100, {"T001"})
        assert "T002" not in fixed
        assert "T003" not in fixed

    def test_freeze_zero_freezes_nothing(self):
        prior = self._prior(3)
        active = {f"T{i + 1:03d}" for i in range(3)}
        fixed = _build_fixed_assignments(prior, 0, active)
        assert fixed == {}


# ───────────────────────────────────────────────────────────────────────
# Unit tests: _build_prior_hints
# ───────────────────────────────────────────────────────────────────────


class TestBuildPriorHints:
    def _prior(self, n: int) -> list[dict]:
        return [
            {"task_id": f"T{i + 1:03d}", "agent_id": "A0", "start": i * 5}
            for i in range(n)
        ]

    def test_excludes_fixed_ids(self):
        prior = self._prior(3)
        active = {f"T{i + 1:03d}" for i in range(3)}
        hints = _build_prior_hints(prior, {"T001"}, active)
        assert "T001" not in hints
        assert "T002" in hints
        assert "T003" in hints

    def test_excludes_inactive_tasks(self):
        prior = self._prior(3)
        hints = _build_prior_hints(prior, set(), {"T001"})
        assert "T002" not in hints
        assert "T003" not in hints


# ───────────────────────────────────────────────────────────────────────
# Integration tests: solve_with_fixed
# ───────────────────────────────────────────────────────────────────────


class TestSolveWithFixed:
    def test_fixed_task_has_correct_start(self):
        si = _make_solver_input(2, chain=False)
        initial = solve_from_json(si)
        assert initial["status"] in {"OPTIMAL", "FEASIBLE"}
        first_assn = initial["assignments"][0]
        task_id = first_assn["task_id"]
        agent_id = first_assn["agent_id"]
        expected_start = first_assn["start"]

        fixed = {task_id: {"agent_id": agent_id, "start": expected_start}}
        result = solve_with_fixed(si, fixed)
        assert result["status"] in {"OPTIMAL", "FEASIBLE"}
        assn_map = {a["task_id"]: a for a in result["assignments"]}
        assert assn_map[task_id]["start"] == expected_start
        assert assn_map[task_id]["agent_id"] == agent_id

    def test_no_fixed_equivalent_to_solve_from_json(self):
        si = _make_solver_input(3)
        r1 = solve_from_json(si)
        r2 = solve_with_fixed(si, {})
        assert r1["status"] == r2["status"]
        assert r1["stats"]["makespan"] == r2["stats"]["makespan"]

    def test_result_has_quantile_and_edges(self):
        si = _make_solver_input(2)
        result = solve_with_fixed(si, {})
        assert "quantile_used" in result["stats"]
        assert "edges" in result
        assert "tasks" in result

    def test_incompatible_agent_in_fixed_raises(self):
        si = _make_solver_input(2, chain=False)
        fixed = {"T001": {"agent_id": "NONEXISTENT", "start": 0}}
        with pytest.raises(ScheduleInputError, match="Frozen task"):
            solve_with_fixed(si, fixed)


# ───────────────────────────────────────────────────────────────────────
# Integration tests: replan()
# ───────────────────────────────────────────────────────────────────────


class TestReplan:
    def test_replan_stats_keys_present(self):
        si = _make_solver_input(3)
        prior = solve_from_json(si)
        result = replan(prior, si, freeze_before=None)
        rp = result["stats"]["replan"]
        assert "fixed_count" in rp
        assert "completed_count" in rp
        assert "added_count" in rp
        assert "reused_from" in rp

    def test_freeze_before_pins_early_tasks(self):
        si = _make_solver_input(3)
        prior = solve_from_json(si)
        makespan = prior["stats"]["makespan"]
        result = replan(prior, si, freeze_before=makespan + 1)
        rp = result["stats"]["replan"]
        assert rp["fixed_count"] == 3

        assn_map_prior = {a["task_id"]: a for a in prior["assignments"]}
        assn_map_new = {a["task_id"]: a for a in result["assignments"]}
        for task_id, pa in assn_map_prior.items():
            if pa["start"] < makespan + 1:
                assert assn_map_new[task_id]["start"] == pa["start"]
                assert assn_map_new[task_id]["agent_id"] == pa["agent_id"]

    def test_completed_tasks_removed(self):
        si = _make_solver_input(4)
        prior = solve_from_json(si)
        result = replan(prior, _make_solver_input(4), completed_ids={"T001"})
        rp = result["stats"]["replan"]
        assert rp["completed_count"] == 1
        task_ids = {a["task_id"] for a in result["assignments"]}
        assert "T001" not in task_ids

    def test_makespan_nondecreasing_after_freeze(self):
        si = _make_solver_input(4)
        prior = solve_from_json(si)
        prior_makespan = prior["stats"]["makespan"]
        mid = prior_makespan // 2
        result = replan(prior, _make_solver_input(4), freeze_before=mid)
        assert result["stats"]["makespan"] >= prior_makespan


# ───────────────────────────────────────────────────────────────────────
# E2E test: solve → freeze → replan → verify frozen identical
# ───────────────────────────────────────────────────────────────────────


class TestE2EFreeze:
    def test_frozen_assignments_unchanged_after_replan(self):
        si = _make_solver_input(4, chain=True)
        prior = solve_from_json(si)
        assert prior["status"] in {"OPTIMAL", "FEASIBLE"}
        prior_makespan = prior["stats"]["makespan"]

        prior_map = {a["task_id"]: a for a in prior["assignments"]}
        freeze_t = prior_map["T004"]["start"]

        changed = _make_solver_input(4, chain=True)
        changed["agents"].append(
            {
                "id": "A1",
                "model": "test",
                "skills": ["frontend"],
                "kappa": 10,
                "context_budget": 100_000,
                "speed_factor": 1.0,
            }
        )
        changed["tasks"][3]["required_skill"] = "frontend"

        result = replan(prior, changed, freeze_before=freeze_t)

        assert result["status"] in {"OPTIMAL", "FEASIBLE"}

        new_map = {a["task_id"]: a for a in result["assignments"]}

        frozen_count = 0
        for task_id, pa in prior_map.items():
            if pa["start"] < freeze_t:
                frozen_count += 1
                assert new_map[task_id]["start"] == pa["start"], (
                    f"{task_id}: expected start={pa['start']}, got {new_map[task_id]['start']}"
                )
                assert new_map[task_id]["agent_id"] == pa["agent_id"], (
                    f"{task_id}: expected agent={pa['agent_id']}, got {new_map[task_id]['agent_id']}"
                )

        assert frozen_count == result["stats"]["replan"]["fixed_count"]
        assert new_map["T004"]["agent_id"] == "A1"
        assert result["stats"]["makespan"] >= prior_makespan


# ───────────────────────────────────────────────────────────────────────
# CLI tests
# ───────────────────────────────────────────────────────────────────────


class TestReplanCLI:
    def _run(self, args: list[str], input_json: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "solver.replan", *args],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

    def test_cli_smoke(self, tmp_path: Path):
        docs = REPO_ROOT / "docs"
        config_path = docs / "example-config.yml"
        tasks_path = docs / "example-tasks.md"

        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        solver_input = parse_tasks_md(str(tasks_path), config)
        prior = solve_from_json(solver_input)

        prior_path = tmp_path / "prior.json"
        prior_path.write_text(json.dumps(prior), encoding="utf-8")

        proc = self._run([str(prior_path), str(tasks_path), str(config_path)])
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert out["status"] in {"OPTIMAL", "FEASIBLE"}
        assert "replan" in out["stats"]

    def test_cli_freeze_before(self, tmp_path: Path):
        docs = REPO_ROOT / "docs"
        config_path = docs / "example-config.yml"
        tasks_path = docs / "example-tasks.md"

        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        solver_input = parse_tasks_md(str(tasks_path), config)
        prior = solve_from_json(solver_input)
        prior_makespan = prior["stats"]["makespan"]

        prior_path = tmp_path / "prior.json"
        prior_path.write_text(json.dumps(prior), encoding="utf-8")

        proc = self._run([
            str(prior_path), str(tasks_path), str(config_path),
            "--freeze-before", str(prior_makespan + 1),
        ])
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert out["stats"]["replan"]["fixed_count"] == len(prior["assignments"])

    def test_cli_add_task(self, tmp_path: Path):
        docs = REPO_ROOT / "docs"
        config_path = docs / "example-config.yml"
        tasks_path = docs / "example-tasks.md"

        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        solver_input = parse_tasks_md(str(tasks_path), config)
        prior = solve_from_json(solver_input)

        prior_path = tmp_path / "prior.json"
        prior_path.write_text(json.dumps(prior), encoding="utf-8")

        new_line = "- [ ] T999 Implement extra feature in `src/extra.ts`"
        proc = self._run([
            str(prior_path), str(tasks_path), str(config_path),
            "--add-task", new_line,
        ])
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert out["stats"]["replan"]["added_count"] == 1
        task_ids = {a["task_id"] for a in out["assignments"]}
        assert "T999" in task_ids

    def test_cli_completed_unknown_exits_2(self, tmp_path: Path):
        docs = REPO_ROOT / "docs"
        config_path = docs / "example-config.yml"
        tasks_path = docs / "example-tasks.md"

        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        solver_input = parse_tasks_md(str(tasks_path), config)
        prior = solve_from_json(solver_input)

        prior_path = tmp_path / "prior.json"
        prior_path.write_text(json.dumps(prior), encoding="utf-8")

        proc = self._run([
            str(prior_path), str(tasks_path), str(config_path),
            "--completed", "T999",
        ])
        assert proc.returncode == 2
        assert "T999" in proc.stderr

    def test_cli_reused_from_is_absolute(self, tmp_path: Path):
        docs = REPO_ROOT / "docs"
        config_path = docs / "example-config.yml"
        tasks_path = docs / "example-tasks.md"

        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        solver_input = parse_tasks_md(str(tasks_path), config)
        prior = solve_from_json(solver_input)

        prior_path = tmp_path / "prior.json"
        prior_path.write_text(json.dumps(prior), encoding="utf-8")

        proc = self._run([str(prior_path), str(tasks_path), str(config_path)])
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        reused = out["stats"]["replan"]["reused_from"]
        assert Path(reused).is_absolute()
