# spec-kit-schedule — parallel subagent rounds for spec-kit

[CI](https://github.com/jfranc38/spec-kit-schedule/actions/workflows/ci.yml)
[Python](https://github.com/jfranc38/spec-kit-schedule)
[License: MIT](LICENSE)

> A [spec-kit](https://github.com/github/spec-kit) extension that turns
> `tasks.md` into **parallel rounds for subagents** — provably optimal
> (Google OR-Tools CP-SAT), critical-path aware, safe against file
> conflicts — and then **runs those rounds** with your AI assistant's
> subagents. Zero configuration.

## In 30 seconds

```
specify extension add schedule --from https://github.com/jfranc38/spec-kit-schedule/archive/refs/tags/v0.7.2.zip

/speckit.tasks                 # spec-kit generates tasks.md as usual
/speckit-schedule-run          # plan: 25 tasks → 4 rounds × 3 subagents, 2.6× faster
/speckit-schedule-implement    # run: one subagent per lane per round, tasks.md ticks itself
```

That is the whole workflow. No YAML, no agent portfolio, no model
strings. The first run bootstraps a private Python environment under the
extension directory (about a minute); every later run takes seconds.

> Claude Code registers extension commands as skills, so the commands
> are spelled with hyphens: `/speckit-schedule-run`,
> `/speckit-schedule-implement`, `/speckit-schedule-status`. Other
> assistants may accept the dotted form.

## What you get

`/speckit-schedule-run` prints a summary like this and writes
`schedule.md` + `schedule.json` next to `tasks.md`:

```
═══ Schedule — 004-team-notes ═══
Status:    OPTIMAL
Tasks:     25
Rounds:    4 parallel rounds × 3 workers
Speedup:   2.6× (sequential 118 → 45 time units with barriers)

Critical path (7 tasks, 38% of total effort):
  T001 → T004 → T007 → T011 → T013 → T014 → T015
    T004  Setup database schema and migrations framework in src/db/base.py
    ...
Next: /speckit-schedule-implement to run the rounds with parallel subagents
```

`schedule.md` holds the round tables (lane → ordered tasks → files), the
critical path with descriptions, a Mermaid Gantt and the dependency
DAG. `schedule.json` is the machine-readable plan the implement command
follows.

`/speckit-schedule-implement` then loops: get the next round → launch
one subagent per lane **in parallel**, each with a self-contained brief
(its ordered tasks, the files they name, the files other lanes own
this round, the spec/plan to read, the report format) → collect the
`DONE / FAILED / TOUCHED` reports → tick the finished tasks in
`tasks.md` → run the project's tests → next round. Progress lives only
in the `[x]` checkboxes, so you can stop and resume at any time, and a
failed lane never advances the round.

## How it works

1. **Parse** `tasks.md` exactly as `/speckit.tasks` writes it (bare
   file paths, `(Priority: P1)`, `### Tests for User Story N`
   sub-sections). Dependencies come from phase barriers
   (Setup → Foundational → stories → Polish), `[P]` markers, same-file
   write order, tests-before-implementation, and optional
   `(depends on T###)` notes. Contradictory heuristic edges are dropped
   with a warning; only an explicit cycle is an error.
2. **Solve** a CP-SAT model over N identical subagent lanes: DAG
   precedence, file mutex across lanes, optional cap on tasks per lane;
   minimise makespan, then balance the lanes. Warm-started, anytime —
   there is always a plan.
3. **Batch into rounds.** A subagent orchestrator can only "launch a
   batch, wait for all, launch the next". Each lane gets the longest
   prefix of its queue whose cross-lane predecessors are already done;
   the same function, fed the live `[x]` state, is what `next` uses at
   run time. The reported speedup is measured against these barriers,
   not the idealised solver makespan.
4. **Brief and run.** Each lane's brief is a complete instruction sheet;
   `tasks.md` has a single writer (the orchestrator), so lanes never
   race on it.

Details: [`docs/execution.md`](docs/execution.md) (rounds, briefs,
report protocol), [`docs/tasks-format.md`](docs/tasks-format.md)
(parser contract), [`docs/formulation.md`](docs/formulation.md)
(the optimisation model), [`docs/architecture.md`](docs/architecture.md).

## Optional configuration

Nothing is required. `specify extension add` drops a two-knob
`schedule-config.yml` under `.specify/extensions/schedule/` (equivalent
to the defaults; kept across updates). Edit it there, or create
`.specify/schedule/schedule-config.yml`, which takes precedence:

```yaml
workers: 3              # parallel subagent lanes (default 3)
max_tasks_per_worker: 0 # 0 = no cap; e.g. 6 keeps every subagent's context short
```

Both are also flags: `/speckit-schedule-run --workers 4 --max-tasks-per-worker 6`.
If the cap is too small for the task count, lanes are added
automatically (never silently infeasible).

### Advanced: heterogeneous agents

The original portfolio schema still works for teams that route tasks
to different models: an `agents:` list with `skills`, `kappa` (max
tasks), `context_budget` (kilotokens), `speed_factor`,
`price_per_1k_tokens`, plus `skill_rules`, `token_estimates` and the
`solver:` block (`objective: lexicographic | weighted | cost_aware`,
`time_limit`, …). See [`docs/example-config.yml`](docs/example-config.yml)
and [`config-template.yml`](config-template.yml). When `agents:` is
present the `workers` knobs are ignored. Replanning a partially
executed run (`python -m solver.replan`) and calibration from run logs
(`python -m solver.calibrate`) remain available as CLI tools.

## Commands

| Command | What it does |
|---------|--------------|
| `/speckit-schedule-run` (aliases `plan`, `solve`) | Plan `tasks.md` into rounds → `schedule.md` + `schedule.json` + inline summary |
| `/speckit-schedule-implement` | Run the rounds with parallel subagents; resumable; single writer of `tasks.md` |
| `/speckit-schedule-status` | Diagnose the installation (files, hook, solver env, optional config, run history) |

The extension registers an optional hook after `/speckit.tasks` ("Plan
these tasks into parallel subagent rounds?") and after
`/speckit.converge` (re-plan when tasks were appended). Hooks never
auto-execute.

### Underneath: one CLI

Every command calls `bin/speckit-schedule`, a wrapper that bootstraps
the environment and runs `python -m solver <verb>`:

```bash
python -m solver plan   .specify/specs/004-x/tasks.md [--workers N] [--max-tasks-per-worker M] [--images]
python -m solver next   .specify/specs/004-x/schedule.json      # next round: briefs per lane (or DONE)
python -m solver mark   .specify/specs/004-x/tasks.md T001 T002 # tick checkboxes (--undo to untick)
python -m solver status
```

Exit codes: `0` ok / DONE, `1` no schedule, `2` input error (message
names the fix), `3` blocked (every pending task waits on another).
The individual stages (`solver.parse_tasks`, `solver.scheduler`,
`solver.render_schedule`, `solver.render_html`, `solver.visualize`,
`solver.replan`, `solver.calibrate`, `solver.wave_executor`) are still
invocable with `python -m` for pipelines.

## Install

```bash
# From a tagged release (recommended)
specify extension add schedule --from https://github.com/jfranc38/spec-kit-schedule/archive/refs/tags/v0.7.2.zip

# Local development checkout
git clone https://github.com/jfranc38/spec-kit-schedule
cd spec-kit-schedule && make install
specify extension add schedule --dev .
```

Requirements: Python 3.10–3.12 and `uv` (installed automatically by
the bootstrap if missing; `SKIP_UV=1` falls back to pip). See
[`INSTALL.md`](INSTALL.md).

## Layout inside your project

| Resource | Path |
|----------|------|
| Extension code + private venv | `.specify/extensions/schedule/` (+ `.venv/`) |
| Optional config | `.specify/extensions/schedule/schedule-config.yml` (scaffolded) or `.specify/schedule/schedule-config.yml` (wins) |
| Plan snapshots (calibration) | `.specify/schedule/runs/` |
| Plan outputs | `.specify/specs/<feature>/schedule.md`, `schedule.json` |

## Troubleshooting

| Message | Cause | Fix |
|---------|-------|-----|
| `Duplicate task id …` | Two `- [ ] T###` lines share an id | Renumber one |
| `Unresolved dependencies` | `(depends on T###)` names a missing task | Fix the id |
| `Dependency cycle detected: … origins: ['explicit', …]` | Explicit `depends on` notes contradict each other or the phase order | Remove one note |
| `Dropped the same-file edge …` (warning) | Declaration order and an explicit note disagreed; the note won | Nothing — informational |
| `tasks.md changed since the plan was made` | Tasks were added/removed after planning | `/speckit-schedule-run` again |
| `BLOCKED — no lane can start` | A pending task's predecessor is pending too, but not in any lane | Check which tasks are really done; `mark` them |
| `INFEASIBLE` | Only with an advanced `agents:` portfolio whose caps are too tight | Raise `kappa` / `context_budget` or add agents |

## Development

```bash
make install      # uv bootstrap + sync (dev+viz) + smoke test
make test         # pytest
make lint         # ruff
make typecheck    # mypy
make smoke        # end-to-end: legacy pipeline + `python -m solver plan/next/mark`
make examples     # run examples/ end-to-end
make package      # dist/spec-kit-schedule.zip
```

## License

MIT — Julio César Franco Ardila
