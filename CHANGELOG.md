# Changelog

## [0.6.2] - 2026-05-07

### Added
- **`/speckit.schedule.status` self-diagnose command.** Closes the
  adoption-time gap where users (and audit agents like quantkit's
  conductor) saw `.specify/schedule/schedule-config.yml` missing
  immediately after `specify extension add schedule` and assumed the
  extension was broken — the config is in fact bootstrapped
  idempotently on the first `/speckit.schedule.run`. The new command
  surfaces five ordered checks (extension files, hook registration,
  solver deps, portfolio config, run history) and distinguishes
  ``missing`` (real problem) from ``expected-missing`` (will
  bootstrap automatically). Verdicts are `healthy`,
  `first-run-pending`, or `needs-attention`; the latter lists every
  actionable hint in dependency order. New module `solver.status`
  exposes `collect_status` (pure, never raises) and `format_status`
  (terminal-safe plain text — no markdown, simple
  `✓ ⚠ ✗ —` glyphs); the CLI is `python -m solver.status` with
  exit codes that map cleanly to the three verdicts (0 for healthy
  + first-run-pending, 1 for needs-attention). Stdlib-only
  implementation so the command works even when the encapsulated
  venv has not yet been bootstrapped. The README "Diagnose
  installation" section now points users at this command and
  documents the false-alarm vs real-problem distinction explicitly.

## [0.6.1] - 2026-05-07

### Fixed
- **CRITICAL: per-AI template prices were 1000× inflated.** The four
  per-AI portfolio templates (`portfolio-claude.yml`,
  `portfolio-copilot.yml`, `portfolio-cursor.yml`,
  `portfolio-gemini.yml`) populated `price_per_1k_tokens` with
  per-1M-token list prices (e.g. `15.0` for Opus, meant as $15/MTok)
  while the schema field is per-1K tokens. Result: `cost_aware`
  optimization reported dollar figures 1000× larger than reality, and
  cost rankings were correct only by coincidence. All four templates
  are now corrected to genuine per-1K rates (e.g. Opus is now
  `0.005` = $5/MTok). Users on the previous templates who relied on
  cost figures should re-run after upgrading. Examples
  (`examples/02-cost-aware/`, `examples/04-multi-provider/`)
  deliberately keep their scaled prices for demo visibility — they
  are fixtures, not templates, and their frozen baselines are
  unchanged.
- **Per-AI templates updated to verified May 2026 GA model
  identifiers.** Anthropic templates now reference
  `claude-opus-4-7` / `claude-sonnet-4-6` / `claude-haiku-4-5`
  (the prior 4.0/4.1 generations retire 2026-06-15, and
  `claude-haiku-4` was never a real id). OpenAI templates now
  reference `gpt-5.5` (current GA flagship, released 2026-04-24,
  $5/MTok) / `gpt-5.4-mini` / `o4-mini` (replacing the superseded
  `gpt-4o` / `gpt-4o-mini` / `o3-mini`). Google templates now
  reference `gemini-2.5-flash` / `gemini-2.5-flash-lite` for the
  speed tiers (the 2.0-flash family is being shut down). The Gemini
  Pro slot carries a comment about its tiered context-length
  pricing cliff at 200k input tokens.

### Added
- **Inline schedule summary** (Build 3a of v0.6.x). New
  `solver.result.summary.format_inline_summary` renders the headline
  numbers (status, makespan, agent utilisation, top-3 critical-path
  waves, total cost, anytime gap) into a compact, terminal-safe block
  that `/speckit.schedule.run` Step 2 prints to the agent's stdout
  AFTER the solver writes `out.json`. Lex mode shows a single
  `Total cost` line; cost-aware mode adds the per-agent split. Anytime
  runs surface the unproven optimality gap. INFEASIBLE results
  surface the diagnostic from `_phase1_infeasible_message` plus a
  short fixes list. Pure function (no IO, no logging, no side
  effects) so it composes cleanly into pipelines and tests. Closes
  the "what just happened?" gap by removing the need to open
  `schedule.md` for the verdict.
