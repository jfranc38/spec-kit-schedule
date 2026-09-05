# Architecture

A new-contributor's map of the `solver` package: the layout, the data
flow from `tasks.md` to executable rounds, the extension points, and
the test conventions. Cross-references point at the specific files and
functions so claims can be checked at HEAD with one `Read`.

---

## Package layout

```
solver/
├── __init__.py              # Public API surface (curated, see __all__)
├── __main__.py / cli.py     # `python -m solver plan|next|mark|status` (v0.7.0+)
├── _paths.py                # Encapsulated path constants + legacy-config migration
├── parse_tasks.py           # tasks.md → JSON envelope (spec-kit 0.16 format, bare paths)
├── tasks_md.py              # Checkbox scan / atomic `mark` (single writer of tasks.md)
├── workers.py               # Zero-config: N identical lanes, wildcard skill
├── scheduler.py             # solve, solve_from_json, solve_with_fixed, preflight, heuristic
├── model/                   # Pure model construction (no solver loops)
│   ├── types.py             # Task, Agent, SolverConfig, Durations
│   ├── fixed.py             # resolve_fixed_duration (replan)
│   ├── build.py             # build_model + ModelBundle, horizon, symmetry classes
│   └── result_types.py      # ScheduleResult / Stats / RoundBlock TypedDicts (schema doc)
├── orchestration/           # CP-SAT loops: runner, lex (2-phase), cost_aware (3-phase)
├── result/
│   ├── extract.py           # assignments, waves, critical path, rounds, agent summary
│   └── summary.py           # inline terminal summary
├── rounds.py                # Barrier rounds: next_round / build_rounds / barrier_makespan
├── briefs.py                # Subagent briefs + DONE/FAILED/TOUCHED report parser
├── render_schedule.py       # schedule.md (rounds, assignments, critical path, Gantt, DAG)
├── render_html.py           # Plotly HTML (viz extra)
├── visualize.py             # PNG Gantt/DAG (viz extra, `plan --images`)
├── replan.py                # Online re-optimisation with frozen assignments (CLI)
├── calibrate.py / run_log.py# Plan snapshots + speed/token calibration (CLI)
├── wave_executor.py         # schedule.md parser (json/table export)
├── status.py                # Installation self-diagnosis
├── validation.py            # Input checks, cycle detection, ScheduleInputError
├── warnings_collector.py    # Structured warnings
├── defaults.py              # Single source of default constants
└── i18n.py + i18n_catalog.py# EN + ES user-facing messages
```

## Encapsulated state (inside a spec-kit project)

```
<project_root>/
├── .specify/
│   ├── extensions/schedule/         # extension code (managed by `specify`)
│   │   ├── bin/speckit-schedule     #   wrapper: bootstrap venv → python -m solver
│   │   └── .venv/                   #   private Python environment
│   ├── schedule/
│   │   ├── schedule-config.yml      # OPTIONAL (workers / advanced portfolio)
│   │   └── runs/                    # plan snapshots for calibration
│   └── specs/<feature>/
│       ├── tasks.md                 # single source of progress ([x])
│       ├── schedule.md              # human report
│       └── schedule.json            # machine-readable plan (rounds, source hash)
```

`solver/_paths.py` exposes `project_root`, `extension_code_dir`,
`extension_state_dir`, `schedule_config_path`, `runs_dir`,
`encapsulated_venv_python`, `migrate_legacy_config`.

---

## Data flow

```
tasks.md ──► parse_tasks.parse_tasks_md ──► {tasks, edges, agents, config, warnings}
   (config: {} | workers | agents)  │            ▲
                                    │   workers.synthesize_workers (no agents:)
                                    ▼
                  scheduler.solve_from_json
                    ├─ _prepare_solve_inputs: cycle check, preflight (wildcard-aware),
                    │    compat, durations, file-conflict groups, warm-start heuristic
                    ├─ model.build.build_model → ModelBundle
                    ├─ orchestration.{lex | cost_aware | weighted}
                    └─ result.extract._finalize_result
                         ├─ assignments, waves, agent_summary
                         ├─ critical path over the realised graph (+ resource arcs)
                         └─ rounds.build_rounds → rounds, barrier_makespan, speedup
                                    │
        ┌───────────────────────────┼──────────────────────────┐
        ▼                           ▼                          ▼
 render_schedule (schedule.md)   cli.plan (schedule.json)   summary (stdout)
                                    │
                                    ▼
      cli.next: tasks_md.scan_checkboxes → rounds.next_round → briefs.render_round
                                    │
                                    ▼
      orchestrator (commands/implement.md): launch lanes → reports → cli.mark
```

