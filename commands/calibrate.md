---
description: "Recalibrate the portfolio's speed_factor and token_estimates from accumulated execution logs in .specify/schedule/runs/."
---

# /speckit.schedule.calibrate — Calibrate Portfolio from Execution Data

## Purpose

Close the planning feedback loop. Every `/speckit.schedule.run`
silently captures its plan to `.specify/schedule/runs/<run_id>-plan.json`.
Once the user records the observed durations to the matching
`<run_id>-actual.jsonl`, this command aggregates the accumulated
plan/actual pairs and updates the portfolio's `speed_factor`
(per agent) and `token_estimates` (per complexity tier) so future
solves plan against real data instead of the static defaults.

The aggregation uses a median + EMA update so individual outlier runs
do not move the portfolio aggressively. See
[`docs/calibration.md`](../docs/calibration.md) for the full algorithm.

## Workflow

### Step 1 — Verify accumulated runs

Read `.specify/schedule/runs/`. List paired plan/actual files:

```bash
EXT_DIR=".specify/extensions/schedule"
RUNS_DIR=".specify/schedule/runs"
"$EXT_DIR/.venv/bin/python" -c "
from pathlib import Path
runs = Path('$RUNS_DIR')
plans = sorted(runs.glob('*-plan.json')) if runs.exists() else []
actuals = sorted(runs.glob('*-actual.jsonl')) if runs.exists() else []
paired = [p for p in plans if (runs / (p.stem.removesuffix('-plan') + '-actual.jsonl')).is_file()]
print(f'plans: {len(plans)}, actuals: {len(actuals)}, paired: {len(paired)}')
"
```

If fewer than 3 paired runs exist, surface the count and stop:
"Need at least 3 plan/actual pairs before calibration is meaningful.
Currently you have N. Run a few more solves and record actuals."

### Step 2 — Run calibration

```bash
"$EXT_DIR/.venv/bin/python" -m solver.calibrate \
  --from-runs "$RUNS_DIR" \
  --config .specify/schedule/schedule-config.yml \
  --alpha 0.3 \
  --backup
```

`--backup` writes `schedule-config.yml.bak` before mutating, so the
user can revert with a single `mv`.

### Step 3 — Show diff

Show the user the diff of `speed_factor` + `token_estimates` changes
vs the prior config:

```bash
diff -u .specify/schedule/schedule-config.yml.bak .specify/schedule/schedule-config.yml || true
```

Then ask whether to keep or revert:

```bash
# Revert if needed:
mv .specify/schedule/schedule-config.yml.bak .specify/schedule/schedule-config.yml
```

If the agent ran the command without `--backup`, point out that
re-running with `--backup` is the recommended path.

## Recording actuals after /speckit.implement

The plan capture is automatic — every `/speckit.schedule.run` writes
its `*-plan.json`. Recording actuals is currently manual (a future
release will integrate with `/speckit.implement`). For now use the
helper:

```bash
"$EXT_DIR/.venv/bin/python" -m solver.run_log append-actual \
  --run-id <id> \
  --task T001 \
  --agent opus \
  --duration 92
```

`<id>` is the filename stem of the plan you want to attribute the
actual to (e.g. `2026-05-07T12:34:56Z-abc123`). You can list available
ids with:

```bash
ls .specify/schedule/runs/*-plan.json | xargs -n1 basename | sed 's/-plan.json//'
```

Or edit `.specify/schedule/runs/<run_id>-actual.jsonl` directly — it is
a plain JSONL file (one JSON object per line) so a hand-rolled append
works just as well.

## Usage

```
/speckit.schedule.calibrate
```

The default behaviour reads `.specify/schedule/runs/`, applies an
EMA-smoothed update with `alpha=0.3`, and writes a `.bak` of the
prior config alongside the in-place rewrite. Override paths or
parameters with the CLI flags above.

## Errors and edge cases

- **`runs/` missing or empty** — surface the directory location and
  ask the user to run more solves before calibrating.
- **Fewer than 3 paired runs** — `calibrate_from_runs` exits
  gracefully with a `CALIBRATE_INSUFFICIENT_PAIRS` warning and does
  not modify the portfolio.
- **Agent ids drifted** — runs whose `agent_id` is no longer in the
  current portfolio are skipped with a `CALIBRATE_UNKNOWN_AGENT`
  warning. The remaining agents calibrate normally.
- **Actuals reference unknown tasks** — skipped with
  `CALIBRATE_RUN_TASK_MISSING`.
