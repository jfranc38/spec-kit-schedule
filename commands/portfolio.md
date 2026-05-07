---
description: "Interactively create or edit schedule-config.yml — the agent portfolio, skill inference rules, and solver parameters consumed by /speckit.schedule.run. Auto-scaffolds from the project's tech stack via solver.autodetect, then refines per-runtime."
---

# /speckit.schedule.portfolio — Define Agent Portfolio

## Purpose

Create or edit the `schedule-config.yml` file that defines your heterogeneous
agent portfolio, skill inference rules, and solver parameters. The workflow
is **autodetect-first**: scaffold a starter config from the project's actual
tech stack, then refine it against the IDE/CLI you will use to run the
agents.

## Workflow

### Step 1 — Detect the runtime environment

Identify which IDE/CLI is invoking the workflow (Claude Code, Cursor,
GitHub Copilot Workspace, plain terminal, etc.) by reading available
environment cues. If detection is unreliable, ask the user directly:

> *Which environment will run these agents?*
> *(Cursor / GitHub Copilot / Claude Code / Hybrid / Other)*

The chosen runtime drives which provider-specific recipe in
[`docs/portfolio-design.md`](../docs/portfolio-design.md) the user should
follow when overriding the auto-detected models. The solver itself is
provider-agnostic — `provider` and `model` strings are pure metadata
threaded through to `schedule.md` for the downstream executor.

### Step 2 — Auto-scaffold from project context

If `schedule-config.yml` does **NOT** exist, generate a starter config
from the project's tech stack:

```bash
uv run python -m solver.autodetect --project-dir . --output schedule-config.yml
```

`solver.autodetect` inspects the project root for stack signals
(`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `pom.xml`,
`build.gradle*`, `Dockerfile`, `migrations/`, `tests/`, `docs/`, …) and
emits a Pydantic-validated YAML config with:

- An `architect` agent (always emitted) — design / review / schema /
  architecture skills, κ=5, C=32K, speed_factor=0.8.
- A `backend` agent if any backend stack is detected — skills include
  `backend`, `api`, `database` plus the detected language tag
  (`python`, `javascript`, `rust`, `go`, `java`).
- A `frontend` agent if a frontend framework dependency is detected —
  skills include `frontend`, the framework (`react`/`vue`/`svelte`),
  `css`, `html`, `javascript`.
- A `tester` agent if a `tests/`, `test/`, or `spec/` directory (or
  `*.test.*` files) exist — κ=15, C=8K, speed_factor=1.5.
- A `docs` agent if a `docs/` or `doc/` directory exists.
- `skill_rules` derived from the canonical template plus any
  project-specific top-level directories discovered.

Useful CLI flags:

```bash
uv run python -m solver.autodetect --help
# --project-dir DIR     directory to scan (default: .)
# --output PATH         write YAML to PATH (omit for stdout)
# --force               overwrite --output if it exists
# --dry-run             print to stdout, don't write
# --interactive         prompt for each agent's id, model, and kappa
# --provider TAG        default provider tag (default: anthropic)
```

If `schedule-config.yml` already exists, **read it** and skip directly
to Step 3 — never overwrite an existing config without explicit user
confirmation.

### Step 3 — Refine interactively

Show the auto-detected portfolio to the user as a summary table:

| id | provider | model | skills | κ | C (K) | speed |
|----|----------|-------|--------|---|-------|-------|
| architect | anthropic | claude-opus-4 | design, review, … | 5 | 32 | 0.8 |
| backend | anthropic | claude-sonnet-4 | backend, api, … | 10 | 16 | 1.0 |

Then iterate, anchored on the runtime detected in Step 1:

- *"These are the auto-detected agents based on your project stack.
  Replace placeholder models with the actual ones you can invoke from
  {detected-IDE} — see the {Cursor/Copilot/Claude Code/Hybrid} recipe in
  `docs/portfolio-design.md`."*
- For each agent, ask the user to confirm or override:
  `id`, `provider`, `model`, `skills`, `kappa`, `context_budget`,
  `speed_factor`, and `price_per_1k_tokens` (only if `objective:
  cost_aware` is desired).
- Cross-reference [`docs/portfolio-design.md`](../docs/portfolio-design.md)
  for the κ / C / price tier table and provider-specific recipes.

Common edits beyond agent fields:

- **Add or remove an agent** — duplicate an existing block and adjust.
- **Modify `skill_rules`** — add patterns specific to your repo
  layout (e.g. `apps/api/`).
- **Switch `solver.objective`** — `lexicographic` (default) vs
  `weighted` vs `cost_aware`.

### Step 4 — Validate and save

Before writing, validate the edited config through `solver.config_schema`:

```bash
uv run python -c "from solver.config_schema import load_config; load_config('schedule-config.yml')"
```

If it raises, surface the error message verbatim and let the user fix
the offending field. The autodetect output is already validated, so
errors at this step come from interactive overrides.

### Step 5 — Optional pre-flight test solve

If a `tasks.md` already exists in a feature spec directory, offer to
run `/speckit.schedule.run` immediately to confirm the new portfolio
solves end-to-end. This catches infeasibility early (no agent provides
a required skill, aggregate κ too low, etc.) before the user commits
the config.

## Recommended Portfolios

The auto-detect output above is sized to the project. For reference,
these are sensible starter shapes when scaffolding by hand:

**Small project (≤30 tasks)**:
- 2 agents: implementer (Sonnet, κ=10, C=16K) + tester (Haiku, κ=15, C=8K)

**Medium project (30–100 tasks)**:
- 3 agents: architect (Opus, κ=6, C=32K) + implementer (Sonnet, κ=10, C=16K) + tester (Haiku, κ=15, C=8K)

**Large project (100+ tasks)**:
- 4+ agents: architect + backend + frontend + tester (matches the autodetect default for full-stack repos)
- Consider duplicating implementers with identical skills for parallelism.

**Full-stack with TDD**:
- 4 agents: architect (design+review) + backend (python+api) + frontend (react+css) + tester (all test types)

For provider-specific recipes (Cursor, Copilot Workspace, Claude Code
single-provider, Hybrid + local), see
[`docs/portfolio-design.md`](../docs/portfolio-design.md), which also
covers the κ / C tier framework, the `context_budget` kilotoken unit,
and `cost_aware` price tuning.

## Usage

```
/speckit.schedule.portfolio
```
