# Architecture

A new-contributor's map of the `solver` package: the layout, the data
flow from `tasks.md` to a finalised schedule, the extension points,
and the test conventions. Cross-references throughout point at the
specific files, classes, and functions that hold each responsibility,
so claims here can be checked at HEAD with one `Read`.

---

## Package layout

```
solver/
├── __init__.py              # Public API surface (curated, see __all__)
├── _paths.py                # Encapsulated path constants + legacy-config migration (v0.6.0+)
├── scheduler.py             # Top-level entry: solve, solve_from_json, solve_with_fixed, CLI
├── model/                   # Pure model construction (no solver loops)
│   ├── __init__.py
│   ├── types.py             # Domain dataclasses: Task, Agent, SolverConfig, Durations
│   ├── fixed.py             # resolve_fixed_duration helper for replan / solve_with_fixed
│   ├── build.py             # build_model + ModelBundle, _PreparedInputs, horizon, symmetry classes
│   └── result_types.py      # ScheduleResult / Stats / Assignment TypedDicts (schema doc)
├── orchestration/           # Solver loops + phase orchestration
│   ├── __init__.py
│   ├── runner.py            # _run_solver, _AnytimeCallback, _run_phase, Phase-1 helpers
│   ├── lex.py               # _solve_lexicographic (2-phase pinning)
│   └── cost_aware.py        # _solve_cost_aware (3-phase pinning)
├── result/                  # Post-solve result building
│   ├── __init__.py
│   └── extract.py           # _extract_assignments, critical-path, hint adapters
├── _render_helpers.py       # task_label, format_agent_model_label
├── parse_tasks.py           # tasks.md → JSON envelope (CLI + library)
├── replan.py                # Online re-optimisation
├── calibrate.py             # Log ingestion for speed_factor / token_estimates
├── run_log.py               # Plan capture + actual recording for the calibration loop (v0.6.x Build 2)
├── render_schedule.py       # Markdown output
├── render_html.py           # Plotly HTML output
├── visualize.py             # PNG (matplotlib) output
├── validation.py            # Input shape checks; ScheduleInputError
├── warnings_collector.py    # WarningCollector logging Handler
├── wave_executor.py         # Schedule → execution waves
├── anytime.py               # Anytime mode helper(s)
├── autodetect.py            # Auto-detect portfolio from a project (+ AI fleet enrichment, v0.6.0+)
├── integration_detect.py    # Detect AI assistant from .specify/integration.json (v0.6.0+)
├── fleet_discover.py        # Per-AI on-disk fleet discovery (v0.6.0+)
├── defaults.py              # Single source of default constants
├── i18n.py + i18n_catalog.py # i18n with EN+ES translations
└── config_schema.py         # Pydantic config validation (default path → encapsulated, v0.6.0+)
```

## Encapsulated state layout (v0.6.0+)

The extension keeps everything under `.specify/` so no state leaks
into the repo root:

```
<project_root>/
├── .specify/
│   ├── extensions/
│   │   └── schedule/                    # ← Extension code (managed by `specify`)
│   │       ├── bin/, commands/, solver/, templates/, ...
│   │       └── .venv/                   # ← Encapsulated Python venv
│   ├── schedule/                        # ← Extension RUNTIME state (managed by us)
│   │   ├── schedule-config.yml          # ← User portfolio
│   │   └── runs/                        # ← Plan/actual log pairs (calibration feedback loop)
│   │       ├── <run_id>-plan.json       #     Captured automatically after each solve
│   │       └── <run_id>-actual.jsonl    #     Appended via solver.run_log.append_actual
│   ├── integration.json                 # (read-only, written by `specify init`)
│   └── ...
└── tasks.md, package.json, ...
```

The convention is: **`.specify/extensions/<id>/` is for extension
code, `.specify/<id>/` is for runtime state**. The two trees are
separate so users can wipe state without re-installing the extension
(and vice-versa).

`solver/_paths.py` exposes the canonical accessors:

- `project_root(start)` — walks up to the nearest `.specify/`
- `extension_code_dir(start)` — `<root>/.specify/extensions/schedule`
- `extension_state_dir(start)` — `<root>/.specify/schedule`
- `schedule_config_path(start)` — `<state>/schedule-config.yml`
- `runs_dir(start)` — `<state>/runs` (calibration plan/actual logs)
- `encapsulated_venv_python(start)` — `<code>/.venv/bin/python`
- `migrate_legacy_config(project)` — one-shot migration from
  pre-0.6.0 `./schedule-config.yml`. Conservative: refuses to
  overwrite an existing encapsulated file.

The directories `model/`, `orchestration/`, and `result/` mirror the
three responsibilities of a solver-driven application: build, drive,
extract. Imports flow downhill — `orchestration/` depends on `model/`,
`result/` depends on `model/`, neither depends on the other at module
load (a small handful of `_run_phase` helpers in `runner.py` import
`result.extract._rehint_from` lazily to avoid a cycle).

---

## Data flow

End-to-end pipeline from a markdown task list to a rendered schedule:

