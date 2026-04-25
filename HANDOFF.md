# Handoff — spec-kit-schedule v0.5.0 (in-flight)

**Fecha del handoff**: 2026-04-24
**Baseline al partir**: v0.4.1 (76 tests, 0 pending).
**Estado al entregar**: 357 tests passing, lint clean, E2E smoke OPTIMAL. Wave 1 + Wave 2-D completadas; Waves 2-B / 3 / 4 pendientes.

> Plan maestro: `/Users/jcfranco/.claude/plans/act-a-como-principal-software-cuddly-storm.md`.
> No lo sobrescribas. El plan contiene el contrato exacto de cada agente.

---

## TL;DR para el siguiente agente

1. `make install && make test` debe dejarte con **357 passed**. Si rompe, `make smoke` diagnostica.
2. Faltan 3 waves del plan + 5 archivos OSS estáticos.
3. Los archivos OSS estáticos son puro contenido (no requieren solver ni subagente): hazlos tú directamente.
4. Waves 2-B, 3-C2, 4-G son subagentes Sonnet 4.6 con prompts ya listos en el plan maestro; dispaches con `Agent(model="sonnet", run_in_background=true, …)` citando las secciones del plan.
5. **Punto de fricción conocido**: Wave 2-B y Wave 3-C2 ambos tocan `solver/scheduler.py`. El plan exige que B merge antes de lanzar C2.

---

## Inventario por archivo (qué existe hoy)

### Código solver (Wave 1 + Wave 2-D ya aplicado)

| Path | Estado | Observaciones |
|---|---|---|
| `solver/__init__.py` | Bump a `0.5.0` | |
| `solver/config_schema.py` | ✅ Nuevo (Agent A) | Pydantic v2 models: `AgentConfig`, `SolverOptions`, `TokenEstimate`, `SkillRule`, `Config`, `load_config()`. 96% cov. |
| `solver/validation.py` | ✅ Refactored (A) | `validate_agent_config` y `validate_solver_config` ahora son wrappers sobre pydantic; `ScheduleInputError` con field-path. |
| `solver/parse_tasks.py` | ✅ Refactored (A) | `_merge_config` usa `Config.model_validate()`. |
| `solver/scheduler.py` | ✅ Extended (A + F) | (A) `Agent.price_per_1k_tokens`, `SolverConfig.{cost_weight,stochastic_quantile,anytime}`. (F) `build_model` splitted en `_build_variables` (538), `_add_precedence_constraints` (585), `_add_resource_constraints` (595), `_add_objectives` (622), con `build_model` (663) orquestando. **Warning**: firmas extendidas con kwargs — no rompen callers existentes, pero Wave 2-B y 3-C2 deben leer antes de tocar. |
| `solver/warnings_collector.py` | ✅ Refactored (F) | Ahora `class WarningCollector(logging.Handler)` con `emit(record)` + API legacy `.add(code, message, **ctx)`. |
| `solver/wave_executor.py` | ✅ Nuevo (C1) | `parse_schedule_md() → ExecutionPlan`; CLI `--format json|shell|table`. 96% cov, 53 tests. |
| `solver/render_html.py` | ✅ Nuevo (E) | Plotly self-contained HTML (CDN default, `--inline-plotly` ≈4 MB offline). 20 tests. |
| `solver/autodetect.py` | ✅ Nuevo (D) | `detect_portfolio(project_dir)` + CLI. 91% cov, 40 tests. |
| `solver/calibrate.py` | ✅ Nuevo (D) | `calibrate(runs.jsonl, config.yml) → CalibrationReport` + CLI. 95% cov, 33 tests. |
| `solver/i18n.py` | ✅ Nuevo (F) | `t(key, lang="en", **kwargs)` con fallback en → es. |
| `solver/i18n_catalog.py` | ✅ Nuevo (F) | `MESSAGES: dict[str, dict[str, str]]` con claves en/es. |
| `solver/defaults.py`, `solver/render_schedule.py`, `solver/visualize.py` | sin cambio | heredados de v0.4.1. |

