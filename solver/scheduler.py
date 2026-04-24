#!/usr/bin/env python3
"""spec-kit-schedule: CP-SAT Multi-Skill RCPSP Solver.

Input:  JSON on stdin with keys {tasks, edges, agents, config, warnings?}
Output: JSON on stdout with keys {assignments, waves, stats, edges, ...}

Model: Multi-Skill RCPSP with:
  - DAG precedence constraints
  - Heterogeneous agent skills and speed
  - Per-agent cardinality caps (κ)      — hallucination guardrail
  - Per-agent context-token budgets (C) — context-rot guardrail
  - File-mutex NoOverlap for non-[P] tasks sharing files
  - Lexicographic objective: min makespan, then min max-load (fairness),
    then max Σ story-priority weight (break ties towards higher-priority
    stories finishing earlier).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "solver"  # noqa: A001

import networkx as nx
from ortools.sat.python import cp_model

from .defaults import (
    CONTEXT_BUDGET_KTOKENS_DEFAULT,
    HORIZON_MULTIPLIER,
    KAPPA_DEFAULT,
    MAKESPAN_WEIGHT,
    NUM_WORKERS,
    OBJECTIVE,
    SPEED_FACTOR_DEFAULT,
    TIME_LIMIT_SECONDS,
    TOKEN_UNIT,
)
from .validation import (
    ScheduleInputError,
    find_cycle,
    validate_solver_input,
)
from .warnings_collector import WarningCollector

__all__ = ["solve_from_json", "main"]

log = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────
# Data classes
# ───────────────────────────────────────────────────────────────────────

@dataclass
class Task:
    id: str
    phase: str
    story_id: str | None
    story_priority: int
    parallel_flag: bool
    file_paths: list[str]
    required_skill: str
    estimated_tokens: int
    action_verb: str = ""
    index: int = 0


@dataclass
class Agent:
    id: str
    model: str
    skills: list[str]
    kappa: int
    context_budget: int
    speed_factor: float
    provider: str | None = None
    index: int = 0


@dataclass
class SolverConfig:
    objective: str = OBJECTIVE
    makespan_weight: int = MAKESPAN_WEIGHT
    time_limit: int = TIME_LIMIT_SECONDS
    num_workers: int = NUM_WORKERS
    symmetry_breaking: bool = True
    warm_start: bool = True
    horizon_multiplier: float = HORIZON_MULTIPLIER
    token_unit: int = TOKEN_UNIT


# ───────────────────────────────────────────────────────────────────────
# Parser
# ───────────────────────────────────────────────────────────────────────

def _parse_input(data: dict) -> tuple[list[Task], list[tuple[int, int]], list[Agent], SolverConfig]:
    tasks: list[Task] = []
    id_to_idx: dict[str, int] = {}
    for i, t in enumerate(data["tasks"]):
        task = Task(
            id=t["id"],
            phase=t.get("phase", "Setup"),
            story_id=t.get("story_id"),
            story_priority=int(t.get("story_priority", 99)),
            parallel_flag=bool(t.get("parallel_flag", False)),
            file_paths=list(t.get("file_paths", [])),
            required_skill=t.get("required_skill", "backend"),
            estimated_tokens=int(t.get("estimated_tokens", 3500)),
            action_verb=t.get("action_verb", "implement"),
            index=i,
        )
        tasks.append(task)
        id_to_idx[task.id] = i

    edges: list[tuple[int, int]] = []
    for e in data.get("edges", []):
        edges.append((id_to_idx[e[0]], id_to_idx[e[1]]))

    agents: list[Agent] = []
    for j, a in enumerate(data["agents"]):
        agents.append(Agent(
            id=a["id"],
            model=a.get("model", "unknown"),
            skills=list(a["skills"]),
            kappa=int(a.get("kappa", KAPPA_DEFAULT)),
            context_budget=int(
                a.get("context_budget", CONTEXT_BUDGET_KTOKENS_DEFAULT * 1000)
            ),
            speed_factor=float(a.get("speed_factor", SPEED_FACTOR_DEFAULT)),
            provider=a.get("provider"),
            index=j,
        ))

    cfg = data.get("config", {}) or {}
    config = SolverConfig(
        objective=cfg.get("objective", OBJECTIVE),
        makespan_weight=int(cfg.get("makespan_weight", MAKESPAN_WEIGHT)),
        time_limit=int(cfg.get("time_limit", TIME_LIMIT_SECONDS)),
        num_workers=int(cfg.get("num_workers", NUM_WORKERS)),
        symmetry_breaking=bool(cfg.get("symmetry_breaking", True)),
        warm_start=bool(cfg.get("warm_start", True)),
        horizon_multiplier=float(cfg.get("horizon_multiplier", HORIZON_MULTIPLIER)),
        token_unit=int(cfg.get("token_unit", TOKEN_UNIT)),
    )

    return tasks, edges, agents, config


# ───────────────────────────────────────────────────────────────────────
# Preflight
# ───────────────────────────────────────────────────────────────────────

def preflight_checks(
    tasks: list[Task],
    agents: list[Agent],
    warnings: WarningCollector,
) -> None:
    """Raise for conditions that make the problem dead on arrival.

    Catching these before building the CP-SAT model turns a silent
    timeout into an immediate, actionable error.
    """
    # Aggregate everything we need in a single pass over tasks and agents.
    tokens_by_skill: dict[str, int] = defaultdict(int)
    count_by_skill: dict[str, int] = defaultdict(int)
    tasks_by_skill: dict[str, list[str]] = defaultdict(list)
    total_tokens = 0
    for t in tasks:
        tokens_by_skill[t.required_skill] += t.estimated_tokens
        count_by_skill[t.required_skill] += 1
        tasks_by_skill[t.required_skill].append(t.id)
        total_tokens += t.estimated_tokens

    budget_by_skill: dict[str, int] = defaultdict(int)
    kappa_by_skill: dict[str, int] = defaultdict(int)
    all_agent_skills: set[str] = set()
    total_budget = 0
    for a in agents:
        all_agent_skills.update(a.skills)
        total_budget += a.context_budget
        for s in a.skills:
            budget_by_skill[s] += a.context_budget
            kappa_by_skill[s] += a.kappa

    uncovered = set(tokens_by_skill) - all_agent_skills
    if uncovered:
        details = "; ".join(
            f"skill {s!r} required by "
            f"{tasks_by_skill[s][:5]}"
            + (" ..." if len(tasks_by_skill[s]) > 5 else "")
            for s in uncovered
        )
        raise ScheduleInputError(
            f"No agent provides the required skill(s). {details}. "
            "Add an agent with the missing skill, or edit skill_rules in "
            "schedule-config.yml to route those tasks to an existing agent."
        )

    if total_tokens > total_budget:
        raise ScheduleInputError(
            f"Infeasible: sum(estimated_tokens)={total_tokens} exceeds "
            f"sum(context_budget)={total_budget} across all agents. "
            "Increase context_budget, split the feature, or add agents."
        )

    for skill, need in tokens_by_skill.items():
        have = budget_by_skill.get(skill, 0)
        if need > have:
            raise ScheduleInputError(
                f"Infeasible: tasks requiring skill {skill!r} need "
                f"{need} tokens total, but agents with that skill only "
                f"expose {have} tokens combined."
            )

    for skill, need in count_by_skill.items():
        have = kappa_by_skill.get(skill, 0)
        if need > have:
            raise ScheduleInputError(
                f"Infeasible: {need} tasks require skill {skill!r} but "
                f"total κ for agents with that skill is {have}. "
                "Increase κ or add agents."
            )

    log.info(
        "preflight ok: %d tasks, %d agents, %d tokens / %d budget",
        len(tasks), len(agents), total_tokens, total_budget,
    )


# ───────────────────────────────────────────────────────────────────────
# Compatibility and duration computation
# ───────────────────────────────────────────────────────────────────────

def compute_compatible_agents(
    tasks: list[Task],
    agents: list[Agent],
) -> dict[int, list[int]]:
    """Return {task_index: [agent_index, ...]} of skill-matching agents.

    Raises `ScheduleInputError` for any task whose required skill is not
    offered by at least one agent. `preflight_checks` should catch this
    first; the check here is a defensive invariant for library callers
    that bypass preflight.
    """
    compat: dict[int, list[int]] = {}
    for t in tasks:
        matches = [a.index for a in agents if t.required_skill in a.skills]
        if not matches:
            raise ScheduleInputError(
                f"Task {t.id}: no agent provides skill {t.required_skill!r}"
            )
        compat[t.index] = matches
    return compat


def compute_durations(
    tasks: list[Task],
    agents: list[Agent],
    token_unit: int,
) -> dict[tuple[int, int], int]:
    """p[i,a] = ceil(ceil(estimated_tokens / token_unit) / speed_factor).

    `token_unit` trades schedule granularity for horizon size: smaller is
    more precise, larger shrinks the solver search space.
    """
    p: dict[tuple[int, int], int] = {}
    for t in tasks:
        base_units = max(1, math.ceil(t.estimated_tokens / token_unit))
        for a in agents:
            scaled = math.ceil(base_units / a.speed_factor)
            p[(t.index, a.index)] = max(1, int(scaled))
    return p


def compute_min_durations(
    n: int,
    compat: dict[int, list[int]],
    p: dict[tuple[int, int], int],
) -> dict[int, int]:
    """Minimum duration per task across its compatible agents.

    Shared between `critical_path_bound`, `_horizon`, and
    `list_schedule_heuristic`; computing once avoids O(n·m) repeats.
    """
    return {i: min(p[(i, a)] for a in compat[i]) for i in range(n)}


# ───────────────────────────────────────────────────────────────────────
# File-conflict sets
# ───────────────────────────────────────────────────────────────────────

def build_file_conflict_groups(tasks: list[Task]) -> dict[str, list[int]]:
    """Non-[P] tasks sharing a file path form a mutex group."""
    file_to_tasks: dict[str, list[int]] = defaultdict(list)
    for t in tasks:
        if t.parallel_flag:
            continue
        for fp in t.file_paths:
            file_to_tasks[fp].append(t.index)
    return {f: idxs for f, idxs in file_to_tasks.items() if len(idxs) > 1}


# ───────────────────────────────────────────────────────────────────────
# Graph helpers (networkx-backed)
# ───────────────────────────────────────────────────────────────────────

def _build_node_weighted_graph(
    nodes: range | list[int],
    edges: list[tuple[int, int]],
    weight_of: dict[int, int],
    **node_attrs_by_id: dict[int, object],
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


def _precedence_graph(
    n: int,
    edges: list[tuple[int, int]],
    min_dur: dict[int, int],
) -> nx.DiGraph:
    """Precedence DAG weighted by per-task minimum duration."""
    return _build_node_weighted_graph(range(n), edges, min_dur)


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


def critical_path_bound(
    n: int,
    edges: list[tuple[int, int]],
    min_dur: dict[int, int],
    *,
    graph: nx.DiGraph | None = None,
) -> int:
    """Longest path in the precedence DAG weighted by minimum duration.

    Accepts a pre-built `graph` to avoid reconstructing the DAG when the
    caller already has one (see `solve_from_json`).
    """
    if n == 0:
        return 1
    return _node_weighted_longest_path_length(
        graph if graph is not None else _precedence_graph(n, edges, min_dur)
    )


# ───────────────────────────────────────────────────────────────────────
# Priority-rule warm-start
# ───────────────────────────────────────────────────────────────────────

def list_schedule_heuristic(
    tasks: list[Task],
    edges: list[tuple[int, int]],
    agents: list[Agent],
    compat: dict[int, list[int]],
    p: dict[tuple[int, int], int],
    min_dur: dict[int, int],
    file_conflicts: dict[str, list[int]],
    *,
    graph: nx.DiGraph | None = None,
) -> dict[int, tuple[int, int]]:
    """Greedy list scheduling that respects every hard constraint.

    The heuristic mirrors the CP-SAT model constraints (precedence, κ, C,
    file-mutex) so every hint it produces is already feasible; CP-SAT can
    then start from a valid incumbent instead of discarding the hint.
    """
    n = len(tasks)
    if graph is None:
        graph = _precedence_graph(n, edges, min_dur)
    topo = list(nx.topological_sort(graph))

    est = [0] * n
    for u in topo:
        for v in graph.successors(u):
            est[v] = max(est[v], est[u] + min_dur[u])
    pred = {v: list(graph.predecessors(v)) for v in range(n)}

    priority_order = sorted(
        range(n),
        key=lambda i: (est[i], tasks[i].story_priority, i),
    )

    agent_avail = {a.index: 0 for a in agents}
    task_count = {a.index: 0 for a in agents}
    token_used = {a.index: 0 for a in agents}
    file_avail: dict[str, int] = defaultdict(int)
    result: dict[int, tuple[int, int]] = {}
    task_end: dict[int, int] = {}

    def earliest_file_start(t: Task) -> int:
        if t.parallel_flag:
            return 0
        best = 0
        for fp in t.file_paths:
            if fp in file_avail:
                best = max(best, file_avail[fp])
        return best

    for i in priority_order:
        t = tasks[i]
        earliest = max(est[i], earliest_file_start(t))
        for pr in pred[i]:
            if pr in task_end:
                earliest = max(earliest, task_end[pr])

        best_a: int | None = None
        best_start = float("inf")
        for a_idx in compat[i]:
            ag = agents[a_idx]
            if task_count[a_idx] >= ag.kappa:
                continue
            if token_used[a_idx] + t.estimated_tokens > ag.context_budget:
                continue
            start = max(earliest, agent_avail[a_idx])
            if start < best_start:
                best_start = start
                best_a = a_idx

        if best_a is None:
            best_a = compat[i][0]
            best_start = max(earliest, agent_avail[best_a])

        dur = p[(i, best_a)]
        result[i] = (best_a, int(best_start))
        end = int(best_start) + dur
        task_end[i] = end
        agent_avail[best_a] = end
        task_count[best_a] += 1
        token_used[best_a] += t.estimated_tokens
        if not t.parallel_flag:
            for fp in t.file_paths:
                file_avail[fp] = max(file_avail[fp], end)

    return result


# ───────────────────────────────────────────────────────────────────────
# Model construction
# ───────────────────────────────────────────────────────────────────────

@dataclass
class ModelBundle:
    model: cp_model.CpModel
    start: dict[int, cp_model.IntVar]
    end: dict[int, cp_model.IntVar]
    dur: dict[int, cp_model.IntVar]
    x: dict[tuple[int, int], cp_model.IntVar]
    load: dict[int, cp_model.IntVar]
    max_load: cp_model.IntVar
    makespan: cp_model.IntVar
    horizon: int


def _horizon(
    n: int,
    edges: list[tuple[int, int]],
    agents: list[Agent],
    min_dur: dict[int, int],
    file_conflicts: dict[str, list[int]],
    multiplier: float,
    *,
    graph: nx.DiGraph | None = None,
) -> int:
    """Upper bound on any feasible makespan.

    Takes the max of three bounds:
      - critical path (ignores resource contention);
      - the longest sum-of-durations across any file-mutex group (a single
        mutex group alone can dominate the schedule);
      - sum_of_min_durations / num_agents (amortised load per agent).
    Then inflates by `multiplier` as a safety envelope.
    """
    if n == 0:
        return 1
    cp = critical_path_bound(n, edges, min_dur, graph=graph)
    load_bound = math.ceil(sum(min_dur.values()) / max(1, len(agents)))
    mutex_bound = 0
    for idxs in file_conflicts.values():
        mutex_bound = max(mutex_bound, sum(min_dur[i] for i in idxs))
    base = max(cp, load_bound, mutex_bound, 1)
    return max(1, int(math.ceil(base * multiplier)))


def build_model(
    tasks: list[Task],
    edges: list[tuple[int, int]],
    agents: list[Agent],
    compat: dict[int, list[int]],
    p: dict[tuple[int, int], int],
    min_dur: dict[int, int],
    file_conflicts: dict[str, list[int]],
    config: SolverConfig,
    *,
    graph: nx.DiGraph | None = None,
) -> ModelBundle:
    n = len(tasks)
    m = len(agents)
    model = cp_model.CpModel()

    horizon = _horizon(
        n, edges, agents, min_dur, file_conflicts,
        config.horizon_multiplier, graph=graph,
    )

    start = {i: model.new_int_var(0, horizon, f"s_{i}") for i in range(n)}
    end = {i: model.new_int_var(0, horizon, f"e_{i}") for i in range(n)}
    dur: dict[int, cp_model.IntVar] = {}
    for i in range(n):
        d_max = max(p[(i, a)] for a in compat[i])
        dur[i] = model.new_int_var(min_dur[i], d_max, f"d_{i}")

    master_iv = {
        i: model.new_interval_var(start[i], dur[i], end[i], f"iv_{i}")
        for i in range(n)
    }

    x: dict[tuple[int, int], cp_model.IntVar] = {}
    ivs_agent: dict[int, list] = defaultdict(list)
    for i in range(n):
        presences = []
        for a in compat[i]:
            lit = model.new_bool_var(f"x_{i}_{a}")
            x[(i, a)] = lit
            presences.append(lit)
            opt_iv = model.new_optional_fixed_size_interval_var(
                start[i], p[(i, a)], lit, f"oiv_{i}_{a}"
            )
            ivs_agent[a].append(opt_iv)
            model.add(dur[i] == p[(i, a)]).only_enforce_if(lit)
        model.add_exactly_one(presences)

    for a in range(m):
        if ivs_agent[a]:
            model.add_no_overlap(ivs_agent[a])

    for (i, j) in edges:
        model.add(end[i] <= start[j])

    for task_indices in file_conflicts.values():
        if len(task_indices) > 1:
            model.add_no_overlap([master_iv[i] for i in task_indices])

    for a in range(m):
        agent_tasks = [x[(i, a)] for i in range(n) if (i, a) in x]
        if agent_tasks:
            model.add(sum(agent_tasks) <= agents[a].kappa)

    for a in range(m):
        token_terms = [
            tasks[i].estimated_tokens * x[(i, a)]
            for i in range(n) if (i, a) in x
        ]
        if token_terms:
            model.add(sum(token_terms) <= agents[a].context_budget)

    load: dict[int, cp_model.IntVar] = {}
    for a in range(m):
        load_terms = [p[(i, a)] * x[(i, a)] for i in range(n) if (i, a) in x]
        load[a] = model.new_int_var(0, horizon, f"L_{a}")
        if load_terms:
            model.add(load[a] == sum(load_terms))
        else:
            model.add(load[a] == 0)

    max_load = model.new_int_var(0, horizon, "Lmax")
    model.add_max_equality(max_load, [load[a] for a in range(m)])

    makespan = model.new_int_var(0, horizon, "Cmax")
    model.add_max_equality(makespan, [end[i] for i in range(n)])

    if config.symmetry_breaking:
        groups: dict[tuple, list[int]] = defaultdict(list)
        for a in agents:
            key = (tuple(sorted(a.skills)), a.kappa, a.context_budget, a.speed_factor)
            groups[key].append(a.index)
        for group in groups.values():
            if len(group) > 1:
                ordered = sorted(group)
                for k in range(len(ordered) - 1):
                    model.add(load[ordered[k]] >= load[ordered[k + 1]])

    return ModelBundle(
        model=model,
        start=start,
        end=end,
        dur=dur,
        x=x,
        load=load,
        max_load=max_load,
        makespan=makespan,
        horizon=horizon,
    )


def _apply_hints(
    bundle: ModelBundle,
    hints: dict[int, tuple[int, int]],
    tasks: list[Task],
    compat: dict[int, list[int]],
) -> None:
    for i, (a_hint, s_hint) in hints.items():
        if (i, a_hint) in bundle.x:
            bundle.model.add_hint(bundle.x[(i, a_hint)], 1)
            bundle.model.add_hint(bundle.start[i], s_hint)
            for a in compat[i]:
                if a != a_hint and (i, a) in bundle.x:
                    bundle.model.add_hint(bundle.x[(i, a)], 0)


def _run_solver(model: cp_model.CpModel, config: SolverConfig) -> tuple[cp_model.CpSolver, int, float]:
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = config.num_workers
    solver.parameters.max_time_in_seconds = config.time_limit
    solver.parameters.log_search_progress = False
    t0 = time.time()
    status = solver.solve(model)
    elapsed = time.time() - t0
    return solver, status, elapsed


# ───────────────────────────────────────────────────────────────────────
# Solution extraction
# ───────────────────────────────────────────────────────────────────────

def _extract_assignments(
    solver: cp_model.CpSolver,
    bundle: ModelBundle,
    tasks: list[Task],
    agents: list[Agent],
    compat: dict[int, list[int]],
) -> list[dict]:
    assignments = []
    for i, t in enumerate(tasks):
        assigned = None
        for a in compat[i]:
            if (i, a) in bundle.x and solver.value(bundle.x[(i, a)]):
                assigned = a
                break
        assignments.append({
            "task_id": t.id,
            "task_index": i,
            "agent_id": agents[assigned].id if assigned is not None else "unassigned",
            "agent_index": assigned,
            "start": solver.value(bundle.start[i]),
            "end": solver.value(bundle.end[i]),
            "duration": solver.value(bundle.dur[i]),
            "phase": t.phase,
            "story_id": t.story_id,
            "story_priority": t.story_priority,
            "file_paths": t.file_paths,
            "tokens": t.estimated_tokens,
            "required_skill": t.required_skill,
        })
    assignments.sort(key=lambda a: (a["start"], a["task_id"]))
    return assignments


def _build_waves(assignments: list[dict]) -> list[dict]:
    waves: dict[int, list[dict]] = defaultdict(list)
    for a in assignments:
        waves[a["start"]].append(a)
    return [
        {"wave": idx + 1, "start_time": t_start, "tasks": waves[t_start]}
        for idx, t_start in enumerate(sorted(waves.keys()))
    ]


def _build_schedule_graph(
    assignments: list[dict],
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

    for i, t in enumerate(tasks):
        a = assn.get(i)
        start_of[i] = a["start"] if a else 0
        end_of[i] = a["end"] if a else 0
        weight_of[i] = end_of[i] - start_of[i]
        task_id_of[i] = t.id
        if a is not None:
            by_agent[a.get("agent_index")].append(i)
        if not t.parallel_flag:
            for fp in t.file_paths:
                by_file[fp].append(i)

    graph = _build_node_weighted_graph(
        range(n), edges, weight_of,
        start=start_of, end=end_of, task_id=task_id_of,
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
    assignments: list[dict],
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
    for u in nx.lexicographical_topological_sort(graph):
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
        [tasks[s].id, tasks[d].id]
        for s, d in graph.edges
        if (s, d) not in parser_edge_set
    ]
    path_edges: list[list[str]] = [
        [tasks[s].id, tasks[d].id]
        for s, d in zip(chain, chain[1:], strict=False)
    ]
    return path_ids, resource_edges, path_edges


def _build_agent_summary(
    solver: cp_model.CpSolver,
    bundle: ModelBundle,
    assignments: list[dict],
    agents: list[Agent],
) -> list[dict]:
    summary = []
    for a in agents:
        a_tasks = [t for t in assignments if t["agent_index"] == a.index]
        total_tokens = sum(t["tokens"] for t in a_tasks)
        row: dict = {
            "agent_id": a.id,
            "model": a.model,
            "task_count": len(a_tasks),
            "total_tokens": total_tokens,
            "budget_utilization": round(total_tokens / a.context_budget * 100, 1),
            "total_load": solver.value(bundle.load[a.index]),
            "kappa_utilization": round(len(a_tasks) / a.kappa * 100, 1),
            "tasks": [t["task_id"] for t in a_tasks],
        }
        if a.provider is not None:
            row["provider"] = a.provider
        summary.append(row)
    return summary


# ───────────────────────────────────────────────────────────────────────
# Orchestration
# ───────────────────────────────────────────────────────────────────────

def solve(
    tasks: list[Task],
    edges: list[tuple[int, int]],
    agents: list[Agent],
    compat: dict[int, list[int]],
    p: dict[tuple[int, int], int],
    min_dur: dict[int, int],
    file_conflicts: dict[str, list[int]],
    config: SolverConfig,
    warnings: WarningCollector,
    hints: dict[int, tuple[int, int]] | None = None,
    *,
    graph: nx.DiGraph | None = None,
) -> dict:
    bundle = build_model(
        tasks, edges, agents, compat, p, min_dur, file_conflicts, config,
        graph=graph,
    )
    stats: dict = {"horizon": bundle.horizon}

    if hints and config.warm_start:
        _apply_hints(bundle, hints, tasks, compat)

    if config.objective == "weighted":
        bundle.model.minimize(
            config.makespan_weight * bundle.makespan + bundle.max_load
        )
        solver, status, elapsed = _run_solver(bundle.model, config)
        stats["solve_time"] = round(elapsed, 2)
        stats["phase1_status"] = solver.status_name(status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return {
                "status": "INFEASIBLE",
                "message": "No feasible schedule found (weighted objective).",
                "stats": stats,
                "warnings": warnings.as_list(),
            }
        return _finalize_result(
            solver, bundle, tasks, edges, agents, compat,
            stats, status, warnings, phase2=False,
        )

    # Lexicographic: Phase 1 minimises makespan.
    bundle.model.minimize(bundle.makespan)
    solver1, status1, elapsed1 = _run_solver(bundle.model, config)
    stats["phase1_time"] = round(elapsed1, 2)
    stats["phase1_status"] = solver1.status_name(status1)

    if status1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {
            "status": "INFEASIBLE",
            "message": "Phase 1 found no feasible schedule.",
            "stats": stats,
            "warnings": warnings.as_list(),
        }

    ms_star = solver1.value(bundle.makespan)
    stats["makespan_phase1"] = ms_star

    # Phase 2: freeze makespan, minimise max load. Transfer Phase 1 values
    # directly as hints (one pass, no intermediate snapshot).
    bundle.model.add(bundle.makespan <= ms_star)
    bundle.model.clear_objective()
    bundle.model.clear_hints()
    for i in range(len(tasks)):
        bundle.model.add_hint(bundle.start[i], solver1.value(bundle.start[i]))
        bundle.model.add_hint(bundle.end[i], solver1.value(bundle.end[i]))
        for a in compat[i]:
            if (i, a) in bundle.x:
                bundle.model.add_hint(
                    bundle.x[(i, a)], solver1.value(bundle.x[(i, a)])
                )
    bundle.model.minimize(bundle.max_load)

    solver2, status2, elapsed2 = _run_solver(bundle.model, config)
    stats["phase2_time"] = round(elapsed2, 2)
    stats["phase2_status"] = solver2.status_name(status2)

    if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        solver_final = solver2
        final_status = status2
    else:
        warnings.add(
            "phase2_fallback",
            "Phase 2 (load balancing) did not return a solution within "
            "the time limit. Returning the Phase 1 solution; load balance "
            "may be suboptimal. Increase solver.time_limit to improve it.",
        )
        solver_final = solver1
        final_status = status1

    return _finalize_result(
        solver_final, bundle, tasks, edges, agents, compat,
        stats, final_status, warnings, phase2=True,
    )


def _finalize_result(
    solver: cp_model.CpSolver,
    bundle: ModelBundle,
    tasks: list[Task],
    edges: list[tuple[int, int]],
    agents: list[Agent],
    compat: dict[int, list[int]],
    stats: dict,
    status: int,
    warnings: WarningCollector,
    phase2: bool,
) -> dict:
    assignments = _extract_assignments(solver, bundle, tasks, agents, compat)
    waves = _build_waves(assignments)
    agent_summary = _build_agent_summary(solver, bundle, assignments, agents)
    critical_path, resource_edges, critical_path_edges = _critical_path(
        assignments, edges, tasks,
    )

    stats["makespan"] = solver.value(bundle.makespan)
    stats["max_load"] = solver.value(bundle.max_load)
    loads = [solver.value(bundle.load[a.index]) for a in agents]
    stats["min_load"] = min(loads) if loads else 0
    stats["total_tasks"] = len(tasks)
    stats["total_agents"] = len(agents)
    stats["total_waves"] = len(waves)

    if status == cp_model.OPTIMAL:
        status_str = "OPTIMAL"
    elif status == cp_model.FEASIBLE:
        status_str = "FEASIBLE"
    else:
        status_str = "UNKNOWN"
    stats["status"] = status_str

    return {
        "status": status_str,
        "assignments": assignments,
        "waves": waves,
        "agent_summary": agent_summary,
        "critical_path": critical_path,
        "critical_path_edges": critical_path_edges,
        "resource_edges": resource_edges,
        "stats": stats,
        "warnings": warnings.as_list(),
    }


# ───────────────────────────────────────────────────────────────────────
# Top-level entry point
# ───────────────────────────────────────────────────────────────────────

def solve_from_json(data: dict) -> dict:
    """Validate, build, solve. Returns the full result envelope."""
    validate_solver_input(data)
    tasks, edges, agents, config = _parse_input(data)

    cycle = find_cycle(len(tasks), edges)
    if cycle is not None:
        names = " → ".join(tasks[i].id for i in cycle)
        raise ScheduleInputError(f"Dependency cycle in solver input: {names}")

    warnings = WarningCollector()
    for w in data.get("warnings", []) or []:
        warnings.add(
            w.get("code", "upstream"),
            w.get("message", ""),
            **w.get("context", {}),
        )

    preflight_checks(tasks, agents, warnings)

    compat = compute_compatible_agents(tasks, agents)
    p = compute_durations(tasks, agents, config.token_unit)
    min_dur = compute_min_durations(len(tasks), compat, p)
    file_conflicts = build_file_conflict_groups(tasks)
    precedence_graph = _precedence_graph(len(tasks), edges, min_dur)

    hints = None
    if config.warm_start:
        hints = list_schedule_heuristic(
            tasks, edges, agents, compat, p, min_dur, file_conflicts,
            graph=precedence_graph,
        )

    result = solve(
        tasks, edges, agents, compat, p, min_dur, file_conflicts,
        config, warnings, hints, graph=precedence_graph,
    )

    result["edges"] = [[tasks[s].id, tasks[d].id] for s, d in edges]
    result["tasks"] = [
        {
            "id": t.id,
            "phase": t.phase,
            "story_id": t.story_id,
            "story_priority": t.story_priority,
            "required_skill": t.required_skill,
        }
        for t in tasks
    ]
    return result


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="scheduler.py",
        description="Solve a multi-agent schedule from parser JSON on stdin.",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON on stdin: {exc}", file=sys.stderr)
        return 2

    try:
        result = solve_from_json(data)
    except ScheduleInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
