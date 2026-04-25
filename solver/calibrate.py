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
from typing import Literal

import yaml

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


def _load_runs(runs_path: Path, collector: WarningCollector) -> tuple[list[dict], int]:
    """Parse runs.jsonl and return (valid_rows, skipped_count)."""
    valid: list[dict] = []
    skipped = 0
    try:
        text = runs_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScheduleInputError(t("cannot_read_runs_file", error=exc)) from exc

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
        description="Calibrate schedule-config.yml from real execution logs (runs.jsonl).",
    )
    parser.add_argument(
        "--runs",
        required=True,
        type=Path,
        metavar="PATH",
        help="Path to runs.jsonl (one JSON object per line).",
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
        help="Compute updates but do not write to disk.",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=int,
        default=5,
        metavar="N",
        help="Minimum samples for medium confidence (default: 5). N*2 = high.",
    )
    parser.add_argument(
        "--ema-alpha",
        type=float,
        default=0.3,
        metavar="ALPHA",
        help="EMA blending factor in [0,1] (default: 0.3).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point — catches ScheduleInputError and exits with code 2."""
    args = _parse_args(argv)
    try:
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
