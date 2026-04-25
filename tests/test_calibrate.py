"""Tests for solver.calibrate — calibration logic and CLI."""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from solver.calibrate import (
    CalibrationReport,
    calibrate,
    main,
)
from solver.validation import ScheduleInputError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MINIMAL_CONFIG = {
    "agents": [
        {
            "id": "backend-1",
            "model": "claude-sonnet-4",
            "skills": ["backend", "api"],
            "kappa": 10,
            "context_budget": 16,
            "speed_factor": 1.0,
            "price_per_1k_tokens": 0.0,
        },
        {
            "id": "frontend-1",
            "model": "claude-sonnet-4",
            "skills": ["frontend", "react"],
            "kappa": 10,
            "context_budget": 16,
            "speed_factor": 1.0,
            "price_per_1k_tokens": 0.0,
        },
        {
            "id": "tester-1",
            "model": "claude-haiku-4.5",
            "skills": ["test"],
            "kappa": 15,
            "context_budget": 8,
            "speed_factor": 1.5,
            "price_per_1k_tokens": 0.0,
        },
        {
            "id": "architect-1",
            "model": "claude-opus-4",
            "skills": ["design", "architecture"],
            "kappa": 5,
            "context_budget": 32,
            "speed_factor": 0.8,
            "price_per_1k_tokens": 0.0,
        },
        {
            "id": "docs-1",
            "model": "claude-sonnet-4",
            "skills": ["docs", "review"],
            "kappa": 10,
            "context_budget": 16,
            "speed_factor": 1.0,
            "price_per_1k_tokens": 0.0,
        },
    ],
    "skill_rules": [
        {"pattern": "tests/", "skill": "test"},
        {"pattern": "src/api/", "skill": "api"},
    ],
    "default_skill": "backend",
    "token_estimates": {
        "simple": {"mean": 1500, "std_dev": 0},
        "medium": {"mean": 3500, "std_dev": 500},
        "complex": {"mean": 6000, "std_dev": 0},
        "review": {"mean": 2000, "std_dev": 0},
    },
    "complexity_verbs": {
        "simple": ["add", "update"],
        "medium": ["implement", "create"],
        "complex": ["design"],
        "review": ["review"],
    },
    "solver": {"time_limit": 10, "num_workers": 1},
}