- **Calibration feedback loop** (Build 2 of v0.6.x). Every
  `/speckit.schedule.run` now silently drops a
  `<run_id>-plan.json` into `.specify/schedule/runs/` (best-effort
  via the new `solver.run_log` module — write failures are logged,
  never fatal). Users record observed durations to the matching
  `<run_id>-actual.jsonl` (helper CLI:
  `python -m solver.run_log append-actual`), and the new
  `/speckit.schedule.calibrate` command aggregates accumulated
  pairs to update each agent's `speed_factor` and the per-complexity
  `token_estimates` in place. Aggregation uses median-of-runs +
  EMA smoothing (`alpha=0.3` default) so individual outliers do
  not destabilise the portfolio. Adds `solver.calibrate.calibrate_from_runs`
  + `--from-runs` CLI flag, `solver._paths.runs_dir`, and the new
  command file `commands/calibrate.md`. See
  [`docs/calibration.md`](docs/calibration.md) for the full workflow.
- **Per-AI portfolio templates** with realistic 2026 model
  identifiers and list prices: `templates/portfolio-claude.yml`
  (Anthropic-only), `templates/portfolio-copilot.yml` (OpenAI-tier
  via GitHub Copilot), `templates/portfolio-cursor.yml` (5-agent
  multi-provider mix), `templates/portfolio-gemini.yml`
  (Google-only). `solver.autodetect` now picks the matching
  per-AI template when the integration key resolves to one of
  `claude` / `copilot` / `cursor-agent` / `gemini`, falling back
  to the generic `templates/base-portfolio.yml` (with `REPLACE_ME`
  placeholders) for unknown / absent keys. New module
  `solver.portfolio_templates` exposes `template_for_integration`
  for callers that need the lookup directly. Eliminates the
  friction of looking up valid model strings + prices manually
  for the most common AI assistants.
- `solver.autodetect.detect_portfolio` now surfaces hybrid-classified
  fleet agents under a new top-level `discovered_hybrid` key. Pure
  `discovered_reviewers` only contains agents that match reviewer
  keywords AND not implementer keywords — hybrids are no longer
  silently misfiled there.
- Skills inferred from each discovered implementer's frontmatter
  (`tools:` and `description:`) instead of the previous hard-coded
  `["impl", "backend", "frontend", "python", "test"]`. Falls back to
  that wide default when no recognised keyword is found.
- `bin/check-deps.sh` caches a per-mode sentinel file
  (`.venv/.deps-ok-<mode>`) inside the encapsulated venv. The next
  call exits in <50 ms when the sentinel is newer than `pyvenv.cfg`,
  cutting the import probe (300–500 ms) from the hot `/run` path.

### Changed
- `bin/install.sh` smoke test now invokes the venv's `python` binary
  directly instead of `uv run --project ...` so it cannot accidentally
  pick up a different uv-managed environment if `UV_PROJECT_ENVIRONMENT`
  is not exported.
- `commands/schedule.md` Step 1 no longer shells out to `mv` for the
  legacy-config migration; that path is handled in Python by
  `solver._paths.migrate_legacy_config` (conservative refusal-on-
  conflict + logging) via `solver.config_schema.load_config`.

### Internal
- `solver.integration_detect` extracts a small `_str_or_none` helper
  for the four near-identical "non-empty stripped string" probes.

## [0.6.0] - 2026-05-07

### Breaking changes
- **Config path moved** to `.specify/schedule/schedule-config.yml`
  (was `./schedule-config.yml`). Pre-0.6.0 configs at the repo root
  are migrated automatically on first invocation of
  `/speckit.schedule.run`; the helper
  `solver._paths.migrate_legacy_config()` is the single source of
  truth and is conservative (refuses to overwrite if both paths
  exist).
- **Encapsulated venv** at `.specify/extensions/schedule/.venv/`
  (was `./.venv/`). The bootstrap is opt-in via
  `bin/install.sh --target ./.venv` from inside the extension code
  dir, and `bin/check-deps.sh` probes the new location first
  before falling back to the legacy uv-managed venv at the repo
  root.
- **Layout convention codified**: `.specify/extensions/<id>/` is
  for extension code shipped by the installer;
  `.specify/<id>/` is for runtime state the extension writes.
  See `solver/_paths.py` for the path constructors.

### Added
- **`solver/_paths.py`** (NEW): central path constants —
  `project_root`, `extension_code_dir`, `extension_state_dir`,
  `schedule_config_path`, `encapsulated_venv_python` — plus the
  `migrate_legacy_config` migration helper.
- **`solver/integration_detect.py`** (NEW): reads
  `.specify/integration.json` (and `.specify/init-options.json`
  fallback) to identify which AI assistant the user installed
  spec-kit for. Returns a canonical key (`claude`, `copilot`,
  `cursor-agent`, `gemini`, …) plus a `display_name` helper for
  user-facing prompts.
