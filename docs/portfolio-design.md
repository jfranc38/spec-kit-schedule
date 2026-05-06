# Portfolio Design Guide

This guide explains how to map your available LLM access points to a
spec-kit-schedule portfolio config (`config.yml`). The end product is a
realistic agent portfolio that the solver can route work across based on
skills, capacity, and (optionally) cost.

If you want to skip ahead and copy a working portfolio, see
[`examples/04-multi-provider/`](../examples/04-multi-provider/) for a
five-agent hybrid (Anthropic + OpenAI + Google) running with
`objective: cost_aware`.

## Core principle: agents are slots, not models

The solver treats each agent as a scheduling slot with capacity
constraints (κ — task count cap; C — token-context budget). The
`provider` and `model` fields are pure metadata that the solver does
not interpret — they are propagated to `schedule.md` so your downstream
executor (Cursor, Copilot, Claude Code, Aider, MAQA coordinator, custom
orchestrator) knows which runner to invoke for each task.

This separation matters: the same portfolio config works whether the
"opus" slot is actually invoked by `claude-cli`, by Cursor's Composer,
or by a custom Anthropic SDK wrapper. The solver's job ends when it
emits the assignment graph; the executor's job begins when it picks up
that graph and dispatches each task to the runner identified by
`provider`/`model`.

## Step 1: enumerate your access points

What LLMs can you actually call from your environment? The portfolio
should reflect reality — there is no point listing GPT-4o if your
shell cannot reach the OpenAI API.

Common access surfaces in 2026:

- **Anthropic API** — Claude Opus 4, Sonnet 4, Haiku 4.5
- **OpenAI API** — GPT-4o, GPT-4o-mini, o1, o1-mini
- **Google API** — Gemini 2.0 Pro, Flash
- **GitHub Copilot Workspace** — GPT-4o, GPT-4o-mini, Claude Sonnet 4
- **Cursor** — multi-provider routing through Cursor's pricing
- **Local (Ollama, LM Studio)** — Llama 3, Qwen 2.5 Coder, Mistral

If you are evaluating, list every model your CLI/IDE can reach. You can
prune the portfolio later once you see which agents the solver actually
ends up using under your typical task mix.

## Step 2: classify by tier

Use the κ / C tier framework from [`formulation.md`](formulation.md)
(section "Hallucination Calibration"). The tiers are calibrated
from public long-context benchmarks (RULER, NoLiMa) and apply across
providers, not per-vendor:

| Tier     | Examples                                                     | κ  | C       | Notes                                                  |
|----------|--------------------------------------------------------------|----|---------|--------------------------------------------------------|
| Frontier | Claude Opus 4, GPT-4o, Gemini 2.0 Pro, o1                    | 6  | 32K     | Top-tier models retain ≥80% accuracy at 32K context    |
| Mid      | Claude Sonnet 4, GPT-4o-mini, Gemini 2.0 Flash, GPT-4 Turbo  | 10 | 16K     | Stable performance below ~16K                          |
| Small    | Claude Haiku 3.5, Mistral Small, Llama 3 70B, GPT-3.5        | 15 | 8K      | Coding-task degradation past ~8K commonly reported     |

Override individual `kappa`/`context_budget` values when you have
provider-specific calibration data — the table is a starting point, not
a prescription.

`context_budget` in YAML is in **kilotokens** (`32` = 32K tokens). The
parser multiplies by 1000 internally; this is a frequent point of
confusion.

## Step 3: assign skills

The solver routes each task to a compatible agent based on its
`required_skill` (resolved at parse time from the file paths in
`tasks.md` via the `skill_rules` block). Common skill taxonomies you
can pick from or mix:

- **By stack**: `python`, `typescript`, `frontend`, `backend`, `react`, `css`
- **By phase**: `design`, `schema`, `impl`, `test`, `review`, `docs`
- **By specialty**: `security`, `perf`, `infra`, `architecture`

Assign skills to agents based on which models you trust for what.
Frontier models can typically do all skills; smaller and cheaper models
should be constrained to safer-output domains where regressions are
visible (test, docs) — never let a Haiku-tier model pick up a
`design` or `architecture` task in a portfolio that also has Opus
available.

The skill set on each agent is intentionally a coarse capability gate.
Fine-grained quality differences are handled by the solver's
preference for the cheapest-or-fastest agent within a compatible set,
not by the skill list itself.

## Step 4: set prices for cost-aware mode

If you use `objective: cost_aware`, set `price_per_1k_tokens` per
agent. Use list prices from the provider's pricing page, or your
contract rate if it differs.

For Copilot/Cursor where pricing is bundled (per-seat), set notional
prices that reflect your usage-cost preference — for example, $0 for
"included tier" models and $10 for "premium / metered" tier so the
solver prefers the included tier when both can do the work.

For self-hosted models (Ollama, LM Studio), set `price_per_1k_tokens:
0.0`. The solver will then treat the local agent as free, which
typically pulls in routine work that does not need a frontier model.

The `cost_aware` objective is a 3-phase lex
`lex(C_max, TotalCost, L_max)`:

1. Phase 1 finds the optimal makespan (`C_max`).
2. Phase 2 freezes `C_max` and minimises total token cost.
3. Phase 3 freezes total cost and balances max-load.

The solver will not trade away a faster wall-clock for a cheaper bill —
cost is a tiebreaker, not the primary objective. If you need a different
trade-off, switch to `objective: weighted` and set `cost_weight`
explicitly.

## Step 5: calibrate (optional)

The default `κ` / `context_budget` / `speed_factor` values are
literature-derived starting points. Once you have a few real runs,
feed the execution logs to `solver.calibrate` to update
`speed_factor` and `token_estimates` per agent from your own data.
See [`calibration.md`](calibration.md) for the workflow.

