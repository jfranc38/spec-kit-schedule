# Benchmark Harness

Compares the CP-SAT solver against a greedy baseline on synthetic and
real-world-shaped scheduling problems.

## Quick start

```bash
# Run all benchmarks (writes benchmarks/results/latest.md)
make bench

# Run a single size
uv run --extra viz -- python -m benchmarks.run --size tiny --time-limit 10

# Print the latest results table
make bench-report
```

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
reference for the Gap% metric.

## Output files

- `results/YYYYMMDD_HHMMSS.json` — full run data for all problems.
- `results/latest.md` — markdown table (overwritten on each run).

## Reproducibility

- `random.seed(seed)` with fixed seeds per shape.
- CP-SAT runs with `num_workers=1` for determinism.
- Wall-time is reported but not asserted.