- **`solver/fleet_discover.py`** (NEW): per-AI on-disk fleet
  discovery — scans `.claude/agents/*.md`, `.claude/skills/*/SKILL.md`,
  `.github/agents/*.agent.md`, `.cursor/skills/*/SKILL.md`,
  `.gemini/commands/*.md`, and a generic
  `.{key}/{skills,commands,workflows,agents}/*.md` fallback for
  the long tail of integrations. Parses YAML frontmatter and
  classifies each agent as IMPLEMENTER / REVIEWER / HYBRID with a
  conservative keyword heuristic.
- **`templates/base-portfolio.yml`** (NEW): generic
  `frontier`/`mid`/`small` slots with `REPLACE_ME` placeholder
  models and tier-correct κ / context_budget defaults from
  `docs/formulation.md`. Used as the fallback skeleton when the
  user's AI fleet is review-heavy or missing.
- **`solver.autodetect.detect_portfolio`** gains
  `integration_key`, `auto_detect_integration`, and
  `use_base_portfolio` keyword arguments. When opted in, the
  combined output now includes:
  - discovered IMPLEMENTERs as scheduler agents,
  - generic base slots (`frontier`/`mid`/`small`) for role coverage gaps,
  - reviewer-shaped agents under `discovered_reviewers:` (NOT
    auto-routed; offered to the user as a separate prompt),
  - `integration_key` and `integration_display_name` metadata for
    user-facing strings.
- **`--detect-ai`**, **`--integration-key`**, **`--with-base-portfolio`**
  CLI flags on `python -m solver.autodetect`.
- **`solver.config_schema.default_config_path()`** and
  **`resolve_config_path()`**: new helpers; `load_config()` now
  defaults to the encapsulated path when no explicit path is given,
  with a one-shot legacy migration baked in.
- **Idempotent first-run** for `/speckit.schedule.run`: Steps 0–1
  auto-bootstrap the encapsulated venv and the portfolio config so
  users no longer need to remember `/speckit.schedule.portfolio`
  before their first solve. Subsequent runs are no-ops past the
  preflight check.
- **15 new tests** across `tests/test_paths.py` (13),
  `tests/test_integration_detect.py` (13),
  `tests/test_fleet_discover.py` (24), plus 6 new fleet-aware tests
  in `tests/test_autodetect.py`.

### Changed
- `bin/install.sh` accepts `--target <dir>` so the encapsulated venv
  can be created at `.specify/extensions/schedule/.venv/`. Without
  the flag the legacy repo-root behaviour is preserved.
- `bin/check-deps.sh` probe order is now: encapsulated venv → uv +
  uv.lock at repo root → system `python3`. The error message points
  at the new bootstrap command first.
- `commands/schedule.md` rewritten as a three-step workflow
  (bootstrap venv, bootstrap portfolio, solve) instead of the
  previous single-step assume-everything-is-ready flow.
- `commands/portfolio.md` rewritten around AI-aware fleet
  discovery — Steps 1–7 cover detect-AI → discover-fleet →
  combine+enrich → confirm-models → mismatch-report → validate →
  optional pre-flight solve.
- `commands/visualize.md` uses the encapsulated Python interpreter
  by default.
- All version strings bumped to `0.6.0`.

## [0.5.5] - 2026-05-07

### Added
- **README documents the `after_tasks` hook**: the opt-in scheduler
  prompt added in v0.5.4 was wired in `extension.yml` but invisible to
  fresh-clone users browsing the README. Added a "How It Works"
  subsection explaining the prompt verbatim and a one-liner in
  Quick Start pointing to it as the natural path post-`/speckit.tasks`.
- **`/speckit.schedule.portfolio` is now autodetect-first**: the
  command walks the user through (1) detecting the runtime IDE/CLI,
  (2) scaffolding via `uv run python -m solver.autodetect
  --project-dir . --output schedule-config.yml`, (3) interactive
  refinement against the matching recipe in `docs/portfolio-design.md`,
  (4) validation through `solver.config_schema.load_config`, and
  (5) optional pre-flight solve. Replaces the previous blank-slate
  prompting workflow that asked users to invent every agent from
  scratch with no project context.

## [0.5.4] - 2026-05-07