### Benchmarks (Wave 1-E)

| Path | Estado |
|---|---|
| `benchmarks/__init__.py`, `problems.py`, `greedy_baseline.py`, `run.py`, `README.md`, `.gitignore` | ✅ Todos nuevos. |
| `benchmarks/results/` | gitignored. Ejecuta `make bench` para poblar `latest.md`. |

### Tests

| Path | Tests | Dueño |
|---|---|---|
| `tests/test_config_schema.py` | 54 | A |
| `tests/test_wave_executor.py` | 53 | C1 |
| `tests/test_render_html.py` | 20 | E |
| `tests/test_benchmarks.py` | 19 | E |
| `tests/test_autodetect.py` | 40 | D |
| `tests/test_calibrate.py` | 33 | D |
| `tests/test_i18n.py` | ~10 | F |
| `tests/test_warnings_handler.py` | ~8 | F |
| Heredados v0.4.1 | 76 | — |
| **TOTAL** | **357 passing** | |

### Config / docs / scaffolding

| Path | Estado |
|---|---|
| `pyproject.toml` | ✅ v0.5.0, metadata PyPI completa (keywords, urls, classifiers), mypy strict con overrides surgical por módulo, `pydantic>=2`, `plotly>=5` en viz, `pre-commit>=3.6` en dev. |
| `uv.lock` | ✅ Regenerado. |
| `config-template.yml` | ✅ Documenta `price_per_1k_tokens`, `token_estimates` dict form, `cost_weight`, `stochastic_quantile`, `anytime`, y menciona `solver.autodetect`. |
| `docs/example-config.yml`, `docs/example-config-mixed.yml` | ✅ `price_per_1k_tokens` en todos los agentes; un `token_estimates` entry en dict form. |
| `docs/calibration.md` | ✅ Nuevo (D). Guía de calibración + schema de `runs.jsonl`. |
| `docs/formulation.md` | ✅ Sección "Empirical Benchmarks" añadida (E). |
| `commands/schedule.md` | ✅ Sección "Executing Waves via /speckit.implement" añadida (C1). |
| `commands/implement_bridge.md` | ✅ Nuevo (C1). |
| `commands/portfolio.md`, `commands/visualize.md` | sin cambio (heredados v0.4.1). |
| `Makefile` | ✅ Targets `bench`, `bench-report` añadidos (E). |
| `CONTRIBUTING.md` | ✅ Nuevo (F). |
| `CHANGELOG.md` | ⏳ Hoy refleja v0.4.1. Agent G debe añadir entrada v0.5.0. |
| `README.md`, `INSTALL.md` | ⏳ Sin actualizar para v0.5.0. Agent G. |
| `extension.yml` | ⏳ Bump a 0.5.0 pendiente. Agent G. |
| `templates/schedule-template.md` | ⏳ Sin actualizar. Agent G. |

---

## Lo que falta (en orden de ejecución)

### 1. Archivos OSS estáticos — finalizar Wave 1-F (puro contenido, ~15 min)

El subagente F cayó en content-filter al emitir el summary final después de 118 tool calls. Los cambios de código landearon bien, pero 5 archivos OSS estáticos NO fueron creados. Hazlos tú directamente (no requiere subagente):

- [ ] `CODE_OF_CONDUCT.md` — Contributor Covenant 2.1 verbatim (copia-pega del sitio oficial; cambiar solo el email de contacto — usar placeholder `conduct@spec-kit-schedule.dev` si no hay uno real).
- [ ] `.pre-commit-config.yaml` — hooks: `ruff-pre-commit` (check + format), `pre-commit-hooks` (`end-of-file-fixer`, `trailing-whitespace`, `check-added-large-files`, `check-yaml`, `check-toml`). Versiones estables conocidas.
- [ ] `.github/ISSUE_TEMPLATE/bug_report.md` — con front-matter YAML (`name`, `about`, `labels`).
- [ ] `.github/ISSUE_TEMPLATE/feature_request.md` — idem.
- [ ] `.github/ISSUE_TEMPLATE/config.yml` — `blank_issues_enabled: false` + link a Discussions.
- [ ] `.github/pull_request_template.md` — checklist: tests, lint, CHANGELOG, docs updated.
- [ ] `.github/workflows/release.yml` — en tag `v*.*.*`, `uv build` + `uv publish` con OIDC (`permissions: id-token: write`). No requiere API token si PyPI Trusted Publisher está configurado; si no, el workflow debe documentarlo.

