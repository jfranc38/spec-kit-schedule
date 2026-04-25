# /speckit.schedule — Optimal Multi-Agent Task Scheduling

## Purpose

Solve a constrained optimization model (CP-SAT) over the current feature's `tasks.md` to produce `schedule.md`: an optimal assignment of tasks to heterogeneous AI agents with DAG-respecting execution waves, hallucination-aware capacity caps, and file-conflict avoidance.

This command sits between `/speckit.tasks` and `/speckit.implement` in the SDD pipeline.

## Prerequisites

Before running this command, verify:

1. **tasks.md exists** in the current feature spec directory (`.specify/specs/<feature>/tasks.md`)
2. **plan.md exists** (used for file-path cross-referencing)
3. **schedule-config.yml exists** in the project root (run `/speckit.schedule.portfolio` to generate one if missing)
4. **Python 3.10+** with `ortools` installed (`pip install ortools>=9.9`)
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

Run the solver at `.specify/extensions/spec-kit-schedule/solver/scheduler.py` (or invoke the module form `python -m solver.scheduler`) with the parsed task graph and agent portfolio as JSON input. End-to-end pipeline:

```bash
# 1. Parse + solve.
python -m solver.parse_tasks tasks.md schedule-config.yml > in.json
python -m solver.scheduler < in.json > out.json

# 2. (optional) Static images — requires the `viz` extra
#    (uv sync --extra viz) and places <feature>-dag.png / <feature>-gantt.png
#    in <outdir>.
python -m solver.visualize out.json images/ --feature <feature>

# 3. Render markdown; with --image-prefix the PNGs are embedded next to
#    the Mermaid blocks so consumers without Mermaid still see the charts.
python -m solver.render_schedule out.json <feature> \
    --image-prefix images/<feature> > schedule.md

# 4. (optional) Interactive HTML — requires plotly (included in `viz` extra).
python -m solver.render_html out.json <feature> \
    --image-prefix images/<feature> > schedule.html
```

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

## Integration with /speckit.implement

When `schedule.md` exists, `/speckit.implement` SHOULD:

1. Read the Execution Wave Plan
2. For each wave, spawn parallel agents (via worktrees, subagents, or sequential if single-agent)
3. Each agent receives ONLY the tasks assigned to it, in the order specified
4. After each wave completes, proceed to the next wave (checkpoint barrier)

If the MAQA extension is installed, the wave plan can be consumed directly by `/speckit.maqa.coordinator` as the batch assignment source instead of MAQA's built-in greedy heuristic.

## Error Handling

- **No schedule-config.yml**: Prompt user to run `/speckit.schedule.portfolio` first
- **Missing ortools**: Provide installation command `pip install ortools`
- **Infeasible model**: Report which constraints are binding. Common causes:
  - Agent portfolio lacks skills required by tasks → suggest adding an agent or broadening skills
  - Context budget too tight for the number of tasks → suggest splitting into sub-features
  - DAG creates a critical path longer than any single agent's capacity → suggest increasing κ
- **Timeout without optimal**: Report best-found solution with optimality gap

## Usage

```
/speckit.schedule
```

Or with explicit configuration path:

```
/speckit.schedule --config path/to/schedule-config.yml
```

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
and failure-handling guidance, see `commands/implement_bridge.md`.