### Added
- **`after_tasks` opt-in hook**: spec-kit can now surface a prompt
  (`Generate an optimal CP-SAT schedule from the new tasks?`)
  immediately after `/speckit.tasks` finishes, letting the user
  trigger `/speckit.schedule.run` without remembering the command
  by hand. Hook is `optional: true` — the agent asks first; user
  declines → nothing runs. Mirrors the canonical pattern used by
  the bundled `git` extension.

## [0.5.3] - 2026-05-06

### Fixed
- **Install command syntax**: README and INSTALL.md showed
  `specify extension add --from URL` — the `EXTENSION` positional arg
  is required, so the canonical form is
  `specify extension add schedule --from URL`. Same for the `--dev`
  variant. Caught when a user tried the documented command verbatim
  and got `Missing argument 'EXTENSION'`.
- **Release zip leanness**: The zip published by `git archive` now
  excludes developer-only paths via `.gitattributes` `export-ignore`.
  Stripped: `.github/`, `tests/`, `benchmarks/`, build artefacts,
  `.gitignore`, `.pre-commit-config.yaml`, `MANIFEST.in`, `Makefile`,
  `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, several heavy reference
  docs (`HANDOFF*.md`, `calibration.md`, `determinism.md`,
  `example-config-mixed.yml`, generated `example-schedule.{md,html}`),
  and per-example `expected/` baselines. The shipped zip dropped
  ~18% (718 KB → 585 KB), and the top-level layout now matches
  the lean shape used by peer community extensions like `verify`.

## [0.5.2] - 2026-05-06

### Added
- **Explicit `(skill: X)` annotation** in `tasks.md`: overrides the
  parser's path-based skill auto-inference. Documented in the new
  [`docs/tasks-format.md`](docs/tasks-format.md) reference.
- **`Implementation` phase recognised** by the parser
  (`Implementation`, `Implement`, `Build`, `Development`, `Develop`).
  The four shipped examples already used `## Implementation Phase`;
  tasks under that header now carry `phase="Implementation"` instead
  of silently falling back to `Setup`.
- **Top-level `makespan`, `max_load`, `total_cost`** in solver
  result dicts (mirrored from `stats[...]`). Programmatic consumers
  no longer get `None` on direct attribute access.
- **`docs/tasks-format.md`** (NEW): authoritative reference for
  recognised phase headers, annotations, skill inference, and common
  pitfalls. Linked from architecture docs and every example README.
- **Multi-provider portfolio support documented**: `docs/portfolio-design.md`
  now has provider-specific recipes (Cursor, Copilot, Claude Code,
  Hybrid+local) plus a `context_budget` units sub-section clarifying
  the value-times-1000 multiplier.
- **`examples/04-multi-provider/`** (NEW): 5-agent hybrid portfolio
  (Anthropic + OpenAI + Google) demonstrating cost-aware optimisation
  across providers.

### Changed
- `bin/check-deps.sh` probes the project venv via `uv run --no-sync`
  when uv + uv.lock are present, eliminating the false-positive where
  system-Python deps masked an empty venv. Falls back to `python3` for
  non-uv install paths.
- `solver/parse_tasks.py` strips `(skill: X)` from task descriptions
  before action-verb extraction, so the annotation does not leak into
  display fields.
- `pyproject.toml`: `plotly` upper bound widened to `>=5,<7`
  (Plotly 6 verified compatible).
- macOS CI matrix now blocking — the locale-dependent `detect_lang`
  test is fixed and the soft-fail safety valve is no longer needed.

### Fixed
- `test_lang_es_es_utf8` was flaky on macOS GitHub runners because it
  set `LANG=es_ES.UTF-8` without clearing higher-priority `LANGUAGE` /
  `LC_ALL` / `LC_MESSAGES` env vars. The test now clears them first.
- `bin/check-deps.sh` no longer silently passes when system Python
  has the deps but the project venv is empty.

## [0.5.1] - 2026-05-06

### Added
- **Architectural split**: `solver/` is now organised as four cohesive
  layers — `model/` (CP-SAT construction, types, fixed-duration resolution,
  result-shape TypedDicts), `orchestration/` (Phase-1 runner, lex and
  cost-aware drivers), `result/` (post-solve extraction + critical path),
  and the top-level `solver.scheduler` entry point. See
  [`docs/architecture.md`](docs/architecture.md).
