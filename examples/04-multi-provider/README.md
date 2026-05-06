# 04 — Multi-provider

Fourteen tasks across a hybrid five-agent portfolio that mixes three
providers: Anthropic (Opus 4, Sonnet 4), OpenAI (GPT-4o, GPT-4o-mini),
and Google (Gemini 2.0 Flash). Realistic web-app build (URL-shortener)
that exercises every layer: architecture decisions, schema, backend
services, REST API, React frontend, tests, and docs. Uses
`objective: cost_aware` so the solver actually exercises the
cost-vs-makespan trade-off across providers.

## What this example shows

- **The scheduler is provider-agnostic.** Every agent is defined by
  five scheduling-relevant fields — `skills`, `kappa`, `context_budget`,
  `speed_factor`, `price_per_1k_tokens` — plus informational
  `provider` / `model` strings. The solver allocates tasks to slots; it
  does not invoke any model. The actual model invocation is the
  responsibility of your IDE / CLI (Cursor, Copilot, Claude Code,
  Aider, custom orchestrator).
- **Skill specialisation drives routing.** Tasks bound to files matching
  `docs/ADR-` get the `design` skill, which only `opus` carries — so
  the architectural decisions land on the frontier model. Tests
  (`tests/`) only fit `gpt4o-mini`. Docs land on `gemini-flash`. Each
  provider does what it is cheapest-and-good-enough for.
- **`objective: cost_aware` runs a 3-phase lex** `lex(C_max, TotalCost,
  L_max)`. Phase 1 finds the optimal makespan, phase 2 minimises total
  token cost subject to that makespan, phase 3 balances load.
- **The cost trade-off is interesting**: `opus` (the most expensive
  agent) gets only the two `design` tasks — the work nobody else can
  do. The cheap test/docs agents (`gpt4o-mini`, `gemini-flash`)
  vacuum up parallelisable work. The mid-tier `sonnet` carries the
  critical-path backbone where its skill set lines up with the longest
  chain.

For the full `tasks.md` syntax (recognised phase headers, the `[P]`
flag, `[USn]` tag, and the `(depends on T###)` / `(skill: <name>)`
annotations), see [`docs/tasks-format.md`](../../docs/tasks-format.md).

## Run it

From the repository root:

```bash
uv run python -m solver.parse_tasks \
    examples/04-multi-provider/tasks.md \
    examples/04-multi-provider/config.yml \
    > /tmp/in.json

uv run python -m solver.scheduler < /tmp/in.json > /tmp/out.json

uv run python -c "
import json
d = json.load(open('/tmp/out.json'))
print('status:', d['status'])
print('makespan:', d['stats']['makespan'])
print('total_cost: \$', d['stats']['total_cost'])
for s in d['agent_summary']:
    print(f'  {s[\"agent_id\"]:13s} ({s.get(\"provider\",\"?\"):9s} / {s[\"model\"]:18s}): '
          f'{s[\"task_count\"]:2d} tasks, \$ {s[\"cost\"]:8.4f}, '
          f'tasks={s[\"tasks\"]}')
"
```

## Expected output

`expected/out.json` is a frozen reference copy of the solver output.
The schedule should reach status `OPTIMAL` across all three phases in
well under a second.

Key numbers (frozen):

| Agent          | Provider  | Model            | Tasks | Cost      |
|----------------|-----------|------------------|------:|----------:|
| `opus`         | anthropic | claude-opus-4    |     2 | $180.0000 |
| `sonnet`       | anthropic | claude-sonnet-4  |     5 | $ 60.0000 |
| `gpt4o`        | openai    | gpt-4o           |     2 | $ 35.0000 |
| `gpt4o-mini`   | openai    | gpt-4o-mini      |     3 | $  1.5750 |
| `gemini-flash` | google    | gemini-2.0-flash |     2 | $  0.4125 |

Makespan ≈ 238, total cost ≈ $276.99.

To diff your run against the expected output:

```bash
diff <(jq -S . /tmp/out.json) \
     <(jq -S . examples/04-multi-provider/expected/out.json)
```

(Per-run timings — `phase1_time`, `total_solve_time`, the `intermediate`
list — will differ; the schedule structure should not.)

## Pricing note

The `price_per_1k_tokens` values are in dollars but scaled up ~1000×
from 2026 list prices so the cost differential is clearly visible in
the demo numbers. Without scaling, a 14-task toy problem on real list
prices would produce sub-cent agent costs that round to noise and
defeat the point of `objective: cost_aware` as a worked example.

Relative ratios (Opus ≫ GPT-4o ≫ Sonnet ≫ GPT-4o-mini ≈ Gemini Flash)
match the public pricing pages — replace with your own list / contract
prices for a real portfolio. For a realistic-price reference config
(no scaling), see `docs/example-config-mixed.yml`. For the full
mapping recipe, see `docs/portfolio-design.md`.
