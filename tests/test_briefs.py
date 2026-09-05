"""Subagent briefs and the DONE/FAILED/TOUCHED report parser."""

from __future__ import annotations

import pytest

from solver.briefs import (
    BriefContext,
    lane_files,
    parse_report,
    render_brief,
    render_round,
)


def _plan() -> dict:
    return {
        "tasks": [
            {"id": "T001", "description": "Create model in src/models/a.py",
             "file_paths": ["src/models/a.py"], "phase": "User Story 1", "story_id": "US1"},
            {"id": "T002", "description": "Create service in src/services/a.py",
             "file_paths": ["src/services/a.py", "src/models/a.py"], "phase": "User Story 1",
             "story_id": "US1"},
            {"id": "T003", "description": "Write docs in docs/",
             "file_paths": ["docs/"], "phase": "Polish", "story_id": None},
            {"id": "T004", "description": "Add logging", "file_paths": [], "phase": "Polish",
             "story_id": None},
        ],
        "rounds": [
            {"round": 1, "lanes": [
                {"agent_id": "worker-1", "tasks": ["T001", "T002"]},
                {"agent_id": "worker-2", "tasks": ["T003"]},
            ]},
            {"round": 2, "lanes": [{"agent_id": "worker-1", "tasks": ["T004"]}]},
        ],
    }


class TestLaneFiles:
    def test_ordered_and_deduplicated(self) -> None:
        assert lane_files(_plan(), ["T001", "T002"]) == ["src/models/a.py", "src/services/a.py"]

    def test_unknown_task_ignored(self) -> None:
        assert lane_files(_plan(), ["T999"]) == []


class TestRenderBrief:
    def test_contains_tasks_files_and_forbidden_list(self) -> None:
        plan = _plan()
        out = render_brief(plan, plan["rounds"][0], plan["rounds"][0]["lanes"][0],
                           total_rounds=2, context=BriefContext(
                               feature_name="demo", spec_path="specs/demo/spec.md",
                               plan_path="specs/demo/plan.md", tasks_path="specs/demo/tasks.md"))
        assert "lane `worker-1`, round 1 of 2" in out
        assert "one of 2 subagents" in out
        assert "1. **T001** — Create model in src/models/a.py" in out
        assert "2. **T002** — Create service in src/services/a.py" in out
        # Own files, then the other lane's files as forbidden.
        assert "- `src/models/a.py`\n- `src/services/a.py`" in out
        assert "## Files these tasks name" in out
        assert "## Files you must NOT edit" in out
        assert "- `docs/`  (worker-2)" in out
        assert "Never edit a file listed under" in out
        assert "list **every** path" in out
        # Context + single-writer rule.
        assert "`specs/demo/tasks.md`  (do NOT edit" in out
        assert "Do not edit `tasks.md`" in out
        assert "DONE: T001 T002" in out
        assert "TOUCHED:" in out and "FAILED:" in out

    def test_single_lane_round_has_no_forbidden_section(self) -> None:
        plan = _plan()
        out = render_brief(plan, plan["rounds"][1], plan["rounds"][1]["lanes"][0])
        assert "in parallel" not in out
        assert "## Files you must NOT edit" not in out
        assert "(none named" in out
        assert "Read the feature's `spec.md`" in out

    def test_own_files_are_never_forbidden(self) -> None:
        plan = _plan()
        # worker-2 also touches src/models/a.py via a hypothetical task.
        plan["tasks"].append({"id": "T005", "description": "Tweak model",
                              "file_paths": ["src/models/a.py"], "phase": "Polish"})
        rnd = {"round": 1, "lanes": [
            {"agent_id": "worker-1", "tasks": ["T001"]},
            {"agent_id": "worker-2", "tasks": ["T005"]},
        ]}
        out = render_brief(plan, rnd, rnd["lanes"][0])
        assert "## Files you must NOT edit" not in out


class TestRenderRound:
    def test_round_view_has_table_briefs_and_blocked(self) -> None:
        plan = _plan()
        rnd = dict(plan["rounds"][0])
        rnd["blocked"] = [{"agent_id": "worker-3", "task_id": "T004", "waiting_on": ["T002"]}]
        out = render_round(plan, rnd, total_rounds=2, remaining_tasks=1)
        assert out.startswith("# Round 1 of 2 — 2 lanes, 3 tasks, 1 remaining after this round")
        assert "| worker-1 | T001 → T002 | `src/models/a.py`, `src/services/a.py` |" in out
        assert "| worker-2 | T003 | `docs/` |" in out
        assert "worker-3: T004 waits on T002" in out
        assert out.count("# Subagent brief") == 2
        assert out.endswith("\n")

    def test_end_to_end_from_solver_result(self, speckit_solved: tuple[dict, dict]) -> None:
        _, result = speckit_solved
        first = result["rounds"][0]
        out = render_round(result, first, total_rounds=len(result["rounds"]))
        for lane in first["lanes"]:
            assert f"lane `{lane['agent_id']}`" in out
            for tid in lane["tasks"]:
                assert f"**{tid}**" in out
        # Every brief names its tasks' files or says none.
        assert "## Files these tasks name" in out


class TestParseReport:
    def test_parses_all_markers(self) -> None:
        text = (
            "I implemented the models.\n\n```\nDONE: T001 T002\n"
            "FAILED: T003 needs to edit package.json (not allowed)\n"
            "TOUCHED: src/models/a.py src/services/a.py\nNOTES: -\n```\n"
        )
        rep = parse_report(text)
        assert rep == {
            "done": ["T001", "T002"],
            "failed": ["T003"],
            "touched": ["src/models/a.py", "src/services/a.py"],
        }

    def test_failed_wins_over_done_and_is_case_insensitive(self) -> None:
        rep = parse_report("done: T001 T002\nfailed: T002 broke\ntouched: `a.py`, b.py")
        assert rep["done"] == ["T001"]
        assert rep["failed"] == ["T002"]
        assert rep["touched"] == ["a.py", "b.py"]

    @pytest.mark.parametrize("text", ["", "no report here", "DONE:"])
    def test_empty_or_missing_report(self, text: str) -> None:
        assert parse_report(text) == {"done": [], "failed": [], "touched": []}