Verificación post: `pre-commit run --all-files` clean; `uv build` dry `ls dist/` muestra sdist + wheel.

### 2. Wave 2-B — Solver Enhancements (subagente Sonnet 4.6, ~8-12 min wall-clock)

Prompt exacto: ver plan maestro sección "Agent B — Solver Enhancements".
Owns: `solver/scheduler.py` (cost_aware, anytime callback, stochastic), `solver/defaults.py`.
Nuevos tests: `tests/test_cost_objective.py`, `tests/test_anytime.py`, `tests/test_stochastic.py`.

Dispatch:

```python
Agent(
    description="Wave2-B solver enhancements",
    subagent_type="general-purpose",
    model="sonnet",
    name="wave2-b-solver",
    run_in_background=True,
    prompt="... (copiar contrato de la sección Agent B del plan, añadir header con baseline actual: 357 tests passing, solver.config_schema y solver.warnings_collector ya consolidados)",
)
```

**Punto crítico**: B debe reutilizar los kwargs ya presentes en `SolverConfig` (`cost_weight`, `stochastic_quantile`, `anytime`) introducidos por A, y usar los helpers refactorizados de F (`_build_variables`, `_add_precedence_constraints`, etc.). NO reintroducir `build_model` monolítico.

### 3. Wave 3-C2 — Incremental Replanning (subagente, ~6-10 min)

**NO lanzar hasta que B haya mergeado**. Ambos tocan `solver/scheduler.py`.

Prompt: sección "Agent C2 — Incremental Replanning CLI" del plan.
Owns: `solver/replan.py` nuevo + extender `solver/scheduler.py` con `solve_with_fixed(...)`.
Tests: `tests/test_replan.py`.

Debe usar el anytime callback que B introduce en `_run_solver`.

### 4. Wave 4-G — Integration (subagente o manual, ~20-30 min)

Prompt: sección "Agent G — Docs, Version, Verify" del plan.

Tareas:
- Actualizar `README.md`, `INSTALL.md`, `docs/formulation.md`, `commands/*.md`, `templates/schedule-template.md`.
- Bump versiones en `pyproject.toml` (ya está en 0.5.0), `solver/__init__.py` (ya está), `extension.yml` (⏳ está en 0.4.1).
- Añadir target `make schedule-all` (md + png + html).
- Regenerar `docs/example-schedule.md` + `docs/images/*` + `docs/example-schedule.html`.
- Añadir entrada v0.5.0 en `CHANGELOG.md`.
- Ejecutar el gauntlet final (ver plan, sección "Verificación gauntlet final"):
  1. `make lint` clean.
  2. `uv run mypy --strict solver` (los overrides surgicales en `pyproject.toml` permiten pasar; G debe documentar cada override si aún existen).
  3. `make test` ≥357 passing + lo que añadan B y C2 (~+20).
  4. `make smoke` y `make schedule-all` OPTIMAL con imágenes y HTML.
  5. `make bench` tabla reproducible.
  6. Zip teammate flow.
  7. `pre-commit run --all-files` clean.
  8. `uv build && uv publish --dry-run` OK.
  9. Ejemplo cost-aware con `total_cost` en stats.
  10. Replanning E2E.

---

## Open questions / riesgos conocidos

Recopilados de los reportes de subagentes:

