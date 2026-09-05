# 01 — Quickstart (zero-config)

The "hello world" of `spec-kit-schedule`: five tasks, two identical
subagent lanes, no agent portfolio. `config.yml` only pins the lane
count; delete it and you get the default of three.

## What this example shows

- The unified CLI: `plan` → `schedule.md` + `schedule.json`, `next` →
  the first round of briefs, `mark` → tick tasks, `next` → … → `DONE`.
- How `[P]` and `(depends on T###)` shape the rounds.

## Run it

From the repository root:

```bash
uv run python -m solver plan examples/01-quickstart/tasks.md \
    --config examples/01-quickstart/config.yml --out /tmp/quickstart --feature quickstart

uv run python -m solver next /tmp/quickstart/schedule.json examples/01-quickstart/tasks.md
```

The second command prints round 1 with one brief per lane. In a real
project `/speckit-schedule-implement` launches those briefs as
subagents, collects their reports and calls
`python -m solver mark tasks.md T001 …` before asking for the next round.

## Expected output

`expected/out.json` is a frozen solver result (`parse_tasks` +
`scheduler` on this input). Timings differ per run; the assignments,
rounds and critical path should not.
