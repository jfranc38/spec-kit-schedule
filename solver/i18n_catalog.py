"""Internationalisation message catalog.

Keys are snake_case identifiers; each maps to a dict of {lang_code: template}.
Named placeholders use ``{name}`` syntax for :func:`str.format` interpolation.

Only user-facing messages belong here.  Developer-level log strings (info,
debug, internal warnings) stay in English at their call-sites.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "MESSAGES",
    "WARN_ANYTIME_TIMEOUT",
    "WARN_COST_SCALE_UNDERFLOW",
    "WARN_HEURISTIC_EDGE_DROPPED",
    "WARN_PARALLEL_WRITE_CONFLICT",
    "WARN_PHASE2_FALLBACK",
    "WARN_PHASE3_FALLBACK",
    "WARN_WORKERS_RAISED",
]

MESSAGES: dict[str, dict[str, str]] = {
    "duplicate_task_id": {
        "en": "Duplicate task id {task_id!r} at line {line}. Each task id must be unique.",
        "es": "ID de tarea duplicado {task_id!r} en línea {line}. Cada ID debe ser único.",
    },
    "unresolved_dep": {
        "en": "task {task_id} (line {line}) depends on unknown task {dep}",
        "es": "la tarea {task_id} (línea {line}) depende de una tarea desconocida {dep}",
    },
    "cycle_detected": {
        "en": (
            "Dependency cycle detected: {names}. "
            "Edge origins along the cycle: {origins}. "
            "Check explicit 'depends on' clauses and same-file write order."
        ),
        "es": (
            "Ciclo de dependencia detectado: {names}. "
            "Orígenes de aristas en el ciclo: {origins}. "
            "Revise las cláusulas 'depends on' explícitas y el orden de escritura de archivos."
        ),
    },
    "skill_uncovered": {
        "en": (
            "No agent provides the required skill(s). {details}. "
            "Add an agent with the missing skill, or edit skill_rules in "
            "schedule-config.yml to route those tasks to an existing agent."
        ),
        "es": (
            "Ningún agente proporciona la(s) habilidad(es) requerida(s). {details}. "
            "Agregue un agente con la habilidad faltante, o edite skill_rules en "
            "schedule-config.yml para enrutar esas tareas a un agente existente."
        ),
    },
    "budget_exceeded": {
        "en": (
            "Infeasible: sum(estimated_tokens)={total} exceeds "
            "sum(context_budget)={budget} across all agents. "
            "Increase context_budget, split the feature, or add agents."
        ),
        "es": (
            "Inviable: suma(estimated_tokens)={total} supera "
            "suma(context_budget)={budget} entre todos los agentes. "
            "Aumente context_budget, divida la funcionalidad o agregue agentes."
        ),
    },
    "skill_budget_exceeded": {
        "en": (
            "Infeasible: tasks requiring skill {skill!r} need "
            "{required} tokens total, but agents with that skill only "
            "expose {have} tokens combined."
        ),
        "es": (
            "Inviable: las tareas que requieren la habilidad {skill!r} necesitan "
            "{required} tokens en total, pero los agentes con esa habilidad solo "
            "exponen {have} tokens combinados."
        ),
    },
    "kappa_exceeded": {
        "en": (
            "Infeasible: {count} tasks require skill {skill!r} but "
            "total κ for agents with that skill is {kappa}. "
            "Increase κ or add agents."
        ),
        "es": (
            "Inviable: {count} tareas requieren la habilidad {skill!r} pero "
            "el κ total de los agentes con esa habilidad es {kappa}. "
            "Aumente κ o agregue agentes."
        ),
    },
    "heuristic_edge_dropped": {
        "en": (
            "Dropped the {origin} edge {src} → {dst} to break the dependency cycle "
            "{names}; the explicit 'depends on' order wins."
        ),
        "es": (
            "Se eliminó la arista {origin} {src} → {dst} para romper el ciclo de "
            "dependencias {names}; prevalece el orden explícito de 'depends on'."
        ),
    },
    "workers_raised": {
        "en": (
            "Raised workers from {requested} to {workers}: {n_tasks} tasks with "
            "max_tasks_per_worker={cap} need at least {workers} lanes."
        ),
        "es": (
            "Se aumentó workers de {requested} a {workers}: {n_tasks} tareas con "
            "max_tasks_per_worker={cap} requieren al menos {workers} carriles."
        ),
    },
    "parallel_write_conflict": {
        "en": (
            "Multiple [P] tasks write to {file!r}: {task_ids}. "
            "The [P] flag exempts tasks from file-mutex; verify they "
            "are truly idempotent or remove [P]."
        ),
        "es": (
            "Múltiples tareas [P] escriben en {file!r}: {task_ids}. "
            "La bandera [P] exime las tareas del mutex de archivo; verifique que "
            "sean verdaderamente idempotentes o elimine [P]."
        ),
    },
    "phase2_fallback": {
        "en": (
            "Phase 2 (load balancing) did not return a solution within "
            "the time limit. Returning the Phase 1 solution; load balance "
            "may be suboptimal. Increase solver.time_limit to improve it."
        ),
        "es": (
            "La Fase 2 (balanceo de carga) no devolvió una solución dentro del "
            "límite de tiempo. Se devuelve la solución de la Fase 1; el balance "
            "de carga puede ser subóptimo. Aumente solver.time_limit para mejorar."
        ),
    },
    "phase3_fallback": {
        "en": (
            "Phase 3 (load balancing under pinned cost, cost-aware mode) did "
            "not return a solution within the time limit. Returning the Phase 2 "
            "minimum-cost solution; load balance may be suboptimal. Increase "
            "solver.time_limit to improve it."
        ),
        "es": (
            "La Fase 3 (balanceo de carga con costo fijado, modo cost-aware) no "
            "devolvió una solución dentro del límite de tiempo. Se devuelve la "
            "solución de costo mínimo de la Fase 2; el balance de carga puede ser "
            "subóptimo. Aumente solver.time_limit para mejorar."
        ),
    },
    "cost_scale_underflow": {
        "en": (
            "Cost-aware scaling caused some task-cost coefficients to "
            "underflow to 0 after integer rounding. Affected (task, agent) "
            "pairs cannot be discriminated by the cost objective. Increase "
            "price granularity (price_per_1k_tokens), check token estimates, "
            "or raise the internal _COST_SCALE constant."
        ),
        "es": (
            "El escalado cost-aware provocó que algunos coeficientes de costo "
            "por tarea se redondearan a 0. Los pares (tarea, agente) afectados "
            "no pueden ser discriminados por el objetivo de costo. Aumente la "
            "granularidad del precio (price_per_1k_tokens), revise las "
            "estimaciones de tokens, o suba la constante interna _COST_SCALE."
        ),
    },
    "no_tasks_found": {
        "en": (
            "No tasks found in {path}. Verify the file uses the "
            "`- [ ] T### ...` format."
        ),
        "es": (
            "No se encontraron tareas en {path}. Verifique que el archivo use el "
            "formato `- [ ] T### ...`."
        ),
    },
    "empty_agents": {
        "en": (
            "No agents declared in config. Add at least one agent to the "
            "'agents:' list."
        ),
        "es": (
            "No se declararon agentes en la configuración. Agregue al menos un "
            "agente a la lista 'agents:'."
        ),
    },
    "task_no_skill": {
        "en": "Task {task_id}: no agent provides skill {skill!r}",
        "es": "Tarea {task_id}: ningún agente proporciona la habilidad {skill!r}",
    },
    "anytime_timeout": {
        "en": (
            "Solver reached the time limit before proving optimality. "
            "The best incumbent is returned; increase solver.time_limit for a provably optimal schedule."
        ),
        "es": (
            "El solver alcanzó el límite de tiempo antes de probar optimalidad. "
            "Se retorna el mejor incumbente; aumente solver.time_limit para un horario óptimo demostrado."
        ),
    },
    "replan_completed_unknown": {
        "en": (
            "--completed references task IDs not found in the current task list: {ids}. "
            "Remove the IDs from --completed or verify tasks.md is current."
        ),
        "es": (
            "--completed referencia IDs de tareas no encontrados en la lista actual: {ids}. "
            "Elimine los IDs de --completed o verifique que tasks.md esté actualizado."
        ),
    },
    "replan_fixed_missing": {
        "en": "A frozen prior assignment references a task or agent outside the current model.",
        "es": "Una asignación congelada previa referencia una tarea o agente fuera del modelo actual.",
    },
    "replan_fixed_missing_assignment": {
        "en": (
            "Frozen task {task_id!r} with agent {agent_id!r} is not present in the current model. "
            "Adjust --freeze-before or restore the task/agent in the current inputs."
        ),
        "es": (
            "La tarea congelada {task_id!r} con agente {agent_id!r} no existe en el modelo actual. "
            "Ajuste --freeze-before o restaure la tarea/agente en las entradas actuales."
        ),
    },
    "replan_fixed_incompatible": {
        "en": (
            "Frozen task {task_id!r} cannot be assigned to prior agent {agent_id!r} "
            "because the current config makes that assignment incompatible."
        ),
        "es": (
            "La tarea congelada {task_id!r} no puede asignarse al agente previo {agent_id!r} "
            "porque la configuración actual hace incompatible esa asignación."
        ),
    },
    "replan_fixed_invalid_duration": {
        "en": (
            "Frozen task {tid!r} has invalid duration={d}: "
            "duration must be a positive integer."
        ),
        "es": (
            "La tarea congelada {tid!r} tiene una duración inválida={d}: "
            "la duración debe ser un entero positivo."
        ),
    },
    "cannot_read_file": {
        "en": "Cannot read {file_kind} file{path_suffix}: {error}",
        "es": "No se puede leer el archivo {file_kind}{path_suffix}: {error}",
    },
    "unresolved_deps_summary": {
        "en": "Unresolved dependencies: {details}",
        "es": "Dependencias sin resolver: {details}",
    },
    "solver_input_cycle": {
        "en": "Dependency cycle in solver input: {names}",
        "es": "Ciclo de dependencia en la entrada del solver: {names}",
    },
    "schedule_file_no_heading": {
        "en": "No '# Schedule — <name>' heading found in {path}",
        "es": "No se encontró el encabezado '# Schedule — <nombre>' en {path}",
    },
    "schedule_file_no_metadata": {
        "en": (
            "No status metadata line found in {path}. "
            "Expected: '> Status: **X** | Makespan: **N** | Waves: **N** | Agents: **N**'"
        ),
        "es": (
            "No se encontró la línea de metadatos de estado en {path}. "
            "Se esperaba: '> Status: **X** | Makespan: **N** | Waves: **N** | Agents: **N**'"
        ),
    },
    "schedule_file_no_waves": {
        "en": "No wave sections found in {path}",
        "es": "No se encontraron secciones de wave en {path}",
    },
    "phase1_infeasible_proven": {
        "en": (
            "Phase 1 proved INFEASIBLE at horizon={horizon}. "
            "Preflight passed (capacity ≥ demand), so the model is overconstrained "
            "by precedence + κ + budget + file-mutex. Inspect the DAG and resource "
            "caps."
        ),
        "es": (
            "La Fase 1 demostró INFEASIBLE en horizon={horizon}. "
            "El preflight pasó (capacidad ≥ demanda), por lo que el modelo está "
            "sobrerrestringido por precedencia + κ + presupuesto + mutex de archivo. "
            "Inspeccione el DAG y los topes de recursos."
        ),
    },
    "phase1_infeasible_lb_exceeds_horizon": {
        "en": (
            "Phase 1 status={status}; solver lower bound on makespan "
            "({lb}) exceeds horizon ({horizon}). Increase "
            "horizon_multiplier or reduce token granularity."
        ),
        "es": (
            "Estado de Fase 1={status}; la cota inferior del solver sobre el makespan "
            "({lb}) supera el horizon ({horizon}). Aumente "
            "horizon_multiplier o reduzca la granularidad de tokens."
        ),
    },
    "phase1_infeasible_timeout": {
        "en": (
            "Phase 1 found no feasible schedule (status={status}, "
            "horizon={horizon}, lb≈{lb}). "
            "This typically means the time_limit was exhausted before proving "
            "feasibility; consider raising time_limit or num_workers."
        ),
        "es": (
            "La Fase 1 no encontró un horario factible (estado={status}, "
            "horizon={horizon}, lb≈{lb}). "
            "Esto suele significar que se agotó el time_limit antes de probar "
            "factibilidad; considere aumentar time_limit o num_workers."
        ),
    },
    # ── validation.py ─────────────────────────────────────────────────────────
    "validation_must_be_positive": {
        "en": "{name} must be > 0; got {value!r}",
        "es": "{name} debe ser > 0; se recibió {value!r}",
    },
    "validation_agent_config_errors": {
        "en": "{details}",
        "es": "{details}",
    },
    "validation_agent_config_generic": {
        "en": "agent config error: {error}",
        "es": "error de configuración del agente: {error}",
    },
    "validation_solver_config_errors": {
        "en": "{details}",
        "es": "{details}",
    },
    "validation_solver_config_generic": {
        "en": "solver config error: {error}",
        "es": "error de configuración del solver: {error}",
    },
    "validation_input_not_object": {
        "en": "Solver input must be a JSON object",
        "es": "La entrada del solver debe ser un objeto JSON",
    },
    "validation_input_missing_keys": {
        "en": "Solver input missing top-level keys: {missing}",
        "es": "Faltan claves de nivel superior en la entrada del solver: {missing}",
    },
    "validation_input_tasks_not_list": {
        "en": "Solver input 'tasks' must be a non-empty list",
        "es": "El campo 'tasks' de la entrada del solver debe ser una lista no vacía",
    },
    "validation_input_edges_not_list": {
        "en": "Solver input 'edges' must be a list",
        "es": "El campo 'edges' de la entrada del solver debe ser una lista",
    },
    "validation_input_agents_not_list": {
        "en": "Solver input 'agents' must be a non-empty list",
        "es": "El campo 'agents' de la entrada del solver debe ser una lista no vacía",
    },
    "validation_input_config_not_object": {
        "en": "Solver input 'config' must be an object",
        "es": "El campo 'config' de la entrada del solver debe ser un objeto",
    },
    "validation_task_missing_id": {
        "en": "Task missing 'id': {task}",
        "es": "Tarea sin 'id': {task}",
    },
    "validation_duplicate_task_id_input": {
        "en": "Duplicate task id in solver input: {task_id}",
        "es": "ID de tarea duplicado en la entrada del solver: {task_id}",
    },
    "validation_malformed_edge": {
        "en": "Malformed edge (expected [src_id, dst_id]): {edge}",
        "es": "Arista mal formada (se esperaba [src_id, dst_id]): {edge}",
    },
    "validation_edge_unknown_task": {
        "en": "Edge references unknown task id {task_id!r}",
        "es": "La arista referencia un id de tarea desconocido {task_id!r}",
    },
    # ── wave_executor.py ──────────────────────────────────────────────────────
    "wave_exec_no_tasks_in_wave": {
        "en": "Wave {index} (t={start_time}) in {path} has no parseable tasks",
        "es": "La wave {index} (t={start_time}) en {path} no tiene tareas analizables",
    },
    "wave_exec_wave_count_mismatch": {
        "en": "Metadata declares {declared} waves but {parsed} were parsed in {path}",
        "es": "Los metadatos declaran {declared} waves pero se analizaron {parsed} en {path}",
    },
    "wave_exec_no_agents": {
        "en": "No agent headings found in {path}",
        "es": "No se encontraron encabezados de agentes en {path}",
    },
    # ── unified CLI (python -m solver) ──────────────────────────────────
    "cli_config_not_found": {
        "en": "Config file not found: {path}",
        "es": "No se encontró el archivo de configuración: {path}",
    },
    "cli_config_not_mapping": {
        "en": "Config file {path} must contain a YAML mapping at the top level.",
        "es": "El archivo de configuración {path} debe contener un mapa YAML en el nivel superior.",
    },
    "cli_tasks_not_found": {
        "en": "tasks.md not found: {path}",
        "es": "No se encontró tasks.md: {path}",
    },
    "cli_plan_not_found": {
        "en": "schedule.json not found: {path}. Run `/speckit-schedule-run` first.",
        "es": "No se encontró schedule.json: {path}. Ejecuta primero `/speckit-schedule-run`.",
    },
    "cli_plan_invalid": {
        "en": "schedule.json at {path} is not a valid plan ({error}). Re-run `/speckit-schedule-run`.",
        "es": "schedule.json en {path} no es un plan válido ({error}). Vuelve a ejecutar `/speckit-schedule-run`.",
    },
    "cli_agents_override_workers": {
        "en": "schedule-config.yml declares agents; --workers / --max-tasks-per-worker are ignored.",
        "es": "schedule-config.yml declara agents; se ignoran --workers / --max-tasks-per-worker.",
    },
    "cli_written": {
        "en": "Written: {files}",
        "es": "Escrito: {files}",
    },
    "cli_tasks_changed": {
        "en": (
            "{path} changed since the plan was made (added: {added}; removed: {removed}). "
            "Re-run `/speckit-schedule-run` to plan again."
        ),
        "es": (
            "{path} cambió desde que se hizo el plan (añadidas: {added}; eliminadas: {removed}). "
            "Vuelve a ejecutar `/speckit-schedule-run` para planificar de nuevo."
        ),
    },
    "cli_all_done": {
        "en": "DONE — all {n} tasks are marked complete; nothing left to run.",
        "es": "DONE — las {n} tareas están marcadas como completas; no queda nada por ejecutar.",
    },
    "cli_blocked": {
        "en": (
            "BLOCKED — no lane can start: every pending task waits on another pending task. "
            "Check tasks.md for tasks that should already be marked [x]."
        ),
        "es": (
            "BLOCKED — ningún carril puede empezar: cada tarea pendiente espera a otra pendiente. "
            "Revisa en tasks.md las tareas que ya deberían estar marcadas [x]."
        ),
    },
    "cli_marked": {
        "en": "Marked {n} task(s) as {state} in {path}",
        "es": "Se marcaron {n} tarea(s) como {state} en {path}",
    },
    "mark_unknown_task": {
        "en": "Unknown task id(s) {ids} in {path}; nothing was changed.",
        "es": "ID(s) de tarea desconocido(s) {ids} en {path}; no se cambió nada.",
    },
    "wave_exec_unknown_agent": {
        "en": (
            "Task {task_id} references unknown agent {agent_id!r} not in agent "
            "assignments section"
        ),
        "es": (
            "La tarea {task_id} referencia un agente desconocido {agent_id!r} que no "
            "está en la sección de asignaciones de agentes"
        ),
    },
}

WARN_ANYTIME_TIMEOUT: Final = "anytime_timeout"
WARN_COST_SCALE_UNDERFLOW: Final = "cost_scale_underflow"
WARN_HEURISTIC_EDGE_DROPPED: Final = "heuristic_edge_dropped"
WARN_PARALLEL_WRITE_CONFLICT: Final = "parallel_write_conflict"
WARN_PHASE2_FALLBACK: Final = "phase2_fallback"
WARN_PHASE3_FALLBACK: Final = "phase3_fallback"
WARN_WORKERS_RAISED: Final = "workers_raised"
