# Wave Executor Bridge — `/speckit.implement` Integration

> Internal documentation describing how downstream orchestrators consume
> a solved `schedule.md`. Not a slash command and not shipped inside the
> packaged wheel; lives only in the source tree.

## Overview

`solver/wave_executor.py` parses a rendered `schedule.md` into an `ExecutionPlan`
and can emit that plan as JSON, a POSIX shell script, or a plain-text table.
This document describes how `/speckit.implement` (or any downstream orchestrator)
consumes the plan and drives agent execution wave-by-wave.

## Generating the Shell Script

```bash
python -m solver.wave_executor .specify/specs/<feature>/schedule.md --format shell \
    > /tmp/wave-plan.sh
```

Before executing, set the `RUNNER` environment variable to the command that
accepts `(agent_id task_id)` as positional arguments and drives one agent's
work for one task:

```bash
export RUNNER="speckit-agent-runner"
bash /tmp/wave-plan.sh
```

The emitted script uses `set -euo pipefail` and backgrounds each agent's
subprocess within a wave, issuing a `wait` before advancing to the next wave.

## Barrier Semantics

Every wave constitutes a hard precedence barrier:

- All tasks within wave N are launched concurrently (backgrounded).
- The orchestrator `wait`s for **all** wave N tasks to exit before issuing
  wave N+1 tasks.
- This mirrors the CP-SAT invariant: the solver only places tasks in the same
  wave when no edge in the DAG connects them, so concurrent execution is safe.

Wave N+1 tasks cannot start until every wave N task completes successfully.

## File-Mutex Safety

Within a single wave, no two tasks write the same file (the planner enforced
`NoOverlap` constraints for all non-`[P]` tasks sharing a path). Agents in
the same wave therefore never race on file I/O, and parallel launch is safe
without additional locking.

## Failure Handling

The generated shell script uses `set -euo pipefail`. If any agent subprocess
in wave N exits non-zero, the `wait` propagates the failure and the script
aborts before wave N+1 begins.

Operator recovery workflow:

1. Inspect which task(s) failed and correct the underlying issue.
2. Rerun `/speckit.schedule.run` to regenerate `schedule.md` from the
   current task state (future: `/speckit.schedule.run --from-state` will
   resume from the last successful wave without re-solving the full
   problem).
3. Rerun `/speckit.implement` from the corrected schedule.

## Reading the Plan Programmatically

```python
from solver.wave_executor import parse_schedule_md, ExecutionPlan

plan: ExecutionPlan = parse_schedule_md("schedule.md")
for wave in plan.waves:
    print(f"Wave {wave.index} (t={wave.start_time}): {len(wave.tasks)} tasks")
    for task in wave.tasks:
        print(f"  {task.agent_id}: {task.task_id}  files={task.files}")
```

`parse_schedule_md` raises `solver.validation.ScheduleInputError` for any
malformed or structurally incomplete `schedule.md`.

## Available Output Formats

| Format  | Flag              | Description                                    |
|---------|-------------------|------------------------------------------------|
| `json`  | `--format json`   | Full `ExecutionPlan` as JSON (default).        |
| `shell` | `--format shell`  | POSIX script with per-wave barrier `wait`s.    |
| `table` | `--format table`  | Plain-text aligned table for human inspection. |
