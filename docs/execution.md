# Execution — rounds, briefs, and the report protocol

This page is the contract between the planner (`python -m solver plan`),
the round emitter (`python -m solver next`) and the orchestrator
(`/speckit-schedule-implement`, i.e. the AI assistant).

## Why rounds, not waves

A subagent orchestrator has exactly one synchronisation primitive:
launch a batch of subagents, wait for **all** of them, launch the next
batch. The solver's `waves` (tasks sharing a start time) are the wrong
unit for that — three lanes with unequal durations produce a wave per
distinct start time and the barriers serialise almost everything.

A **round** gives every lane (subagent) the longest prefix of its
remaining queue whose cross-lane predecessors are already complete
(`solver/rounds.py:next_round`). Within a round each lane works through
its segment sequentially; lanes run in parallel; the orchestrator waits
once per round. Predecessors are taken from the realised schedule graph
— parser edges **plus** the solver-induced arcs (same-lane order,
file-mutex order) — so two lanes never edit the same file in the same
round unless both tasks were marked `[P]`.

The same function serves two callers:

| Caller | `done` set | Output |
|--------|------------|--------|
| `plan` | grows as each static round is emitted | `rounds` in `schedule.json` / "Execution Rounds" in `schedule.md` |
| `next` | the `[x]` checkboxes of `tasks.md` | the next round to launch, or `DONE` |

Because `next` recomputes from live state it tolerates tasks completed
out of order, tasks done by hand, and interruptions: re-running it
always yields a valid next batch.

### Speedup, honestly

`stats.speedup = sequential_duration / barrier_makespan`, where
`barrier_makespan = Σ_rounds max_lane(Σ duration)`. That is the wall time
of the barrier execution, which is ≥ the solver's continuous makespan
(kept in `stats.makespan` as a bound).

## `schedule.json`

The solver result envelope plus:

```json
{
  "feature": "004-team-notes",
  "config_path": ".specify/schedule/schedule-config.yml",   // or the scaffolded .specify/extensions/schedule/… copy, or null
  "source": { "tasks_md": "tasks.md", "sha256": "…", "task_ids": ["T001", "…"] },
  "rounds": [ { "round": 1, "lanes": [ { "agent_id": "worker-1", "tasks": ["T001", "T004"] } ] } ],
  "tasks":  [ { "id": "T001", "description": "…", "file_paths": ["src/a.py"], "parallel_flag": false, "done": false, "phase": "Setup", "story_id": null } ],
  "assignments": [ … ], "edges": [ … ], "resource_edges": [ … ], "critical_path": [ … ], "stats": { … }
}
```

`next` refuses to run when the set of task ids in `tasks.md` differs
from `source.task_ids` (exit 2, "tasks.md changed since the plan was
made") — re-plan instead of guessing.

## `next` output

```
# Round 2 of 4 — 3 lanes, 7 tasks, 11 remaining after this round

Launch one subagent per lane **in parallel** with the brief below, …

| Lane | Tasks | Files |
|------|-------|-------|
| worker-1 | T004 → T007 | `src/db/base.py`, `src/models/base.py` |
| …

---

# Subagent brief — 004-team-notes: lane `worker-1`, round 2 of 4
…
```

`--format json` returns `{"status": "round", "round", "total_rounds",
"remaining_after", "lanes": [{"agent_id", "tasks", "brief"}], "blocked"}`,
`{"status": "done", "done": N}` or `{"status": "blocked", …}` (exit 3).

## The brief

Each brief (`solver/briefs.py:render_brief`) is complete on purpose —
the orchestrator must pass it verbatim:

1. Header: lane, round k of N, how many lanes run in parallel.
2. **Context (read-only)**: `spec.md`, `plan.md`, `tasks.md` (with the
   note that `tasks.md` must not be edited).
3. **Your tasks (in order)**: the original task text and its files.
4. **Files these tasks name**: union of the lane's task files
   (advisory — spec-kit tasks such as "Add logging for user story 1"
   name no file at all).
5. **Files you must NOT edit**: files owned by the other lanes this
   round (with the owner's lane id). This is the hard rule.
6. **Rules**: never touch a forbidden file (report FAILED instead);
   creating or editing any other file is allowed but **every** path
   must appear in `TOUCHED`; do not edit `tasks.md`; do not commit;
   follow `plan.md`; tests are run.
7. **Report** — the last lines of the reply, exactly:

```
DONE: T004 T007
FAILED: T009 <one-line reason>     (omit if nothing failed)
TOUCHED: src/db/base.py src/models/base.py
NOTES: -
```

`solver/briefs.py:parse_report` extracts the three lists (case-insensitive,
`FAILED` wins over `DONE` for the same id).

## The orchestrator loop (`commands/implement.md`)

```
next ──► launch one subagent per lane (parallel) ──► collect reports
  ▲                                                       │
  │        mark DONE ids ◄── run tests / lint ◄── stage/commit (opt-in)
  └───────────── all lanes DONE? ── yes ──┘
                        │ no
                        ▼
              stop; show FAILED / colliding TOUCHED; user decides:
              retry lane · fix by hand (+mark) · skip (+mark, note the debt)
```

Invariants the protocol relies on:

- **Single writer.** Only the orchestrator edits `tasks.md`, only via
  `python -m solver mark` (atomic rewrite, idempotent, refuses unknown
  ids; `--undo` unticks).
- **No partial rounds.** A round with a FAILED lane is not advanced;
  `next` re-emits the pending tasks after the user decides.
- **Stale plans are rejected**, never silently reused.
- **Lanes are disjoint on named files** within a round by
  construction; the brief makes the other lanes' files explicit so a
  subagent refuses a task that would need one instead of racing.
  Path-less tasks are invisible to the file mutex, so after every round
  the orchestrator cross-checks the `TOUCHED` lists: a path reported by
  two lanes is flagged for review before the next round.

## Exit codes

| Verb | 0 | 1 | 2 | 3 |
|------|---|---|---|---|
| `plan` | schedule written | INFEASIBLE / UNKNOWN (summary printed) | input error | — |
| `next` | round printed, or `DONE` | — | stale, missing or unsolved plan, missing tasks.md | blocked |
| `mark` | done | — | unknown id / missing file | — |
