"""Internationalisation message catalog.

Keys are snake_case identifiers; each maps to a dict of {lang_code: template}.
Named placeholders use ``{name}`` syntax for :func:`str.format` interpolation.

Only user-facing messages belong here.  Developer-level log strings (info,
debug, internal warnings) stay in English at their call-sites.
"""

from __future__ import annotations

__all__ = ["MESSAGES"]

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
    "missing_key_fallback": {
        "en": "i18n key {key!r} missing for lang={lang!r}; falling back to key string.",
        "es": "i18n key {key!r} missing for lang={lang!r}; falling back to key string.",
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
    "not_a_directory": {
        "en": "project_dir is not a directory: {path}",
        "es": "project_dir no es un directorio: {path}",
    },
    "output_exists_use_force": {
        "en": "{path} already exists. Use --force to overwrite.",
        "es": "{path} ya existe. Use --force para sobrescribir.",
    },
    "autodetect_invalid_config": {
        "en": "autodetect produced an invalid config (bug): {error}",
        "es": "autodetect produjo una configuración inválida (bug): {error}",
    },
    "interactive_invalid_config": {
        "en": "Interactive edits produced an invalid config: {error}",
        "es": "Las ediciones interactivas produjeron una configuración inválida: {error}",
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
}
