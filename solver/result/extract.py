"""Post-solve extraction: turn CP-SAT solver output into the result envelope.

This module also hosts the small "schedule-graph" helpers used to compute the
critical path over the realised schedule, plus the warm-start hint / fixed-
constraint / re-hint helpers that operate on the variable bundle. Those last
three are solve-side adapters but are tightly coupled to the result-shaping
flow (they are called immediately before / after extraction), so they live
here for cohesion.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import networkx as nx
from ortools.sat.python import cp_model

from ..defaults import STATUS_FEASIBLE, STATUS_OPTIMAL, STATUS_UNKNOWN
from ..model.types import Agent, Durations, Task

if TYPE_CHECKING:
    from ..model.build import ModelBundle

log = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────
# Graph helpers (networkx-backed)
# ───────────────────────────────────────────────────────────────────────


def _build_node_weighted_graph(
    nodes: range | list[int],
    edges: list[tuple[int, int]],
    weight_of: Mapping[int, int],
    **node_attrs_by_id: Mapping[int, object],
) -> nx.DiGraph:
    """Shared factory for node-weighted DiGraphs.

    Extra attribute maps (start, end, agent_id, …) are applied in one
    pass so callers don't iterate the node set twice.
    """
    graph = nx.DiGraph()
    for i in nodes:
        attrs: dict[str, object] = {"weight": weight_of.get(i, 0)}
        for attr_name, mapping in node_attrs_by_id.items():
            if i in mapping:
                attrs[attr_name] = mapping[i]
        graph.add_node(i, **attrs)
    graph.add_edges_from(edges)
    return graph


def _node_weighted_longest_path_length(graph: nx.DiGraph) -> int:
    """Node-weighted longest-path length over a DAG.

    `networkx.dag_longest_path_length` operates on edge weights, so we do
    the DP ourselves using the library's topological_sort — cleaner than
    hand-rolling Kahn's algorithm and still O(V + E).
    """
    dist: dict[int, int] = {}
    best = 0
    for u in nx.topological_sort(graph):
        w = graph.nodes[u].get("weight", 0)
        pred_best = max((dist[p] for p in graph.predecessors(u)), default=0)
        dist[u] = pred_best + w
        if dist[u] > best:
            best = dist[u]
    return best


# ───────────────────────────────────────────────────────────────────────
# Solve-side adapters (hint / fix / re-hint)
# ───────────────────────────────────────────────────────────────────────


def _apply_fixed_constraints(
    bundle: ModelBundle,
    fixed_constraints: dict[int, tuple[int, int, int]],
    compat: dict[int, list[int]],
) -> None:
    """Pin agent, start, and duration for each task in fixed_constraints.

    Pinning ``dur`` (in addition to ``start`` and the ``x[i,a]`` channel) is
    essential for replan determinism: if ``speed_factor`` or ``token_unit``
    changes between the original solve and the replan (e.g. recalibration),
    the duration channelled from ``p[i,a]`` would silently shift, making the
    "frozen" task land somewhere different than reported.
    """
    for i, (a_fixed, s_fixed, d_fixed) in fixed_constraints.items():
        bundle.model.add(bundle.start[i] == s_fixed)
        bundle.model.add(bundle.dur[i] == d_fixed)
        for a in compat.get(i, []):
            if (i, a) in bundle.x:
                bundle.model.add(bundle.x[(i, a)] == (1 if a == a_fixed else 0))


def _apply_hints(
    bundle: ModelBundle,
    hints: dict[int, tuple[int, int]],
    compat: dict[int, list[int]],
    *,
    p: Durations | None = None,
    n_agents: int | None = None,
) -> None:
    """Seed the warm-start with the heuristic's per-task placements.

    Hints every variable derivable from the heuristic schedule:
    ``x[i,a]``, ``start[i]``, ``dur[i]``, ``end[i]``, plus aggregate
    ``load[a]``, ``max_load``, and ``makespan`` when ``p`` and ``n_agents``
    are supplied. Richer hints give CP-SAT a feasible incumbent
    immediately, which the documentation calls out as the highest-leverage
    warm-start hook.
    """
    have_aggregates = p is not None and n_agents is not None
    load_hint: dict[int, int] | None = (
        dict.fromkeys(range(n_agents), 0)
        if (have_aggregates and n_agents is not None)
        else None
    )
    end_max = 0
    for i, (a_hint, s_hint) in hints.items():
        if (i, a_hint) not in bundle.x:
            continue
        bundle.model.add_hint(bundle.x[(i, a_hint)], 1)
        bundle.model.add_hint(bundle.start[i], s_hint)
        for a in compat[i]:
            if a != a_hint and (i, a) in bundle.x:
                bundle.model.add_hint(bundle.x[(i, a)], 0)
        if have_aggregates:
            assert p is not None and load_hint is not None  # for mypy
            d_hint = p[(i, a_hint)]
            bundle.model.add_hint(bundle.dur[i], d_hint)
            bundle.model.add_hint(bundle.end[i], s_hint + d_hint)
            load_hint[a_hint] = load_hint.get(a_hint, 0) + d_hint
            end_max = max(end_max, s_hint + d_hint)
    if have_aggregates and load_hint is not None:
        for a, l_hint in load_hint.items():
            if a in bundle.load:
                bundle.model.add_hint(bundle.load[a], l_hint)
        bundle.model.add_hint(bundle.max_load, max(load_hint.values(), default=0))
        bundle.model.add_hint(bundle.makespan, end_max)


def _rehint_from(
    bundle: ModelBundle,
    solver: cp_model.CpSolver,
    tasks: list[Task],
    compat: dict[int, list[int]],
) -> None:
    """Seed the next phase with variable values from a previous solver.

    Hints every variable available in the bundle so the next phase can
    start from a fully consistent incumbent. ``total_cost`` is hinted only
    when the cost-aware variable is present.
    """
    # Route through the wrapper so the ortools-stub `# type: ignore` lives
    # in exactly one place (solver.model.build).
    from ..model.build import _clear_hints

    _clear_hints(bundle.model)
    for i in range(len(tasks)):
        bundle.model.add_hint(bundle.start[i], solver.value(bundle.start[i]))
        bundle.model.add_hint(bundle.end[i], solver.value(bundle.end[i]))
        bundle.model.add_hint(bundle.dur[i], solver.value(bundle.dur[i]))
        for a in compat[i]:
            if (i, a) in bundle.x:
                bundle.model.add_hint(bundle.x[(i, a)], solver.value(bundle.x[(i, a)]))
    for load_var in bundle.load.values():
        bundle.model.add_hint(load_var, solver.value(load_var))
    bundle.model.add_hint(bundle.max_load, solver.value(bundle.max_load))
    bundle.model.add_hint(bundle.makespan, solver.value(bundle.makespan))
    if bundle.total_cost is not None:
        bundle.model.add_hint(bundle.total_cost, solver.value(bundle.total_cost))


# ───────────────────────────────────────────────────────────────────────
# Solution extraction
# ───────────────────────────────────────────────────────────────────────


def _extract_assignments(
    solver: cp_model.CpSolver,
    bundle: ModelBundle,
    tasks: list[Task],
    agents: list[Agent],
    compat: dict[int, list[int]],
) -> list[dict[str, Any]]:
    # Materialise every value once: an inner ``solver.value(...)`` call goes
    # through pybind11, so the per-task linear scan over ``compat[i]`` looking
    # for the truthy ``x[(i,a)]`` was paying that cost up to ``len(compat[i])``
    # times per task. The dict comprehension here is one round trip per
    # assignment variable, which is the minimum CP-SAT supports.
    x_vals: dict[tuple[int, int], int] = {
        key: solver.value(lit) for key, lit in bundle.x.items()
    }
    # Collapsed three per-index dict comprehensions into one pass to avoid
    # iterating ``range(len(tasks))`` three times. Same pybind11 cost — one
    # ``solver.value`` per (start, end, dur) variable — but only one Python
    # loop overhead.
    start_vals: dict[int, int] = {}
    end_vals: dict[int, int] = {}
    dur_vals: dict[int, int] = {}
    for i in range(len(tasks)):
        start_vals[i] = solver.value(bundle.start[i])
        end_vals[i] = solver.value(bundle.end[i])
        dur_vals[i] = solver.value(bundle.dur[i])

    assignments = []
    for i, task in enumerate(tasks):
        assigned = None
        for a in compat[i]:
            if x_vals.get((i, a)):
                assigned = a
                break
        assignments.append(
            {
                "task_id": task.id,
                "task_index": i,
                "agent_id": agents[assigned].id if assigned is not None else "unassigned",
                "agent_index": assigned,
                "start": start_vals[i],
                "end": end_vals[i],
                "duration": dur_vals[i],
                "phase": task.phase,
                "story_id": task.story_id,
                "story_priority": task.story_priority,
                "file_paths": task.file_paths,
                "tokens": task.estimated_tokens,
                "required_skill": task.required_skill,
            }
        )
    assignments.sort(key=lambda a: (a["start"], a["task_id"]))
    return assignments


def _build_waves(assignments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    waves: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for a in assignments:
        waves[a["start"]].append(a)
    return [
        {"wave": idx + 1, "start_time": t_start, "tasks": waves[t_start]}
        for idx, t_start in enumerate(sorted(waves.keys()))
    ]


def _build_schedule_graph(
    assignments: list[dict[str, Any]],
    edges: list[tuple[int, int]],
    tasks: list[Task],
) -> nx.DiGraph:
    """DAG of the realised schedule: explicit edges + induced resource arcs.

    Same-agent consecutive tasks and same-file non-[P] tasks become real
    precedence arcs in the solved schedule, since the solver enforced
    those disjunctive constraints. Making them explicit here lets
    networkx compute the true critical path over a single DAG.
    """
    assn = {a["task_index"]: a for a in assignments}
    n = len(tasks)
    weight_of: dict[int, int] = {}
    start_of: dict[int, int] = {}
    end_of: dict[int, int] = {}
    task_id_of: dict[int, str] = {}
    by_agent: dict[int | None, list[int]] = defaultdict(list)
    by_file: dict[str, list[int]] = defaultdict(list)

    for i, task in enumerate(tasks):
        a = assn.get(i)
        start_of[i] = a["start"] if a else 0
        end_of[i] = a["end"] if a else 0
        weight_of[i] = end_of[i] - start_of[i]
        task_id_of[i] = task.id
        if a is not None:
            by_agent[a.get("agent_index")].append(i)
        if not task.parallel_flag:
            for fp in task.file_paths:
                by_file[fp].append(i)

    graph = _build_node_weighted_graph(
        range(n),
        edges,
        weight_of,
        start=start_of,
        end=end_of,
        task_id=task_id_of,
    )

    def _add_sequential(idxs: list[int]) -> None:
        idxs.sort(key=lambda i: (start_of[i], i))
        graph.add_edges_from(zip(idxs, idxs[1:], strict=False))

    for group in by_agent.values():
        _add_sequential(group)
    for group in by_file.values():
        if len(group) >= 2:
            _add_sequential(group)

    return graph


def _critical_path(
    assignments: list[dict[str, Any]],
    edges: list[tuple[int, int]],
    tasks: list[Task],
) -> tuple[list[str], list[list[str]], list[list[str]]]:
    """Node-weighted longest path over the realised schedule graph.

    Returns `(path, resource_edges, path_edges)`:

    - ``path``: task ids along the makespan-driving chain.
    - ``resource_edges``: induced precedence arcs that the solver enforced
      but which are NOT in the parser edges (same-agent-consecutive and
      same-file). Callers can draw the full schedule DAG by unioning these
      with ``edges``.
    - ``path_edges``: every consecutive pair along ``path``, whether
      originally explicit or resource-induced. Downstream renderers use
      this to highlight the chain end-to-end without re-deriving it.
    """
    n = len(tasks)
    if n == 0 or not assignments:
        return [], [], []
    graph = _build_schedule_graph(assignments, edges, tasks)
    parser_edge_set = {(s, d) for s, d in edges}

    dist: dict[int, int] = {}
    best_pred: dict[int, int | None] = {}
    try:
        topo_iter = list(nx.lexicographical_topological_sort(graph))
    except nx.NetworkXUnfeasible:
        # Defensive: a cycle in the realised-schedule graph indicates an
        # internal inconsistency (e.g. a bug in resource-arc inference).
        # Don't crash the whole result — return an empty critical path so
        # the schedule itself is still surfaced to the caller.
        log.warning(
            "critical-path: realised-schedule graph has a cycle; "
            "returning empty critical path."
        )
        return [], [], []
    for u in topo_iter:
        w = graph.nodes[u]["weight"]
        chosen: int | None = None
        best_len = 0
        for p in graph.predecessors(u):
            if dist[p] > best_len:
                best_len = dist[p]
                chosen = p
        dist[u] = best_len + w
        best_pred[u] = chosen

    if not dist:
        return [], [], []
    sink = max(dist, key=lambda i: (dist[i], -i))
    chain: list[int] = []
    cur: int | None = sink
    while cur is not None:
        chain.append(cur)
        cur = best_pred.get(cur)
    chain.reverse()
    path_ids = [tasks[i].id for i in chain]

    resource_edges: list[list[str]] = [
        [tasks[s].id, tasks[d].id] for s, d in graph.edges if (s, d) not in parser_edge_set
    ]
    path_edges: list[list[str]] = [
        [tasks[s].id, tasks[d].id] for s, d in zip(chain, chain[1:], strict=False)
    ]
    return path_ids, resource_edges, path_edges


def _build_agent_summary(
    solver: cp_model.CpSolver,
    bundle: ModelBundle,
    assignments: list[dict[str, Any]],
    agents: list[Agent],
) -> list[dict[str, Any]]:
    from ..model.build import cost_dollars

    # Group assignments by agent index in a single pass so the per-agent
    # iteration below is O(n + m) instead of O(n × m). Negligible for
    # realistic project sizes but tidier and avoids the inner ``filter``-
    # like scan on each agent.
    by_agent: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
    for task in assignments:
        by_agent[task["agent_index"]].append(task)

    summary = []
    for ag in agents:
        a_tasks = by_agent.get(ag.index, [])
        total_tokens = sum(task["tokens"] for task in a_tasks)
        cost = round(cost_dollars(total_tokens, ag.price_per_1k_tokens), 4)
        row: dict[str, Any] = {
            "agent_id": ag.id,
            "model": ag.model,
            "task_count": len(a_tasks),
            "total_tokens": total_tokens,
            "budget_utilization": round(total_tokens / ag.context_budget * 100, 1),
            "total_load": solver.value(bundle.load[ag.index]),
            "kappa_utilization": round(len(a_tasks) / ag.kappa * 100, 1),
            "cost": cost,
            "tasks": [task["task_id"] for task in a_tasks],
        }
        if ag.provider is not None:
            row["provider"] = ag.provider
        summary.append(row)
    return summary


def _finalize_result(
    solver: cp_model.CpSolver,
    bundle: ModelBundle,
    tasks: list[Task],
    edges: list[tuple[int, int]],
    agents: list[Agent],
    compat: dict[int, list[int]],
    stats: dict[str, Any],
    status: cp_model.CpSolverStatus,
    warnings: Any,
) -> dict[str, Any]:
    # Return shape mirrors :class:`solver.model.result_types.ScheduleResult`
    # (TypedDict, ``total=False``) — the runtime annotation stays
    # ``dict[str, Any]`` because mypy's TypedDict-vs-``dict[str, Any]``
    # variance rules reject ``stats`` (assembled here as a plain dict)
    # against the typed ``Stats`` slot. ``ScheduleResult`` is therefore
    # documentation-only; implementations across the package return
    # ``dict[str, Any]`` end-to-end.
    assignments = _extract_assignments(solver, bundle, tasks, agents, compat)
    waves = _build_waves(assignments)
    agent_summary = _build_agent_summary(solver, bundle, assignments, agents)
    critical_path, resource_edges, critical_path_edges = _critical_path(
        assignments,
        edges,
        tasks,
    )

    stats["makespan"] = solver.value(bundle.makespan)
    stats["max_load"] = solver.value(bundle.max_load)
    loads = [solver.value(bundle.load[ag.index]) for ag in agents]
    stats["min_load"] = min(loads) if loads else 0
    stats["total_tasks"] = len(tasks)
    stats["total_agents"] = len(agents)
    stats["total_waves"] = len(waves)
    stats["total_cost"] = round(sum(row["cost"] for row in agent_summary), 4)

    if status == cp_model.OPTIMAL:
        status_str = STATUS_OPTIMAL
    elif status == cp_model.FEASIBLE:
        status_str = STATUS_FEASIBLE
    else:
        status_str = STATUS_UNKNOWN
    # Joint-optimum provenness: only report OPTIMAL when every executed phase
    # proved optimality. Per-phase statuses remain in stats for diagnostics.
    if status_str == STATUS_OPTIMAL:
        for key in (f"phase{i}_status_code" for i in (1, 2, 3)):
            phase_code = stats.get(key)
            if phase_code is not None and phase_code != cp_model.OPTIMAL:
                status_str = STATUS_FEASIBLE
                break
    stats["status"] = status_str

    # Mirror the canonical scalar metrics at the top level so programmatic
    # consumers (``result["makespan"]`` etc.) work without reaching into
    # ``stats``. The rendered output and existing tests still read from
    # ``stats``; mirroring keeps both surfaces in sync without duplicating
    # the source-of-truth (which remains the per-phase computation above).
    result: dict[str, Any] = {
        "status": status_str,
        "assignments": assignments,
        "waves": waves,
        "agent_summary": agent_summary,
        "critical_path": critical_path,
        "critical_path_edges": critical_path_edges,
        "resource_edges": resource_edges,
        "stats": stats,
        "warnings": warnings.as_list(),
        "makespan": stats["makespan"],
        "max_load": stats["max_load"],
        "total_cost": stats["total_cost"],
    }

    # Best-effort calibration capture: write a plan.json snapshot under
    # ``.specify/schedule/runs/`` so the user can run
    # ``/speckit.schedule.calibrate`` later. ``record_plan`` is the
    # designated swallow-all wrapper — it never raises, returns ``None``
    # whenever the write is impossible (no ``.specify/`` ancestor,
    # filesystem error, …) — so this call cannot regress the solve.
    # Lazy import to keep ``solver.result.extract`` cheap for callers
    # that only need the helpers above.
    if status_str in ("OPTIMAL", "FEASIBLE"):
        from .. import run_log

        run_log.record_plan(result, objective=stats.get("objective"))

    return result
