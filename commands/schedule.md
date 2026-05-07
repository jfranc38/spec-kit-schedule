---
description: "Parse tasks.md, solve the CP-SAT scheduling model, and produce schedule.md with agent assignments, execution waves, and a Gantt diagram."
---

# /speckit.schedule.run — Optimal Multi-Agent Task Scheduling

## Purpose

Solve a constrained optimization model (CP-SAT) over the current feature's `tasks.md` to produce `schedule.md`: an optimal assignment of tasks to heterogeneous AI agents with DAG-respecting execution waves, hallucination-aware capacity caps, and file-conflict avoidance.

This command sits between `/speckit.tasks` and `/speckit.implement` in the SDD pipeline.

## Idempotent first-run

`/speckit.schedule.run` is **safe to invoke at any time** — first
run, every run. Steps 0 and 1 below auto-bootstrap missing
prerequisites (Python venv, portfolio config) so the user never has
to run a separate `/speckit.schedule.portfolio` to make the
scheduler work. After the first successful invocation, Steps 0 and 1
become no-ops and only Step 2 (the actual solve) does work.

### Step 0 — Encapsulated Python venv (auto-bootstrap if missing)

The solver is a Python package distinct from the spec-kit command
registration. v0.6.0+ keeps the venv inside the extension code dir
(`.specify/extensions/schedule/.venv/`) so it ships and is removed
together with the extension itself.

```bash
EXT_DIR=".specify/extensions/schedule"
if [ ! -x "$EXT_DIR/.venv/bin/python" ]; then
  echo "First-run: bootstrapping encapsulated Python solver venv at $EXT_DIR/.venv ..."
  if ! ( cd "$EXT_DIR" && bash bin/install.sh --target ./.venv --skip-smoke ); then
    echo "ERROR: solver venv bootstrap failed. Run manually:" >&2
    echo "  cd $EXT_DIR && bash bin/install.sh --target ./.venv" >&2
    exit 1
  fi
fi
"$EXT_DIR/bin/check-deps.sh" solver
```

If the dep check exits non-zero after the auto-bootstrap, surface its
stderr message verbatim and STOP — do not attempt the solver
invocations below. If the check succeeds, proceed.

### Step 1 — Encapsulated portfolio config (auto-portfolio if missing)

```bash
CFG=".specify/schedule/schedule-config.yml"

# Legacy config at repo root is migrated automatically by load_config();
# see solver/_paths.py:migrate_legacy_config (conservative refusal-on-
# conflict, logs the move). No shell mv needed here.

if [ ! -f "$CFG" ]; then
  echo "First-run: no portfolio yet. Launching portfolio scaffolder ..."
  # The agent reads commands/portfolio.md and executes the AI-aware
  # workflow inline (detect_integration → discover_fleet → confirm
  # models with the user → write $CFG).
fi
```

When `$CFG` does not exist, **the agent SHOULD invoke the portfolio
scaffolder workflow inline** (read `commands/portfolio.md` and follow
its steps) rather than telling the user to run a second command. The
v0.6.0 design is single-entry-point: only `/speckit.schedule.run`.

### Prerequisites for Step 2

After Steps 0 and 1 succeed:

