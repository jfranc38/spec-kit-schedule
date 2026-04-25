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


def require_positive(value, name: str) -> None:
    if not isinstance(value, int | float) or value <= 0:
        raise ScheduleInputError(f"{name} must be > 0; got {value!r}")


def require_non_negative(value, name: str) -> None:
    if not isinstance(value, int | float) or value < 0:
        raise ScheduleInputError(f"{name} must be >= 0; got {value!r}")


KNOWN_PROVIDERS = frozenset(
    {
        "anthropic",
        "openai",
        "github",
        "google",
        "ollama",
        "azure",
        "bedrock",
        "groq",
        "mistral",
        "local",
        "custom",
    }
)


def validate_agent_config(agent: dict) -> None:
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
            raise ScheduleInputError("; ".join(parts)) from exc
        raise ScheduleInputError(f"agent config error: {exc}") from exc
    # `provider` is intentionally free-form: KNOWN_PROVIDERS is a discovery
    # hint for docs/tooling, not an allow-list, so bespoke runners work.


def validate_solver_config(cfg: dict) -> None:
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
            raise ScheduleInputError("; ".join(parts)) from exc
        raise ScheduleInputError(f"solver config error: {exc}") from exc


def validate_solver_input(data: dict) -> None:
    """Validate the JSON handed from parse_tasks to scheduler."""
    if not isinstance(data, dict):
        raise ScheduleInputError("Solver input must be a JSON object")
    required = {"tasks", "edges", "agents", "config"}
    missing = required - set(data.keys())
    if missing:
        raise ScheduleInputError(f"Solver input missing top-level keys: {sorted(missing)}")
    if not isinstance(data["tasks"], list) or not data["tasks"]:
        raise ScheduleInputError("Solver input 'tasks' must be a non-empty list")
    if not isinstance(data["edges"], list):
        raise ScheduleInputError("Solver input 'edges' must be a list")
    if not isinstance(data["agents"], list) or not data["agents"]:
        raise ScheduleInputError("Solver input 'agents' must be a non-empty list")
    if not isinstance(data["config"], dict):
        raise ScheduleInputError("Solver input 'config' must be an object")

    seen_ids: set[str] = set()
    for t in data["tasks"]:
        if "id" not in t:
            raise ScheduleInputError(f"Task missing 'id': {t}")
        if t["id"] in seen_ids:
            raise ScheduleInputError(f"Duplicate task id in solver input: {t['id']}")
        seen_ids.add(t["id"])
        require_positive(
            t.get("estimated_tokens", 1),
            f"task {t['id']!r}.estimated_tokens",
        )

    for a in data["agents"]:
        validate_agent_config(a)

    validate_solver_config(data["config"])

    ids = {t["id"] for t in data["tasks"]}
    for e in data["edges"]:
        if not (isinstance(e, list | tuple) and len(e) == 2):
            raise ScheduleInputError(f"Malformed edge (expected [src_id, dst_id]): {e}")
        src, dst = e
        if src not in ids:
            raise ScheduleInputError(f"Edge references unknown task id {src!r}")
        if dst not in ids:
            raise ScheduleInputError(f"Edge references unknown task id {dst!r}")