A common pitfall is over-trusting the defaults: a tester agent set to
`speed_factor: 1.5` from the docs may actually run at `speed_factor:
0.8` against your specific test suite. Calibration cannot fix bad κ /
C choices but it can substantially improve the makespan estimates the
solver uses.

## Provider-specific recipes

### Cursor portfolio

Cursor routes through multiple providers under one bill. Define one
agent per model you actually invoke from Composer, with notional
pricing reflecting Cursor's premium/included tiers.

```yaml
agents:
  - id: cursor-opus
    provider: cursor
    model: claude-opus-4
    skills: [design, schema, architecture, review]
    kappa: 6
    context_budget: 32
    speed_factor: 0.8
    price_per_1k_tokens: 15.0   # premium

  - id: cursor-gpt4o
    provider: cursor
    model: gpt-4o
    skills: [python, backend, frontend, react]
    kappa: 10
    context_budget: 32
    speed_factor: 1.0
    price_per_1k_tokens: 5.0    # premium

  - id: cursor-haiku
    provider: cursor
    model: claude-haiku-4.5
    skills: [test, docs, review]
    kappa: 15
    context_budget: 8
    speed_factor: 1.5
    price_per_1k_tokens: 0.0    # included tier
```

### Copilot Workspace portfolio

Copilot Workspace uses a small set of underlying models. Use Copilot's
own model identifiers in `model:` so the schedule.md output is
unambiguous about which Copilot route to call.

```yaml
agents:
  - id: copilot-claude
    provider: github
    model: copilot-claude-sonnet-4
    skills: [python, backend, api, schema, review]
    kappa: 10
    context_budget: 16
    speed_factor: 1.0
    price_per_1k_tokens: 0.0    # included in Copilot subscription

  - id: copilot-gpt4o
    provider: github
    model: copilot-gpt-4o
    skills: [python, frontend, react, test]
    kappa: 10
    context_budget: 16
    speed_factor: 1.0
    price_per_1k_tokens: 0.0
```

### Claude Code single-provider portfolio

Pure Anthropic portfolio. Differentiate by tier — Opus for design and
review, Sonnet for the bulk of impl, Haiku for tests and docs.

```yaml
agents:
  - id: opus
    provider: anthropic
    model: claude-opus-4
    skills: [design, architecture, review]
    kappa: 6
    context_budget: 32
    speed_factor: 0.8
    price_per_1k_tokens: 15.0

  - id: sonnet
    provider: anthropic
    model: claude-sonnet-4
    skills: [python, backend, api, frontend, schema]
    kappa: 10
    context_budget: 32
    speed_factor: 1.0
    price_per_1k_tokens: 3.0

  - id: haiku
    provider: anthropic
    model: claude-haiku-4.5
    skills: [test, e2e, unit-test, docs]
    kappa: 15
    context_budget: 8
    speed_factor: 1.5
    price_per_1k_tokens: 0.25
```

### Hybrid (Anthropic + OpenAI + local)

A real heterogeneous portfolio combines frontier-cloud agents with a
self-hosted local model for offline / private work. The cloud-side
agents (Opus, GPT-4o, GPT-4o-mini) follow the same tier conventions as
the recipes above; the genuinely new piece is the Ollama slot:

```yaml
  - id: local-qwen
    provider: ollama
    model: qwen2.5-coder:32b
    skills: [docs, review]
    kappa: 8
    context_budget: 16
    speed_factor: 0.6
    price_per_1k_tokens: 0.0    # self-hosted: no per-token cost
```

`price_per_1k_tokens: 0.0` makes the local agent free under
`objective: cost_aware`, so the solver pulls routine docs/review work
onto it whenever the skill set lines up. See
[`examples/04-multi-provider/config.yml`](../examples/04-multi-provider/config.yml)
for the complete hybrid portfolio (5 agents) along with `tasks.md`,
expected output, and run instructions in
[`examples/04-multi-provider/`](../examples/04-multi-provider/).

## Common pitfalls

- **All agents on one provider with no skill differentiation.** The
  solver becomes a load-balancer with no real optimization signal.
  Either differentiate by tier (κ, C, speed_factor) or by skill — ideally
  both.
- **Setting κ too high.** Context-rot risk; quality degrades silently
  as a single agent is asked to track too many parallel tasks. The
  defaults in [`formulation.md`](formulation.md) (Frontier κ=6,
  Mid κ=10, Small κ=15) are conservative envelopes — go lower, not
  higher, when in doubt.
- **Setting `price_per_1k_tokens: 0` for all agents in `cost_aware`
  mode.** The cost objective becomes degenerate (everything ties at
  zero) and the solver falls back to phase 1 behaviour. If your
  portfolio is genuinely free (e.g., all-local), use
  `objective: lexicographic` instead.
- **Forgetting that the IDE/CLI must actually be able to invoke the
  named model.** The solver does not check; it will happily emit a
  `model: gpt-99` assignment if you put it in the config. The schedule
  will be unrealisable at runtime.
- **Confusing `context_budget` units.** YAML accepts kilotokens
  (`32` = 32K). The parser multiplies by 1000 — do not put `32000` in
  the YAML or you will end up with a 32-million-token agent and
  trivially feasible problems.

## See also

- [`docs/formulation.md`](formulation.md) — formal model and tier table
- [`docs/architecture.md`](architecture.md) — code structure
- [`docs/calibration.md`](calibration.md) — speed/token-estimate refinement
- [`examples/04-multi-provider/`](../examples/04-multi-provider/) —
  complete hybrid portfolio with `tasks.md`, config, and frozen output
