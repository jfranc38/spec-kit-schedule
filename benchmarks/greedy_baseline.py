"""Pure-Python greedy baseline scheduler (MAQA-style first-available-agent).

No CP-SAT. Schedules tasks in topological order, assigning each to the
eligible agent with the earliest available time.

Returns a dict compatible with the solver output contract.
"""

from __future__ import annotations

import math
import sys
from collections import defaultdict, deque

__all__ = ["greedy_solve"]

_STATUS_FEASIBLE = "GREEDY_FEASIBLE"
_STATUS_INFEASIBLE = "GREEDY_INFEASIBLE"


def _topological_sort(task_ids: list[str], edges: list[list[str]]) -> list[str]:
    """Kahn's algorithm; raises ValueError on cycle.

    Uses ``collections.deque.popleft()`` (O(1)) rather than ``list.pop(0)``
    (O(n)) so the routine stays linear in the graph size — the previous
    quadratic behaviour was visible on the ``xl`` shape (n=400).
    """
    in_degree: dict[str, int] = dict.fromkeys(task_ids, 0)
    adjacency: dict[str, list[str]] = {t: [] for t in task_ids}
    for src, dst in edges:
        if src in adjacency and dst in in_degree:
            adjacency[src].append(dst)
            in_degree[dst] += 1

    queue: deque[str] = deque(sorted(t for t, d in in_degree.items() if d == 0))
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for nxt in sorted(adjacency[node]):
            in_degree[nxt] -= 1
            if in_degree[nxt] == 0:
                queue.append(nxt)

    if len(order) != len(task_ids):
        raise ValueError("Cycle detected in task graph")
    return order


