# Benchmark Harness

Compares the CP-SAT solver against a greedy baseline on synthetic and
real-world-shaped scheduling problems.

## Quick start

```bash
# Run all benchmarks (writes benchmarks/results/latest.md)
make bench

# Run a single size at the default worker count (solver.defaults.NUM_WORKERS)
uv run --extra viz -- python -m benchmarks.run --size tiny --time-limit 10

# Print the latest results table
make bench-report
```

## CLI flags

| Flag                | Default                       | Notes                                                                 |
|---------------------|-------------------------------|-----------------------------------------------------------------------|
| `--all`             | —                             | Run every size + real-world shape. Mutually exclusive with `--size`.  |
| `--size SIZE`       | —                             | Run a single named shape (e.g. `tiny`, `frontend_heavy`).             |
| `--time-limit N`    | `60`                          | Per-problem CP-SAT time limit in seconds.                             |
| `--output-dir DIR`  | `benchmarks/results`          | Where the timestamped JSON + `latest.md` are written.                 |
| `--num-workers N`   | `0` (= `solver.defaults.NUM_WORKERS`) | Pass an integer to pin workers, `0` for the solver default, or `axis` to sweep `[1, 2, 4, 8]`. |
| `--include-replan`  | `false`                       | Add a replan section: solve full → mark 50 % completed → replan, with a from-scratch baseline. |
| `--memory`          | `false`                       | Track peak memory per CP-SAT solve via `tracemalloc` (~5 % overhead). |

`make bench` (no args) is unchanged: `--all --time-limit 60` at the solver
default worker count.

## Problem sizes

| size     | n_tasks | n_agents | density (edges/n) | skill_complexity |
|----------|---------|----------|--------------------|-----------------|
| tiny     | 10      | 2        | 0.8                | 2 skills        |
| small    | 25      | 3        | 1.2                | 3 skills        |
| medium   | 75      | 5        | 1.5                | 4 skills        |
| large    | 200     | 8        | 2.0                | 5 skills        |
| xl       | 400     | 10       | 2.5                | 6 skills        |

## Real-world shapes

| shape           | n_tasks | n_agents | notes                                          |
|-----------------|---------|----------|------------------------------------------------|
| frontend_heavy  | 40      | 4        | 50% frontend tasks, low mutex density          |
| backend_heavy   | 40      | 4        | 60% backend tasks, higher mutex density        |
| balanced_tdd    | 36      | 4        | Equal skill spread, medium mutex density       |
| migration       | 50      | 5        | Many cross-skill dependencies, high mutex      |
| greenfield      | 30      | 3        | Low density, sparse dependencies               |

Each shape has a fixed seed for reproducibility.

## Greedy baseline

Pure-Python MAQA-style scheduler (no CP-SAT). For each task in topological
order, picks the eligible agent with the earliest available time, respecting
κ (task cardinality) and context-budget constraints. Used as a lower-bound
reference for the Gap% metric. The Kahn’s topological sort uses
`collections.deque.popleft()` (O(1)) so the routine stays linear on the
larger shapes.

## Output schema

Each run writes a timestamped JSON envelope to `results/YYYYMMDD_HHMMSS.json`
plus an overwriting `results/latest.md`:

```json
{
  "schema_version": 2,
  "config": {
    "time_limit": 60,
    "num_workers": 8,
    "memory": false,
    "include_replan": false
  },
  "runs": [
    {
      "size": "tiny",
      "n_tasks": 10,
      "n_agents": 2,
      "greedy": {
        "makespan": 230,
        "max_load": 230,
        "min_load": 0,
        "load_range": 230,
        "status": "GREEDY_FEASIBLE",
        "solve_time": 0.001
      },
      "cpsat": {
        "makespan": 230,
        "max_load": 230,
        "min_load": 0,
        "load_range": 230,
        "status": "OPTIMAL",
        "solve_time": 0.123,
        "time_to_first_feasible": 0.045,
        "phase1_time": 0.05,
        "phase2_time": 0.07,
        "num_workers": 8,
        "peak_memory_mb": 12.345
      },
      "gap_pct": "0.0%"
    }
  ],
  "scaling": [],
  "replan": []
}
```

Notes:

- **`time_to_first_feasible`**: when CP-SAT runs in anytime mode, this is
  the wall-time of the first incumbent recorded by the orchestration
  callback. Otherwise the harness falls back to the full `solve_time`
  (the first feasible was at most that fast).
- **`peak_memory_mb`**: only present with `--memory`. Captured around the
  CP-SAT call via `tracemalloc.get_traced_memory()`; reported in MiB.
- **`phase1_time` / `phase2_time` / `phase3_time`**: lifted from the solver’s
  `stats` dict. `phase3_time` only appears for the cost-aware objective.
- **`scaling`**: only populated when `--num-workers axis` is set. Each entry
  is the same shape as a `runs` entry but at a single fixed worker count.
- **`replan`**: only populated when `--include-replan` is set. Compares
  replan wall-time and makespan to a from-scratch solve on the residual
  problem (post-removal of completed tasks). `quality_gap_pct` is
  `(replan_makespan - scratch_makespan) / scratch_makespan`; positive means
  replan is worse, negative means it beat the from-scratch reference.

## Reproducibility

- `random.seed(seed)` with fixed seeds per shape.
- Default worker count is the solver default (8) — pass `--num-workers 1`
  for single-threaded determinism, or `axis` to sweep `[1, 2, 4, 8]`.
- Wall-time is reported but not asserted.