_AGENT_IDS = ["backend-1", "frontend-1", "tester-1", "architect-1", "docs-1"]


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "schedule-config.yml"
    config_path.write_text(
        yaml.dump(_MINIMAL_CONFIG, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return config_path


def _make_run(
    task_id: str,
    agent_id: str,
    complexity: str = "medium",
    predicted: float = 35.0,
    actual: float = 35.0,
    estimated_tokens: int = 3500,
    actual_tokens: int = 3500,
    status: str = "success",
) -> dict:
    return {
        "task_id": task_id,
        "agent_id": agent_id,
        "model": "claude-sonnet-4",
        "skill": "backend",
        "complexity": complexity,
        "estimated_tokens": estimated_tokens,
        "predicted_duration": predicted,
        "actual_duration": actual,
        "actual_tokens": actual_tokens,
        "start_ts": "2026-04-24T10:00:00Z",
        "end_ts": "2026-04-24T10:41:00Z",
        "status": status,
    }


def _write_runs(tmp_path: Path, rows: list[dict], filename: str = "runs.jsonl") -> Path:
    runs_path = tmp_path / filename
    runs_path.write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
    )
    return runs_path


# ---------------------------------------------------------------------------
# Core calibration tests
# ---------------------------------------------------------------------------

class TestCalibrate:
    def test_five_agents_four_runs_each(self, tmp_path: Path) -> None:
        """20 rows (5 agents × 4 runs) → all agents calibrated, report shows all."""
        rows = []
        for i, aid in enumerate(_AGENT_IDS):
            for j in range(4):
                rows.append(_make_run(
                    f"T{i:02d}{j:02d}",
                    aid,
                    predicted=40.0,
                    actual=32.0,  # agent is faster than predicted → ratio > 1
                ))
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(runs_path, config_path, dry_run=True, confidence_threshold=3)

        assert report.runs_analysed == 20
        agent_ids_in_report = {a.agent_id for a in report.agents}
        assert agent_ids_in_report == set(_AGENT_IDS)

    def test_speed_factor_updated_above_threshold(self, tmp_path: Path) -> None:
        """With 6 samples, confidence='medium' or 'high' → speed_factor is updated."""
        rows = [
            _make_run(f"T{j:03d}", "backend-1", predicted=40.0, actual=32.0)
            for j in range(6)
        ]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(
            runs_path, config_path, dry_run=True,
            confidence_threshold=5, ema_alpha=0.3,
        )
        ac = next(a for a in report.agents if a.agent_id == "backend-1")
        # ratio = 40/32 = 1.25; raw_new = 1.25 * 1.0 = 1.25; new = 1.0 + 0.3*(1.25-1.0)=1.075
        assert ac.confidence == "medium"
        assert abs(ac.new_speed_factor - 1.075) < 1e-4

    def test_low_confidence_value_preserved(self, tmp_path: Path) -> None:
        """Fewer than confidence_threshold samples → low confidence, old value kept."""
        rows = [
            _make_run(f"T{j:03d}", "backend-1", predicted=40.0, actual=20.0)
            for j in range(3)  # below threshold=5
        ]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(
            runs_path, config_path, dry_run=True, confidence_threshold=5
        )
        ac = next(a for a in report.agents if a.agent_id == "backend-1")
        assert ac.confidence == "low"
        assert ac.new_speed_factor == ac.old_speed_factor  # unchanged

    def test_low_confidence_in_warning_list(self, tmp_path: Path) -> None:
        """Low-confidence agent appears in report warnings."""
        rows = [_make_run("T001", "backend-1", predicted=40.0, actual=20.0)]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(
            runs_path, config_path, dry_run=True, confidence_threshold=5
        )
        warning_text = " ".join(report.warnings)
        assert "backend-1" in warning_text

    def test_missing_keys_row_skipped(self, tmp_path: Path) -> None:
        """Rows missing required keys are skipped and counted."""
        rows = [
            {"task_id": "T001", "agent_id": "backend-1"},  # missing many keys
            _make_run("T002", "backend-1"),  # valid
        ]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(
            runs_path, config_path, dry_run=True, confidence_threshold=1
        )
        assert report.runs_skipped >= 1

    def test_non_success_status_skipped(self, tmp_path: Path) -> None:
        """Rows with status != 'success' are excluded from calibration."""
        rows = [
            _make_run("T001", "backend-1", status="failed"),
            _make_run("T002", "backend-1", status="timeout"),
            _make_run("T003", "backend-1", status="success"),
        ]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(
            runs_path, config_path, dry_run=True, confidence_threshold=1
        )
        # Only 1 successful row
        assert report.runs_analysed == 1
        # 2 non-success rows counted as skipped
        assert report.runs_skipped >= 2

    def test_dry_run_does_not_modify_config(self, tmp_path: Path) -> None:
        """With dry_run=True, the config file on disk is not modified."""
        rows = [
            _make_run(f"T{j:03d}", "backend-1", predicted=40.0, actual=20.0)
            for j in range(10)
        ]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)
        original_text = config_path.read_text(encoding="utf-8")

        report = calibrate(
            runs_path, config_path, dry_run=True, confidence_threshold=5
        )

        assert report.dry_run is True
        assert report.written_to is None
        assert config_path.read_text(encoding="utf-8") == original_text

    def test_non_dry_run_writes_config(self, tmp_path: Path) -> None:
        """Without dry_run, config is updated on disk."""
        rows = [
            _make_run(f"T{j:03d}", "backend-1", predicted=40.0, actual=32.0)
            for j in range(10)
        ]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(
            runs_path, config_path, dry_run=False, confidence_threshold=5, ema_alpha=0.3
        )

        assert report.written_to == config_path
        updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        backend = next(a for a in updated["agents"] if a["id"] == "backend-1")
        # speed_factor should have changed
        assert abs(backend["speed_factor"] - 1.0) > 0.001

    def test_ema_perfect_prediction_unchanged(self, tmp_path: Path) -> None:
        """If ratios=1.0 (perfect prediction), new_speed_factor == old."""
        rows = [
            _make_run(f"T{j:03d}", "backend-1", predicted=35.0, actual=35.0)
            for j in range(10)
        ]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(
            runs_path, config_path, dry_run=True, confidence_threshold=5, ema_alpha=0.3
        )
        ac = next(a for a in report.agents if a.agent_id == "backend-1")
        # ratio=1.0, raw_new=1.0*1.0=1.0, new=1.0+0.3*(1.0-1.0)=1.0
        assert abs(ac.new_speed_factor - 1.0) < 1e-6

    def test_token_bucket_calibration(self, tmp_path: Path) -> None:
        """5 'medium' rows with actual_tokens ~N(4000,300) → new_mean≈4000, std>0."""
        import random
        rng = random.Random(42)
        actual_tokens_list = [round(rng.gauss(4000, 300)) for _ in range(5)]
        rows = [
            _make_run(f"T{j:03d}", "backend-1", complexity="medium",
                      actual_tokens=actual_tokens_list[j])
            for j in range(5)
        ]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(
            runs_path, config_path, dry_run=True, confidence_threshold=5
        )
        medium_tc = next(
            (tc for tc in report.token_estimates if tc.complexity == "medium"), None
        )
        assert medium_tc is not None
        assert medium_tc.n_samples == 5
        # With 5 samples and threshold=5, confidence is medium (>=5 samples)
        # mean should be close to 4000
        assert abs(medium_tc.new_mean - round(statistics.mean(actual_tokens_list))) <= 1

    def test_token_calibration_stdev_positive(self, tmp_path: Path) -> None:
        """With multiple samples, std_dev > 0."""
        rows = [
            _make_run(f"T{j:03d}", "backend-1", complexity="medium",
                      actual_tokens=3500 + j * 200)
            for j in range(10)
        ]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(
            runs_path, config_path, dry_run=True, confidence_threshold=5
        )
        medium_tc = next(tc for tc in report.token_estimates if tc.complexity == "medium")
        assert medium_tc.new_std_dev > 0

    def test_unknown_agent_in_runs_gets_warning(self, tmp_path: Path) -> None:
        """An agent_id in runs.jsonl not present in config is warned and skipped."""
        rows = [
            _make_run(f"T{j:03d}", "ghost-agent", predicted=35.0, actual=35.0)
            for j in range(10)
        ]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(
            runs_path, config_path, dry_run=True, confidence_threshold=5
        )
        warning_text = " ".join(report.warnings)
        assert "ghost-agent" in warning_text
        agent_ids = {a.agent_id for a in report.agents}
        assert "ghost-agent" not in agent_ids

    def test_high_confidence_requires_double_threshold(self, tmp_path: Path) -> None:
        """confidence='high' requires n >= threshold*2."""
        rows = [
            _make_run(f"T{j:03d}", "backend-1", predicted=40.0, actual=32.0)
            for j in range(12)
        ]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(
            runs_path, config_path, dry_run=True, confidence_threshold=5
        )
        ac = next(a for a in report.agents if a.agent_id == "backend-1")
        assert ac.confidence == "high"  # 12 >= 5*2

    def test_medium_confidence(self, tmp_path: Path) -> None:
        """5 samples with threshold=5 → 'medium' confidence."""
        rows = [
            _make_run(f"T{j:03d}", "backend-1", predicted=40.0, actual=32.0)
            for j in range(5)
        ]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(
            runs_path, config_path, dry_run=True, confidence_threshold=5
        )
        ac = next(a for a in report.agents if a.agent_id == "backend-1")
        assert ac.confidence == "medium"  # 5 >= 5 but < 10

    def test_empty_runs_file(self, tmp_path: Path) -> None:
        """Empty runs.jsonl → 0 analysed, 0 skipped, no calibrations."""
        runs_path = tmp_path / "runs.jsonl"
        runs_path.write_text("", encoding="utf-8")
        config_path = _write_config(tmp_path)

        report = calibrate(runs_path, config_path, dry_run=True, confidence_threshold=5)
        assert report.runs_analysed == 0
        assert report.agents == []

    def test_report_dataclass_fields(self, tmp_path: Path) -> None:
        """CalibrationReport is a proper dataclass with expected fields."""
        rows = [_make_run("T001", "backend-1")]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        report = calibrate(runs_path, config_path, dry_run=True, confidence_threshold=1)
        assert isinstance(report, CalibrationReport)
        assert isinstance(report.agents, list)
        assert isinstance(report.token_estimates, list)
        assert isinstance(report.runs_analysed, int)
        assert isinstance(report.runs_skipped, int)

    def test_missing_runs_file_raises(self, tmp_path: Path) -> None:
        """Non-existent runs.jsonl raises ScheduleInputError."""
        config_path = _write_config(tmp_path)
        with pytest.raises(ScheduleInputError, match="Cannot read"):
            calibrate(tmp_path / "nonexistent.jsonl", config_path, dry_run=True)


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestCalibrateCLI:
    def test_dry_run_cli(self, tmp_path: Path, capsys) -> None:
        """--dry-run flag prevents disk write."""
        rows = [_make_run(f"T{j:03d}", "backend-1") for j in range(10)]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)
        original = config_path.read_text(encoding="utf-8")

        main([
            "--runs", str(runs_path),
            "--config", str(config_path),
            "--dry-run",
            "--confidence-threshold", "5",
        ])
        assert config_path.read_text(encoding="utf-8") == original

    def test_cli_help_works(self) -> None:
        """--help exits with code 0."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_module_invocation_help(self) -> None:
        """python -m solver.calibrate --help works as subprocess."""
        result = subprocess.run(
            [sys.executable, "-m", "solver.calibrate", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parents[1]),
        )
        assert result.returncode == 0
        assert "--runs" in result.stdout

    def test_missing_required_args_exits_nonzero(self) -> None:
        """Missing --runs or --config exits with non-zero code."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", "x.yml"])  # missing --runs
        assert exc_info.value.code != 0

    def test_cli_output_shows_table(self, tmp_path: Path, capsys) -> None:
        """Markdown report contains agent table header."""
        rows = [_make_run(f"T{j:03d}", "backend-1") for j in range(10)]
        runs_path = _write_runs(tmp_path, rows)
        config_path = _write_config(tmp_path)

        main([
            "--runs", str(runs_path),
            "--config", str(config_path),
            "--dry-run",
        ])
        captured = capsys.readouterr()
        assert "Agent Speed Factors" in captured.out
        assert "Token Estimates" in captured.out


