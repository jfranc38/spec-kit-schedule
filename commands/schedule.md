---
description: "Plan the current feature's tasks.md into parallel subagent rounds (CP-SAT optimal, critical path aware) and write schedule.md + schedule.json. Zero configuration required."
---

# /speckit.schedule.run — Plan parallel rounds

User input (optional flags, passed straight through):

$ARGUMENTS

## What this does

Reads the feature's `tasks.md`, builds the dependency graph (phase order,
`[P]` markers, same-file conflicts, tests-before-implementation,
`(depends on T###)` notes), and solves for the assignment of tasks to
**N identical subagent lanes** that minimises the total time. The result
is a list of **rounds**: in each round every lane runs its tasks in
order, in parallel with the other lanes, and the next round starts only
when the whole round is complete. No configuration file is needed.

## Steps

1. **Locate the feature.** From the repo root run
   `.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks`
   and read `FEATURE_DIR`. If the script is unavailable, use the
   `tasks.md` path the user named, or ask.

2. **Plan.** Run exactly this (the first run bootstraps a private Python
   environment under the extension directory — about a minute — and
   every later run takes seconds):

   ```bash
   EXT=".specify/extensions/schedule"; [ -x "$EXT/bin/speckit-schedule" ] || EXT="."
   "$EXT/bin/speckit-schedule" plan "$FEATURE_DIR/tasks.md" <flags>
   ```

   Translate the user input into flags — do not pass free text through.
   "4 workers" / "use four subagents" → `--workers 4`; "small contexts",
   "max 6 tasks each" → `--max-tasks-per-worker 6`; "with images" →
   `--images`; a config path → `--config PATH`. No input → no flags.
   Lanes are added automatically if the cap is too small.

3. **Relay the summary verbatim** — it is designed for the terminal:
   rounds × lanes, the speedup versus doing everything sequentially, the
   critical path (the chain of tasks that bounds the whole feature), and
   the agent utilisation. Point at `schedule.md` for the full report
   (round tables, Gantt, dependency DAG) and `schedule.json` for the
   machine-readable plan.

4. **Offer the next step** in one line:
   *"Run these rounds now with parallel subagents? → `/speckit-schedule-implement`"*
   (spelled with hyphens; spec-kit registers extension commands as skills).

## Exit codes

| Code | Meaning | What to do |
|------|---------|------------|
| 0 | Plan written | Relay the summary. |
| 1 | No schedule (INFEASIBLE / UNKNOWN); nothing written | Show the printed diagnostic; usually only reachable with an advanced `agents:` portfolio whose caps are too tight. |
| 2 | Input error | Show the message verbatim — it names the line / task (duplicate id, unknown `depends on`, contradictory dependency cycle, missing file). |

## Optional configuration

No config is required. `specify extension add` scaffolds a two-knob
`.specify/extensions/schedule/schedule-config.yml` (equivalent to the
defaults); `.specify/schedule/schedule-config.yml` takes precedence if
present. Either file:

```yaml
workers: 3              # parallel subagent lanes
max_tasks_per_worker: 0 # 0 = no cap; e.g. 6 keeps each subagent short
```

An explicit `agents:` list (heterogeneous models, skills, κ, context
budgets, prices, `objective: cost_aware`) is still supported for
advanced portfolios — see `docs/example-config.yml`. When `agents:` is
present the `workers` knobs are ignored.

## Notes

- Re-run this command whenever `tasks.md` changes (`/speckit.converge`
  appends tasks, for example). `next`/`implement` refuse to run against
  a stale plan and say so.
- Every solve also drops a small plan snapshot under
  `.specify/schedule/runs/` for the optional calibration workflow
  (`python -m solver.calibrate --help`).
- Command files call `bin/speckit-schedule`; the same verbs are
  available as `python -m solver plan|next|mark|status` inside the
  extension's environment.
