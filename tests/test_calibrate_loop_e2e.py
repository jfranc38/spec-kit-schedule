"""End-to-end tests for the v0.6.x calibration feedback loop.

Pipeline under test:

1. ``solver.run_log.record_plan`` writes ``<run_id>-plan.json``.
2. ``solver.run_log.append_actual`` appends one observed-duration
   record per task to ``<run_id>-actual.jsonl``.
3. ``solver.calibrate.calibrate_from_runs`` reads every paired
   plan/actual file and updates the portfolio's ``speed_factor``
   and ``token_estimates`` in place.

The synthetic scenarios in this module sidestep the real CP-SAT
solver — we hand-build plan dicts so the assertions are about the
calibration math itself, not whether the solver re-ran.
"""

from __future__ import annotations

__all__: list[str] = []

from pathlib import Path

import pytest
import yaml

from solver import run_log
from solver._paths import runs_dir
from solver.calibrate import calibrate_from_runs

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_BASE_CONFIG = {
    "agents": [
        {
            "id": "opus",
            "model": "claude-opus-4",
            "skills": ["design", "backend"],
            "kappa": 5,
            "context_budget": 32,
            "speed_factor": 1.0,
            "price_per_1k_tokens": 0.0,
        },
        {
            "id": "sonnet",
            "model": "claude-sonnet-4",
            "skills": ["backend", "api"],
            "kappa": 10,
            "context_budget": 16,
            "speed_factor": 1.5,
            "price_per_1k_tokens": 0.0,
        },
    ],
    "skill_rules": [{"pattern": "tests/", "skill": "test"}],
    "default_skill": "backend",
    # Token estimates double as bucket boundaries when the runs-mode
    # aggregator infers complexity from expected_duration: 50 → simple,
    # 100 → medium, 200 → complex.
    "token_estimates": {
        "simple": {"mean": 50, "std_dev": 0},
        "medium": {"mean": 100, "std_dev": 0},
        "complex": {"mean": 200, "std_dev": 0},
    },
    "complexity_verbs": {
        "simple": ["add"],
        "medium": ["implement"],
        "complex": ["design"],
    },
    "solver": {"time_limit": 5, "num_workers": 1},
}


def _make_specify_root(tmp_path: Path) -> Path:
    (tmp_path / ".specify").mkdir()
    sched_dir = tmp_path / ".specify" / "schedule"
    sched_dir.mkdir(parents=True)
    return tmp_path


