"""Unified CLI (``python -m solver plan|next|mark|status``), driven in-process."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest
import yaml

from solver.briefs import parse_report
from solver.cli import EXIT_BLOCKED, EXIT_INPUT_ERROR, EXIT_OK, main
from solver.parse_tasks import parse_tasks_md
from solver.tasks_md import mark_tasks, scan_checkboxes
from solver.validation import ScheduleInputError
from tests._helpers import FAST_SOLVER, SPECKIT_FIXTURE


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A spec-kit-shaped project: .specify/ marker + specs/demo/{tasks,spec,plan}.md."""
    (tmp_path / ".specify").mkdir()
    feature = tmp_path / ".specify" / "specs" / "004-demo"
    feature.mkdir(parents=True)
    shutil.copy(SPECKIT_FIXTURE, feature / "tasks.md")
    (feature / "spec.md").write_text("# Spec\n", encoding="utf-8")
    (feature / "plan.md").write_text("# Plan\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_config(project: Path, body: dict) -> Path:
    cfg_dir = project / ".specify" / "schedule"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "schedule-config.yml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def _plan(project: Path, *extra: str, out: Path | None = None) -> dict:
    tasks = project / ".specify" / "specs" / "004-demo" / "tasks.md"
    rc = main(["plan", str(tasks), *extra])
    assert rc == EXIT_OK
    return json.loads(((out or tasks.parent) / "schedule.json").read_text(encoding="utf-8"))


def _hand_plan(tmp_path: Path) -> tuple[Path, Path]:
    """schedule.json + tasks.md for two tasks in one lane — no solve needed."""
    tasks = tmp_path / "tasks.md"
    tasks.write_text(
        "## Setup\n- [ ] T001 Implement a in src/a.py\n- [ ] T002 Implement b in src/b.py\n",
        encoding="utf-8",
    )
    plan = {
        "feature": "demo",
        "assignments": [
            {"task_id": "T001", "agent_id": "w", "start": 0, "duration": 1},
            {"task_id": "T002", "agent_id": "w", "start": 1, "duration": 1},
        ],
        "edges": [],
        "resource_edges": [["T001", "T002"]],
        "tasks": [
            {"id": "T001", "description": "Implement a", "file_paths": ["src/a.py"]},
            {"id": "T002", "description": "Implement b", "file_paths": ["src/b.py"]},
        ],
        "rounds": [{"round": 1, "lanes": [{"agent_id": "w", "tasks": ["T001", "T002"]}]}],
        "source": {"tasks_md": "tasks.md", "task_ids": ["T001", "T002"]},
    }
    sched = tmp_path / "schedule.json"
    sched.write_text(json.dumps(plan), encoding="utf-8")
    return sched, tasks


class TestPlan:
    def test_zero_config_writes_both_artifacts(self, project: Path, capsys) -> None:
        _write_config(project, FAST_SOLVER)
        plan = _plan(project)
        feature_dir = project / ".specify" / "specs" / "004-demo"
        md = (feature_dir / "schedule.md").read_text(encoding="utf-8")
        out = capsys.readouterr().out
        assert "# Schedule — 004-demo" in md
        assert "## Execution Rounds" in md
        assert plan["feature"] == "004-demo"
        assert plan["source"]["tasks_md"] == "tasks.md"
        assert len(plan["source"]["sha256"]) == 64
        assert plan["source"]["task_ids"][:2] == ["T001", "T002"]
        assert plan["stats"]["portfolio_mode"] == "workers"
        assert plan["rounds"]
        assert "Rounds:" in out and "Speedup:" in out
        assert "Written: " in out and "schedule.md" in out

    def test_scaffolded_config_is_found(self, project: Path) -> None:
        scaffolded = project / ".specify" / "extensions" / "schedule" / "schedule-config.yml"
        scaffolded.parent.mkdir(parents=True)
        scaffolded.write_text(yaml.safe_dump({"workers": 2, **FAST_SOLVER}), encoding="utf-8")
        plan = _plan(project)
        assert plan["config_path"].replace("\\", "/").endswith(
            ".specify/extensions/schedule/schedule-config.yml"
        )
        assert plan["stats"]["total_agents"] == 2
    def test_workers_flag_and_feature_and_out(self, project: Path, tmp_path: Path) -> None:
        _write_config(project, FAST_SOLVER)
        out_dir = tmp_path / "elsewhere"
        plan = _plan(
            project, "--workers", "2", "--feature", "notes", "--out", str(out_dir), out=out_dir
        )
        assert plan["feature"] == "notes"
        assert plan["stats"]["total_agents"] == 2
        assert (out_dir / "schedule.md").is_file()
        assert plan["source"]["tasks_md"].endswith("004-demo/tasks.md")

    def test_cap_raises_workers(self, project: Path) -> None:
        _write_config(project, FAST_SOLVER)
        plan = _plan(project, "--workers", "1", "--max-tasks-per-worker", "10")
        assert plan["stats"]["total_agents"] == 3  # ceil(25 / 10)
        assert any(w["code"] == "workers_raised" for w in plan["warnings"])

    def test_explicit_config_with_agents_ignores_workers_flag(
        self, project: Path, tmp_path: Path, capsys
    ) -> None:
        cfg = tmp_path / "cfg.yml"
        cfg.write_text(
            yaml.safe_dump(
                {
                    "agents": [
                        {"id": "a", "model": "m", "skills": ["*"], "kappa": 30, "context_budget": 500},
                        {"id": "b", "model": "m", "skills": ["*"], "kappa": 30, "context_budget": 500},
                    ],
                    **FAST_SOLVER,
                }
            ),
            encoding="utf-8",
        )
        plan = _plan(project, "--config", str(cfg), "--workers", "5")
        assert plan["stats"]["total_agents"] == 2
        assert plan["stats"]["portfolio_mode"] == "agents"
        assert "ignored" in capsys.readouterr().err

    def test_missing_config_or_tasks_is_input_error(self, project: Path, capsys) -> None:
        tasks = project / ".specify" / "specs" / "004-demo" / "tasks.md"
        assert main(["plan", str(tasks), "--config", "nope.yml"]) == EXIT_INPUT_ERROR
        assert "Config file not found" in capsys.readouterr().err
        assert main(["plan", str(project / "missing.md")]) == EXIT_INPUT_ERROR

    def test_images_flag(self, project: Path) -> None:
        pytest.importorskip("matplotlib")
        _write_config(project, FAST_SOLVER)
        _plan(project, "--images")
        feature_dir = project / ".specify" / "specs" / "004-demo"
        assert (feature_dir / "images" / "004-demo-gantt.png").is_file()
        assert "![Gantt](images/004-demo-gantt.png)" in (
            feature_dir / "schedule.md"
        ).read_text(encoding="utf-8")


class TestNextMarkLoop:
    def test_follows_the_static_rounds_to_done(self, project: Path, capsys) -> None:
        _write_config(project, FAST_SOLVER)
        plan = _plan(project)
        capsys.readouterr()
        feature_dir = project / ".specify" / "specs" / "004-demo"
        sched = str(feature_dir / "schedule.json")
        tasks = feature_dir / "tasks.md"

        seen_rounds = 0
        while True:
            rc = main(["next", sched, "--format", "json"])
            out = capsys.readouterr().out
            assert rc == EXIT_OK
            payload = json.loads(out)
            if payload["status"] == "done":
                break
            seen_rounds += 1
            assert payload["round"] == seen_rounds
            assert payload["total_rounds"] == len(plan["rounds"])
            expected = plan["rounds"][seen_rounds - 1]["lanes"]
            assert [(lane["agent_id"], lane["tasks"]) for lane in payload["lanes"]] == [
                (lane["agent_id"], lane["tasks"]) for lane in expected
            ]
            for lane in payload["lanes"]:
                assert "# Subagent brief" in lane["brief"]
                assert "`.specify/specs/004-demo/spec.md`" in lane["brief"]
            ids = [tid for lane in payload["lanes"] for tid in lane["tasks"]]
            assert main(["mark", str(tasks), *ids]) == EXIT_OK
            assert "Marked" in capsys.readouterr().out
        assert seen_rounds == len(plan["rounds"])
        assert all(scan_checkboxes(tasks).values())
        assert payload["done"] == len(plan["source"]["task_ids"])

    def test_markdown_format_and_all_done_message(self, tmp_path: Path, capsys) -> None:
        sched, tasks = _hand_plan(tmp_path)
        assert main(["next", str(sched)]) == EXIT_OK
        out = capsys.readouterr().out
        assert out.startswith("# Round 1 of 1 ")
        assert "Launch one subagent per lane" in out
        assert "## Files these tasks name" in out
        # Mark everything done → DONE.
        mark_tasks(tasks, scan_checkboxes(tasks).keys())
        assert main(["next", str(sched), str(tasks)]) == EXIT_OK
        assert "DONE" in capsys.readouterr().out

    def test_tasks_changed_is_rejected(self, tmp_path: Path, capsys) -> None:
        sched, tasks = _hand_plan(tmp_path)
        with tasks.open("a", encoding="utf-8") as fh:
            fh.write("- [ ] T099 Add a late task in src/late.py\n")
        assert main(["next", str(sched)]) == EXIT_INPUT_ERROR
        err = capsys.readouterr().err
        assert "added: T099" in err and "speckit-schedule-run" in err

    def test_blocked_when_predecessor_is_outside_the_lanes(self, tmp_path: Path, capsys) -> None:
        tasks = tmp_path / "tasks.md"
        tasks.write_text("## Setup\n- [ ] T001 Implement a in src/a.py\n", encoding="utf-8")
        plan = {
            "assignments": [{"task_id": "T001", "agent_id": "w", "start": 0, "duration": 1}],
            "edges": [["T009", "T001"]],
            "resource_edges": [],
            "tasks": [{"id": "T001", "description": "Implement a", "file_paths": ["src/a.py"]}],
            "rounds": [],
            "source": {"tasks_md": "tasks.md", "task_ids": ["T001"]},
        }
        sched = tmp_path / "schedule.json"
        sched.write_text(json.dumps(plan), encoding="utf-8")
        assert main(["next", str(sched)]) == EXIT_BLOCKED
        assert "BLOCKED" in capsys.readouterr().out

    def test_missing_or_invalid_plan(self, tmp_path: Path, capsys) -> None:
        assert main(["next", str(tmp_path / "schedule.json")]) == EXIT_INPUT_ERROR
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert main(["next", str(bad)]) == EXIT_INPUT_ERROR
        bad.write_text('{"foo": 1}', encoding="utf-8")
        assert main(["next", str(bad)]) == EXIT_INPUT_ERROR


class TestMark:
    def test_mark_undo_idempotent(self, tmp_path: Path, capsys) -> None:
        tasks = tmp_path / "tasks.md"
        tasks.write_text(
            "## Setup\n- [ ] T001 Do a in a.py\n- [x] T002 Do b in b.py\n"
            "  - [ ] T003 Nested in c.py\n",
            encoding="utf-8",
        )
        assert main(["mark", str(tasks), "T001", "T002", "T003"]) == EXIT_OK
        assert "Marked 2 task(s) as [x]" in capsys.readouterr().out
        assert scan_checkboxes(tasks) == {"T001": True, "T002": True, "T003": True}
        assert mark_tasks(tasks, ["T001"]) == 0  # already done
        assert main(["mark", str(tasks), "--undo", "T002"]) == EXIT_OK
        assert scan_checkboxes(tasks)["T002"] is False
        # Nested indentation preserved.
        assert "  - [x] T003 Nested in c.py" in tasks.read_text(encoding="utf-8")

    def test_unknown_id_changes_nothing(self, tmp_path: Path, capsys) -> None:
        tasks = tmp_path / "tasks.md"
        original = "## Setup\n- [ ] T001 Do a in a.py\n"
        tasks.write_text(original, encoding="utf-8")
        with pytest.raises(ScheduleInputError, match="T999"):
            mark_tasks(tasks, ["T001", "T999"])
        assert tasks.read_text(encoding="utf-8") == original
        assert main(["mark", str(tasks), "T999"]) == EXIT_INPUT_ERROR
        assert "T999" in capsys.readouterr().err

    def test_ids_may_arrive_joined(self, tmp_path: Path) -> None:
        tasks = tmp_path / "tasks.md"
        tasks.write_text("- [ ] T001 a in a.py\n- [ ] T002 b in b.py\n- [ ] T003 c\n", encoding="utf-8")
        assert mark_tasks(tasks, ["T001 T002", "T003"]) == 3
        assert mark_tasks(tasks, ["T001,T002"], done=False) == 2

    def test_preserves_trailing_newline_state(self, tmp_path: Path) -> None:
        tasks = tmp_path / "tasks.md"
        tasks.write_text("- [ ] T001 a in a.py", encoding="utf-8")  # no trailing newline
        mark_tasks(tasks, ["T001"])
        assert tasks.read_text(encoding="utf-8") == "- [x] T001 a in a.py"


class TestStatus:
    def test_delegates_to_status_module(self, project: Path, capsys) -> None:
        # A bare .specify/ project with nothing installed: needs-attention, exit 1.
        assert main(["status"]) == 1
        assert "Status: needs-attention" in capsys.readouterr().out


class TestCheckboxShapes:
    def test_uppercase_x_counts_as_done_and_undo_flips_it(self, tmp_path: Path) -> None:
        # /speckit.implement writes ``[X]``.
        tasks = tmp_path / "tasks.md"
        tasks.write_text("- [X] T001 a in a.py\n- [ ] T002 b in b.py\n", encoding="utf-8")
        assert scan_checkboxes(tasks) == {"T001": True, "T002": False}
        assert mark_tasks(tasks, ["T001"]) == 0
        assert mark_tasks(tasks, ["T001"], done=False) == 1
        assert tasks.read_text(encoding="utf-8").startswith("- [ ] T001")

    def test_four_digit_ids_round_trip(self, tmp_path: Path) -> None:
        tasks = tmp_path / "tasks.md"
        tasks.write_text("## Setup\n- [ ] T1000 Implement a in src/a.py\n", encoding="utf-8")
        assert [t["id"] for t in parse_tasks_md(str(tasks), {})["tasks"]] == ["T1000"]
        assert mark_tasks(tasks, ["T1000"]) == 1
        assert scan_checkboxes(tasks) == {"T1000": True}
        assert parse_report("DONE: T1000\nTOUCHED: src/a.py")["done"] == ["T1000"]

    def test_next_accepts_a_plan_without_source_or_rounds(self, tmp_path: Path, capsys) -> None:
        tasks = tmp_path / "tasks.md"
        tasks.write_text("## Setup\n- [ ] T001 Implement a in src/a.py\n", encoding="utf-8")
        plan = {
            "assignments": [{"task_id": "T001", "agent_id": "w", "start": 0, "duration": 1}],
            "edges": [],
            "resource_edges": [],
            "tasks": [{"id": "T001", "description": "Implement a", "file_paths": ["src/a.py"]}],
        }
        sched = tmp_path / "schedule.json"
        sched.write_text(json.dumps(plan), encoding="utf-8")
        assert main(["next", str(sched), str(tasks)]) == EXIT_OK
        assert capsys.readouterr().out.startswith("# Round 1 —")

    def test_undo_on_an_unchecked_task_changes_nothing(self, tmp_path: Path) -> None:
        tasks = tmp_path / "tasks.md"
        tasks.write_text("- [ ] T001 a in a.py\n", encoding="utf-8")
        assert mark_tasks(tasks, ["T001"], done=False) == 0

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
    def test_mark_keeps_the_file_mode(self, tmp_path: Path) -> None:
        tasks = tmp_path / "tasks.md"
        tasks.write_text("- [ ] T001 a in a.py\n", encoding="utf-8")
        tasks.chmod(0o664)
        mark_tasks(tasks, ["T001"])
        assert stat.S_IMODE(tasks.stat().st_mode) == 0o664

    def test_mark_preserves_crlf_line_endings(self, tmp_path: Path) -> None:
        tasks = tmp_path / "tasks.md"
        tasks.write_bytes(b"# T\r\n- [ ] T001 a in a.py\r\n- [ ] T002 b in b.py\r\n")
        assert mark_tasks(tasks, ["T001"]) == 1
        assert tasks.read_bytes() == b"# T\r\n- [x] T001 a in a.py\r\n- [ ] T002 b in b.py\r\n"


class TestInvalidKnobs:
    def test_workers_zero_is_an_input_error_not_a_traceback(self, project: Path, capsys) -> None:
        tasks = project / ".specify" / "specs" / "004-demo" / "tasks.md"
        assert main(["plan", str(tasks), "--workers", "0"]) == EXIT_INPUT_ERROR
        err = capsys.readouterr().err
        assert err.startswith("ERROR: config error at 'workers'")

    def test_unsolved_plan_is_rejected_by_next(self, tmp_path: Path, capsys) -> None:
        plan = {
            "status": "INFEASIBLE",
            "assignments": [],
            "edges": [],
            "resource_edges": [],
            "tasks": [],
            "source": {"tasks_md": "tasks.md", "task_ids": ["T001"]},
        }
        sched = tmp_path / "schedule.json"
        sched.write_text(json.dumps(plan), encoding="utf-8")
        (tmp_path / "tasks.md").write_text("- [ ] T001 a in a.py\n", encoding="utf-8")
        assert main(["next", str(sched)]) == EXIT_INPUT_ERROR
        assert "INFEASIBLE" in capsys.readouterr().err

    def test_invalid_yaml_config_is_an_input_error(self, project: Path, tmp_path: Path, capsys) -> None:
        bad = tmp_path / "bad.yml"
        bad.write_text("workers: [1, 2\n", encoding="utf-8")
        tasks = project / ".specify" / "specs" / "004-demo" / "tasks.md"
        assert main(["plan", str(tasks), "--config", str(bad)]) == EXIT_INPUT_ERROR
        assert capsys.readouterr().err.startswith("ERROR: Cannot read config file")

    @pytest.mark.skipif(
        sys.platform == "win32" or getattr(os, "geteuid", lambda: 1)() == 0,
        reason="POSIX permissions, non-root",
    )
    def test_unwritable_out_dir_is_an_input_error(self, project: Path, tmp_path: Path, capsys) -> None:
        out = tmp_path / "ro"
        out.mkdir()
        out.chmod(0o500)
        tasks = project / ".specify" / "specs" / "004-demo" / "tasks.md"
        _write_config(project, FAST_SOLVER)
        try:
            assert main(["plan", str(tasks), "--out", str(out)]) == EXIT_INPUT_ERROR
            assert "\nERROR: " in "\n" + capsys.readouterr().err  # after any solver WARN lines
        finally:
            out.chmod(0o700)
