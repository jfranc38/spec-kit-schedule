---
description: "Interactively create or edit the encapsulated schedule-config.yml — the agent portfolio, skill inference rules, and solver parameters consumed by /speckit.schedule.run. Auto-scaffolds from the project's tech stack AND the AI assistant's on-disk fleet via solver.autodetect."
---

# /speckit.schedule.portfolio — Define Agent Portfolio (AI-aware)

## Purpose

Create or edit `.specify/schedule/schedule-config.yml` (v0.6.0+
encapsulated location) — the file that defines your heterogeneous
agent portfolio, skill inference rules, and solver parameters. The
workflow is **autodetect-first AND AI-aware**: read which AI
assistant the user installed spec-kit for, discover the on-disk
agent fleet for that assistant, combine those agents with stack-
derived slots and a generic base portfolio, then refine with the
user.

> **Single-entry-point note (v0.6.0+):** in most cases users run
> `/speckit.schedule.run` directly — it auto-bootstraps the portfolio
> on first invocation by following these steps inline. This command
> is for **explicit re-scaffolding** or editing.

## Workflow

### Step 1 — Detect the AI assistant

Read `.specify/integration.json` (and the legacy
`.specify/init-options.json` fallback) to identify the AI the user
installed spec-kit for:

```bash
PY=".specify/extensions/schedule/.venv/bin/python"
"$PY" -c "from solver.integration_detect import detect_integration, display_name; \
    k = detect_integration(); print(f'{k}|{display_name(k)}')"
```

Resolution order: `integration_key` → `installed_integrations[0]` →
`init-options.json:integration` → `init-options.json:ai` (legacy)
→ `None`.

Known canonical keys: `claude`, `cursor-agent`, `copilot`, `gemini`,
`codex`, `opencode`, `windsurf`, `aider`, `q`, `qwen`, `zed`, …
(see `solver/integration_detect.py:KNOWN_INTEGRATIONS`).

If the marker is missing, **ask the user directly**:

> *Which AI assistant will run these agents?*
> *(Claude Code / Cursor / Copilot / Gemini / Other — type the key)*

### Step 2 — Discover the user's fleet for that assistant

Per AI, scan the canonical on-disk locations:

| Integration key | Discovery layout                                                     |
|-----------------|----------------------------------------------------------------------|
| `claude`        | `.claude/agents/*.md`, `.claude/skills/*/SKILL.md`                   |
| `copilot`       | `.github/agents/*.agent.md`, `.github/agents/*.md`                   |
| `cursor-agent`  | `.cursor/skills/*/SKILL.md`, `.cursor/commands/*.md`                 |
| `gemini`        | `.gemini/commands/*.md`                                              |
| (other)         | `.{key}/{skills,commands,workflows,agents}/*.md` (best effort)       |

For each markdown file found, parse YAML frontmatter (`description`,
`tools`, `model` if present) and classify with a **conservative**
heuristic:

- **REVIEWER** — name/description contains `review`, `audit`,
  `verify`, `test`, `qa`, `quality`, `security`, `lint`.
- **IMPLEMENTER** — name/description contains `implement`, `build`,
  `develop`, `engineer`, `code`, `dev`.
- **HYBRID** — matches both lists, or matches neither.

This is the autodetect step's input; the actual call combines stack
detection with fleet discovery in one shot:

```bash
"$PY" -m solver.autodetect \
    --project-dir . \
    --detect-ai \
    --output .specify/schedule/schedule-config.yml
```

### Step 3 — Combine + enrich (autodetect output)

The output portfolio is the **union** of:

1. **Stack-derived agents** from project files (`pyproject.toml`,
   `package.json`, `tests/`, `docs/`, …) — `architect`, `backend`,
   `frontend`, `tester`, `docs` slots as applicable.
2. **Discovered IMPLEMENTERs** from the AI fleet — added as scheduler
   agents with their on-disk `model:` (or `REPLACE_ME` when
   frontmatter is missing).
