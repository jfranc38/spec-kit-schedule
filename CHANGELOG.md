# Changelog

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
  cross-OS matrix (ubuntu/macos/windows × py3.10–3.12), and a
  `smoke-stress` target running a medium benchmark each CI run. A
  composite `setup-uv-python` action consolidates the boilerplate, and
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
- MAQA coordinator integration support
- Three slash commands: /speckit.schedule, /speckit.schedule.portfolio, /speckit.schedule.visualize