def greedy_solve(data: dict) -> dict:
    """Schedule tasks greedily and return a solver-compatible result dict.

    Algorithm
    ---------
    For each task in topological order:
        eligible = [a for a in agents
                    if task.skill in a.skills
                    and a.kappa_used < a.kappa
                    and a.tokens_used + task.tokens <= a.context_budget]
        agent = agent in eligible with earliest available time (ties: lowest id)
        start = max(max(pred.end for pred in task.preds),
                    agent.available,
                    file_mutex_wait)
        assign(task, agent, start, start + task.duration)
    """
    tasks_raw: list[dict] = data.get("tasks", [])
    edges_raw: list[list[str]] = data.get("edges", [])
    agents_raw: list[dict] = data.get("agents", [])

    if not tasks_raw or not agents_raw:
        return {
            "status": _STATUS_INFEASIBLE,
            "assignments": [],
            "waves": [],
            "agent_summary": [],
            "critical_path": [],
            "critical_path_edges": [],
            "resource_edges": [],
            "edges": edges_raw,
            "stats": {
                "status": _STATUS_INFEASIBLE,
                "makespan": 0,
                "max_load": 0,
                "min_load": 0,
                "total_tasks": 0,
                "total_agents": len(agents_raw),
                "total_waves": 0,
            },
            "warnings": [],
        }

    task_ids = [t["id"] for t in tasks_raw]
    task_by_id: dict[str, dict] = {t["id"]: t for t in tasks_raw}

    topo_order = _topological_sort(task_ids, edges_raw)

    # Predecessor map: task_id -> [predecessor task_ids]
    preds: dict[str, list[str]] = defaultdict(list)
    for src, dst in edges_raw:
        preds[dst].append(src)

    # Agent state
    agent_avail: dict[str, int] = {a["id"]: 0 for a in agents_raw}
    agent_kappa_used: dict[str, int] = {a["id"]: 0 for a in agents_raw}
    agent_tokens_used: dict[str, int] = {a["id"]: 0 for a in agents_raw}

    # File mutex: track when each file was last released
    file_avail: dict[str, int] = defaultdict(int)

    # Task end times (for precedence)
    task_end: dict[str, int] = {}

    assignments: list[dict] = []
    any_infeasible = False

    for task_id in topo_order:
        task = task_by_id[task_id]
        skill = task.get("required_skill", "backend")
        tokens = int(task.get("estimated_tokens", 3500))
        file_paths: list[str] = task.get("file_paths", [])
        parallel_flag: bool = bool(task.get("parallel_flag", False))

        # Earliest start from precedence constraints
        pred_end = max((task_end.get(p, 0) for p in preds[task_id]), default=0)

        # Earliest start from file mutex (skip for parallel tasks)
        if parallel_flag:
            mutex_wait = 0
        else:
            mutex_wait = max((file_avail.get(fp, 0) for fp in file_paths), default=0)

        earliest = max(pred_end, mutex_wait)

        # Find eligible agents
        eligible = [
            a for a in agents_raw
            if skill in a.get("skills", [])
            and agent_kappa_used[a["id"]] < int(a.get("kappa", 10))
            and agent_tokens_used[a["id"]] + tokens <= int(a.get("context_budget", 16000))
        ]

        if not eligible:
            # Fall back: any agent with the required skill (ignore caps)
            eligible = [a for a in agents_raw if skill in a.get("skills", [])]

        if not eligible:
            # No agent has the skill at all — schedule on the first agent anyway
            eligible = agents_raw[:1]
            any_infeasible = True
            print(
                f"WARNING: no agent has skill {skill!r} for task {task_id}",
                file=sys.stderr,
            )

        # Pick agent with earliest available time; break ties by agent id
        best_agent = min(
            eligible,
            key=lambda a: (max(earliest, agent_avail[a["id"]]), a["id"]),
        )
        best_id = best_agent["id"]

        start = max(earliest, agent_avail[best_id])
        speed = float(best_agent.get("speed_factor", 1.0))
        # Match CP-SAT duration formula: ceil(ceil(tokens/TOKEN_UNIT)/speed)
        base_units = max(1, math.ceil(tokens / 100))
        duration = max(1, int(math.ceil(base_units / speed)))
        end = start + duration

        task_end[task_id] = end
        agent_avail[best_id] = end
        agent_kappa_used[best_id] += 1
        agent_tokens_used[best_id] += tokens
        if not parallel_flag:
            for fp in file_paths:
                file_avail[fp] = max(file_avail.get(fp, 0), end)

        assignments.append({
            "task_id": task_id,
            "task_index": task_ids.index(task_id),
            "agent_id": best_id,
            "agent_index": next(
                i for i, a in enumerate(agents_raw) if a["id"] == best_id
            ),
            "start": start,
            "end": end,
            "duration": duration,
            "phase": task.get("phase", "Implementation"),
            "story_id": task.get("story_id"),
            "story_priority": int(task.get("story_priority", 99)),
            "file_paths": file_paths,
            "tokens": tokens,
            "required_skill": skill,
        })

    assignments.sort(key=lambda a: (a["start"], a["task_id"]))

    # Build waves (tasks starting at same time form a wave)
    waves_map: dict[int, list[dict]] = defaultdict(list)
    for a in assignments:
        waves_map[a["start"]].append(a)
    waves = [
        {"wave": idx + 1, "start_time": t, "tasks": waves_map[t]}
        for idx, t in enumerate(sorted(waves_map))
    ]

    makespan = max((a["end"] for a in assignments), default=0)
    loads: dict[str, int] = defaultdict(int)
    for a in assignments:
        loads[a["agent_id"]] += a["duration"]

    agent_summary = []
    for ag in agents_raw:
        ag_id = ag["id"]
        ag_tasks = [a for a in assignments if a["agent_id"] == ag_id]
        total_tokens = sum(a["tokens"] for a in ag_tasks)
        budget = int(ag.get("context_budget", 16000))
        kappa = int(ag.get("kappa", 10))
        total_load = loads[ag_id]
        row: dict = {
            "agent_id": ag_id,
            "model": ag.get("model", "benchmark-model"),
            "task_count": len(ag_tasks),
            "total_tokens": total_tokens,
            "budget_utilization": round(total_tokens / budget * 100, 1) if budget else 0.0,
            "total_load": total_load,
            "kappa_utilization": round(len(ag_tasks) / kappa * 100, 1) if kappa else 0.0,
            "tasks": [a["task_id"] for a in ag_tasks],
        }
        if ag.get("provider"):
            row["provider"] = ag["provider"]
        agent_summary.append(row)

    load_values = [loads[ag["id"]] for ag in agents_raw]
    max_load = max(load_values, default=0)
    min_load = min(load_values, default=0)

    status = _STATUS_INFEASIBLE if any_infeasible else _STATUS_FEASIBLE
    stats = {
        "status": status,
        "makespan": makespan,
        "max_load": max_load,
        "min_load": min_load,
        "total_tasks": len(assignments),
        "total_agents": len(agents_raw),
        "total_waves": len(waves),
    }

    return {
        "status": status,
        "assignments": assignments,
        "waves": waves,
        "agent_summary": agent_summary,
        "critical_path": [],
        "critical_path_edges": [],
        "resource_edges": [],
        "edges": edges_raw,
        "stats": stats,
        "warnings": [],
    }