1. **tasks.md exists** in the current feature spec directory (`.specify/specs/<feature>/tasks.md`)
2. **plan.md exists** (used for file-path cross-referencing)
3. **`.specify/schedule/schedule-config.yml` exists** (created by Step 1's auto-portfolio path)
4. **Python 3.10+** with `ortools` installed (handled by Step 0's venv bootstrap)
5. **Recommended**: The *Explicit Task Dependencies* preset is installed for machine-readable `(depends on T###)` annotations

## Workflow

### Phase 1 — Parse tasks.md into a Task Graph

Read the current feature's `tasks.md` and extract:

1. **Task nodes**: For each `- [ ] T###` line, extract:
   - `id`: The T### identifier
   - `phase`: Setup | Foundational | User Story N | Polish
   - `story_id`: The `[USn]` label (null for Setup/Foundational/Polish)
   - `story_priority`: P1/P2/P3 from the user story header
   - `parallel_flag`: Whether `[P]` is present
   - `file_paths`: All paths extracted from `in <path>` suffix and action description
   - `action_verb`: The leading verb (implement, create, write, test, etc.)

2. **Edges (DAG)**: Build precedence arcs from four sources, in priority order:
   - **(a) Explicit dependencies**: `(depends on T###, T###)` annotations (requires Explicit Task Dependencies preset)
   - **(b) Phase ordering**: Setup → Foundational → {User Stories} → Polish (checkpoint barriers)
   - **(c) Same-file write order**: Within a user story, tasks writing the same file are serialized by declaration order
   - **(d) TDD rule**: Contract/unit test tasks precede implementation tasks that touch the same file

3. **Skill inference**: Apply `skill_rules` from `schedule-config.yml` to each task's file paths to determine its `required_skill`. Fall back to `default_skill` if no pattern matches.

4. **Token estimation**: Classify each task by its `action_verb` using `complexity_verbs`, then assign the corresponding `token_estimates` value.

### Phase 2 — Build and Solve CP-SAT Model

Use the encapsulated Python interpreter from Step 0 so dependencies
are sourced from the extension's own venv:

```bash
PY="$EXT_DIR/.venv/bin/python"

# 1. Parse + solve, reading the encapsulated portfolio config.
"$PY" -m solver.parse_tasks tasks.md "$CFG" > in.json
"$PY" -m solver.scheduler < in.json > out.json

# 2. Inline summary — headline numbers (status, makespan, agent
#    utilisation, top critical-path waves, total cost) printed to the
#    agent's stdout so the user sees the verdict WITHOUT opening
#    schedule.md. Pure read of out.json; no file IO. Runs after the
#    solver writes out.json so a piped solver invocation never sees this
#    output.
"$PY" -c '
import json, sys
from solver.result.summary import format_inline_summary
with open("out.json", encoding="utf-8") as f:
    result = json.load(f)
print(format_inline_summary(result, feature_name="<feature>"))
'

# 3. (optional) Static images — requires the `viz` extra
#    (auto-installed by bin/install.sh) and places <feature>-dag.png /
#    <feature>-gantt.png in <outdir>.
"$PY" -m solver.visualize out.json images/ --feature <feature>

# 4. Render markdown; with --image-prefix the PNGs are embedded next to
#    the Mermaid blocks so consumers without Mermaid still see the charts.
"$PY" -m solver.render_schedule out.json <feature> \
    --image-prefix images/<feature> > schedule.md

# 5. (optional) Interactive HTML — requires plotly (included in `viz` extra).
"$PY" -m solver.render_html out.json <feature> \
    --image-prefix images/<feature> > schedule.html
```

After Step 2 prints the summary, the agent SHOULD relay that summary
verbatim to the user (or surface its highlights) so the headline
numbers are visible inline without the user having to open
`schedule.md`. `schedule.md` still holds the full report.

The solver output exposes three edge collections:

| Field                  | Meaning                                                |
|------------------------|--------------------------------------------------------|
| `edges`                | Parser edges (explicit, phase, same-file, TDD)         |
| `resource_edges`       | Solver-induced arcs (same-agent, file-mutex)           |
| `critical_path_edges`  | Arcs on the makespan-driving chain (from either set)   |

`schedule.md` uses all three: solid `-->` for parser edges, dotted `-.->`
for resource-induced arcs, and thick `==>` red arrows for
`critical_path_edges`. Gantt bars on the chain carry Mermaid's `crit`
marker. The matplotlib renderer (`python -m solver.visualize`) uses the
same colour code and guarantees every critical arc is drawn even if it
is not in the parser-edge set.

The solver:

1. Creates one master interval variable per task and one optional interval per (task, compatible-agent) pair
2. Enforces:
   - **Unique assignment**: Each task assigned to exactly one agent
   - **Skill eligibility**: Agent must possess the task's required skill
   - **DAG precedence**: end[i] ≤ start[j] for all edges (i,j)
   - **Per-agent disjunctive**: No two tasks overlap on the same agent (NoOverlap)
   - **File mutex**: Non-[P] tasks writing the same file cannot overlap across ANY agents (NoOverlap)
   - **Cardinality cap**: Agent task count ≤ κ_a (hallucination guardrail)
   - **Context budget**: Sum of task token costs on agent ≤ C_a (context-rot guardrail)
3. Solves with the configured objective (`lexicographic` | `weighted` | `cost_aware`); default is lexicographic (minimize makespan, then minimize max agent load)
4. Outputs the solution as a JSON structure with per-task assignments and timing

### Phase 3 — Generate schedule.md

From the solver output, produce `.specify/specs/<feature>/schedule.md` containing:

#### 3a. Agent Assignment Summary

For each agent, list assigned tasks with total token load and utilization percentage:

```markdown
## Agent Assignments

### architect (Claude Opus 4) — 5 tasks, 14.2K tokens (44% of 32K budget)
- T003: Design database schema in `src/models/schema.py`
- T012: Review API contracts in `docs/api-spec.json`
...

### backend (Claude Sonnet 4) — 8 tasks, 15.1K tokens (94% of 16K budget)
...
```

#### 3b. Execution Wave Plan

Group tasks by their solved start-time into sequential waves. Tasks within a wave execute in parallel:

```markdown
## Execution Waves

### Wave 1 (t=0) — Setup
| Task | Agent | Duration | Files |
|------|-------|----------|-------|
| T001 | backend | 2 | `package.json`, `.env` |
| T002 | backend | 1 | `tsconfig.json` |

### Wave 2 (t=2) — Foundational
| Task | Agent | Duration | Files |
|------|-------|----------|-------|
| T003 | architect | 4 | `src/models/schema.py` |
| T004 | tester | 2 | `tests/conftest.py` |

### Wave 3 (t=4) — US1 + US2 parallel
| Task | Agent | Duration | Files |
...
```

#### 3c. Mermaid Gantt Chart

```markdown
## Schedule Gantt

```mermaid
gantt
    title Feature Schedule — <feature-name>
    dateFormat X
    axisFormat %s

    section architect
    T003 Design schema :a3, 2, 6
    T012 Review contracts :a12, 8, 10

    section backend
    T001 Setup package :b1, 0, 2
    ...
```

#### 3d. Solver Statistics

```markdown
## Solver Stats
- Makespan: 14 time units
- Max agent load: 8 units (backend)
- Load range: 4 units (min: tester=4, max: backend=8)
- Tasks: 24 total, 4 agents
- Solve time: 1.2s (phase 1) + 0.8s (phase 2)
- Status: OPTIMAL
```

### Phase 4 — Validate and Report

1. Verify all tasks from tasks.md appear in schedule.md (no orphans)
2. Verify no DAG violations in the wave ordering
3. Verify no file-conflict violations between concurrent tasks
4. Print summary to the agent console

### Calibration capture (automatic, best-effort)

Every successful solve also drops a small `<run_id>-plan.json` into
`.specify/schedule/runs/` via `solver.run_log.record_plan`. The capture
is best-effort — a write failure is logged but never fails the solve
— and silently no-ops when there is no `.specify/` ancestor (e.g. ad-
hoc CLI runs in `examples/`). The captured plans feed
`/speckit.schedule.calibrate`, which closes the planning feedback loop
once the user records observed durations to the matching
`<run_id>-actual.jsonl`. See `commands/calibrate.md` for the full
workflow.

## Integration with /speckit.implement

When `schedule.md` exists, `/speckit.implement` SHOULD:

1. Read the Execution Wave Plan
2. For each wave, spawn parallel agents (via worktrees, subagents, or sequential if single-agent)
3. Each agent receives ONLY the tasks assigned to it, in the order specified
4. After each wave completes, proceed to the next wave (checkpoint barrier)

If the MAQA extension is installed, the wave plan can be consumed directly by `/speckit.maqa.coordinator` as the batch assignment source instead of MAQA's built-in greedy heuristic.

## Error Handling

- **No `.specify/schedule/schedule-config.yml`**: Step 1 auto-bootstraps it.
  Only surface an error if the auto-bootstrap itself fails.
- **Missing ortools**: Step 0 auto-installs the venv. If the bootstrap
  fails, surface the install.sh stderr verbatim.
- **Infeasible model**: Report which constraints are binding. Common causes:
  - Agent portfolio lacks skills required by tasks → suggest adding an agent or broadening skills
  - Context budget too tight for the number of tasks → suggest splitting into sub-features
  - DAG creates a critical path longer than any single agent's capacity → suggest increasing κ
- **Timeout without optimal**: Report best-found solution with optimality gap

## Usage

```
/speckit.schedule.run
```

Or with explicit configuration path (overrides the encapsulated default):

```
/speckit.schedule.run --config path/to/schedule-config.yml
```

The default config path is `.specify/schedule/schedule-config.yml`
(v0.6.0+). Pre-0.6.0 configs at `./schedule-config.yml` are migrated
automatically on first run.

## Executing Waves via /speckit.implement

Once `schedule.md` is generated, use the Wave Executor Bridge to drive
agent execution respecting the solver's precedence barriers:

```bash
# Emit a POSIX shell script — one backgrounded subprocess per agent per wave,
# with a `wait` barrier between waves.
python -m solver.wave_executor schedule.md --format shell > /tmp/wave-plan.sh

# Set RUNNER to your agent driver, then execute.
RUNNER="speckit-agent-runner" bash /tmp/wave-plan.sh
```

You can also read the plan programmatically:

```python
from solver.wave_executor import parse_schedule_md
plan = parse_schedule_md("schedule.md")
```

For full integration details, barrier semantics, file-mutex guarantees,
and failure-handling guidance, see `docs/internal/wave-executor-bridge.md`.
