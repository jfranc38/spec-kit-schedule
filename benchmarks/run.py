"""Benchmark runner: greedy baseline vs CP-SAT solver.

Usage
-----
    # Default: parallel CP-SAT (NUM_WORKERS = solver.defaults.NUM_WORKERS = 8)
    python -m benchmarks.run --all --time-limit 60
    python -m benchmarks.run --size tiny --time-limit 10

    # Force a specific worker count (1 = single-threaded determinism)
    python -m benchmarks.run --size tiny --time-limit 10 --num-workers 1

    # Worker scaling axis: run each problem at [1, 2, 4, 8] workers
    python -m benchmarks.run --size tiny --time-limit 10 --num-workers axis

    # Add the replan benchmark (solve, mark 50% completed, replan)
    python -m benchmarks.run --size tiny --time-limit 10 --include-replan

    # Track peak memory per solve (~5% overhead)
    python -m benchmarks.run --size tiny --time-limit 10 --memory

Output schema (``benchmarks/results/<timestamp>.json``)
-------------------------------------------------------
Top-level::

    {
      "schema_version": 2,
      "config": {
        "time_limit": int,
        "num_workers": int | "axis",
        "memory": bool,
        "include_replan": bool,
      },
      "runs": [run, ...],
      "scaling": [scaling_run, ...],   # only with --num-workers axis
      "replan": [replan_run, ...],     # only with --include-replan
    }

A ``run`` includes greedy + cpsat metrics with ``solve_time``,
``time_to_first_feasible``, ``peak_memory_mb`` (if --memory), the
``num_workers`` used, and ``phase1_time``/``phase2_time``/``phase3_time``
extracted from the solver stats.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "benchmarks"  # noqa: A001

from .greedy_baseline import greedy_solve
from .problems import REAL_WORLD_SHAPES, SIZES, generate

__all__ = ["main"]

_ALL_SIZES = list(SIZES) + list(REAL_WORLD_SHAPES)
_SCALING_AXIS_WORKERS: tuple[int, ...] = (1, 2, 4, 8)
_SCHEMA_VERSION = 2


# ---------------------------------------------------------------------------
# Defaults — keep imports lazy so `--help` works without a built solver venv.
# ---------------------------------------------------------------------------


def _default_num_workers() -> int:
    """Return ``solver.defaults.NUM_WORKERS`` (lazy import)."""
    from solver.defaults import NUM_WORKERS

    return int(NUM_WORKERS)


# ---------------------------------------------------------------------------
# Solve helpers
# ---------------------------------------------------------------------------


@dataclass
class _SolveOutcome:
    """A single CP-SAT invocation captured with the metrics the harness needs."""

    result: dict[str, Any]
    solve_time: float
    time_to_first_feasible: float | None
    peak_memory_mb: float | None
    error: str | None = None


def _run_cpsat(
    data: dict,
    *,
    time_limit: int,
    num_workers: int,
    track_memory: bool,
) -> _SolveOutcome:
    """Run the CP-SAT solver and capture wall-time, peak memory, and TTFF.

    ``num_workers=0`` is mapped to the solver default (`NUM_WORKERS`); any
    positive integer is honoured verbatim. ``time_to_first_feasible`` is
    extracted from the anytime intermediate list when available, otherwise
    falls back to total ``solve_time`` (documented in the README).
    """
    from solver.scheduler import solve_from_json

    cfg = dict(data.get("config", {}))
    effective_workers = num_workers if num_workers > 0 else _default_num_workers()
    cfg["num_workers"] = effective_workers
    cfg["time_limit"] = time_limit
    patched = {**data, "config": cfg}

    peak_mb: float | None = None
    if track_memory:
        tracemalloc.start()

    t0 = time.time()
    try:
        result = solve_from_json(patched)
        error: str | None = None
    except Exception as exc:  # noqa: BLE001
        result = {
            "status": "ERROR",
            "stats": {
                "makespan": -1,
                "max_load": -1,
                "min_load": -1,
                "status": "ERROR",
            },
        }
        error = f"{type(exc).__name__}: {exc}"
    solve_time = time.time() - t0

    if track_memory:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_mb = round(peak / (1024 * 1024), 3)

    intermediates = result.get("stats", {}).get("intermediate", []) or []
    if intermediates:
        first = intermediates[0]
        ttff = float(first.get("time", solve_time))
    else:
        # Fallback: anytime not enabled or no callback events emitted.
        # Solver returned a feasible/optimal result, so the first feasible
        # was at most the full solve time. Documented in module docstring.
        ttff = solve_time if error is None else None

    return _SolveOutcome(
        result=result,
        solve_time=solve_time,
        time_to_first_feasible=ttff,
        peak_memory_mb=peak_mb,
        error=error,
    )


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------


def _metric(result: dict) -> dict:
    stats = result.get("stats", {})
    max_l = stats.get("max_load")
    min_l = stats.get("min_load")
    load_range = (max_l - min_l) if (max_l is not None and min_l is not None) else -1
    out = {
        "makespan": stats.get("makespan", -1),
        "max_load": max_l if max_l is not None else -1,
        "min_load": min_l if min_l is not None else -1,
        "load_range": load_range,
        "status": stats.get("status", result.get("status", "UNKNOWN")),
    }
    # Pass through phase timings when present so callers can include them.
    for key in ("phase1_time", "phase2_time", "phase3_time"):
        if key in stats:
            out[key] = stats[key]
    return out


def _gap_pct(greedy_ms: int, cpsat_ms: int) -> str:
    if greedy_ms <= 0 or cpsat_ms < 0:
        return "n/a"
    gap = (greedy_ms - cpsat_ms) / greedy_ms * 100
    return f"{gap:.1f}%"


def _round_or_none(value: float | None, ndigits: int = 3) -> float | None:
    return round(value, ndigits) if value is not None else None


# ---------------------------------------------------------------------------
# Run a single benchmark problem
# ---------------------------------------------------------------------------


def _run_problem(
    size: str,
    *,
    time_limit: int,
    num_workers: int,
    track_memory: bool,
) -> dict:
    """Run greedy + CP-SAT on ``size`` and return the result row."""
    data = generate(size=size)
    meta = data.get("_meta", {})
    n = meta.get("n_tasks", len(data["tasks"]))
    m = meta.get("n_agents", len(data["agents"]))

    t0 = time.time()
    greedy_result = greedy_solve(data)
    greedy_time = time.time() - t0

    outcome = _run_cpsat(
        data,
        time_limit=time_limit,
        num_workers=num_workers,
        track_memory=track_memory,
    )
    if outcome.error:
        print(f"  CP-SAT failed for {size}: {outcome.error}", file=sys.stderr)

    gm = _metric(greedy_result)
    cm = _metric(outcome.result)

    cpsat_row: dict[str, Any] = {
        **cm,
        "solve_time": round(outcome.solve_time, 3),
        "time_to_first_feasible": _round_or_none(outcome.time_to_first_feasible),
        "num_workers": (
            num_workers if num_workers > 0 else _default_num_workers()
        ),
    }
    if track_memory:
        cpsat_row["peak_memory_mb"] = outcome.peak_memory_mb

    return {
        "size": size,
        "n_tasks": n,
        "n_agents": m,
        "greedy": {**gm, "solve_time": round(greedy_time, 3)},
        "cpsat": cpsat_row,
        "gap_pct": _gap_pct(gm["makespan"], cm["makespan"]),
    }


# ---------------------------------------------------------------------------
# Replan benchmark — solve full → mark 50% completed → replan
# ---------------------------------------------------------------------------


def _run_replan_problem(
    size: str,
    *,
    time_limit: int,
    num_workers: int,
    track_memory: bool,
) -> dict:
    """Solve, freeze the first half of assignments as completed, then replan."""
    from solver.replan import replan as solver_replan

    data = generate(size=size)

    # Step 1: full solve to get a baseline assignment.
    base = _run_cpsat(
        data,
        time_limit=time_limit,
        num_workers=num_workers,
        track_memory=False,
    )
    if base.error or base.result.get("status") in ("INFEASIBLE", "ERROR"):
        return {
            "size": size,
            "error": base.error or base.result.get("status", "UNKNOWN"),
            "stage": "full_solve",
        }
    full_makespan = base.result.get("stats", {}).get("makespan", -1)
    assignments = base.result.get("assignments", [])
    if not assignments:
        return {
            "size": size,
            "error": "no assignments returned from full solve",
            "stage": "full_solve",
        }

    # Step 2: pick the first 50% (by start time, then task_id) as completed.
    sorted_assignments = sorted(
        assignments, key=lambda a: (a.get("start", 0), a.get("task_id", ""))
    )
    half = max(1, len(sorted_assignments) // 2)
    completed_ids = {a["task_id"] for a in sorted_assignments[:half]}

    # Step 3: replan with the remainder.
    replan_input = copy.deepcopy(data)
    cfg = dict(replan_input.get("config", {}))
    cfg["time_limit"] = time_limit
    cfg["num_workers"] = num_workers if num_workers > 0 else _default_num_workers()
    replan_input["config"] = cfg

    peak_mb: float | None = None
    if track_memory:
        tracemalloc.start()
    t0 = time.time()
    try:
        replan_result = solver_replan(
            base.result,
            replan_input,
            completed_ids=completed_ids,
        )
        replan_error: str | None = None
    except Exception as exc:  # noqa: BLE001
        replan_result = {"status": "ERROR", "stats": {"makespan": -1, "status": "ERROR"}}
        replan_error = f"{type(exc).__name__}: {exc}"
    replan_time = time.time() - t0
    if track_memory:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_mb = round(peak / (1024 * 1024), 3)

    # Step 4: from-scratch solve on the residual problem (same removal as
    # replan) so the gap is apples-to-apples.
    from solver.replan import _remove_completed  # private helper, intentional.

    residual_input, _removed = _remove_completed(replan_input, completed_ids)
    scratch = _run_cpsat(
        residual_input,
        time_limit=time_limit,
        num_workers=num_workers,
        track_memory=False,
    )

    replan_makespan = replan_result.get("stats", {}).get("makespan", -1)
    scratch_makespan = scratch.result.get("stats", {}).get("makespan", -1)
    if scratch_makespan > 0 and replan_makespan >= 0:
        # Gap relative to from-scratch lower-effort reference (positive ⇒
        # replan is worse, negative ⇒ replan beat the baseline).
        gap = (replan_makespan - scratch_makespan) / scratch_makespan * 100
        quality_gap_pct = f"{gap:+.1f}%"
    else:
        quality_gap_pct = "n/a"

    row: dict[str, Any] = {
        "size": size,
        "n_tasks": len(data.get("tasks", [])),
        "n_completed": len(completed_ids),
        "full_makespan": full_makespan,
        "replan_makespan": replan_makespan,
        "scratch_makespan": scratch_makespan,
        "replan_time": round(replan_time, 3),
        "scratch_time": round(scratch.solve_time, 3),
        "quality_gap_pct": quality_gap_pct,
        "error": replan_error,
    }
    if track_memory:
        row["replan_peak_memory_mb"] = peak_mb
    return row


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_table(runs: list[dict], *, track_memory: bool) -> str:
    """Render the primary greedy-vs-CP-SAT table.

    Includes ``num_workers``, ``time-to-first-feasible``, optional
    ``peak_memory``, and per-phase timings (when present).
    """
    cols = [
        "Problem", "n", "m",
        "Greedy MS", "CP-SAT MS", "Gap%",
        "Greedy load_range", "CP-SAT load_range",
        "Solve t (s)", "TTFF (s)",
        "P1 t", "P2 t", "P3 t",
        "workers",
    ]
    if track_memory:
        cols.append("Peak MB")
    sep = "|" + "|".join(["-" * max(3, len(c)) for c in cols]) + "|"
    header = "| " + " | ".join(cols) + " |\n" + sep + "\n"

    rows: list[str] = []
    for r in runs:
        cp = r["cpsat"]
        gp = r["greedy"]
        ttff = cp.get("time_to_first_feasible")
        ttff_s = f"{ttff:.3f}" if isinstance(ttff, (int, float)) else "n/a"
        cells = [
            r["size"],
            str(r["n_tasks"]),
            str(r["n_agents"]),
            str(gp["makespan"]),
            str(cp["makespan"]),
            r["gap_pct"],
            str(gp["load_range"]),
            str(cp["load_range"]),
            f"{cp['solve_time']:.3f}",
            ttff_s,
            str(cp.get("phase1_time", "?")),
            str(cp.get("phase2_time", "?")),
            str(cp.get("phase3_time", "?")),
            str(cp.get("num_workers", "?")),
        ]
        if track_memory:
            cells.append(str(cp.get("peak_memory_mb", "?")))
        rows.append("| " + " | ".join(cells) + " |")
    return header + "\n".join(rows) + "\n"


def _render_scaling_table(scaling: list[dict]) -> str:
    """One row per (size, num_workers) with wall-time and makespan."""
    header = (
        "| Problem | n | m | workers | Solve t (s) | TTFF (s) | "
        "Makespan | Status |\n"
        "|---------|---|---|---------|-------------|----------|"
        "----------|--------|\n"
    )
    rows: list[str] = []
    for r in scaling:
        cp = r["cpsat"]
        ttff = cp.get("time_to_first_feasible")
        ttff_s = f"{ttff:.3f}" if isinstance(ttff, (int, float)) else "n/a"
        rows.append(
            f"| {r['size']} | {r['n_tasks']} | {r['n_agents']} "
            f"| {cp.get('num_workers', '?')} "
            f"| {cp['solve_time']:.3f} | {ttff_s} "
            f"| {cp['makespan']} | {cp['status']} |"
        )
    return header + "\n".join(rows) + "\n"


def _render_replan_table(replan_runs: list[dict]) -> str:
    header = (
        "| Problem | n | completed | Full MS | Replan MS | Scratch MS "
        "| Quality gap | Replan t (s) | Scratch t (s) |\n"
        "|---------|---|-----------|---------|-----------|------------"
        "|-------------|--------------|---------------|\n"
    )
    rows: list[str] = []
    for r in replan_runs:
        if r.get("error"):
            rows.append(
                f"| {r['size']} | n/a | n/a | n/a | n/a | n/a | "
                f"ERROR ({r.get('stage', '?')}: {r['error']}) | n/a | n/a |"
            )
            continue
        rows.append(
            f"| {r['size']} | {r['n_tasks']} | {r['n_completed']} "
            f"| {r['full_makespan']} | {r['replan_makespan']} "
            f"| {r['scratch_makespan']} | {r['quality_gap_pct']} "
            f"| {r['replan_time']} | {r['scratch_time']} |"
        )
    return header + "\n".join(rows) + "\n"


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _write_outputs(
    runs: list[dict],
    *,
    output_dir: Path,
    track_memory: bool,
    scaling: list[dict] | None,
    replan_runs: list[dict] | None,
    cli_config: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    payload: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "config": cli_config,
        "runs": runs,
    }
    if scaling is not None:
        payload["scaling"] = scaling
    if replan_runs is not None:
        payload["replan"] = replan_runs

    json_path = output_dir / f"{stamp}.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    md_parts = ["# Benchmark Results\n"]
    md_parts.append(
        f"_time_limit={cli_config['time_limit']}s, "
        f"num_workers={cli_config['num_workers']}, "
        f"memory={cli_config['memory']}, "
        f"include_replan={cli_config['include_replan']}_\n"
    )
    if runs:
        md_parts.append("## Greedy vs CP-SAT\n")
        md_parts.append(_render_table(runs, track_memory=track_memory))
    if scaling:
        md_parts.append("\n## Worker scaling\n")
        md_parts.append(_render_scaling_table(scaling))
    if replan_runs:
        md_parts.append("\n## Replan benchmark\n")
        md_parts.append(_render_replan_table(replan_runs))
    (output_dir / "latest.md").write_text("\n".join(md_parts), encoding="utf-8")

    print(f"Wrote {json_path}", file=sys.stderr)
    print(f"Wrote {output_dir / 'latest.md'}", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_workers_arg(raw: str | None) -> int | str:
    """Accept ``axis``, ``0`` (default), or any positive int.

    Returns the integer worker count, ``0`` for "use solver default", or the
    sentinel string ``"axis"`` for scaling experiments.
    """
    if raw is None:
        return 0  # caller will translate to NUM_WORKERS lazily
    if raw == "axis":
        return "axis"
    try:
        n = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--num-workers must be 'axis', 0 (default), or a positive int (got {raw!r})"
        ) from exc
    if n < 0:
        raise argparse.ArgumentTypeError("--num-workers must be >= 0")
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="benchmarks.run",
        description="Run greedy + CP-SAT benchmarks on synthetic problems.",
    )
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="Run all problem sizes/shapes")
    group.add_argument("--size", metavar="SIZE", help="Run a single problem size/shape")
    ap.add_argument(
        "--time-limit", type=int, default=60, metavar="SECONDS",
        help="CP-SAT time limit per problem (default: 60)",
    )
    ap.add_argument(
        "--output-dir", default="benchmarks/results", metavar="DIR",
        help="Directory for result files (default: benchmarks/results)",
    )
    ap.add_argument(
        "--num-workers", type=_parse_workers_arg, default=0, metavar="N",
        help=(
            "CP-SAT worker count. Default 0 = solver.defaults.NUM_WORKERS. "
            "Pass an integer to pin a specific value, or 'axis' to sweep "
            f"{list(_SCALING_AXIS_WORKERS)}."
        ),
    )
    ap.add_argument(
        "--include-replan", action="store_true",
        help="Add a replan benchmark: solve full → mark 50%% completed → replan.",
    )
    ap.add_argument(
        "--memory", action="store_true",
        help="Track peak memory per CP-SAT solve via tracemalloc (~5%% overhead).",
    )
    args = ap.parse_args(argv)

    output_dir = Path(args.output_dir)
    time_limit: int = args.time_limit
    track_memory: bool = args.memory
    num_workers_arg = args.num_workers  # int or "axis"

    sizes_to_run: list[str]
    if args.all:
        sizes_to_run = _ALL_SIZES
    else:
        if args.size not in _ALL_SIZES:
            print(
                f"ERROR: unknown size {args.size!r}. Valid: {_ALL_SIZES}",
                file=sys.stderr,
            )
            return 2
        sizes_to_run = [args.size]

    cli_config: dict[str, Any] = {
        "time_limit": time_limit,
        "num_workers": (
            num_workers_arg if isinstance(num_workers_arg, str)
            else (num_workers_arg if num_workers_arg > 0 else _default_num_workers())
        ),
        "memory": track_memory,
        "include_replan": bool(args.include_replan),
    }

    # Worker-scaling axis: run each size at every value in _SCALING_AXIS_WORKERS.
    if num_workers_arg == "axis":
        runs: list[dict] = []
        scaling_runs: list[dict] = []
        # First pass at default workers feeds the primary table; the axis
        # sweep populates the scaling table.
        default_workers = _default_num_workers()
        for size in sizes_to_run:
            print(f"Running {size} (workers={default_workers})...", file=sys.stderr)
            try:
                row = _run_problem(
                    size,
                    time_limit=time_limit,
                    num_workers=default_workers,
                    track_memory=track_memory,
                )
                runs.append(row)
            except Exception as exc:  # noqa: BLE001
                print(f"  ERROR in {size}: {exc}", file=sys.stderr)
                continue
            for w in _SCALING_AXIS_WORKERS:
                print(f"  scaling: {size} @ workers={w}...", file=sys.stderr)
                try:
                    sr = _run_problem(
                        size,
                        time_limit=time_limit,
                        num_workers=w,
                        track_memory=False,
                    )
                    scaling_runs.append(sr)
                except Exception as exc:  # noqa: BLE001
                    print(f"    ERROR in {size}@{w}: {exc}", file=sys.stderr)
        replan_runs: list[dict] | None = None
        if args.include_replan:
            replan_runs = []
            for size in sizes_to_run:
                print(f"  replan: {size}...", file=sys.stderr)
                try:
                    rr = _run_replan_problem(
                        size,
                        time_limit=time_limit,
                        num_workers=default_workers,
                        track_memory=track_memory,
                    )
                    replan_runs.append(rr)
                except Exception as exc:  # noqa: BLE001
                    print(f"    ERROR replan {size}: {exc}", file=sys.stderr)
        if not runs:
            print("ERROR: no successful benchmark runs", file=sys.stderr)
            return 2
        _write_outputs(
            runs,
            output_dir=output_dir,
            track_memory=track_memory,
            scaling=scaling_runs,
            replan_runs=replan_runs,
            cli_config=cli_config,
        )
        print(_render_table(runs, track_memory=track_memory))
        if scaling_runs:
            print(_render_scaling_table(scaling_runs))
        if replan_runs:
            print(_render_replan_table(replan_runs))
        return 0

    # Single-fixed-workers run (the common path).
    workers_int: int = int(num_workers_arg)  # 0 = default
    runs = []
    for size in sizes_to_run:
        print(f"Running {size}...", file=sys.stderr)
        try:
            result = _run_problem(
                size,
                time_limit=time_limit,
                num_workers=workers_int,
                track_memory=track_memory,
            )
            runs.append(result)
            gms = result["greedy"]["makespan"]
            cms = result["cpsat"]["makespan"]
            gap = result["gap_pct"]
            print(
                f"  {size}: greedy={gms} cpsat={cms} gap={gap} "
                f"t={result['cpsat']['solve_time']}s "
                f"workers={result['cpsat']['num_workers']}",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR in {size}: {exc}", file=sys.stderr)

    replan_runs2: list[dict] | None = None
    if args.include_replan:
        replan_runs2 = []
        for size in sizes_to_run:
            print(f"  replan: {size}...", file=sys.stderr)
            try:
                rr = _run_replan_problem(
                    size,
                    time_limit=time_limit,
                    num_workers=workers_int,
                    track_memory=track_memory,
                )
                replan_runs2.append(rr)
            except Exception as exc:  # noqa: BLE001
                print(f"    ERROR replan {size}: {exc}", file=sys.stderr)

    if not runs:
        print("ERROR: no successful benchmark runs", file=sys.stderr)
        return 2

    _write_outputs(
        runs,
        output_dir=output_dir,
        track_memory=track_memory,
        scaling=None,
        replan_runs=replan_runs2,
        cli_config=cli_config,
    )
    print(_render_table(runs, track_memory=track_memory))
    if replan_runs2:
        print(_render_replan_table(replan_runs2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
