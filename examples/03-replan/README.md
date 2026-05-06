# 03 — Replan

Six tasks across two agents. The example walks the full replan
workflow: solve once, simulate that the first two tasks completed,
then re-solve the residual subgraph with `solver.replan`.

## What this example shows

- The `solver.replan` CLI takes a *prior solver output* (`out.json`),
  the (possibly modified) `tasks.md`, the original `config.yml`, and
  one of two replan triggers:
  - `--completed T001,T002` removes those tasks from the residual
    problem (their precedence-edges to active tasks are honoured but
    they are no longer scheduled).
  - `--freeze-before T` pins every task whose `start < T` in the prior
    output; the residual solve cannot move them.
- The library equivalent is `solver.solve_with_fixed(data, fixed,
  prior_hints)` — `replan.replan` is a thin CLI on top.
- Frozen tasks have their **duration pinned to the prior value** (not
  re-derived from `p[i,a]`) so calibration drift between solves can't
  silently shift a frozen task. Fix landed in v0.5.1.

For the full `tasks.md` syntax (recognised phase headers, the `[P]`
flag, `[USn]` tag, and the `(depends on T###)` / `(skill: <name>)`
annotations), see [`docs/tasks-format.md`](../../docs/tasks-format.md).

## Run it

From the repository root:

```bash
# 1. Original solve.
uv run python -m solver.parse_tasks \
    examples/03-replan/tasks.md \
    examples/03-replan/config.yml \
    > /tmp/in.json

uv run python -m solver.scheduler < /tmp/in.json > /tmp/out.json

# 2. Simulate that T001 and T002 finished — replan the rest.
uv run python -m solver.replan \
    /tmp/out.json \
    examples/03-replan/tasks.md \
    examples/03-replan/config.yml \
    --completed T001,T002 \
    > /tmp/replan.json

uv run python -c "
import json
for label, path in [('Original', '/tmp/out.json'), ('Replan', '/tmp/replan.json')]:
    d = json.load(open(path))
    print(f'=== {label}: makespan={d[\"stats\"][\"makespan\"]} ===')
    for a in d['assignments']:
        print(f'  {a[\"task_id\"]} -> {a[\"agent_id\"]:9s} start={a[\"start\"]:3d} end={a[\"end\"]:3d}')
"
```

## Expected output

`expected/out.json` is the original solve (makespan ≈ 165 with
T001..T006).
`expected/replan.json` is the residual solve after T001 and T002 are
marked completed (makespan ≈ 95, only the remaining four tasks).

Both reach `OPTIMAL` in well under a second.