- **Curated public API**: `solver/__init__.py` now exports an explicit
  `__all__` (`solve_from_json`, `solve_with_fixed`, `replan`,
  `parse_tasks_md`, `Task`, `Agent`, `SolverConfig`, `ScheduleInputError`,
  `WARN_*`, `__version__`). Symbols outside that list are private and may
  change without notice.
- **Test suite expansion**: 553 tests total (+57 since v0.5.0) across 7
  new specialised modules — `test_correctness_invariants.py`,
  `test_phase3_fallback.py`, `test_phase1_infeasible_message.py`,
  `test_horizon_stress.py`, `test_gap_tolerance.py`,
  `test_numerical_scale.py`, `test_invariant_branches.py`. Coverage at
  92.51%.
- **CI gates**: `actionlint` (workflow lint), `shellcheck` (shell-script
  lint), `pip-audit` (CVE scan), `pip-licenses` (license inventory),
  CI matrix: ubuntu-latest × Python 3.10/3.11/3.12 (blocking) +
  macos-latest × Python 3.12 (allowed-fail), and a `smoke-stress`
  target running a medium benchmark each CI run. A composite
  `setup-uv-python` action consolidates the boilerplate, and
  `verify-zip-assets.sh` checks the released zip contains the expected
  payload.
- **Result schema documentation**: `solver/model/result_types.py` defines
  `ScheduleResult`, `Assignment`, `AgentSummary`, `WaveBlock`, `Stats`,
  `WarningRecord` as TypedDicts (`total=False`) — schema-doc only,
  runtime annotations stay `dict[str, Any]` because of TypedDict
  variance rules with the literal `stats` dict.
- **Benchmark CLI flags**: `--num-workers axis` (sweep [1,2,4,8]),
  `--include-replan` (add a replan benchmark), `--memory` (peak-memory
  tracking via tracemalloc).
- **Named constants** for status strings (`STATUS_OPTIMAL`,
  `STATUS_FEASIBLE`, `STATUS_INFEASIBLE`, `STATUS_UNKNOWN`) and warning
  codes (`WARN_PHASE2_FALLBACK`, `WARN_PHASE3_FALLBACK`,
  `WARN_ANYTIME_TIMEOUT`, `WARN_COST_SCALE_UNDERFLOW`,
  `WARN_PARALLEL_WRITE_CONFLICT`); `ObjectiveMode` Literal type for
  `objective`.
- **`--verbose` CLI flag** wired through to CP-SAT's
  `log_search_progress` so the search log is forwarded to stderr on
  demand.
- `examples/` directory with three runnable examples
  (`01-quickstart`, `02-cost-aware`, `03-replan`) plus
  `make examples` to verify they all solve.
- `docs/architecture.md` documenting the post-refactor package layout.
- `.github/dependabot.yml` (weekly pip + GitHub Actions updates).
- `.github/ISSUE_TEMPLATE/{bug_report,feature_request}.yml` (YAML
  forms) and a structured PR template.

### Changed
- **Replan now pins frozen duration**: `solve_with_fixed`
  (and `solver.replan`) records the prior task's duration on the
  `dur[i] == d_fixed` constraint instead of silently re-deriving it
  from `p[i,a]`. This makes replans deterministic across recalibration
  changes (speed_factor / token_unit drift no longer shifts a frozen
  task).
- **Horizon as a true upper bound**: when the warm-start heuristic
  produces a feasible schedule, its makespan is now used as the
  horizon UB (strictly tighter than the previous serial-UB) and is also
  posted as `makespan <= H_heur` for direct propagation.
- **Cross-phase time-limit budget**: a 3-phase lex/cost-aware solve no
  longer burns `3 × config.time_limit` in the worst case. Each phase
  receives the *remaining* budget via `time_limit_override`.
- **`status="OPTIMAL"` is joint**: the top-level status downgrades to
  `FEASIBLE` whenever any executed phase did not prove optimality.
  Per-phase statuses remain in `stats` for diagnostics.
- **Cost-aware is now a 3-phase lex**: `lex(C_max, TotalCost, L_max)`.
  Phase 3 minimises max-load under pinned cost, with its own fallback
  warning (`WARN_PHASE3_FALLBACK`).
- **Symmetry equivalence class includes `price_per_1k_tokens`**: agents
  that differ only in price are no longer treated as interchangeable —
  the cost-aware Phase 2 needs them distinguishable.
- **`_MAX_TOKENS` lowered to 1e8**: keeps the cumulative scaled cost
  arithmetic safely inside int64 even for callers that bypass schema
  validation.