3. **Per-AI starter slots** — appended for any role coverage gaps.
   Always emitted when implementers were discovered, so the user has
   a known-good fallback skeleton. v0.6.x ships realistic starter
   templates for the four most common AI assistants:

   | Integration key | Template                        | Tier slots                          |
   |-----------------|---------------------------------|-------------------------------------|
   | `claude`        | `templates/portfolio-claude.yml`  | `opus` / `sonnet` / `haiku`         |
   | `copilot`       | `templates/portfolio-copilot.yml` | `gpt4o` / `gpt4o-mini` / `o3-mini`  |
   | `cursor-agent`  | `templates/portfolio-cursor.yml`  | 5-agent multi-provider mix          |
   | `gemini`        | `templates/portfolio-gemini.yml`  | `pro` / `flash` / `flash-lite`      |
   | (other / none)  | `templates/base-portfolio.yml`    | `frontier` / `mid` / `small` (REPLACE_ME) |

   The per-AI templates ship realistic 2026 model identifiers and
   list prices, so users do NOT have to look up valid model strings
   manually. Only the generic `base-portfolio.yml` fallback retains
   `REPLACE_ME` placeholders.

REVIEWERs are **NOT** auto-added as scheduler agents. They show up
under `discovered_reviewers:` in the YAML output, with this prompt:

> *Detected reviewer-shaped agents — add as `review`-skill scheduler
> agents? They'll only be matched to test/review tasks.*

HYBRID-shaped agents (matched both keyword sets, or matched neither
confidently) are surfaced separately under `discovered_hybrid:` so the
scaffolder can prompt the user honestly rather than misfiling them
under reviewers:

> *Detected ambiguous (hybrid) agents — review each one and decide
> whether to promote it to a scheduler agent.*

Only on user confirmation are reviewer- or hybrid-shaped agents
promoted to scheduler agents (with `skills: [review, test]`,
`kappa: 8`, `context_budget: 16`).

### Step 4 — Confirm models with the user

When a per-AI template was used (Claude / Copilot / Cursor / Gemini)
the slot models reflect each provider's 2026 list — but pricing and
model availability change, and individual users may have disabled
access to specific models in their AI assistant. The discovered
fleet's `model:` field may also be absent or aspirational.

When the generic `base-portfolio.yml` fallback was used (unknown AI
key), the slots carry `REPLACE_ME` placeholders that the user MUST
override before the schedule is realisable.

ALWAYS prompt the user explicitly:

> *Reviewing your portfolio for {AI display name}. Confirm or
> override each agent's `model:` field — type the exact model string
> you can invoke from {AI display name}.*

For each agent, ask: `id` (default OK), `provider`, **`model`
(REQUIRED — replace any REPLACE_ME)**, `skills`, `kappa`,
`context_budget`, `speed_factor`, `price_per_1k_tokens` (only when
`objective: cost_aware`).

Cross-reference [`docs/portfolio-design.md`](../docs/portfolio-design.md)
for the κ / C / price tier table and provider-specific recipes.

### Step 5 — Honest mismatch report

When the user's fleet is review-heavy and `tasks.md` is
implementation-heavy, surface this **explicitly**:

> *X review agents found, Y implementation tasks. Adding 3 base
> implementer slots (frontier / mid / small). You'll need to fill in
> model names you can invoke from {AI display name}.*

Do NOT silently fabricate model strings. The per-AI templates ship
realistic defaults but only for the four assistants with bundled
templates (Claude / Copilot / Cursor / Gemini); for other
assistants the placeholder approach (`REPLACE_ME` in
`base-portfolio.yml`) is intentional — users see exactly which
fields they must override.

### Step 6 — Validate and save

Validate the edited config through `solver.config_schema`:

```bash
"$PY" -c "from solver.config_schema import load_config; load_config()"
```

`load_config()` with no args reads the encapsulated default path. If
it raises, surface the error verbatim and let the user fix the
offending field.

### Step 7 — Optional pre-flight test solve

If a `tasks.md` already exists in a feature spec directory, offer to
run `/speckit.schedule.run` immediately to confirm the new portfolio
solves end-to-end. This catches infeasibility early (no agent
provides a required skill, aggregate κ too low, etc.) before the user
commits the config.

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
[`docs/portfolio-design.md`](../docs/portfolio-design.md).

## Encapsulated paths (v0.6.0+)

| Resource          | Path                                                |
|-------------------|-----------------------------------------------------|
| Extension code    | `.specify/extensions/schedule/`                     |
| Encapsulated venv | `.specify/extensions/schedule/.venv/`               |
| Portfolio config  | `.specify/schedule/schedule-config.yml`             |
| Legacy (auto-migrated) | `./schedule-config.yml`                        |

## Usage

```
/speckit.schedule.portfolio
```
