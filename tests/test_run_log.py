"""Tests for solver.run_log — plan capture and actual recording."""

from __future__ import annotations

__all__: list[str] = []

import json
import logging
import os
import re
import stat
import sys
from pathlib import Path

import pytest

from solver import run_log
from solver._paths import runs_dir

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_specify_root(tmp_path: Path) -> Path:
    (tmp_path / ".specify").mkdir()
    return tmp_path


def _sample_result() -> dict:
    """Minimal solver result envelope sufficient for record_plan."""
    return {
        "status": "OPTIMAL",
        "stats": {"makespan": 220, "max_load": 110, "total_cost": 0.5},
        "makespan": 220,
        "max_load": 110,
        "total_cost": 0.5,
        "assignments": [
            {
                "task_id": "T001",
                "agent_id": "opus",
                "duration": 80,
                "start": 0,
                "end": 80,
                "phase": "Foundational",
            },
            {
                "task_id": "T002",
                "agent_id": "sonnet",
                "duration": 40,
                "start": 80,
                "end": 120,
                "phase": "User Story 1",
            },
        ],
    }


# ---------------------------------------------------------------------------
# new_run_id
# ---------------------------------------------------------------------------


class TestNewRunId:
    def test_format_is_iso_plus_short_uuid(self) -> None:
        rid = run_log.new_run_id()
        # Pattern: YYYY-MM-DDTHH:MM:SSZ-<8 hex>
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z-[0-9a-f]{8}$", rid)

    def test_uses_supplied_timestamp(self) -> None:
        rid = run_log.new_run_id(now="2026-01-02T03:04:05Z")
        assert rid.startswith("2026-01-02T03:04:05Z-")

    def test_two_calls_differ(self) -> None:
        a = run_log.new_run_id(now="2026-01-02T03:04:05Z")
        b = run_log.new_run_id(now="2026-01-02T03:04:05Z")
        # Even with identical timestamps the uuid suffix must differ.
        assert a != b


# ---------------------------------------------------------------------------
# record_plan
# ---------------------------------------------------------------------------