- **Benchmark `num_workers` no longer forced to 1**: respects the
  configured value to surface real-world parallel performance.
- **Coverage gate raised 85% → 90%** (project is at 92.51%).

### Fixed
- 19 hardcoded English error / warning strings migrated to the i18n
  catalog with `en` and `es` translations. Catalog completeness is
  enforced by `tests/test_i18n.py`.
- Cost-scale underflow now detected at the partial level (any
  `(task, agent)` pair with positive raw cost but zero scaled cost),
  not just total. Surfaces `WARN_COST_SCALE_UNDERFLOW`.
- `_compute_gap` rounds to 6 dp and treats objectives below
  `1e-9` as zero, removing a numerical instability around
  near-zero objectives. The anytime callback now shares this
  helper so per-incumbent and post-solve gaps agree.
- `docs/formulation.md` polish: Graham α|β|γ notation, formal
  proofs for the LP relaxation lower bound, citation block.

### Removed
- Unused `solver/_assets.py` and the redundant
  `_ModelVars.ivs_agent` field.
- `image_prefix` argument of `render_html` (was never wired through).
- Non-schema `install:` / `uninstall:` keys from `extension.yml` (the
  `specify` CLI does not consume them).
- PyPI-centric defaults in `extension.yml` and the README badges.
  Distribution remains via tagged GitHub Releases until PyPI is
  configured (see `INSTALL.md`).
- `docs/HANDOFF-v0.5.0.md` archived to `docs/archive/` — the in-flight
  plan it described is now superseded by the as-built architecture.

### Security
- `random_seed=42` set on `CpSolver` for reproducibility across runs of
  the same model.
- Sigstore attestations on release zips (`actions/attest-build-provenance`).

## [0.5.0] - 2026-04-24

### Added
- **Cost-aware objective** (`objective: cost_aware`): Phase 1 minimises makespan;
  Phase 2 pins `C_max` and minimises total token cost weighted by
  `price_per_1k_tokens` per agent. Solver output includes `total_cost`.
  Parser preserves `price_per_1k_tokens` and `token_std_dev` from config.
- **Stochastic durations**: Tasks may carry `token_std_dev`; the solver
  applies deterministic-quantile substitution (`Φ⁻¹(q; μ, σ)`,
  left-truncated at 0) at the configured quantile (default median).
  Config key: `solver.stochastic_quantile`.
- **Anytime callback**: `solver.scheduler` accepts an `on_solution` callable
  invoked at each incumbent improvement — enables streaming progress to UIs
  without polling.
- **Replanning** (`solver.replan`): `solve_with_fixed` freezes completed /
  in-flight task assignments and re-solves the residual subgraph. Frozen
  assignments incompatible with the portfolio raise `ScheduleInputError`
  instead of being silently dropped. Horizon expands to cover frozen
  start+duration before the residual solve.
- **Interactive HTML output** (`python -m solver.render_html`): Plotly-based
  self-contained schedule page with interactive Gantt and DAG. `--inline-plotly`
  embeds the full (~4 MB) bundle for offline / air-gapped use. 20 tests.
- **`make schedule-all`** Makefile target: regenerates `docs/example-schedule.md`
  + `docs/images/example-{dag,gantt}.png` + `docs/example-schedule.html` in one
  command.
- **`--help` regression test**: `tests/test_cli_help.py` verifies all five CLI
  entry-point modules respond to `--help` without error.
- PyPI install section in `INSTALL.md` documenting the planned
  distribution channel (PyPI publish itself remains on the roadmap).

### Changed
- `solver/__init__.__version__` bumped to `0.5.0`.
- `extension.yml` version bumped to `0.5.0`; description updated.
- `docs/formulation.md` updated with cost-aware objective formula, stochastic
  durations section, and replanning semantics.
- `commands/schedule.md` and `commands/visualize.md` updated with HTML output
  and new objective modes.
- `templates/schedule-template.md` adds optional `total_cost` row in Solver
  Statistics table.
- `INSTALL.md` adds PyPI install section, plotly requirement row, and
  `render_html --help` verification step.
- Scheduler symmetry-breaking key includes `price_per_1k_tokens` so agents
  differing only in cost are not incorrectly treated as interchangeable.

## [0.4.1] - 2026-04-24

