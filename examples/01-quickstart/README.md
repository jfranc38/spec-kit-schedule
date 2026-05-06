# 01 — Quickstart

The "hello world" of `spec-kit-schedule`. Five tasks, two agents,
default lexicographic objective `lex(C_max, L_max)` (minimise makespan,
then balance load).

## What this example shows

- The smallest end-to-end pipeline: `tasks.md` + `config.yml` →
  parser JSON → solver JSON → rendered `schedule.md`.
- How `[P]` (parallel) and `(depends on T###)` annotations shape the DAG.
- A two-agent portfolio with a `backend` and a `tester` skill split.

For the full `tasks.md` syntax (recognised phase headers, the `[P]`
flag, `[USn]` tag, and the `(depends on T###)` / `(skill: <name>)`
annotations), see [`docs/tasks-format.md`](../../docs/tasks-format.md).

## Run it

From the repository root:

```bash
uv run python -m solver.parse_tasks \
    examples/01-quickstart/tasks.md \
    examples/01-quickstart/config.yml \
    > /tmp/in.json

uv run python -m solver.scheduler < /tmp/in.json > /tmp/out.json

uv run python -m solver.render_schedule /tmp/out.json quickstart \
    > /tmp/schedule.md

cat /tmp/schedule.md
```

## Expected output

`expected/out.json` is a frozen reference copy of the solver output.
The schedule should reach status `OPTIMAL` in under one second on a
modern laptop.

To diff your run against the expected output:

```bash
diff <(jq -S . /tmp/out.json) <(jq -S . examples/01-quickstart/expected/out.json)
```

(Per-run timings — `phase1_time`, `total_solve_time`, the `intermediate`
list — will differ; the schedule structure should not.)
