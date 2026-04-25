"""Benchmark runner: greedy baseline vs CP-SAT solver.

Usage
-----
    python -m benchmarks.run --all --time-limit 60
    python -m benchmarks.run --size tiny --time-limit 10
    python -m benchmarks.run --size frontend_heavy --time-limit 30
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "benchmarks"  # noqa: A001

from .greedy_baseline import greedy_solve
from .problems import REAL_WORLD_SHAPES, SIZES, generate

__all__ = ["main"]

_ALL_SIZES = list(SIZES) + list(REAL_WORLD_SHAPES)


def _run_cpsat(data: dict, time_limit: int) -> dict:
    """Run the CP-SAT solver with deterministic settings."""
    from solver.scheduler import solve_from_json

    cfg = dict(data.get("config", {}))
    cfg["num_workers"] = 1
    cfg["time_limit"] = time_limit
    patched = {**data, "config": cfg}
    return solve_from_json(patched)


def _metric(result: dict) -> dict:
    stats = result.get("stats", {})
    return {
        "makespan": stats.get("makespan", -1),
        "max_load": stats.get("max_load", -1),
        "min_load": stats.get("min_load", -1),
        "load_range": (
            stats.get("max_load", 0) - stats.get("min_load", 0)
            if stats.get("max_load") is not None and stats.get("min_load") is not None
            else -1
        ),
        "status": stats.get("status", result.get("status", "UNKNOWN")),
    }


def _gap_pct(greedy_ms: int, cpsat_ms: int) -> str:
    if greedy_ms <= 0:
        return "n/a"
    gap = (greedy_ms - cpsat_ms) / greedy_ms * 100
    return f"{gap:.1f}%"


def _run_problem(size: str, time_limit: int) -> dict:
    data = generate(size=size)
    meta = data.get("_meta", {})
    n = meta.get("n_tasks", len(data["tasks"]))
    m = meta.get("n_agents", len(data["agents"]))

    t0 = time.time()
    greedy_result = greedy_solve(data)
    greedy_time = time.time() - t0

    t1 = time.time()
    try:
        cpsat_result = _run_cpsat(data, time_limit)
        cpsat_time = time.time() - t1
    except Exception as exc:  # noqa: BLE001
        print(f"  CP-SAT failed for {size}: {exc}", file=sys.stderr)
        cpsat_result = {
            "status": "ERROR",
            "stats": {"makespan": -1, "max_load": -1, "min_load": -1, "status": "ERROR"},
        }
        cpsat_time = time.time() - t1

    gm = _metric(greedy_result)
    cm = _metric(cpsat_result)

    return {
        "size": size,
        "n_tasks": n,
        "n_agents": m,
        "greedy": {**gm, "solve_time": round(greedy_time, 3)},
        "cpsat": {**cm, "solve_time": round(cpsat_time, 3)},
        "gap_pct": _gap_pct(gm["makespan"], cm["makespan"]),
    }


def _render_table(runs: list[dict]) -> str:
    header = (
        "| Problem | n | m | Greedy makespan | CP-SAT makespan | Gap% "
        "| Greedy load_range | CP-SAT load_range | Solve time (s) |\n"
        "|---------|---|---|-----------------|-----------------|------"
        "|-------------------|-------------------|----------------|\n"
    )
    rows = []
    for r in runs:
        rows.append(
            f"| {r['size']} | {r['n_tasks']} | {r['n_agents']} "
            f"| {r['greedy']['makespan']} | {r['cpsat']['makespan']} "
            f"| {r['gap_pct']} "
            f"| {r['greedy']['load_range']} | {r['cpsat']['load_range']} "
            f"| {r['cpsat']['solve_time']} |"
        )
    return header + "\n".join(rows) + "\n"


def _write_outputs(runs: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"{stamp}.json"
    json_path.write_text(json.dumps(runs, indent=2), encoding="utf-8")

    md_content = "# Benchmark Results\n\n" + _render_table(runs)
    (output_dir / "latest.md").write_text(md_content, encoding="utf-8")
    print(f"Wrote {json_path}", file=sys.stderr)
    print(f"Wrote {output_dir / 'latest.md'}", file=sys.stderr)


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
    args = ap.parse_args(argv)

    output_dir = Path(args.output_dir)
    time_limit: int = args.time_limit

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

    runs: list[dict] = []
    for size in sizes_to_run:
        print(f"Running {size}...", file=sys.stderr)
        try:
            result = _run_problem(size, time_limit)
            runs.append(result)
            gms = result["greedy"]["makespan"]
            cms = result["cpsat"]["makespan"]
            gap = result["gap_pct"]
            print(
                f"  {size}: greedy={gms} cpsat={cms} gap={gap} "
                f"t={result['cpsat']['solve_time']}s",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR in {size}: {exc}", file=sys.stderr)

    if not runs:
        print("ERROR: no successful benchmark runs", file=sys.stderr)
        return 2

    _write_outputs(runs, output_dir)
    print(_render_table(runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