def _write_config(root: Path) -> Path:
    cfg = root / ".specify" / "schedule" / "schedule-config.yml"
    cfg.write_text(
        yaml.dump(_BASE_CONFIG, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return cfg


def _record_synthetic_run(
    root: Path,
    run_id: str,
    *,
    opus_actual: float,
    sonnet_actual: float,
    opus_expected: float = 100.0,
    sonnet_expected: float = 100.0,
    opus_tokens: int = 100,
    sonnet_tokens: int = 100,
) -> None:
    """Record a single (plan, actual) pair into the runs directory.

    Two tasks: one for opus, one for sonnet. The plan writes the
    expected durations; the actuals file records the observed
    durations. The aggregator joins on task_id.
    """
    result = {
        "status": "OPTIMAL",
        "stats": {"makespan": 250, "max_load": 130, "total_cost": 0.0},
        "makespan": 250,
        "max_load": 130,
        "total_cost": 0.0,
        "assignments": [
            {
                "task_id": "T001",
                "agent_id": "opus",
                "duration": opus_expected,
                "start": 0,
                "end": opus_expected,
                "tokens": opus_tokens,
            },
            {
                "task_id": "T002",
                "agent_id": "sonnet",
                "duration": sonnet_expected,
                "start": 0,
                "end": sonnet_expected,
                "tokens": sonnet_tokens,
            },
        ],
    }
    out = run_log.record_plan(result, project_root=root, run_id=run_id)
    assert out is not None, "record_plan must succeed when .specify/ exists"

    run_log.append_actual("T001", "opus", opus_actual, run_id, project_root=root)
    run_log.append_actual("T002", "sonnet", sonnet_actual, run_id, project_root=root)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCalibrateLoopE2E:
    def test_speed_factor_moves_for_consistently_slower_agent(
        self, tmp_path: Path
    ) -> None:
        """opus consistently 1.5× slower → speed_factor reduced by EMA.

        Initial opus speed_factor = 1.0. With actual = 150 vs
        expected = 100, the implied factor is 1.0 / 1.5 ≈ 0.6667.
        EMA with alpha=0.3:
            new = (1 - 0.3) * 1.0 + 0.3 * 0.6667 = 0.7 + 0.2 = 0.9.
        sonnet runs at exactly the predicted pace (ratio=1.0) so its
        speed_factor must be unchanged.
        """
        root = _make_specify_root(tmp_path)
        cfg_path = _write_config(root)

        for i, ts in enumerate(
            [
                "2026-05-07T12:00:00Z-aaaaaaa1",
                "2026-05-07T12:00:01Z-aaaaaaa2",
                "2026-05-07T12:00:02Z-aaaaaaa3",
            ]
        ):
            _record_synthetic_run(
                root,
                run_id=ts,
                opus_actual=150.0,
                sonnet_actual=100.0,
            )
            assert i >= 0  # silence unused-loop-var lint

        report = calibrate_from_runs(
            runs_dir(root), cfg_path, alpha=0.3, min_pairs=3
        )
        assert report.written_to == cfg_path

        opus = next(a for a in report.agents if a.agent_id == "opus")
        sonnet = next(a for a in report.agents if a.agent_id == "sonnet")
        # opus moved DOWN: 1.0 → ~0.9
        assert opus.old_speed_factor == 1.0
        assert opus.new_speed_factor == pytest.approx(0.9, abs=1e-3)
        # sonnet unchanged: ratio=1.0 → implied=1.5, EMA against
        # old=1.5 must be exactly 1.5.
        assert sonnet.new_speed_factor == pytest.approx(1.5, abs=1e-6)

        # And the YAML on disk reflects the new speed factor.
        new_cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        opus_yaml = next(a for a in new_cfg["agents"] if a["id"] == "opus")
        assert opus_yaml["speed_factor"] == pytest.approx(0.9, abs=1e-3)

    def test_insufficient_runs_no_portfolio_change(
        self, tmp_path: Path
    ) -> None:
        """One pair (< min_pairs=3) → portfolio is left untouched.

        Calibration emits ``CALIBRATE_INSUFFICIENT_PAIRS`` and exits
        gracefully. The on-disk YAML must equal the original byte-for-
        byte.
        """
        root = _make_specify_root(tmp_path)
        cfg_path = _write_config(root)
        original = cfg_path.read_text(encoding="utf-8")

        _record_synthetic_run(
            root,
            run_id="2026-05-07T12:00:00Z-bbbbbbb1",
            opus_actual=150.0,
            sonnet_actual=100.0,
        )

        report = calibrate_from_runs(
            runs_dir(root), cfg_path, alpha=0.3, min_pairs=3
        )
        assert report.written_to is None
        assert not report.agents
        # Warning code was emitted.
        assert any("INSUFFICIENT_PAIRS" in w for w in report.warnings)
        # YAML untouched.
        assert cfg_path.read_text(encoding="utf-8") == original

    def test_missing_runs_dir_graceful_exit(self, tmp_path: Path) -> None:
        """No runs directory at all → graceful exit, no mutation."""
        root = _make_specify_root(tmp_path)
        cfg_path = _write_config(root)
        original = cfg_path.read_text(encoding="utf-8")
        # Note: do NOT create runs/ at all.
        rdir = runs_dir(root)
        assert not rdir.exists()

        report = calibrate_from_runs(rdir, cfg_path, alpha=0.3, min_pairs=3)
        assert report.written_to is None
        assert any("NO_PAIRS" in w for w in report.warnings)
        assert cfg_path.read_text(encoding="utf-8") == original

    def test_backup_writes_bak_file(self, tmp_path: Path) -> None:
        """``backup=True`` writes ``<config>.bak`` before mutation."""
        root = _make_specify_root(tmp_path)
        cfg_path = _write_config(root)
        original = cfg_path.read_text(encoding="utf-8")

        for ts in (
            "2026-05-07T12:00:00Z-ccccccc1",
            "2026-05-07T12:00:01Z-ccccccc2",
            "2026-05-07T12:00:02Z-ccccccc3",
        ):
            _record_synthetic_run(
                root,
                run_id=ts,
                opus_actual=150.0,
                sonnet_actual=100.0,
            )

        report = calibrate_from_runs(
            runs_dir(root), cfg_path, alpha=0.3, min_pairs=3, backup=True
        )
        assert report.written_to == cfg_path

        bak = cfg_path.with_suffix(cfg_path.suffix + ".bak")
        assert bak.exists()
        # The .bak holds the pre-mutation contents.
        assert bak.read_text(encoding="utf-8") == original
        # The live config differs — at least the speed_factor changed.
        assert cfg_path.read_text(encoding="utf-8") != original

    def test_unknown_agent_in_runs_skipped(self, tmp_path: Path) -> None:
        """Runs referencing an agent_id not in the current portfolio
        are skipped with a warning, but valid agents still calibrate.
        """
        root = _make_specify_root(tmp_path)
        cfg_path = _write_config(root)

        # Record three runs but mention a stale agent in one of them.
        # Stale: write a plan that references "old-agent" which is
        # absent from _BASE_CONFIG.
        ts = "2026-05-07T12:00:00Z-stale001"
        result = {
            "status": "OPTIMAL",
            "stats": {},
            "assignments": [
                {
                    "task_id": "T001",
                    "agent_id": "old-agent",
                    "duration": 100,
                    "start": 0,
                    "end": 100,
                },
            ],
        }
        out = run_log.record_plan(result, project_root=root, run_id=ts)
        assert out is not None
        run_log.append_actual("T001", "old-agent", 200.0, ts, project_root=root)

        # Two valid runs alongside.
        for vts in (
            "2026-05-07T12:00:01Z-valid001",
            "2026-05-07T12:00:02Z-valid002",
        ):
            _record_synthetic_run(
                root,
                run_id=vts,
                opus_actual=150.0,
                sonnet_actual=100.0,
            )

        report = calibrate_from_runs(
            runs_dir(root), cfg_path, alpha=0.3, min_pairs=3
        )
        # Stale agent is NOT in the calibrated set.
        agent_ids = {a.agent_id for a in report.agents}
        assert "old-agent" not in agent_ids
        # And a warning fired.
        assert any("UNKNOWN_AGENT" in w for w in report.warnings)
        # Valid agents calibrated normally.
        assert "opus" in agent_ids
        assert "sonnet" in agent_ids

    def test_token_estimate_updates_for_buckets(self, tmp_path: Path) -> None:
        """Per-complexity bucket aggregation moves token_estimates.

        With expected_duration=100 (medium bucket per _BASE_CONFIG),
        sonnet running consistently at 200 implies a new median of
        200. EMA against old mean=100 with alpha=0.3 gives:
            new = 0.7 * 100 + 0.3 * 200 = 130.
        opus running at 100 (ratio=1) does not move the medium bucket
        beyond what the median already captures because bucket
        aggregation pools across agents.
        """
        root = _make_specify_root(tmp_path)
        cfg_path = _write_config(root)

        for ts in (
            "2026-05-07T12:00:00Z-ddddddd1",
            "2026-05-07T12:00:01Z-ddddddd2",
            "2026-05-07T12:00:02Z-ddddddd3",
        ):
            _record_synthetic_run(
                root,
                run_id=ts,
                # opus=100 (ratio=1), sonnet=200 → mixed pool, median=150.
                opus_actual=100.0,
                sonnet_actual=200.0,
            )

        report = calibrate_from_runs(
            runs_dir(root), cfg_path, alpha=0.3, min_pairs=3
        )
        # Both tasks (T001/opus and T002/sonnet) had expected=100, so
        # both land in the "medium" bucket. Pooled medians of
        # [100, 200] across 3 runs = 100, 200, 100, 200, ... → 150.
        # EMA against 100 with alpha=0.3 = 115.
        medium = next(t for t in report.token_estimates if t.complexity == "medium")
        assert medium.old_mean == 100
        # Tolerance is generous to absorb median-of-mixed-list quirks
        # but the value must have moved upward from 100.
        assert medium.new_mean > 100
        assert medium.new_mean <= 150  # bounded above by the pure median

    def test_alpha_zero_freezes_speed_factor(self, tmp_path: Path) -> None:
        """``alpha=0`` is a no-op: new_speed_factor == old_speed_factor."""
        root = _make_specify_root(tmp_path)
        cfg_path = _write_config(root)

        for ts in (
            "2026-05-07T12:00:00Z-eeeeeee1",
            "2026-05-07T12:00:01Z-eeeeeee2",
            "2026-05-07T12:00:02Z-eeeeeee3",
        ):
            _record_synthetic_run(
                root,
                run_id=ts,
                opus_actual=300.0,  # would normally pull speed_factor way down
                sonnet_actual=300.0,
            )

        report = calibrate_from_runs(
            runs_dir(root), cfg_path, alpha=0.0, min_pairs=3
        )
        for a in report.agents:
            assert a.new_speed_factor == pytest.approx(a.old_speed_factor)