class TestCalibrateEdgeCases:
    def test_bad_json_line_skipped(self, tmp_path: Path) -> None:
        """Invalid JSON lines in runs.jsonl are skipped with a warning."""
        config_path = _write_config(tmp_path)
        runs_path = tmp_path / "runs.jsonl"
        runs_path.write_text(
            'not json at all\n' + json.dumps(_make_run("T001", "backend-1")) + "\n",
            encoding="utf-8",
        )
        report = calibrate(runs_path, config_path, dry_run=True, confidence_threshold=1)
        assert report.runs_skipped >= 1
        warning_text = " ".join(report.warnings)
        assert "CALIBRATE_PARSE_ERROR" in warning_text

    def test_zero_actual_duration_skipped(self, tmp_path: Path) -> None:
        """Rows with actual_duration == 0 are skipped."""
        row = _make_run("T001", "backend-1", actual=0.0)
        rows_path = _write_runs(tmp_path, [row])
        config_path = _write_config(tmp_path)
        report = calibrate(rows_path, config_path, dry_run=True, confidence_threshold=1)
        warning_text = " ".join(report.warnings)
        assert "CALIBRATE_ZERO_DURATION" in warning_text or report.runs_analysed == 0

    def test_nonnumeric_actual_tokens_skipped(self, tmp_path: Path) -> None:
        """Rows with non-numeric actual_tokens produce a warning."""
        row = {**_make_run("T001", "backend-1"), "actual_tokens": "bad"}
        rows_path = _write_runs(tmp_path, [row])
        config_path = _write_config(tmp_path)
        report = calibrate(rows_path, config_path, dry_run=True, confidence_threshold=1)
        warning_text = " ".join(report.warnings)
        assert "CALIBRATE_BAD_TOKENS" in warning_text

    def test_nonnumeric_duration_skipped(self, tmp_path: Path) -> None:
        """Rows with non-numeric duration fields are skipped."""
        row = {**_make_run("T001", "backend-1"), "actual_duration": "bad"}
        rows_path = _write_runs(tmp_path, [row])
        config_path = _write_config(tmp_path)
        report = calibrate(rows_path, config_path, dry_run=True, confidence_threshold=1)
        warning_text = " ".join(report.warnings)
        assert "CALIBRATE_BAD_DURATION" in warning_text

    def test_blank_lines_ignored(self, tmp_path: Path) -> None:
        """Blank lines in runs.jsonl are silently ignored."""
        runs_path = tmp_path / "runs.jsonl"
        rows = [_make_run(f"T{j:03d}", "backend-1") for j in range(3)]
        content = "\n".join(json.dumps(r) for r in rows)
        content = "\n\n" + content + "\n\n"
        runs_path.write_text(content, encoding="utf-8")
        config_path = _write_config(tmp_path)
        report = calibrate(runs_path, config_path, dry_run=True, confidence_threshold=1)
        assert report.runs_analysed == 3

    def test_written_to_is_none_on_dry_run(self, tmp_path: Path) -> None:
        """dry_run=True → CalibrationReport.written_to is None."""
        rows = [_make_run("T001", "backend-1")]
        config_path = _write_config(tmp_path)
        runs_path = _write_runs(tmp_path, rows)
        report = calibrate(runs_path, config_path, dry_run=True, confidence_threshold=1)
        assert report.written_to is None
        assert report.dry_run is True

    def test_report_written_to_path_after_write(self, tmp_path: Path) -> None:
        """dry_run=False → CalibrationReport.written_to points to config."""
        rows = [_make_run(f"T{j:03d}", "backend-1") for j in range(10)]
        config_path = _write_config(tmp_path)
        runs_path = _write_runs(tmp_path, rows)
        report = calibrate(
            runs_path, config_path, dry_run=False, confidence_threshold=5
        )
        assert report.written_to == config_path

    def test_token_estimates_section_written(self, tmp_path: Path) -> None:
        """After non-dry-run calibration, updated token_estimates appear in YAML."""
        rows = [
            _make_run(f"T{j:03d}", "backend-1", complexity="simple",
                      actual_tokens=2000 + j * 50)
            for j in range(10)
        ]
        config_path = _write_config(tmp_path)
        runs_path = _write_runs(tmp_path, rows)
        calibrate(runs_path, config_path, dry_run=False, confidence_threshold=5)
        updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        simple_te = updated["token_estimates"]["simple"]
        # Should be a dict {mean: ..., std_dev: ...} now
        assert isinstance(simple_te, dict)
        assert simple_te["mean"] > 1500  # actual tokens are larger than default

    def test_spec_dir_detected_as_tests(self, tmp_path: Path) -> None:
        """calibrate does not crash on config with spec/ in skill_rules."""
        # This tests the config loading path
        rows = [_make_run("T001", "backend-1")]
        config_path = _write_config(tmp_path)
        runs_path = _write_runs(tmp_path, rows)
        # Just make sure it doesn't error
        report = calibrate(runs_path, config_path, dry_run=True, confidence_threshold=1)
        assert isinstance(report, CalibrationReport)
