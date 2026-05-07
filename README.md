# spec-kit-schedule — CP-SAT Multi-Agent Task Orchestrator

[CI](https://github.com/jfranc38/spec-kit-schedule/actions/workflows/ci.yml)
[Python](https://github.com/jfranc38/spec-kit-schedule)
[License: MIT](LICENSE)

> A [spec-kit](https://github.com/github/spec-kit) extension that uses **constraint programming** (Google OR-Tools CP-SAT) to produce **provably optimal** task-to-agent assignments with DAG precedence, hallucination-aware capacity caps, file-conflict avoidance, stochastic durations, online replanning, and interactive HTML output.

## The Problem

After `/speckit.tasks` generates your task breakdown, you face a scheduling decision: which tasks go to which agents, in what order, and how many can run in parallel without causing conflicts or overloading any single agent's context window?

MAQA solves this with a **greedy heuristic** (first-available agent). This extension replaces that heuristic with a **formal optimization model** — a Multi-Skill Resource-Constrained Project Scheduling Problem (MS-RCPSP) — that **minimizes total project time while balancing load** across heterogeneous agents.

## How It Works

```
/speckit.tasks  →  tasks.md
                      │
                      ▼
              parse_tasks ────────────────────────────────────────────────────────────────┐
                      │                                                                   │
                      ▼                                                                   │
              scheduler (CP-SAT) ─── calibrate (log-ingestion EMA) ─── replan (mid-run) ──┘
                      │
          ┌───────────┴────────────────┬────────────────────┐
          ▼                            ▼                     ▼
    render_schedule             render_html             visualize
       schedule.md             schedule.html     {dag,gantt}.png
          │
          ▼
  /speckit.implement
```

1. **Parse** `tasks.md` into a typed task graph `G = (T, E)` with skill requirements, token estimates, file footprints, and DAG edges (fails fast on duplicate ids, unknown dependencies, or cycles).
2. **Calibrate** (optional) — `solver.calibrate` ingests historical run logs and updates per-agent `speed_factor` and per-complexity token estimates via EMA. Stochastic durations are then applied via deterministic-quantile substitution at solve time (`solver.stochastic_quantile`, default median).
3. **Solve** a CP-SAT model that assigns each task to a compatible agent and determines start/end times. Preflight catches infeasibility before the solver runs, and a `networkx`-backed priority-rule heuristic provides a feasible warm-start.
4. **Replan** (optional) — after a partial execution, freeze completed/in-flight tasks and re-solve the residual subgraph to correct for duration overruns without disrupting already-started work.
5. **Extract** the makespan-driving critical path (node-weighted longest path over the realised schedule graph, including same-agent and file-mutex resource arcs).
6. **Render** `schedule.md` with agent assignments, execution waves, **Critical Path table**, Gantt, dependency DAG (with critical chain highlighted via `==>` arrows and a red class), and any warnings the solver surfaced.
7. **(Optional)** Static `{feature}-dag.png` and `{feature}-gantt.png` via the matplotlib-backed visualiser (see `--image-prefix`).
8. **(Optional)** Interactive `schedule.html` via the Plotly-backed renderer (`python -m solver.render_html`) — self-contained, no server required.

### Auto-suggestion via the `after_tasks` hook

When installed alongside spec-kit, the extension registers an
opt-in hook on the `after_tasks` event. Right after `/speckit.tasks`
finishes, the agent will surface this prompt:

> *Generate an optimal CP-SAT schedule from the new tasks?*

Decline and the workflow continues normally. Accept and `/speckit.schedule.run`
fires immediately. The hook is `optional: true` — it never auto-executes.

## The Optimization Model

The model is a Multi-Skill RCPSP (Bellenguez-Morineau & Néron 2007) enhanced with:

- **DAG precedence**: Tasks respect dependency ordering from phase barriers, explicit `(depends on T###)` annotations, same-file write order, and TDD rules.
- **Heterogeneous agents**: Each agent has a skill set, speed factor, and capacity limits.
- **Cardinality cap (κ)**: Max tasks per agent session — calibrated to empirical hallucination thresholds (RULER, NoLiMa, and community findings on long-context coding-task degradation).
- **Context budget (C)**: Max cumulative tokens per agent — prevents context-rot quality degradation.
- **File mutex**: Non-`[P]` tasks writing the same file cannot execute in parallel across agents.
- **Stochastic durations**: Each task carries optional `token_std_dev`; the solver applies deterministic-quantile substitution (`Φ⁻¹(q; μ, σ)` left-truncated at 0) at the configured quantile (`solver.stochastic_quantile`, default median).
- **Replanning**: `solver.replan` freezes assignments for completed/in-flight tasks and resolves the residual sub-problem with original precedences preserved.

### Objectives


| Mode                    | `objective` value | Behaviour                                                                                |
| ----------------------- | ----------------- | ---------------------------------------------------------------------------------------- |
| Lexicographic (default) | `lexicographic`   | Phase 1: min makespan. Phase 2: pin makespan, min max-load.                              |
| Weighted                | `weighted`        | `W·C_max + L_max` — single-phase; W large enough to dominate.                            |
| Cost-aware              | `cost_aware`      | Phase 1: min makespan. Phase 2: pin makespan, min cost. Phase 3: pin cost, min max-load. |


**Cost-aware example** (add `objective: cost_aware` to your config and set `price_per_1k_tokens` per agent):

```yaml
solver:
  objective: cost_aware

agents:
  - id: cheap
    provider: openai
    model: gpt-4o-mini
    price_per_1k_tokens: 0.15
    skills: [python, backend]
    kappa: 12
    context_budget: 20
  - id: premium
    provider: anthropic
    model: claude-opus-4
    price_per_1k_tokens: 15.0
    skills: [design, review, schema]
    kappa: 6
    context_budget: 32
```

The solver output includes `total_cost` (in the same unit as `price_per_1k_tokens × tokens`) and picks the cheapest assignment that achieves optimal makespan.

## Install

### From a tagged release (recommended)

```bash
specify extension add schedule --from https://github.com/jfranc38/spec-kit-schedule/archive/refs/tags/v0.6.2.zip
```

### Local development install

```bash
git clone https://github.com/jfranc38/spec-kit-schedule
cd spec-kit-schedule
uv sync --extra dev          # bootstrap Python solver dependencies
specify extension add schedule --dev .
```

### Python solver dependencies

This extension ships a Python CP-SAT solver under `solver/`. The
`specify` CLI does not install Python packages, so the solver
dependencies must be bootstrapped once: `uv sync --extra dev` from the
cloned repo, or run `bin/install.sh` (which also installs `uv` if
absent and runs an end-to-end smoke test). PyPI distribution is on the
roadmap — see `[INSTALL.md](INSTALL.md)` and `CHANGELOG.md`.

See `[INSTALL.md](INSTALL.md)` for the zip-sharing flow, contributor
setup, and the `pip` fallback (`SKIP_UV=1 ./bin/install.sh`) for
environments where `uv` is blocked.

### Recommended Companion

Install the **Explicit Task Dependencies** preset for machine-readable dependency annotations (e.g., `(depends on T001, T003)`).

## Quick Start

```bash
# 1. After running /speckit.tasks, generate the optimal schedule.
#    First-run auto-bootstraps the venv and portfolio — no separate setup.
/speckit.schedule.run

# 2. (Optional) Visualize the result
/speckit.schedule.visualize

# 3. Execute using the wave plan
/speckit.implement
```

After `/speckit.tasks` finishes, accept the auto-prompt to schedule.
Or invoke `/speckit.schedule.run` manually any time. The first
invocation **bootstraps the encapsulated Python venv and the
portfolio config inline** — you no longer need to run
`/speckit.schedule.portfolio` separately. Re-run that command only
when you want to explicitly re-scaffold the portfolio.

### Diagnose installation

If you (or an audit tool) report `schedule-config.yml missing` after
`specify extension add schedule`, that is **expected pre-first-run
state** — the portfolio config bootstraps automatically on the first
invocation of `/speckit.schedule.run`. To get a definitive read-out
of installation health:

```
/speckit.schedule.status
```

Reports five checks (extension files, hook registration, solver
deps, portfolio config, run history) and tells you which state is
expected pre-first-run vs which actually requires intervention.
Verdicts are `healthy` (all ok), `first-run-pending` (only
expected-missing items remain — bootstrap on first run), or
`needs-attention` (at least one real problem, with hints listed in
dependency order).

Inside the repository the solver stages are regular Python modules and can be chained directly:

```bash
# 1. Parse + solve.
python -m solver.parse_tasks tasks.md schedule-config.yml > in.json
python -m solver.scheduler  < in.json                    > out.json

# 2. (Optional) Static images — requires the `viz` extra (installed by
#    default via bin/install.sh, or `uv sync --extra viz`).
python -m solver.visualize out.json images/ --feature my-feature

# 3. Render the markdown; --image-prefix embeds the PNGs next to the
#    Mermaid blocks so both views live in the same document.
python -m solver.render_schedule out.json my-feature \
    --image-prefix images/my-feature > schedule.md
```

Add `--verbose` to the parse/solve stages for DEBUG logging on stderr.

Example output is committed under `docs/example-schedule.md` plus
`docs/images/example-{dag,gantt}.png` and `docs/example-schedule.html`;
regenerate all artifacts with `make schedule-all`.

## Encapsulated layout (v0.6.0+)

The extension keeps everything under `.specify/` so no state leaks
into the repo root:

| Resource          | Path                                                |
|-------------------|-----------------------------------------------------|
| Extension code    | `.specify/extensions/schedule/`                     |
| Encapsulated venv | `.specify/extensions/schedule/.venv/`               |
| Portfolio config  | `.specify/schedule/schedule-config.yml`             |

**Breaking change vs v0.5.x:** the portfolio config moved from
`./schedule-config.yml` to `.specify/schedule/schedule-config.yml`.
Pre-0.6.0 configs at the repo root are migrated automatically the
first time `/speckit.schedule.run` is invoked. The migration is
conservative — if both paths exist, the existing encapsulated file
is left alone and the legacy one stays in place for the user to
reconcile manually.

## AI-aware portfolio scaffolding (v0.6.0+)

`/speckit.schedule.portfolio` (and the auto-bootstrap path inside
`/speckit.schedule.run`) reads `.specify/integration.json` to
identify the AI assistant the user installed spec-kit for, then
discovers the on-disk fleet for that assistant:

- `claude` → `.claude/agents/*.md` and `.claude/skills/*/SKILL.md`
- `copilot` → `.github/agents/*.agent.md`
- `cursor-agent` → `.cursor/skills/*/SKILL.md`
- `gemini` → `.gemini/commands/*.md`
- 26 other known integrations → generic `.{key}/{skills,commands,workflows,agents}/*.md`

Each markdown file's YAML frontmatter is parsed for `description` /
`model` / `tools`, and the agent is classified as IMPLEMENTER /
REVIEWER / HYBRID via a conservative keyword heuristic. Discovered
implementers become scheduler agents; reviewers are surfaced under
`discovered_reviewers` and offered to the user as an opt-in addition
(routed only to test/review tasks). Generic `frontier`/`mid`/`small`
slots from `templates/base-portfolio.yml` fill any role-coverage
gaps with `REPLACE_ME` placeholders the user must override with
models they can actually invoke from their AI assistant.

## Agent Portfolio

Configure your agents in `.specify/schedule/schedule-config.yml`. See `config-template.yml` for a fully annotated example, or `docs/example-config-mixed.yml` for a multi-provider portfolio.

```yaml
agents:
  - id: "architect"
    provider: "anthropic"
    model: "claude-opus-4"
    skills: ["design", "review", "schema"]
    kappa: 6
    context_budget: 32
    speed_factor: 0.8
```

### Provider-agnostic by design

The scheduler does not call any LLM API — it only emits a schedule. The
`model` and `provider` strings are metadata passed through to
`schedule.md` so the downstream executor (MAQA, `/speckit.implement`, a
custom coordinator) can route each task to the right runner.

Known `provider` tags: `anthropic`, `openai`, `github`, `google`,
`ollama`, `azure`, `bedrock`, `groq`, `mistral`, `local`, `custom`.
Unknown values are accepted too — useful for bespoke runners. Mix and
match freely in a single portfolio:

```yaml
agents:
  - { id: architect, provider: anthropic, model: claude-opus-4,   skills: [design, review, schema],   kappa: 6,  context_budget: 40, speed_factor: 0.8 }
  - { id: backend,   provider: openai,    model: gpt-5,            skills: [python, backend, api],    kappa: 10, context_budget: 24, speed_factor: 1.0 }
  - { id: frontend,  provider: github,    model: copilot-gpt-4.1,  skills: [frontend, react, css],    kappa: 10, context_budget: 16, speed_factor: 1.0 }
  - { id: tester,    provider: google,    model: gemini-2.5-flash, skills: [test, e2e, unit-test],    kappa: 18, context_budget: 12, speed_factor: 1.4 }
  - { id: local,     provider: ollama,    model: qwen2.5-coder:32b, skills: [review, docs],           kappa: 8,  context_budget: 16, speed_factor: 0.6 }
```

> **Re-calibrate `kappa` / `context_budget` per model.** The defaults in
> `config-template.yml` are starting estimates anchored to the current
> generation of frontier models; smaller or locally-hosted models
> typically need stricter caps.

## Commands


| Command                       | Description                                                                                                                          |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `/speckit.schedule.run`       | Parse tasks.md → solve CP-SAT → produce schedule.md (+ optional PNG images)                                                          |
| `/speckit.schedule.portfolio` | Create or edit agent portfolio configuration                                                                                         |
| `/speckit.schedule.visualize` | Emit publication-grade `<feature>-dag.png` and `<feature>-gantt.png` from a solver output JSON and embed references in `schedule.md` |
| `/speckit.schedule.calibrate` | Recalibrate `speed_factor` and `token_estimates` from accumulated plan/actual logs in `.specify/schedule/runs/`                      |
| `/speckit.schedule.status`    | Self-diagnose installation state — distinguishes real problems from expected pre-first-run state                                     |


## Mathematical Formulation

For the complete formal model with sets, parameters, decision variables, constraints, and objective function in LaTeX notation, see `[docs/formulation.md](docs/formulation.md)`.

## Troubleshooting

**Solver reports `INFEASIBLE` or raises `ScheduleInputError`.** The parser and solver run several sanity checks and prefer loud failure over silent degradation:


| Error                                               | Cause                                                   | Fix                                                                                 |
| --------------------------------------------------- | ------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `Duplicate task id …`                               | Two `- [ ] T###` lines share an id.                     | Renumber one of them.                                                               |
| `Unresolved dependencies`                           | `(depends on TNNN)` references a missing task.          | Fix the typo or declare the missing task.                                           |
| `Dependency cycle detected`                         | Explicit deps + same-file + TDD together form a cycle.  | The message prints the cycle; reorder or remove the conflicting `depends on`.       |
| `No agent provides the required skill(s)`           | `skill_rules` map a task to a skill that no agent owns. | Add the skill to an agent, or route the path to a different skill in `skill_rules`. |
| `sum(estimated_tokens) exceeds sum(context_budget)` | The portfolio cannot hold the feature.                  | Increase `context_budget`, add agents, or split the feature.                        |
| `N tasks require skill 'X' but total κ … is M`      | Cardinality cap too low for that skill bucket.          | Raise `kappa` on matching agents or add more.                                       |


**Load balance looks uneven.** Check the Warnings section in `schedule.md`: if `phase2_fallback` fired, Phase 2 timed out. In `cost_aware` mode, `phase3_fallback` signals the same condition for the load-balancing pass run after the cost has been pinned. Raise `solver.time_limit` or reduce the problem size.

**Dependency DAG in schedule.md looks wrong.** The renderer draws three arrow styles:

- `-->` (solid thin): parser edge (explicit `depends on`, phase barrier, same-file write order, or TDD rule).
- `-.->` (dotted): resource-induced edge — same-agent consecutivity or file-mutex serialisation enforced by the solver.
- `==>` (thick red): edge on the critical path; every such arc is also one of the two above, but drawn bold to mark the makespan-driver.

If an arrow surprises you, inspect the `edges`, `resource_edges`, and `critical_path_edges` fields in the solver output JSON.

## Limitations

1. **Deterministic durations**: Task times are estimated heuristically. Real LLM completion times vary ±30–50%. The model uses deterministic values with a retry budget approach rather than stochastic programming.
2. **Linear quality proxy**: The hallucination constraint uses cardinality + token caps as a linear approximation of the true non-linear quality degradation curve.
3. **Bundle composition**: Task-to-bundle mapping derives from spec-kit's `[USn]` labels. An integrated set-partitioning layer would jointly optimize packaging and scheduling but adds significant complexity.

## Development

```bash
make install      # uv bootstrap + sync (dev+viz) + smoke test
make sync         # re-materialise the venv from uv.lock
make test         # pytest
make cov          # pytest + coverage
make lint         # ruff
make typecheck    # mypy (non-blocking)
make smoke        # end-to-end docs example
make schedule     # regenerate docs/example-schedule.md + docs/images/example-{dag,gantt}.png
make schedule-all # regenerate all docs artifacts: schedule.md + PNGs + schedule.html
make package      # build dist/spec-kit-schedule.zip for teammates
make bench        # run benchmarks and write benchmarks/results/latest.md
make clean        # drop caches, dist, venv
```

## When to use

If you're unsure whether the optimisation overhead is worth it for your project, see `[docs/when-to-use.md](docs/when-to-use.md)` for a decision guide covering portfolio size, task graph density, and the heuristics-vs-CP-SAT tradeoff.

## Distribution

The canonical distribution channel is a tagged GitHub Release with the
extension zip attached as an asset. Install via the `specify` CLI as
shown under [Install](#install).

> **PyPI distribution is on the roadmap.** The repository keeps the
> wheel/sdist machinery (`pyproject.toml`, `MANIFEST.in`,
> `share/spec-kit-schedule/` data layout) intact for that future
> rollout, but for now `specify extension add` is the supported install
> path.

## License

MIT

## Author

Julio César Franco Ardila — Senior Algorithm Engineer