```
tasks.md  ─┐
           ├──> parse_tasks.parse_tasks_md ──> input JSON envelope
config.yml─┘                                       │
                                                   v
                          scheduler.solve_from_json(data)
                                                   │
            ┌──────────────────────────────────────┤
            │                                      │
            v                                      v
  _parse_input  →  Task[], Agent[],     model.build._prepare_solve_inputs
                   (i,j) edges,              │
                   SolverConfig              ├─ _raise_if_cycle
                                             ├─ preflight_checks
                                             ├─ compute_compatible_agents
                                             ├─ compute_durations
                                             ├─ compute_min_durations
                                             ├─ build_file_conflict_groups
                                             └─ list_schedule_heuristic  (warm start)
                                                   │
                                                   v
                          model.build.build_model(...)  →  ModelBundle
                                                   │  (CP-SAT model, vars,
                                                   │   constraints, horizon,
                                                   │   total_cost when cost_aware)
                                                   │
                                                   v
                       orchestration.{lex, cost_aware, weighted}
                                                   │
                                                   v
                        result.extract._finalize_result
                                                   │
                                                   v
                                    ScheduleResult dict
                                                   │
                                                   v
                         render_schedule | render_html | visualize | wave_executor
```

Numbered walk-through:

1. **Input.** A user provides `tasks.md` (markdown checklist with
   optional `(depends on Tnnn)` and `[P]` annotations) and `config.yml`
   (agent portfolio + skill-routing rules). See
   `docs/example-tasks.md` and `docs/example-config.yml` for canonical
   shapes, and [`docs/tasks-format.md`](tasks-format.md) for the full
   parser specification (recognised phase headers, annotation syntax,
   skill inference, complexity bucketing).
2. **Parse.** `parse_tasks.parse_tasks_md(path, config_dict)` produces
   the JSON envelope `{tasks, edges, agents, config, warnings?}`
   that the solver consumes.
3. **Solve orchestration.** `scheduler.solve_from_json(data)` dispatches:
   - `_parse_input` re-typifies the dict into typed `Task[]`,
     `Agent[]`, `SolverConfig` and integer-indexed edges.
   - `model.build._prepare_solve_inputs` runs cycle detection,
     `preflight_checks`, computes `compat`, `Durations`, `min_dur`,
     file-conflict groups, and the warm-start hints from
     `list_schedule_heuristic`. It returns a `_PreparedInputs` bundle.
   - `model.build.build_model(...)` posts the CP-SAT model: variables
     (`start`, `end`, `dur`, `x[i,a]`, `load`, `max_load`, `makespan`,
     and `total_cost` when cost-aware), precedence, file-mutex
     NoOverlap, per-agent κ and context-budget caps, symmetry-breaking
     between permutation-equivalent agents, and the horizon estimator.
     Returns a `ModelBundle`.
   - `scheduler.solve(...)` applies fixed-task pins (replan only),
     applies warm-start hints, then dispatches by `config.objective`:
     - `weighted` — a single-phase minimise of
       `makespan_weight * makespan + max_load`, inlined in `solve`.
     - `cost_aware` — `orchestration.cost_aware._solve_cost_aware`
       (Phase 1 makespan, Phase 2 cost, Phase 3 max-load).
     - default `lexicographic` — `orchestration.lex._solve_lexicographic`
       (Phase 1 makespan, Phase 2 max-load).
4. **Result.** Each phase loop ends in
   `result.extract._finalize_result`, which builds `assignments`,
   `waves`, `agent_summary`, `critical_path`, and the `stats` block.
   `_decorate_result` then appends `quantile_used`, `edges`, and the
   serialised `tasks` list. Schema is documented by
   `model.result_types.ScheduleResult`.
5. **Render (optional).** Any of `render_schedule` (markdown),
   `render_html` (Plotly), `visualize` (PNG), or `wave_executor`
   (execution waves) consumes the result dict.

The replan path takes a parallel route: `replan.replan(prior_output,
solver_input, freeze_before|completed_ids)` removes completed tasks
from the residual subgraph (preserving transitive precedence), pins
in-flight tasks via `_build_fixed_assignments`, then delegates to
`scheduler.solve_with_fixed`.

---

## Extension points

The package is laid out so each common extension lands in exactly one
place. Use the corresponding hook below.

- **New objective mode.** Add a function in `solver/orchestration/`
  matching the lex / cost_aware pattern (Phase 1 makespan via
  `runner._solve_phase1_makespan`, then `runner._run_phase` for each
  pinned subsequent phase). Register an `OBJECTIVE_*` constant in
  `solver/defaults.py`, list it on `ObjectiveMode`, and dispatch in
  `solver.scheduler.solve(...)`. If the new objective requires a new
  CP-SAT variable (e.g. cost), add it inside
  `solver.model.build.build_model` and expose it on `ModelBundle`.
- **New constraint.** Add an `_add_*_constraints` function in
  `solver/model/build.py` and call it from `build_model` after
  `_build_variables`. Update `list_schedule_heuristic` in
  `scheduler.py` to respect the new constraint so warm-start hints
  remain feasible.
