"""Ingest real execution logs and update schedule-config.yml estimates.

Usage (module):
    from solver.calibrate import calibrate
    report = calibrate(Path("runs.jsonl"), Path("schedule-config.yml"), dry_run=True)

Usage (CLI):
    python -m solver.calibrate --runs runs.jsonl --config schedule-config.yml
                               [--dry-run] [--confidence-threshold 5] [--ema-alpha 0.3]
"""

from __future__ import annotations

__all__ = [
    "AgentCalibration",
    "TokenCalibration",
    "CalibrationReport",
    "calibrate",
    "calibrate_from_runs",
]

import argparse
import contextlib
import json
import logging
import os
import statistics
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped, unused-ignore]

from .config_schema import Config, load_config
from .i18n import t
from .validation import ScheduleInputError
from .warnings_collector import WarningCollector

log = logging.getLogger(__name__)

# Required keys in each runs.jsonl row
_REQUIRED_KEYS = frozenset({
    "task_id",
    "agent_id",
    "model",
    "actual_duration",
    "predicted_duration",
    "actual_tokens",
    "estimated_tokens",
    "complexity",
    "status",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AgentCalibration:
    agent_id: str
    n_samples: int
    old_speed_factor: float
    new_speed_factor: float
    confidence: Literal["low", "medium", "high"]


@dataclass
class TokenCalibration:
    complexity: str
    n_samples: int
    old_mean: int
    new_mean: int
    new_std_dev: int
    confidence: Literal["low", "medium", "high"]


@dataclass
class CalibrationReport:
    runs_analysed: int
    runs_skipped: int
    agents: list[AgentCalibration] = field(default_factory=list)
    token_estimates: list[TokenCalibration] = field(default_factory=list)
    config_path: Path = field(default_factory=lambda: Path("."))
    dry_run: bool = False
    written_to: Path | None = None
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _confidence(n: int, threshold: int) -> Literal["low", "medium", "high"]:
    if n >= threshold * 2:
        return "high"
    if n >= threshold:
        return "medium"
    return "low"


def _ema_update(old: float, raw_new: float, alpha: float) -> float:
    """Exponential moving average: new = old + alpha * (raw_new - old)."""
    return old + alpha * (raw_new - old)


def _load_runs(
    runs_path: Path, collector: WarningCollector
) -> tuple[list[dict[str, Any]], int]:
    """Parse runs.jsonl and return (valid_rows, skipped_count)."""
    valid: list[dict[str, Any]] = []
    skipped = 0
    try:
        text = runs_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScheduleInputError(
            t("cannot_read_file", file_kind="runs", path_suffix="", error=exc)
        ) from exc

    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            collector.add(
                "CALIBRATE_PARSE_ERROR",
                f"Line {line_no}: JSON parse error — {exc}",
                line=line_no,
            )
            skipped += 1
            continue

        missing = _REQUIRED_KEYS - set(row.keys())
        if missing:
            collector.add(
                "CALIBRATE_MISSING_KEYS",
                f"Line {line_no}: task {row.get('task_id', '?')!r} missing keys {sorted(missing)} — skipped",
                line=line_no,
                task_id=row.get("task_id"),
            )
            skipped += 1
            continue

        valid.append(row)

    return valid, skipped


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calibrate(
    runs_path: Path,
    config_path: Path,
    *,
    dry_run: bool = False,
    confidence_threshold: int = 5,
    ema_alpha: float = 0.3,
) -> CalibrationReport:
    """Ingest runs.jsonl and update schedule-config.yml speed factors and token estimates.

    Parameters
    ----------
    runs_path:
        Path to a ``runs.jsonl`` file (one JSON object per line).
    config_path:
        Path to an existing ``schedule-config.yml`` to update in-place.
    dry_run:
        When True, compute updates but do not write to disk.
    confidence_threshold:
        Minimum sample count for "medium" confidence. ``threshold*2`` for "high".
        Values below this produce "low" confidence and are NOT written.
    ema_alpha:
        EMA blending factor in [0, 1]. 0 = keep old value; 1 = use raw new value.

    Returns
    -------
    CalibrationReport
        Detailed calibration results including per-agent and per-complexity updates.
    """
    runs_path = Path(runs_path)
    config_path = Path(config_path)

    collector = WarningCollector()

    # Load existing config
    config: Config = load_config(config_path)

    # Parse runs
    valid_rows, skipped = _load_runs(runs_path, collector)

    # Filter only successful runs for speed_factor calculations
    success_rows = [r for r in valid_rows if r["status"] == "success"]
    skipped += len(valid_rows) - len(success_rows)

    # Build per-agent data: agent_id -> list[ratio]
    agent_ratios: dict[str, list[float]] = {}
    for row in success_rows:
        aid = row["agent_id"]
        try:
            actual = float(row["actual_duration"])
            predicted = float(row["predicted_duration"])
        except (TypeError, ValueError):
            collector.add(
                "CALIBRATE_BAD_DURATION",
                f"Task {row['task_id']!r}: non-numeric duration — skipped",
                task_id=row["task_id"],
            )
            skipped += 1
            continue
        if actual <= 0:
            collector.add(
                "CALIBRATE_ZERO_DURATION",
                f"Task {row['task_id']!r}: actual_duration <= 0 — skipped",
                task_id=row["task_id"],
            )
            skipped += 1
            continue
        ratio = predicted / actual
        agent_ratios.setdefault(aid, []).append(ratio)

    # Build per-complexity token data: complexity -> list[actual_tokens]
    complexity_tokens: dict[str, list[int]] = {}
    for row in success_rows:
        comp = row["complexity"]
        try:
            tokens = int(row["actual_tokens"])
        except (TypeError, ValueError):
            collector.add(
                "CALIBRATE_BAD_TOKENS",
                f"Task {row['task_id']!r}: non-numeric actual_tokens — skipped",
                task_id=row["task_id"],
            )
            continue
        complexity_tokens.setdefault(comp, []).append(tokens)

    # ---------------------------------------------------------------------------
    # Build calibrated agent configs
    # ---------------------------------------------------------------------------
    agent_calibrations: list[AgentCalibration] = []

    # Index existing agents by id
    agent_map = {a.id: a for a in config.agents}

    for aid, ratios in agent_ratios.items():
        n = len(ratios)
        conf = _confidence(n, confidence_threshold)

        if aid not in agent_map:
            collector.add(
                "CALIBRATE_UNKNOWN_AGENT",
                f"Agent {aid!r} in runs.jsonl not found in config — skipped",
                agent_id=aid,
            )
            continue

        old_sf = agent_map[aid].speed_factor
        raw_new = statistics.median(ratios) * old_sf
        new_sf = _ema_update(old_sf, raw_new, ema_alpha)

        if conf == "low":
            collector.add(
                "CALIBRATE_LOW_CONFIDENCE_AGENT",
                f"Agent {aid!r}: only {n} samples (threshold={confidence_threshold}) — "
                f"keeping old speed_factor={old_sf:.4f}",
                agent_id=aid,
                n_samples=n,
            )
            new_sf = old_sf  # Do not mutate low-confidence values

        agent_calibrations.append(AgentCalibration(
            agent_id=aid,
            n_samples=n,
            old_speed_factor=old_sf,
            new_speed_factor=round(new_sf, 6),
            confidence=conf,
        ))

    # ---------------------------------------------------------------------------
    # Build calibrated token estimates
    # ---------------------------------------------------------------------------
    token_calibrations: list[TokenCalibration] = []

    # Current token_estimates: complexity -> mean
    existing_te = config.token_estimates  # dict[str, TokenEstimateLike]

    def _old_mean(comp: str) -> int:
        te = existing_te.get(comp)
        if te is None:
            return 0
        if isinstance(te, int):
            return te
        return te.mean  # TokenEstimate

    for comp, token_list in complexity_tokens.items():
        n = len(token_list)
        conf = _confidence(n, confidence_threshold)
        old_m = _old_mean(comp)
        new_m = round(statistics.mean(token_list))
        new_std = round(statistics.stdev(token_list)) if n >= 2 else 0

        if conf == "low":
            collector.add(
                "CALIBRATE_LOW_CONFIDENCE_TOKENS",
                f"Complexity {comp!r}: only {n} samples (threshold={confidence_threshold}) — "
                f"keeping old mean={old_m}",
                complexity=comp,
                n_samples=n,
            )
            new_m = old_m  # Do not mutate low-confidence values
            new_std = 0

        token_calibrations.append(TokenCalibration(
            complexity=comp,
            n_samples=n,
            old_mean=old_m,
            new_mean=new_m,
            new_std_dev=new_std,
            confidence=conf,
        ))

    # ---------------------------------------------------------------------------
    # Build updated config dict
    # ---------------------------------------------------------------------------
    # Load the raw YAML so we can preserve structure/comments as much as possible
    raw_yaml = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    # Apply speed_factor updates (non-low only)
    speed_updates = {
        ac.agent_id: ac.new_speed_factor
        for ac in agent_calibrations
        if ac.confidence != "low"
    }
    for agent_dict in raw_yaml.get("agents", []):
        aid = agent_dict.get("id")
        if aid in speed_updates:
            agent_dict["speed_factor"] = speed_updates[aid]

    # Apply token estimate updates (non-low only)
    token_updates = {
        tc.complexity: {"mean": tc.new_mean, "std_dev": tc.new_std_dev}
        for tc in token_calibrations
        if tc.confidence != "low"
    }
    if token_updates:
        te_section = raw_yaml.setdefault("token_estimates", {})
        for comp, val in token_updates.items():
            te_section[comp] = val

    # Collect warnings as strings for the report
    warning_strings = [
        f"[{w.code}] {w.message}" for w in collector
    ]

    report = CalibrationReport(
        runs_analysed=len(success_rows),
        runs_skipped=skipped,
        agents=agent_calibrations,
        token_estimates=token_calibrations,
        config_path=config_path,
        dry_run=dry_run,
        written_to=None,
        warnings=warning_strings,
    )

    # ---------------------------------------------------------------------------
    # Write updated config atomically (unless dry_run)
    # ---------------------------------------------------------------------------
    if not dry_run:
        updated_yaml = yaml.dump(raw_yaml, sort_keys=False, default_flow_style=False, allow_unicode=True)
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=config_path.parent, prefix=".calibrate_tmp_"
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(updated_yaml)
            os.replace(str(tmp_path), str(config_path))
        except Exception:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
            raise
        report.written_to = config_path

    # ---------------------------------------------------------------------------
    # Print Markdown report to stdout
    # ---------------------------------------------------------------------------
    _print_report(report)

    return report


# ---------------------------------------------------------------------------
# Aggregate-from-runs (plan.json + actual.jsonl pairs)
# ---------------------------------------------------------------------------


def _list_run_pairs(runs_dir: Path) -> list[tuple[Path, Path]]:
    """Return paired ``(plan, actual)`` files in *runs_dir*.

    A pair exists when both ``<run_id>-plan.json`` and
    ``<run_id>-actual.jsonl`` are present and non-empty. Plans without
    actuals are skipped silently — actuals may legitimately not be
    recorded yet for the most recent solve.
    """
    if not runs_dir.is_dir():
        return []
    pairs: list[tuple[Path, Path]] = []
    for plan in sorted(runs_dir.glob("*-plan.json")):
        run_id = plan.stem.removesuffix("-plan")
        actual = runs_dir / f"{run_id}-actual.jsonl"
        if not actual.is_file():
            continue
        if actual.stat().st_size == 0:
            continue
        pairs.append((plan, actual))
    return pairs


def _read_plan(path: Path, collector: WarningCollector) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        collector.add(
            "CALIBRATE_PLAN_PARSE",
            f"Plan {path.name}: parse error — {exc}; skipped",
            path=str(path),
        )
        return None
    if not isinstance(data, dict):
        collector.add(
            "CALIBRATE_PLAN_PARSE",
            f"Plan {path.name}: top-level value is not an object; skipped",
            path=str(path),
        )
        return None
    return data


def _read_actuals(path: Path, collector: WarningCollector) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        collector.add(
            "CALIBRATE_ACTUAL_PARSE",
            f"Actuals {path.name}: read error — {exc}; skipped",
            path=str(path),
        )
        return rows
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            collector.add(
                "CALIBRATE_ACTUAL_PARSE",
                f"Actuals {path.name}:{line_no}: JSON parse error — {exc}; skipped",
                path=str(path),
                line=line_no,
            )
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _bucket_for_tokens(tokens: int, breakpoints: list[tuple[int, str]]) -> str:
    """Map a token count to its complexity bucket.

    *breakpoints* is a list of ``(upper_bound, name)`` sorted by upper
    bound ascending. The first entry whose ``upper_bound >= tokens``
    wins; if none match, the last entry is returned.
    """
    for upper, name in breakpoints:
        if tokens <= upper:
            return name
    return breakpoints[-1][1]


def _bucket_breakpoints(
    token_estimates: dict[str, Any],
) -> list[tuple[int, str]]:
    """Derive ascending ``(upper_bound, bucket_name)`` from the YAML estimates.

    Each bucket's mean is its UPPER bound (so a task with tokens at or
    below that mean lands in that bucket). Buckets without a numeric
    mean are dropped — calibration cannot infer them.

    Accepts both raw YAML shapes — bare ``int``, ``{"mean": N}`` dict —
    and the pydantic-validated ``TokenEstimate`` model whose ``.mean``
    attribute holds the value. The polymorphic input mirrors
    ``Config.token_estimates`` (annotated ``TokenEstimateLike`` =
    int | TokenEstimate) without coupling this helper to the schema.
    """
    pairs: list[tuple[int, str]] = []
    for name, val in token_estimates.items():
        mean: Any
        if isinstance(val, int):
            mean = val
        elif isinstance(val, dict):
            mean = val.get("mean")
        else:
            # ``TokenEstimate`` (pydantic model) exposes ``.mean``.
            mean = getattr(val, "mean", None)
        if isinstance(mean, int) and mean > 0:
            pairs.append((mean, name))
    pairs.sort(key=lambda kv: kv[0])
    return pairs


def calibrate_from_runs(
    runs_dir: Path,
    config_path: Path,
    *,
    alpha: float = 0.3,
    backup: bool = False,
    min_pairs: int = 3,
) -> CalibrationReport:
    """Aggregate plan/actual pairs in *runs_dir* into a config update.

    Walks every ``*-plan.json`` + ``*-actual.jsonl`` pair under
    *runs_dir*, joins them by ``task_id``, and produces:

    * **Per-agent ``speed_factor``** — implied factor is
      ``actual / expected`` (slower than expected → ratio > 1 → smaller
      ``speed_factor``). Aggregated by ``median`` across all pairs for
      that agent and EMA-smoothed against the existing config.
    * **Per-complexity ``token_estimates``** — bucket inferred from
      each task's ``expected_duration`` interpreted via the existing
      bucket means, then aggregated by ``median`` of actual durations
      and EMA-smoothed against the existing mean.

    The function exits gracefully (logs a warning, no mutation) when:

    * *runs_dir* does not exist;
    * fewer than *min_pairs* paired runs are present;
    * no pair has at least one usable matching task.

    When *backup* is ``True`` the existing config is copied to
    ``<config_path>.bak`` before the in-place rewrite.

    Parameters
    ----------
    runs_dir:
        ``.specify/schedule/runs/``-shaped directory.
    config_path:
        Portfolio YAML file to update in place.
    alpha:
        EMA smoothing factor in [0, 1]. ``0`` keeps the old value;
        ``1`` replaces it with the implied factor outright.
    backup:
        Write ``<config_path>.bak`` before mutation.
    min_pairs:
        Minimum number of paired runs required to perform any
        update. Below this threshold the function logs a warning and
        returns early.

    Returns
    -------
    CalibrationReport
        Same shape used by the log-ingestion path, so callers can
        reuse the printer and downstream tooling.
    """
    runs_dir = Path(runs_dir)
    config_path = Path(config_path)
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0, 1] (got {alpha})")

    collector = WarningCollector()
    config: Config = load_config(config_path)
    pairs = _list_run_pairs(runs_dir)

    if not pairs:
        collector.add(
            "CALIBRATE_NO_PAIRS",
            f"No plan/actual pairs found in {runs_dir} — nothing to calibrate.",
            runs_dir=str(runs_dir),
        )
        report = CalibrationReport(
            runs_analysed=0,
            runs_skipped=0,
            agents=[],
            token_estimates=[],
            config_path=config_path,
            dry_run=True,
            written_to=None,
            warnings=[f"[{w.code}] {w.message}" for w in collector],
        )
        _print_report(report)
        return report

    if len(pairs) < min_pairs:
        collector.add(
            "CALIBRATE_INSUFFICIENT_PAIRS",
            (
                f"Found {len(pairs)} plan/actual pair(s); "
                f"need at least {min_pairs} before calibration is meaningful."
            ),
            runs_dir=str(runs_dir),
            min_pairs=min_pairs,
        )
        report = CalibrationReport(
            runs_analysed=0,
            runs_skipped=0,
            agents=[],
            token_estimates=[],
            config_path=config_path,
            dry_run=True,
            written_to=None,
            warnings=[f"[{w.code}] {w.message}" for w in collector],
        )
        _print_report(report)
        return report

    # Aggregate ratios per agent and actual-durations per complexity.
    agent_ratios: dict[str, list[float]] = {}
    bucket_actuals: dict[str, list[float]] = {}
    pair_count = 0
    skipped = 0
    breakpoints = _bucket_breakpoints(config.token_estimates)

    agent_ids = {a.id for a in config.agents}

    for plan_path, actual_path in pairs:
        plan = _read_plan(plan_path, collector)
        if plan is None:
            skipped += 1
            continue
        # Index plan assignments by task id for O(1) joins.
        plan_by_task: dict[str, dict[str, Any]] = {}
        for assn in plan.get("assignments") or []:
            tid = assn.get("task_id")
            if isinstance(tid, str):
                plan_by_task[tid] = assn

        actuals = _read_actuals(actual_path, collector)
        if not actuals:
            skipped += 1
            continue

        pair_count += 1
        for row in actuals:
            tid = row.get("task_id")
            agent_id = row.get("agent_id")
            actual = row.get("actual_duration")
            if not isinstance(tid, str) or not isinstance(agent_id, str):
                continue
            try:
                actual_f = float(actual)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if actual_f <= 0:
                continue
            assn = plan_by_task.get(tid)
            if assn is None:
                collector.add(
                    "CALIBRATE_RUN_TASK_MISSING",
                    f"Run {plan.get('run_id')!r}: task {tid!r} in actuals not in plan; skipped",
                    run_id=plan.get("run_id"),
                    task_id=tid,
                )
                continue
            expected = assn.get("expected_duration")
            try:
                expected_f = float(expected)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if expected_f <= 0:
                continue
            if agent_id not in agent_ids:
                collector.add(
                    "CALIBRATE_UNKNOWN_AGENT",
                    f"Agent {agent_id!r} from run {plan.get('run_id')!r} not in current config; skipped",
                    agent_id=agent_id,
                    run_id=plan.get("run_id"),
                )
                continue
            agent_ratios.setdefault(agent_id, []).append(actual_f / expected_f)
            # Bucketing uses ``estimated_tokens`` from the plan (mirrors
            # the static config tier means). When a plan was written
            # before the runs-mode update — or by a future writer that
            # drops the field — fall back to skipping that data point
            # for token calibration. Aggregate per-agent calibration
            # already captured the durational signal.
            est_tokens = assn.get("estimated_tokens")
            try:
                tokens_i = int(est_tokens)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                tokens_i = 0
            if breakpoints and tokens_i > 0:
                bucket = _bucket_for_tokens(tokens_i, breakpoints)
                bucket_actuals.setdefault(bucket, []).append(actual_f)

    # ---------------------------------------------------------------
    # Build per-agent calibrations
    # ---------------------------------------------------------------
    agent_map = {a.id: a for a in config.agents}
    agent_calibrations: list[AgentCalibration] = []
    for aid, ratios in agent_ratios.items():
        n = len(ratios)
        old_sf = agent_map[aid].speed_factor
        # ``actual / expected`` > 1 means the agent ran SLOWER than the
        # plan predicted; lower the speed_factor proportionally so the
        # next solve allocates them less work per time unit.
        median_ratio = statistics.median(ratios)
        implied = old_sf if median_ratio <= 0 else old_sf / median_ratio
        new_sf = _ema_update(old_sf, implied, alpha)
        agent_calibrations.append(
            AgentCalibration(
                agent_id=aid,
                n_samples=n,
                old_speed_factor=old_sf,
                new_speed_factor=round(new_sf, 6),
                # Confidence label is informational here — runs-mode
                # uses the *pair count* gate, not the per-agent count.
                confidence=_confidence(n, max(1, min_pairs)),
            )
        )

    # ---------------------------------------------------------------
    # Build per-complexity token calibrations
    # ---------------------------------------------------------------
    token_calibrations: list[TokenCalibration] = []
    existing_te = config.token_estimates

    def _old_mean(comp: str) -> int:
        te = existing_te.get(comp)
        if te is None:
            return 0
        if isinstance(te, int):
            return te
        return te.mean  # TokenEstimate

    for comp, durations in bucket_actuals.items():
        n = len(durations)
        if n == 0:
            continue
        old_m = _old_mean(comp)
        # Median of actual durations for the bucket; EMA-smoothed
        # against the existing mean. Same shape as the speed_factor
        # update so config evolution stays predictable.
        implied = statistics.median(durations)
        new_m_float = (1 - alpha) * old_m + alpha * implied if old_m else implied
        new_m = max(1, int(round(new_m_float)))
        new_std = round(statistics.stdev(durations)) if n >= 2 else 0
        token_calibrations.append(
            TokenCalibration(
                complexity=comp,
                n_samples=n,
                old_mean=old_m,
                new_mean=new_m,
                new_std_dev=new_std,
                confidence=_confidence(n, max(1, min_pairs)),
            )
        )

    # ---------------------------------------------------------------
    # Apply updates to raw YAML and write back
    # ---------------------------------------------------------------
    raw_yaml = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    speed_updates = {
        ac.agent_id: ac.new_speed_factor for ac in agent_calibrations
    }
    for agent_dict in raw_yaml.get("agents", []):
        aid = agent_dict.get("id")
        if aid in speed_updates:
            agent_dict["speed_factor"] = speed_updates[aid]
    if token_calibrations:
        te_section = raw_yaml.setdefault("token_estimates", {})
        for tc in token_calibrations:
            te_section[tc.complexity] = {
                "mean": tc.new_mean,
                "std_dev": tc.new_std_dev,
            }

    written_to: Path | None = None
    if agent_calibrations or token_calibrations:
        if backup:
            backup_path = config_path.with_suffix(config_path.suffix + ".bak")
            backup_path.write_text(
                config_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        updated_yaml = yaml.dump(
            raw_yaml,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
        )
        # Atomic write: same pattern as ``calibrate``. Avoid leaving a
        # partial file on disk if the process is killed mid-write.
        tmp_fd, tmp_path_str = tempfile.mkstemp(
            dir=config_path.parent, prefix=".calibrate_runs_tmp_"
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(updated_yaml)
            os.replace(str(tmp_path), str(config_path))
        except Exception:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
            raise
        written_to = config_path

    report = CalibrationReport(
        runs_analysed=pair_count,
        runs_skipped=skipped,
        agents=agent_calibrations,
        token_estimates=token_calibrations,
        config_path=config_path,
        dry_run=False,
        written_to=written_to,
        warnings=[f"[{w.code}] {w.message}" for w in collector],
    )
    _print_report(report)
    return report


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def _pct(old: float, new: float) -> str:
    if old == 0:
        return "N/A"
    return f"{(new - old) / old * 100:+.1f}%"


def _print_report(report: CalibrationReport) -> None:
    lines: list[str] = []
    lines.append("# Calibration Report\n")
    lines.append(
        f"- **Runs analysed**: {report.runs_analysed}  "
        f"**Runs skipped**: {report.runs_skipped}"
    )
    lines.append(f"- **Config**: `{report.config_path}`")
    status = "dry-run (no changes written)" if report.dry_run else f"written to `{report.written_to}`"
    lines.append(f"- **Status**: {status}\n")

    lines.append("## Agent Speed Factors\n")
    lines.append(
        "| Agent | Samples | Old speed_factor | New speed_factor | Delta | Confidence |"
    )
    lines.append(
        "|-------|---------|-----------------|-----------------|-------|------------|"
    )
    for ac in report.agents:
        delta = _pct(ac.old_speed_factor, ac.new_speed_factor)
        lines.append(
            f"| {ac.agent_id} | {ac.n_samples} | {ac.old_speed_factor:.4f} "
            f"| {ac.new_speed_factor:.4f} | {delta} | {ac.confidence} |"
        )

    lines.append("\n## Token Estimates by Complexity\n")
    lines.append(
        "| Complexity | Samples | Old mean | New mean | New std_dev | Confidence |"
    )
    lines.append(
        "|-----------|---------|----------|----------|-------------|------------|"
    )
    for tc in report.token_estimates:
        lines.append(
            f"| {tc.complexity} | {tc.n_samples} | {tc.old_mean} "
            f"| {tc.new_mean} | {tc.new_std_dev} | {tc.confidence} |"
        )

    if report.warnings:
        lines.append("\n## Warnings\n")
        for w in report.warnings:
            lines.append(f"- {w}")

    print("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m solver.calibrate",
        description=(
            "Calibrate schedule-config.yml from real execution logs. "
            "Two modes: (1) --runs <jsonl> ingests a flat runs.jsonl; "
            "(2) --from-runs <dir> aggregates plan.json + actual.jsonl pairs "
            "from .specify/schedule/runs/."
        ),
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--runs",
        type=Path,
        metavar="PATH",
        help="Path to runs.jsonl (legacy log-ingestion mode).",
    )
    src.add_argument(
        "--from-runs",
        type=Path,
        metavar="DIR",
        help="Directory of <run_id>-plan.json + <run_id>-actual.jsonl pairs.",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        metavar="PATH",
        help="Path to schedule-config.yml to update.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="(legacy --runs mode only) Compute updates but do not write.",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=int,
        default=5,
        metavar="N",
        help="(legacy --runs mode only) Minimum samples for medium confidence.",
    )
    parser.add_argument(
        "--ema-alpha",
        type=float,
        default=0.3,
        metavar="ALPHA",
        help="(legacy --runs mode only) EMA blending factor in [0,1].",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=0.3,
        metavar="ALPHA",
        help="(--from-runs mode) EMA blending factor in [0,1] (default: 0.3).",
    )
    parser.add_argument(
        "--min-pairs",
        type=int,
        default=3,
        metavar="N",
        help="(--from-runs mode) Minimum plan/actual pairs required (default: 3).",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="(--from-runs mode) Write <config>.bak before mutation.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point — catches ScheduleInputError and exits with code 2."""
    args = _parse_args(argv)
    try:
        if args.from_runs is not None:
            calibrate_from_runs(
                runs_dir=args.from_runs,
                config_path=args.config,
                alpha=args.alpha,
                backup=args.backup,
                min_pairs=args.min_pairs,
            )
            return
        calibrate(
            runs_path=args.runs,
            config_path=args.config,
            dry_run=args.dry_run,
            confidence_threshold=args.confidence_threshold,
            ema_alpha=args.ema_alpha,
        )
    except ScheduleInputError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":  # pragma: no cover
    main()
