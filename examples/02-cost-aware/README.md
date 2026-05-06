# 02 — Cost-aware

Eight tasks across three agents with distinct prices: a `cheap`
(`$0.15 / 1k tokens`), a `balanced` ($3 / 1k tokens), and a `premium`
($15 / 1k tokens). All three offer the same skill set, so the solver is
free to assign any task to any agent — the differentiator is price.

## What this example shows

- `objective: cost_aware` runs a 3-phase lex
  `lex(C_max, TotalCost, L_max)`:
  1. Phase 1 finds the optimal makespan (`C_max`).
  2. Phase 2 freezes `C_max` and minimises total token cost.
  3. Phase 3 freezes total cost and balances max-load.
- The solver picks the cheapest-feasible assignment given the optimal
  makespan, not just the cheapest portfolio member. Tasks that can run
  in parallel are pushed to the cheap agent; tasks on the critical
  path may go to a more expensive agent if doing so shortens makespan.
- `total_cost` (in dollars, four decimal places) is reported in
  `stats` and per-agent in `agent_summary`.

For the full `tasks.md` syntax (recognised phase headers, the `[P]`
flag, `[USn]` tag, and the `(depends on T###)` / `(skill: <name>)`
annotations), see [`docs/tasks-format.md`](../../docs/tasks-format.md).

## Run it

From the repository root:

```bash
uv run python -m solver.parse_tasks \
    examples/02-cost-aware/tasks.md \
    examples/02-cost-aware/config.yml \
    > /tmp/in.json

uv run python -m solver.scheduler < /tmp/in.json > /tmp/out.json

uv run python -c "
import json
d = json.load(open('/tmp/out.json'))
print('status:', d['status'])
print('makespan:', d['stats']['makespan'])
print('total_cost: \$', d['stats']['total_cost'])
for s in d['agent_summary']:
    print(f'  {s[\"agent_id\"]:9s}: {s[\"task_count\"]} tasks, \$ {s[\"cost\"]:.4f}')
"
```

## Expected output

`expected/out.json` shows the `cheap` agent owning the bulk of the tasks
(≈$3 of cost) while `balanced` and `premium` are used only when needed
for parallelism. `premium` typically receives zero tasks.

The schedule should reach status `OPTIMAL` in well under a second.
