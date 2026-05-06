# Examples

Four self-contained, end-to-end examples of `spec-kit-schedule`.
Each subdirectory ships a `tasks.md`, a `config.yml`, a `README.md`
explaining the example, and an `expected/` folder with the frozen
solver-output JSON for diffing against your own runs.

| # | Directory | Demonstrates |
|---|-----------|--------------|
| 1 | [`01-quickstart/`](01-quickstart/) | Five tasks, two agents, default lexicographic objective. The "hello world". |
| 2 | [`02-cost-aware/`](02-cost-aware/) | Eight tasks, three agents priced cheap / balanced / premium. `objective: cost_aware` (3-phase lex on makespan, cost, max-load). |
| 3 | [`03-replan/`](03-replan/) | Six tasks, two agents. Original solve plus a `solver.replan --completed` invocation showing the residual subgraph re-solve. |
| 4 | [`04-multi-provider/`](04-multi-provider/) | Fourteen tasks, five agents across three providers (Anthropic, OpenAI, Google). `objective: cost_aware` showing skill-driven routing across a heterogeneous portfolio — the scheduler is provider-agnostic. |

Each example completes in well under a second on a modern laptop.

## Run them all

From the repository root:

```bash
make examples
```

This invokes `bin/run-examples.sh`, which parses + solves each example
and reports the final makespan and status.
