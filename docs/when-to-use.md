# When to use spec-kit-schedule

Short version: use it whenever a feature has more than a handful of
tasks and you want subagents to work on it in parallel without stepping
on each other. The planner is zero-config, the solve takes seconds, and
the output is directly executable.

## What you gain over "just run /speckit.implement"

| | `/speckit.implement` | `/speckit-schedule-implement` |
|---|---|---|
| Execution | one agent, phase by phase, task by task | N subagents per round, rounds in sequence |
| Parallelism | `[P]` is advisory prose | lanes are disjoint on files by construction |
| Ordering | narrative "Dependencies" section | DAG: phases, same-file, tests-first, `depends on` |
| Load balance | none | CP-SAT minimises makespan then balances lanes |
| Insight | none | critical path, speedup, per-round tables |
| Progress | `[x]` in tasks.md | `[x]` in tasks.md (single writer, resumable) |

The reported **speedup** is measured against the barrier execution the
orchestrator actually performs, so it is a realistic figure, not the
solver's ideal makespan.

## When it is not worth it

- **Fewer than ~5 tasks, or a strictly linear chain.** There is nothing
  to parallelise; the plan will be one lane and the speedup 1×.
- **You cannot run subagents at all.** The plan is still valid and
  `/speckit-schedule-implement` executes lanes sequentially, but then the
  benefit is only the explicit ordering and the critical-path insight.
- **Every task edits the same file.** File mutex serialises everything;
  fix the task breakdown first (that is what spec-kit's `[P]` guidance
  is for).

## Knobs that matter

- `--workers N` — how many subagents you are willing to run at once.
  More lanes ≠ more speed once the critical path dominates; the summary
  tells you the share of effort on the critical path.
- `--max-tasks-per-worker M` — keeps each subagent's context short.
  Lanes are added automatically when M is too small.
- Advanced `agents:` portfolio — only when tasks genuinely need
  different models (e.g. a review-only model), or you want cost-aware
  routing. See `docs/example-config.yml` and `docs/formulation.md`.

## Cost of the dependency

The solver pulls in Google OR-Tools (native wheels for glibc-Linux,
macOS x86_64/arm64, Windows). The first run creates a private
environment under the extension directory (~1 min); afterwards a solve
for a 25-task feature returns in seconds (anytime mode: the warm-start
incumbent is always available, the remaining budget only buys the
optimality proof). Benchmarks against a greedy list scheduler live in
[`benchmarks/`](../benchmarks/README.md).