Walk-through:

1. **Parse.** `parse_tasks_md(path, config)` accepts the spec-kit 0.16
   format: bare file paths (`extract_file_paths`), `(Priority: P1)`,
   `### Tests/Implementation for User Story N` sub-sections
   (`_PhaseTracker`). Edges: explicit `depends on`, same-file order and
   the two TDD rules (heuristic, droppable on cycles —
   `_break_heuristic_cycles`), then complete phase barriers
   (`_add_phase_barriers`, sinks → sources). With no `agents:` block,
   `DEFAULT_SKILL_RULES` apply and `synthesize_workers` builds N lanes
   with `skills: ["*"]`; zero-config solver defaults are anytime mode
   with a short per-phase limit.
2. **Solve.** Unchanged MS-RCPSP core (see `docs/formulation.md`);
   `workers.agent_covers` makes the wildcard skill first-class in
   preflight and compatibility.
3. **Rounds.** `rounds.next_round(queues, preds, done)` — per lane, the
   longest pending prefix whose predecessors are done or earlier in the
   prefix. `build_rounds` iterates it for the static plan;
   `barrier_makespan` and `speedup` land in `stats`.
4. **Render / emit.** `render_schedule` (Execution Rounds first),
   `summary.format_inline_summary`, `cli.plan` writes both artifacts with
   a `source` block (`tasks_md`, sha256, task ids).
5. **Execute.** `cli.next` validates the id set, reads checkboxes,
   recomputes the next round and prints `briefs.render_round`;
   `cli.mark` is the only writer of `tasks.md`. Protocol:
   [`docs/execution.md`](execution.md).

---

## Extension points

- **New objective.** Add an orchestration function mirroring
  `orchestration/lex.py`, register an `OBJECTIVE_*` constant in
  `defaults.py`, dispatch in `scheduler.solve`.
- **New constraint.** `_add_*_constraints` in `model/build.py`, called
  from `build_model`; keep `list_schedule_heuristic` consistent so hints
  stay feasible.
- **New parser rule.** `parse_tasks.py`: edges with a new `EdgeOrigin`;
  decide whether it is heuristic (droppable) or authoritative.
- **New renderer.** Consume `ScheduleResult` (`model/result_types.py`).
- **New CLI verb.** `cli.py`: add a sub-parser + `cmd_*`; keep it a thin
  wrapper over a pure module so it is testable in-process.
- **New message.** `i18n_catalog.MESSAGES` with `en` **and** `es`.
- **New default.** `defaults.py` only.

---

## Test layout

- `tests/_helpers.py` — JSON builders (`make_task`, `make_agent`,
  `make_solver_input`, chain builders).
- `tests/fixtures/tasks-speckit-0.16.md` — a `tasks.md` in the exact
  format `/speckit.tasks` generates; used by the parser, workers,
  rounds, briefs, render and CLI tests.
- Per-feature files: `test_parse_tasks.py`, `test_parse_speckit_format.py`,
  `test_workers.py`, `test_rounds.py` (property-based with hypothesis),
  `test_briefs.py`, `test_render_rounds.py`, `test_cli.py` (in-process
  `cli.main`), plus the solver suites (`test_scheduler.py`,
  `test_replan.py`, `test_property.py`, `test_correctness_invariants.py`, …).
- Gate (CI): `ruff`, `mypy --strict` on `scheduler.py`/`parse_tasks.py`,
  `mypy solver`, `pytest --cov-fail-under=90`, `make smoke`.

---

## Public API

`solver/__init__.py.__all__` lists the supported library surface
(`Task`, `Agent`, `SolverConfig`, `ScheduleInputError`, `parse_tasks_md`,
`replan`, `solve_from_json`, `solve_with_fixed`, `WARN_*`). Everything
else — including `rounds`, `briefs`, `cli` — is internal and may change
between minor versions; the stable contracts are the CLI verbs and the
`schedule.json` keys documented in [`docs/execution.md`](execution.md).