### Fixed
- **Critical-path rendering was incomplete**: the matplotlib DAG drew
  only parser-supplied edges, so arcs induced by same-agent or
  file-mutex constraints (common along the critical chain) appeared as
  red-bordered nodes with no connecting red arrow. The solver now
  exposes `resource_edges` and `critical_path_edges` alongside `edges`,
  and both renderers (Mermaid + matplotlib) draw every arc on the chain
  explicitly — Mermaid uses `==>` for critical, `-.->` for resource-
  induced, and `-->` for parser edges.

### Added
- `result["resource_edges"]` — solver-induced arcs (same-agent
  consecutive, file-mutex) that were enforced by the CP-SAT model but
  are not in `edges`. Consumers can union `edges` + `resource_edges`
  to walk the full realised schedule DAG.
- `result["critical_path_edges"]` — `[[src, dst], …]` for every arc on
  the critical chain. Downstream renderers no longer re-derive this.
- Regression test: every critical arc must be reachable via
  `edges ∪ resource_edges`.
- Regression test: critical arcs induced by resources must still be
  rendered with the `==>` Mermaid arrow.

### Changed
- `solver.render_schedule` now draws Mermaid DAG edges in three styles:
  `==>` critical (red, thick), `-->` parser (solid thin),
  `-.->` resource-induced (dotted). README + `commands/schedule.md`
  updated with the arrow legend.
- `solver.visualize` guarantees every critical arc appears in the PNG,
  scales figure size with graph layer count, and uses the shared
  `AGENT_COLORS` / `CRITICAL_COLOR` palette from `solver.defaults`.
- `_hierarchical_layout` now delegates to `nx.multipartite_layout` with
  topological-depth layers for a cleaner left-to-right flow.
- Precedence graph is built once per solve (cached in
  `solve_from_json`) and threaded through `critical_path_bound`,
  `_horizon`, `list_schedule_heuristic`, and `build_model` via kwargs.
- `solver.defaults` owns the colour palette; `render_schedule` and
  `visualize` both import from it (previously duplicated).
- `_require_matplotlib` now raises `ImportError` instead of
  `SystemExit` so library callers can handle the missing optional
  dependency gracefully.
- INSTALL.md and commands/portfolio.md updated to reflect the `viz`
  extra being installed by default and the `provider` field prompt.

## [0.4.0] - 2026-04-24

### Fixed
- **Gantt regression**: Mermaid's `dateFormat X` gantt takes
  `start, end` (absolute), not `start, duration`. The 0.3.0 "fix" that
  passed `duration` produced zero-length or negative-width bars when
  `duration < start`. Reverted to emitting absolute end times and added
  a regression test that refuses to ship broken bars.

### Added
- `networkx` as a core dependency; all DAG operations — cycle detection,
  critical-path bound, topological sort in the warm-start heuristic, and
  critical-path extraction — now use battle-tested library algorithms.
- `solver/visualize.py`: matplotlib-backed static renderer for the DAG
  and Gantt. Shipped as the optional `viz` extra (`uv sync --extra viz`).
  Produces publication-grade `{feature}-dag.png` and
  `{feature}-gantt.png`. Layout is a pure-Python hierarchical topological
  sort (no graphviz binary required).
- `--image-prefix` flag on `solver.render_schedule` that injects
  `![…]({prefix}-{dag,gantt}.png)` references next to the Mermaid blocks,
  so consumers without Mermaid still see the charts.
- `make schedule` now regenerates `docs/example-schedule.md` AND
  `docs/images/example-{dag,gantt}.png` in one step.
- `.gitignore` (finally) — covers caches, venvs, egg-info, OS cruft, and
  solver output artifacts. Cleaned up previously-tracked `__pycache__` and
  `.DS_Store`.
- `tests/test_visualize.py` with `pytest.importorskip("matplotlib")`.

### Changed
- Commands (`commands/schedule.md`, `commands/visualize.md`) and
  `templates/schedule-template.md` updated to reflect the new pipeline,
  visualizer flow, provider field, and critical-path section.
- `docs/formulation.md` updated with the networkx-backed horizon
  computation (`max(critical_path, load_bound, file-mutex-bound)`) and
  the critical-path-aware warm-start description.

## [0.3.0] - 2026-04-24

### Added
- **Provider-agnostic portfolio**: optional `provider` field on agents
  (`anthropic | openai | github | google | ollama | azure | bedrock |
  groq | mistral | local | custom`), surfaced in `schedule.md` so
  downstream executors can route tasks to the right runner. See
  `docs/example-config-mixed.yml` for a Claude + GPT + Copilot + Gemini
  + Ollama portfolio.
