---
description: "Interactively create or edit schedule-config.yml — the agent portfolio, skill inference rules, and solver parameters consumed by /speckit.schedule.run."
---

# /speckit.schedule.portfolio — Define Agent Portfolio

## Purpose

Interactively create or edit the `schedule-config.yml` file that defines your heterogeneous agent portfolio, skill inference rules, and solver parameters.

## Workflow

### If schedule-config.yml does NOT exist

1. Copy the config template from `.specify/extensions/spec-kit-schedule/config-template.yml` to the project root as `schedule-config.yml`
2. Ask the user how many agents they want to configure
3. For each agent, prompt for:
   - **id**: Short lowercase name (e.g., "architect", "backend", "tester")
   - **provider**: Optional runner tag — `anthropic`, `openai`, `github`,
     `google`, `ollama`, `azure`, `bedrock`, `groq`, `mistral`, `local`,
     `custom`, or any free-form string understood by the downstream
     executor. The scheduler is provider-agnostic; this field is
     metadata, not a gate.
   - **model**: Free-form model string (e.g. `claude-opus-4`, `gpt-5`,
     `copilot-gpt-4.1`, `gemini-2.5-pro`, `qwen2.5-coder:32b`)
   - **skills**: Comma-separated skill tags
   - **kappa**: Max tasks per session (suggest 6 for large models, 10 for medium, 15 for small)
   - **context_budget**: Max token budget in K (suggest 32 for Opus, 16 for Sonnet, 8 for Haiku)
   - **speed_factor**: Relative speed (1.0 baseline)
4. Write the completed configuration (see `docs/example-config-mixed.yml`
   for a multi-provider portfolio template)

### If schedule-config.yml ALREADY exists

1. Read the existing configuration
2. Display current agent portfolio as a summary table
3. Ask what the user wants to modify:
   - Add a new agent
   - Edit an existing agent's parameters
   - Remove an agent
   - Modify skill inference rules
   - Adjust solver parameters
4. Apply changes and write updated file

## Recommended Portfolios

Suggest these starter portfolios based on project type:

**Small project (≤30 tasks)**:
- 2 agents: implementer (Sonnet, κ=10, C=16K) + tester (Haiku, κ=15, C=8K)

**Medium project (30–100 tasks)**:
- 3 agents: architect (Opus, κ=6, C=32K) + implementer (Sonnet, κ=10, C=16K) + tester (Haiku, κ=15, C=8K)

**Large project (100+ tasks)**:
- 4+ agents: architect + backend + frontend + tester (as in config template)
- Consider duplicating the implementer with identical skills for parallelism

**Full-stack with TDD**:
- 4 agents: architect (design+review) + backend (python+api) + frontend (react+css) + tester (all test types)

## Usage

```
/speckit.schedule.portfolio
```