- **New renderer.** Consume the `ScheduleResult` shape defined in
  `solver/model/result_types.py`. Match the existing renderer entry
  signatures (`render_schedule`, `render_html`, `visualize`) — they
  all take the result dict directly.
- **New i18n message.** Add a key to
  `solver.i18n_catalog.MESSAGES` with both `"en"` and `"es"`
  translations. Reference it via `solver.i18n.t("key", **fmt_kwargs)`.
  Warning codes (`WARN_*`) are also exported from `i18n_catalog`.
- **New default constant.** Add to `solver/defaults.py` only — this
  module is the documented single source of truth, and previous
  duplications between parser and scheduler caused config drift. Other
  modules `from .defaults import …`.
- **New input field.** Extend the relevant dataclass in
  `solver/model/types.py`, populate it in `scheduler._parse_input`,
  and (if user-facing) update `solver/config_schema.py` Pydantic
  validation.

---

## Test layout

- `tests/_helpers.py` — JSON-builder helpers (`make_task`, `make_agent`,
  `make_solver_input`, plus DAG builders like `make_chain_*`). Plain
  functions, not fixtures, so callers can pass per-task overrides.
- `tests/conftest.py` — shared pytest fixtures and hooks only. The
  builder functions live in `_helpers.py` to keep `conftest.py` focused.
- Per-feature test files: `test_scheduler.py`, `test_replan.py`,
  `test_calibrate.py`, `test_render.py`, `test_render_html.py`,
  `test_visualize.py`, `test_wave_executor.py`, `test_parse_tasks.py`,
  `test_validation.py`, `test_config_schema.py`, `test_i18n.py`,
  `test_anytime.py`, `test_autodetect.py`, `test_cost_objective.py`,
  `test_symmetry_breaking.py`, `test_warnings_handler.py`,
  `test_phase1_infeasible_message.py`, `test_phase3_fallback.py`,
  `test_horizon_stress.py`, `test_numerical_scale.py`,
  `test_gap_tolerance.py`, `test_preflight_branches.py`,
  `test_invariant_branches.py`, `test_stochastic.py`,
  `test_timeout.py`, `test_cli_help.py`, `test_integration.py`,
  `test_benchmarks.py`.
- `tests/test_property.py` — Hypothesis-based property tests over
  small random RCPSP instances (n_tasks ≤ 6, n_agents ≤ 3) asserting
  per-task assignment, precedence, κ caps, context budgets,
  file-mutex respect, and the critical-path lower bound.
- `tests/test_correctness_invariants.py` — invariants that span more
  than one module: horizon UB monotonicity, replan determinism under
  recalibration, status-reporting contracts, etc.

---

## Why this structure

The model / orchestration / result split keeps each layer responsible
for exactly one thing.

- `model/` is **pure**: it does not run CP-SAT. It owns the dataclasses
  (`Task`, `Agent`, `SolverConfig`, `Durations`), the
  `ModelBundle` data carrier, the input preparation pipeline
  (`_prepare_solve_inputs`), and the model construction
  (`build_model` and its private constraint-posting helpers). Any
  function here can be exercised in tests without invoking the solver.
- `orchestration/` **runs** CP-SAT. The runner module wraps the
  OR-Tools solver call and per-phase bookkeeping; `lex` and
  `cost_aware` are thin compositions on top of `_solve_phase1_makespan`,
  `_freeze_makespan_and_run_phase2`, and `_run_phase`. New objectives
  fit cleanly into the same pattern.
- `result/` reads `ModelBundle` plus a solved `cp_model.CpSolver` and
  builds the JSON envelope. It also hosts the small adapters that
  flow between solve and extract (`_apply_hints`, `_apply_fixed_constraints`,
  `_rehint_from`) because those operate on the same variable bundle.

The cleanly-cut layers also leave room for an alternate solver
backend — only `orchestration/` and `model.build` would need to learn
the new backend's API. That swap is not yet implemented.

---

## Public API guarantees

The supported library surface is whatever
`solver/__init__.py.__all__` lists:

```python
__all__ = [
    "Agent",
    "ScheduleInputError",
    "SolverConfig",
    "Task",
    "WARN_ANYTIME_TIMEOUT",
    "WARN_COST_SCALE_UNDERFLOW",
    "WARN_PARALLEL_WRITE_CONFLICT",
    "WARN_PHASE2_FALLBACK",
    "WARN_PHASE3_FALLBACK",
    "__version__",
    "parse_tasks_md",
    "replan",
    "solve_from_json",
    "solve_with_fixed",
]
```

Anything outside this list — including any symbol prefixed with `_`,
any helper inside `solver.model.build`, anything in
`solver.orchestration`, and the entire `solver.result` package — is
internal and may change without a semver bump. Importers reaching
into private helpers do so at their own risk; the package docstring
in `solver/__init__.py` says the same.

The result envelope is documented by the TypedDicts in
`solver/model/result_types.py` (`ScheduleResult`, `Stats`,
`Assignment`, `AgentSummary`, `WaveBlock`, `WarningRecord`). The
runtime annotation across the package is `dict[str, Any]` because
mypy's TypedDict variance rules block passing the literal envelopes
through without unsafe narrowing — treat the TypedDicts as the schema
reference.