- **Critical-path reconstruction** (`result["critical_path"]`) that
  respects both precedence and resource-induced arcs (same-agent, file-
  mutex), so the reported chain actually equals the makespan.
- **Critical Path section** in `schedule.md` with per-task cumulative
  time; Gantt bars on the critical chain gain Mermaid's `crit` marker;
  DAG edges on the critical chain render with `==>` and a bold red
  `classDef`.
- **uv-first tooling**: `uv.lock` committed, `bin/install.sh` bootstrap
  (auto-installs `uv`, syncs with `--frozen`, smoke-tests), top-level
  `Makefile` (`install`, `sync`, `test`, `cov`, `lint`, `smoke`,
  `schedule`, `package`), and `INSTALL.md` documenting zip / contributor
  / locked-down flows.
- `extension.yml` declares an `install` hook so
  `specify extension add` runs `bin/install.sh` automatically.
- CI runs via `astral-sh/setup-uv`, plus a job that packages the repo
  into a zip, extracts it in a clean temp dir, and runs `install.sh`
  end-to-end to catch teammate-install regressions.

### Fixed
- Regenerated `docs/example-schedule.md` with the current renderer
  (previous file was produced by the pre-0.2 Gantt bug and showed
  `end` in place of `duration`).

## [0.2.0] - 2026-04-24

### Changed — fail-fast and defensive programming
- Parser raises `ScheduleInputError` on duplicate task ids, unresolved
  `(depends on …)` references, and dependency cycles (previously silent
  `continue`).
- Scheduler raises on skill mismatches instead of falling back to "assign
  to any agent", and preflights total and per-skill token/κ budgets before
  building the CP-SAT model.
- Solver input JSON is now schema-validated (top-level keys, task ids,
  agent bounds, edge references).

### Added
- `solver/validation.py` — centralised input validation, cycle detection,
  and path normalisation.
- `solver/defaults.py` — single source of truth for defaults (eliminated
  duplicated defaults between parser and config template).
- `solver/warnings_collector.py` — structured warnings surfaced in both
  stderr and `schedule.md`.
- Preflight feasibility checks: no-skill-coverage, aggregate and per-skill
  token budgets, aggregate and per-skill κ budgets.
- Phase-2 fallback warning now visible in the rendered schedule.
- Warm-start heuristic now respects file-mutex constraints, so hints are
  always feasible.
- Dependency DAG in `schedule.md` is rendered from the real parser edges
  instead of an inter-agent reconstruction heuristic.
- Configurable `solver.horizon_multiplier` and `solver.token_unit`.
- `--verbose` flag on parser and scheduler for DEBUG logging to stderr.
- `pyproject.toml` with dev extras, console-script entry points, ruff and
  mypy configuration.
- GitHub Actions CI workflow (lint + typecheck + tests + smoke test).
- Initial unit and integration test suite under `tests/`.

### Fixed
- Duration computation uses a configurable token unit (default 100) and
  `math.ceil` rounding to preserve granularity that `// 1000` discarded.
- Mermaid Gantt bars now pass `duration` instead of `end` as the third
  argument, so bars reflect actual task length.
- Phase header detection is anchored and no longer matches "Advanced
  Setup Instructions" as a Setup phase.
- File paths are normalised before file-mutex grouping, so `./src/a.py`
  and `src/a.py` are the same key.
- Skill rule matching uses longest-pattern precedence so specific markers
  outrank broad prefixes.

### Removed
- Unused `field` import in `scheduler.py`.
- Duplicated defaults for `token_estimates` / `complexity_verbs` between
  parser and config template.
- Silent fallback that assigned tasks to any agent when no skill matched.

## [0.1.0] - 2026-04-24

### Added
- Initial release
- CP-SAT solver for Multi-Skill RCPSP with DAG precedence
- tasks.md parser with skill inference and token estimation
- Agent portfolio configuration via schedule-config.yml
- Lexicographic objective: minimize makespan, then minimize max load
- File-mutex constraints for conflict avoidance between parallel agents
- Hallucination-aware cardinality caps (κ) and context budgets (C)
- Warm-start from priority-rule heuristic
- Symmetry breaking for identical agents
- schedule.md output with Execution Waves, Gantt chart, and DAG
- Three slash commands: /speckit.schedule, /speckit.schedule.portfolio, /speckit.schedule.visualize