class TestRecordPlan:
    def test_writes_plan_with_required_fields(self, tmp_path: Path) -> None:
        root = _make_specify_root(tmp_path)
        result = _sample_result()
        out = run_log.record_plan(
            result,
            project_root=root,
            run_id="2026-05-07T12:00:00Z-abc12345",
            config_path=".specify/schedule/schedule-config.yml",
            tasks_md_path="tasks.md",
            objective="lexicographic",
        )
        assert out is not None
        assert out.exists()
        payload = json.loads(out.read_text(encoding="utf-8"))
        # Required fields per the disk schema.
        assert payload["schema_version"] == run_log.RUN_LOG_SCHEMA_VERSION
        assert payload["run_id"] == "2026-05-07T12:00:00Z-abc12345"
        assert payload["status"] == "OPTIMAL"
        assert payload["makespan"] == 220
        assert payload["max_load"] == 110
        assert payload["objective"] == "lexicographic"
        assert payload["tasks_md_path"] == "tasks.md"
        # Assignments distilled to the calibration-relevant subset.
        assert len(payload["assignments"]) == 2
        first = payload["assignments"][0]
        assert first["task_id"] == "T001"
        assert first["agent_id"] == "opus"
        assert first["expected_duration"] == 80
        assert first["expected_start"] == 0
        assert first["expected_end"] == 80
        # Internal-only fields are NOT persisted.
        assert "phase" not in first

    def test_returns_none_when_no_specify_ancestor(self, tmp_path: Path) -> None:
        # No `.specify/` anywhere → record_plan must skip silently.
        out = run_log.record_plan(_sample_result(), project_root=tmp_path)
        assert out is None
        # And nothing was created on disk.
        assert not (tmp_path / "schedule" / "runs").exists()

    def test_creates_runs_directory(self, tmp_path: Path) -> None:
        root = _make_specify_root(tmp_path)
        run_log.record_plan(
            _sample_result(),
            project_root=root,
            run_id="2026-05-07T12:00:00Z-abcdef01",
        )
        assert runs_dir(root).is_dir()

    def test_default_run_id_is_unique_per_call(self, tmp_path: Path) -> None:
        root = _make_specify_root(tmp_path)
        a = run_log.record_plan(_sample_result(), project_root=root)
        b = run_log.record_plan(_sample_result(), project_root=root)
        assert a is not None and b is not None
        assert a != b
        # Both files survive.
        assert a.exists() and b.exists()

    def test_best_effort_swallows_write_failure(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """record_plan must NEVER raise into its caller.

        Make the runs directory unwritable so the underlying write
        fails. The function should log a warning and return ``None``
        without raising.
        """
        if sys.platform.startswith("win"):
            pytest.skip("chmod-based read-only fs not portable on Windows")
        if os.geteuid() == 0:  # type: ignore[attr-defined]
            pytest.skip("running as root makes chmod 0o400 ineffective")

        root = _make_specify_root(tmp_path)
        # Pre-create the runs directory and lock it down so the
        # subsequent file write fails. ``record_plan`` calls
        # ``mkdir(exist_ok=True)`` first, so we cannot just chmod the
        # parent; we need to chmod the leaf directory itself.
        rdir = runs_dir(root)
        rdir.mkdir(parents=True, exist_ok=True)
        rdir.chmod(stat.S_IRUSR | stat.S_IXUSR)  # read + execute, no write
        try:
            with caplog.at_level(logging.WARNING, logger="solver.run_log"):
                out = run_log.record_plan(
                    _sample_result(),
                    project_root=root,
                    run_id="2026-05-07T12:00:00Z-locked001",
                )
        finally:
            # Restore so pytest's cleanup can remove the tmp tree.
            rdir.chmod(stat.S_IRWXU)
        assert out is None
        # A warning must have been emitted.
        assert any("record_plan" in rec.message for rec in caplog.records)

    def test_status_pulled_from_top_level(self, tmp_path: Path) -> None:
        root = _make_specify_root(tmp_path)
        result = _sample_result()
        result["status"] = "FEASIBLE"
        out = run_log.record_plan(
            result, project_root=root, run_id="2026-05-07T12:00:00Z-feas0001"
        )
        assert out is not None
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["status"] == "FEASIBLE"


# ---------------------------------------------------------------------------
# append_actual
# ---------------------------------------------------------------------------


class TestAppendActual:
    def test_creates_file_on_first_append(self, tmp_path: Path) -> None:
        root = _make_specify_root(tmp_path)
        path = run_log.append_actual(
            task_id="T001",
            agent_id="opus",
            actual_duration=92.0,
            run_id="2026-05-07T12:00:00Z-abc01234",
            project_root=root,
        )
        assert path.exists()
        # File ends with the JSONL we just wrote.
        contents = path.read_text(encoding="utf-8").splitlines()
        assert len(contents) == 1
        row = json.loads(contents[0])
        assert row["task_id"] == "T001"
        assert row["agent_id"] == "opus"
        assert row["actual_duration"] == 92.0
        assert row["completed_at"] is not None
        assert row["notes"] is None

    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        root = _make_specify_root(tmp_path)
        rid = "2026-05-07T12:00:00Z-abc01234"
        run_log.append_actual("T001", "opus", 92.0, rid, project_root=root)
        run_log.append_actual("T002", "sonnet", 41.0, rid, project_root=root)
        path = runs_dir(root) / f"{rid}-actual.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert [r["task_id"] for r in rows] == ["T001", "T002"]
        assert [r["actual_duration"] for r in rows] == [92.0, 41.0]

    def test_notes_and_completed_at_pass_through(self, tmp_path: Path) -> None:
        root = _make_specify_root(tmp_path)
        path = run_log.append_actual(
            "T001",
            "opus",
            100.0,
            "2026-05-07T12:00:00Z-zzz99999",
            project_root=root,
            notes="restarted once",
            completed_at="2026-05-07T13:30:00Z",
        )
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert row["notes"] == "restarted once"
        assert row["completed_at"] == "2026-05-07T13:30:00Z"


# ---------------------------------------------------------------------------
# CLI: append-actual subcommand
# ---------------------------------------------------------------------------


class TestCli:
    def test_append_actual_subcommand(self, tmp_path: Path) -> None:
        root = _make_specify_root(tmp_path)
        rc = run_log.main(
            [
                "append-actual",
                "--run-id",
                "2026-05-07T12:00:00Z-cli00001",
                "--task",
                "T001",
                "--agent",
                "opus",
                "--duration",
                "85.5",
                "--project-root",
                str(root),
            ]
        )
        assert rc == 0
        target = runs_dir(root) / "2026-05-07T12:00:00Z-cli00001-actual.jsonl"
        assert target.exists()
        row = json.loads(target.read_text(encoding="utf-8").splitlines()[0])
        assert row["task_id"] == "T001"
        assert row["actual_duration"] == 85.5