1. **Wave 2-B debe respetar split de F en `build_model`**. Las nuevas funciones (`_build_variables`, `_add_*`) viven entre líneas ~538-662 de `scheduler.py`. B añade su lógica a `_add_objectives` (cost_aware) y posiblemente crea `_run_solver_anytime` siblings.
2. **`runs.jsonl` producer**: D dejó documentado el schema en `docs/calibration.md`, pero NO hay instrumentación que emita esas líneas durante la ejecución. Wave 3-C2 (replanning) podría añadir un flag `--log-runs runs.jsonl`, o dejarlo para una versión posterior.
3. **Angular detection en autodetect** (D): `@angular/core` cae al default branch de React. Follow-up trivial: añadir branch explícito.
4. **Interactive kappa validation en autodetect** (D): `int()` sobre input no válido lanza `ValueError` crudo en vez de `ScheduleInputError`. Defensivo menor.
5. **mypy overrides**: F añadió overrides surgicales por cada módulo existente (`pyproject.toml` líneas 96-112). G debe revisar y, si el tiempo lo permite, migrar cada módulo a strict real (o documentar en CHANGELOG que los overrides son temporales).
6. **`benchmarks/results/latest.md` aún no generado**. G debe ejecutar `make bench` y commitear el resultado para que README pueda linkarlo.
7. **Content-filter block de F**: el summary del subagente fue bloqueado pero los edits landearon. Si vuelve a pasar con otro subagente, verificar con `ls` + `git diff` (si hubiera git) o `pytest` qué se consolidó.

---

## Comandos rápidos para el siguiente agente

```bash
# Setup limpio
cd /Users/jcfranco/Downloads/spec-kit-schedule
make clean && ./bin/install.sh

# Verify baseline
make test          # debe ser 357 passed
make lint          # clean
make smoke         # OPTIMAL

# Cuando hagas tu wave, al final
make test && make lint && make smoke

# E2E de feature nueva (cost-aware — tras Wave 2-B)
uv run python -m solver.parse_tasks docs/example-tasks.md docs/example-config-mixed.yml > /tmp/in.json
uv run python -m solver.scheduler < /tmp/in.json > /tmp/out.json
python3 -c "import json; d=json.load(open('/tmp/out.json')); print('cost:', d['stats'].get('total_cost'))"

# Benchmarks (tras Wave 1-E, ya mergeado)
make bench         # puebla benchmarks/results/latest.md
make bench-report  # imprime la tabla

# Autodetect (tras Wave 2-D, ya mergeado)
uv run python -m solver.autodetect --project-dir . --dry-run

# Wave executor (tras Wave 1-C1, ya mergeado)
uv run python -m solver.wave_executor docs/example-schedule.md --format table
```

---

## Convenciones compartidas (recordatorio)

El plan las detalla; resumen operativo:

- **Raise**: `solver.validation.ScheduleInputError` con mensaje accionable. Nunca `SystemExit` en library; `main()` puede `sys.exit(2)` tras stderr.
- **Warnings**: `solver.warnings_collector.WarningCollector` (ahora `logging.Handler` subclass). API legacy `.add()` sigue funcionando.
- **Defaults**: `solver/defaults.py` es fuente única. Cero magic numbers dispersos.
- **Grafos**: usar `networkx` vía `_build_node_weighted_graph`, `_precedence_graph` en `solver/scheduler.py`.
- **Edges del output**: siempre tres colecciones (`edges`, `resource_edges`, `critical_path_edges`). Nunca inventar un cuarto.
- **i18n**: solo para mensajes user-facing (`ScheduleInputError`, `WarningCollector.add`). Logs internos en inglés.
- **Tests**: `pytest.importorskip` para deps opcionales (`matplotlib`, `plotly`).
- **Zero tolerance**: sin narrative comments, sin TODO, sin dead code, sin strings como enums.

---

## Reporte final cuando termines

Al completar todas las waves + gauntlet:

1. Tag `v0.5.0` y push.
2. Actualiza este HANDOFF.md con "Cerrado — v0.5.0 released YYYY-MM-DD".
3. Archiva plan maestro en `/Users/jcfranco/.claude/plans/archived/v0.5.0-<fecha>.md`.
