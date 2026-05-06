"""Input validation and graph sanity checks.

The parser and the solver both receive user-controlled data. When that data
is wrong, we want a single, actionable error message instead of a stack
trace or a silently-degraded schedule. This module centralises the checks
so parse_tasks and scheduler speak the same language.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable

import networkx as nx

from .i18n import t


class ScheduleInputError(ValueError):
    """Raised when user input is semantically invalid."""


def normalize_path(path: str) -> str:
    """Canonicalise a file path so equal paths compare equal.

    Strips leading `./`, collapses `..`, and uses POSIX separators so that
    `./src/a.py`, `src/a.py`, and `src/./a.py` all hash the same key in
    file-mutex groups.
    """
    if not path:
        return path
    # Collapse `.` and `..` segments, normalise separators, and drop
    # leading `./` while preserving relative form.
    return posixpath.normpath(path.replace("\\", "/"))


def find_cycle(n: int, edges: Iterable[tuple[int, int]]) -> list[int] | None:
    """Return one cycle as a list of node indices, or None if acyclic.

    The returned list starts and ends with the same node so callers can
    print it unambiguously.
    """
    graph = nx.DiGraph()
    graph.add_nodes_from(range(n))
    graph.add_edges_from(edges)
    try:
        cycle_edges = nx.find_cycle(graph, orientation="original")
    except nx.NetworkXNoCycle:
        return None
    nodes = [u for u, _, *_ in cycle_edges]
    nodes.append(nodes[0])
    return nodes


def require_positive(value: object, name: str) -> None:
    if not isinstance(value, int | float) or value <= 0:
        raise ScheduleInputError(t("validation_must_be_positive", name=name, value=value))


def validate_agent_config(agent: dict[str, object]) -> None:
    """Validate a single agent block via pydantic AgentConfig.

    Thin wrapper that converts pydantic ValidationError into ScheduleInputError
    with a field-path prefix so callers receive an actionable message.
    """
    from .config_schema import AgentConfig  # local import avoids circular dep at module load

    try:
        AgentConfig.model_validate(agent)
    except Exception as exc:  # pydantic.ValidationError
        errors = getattr(exc, "errors", None)
        if errors is not None:
            parts = []
            for err in errors():
                loc = ".".join(str(s) for s in err["loc"]) if err.get("loc") else "?"
                msg = err.get("msg", str(err))
                agent_id = agent.get("id", "<unknown>")
                parts.append(f"agent {agent_id!r}.{loc}: {msg}")
            raise ScheduleInputError(
                t("validation_agent_config_errors", details="; ".join(parts))
            ) from exc
        raise ScheduleInputError(
            t("validation_agent_config_generic", error=exc)
        ) from exc
    # `provider` is intentionally free-form so bespoke runners work; pydantic
    # only enforces the surrounding shape, not the provider value itself.


def validate_solver_config(cfg: dict[str, object]) -> None:
    """Validate solver options block via pydantic SolverOptions.

    Thin wrapper that converts pydantic ValidationError into ScheduleInputError.
    """
    from .config_schema import SolverOptions  # local import avoids circular dep at module load

    try:
        SolverOptions.model_validate(cfg)
    except Exception as exc:  # pydantic.ValidationError
        errors = getattr(exc, "errors", None)
        if errors is not None:
            parts = []
            for err in errors():
                loc = ".".join(str(s) for s in err["loc"]) if err.get("loc") else "?"
                msg = err.get("msg", str(err))
                parts.append(f"solver.{loc}: {msg}")
            raise ScheduleInputError(
                t("validation_solver_config_errors", details="; ".join(parts))
            ) from exc
        raise ScheduleInputError(
            t("validation_solver_config_generic", error=exc)
        ) from exc


def validate_solver_input(data: dict[str, object]) -> None:
    """Validate the JSON handed from parse_tasks to scheduler."""
    if not isinstance(data, dict):
        raise ScheduleInputError(t("validation_input_not_object"))
    required = {"tasks", "edges", "agents", "config"}
    missing = required - set(data.keys())
    if missing:
        raise ScheduleInputError(
            t("validation_input_missing_keys", missing=sorted(missing))
        )
    if not isinstance(data["tasks"], list) or not data["tasks"]:
        raise ScheduleInputError(t("validation_input_tasks_not_list"))
    if not isinstance(data["edges"], list):
        raise ScheduleInputError(t("validation_input_edges_not_list"))
    if not isinstance(data["agents"], list) or not data["agents"]:
        raise ScheduleInputError(t("validation_input_agents_not_list"))
    if not isinstance(data["config"], dict):
        raise ScheduleInputError(t("validation_input_config_not_object"))

    seen_ids: set[str] = set()
    for task in data["tasks"]:
        if "id" not in task:
            raise ScheduleInputError(t("validation_task_missing_id", task=task))
        if task["id"] in seen_ids:
            raise ScheduleInputError(
                t("validation_duplicate_task_id_input", task_id=task["id"])
            )
        seen_ids.add(task["id"])
        require_positive(
            task.get("estimated_tokens", 1),
            f"task {task['id']!r}.estimated_tokens",
        )

    for a in data["agents"]:
        validate_agent_config(a)

    validate_solver_config(data["config"])

    ids = {task["id"] for task in data["tasks"]}
    for e in data["edges"]:
        if not (isinstance(e, list | tuple) and len(e) == 2):
            raise ScheduleInputError(t("validation_malformed_edge", edge=e))
        src, dst = e
        if src not in ids:
            raise ScheduleInputError(t("validation_edge_unknown_task", task_id=src))
        if dst not in ids:
            raise ScheduleInputError(t("validation_edge_unknown_task", task_id=dst))
