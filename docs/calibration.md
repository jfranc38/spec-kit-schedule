# Calibration Guide

`solver.calibrate` closes the plan→execute→improve loop by ingesting real execution logs
and updating your `schedule-config.yml` so the second solve is measurably better than
the first.

There are **two ways** to feed calibration data:

1. **Runs-directory feedback loop** (v0.6.x Build 2, recommended) — every
   `/speckit.schedule.run` writes a plan snapshot under
   `.specify/schedule/runs/<run_id>-plan.json`. You record the observed
   durations to the matching `<run_id>-actual.jsonl`, and
   `/speckit.schedule.calibrate` aggregates the pairs in place. No
   manual JSONL schema, no separate orchestrator integration.
2. **Flat `runs.jsonl` ingestion** (legacy) — emit a single combined
   JSONL file from a custom orchestrator and feed it via
   `python -m solver.calibrate --runs runs.jsonl --config …`. Same
   algorithm, different data source.

The two modes are mutually exclusive on a single CLI invocation but
can be used interchangeably across runs.

---

## Quickstart — runs-directory mode (recommended)

Every `/speckit.schedule.run` automatically writes its plan to
`.specify/schedule/runs/<run_id>-plan.json` (see
[Plan capture](#plan-capture-runs-mode) below). After executing a
batch of runs and recording the observed durations to the matching
`<run_id>-actual.jsonl` files:

```bash
# Aggregate every plan/actual pair under .specify/schedule/runs/
# and rewrite the portfolio in place. --backup keeps a .bak copy.
python -m solver.calibrate \
  --from-runs .specify/schedule/runs \
  --config .specify/schedule/schedule-config.yml \
  --alpha 0.3 \
  --backup
```

Or, equivalently, run the slash command:

```
/speckit.schedule.calibrate
```

If fewer than 3 paired runs exist the command exits with a warning
and does not modify the portfolio.

## Quickstart — flat runs.jsonl (legacy)

```bash
# 1. Run a schedule and collect execution data into runs.jsonl (see "Producing runs.jsonl" below).

# 2. Preview what would change without touching the config:
python -m solver.calibrate \
  --runs runs.jsonl \
  --config schedule-config.yml \
  --dry-run

# 3. Apply the updates in-place (atomic write):
python -m solver.calibrate \
  --runs runs.jsonl \
  --config schedule-config.yml
```

---

## Plan capture (runs mode)

Every successful solve writes a small JSON snapshot to
`.specify/schedule/runs/<run_id>-plan.json` via
`solver.run_log.record_plan`. The capture is **best-effort**:

- A failed write is logged at `WARNING` level but does not raise.
- Solves outside a `.specify/`-rooted project are no-ops (the helper
  detects the missing marker and skips).

The on-disk shape is intentionally a subset of the full result envelope:

```json
{
  "schema_version": "1.0",
  "run_id": "2026-05-07T12:34:56Z-abc123",
  "created_at": "2026-05-07T12:34:56Z",
  "config_path": ".specify/schedule/schedule-config.yml",
  "tasks_md_path": "tasks.md",
  "objective": "lexicographic",
  "status": "OPTIMAL",
  "makespan": 218,
  "max_load": 106,
  "total_cost": null,
  "assignments": [
    {"task_id": "T001", "agent_id": "opus",
     "expected_duration": 80, "expected_start": 0, "expected_end": 80}
  ]
}
```

`run_id` is `<ISO8601 timestamp>-<short-uuid>` so concurrent solves
in the same second still produce unique files.

### Recording actuals

Each line of `<run_id>-actual.jsonl` is a JSON object:

```json
{"task_id": "T001", "agent_id": "opus", "actual_duration": 92,
 "completed_at": "2026-05-07T13:15:32Z", "notes": null}
```

Append via the helper CLI:

```bash
python -m solver.run_log append-actual \
  --run-id 2026-05-07T12:34:56Z-abc123 \
  --task T001 --agent opus --duration 92
```

…or write the line by hand. JSONL is append-safe, so any append-only
writer (including `>> file`) works.

### Aggregation algorithm (`--from-runs`)

For every paired `(plan, actual)` file in the runs directory:

1. Index the plan's assignments by `task_id`.
2. For each actual line, join on `task_id` and compute
   `actual_duration / expected_duration` (the slowdown ratio).
3. Per agent: aggregate ratios via `median`, then derive the
   implied speed_factor as `old_speed_factor / median_ratio`
   (slower → smaller factor).
4. Per complexity bucket (inferred from the expected duration vs the
   existing `token_estimates` means): aggregate observed durations
   via `median` and use that as the implied bucket mean.
5. Apply EMA smoothing against the existing values:
   `new = (1 - alpha) * old + alpha * implied`.

The function exits gracefully (no portfolio mutation) when:

- The runs directory does not exist;
- Fewer than `--min-pairs` paired runs are present (default 3);
- No pair contains a usable matching task.

Runs whose `agent_id` is no longer in the current portfolio are
skipped with a `CALIBRATE_UNKNOWN_AGENT` warning. The remaining
agents calibrate normally.

---

## Producing runs.jsonl

Each line is a JSON object with the following keys:

| Key | Type | Description |
|-----|------|-------------|
| `task_id` | string | Unique task identifier (matches `tasks.md`) |
| `agent_id` | string | Agent that executed the task (matches `schedule-config.yml`) |
| `model` | string | Model name used |
| `skill` | string | Skill tag assigned to the task |
| `complexity` | string | Complexity bucket: `simple`, `medium`, `complex`, or `review` |
| `estimated_tokens` | int | Token estimate used during scheduling |
| `predicted_duration` | float | Duration the solver predicted (in minutes) |
| `actual_duration` | float | Observed wall-clock duration (in minutes) |
| `actual_tokens` | int | Tokens actually consumed |
| `start_ts` | string | ISO 8601 start timestamp |
| `end_ts` | string | ISO 8601 end timestamp |
| `status` | string | `"success"` or `"failed"` / `"timeout"` |

Example row:

```json
{"task_id": "T007", "agent_id": "backend-1", "model": "claude-sonnet-4",
 "skill": "api", "complexity": "medium", "estimated_tokens": 3500,
 "predicted_duration": 35, "actual_duration": 41, "actual_tokens": 3820,
 "start_ts": "2026-04-24T10:00:00Z", "end_ts": "2026-04-24T10:41:00Z",
 "status": "success"}
```

Only `status == "success"` rows are used for calibration. Rows with missing required
keys are skipped with a warning in the report.

### Collection strategies

- **Wave executor integration**: `solver.wave_executor` can be configured to append a
  row to `runs.jsonl` after each task completes.
- **Custom orchestrator**: Append a JSON line to `runs.jsonl` after each task finishes.
  Any append-safe file writer works since lines are parsed independently.
- **Manual logging**: For small experiments, write rows by hand or with a short script.

---

## How calibration works

### Agent speed factors

For each `agent_id` present in both the runs file and the config:

1. Collect all successful runs for that agent.
2. Compute `ratio = predicted_duration / actual_duration` for each run.
3. `raw_new = median(ratios) × old_speed_factor`
4. Apply EMA smoothing: `new_speed_factor = old + α × (raw_new − old)`

A `ratio > 1` means the agent ran faster than predicted (the schedule was conservative).
A `ratio < 1` means the agent was slower (the schedule was optimistic).

### Token estimates

For each `complexity` bucket present in the runs:

1. Collect `actual_tokens` from all successful runs in that bucket.
2. `new_mean = round(mean(actual_tokens))`
3. `new_std_dev = round(stdev(actual_tokens))` — `0` when fewer than 2 samples.

Updated token estimates are written back as `{mean, std_dev}` dicts, enabling
stochastic scheduling when `solver.stochastic_quantile != 0.5`.

### Confidence levels

| Confidence | Samples required | Action |
|-----------|-----------------|--------|
| `low` | `< threshold` | Old value kept; warning emitted |
| `medium` | `≥ threshold` | Value updated |
| `high` | `≥ threshold × 2` | Value updated |

Default `--confidence-threshold` is 5. Low-confidence values are **never** mutated
— they appear in the report for manual review.

### EMA smoothing

The `--ema-alpha` parameter (default `0.3`) controls how aggressively the new
measurement replaces the old estimate:

- `alpha = 0.0` → old value never changes (no-op).
- `alpha = 1.0` → new value fully replaces old (no memory).
- `alpha = 0.3` (default) → gradual convergence; safe for noisy measurements.

Lower alpha is appropriate when runs are highly variable. Higher alpha converges
faster when you have stable, representative data.

---

## Interpreting the report

The calibration report (printed to stdout as Markdown) contains three sections:

### Summary header

```
- **Runs analysed**: 40  **Runs skipped**: 2
- **Config**: `schedule-config.yml`
- **Status**: written to `schedule-config.yml`
```

- **Runs analysed**: successful rows used for calculations.
- **Runs skipped**: non-success, missing keys, or parse errors.

### Agent Speed Factors table

| Agent | Samples | Old speed_factor | New speed_factor | Delta | Confidence |
|-------|---------|-----------------|-----------------|-------|------------|
| backend | 12 | 1.0000 | 1.0750 | +7.5% | high |
| tester | 3 | 1.5000 | 1.5000 | +0.0% | low |

A positive Delta means the agent ran faster than predicted — the new schedule will
assign it more work per time unit. A negative Delta means it was slower.

`low` confidence rows have Delta = 0.0% (old value preserved).

### Token Estimates table

| Complexity | Samples | Old mean | New mean | New std_dev | Confidence |
|-----------|---------|----------|----------|-------------|------------|
| medium | 15 | 3500 | 3820 | 410 | high |

A higher `new_mean` means tasks are consuming more tokens than estimated —
consider raising the estimate or splitting tasks. A non-zero `new_std_dev` enables
stochastic scheduling (`solver.stochastic_quantile`).

### Warnings list

Low-confidence agents and skipped rows are listed here with diagnostic codes:

- `CALIBRATE_LOW_CONFIDENCE_AGENT` — agent has too few samples.
- `CALIBRATE_LOW_CONFIDENCE_TOKENS` — complexity bucket has too few samples.
- `CALIBRATE_UNKNOWN_AGENT` — agent_id in runs.jsonl not found in config.
- `CALIBRATE_MISSING_KEYS` — row skipped due to missing required keys.
- `CALIBRATE_BAD_DURATION` / `CALIBRATE_ZERO_DURATION` — non-numeric or zero duration.

---

## When to re-run calibration

- **After each sprint or release cycle** — new data replaces stale estimates.
- **When you change models** — a new model may have a different speed profile.
- **When Delta exceeds ±20%** — large deltas indicate the schedule is significantly
  mis-calibrated and tasks are being over- or under-allocated.
- **When `low` confidence warnings dominate** — collect more data before trusting
  the estimates.

---

## CLI reference

`solver.calibrate` is dual-mode — `--runs` and `--from-runs` are
mutually exclusive. Pick one source per invocation.

```
python -m solver.calibrate (--runs PATH | --from-runs DIR) --config PATH [options]

Source flags:
  --runs PATH                Flat runs.jsonl (legacy log-ingestion mode).
  --from-runs DIR            Directory of plan.json + actual.jsonl pairs.

Legacy --runs options:
  --dry-run                  Compute updates but do not write to disk.
  --confidence-threshold N   Min samples for medium confidence (default: 5).
  --ema-alpha ALPHA          EMA blending factor [0,1] (default: 0.3).

Runs-mode --from-runs options:
  --alpha ALPHA              EMA blending factor [0,1] (default: 0.3).
  --min-pairs N              Min paired runs required (default: 3).
  --backup                   Write <config>.bak before mutation.
```

---

## Bootstrapping a new project

If you don't have a `schedule-config.yml` yet, generate one from your project layout:

```bash
python -m solver.autodetect --project-dir . --output schedule-config.yml
```

Then run a few tasks, collect `runs.jsonl`, and feed it to `solver.calibrate`
to tune the estimates to your actual workload.
