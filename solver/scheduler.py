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
from dataclasses import field as _field
from statistics import NormalDist

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "solver"  # noqa: A001

import networkx as nx
from ortools.sat.python import cp_model

from .defaults import (
    ANYTIME_DEFAULT,
    CONTEXT_BUDGET_KTOKENS_DEFAULT,
    HORIZON_MULTIPLIER,
    KAPPA_DEFAULT,
    MAKESPAN_WEIGHT,
    NUM_WORKERS,
    OBJECTIVE,
    SPEED_FACTOR_DEFAULT,
    STOCHASTIC_QUANTILE_DEFAULT,
    TIME_LIMIT_SECONDS,
    TOKEN_UNIT,
)
from .i18n import t
from .validation import (
    ScheduleInputError,
    find_cycle,
    validate_solver_input,
)
from .warnings_collector import WarningCollector

__all__ = ["solve_from_json", "solve_with_fixed", "main"]

log = logging.getLogger(__name__)

# Scaling factor that converts dollar costs to integers for CP-SAT.
# Four decimal places of dollar precision: $0.0001 is 1 unit.
_COST_SCALE = 10_000


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
    token_std_dev: float = 0.0
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
    price_per_1k_tokens: float = 0.0
    index: int = 0


@dataclass
class SolverConfig:
    objective: str = OBJECTIVE
    makespan_weight: int = MAKESPAN_WEIGHT
    cost_weight: int = 0
    time_limit: int = TIME_LIMIT_SECONDS
    num_workers: int = NUM_WORKERS
    symmetry_breaking: bool = True
    warm_start: bool = True
    horizon_multiplier: float = HORIZON_MULTIPLIER
    token_unit: int = TOKEN_UNIT
    stochastic_quantile: float = STOCHASTIC_QUANTILE_DEFAULT
    anytime: bool = ANYTIME_DEFAULT


# ───────────────────────────────────────────────────────────────────────
# Parser
# ───────────────────────────────────────────────────────────────────────


def _parse_input(data: dict) -> tuple[list[Task], list[tuple[int, int]], list[Agent], SolverConfig]:
    tasks: list[Task] = []
    id_to_idx: dict[str, int] = {}
    for i, t_raw in enumerate(data["tasks"]):
        task = Task(
            id=t_raw["id"],
            phase=t_raw.get("phase", "Setup"),
            story_id=t_raw.get("story_id"),
            story_priority=int(t_raw.get("story_priority", 99)),
            parallel_flag=bool(t_raw.get("parallel_flag", False)),
            file_paths=list(t_raw.get("file_paths", [])),
            required_skill=t_raw.get("required_skill", "backend"),
            estimated_tokens=int(t_raw.get("estimated_tokens", 3500)),
            action_verb=t_raw.get("action_verb", "implement"),
            token_std_dev=float(t_raw.get("token_std_dev", 0.0)),
            index=i,
        )
        tasks.append(task)
        id_to_idx[task.id] = i

    edges: list[tuple[int, int]] = []
    for e in data.get("edges", []):
        edges.append((id_to_idx[e[0]], id_to_idx[e[1]]))

    agents: list[Agent] = []
    for j, a in enumerate(data["agents"]):
        agents.append(
            Agent(
                id=a["id"],
                model=a.get("model", "unknown"),
                skills=list(a["skills"]),
                kappa=int(a.get("kappa", KAPPA_DEFAULT)),
                context_budget=int(a.get("context_budget", CONTEXT_BUDGET_KTOKENS_DEFAULT * 1000)),
                speed_factor=float(a.get("speed_factor", SPEED_FACTOR_DEFAULT)),
                provider=a.get("provider"),
                price_per_1k_tokens=float(a.get("price_per_1k_tokens", 0.0)),
                index=j,
            )
        )

    cfg = data.get("config", {}) or {}
    config = SolverConfig(
        objective=cfg.get("objective", OBJECTIVE),
        makespan_weight=int(cfg.get("makespan_weight", MAKESPAN_WEIGHT)),
        cost_weight=int(cfg.get("cost_weight", 0)),
        time_limit=int(cfg.get("time_limit", TIME_LIMIT_SECONDS)),
        num_workers=int(cfg.get("num_workers", NUM_WORKERS)),
        symmetry_breaking=bool(cfg.get("symmetry_breaking", True)),
        warm_start=bool(cfg.get("warm_start", True)),
        horizon_multiplier=float(cfg.get("horizon_multiplier", HORIZON_MULTIPLIER)),
        token_unit=int(cfg.get("token_unit", TOKEN_UNIT)),
        stochastic_quantile=float(cfg.get("stochastic_quantile", STOCHASTIC_QUANTILE_DEFAULT)),
        anytime=bool(cfg.get("anytime", ANYTIME_DEFAULT)),
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
    for task in tasks:
        tokens_by_skill[task.required_skill] += task.estimated_tokens
        count_by_skill[task.required_skill] += 1
        tasks_by_skill[task.required_skill].append(task.id)
        total_tokens += task.estimated_tokens

    budget_by_skill: dict[str, int] = defaultdict(int)
    kappa_by_skill: dict[str, int] = defaultdict(int)
    all_agent_skills: set[str] = set()
    total_budget = 0
    for ag in agents:
        all_agent_skills.update(ag.skills)
        total_budget += ag.context_budget
        for s in ag.skills:
            budget_by_skill[s] += ag.context_budget
            kappa_by_skill[s] += ag.kappa

    uncovered = set(tokens_by_skill) - all_agent_skills
    if uncovered:
        details = "; ".join(
            f"skill {s!r} required by "
            f"{tasks_by_skill[s][:5]}" + (" ..." if len(tasks_by_skill[s]) > 5 else "")
            for s in uncovered
        )
        raise ScheduleInputError(t("skill_uncovered", details=details))

    if total_tokens > total_budget:
        raise ScheduleInputError(t("budget_exceeded", total=total_tokens, budget=total_budget))

    for skill, need in tokens_by_skill.items():
        have = budget_by_skill.get(skill, 0)
        if need > have:
            raise ScheduleInputError(
                t("skill_budget_exceeded", skill=skill, required=need, have=have)
            )

    for skill, need in count_by_skill.items():
        have = kappa_by_skill.get(skill, 0)
        if need > have:
            raise ScheduleInputError(t("kappa_exceeded", count=need, skill=skill, kappa=have))

    log.info(
        "preflight ok: %d tasks, %d agents, %d tokens / %d budget",
        len(tasks),
        len(agents),
        total_tokens,
        total_budget,
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
    for task in tasks:
        matches = [ag.index for ag in agents if task.required_skill in ag.skills]
        if not matches:
            raise ScheduleInputError(t("task_no_skill", task_id=task.id, skill=task.required_skill))
        compat[task.index] = matches
    return compat


def _quantile_tokens(mean: int, std_dev: float, q: float) -> float:
    """Quantile of Normal(mean, std_dev) with left-truncation at 0."""
    return max(0.0, NormalDist(mu=mean, sigma=std_dev).inv_cdf(q))


def compute_durations(
    tasks: list[Task],
    agents: list[Agent],
    token_unit: int,
    stochastic_quantile: float = STOCHASTIC_QUANTILE_DEFAULT,
) -> dict[tuple[int, int], int]:
    """p[i,a] = ceil(ceil(effective_tokens / token_unit) / speed_factor).

    When a task carries ``token_std_dev > 0``, ``effective_tokens`` is the
    ``stochastic_quantile`` quantile of Normal(estimated_tokens, token_std_dev)
    truncated at 0.  Otherwise ``effective_tokens = estimated_tokens``.
    ``token_unit`` trades schedule granularity for horizon size.
    """
    p: dict[tuple[int, int], int] = {}
    for task in tasks:
        if task.token_std_dev > 0:
            eff = _quantile_tokens(task.estimated_tokens, task.token_std_dev, stochastic_quantile)
            base_units = max(1, math.ceil(eff / token_unit))
        else:
            base_units = max(1, math.ceil(task.estimated_tokens / token_unit))
        for ag in agents:
            scaled = math.ceil(base_units / ag.speed_factor)
            p[(task.index, ag.index)] = max(1, int(scaled))
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


def _raise_if_cycle(tasks: list[Task], edges: list[tuple[int, int]]) -> None:
    """Raise `ScheduleInputError` with the cycle path if `edges` form a cycle."""
    cycle = find_cycle(len(tasks), edges)
    if cycle is None:
        return
    names = " → ".join(tasks[i].id for i in cycle)
    raise ScheduleInputError(t("solver_input_cycle", names=names))


# ───────────────────────────────────────────────────────────────────────
# File-conflict sets
# ───────────────────────────────────────────────────────────────────────


def build_file_conflict_groups(tasks: list[Task]) -> dict[str, list[int]]:
    """Non-[P] tasks sharing a file path form a mutex group."""
    file_to_tasks: dict[str, list[int]] = defaultdict(list)
    for task in tasks:
        if task.parallel_flag:
            continue
        for fp in task.file_paths:
            file_to_tasks[fp].append(task.index)
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

    agent_avail = {ag.index: 0 for ag in agents}
    task_count = {ag.index: 0 for ag in agents}
    token_used = {ag.index: 0 for ag in agents}
    file_avail: dict[str, int] = defaultdict(int)
    result: dict[int, tuple[int, int]] = {}
    task_end: dict[int, int] = {}

    def earliest_file_start(task: Task) -> int:
        if task.parallel_flag:
            return 0
        best = 0
        for fp in task.file_paths:
            if fp in file_avail:
                best = max(best, file_avail[fp])
        return best

    for i in priority_order:
        task = tasks[i]
        earliest = max(est[i], earliest_file_start(task))
        for pr in pred[i]:
            if pr in task_end:
                earliest = max(earliest, task_end[pr])

        best_a: int | None = None
        best_start = float("inf")
        for a_idx in compat[i]:
            ag = agents[a_idx]
            if task_count[a_idx] >= ag.kappa:
                continue
            if token_used[a_idx] + task.estimated_tokens > ag.context_budget:
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
        token_used[best_a] += task.estimated_tokens
        if not task.parallel_flag:
            for fp in task.file_paths:
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
    total_cost: cp_model.IntVar | None = None


@dataclass
class _ModelVars:
    """Internal bundle of CP-SAT variables produced by :func:`_build_variables`."""

    start: dict[int, cp_model.IntVar]
    end: dict[int, cp_model.IntVar]
    dur: dict[int, cp_model.IntVar]
    x: dict[tuple[int, int], cp_model.IntVar]
    master_iv: dict[tuple[int, int], object]
    ivs_agent: dict[int, list[object]]
    load: dict[int, cp_model.IntVar] = _field(default_factory=dict)
    max_load: cp_model.IntVar | None = None
    makespan: cp_model.IntVar | None = None


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


def _build_variables(
    model: cp_model.CpModel,
    n: int,
    m: int,
    compat: dict[int, list[int]],
    p: dict[tuple[int, int], int],
    min_dur: dict[int, int],
    horizon: int,
) -> _ModelVars:
    """Create all IntVar / BoolVar / IntervalVar objects for the CP-SAT model.

    Posts agent NoOverlap and exactly-one assignment constraints (which are
    variable-creation artefacts).  No other constraints are added here.
    """
    start = {i: model.new_int_var(0, horizon, f"s_{i}") for i in range(n)}
    end = {i: model.new_int_var(0, horizon, f"e_{i}") for i in range(n)}
    dur: dict[int, cp_model.IntVar] = {
        i: model.new_int_var(min_dur[i], max(p[(i, a)] for a in compat[i]), f"d_{i}")
        for i in range(n)
    }
    master_iv: dict[int, object] = {
        i: model.new_interval_var(start[i], dur[i], end[i], f"iv_{i}") for i in range(n)
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
    return _ModelVars(
        start=start,
        end=end,
        dur=dur,
        x=x,
        master_iv=master_iv,
        ivs_agent=ivs_agent,
    )


def _add_precedence_constraints(
    model: cp_model.CpModel,
    vars_: _ModelVars,
    edges: list[tuple[int, int]],
) -> None:
    """Post end[i] <= start[j] for every DAG precedence edge (i, j)."""
    for i, j in edges:
        model.add(vars_.end[i] <= vars_.start[j])


def _add_resource_constraints(
    model: cp_model.CpModel,
    vars_: _ModelVars,
    agents: list[Agent],
    tasks: list[Task],
    file_conflicts: dict[str, list[int]],
) -> None:
    """Post file-mutex NoOverlap, per-agent κ cap, and context-budget cap."""
    n = len(tasks)
    for task_indices in file_conflicts.values():
        if len(task_indices) > 1:
            model.add_no_overlap([vars_.master_iv[i] for i in task_indices])
    for a in range(len(agents)):
        agent_tasks = [vars_.x[(i, a)] for i in range(n) if (i, a) in vars_.x]
        if agent_tasks:
            model.add(sum(agent_tasks) <= agents[a].kappa)
    for a in range(len(agents)):
        token_terms = [
            tasks[i].estimated_tokens * vars_.x[(i, a)] for i in range(n) if (i, a) in vars_.x
        ]
        if token_terms:
            model.add(sum(token_terms) <= agents[a].context_budget)


def _add_objectives(
    model: cp_model.CpModel,
    vars_: _ModelVars,
    agents: list[Agent],
    p: dict[tuple[int, int], int],
    config: SolverConfig,
    horizon: int,
) -> None:
    """Define load vars, makespan var, and symmetry-breaking constraints.

    Results are stored back onto *vars_* (``load``, ``max_load``, ``makespan``).
    """
    n_tasks = len(vars_.start)
    m = len(agents)
    load: dict[int, cp_model.IntVar] = {}
    for a in range(m):
        load_terms = [p[(i, a)] * vars_.x[(i, a)] for i in range(n_tasks) if (i, a) in vars_.x]
        load[a] = model.new_int_var(0, horizon, f"L_{a}")
        if load_terms:
            model.add(load[a] == sum(load_terms))
        else:
            model.add(load[a] == 0)
    max_load = model.new_int_var(0, horizon, "Lmax")
    model.add_max_equality(max_load, [load[a] for a in range(m)])
    makespan = model.new_int_var(0, horizon, "Cmax")
    model.add_max_equality(makespan, [vars_.end[i] for i in range(n_tasks)])
    vars_.load = load
    vars_.max_load = max_load
    vars_.makespan = makespan
    if config.symmetry_breaking:
        groups: dict[tuple, list[int]] = defaultdict(list)
        for ag in agents:
            key = (
                tuple(sorted(ag.skills)),
                ag.kappa,
                ag.context_budget,
                ag.speed_factor,
                ag.price_per_1k_tokens,
            )
            groups[key].append(ag.index)
        for group in groups.values():
            if len(group) > 1:
                ordered = sorted(group)
                for k in range(len(ordered) - 1):
                    model.add(load[ordered[k]] >= load[ordered[k + 1]])


def _add_cost_variable(
    model: cp_model.CpModel,
    vars_: _ModelVars,
    tasks: list[Task],
    agents: list[Agent],
) -> cp_model.IntVar:
    """Create a CP-SAT integer variable for total token cost scaled by ``_COST_SCALE``.

    cost[i,a] = round(tokens_i * price_a / 1000 * _COST_SCALE)
    total_cost = sum(cost[i,a] * x[i,a] for all (i,a) in vars_.x)
    """
    cost_terms = []
    max_cost = 0
    for (i, a), x_var in vars_.x.items():
        cost_ia = int(
            round(tasks[i].estimated_tokens * agents[a].price_per_1k_tokens / 1000 * _COST_SCALE)
        )
        if cost_ia > 0:
            cost_terms.append(cost_ia * x_var)
            max_cost += cost_ia
    total_cost = model.new_int_var(0, max(max_cost, 1), "total_cost")
    if cost_terms:
        model.add(total_cost == sum(cost_terms))
    else:
        model.add(total_cost == 0)
    return total_cost


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
    min_horizon: int = 0,
) -> ModelBundle:
    """Orchestrate variable creation, constraint posting, and objective setup."""
    n = len(tasks)
    m = len(agents)
    model = cp_model.CpModel()
    horizon = max(
        min_horizon,
        _horizon(n, edges, agents, min_dur, file_conflicts, config.horizon_multiplier, graph=graph),
    )
    vars_ = _build_variables(model, n, m, compat, p, min_dur, horizon)
    _add_precedence_constraints(model, vars_, edges)
    _add_resource_constraints(model, vars_, agents, tasks, file_conflicts)
    _add_objectives(model, vars_, agents, p, config, horizon)
    assert vars_.max_load is not None  # guaranteed by _add_objectives
    assert vars_.makespan is not None  # guaranteed by _add_objectives
    total_cost = None
    if config.objective == "cost_aware":
        total_cost = _add_cost_variable(model, vars_, tasks, agents)
    return ModelBundle(
        model=model,
        start=vars_.start,
        end=vars_.end,
        dur=vars_.dur,
        x=vars_.x,
        load=vars_.load,
        max_load=vars_.max_load,
        makespan=vars_.makespan,
        horizon=horizon,
        total_cost=total_cost,
    )


def _apply_fixed_constraints(
    bundle: ModelBundle,
    fixed_constraints: dict[int, tuple[int, int]],
    compat: dict[int, list[int]],
) -> None:
    """Pin start time and agent assignment for each task in fixed_constraints."""
    for i, (a_fixed, s_fixed) in fixed_constraints.items():
        bundle.model.add(bundle.start[i] == s_fixed)
        for a in compat.get(i, []):
            if (i, a) in bundle.x:
                bundle.model.add(bundle.x[(i, a)] == (1 if a == a_fixed else 0))


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


class _AnytimeCallback(cp_model.CpSolverSolutionCallback):
    """Records each improving incumbent during an anytime solve."""

    def __init__(self, t0: float) -> None:
        super().__init__()
        self._t0 = t0
        self.intermediates: list[dict] = []

    def on_solution_callback(self) -> None:
        obj = self.objective_value
        bound = self.best_objective_bound
        elapsed = time.time() - self._t0
        gap = abs(obj - bound) / max(1e-9, abs(obj)) if obj != 0 else 0.0
        self.intermediates.append(
            {
                "makespan": int(obj),
                "time": round(elapsed, 3),
                "gap": round(gap, 6),
            }
        )


def _compute_gap(solver: cp_model.CpSolver) -> float:
    obj = solver.objective_value
    bound = solver.best_objective_bound
    if abs(obj) < 1e-9:
        return 0.0
    return round(abs(obj - bound) / abs(obj), 6)


def _run_solver(
    model: cp_model.CpModel,
    config: SolverConfig,
    callback: cp_model.CpSolverSolutionCallback | None = None,
) -> tuple[cp_model.CpSolver, int, float]:
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = config.num_workers
    solver.parameters.max_time_in_seconds = config.time_limit
    solver.parameters.log_search_progress = False
    t0 = time.time()
    status = solver.solve(model, callback) if callback is not None else solver.solve(model)
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
    for i, task in enumerate(tasks):
        assigned = None
        for a in compat[i]:
            if (i, a) in bundle.x and solver.value(bundle.x[(i, a)]):
                assigned = a
                break
        assignments.append(
            {
                "task_id": task.id,
                "task_index": i,
                "agent_id": agents[assigned].id if assigned is not None else "unassigned",
                "agent_index": assigned,
                "start": solver.value(bundle.start[i]),
                "end": solver.value(bundle.end[i]),
                "duration": solver.value(bundle.dur[i]),
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
        [tasks[s].id, tasks[d].id] for s, d in graph.edges if (s, d) not in parser_edge_set
    ]
    path_edges: list[list[str]] = [
        [tasks[s].id, tasks[d].id] for s, d in zip(chain, chain[1:], strict=False)
    ]
    return path_ids, resource_edges, path_edges


def _build_agent_summary(
    solver: cp_model.CpSolver,
    bundle: ModelBundle,
    assignments: list[dict],
    agents: list[Agent],
) -> list[dict]:
    summary = []
    for ag in agents:
        a_tasks = [task for task in assignments if task["agent_index"] == ag.index]
        total_tokens = sum(task["tokens"] for task in a_tasks)
        cost = round(total_tokens * ag.price_per_1k_tokens / 1000, 4)
        row: dict = {
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
    fixed_constraints: dict[int, tuple[int, int]] | None = None,
) -> dict:
    min_horizon = 0
    if fixed_constraints:
        for i, (a_fixed, s_fixed) in fixed_constraints.items():
            if i < 0 or i >= len(tasks) or a_fixed < 0 or a_fixed >= len(agents):
                raise ScheduleInputError(t("replan_fixed_missing"))
            if a_fixed not in compat.get(i, []) or (i, a_fixed) not in p:
                raise ScheduleInputError(
                    t("replan_fixed_incompatible", task_id=tasks[i].id, agent_id=agents[a_fixed].id)
                )
            min_horizon = max(min_horizon, s_fixed + p[(i, a_fixed)])

    bundle = build_model(
        tasks,
        edges,
        agents,
        compat,
        p,
        min_dur,
        file_conflicts,
        config,
        graph=graph,
        min_horizon=min_horizon,
    )
    stats: dict = {"horizon": bundle.horizon}

    if fixed_constraints:
        _apply_fixed_constraints(bundle, fixed_constraints, compat)

    if hints and config.warm_start:
        _apply_hints(bundle, hints, tasks, compat)

    if config.objective == "weighted":
        bundle.model.minimize(config.makespan_weight * bundle.makespan + bundle.max_load)
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
            solver,
            bundle,
            tasks,
            edges,
            agents,
            compat,
            stats,
            status,
            warnings,
            phase2=False,
        )

    if config.objective == "cost_aware":
        assert bundle.total_cost is not None, "cost_aware requires total_cost variable in bundle"
        return _solve_cost_aware(bundle, tasks, edges, agents, compat, config, stats, warnings)

    # Lexicographic (default): Phase 1 minimises makespan.
    return _solve_lexicographic(bundle, tasks, edges, agents, compat, config, stats, warnings)


def _solve_phase1_makespan(
    bundle: ModelBundle,
    config: SolverConfig,
    stats: dict,
    warnings: WarningCollector,
) -> tuple[cp_model.CpSolver, int, _AnytimeCallback | None]:
    """Phase 1 shared by lexicographic and cost_aware: minimise makespan."""
    bundle.model.minimize(bundle.makespan)
    callback: _AnytimeCallback | None = None
    if config.anytime:
        callback = _AnytimeCallback(time.time())
    solver1, status1, elapsed1 = _run_solver(bundle.model, config, callback=callback)
    stats["phase1_time"] = round(elapsed1, 2)
    stats["phase1_status"] = solver1.status_name(status1)
    if callback is not None:
        stats["intermediate"] = callback.intermediates
    if status1 == cp_model.FEASIBLE and callback is not None:
        stats["final_gap"] = _compute_gap(solver1)
        warnings.add("anytime_timeout", t("anytime_timeout"))
    return solver1, status1, callback


def _rehint_from(
    bundle: ModelBundle,
    solver: cp_model.CpSolver,
    tasks: list[Task],
    compat: dict[int, list[int]],
) -> None:
    """Seed the next phase with variable values from a previous solver."""
    bundle.model.clear_hints()
    for i in range(len(tasks)):
        bundle.model.add_hint(bundle.start[i], solver.value(bundle.start[i]))
        bundle.model.add_hint(bundle.end[i], solver.value(bundle.end[i]))
        for a in compat[i]:
            if (i, a) in bundle.x:
                bundle.model.add_hint(bundle.x[(i, a)], solver.value(bundle.x[(i, a)]))


def _solve_lexicographic(
    bundle: ModelBundle,
    tasks: list[Task],
    edges: list[tuple[int, int]],
    agents: list[Agent],
    compat: dict[int, list[int]],
    config: SolverConfig,
    stats: dict,
    warnings: WarningCollector,
) -> dict:
    solver1, status1, _cb = _solve_phase1_makespan(bundle, config, stats, warnings)

    if status1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {
            "status": "INFEASIBLE",
            "message": "Phase 1 found no feasible schedule.",
            "stats": stats,
            "warnings": warnings.as_list(),
        }

    ms_star = solver1.value(bundle.makespan)
    stats["makespan_phase1"] = ms_star

    # Phase 2: freeze makespan, minimise max load.
    bundle.model.add(bundle.makespan <= ms_star)
    bundle.model.clear_objective()
    _rehint_from(bundle, solver1, tasks, compat)
    bundle.model.minimize(bundle.max_load)

    solver2, status2, elapsed2 = _run_solver(bundle.model, config)
    stats["phase2_time"] = round(elapsed2, 2)
    stats["phase2_status"] = solver2.status_name(status2)

    if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        solver_final = solver2
        final_status = status2
    else:
        warnings.add("phase2_fallback", t("phase2_fallback"))
        solver_final = solver1
        final_status = status1

    return _finalize_result(
        solver_final,
        bundle,
        tasks,
        edges,
        agents,
        compat,
        stats,
        final_status,
        warnings,
        phase2=True,
    )


def _solve_cost_aware(
    bundle: ModelBundle,
    tasks: list[Task],
    edges: list[tuple[int, int]],
    agents: list[Agent],
    compat: dict[int, list[int]],
    config: SolverConfig,
    stats: dict,
    warnings: WarningCollector,
) -> dict:
    """Lexicographic lex(makespan, cost, max_load) three-phase solve."""
    assert bundle.total_cost is not None

    solver1, status1, _cb = _solve_phase1_makespan(bundle, config, stats, warnings)

    if status1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {
            "status": "INFEASIBLE",
            "message": "Phase 1 found no feasible schedule.",
            "stats": stats,
            "warnings": warnings.as_list(),
        }

    ms_star = solver1.value(bundle.makespan)
    stats["makespan_phase1"] = ms_star

    # Phase 2: freeze makespan, minimise cost.
    bundle.model.add(bundle.makespan <= ms_star)
    bundle.model.clear_objective()
    _rehint_from(bundle, solver1, tasks, compat)
    bundle.model.minimize(bundle.total_cost)

    solver2, status2, elapsed2 = _run_solver(bundle.model, config)
    stats["phase2_time"] = round(elapsed2, 2)
    stats["phase2_status"] = solver2.status_name(status2)

    if status2 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        warnings.add("phase2_fallback", t("phase2_fallback"))
        return _finalize_result(
            solver1, bundle, tasks, edges, agents, compat, stats, status1, warnings, phase2=False
        )

    cost_star = solver2.value(bundle.total_cost)

    # Phase 3: freeze cost, minimise max_load.
    bundle.model.add(bundle.total_cost <= cost_star)
    bundle.model.clear_objective()
    _rehint_from(bundle, solver2, tasks, compat)
    bundle.model.minimize(bundle.max_load)

    solver3, status3, elapsed3 = _run_solver(bundle.model, config)
    stats["phase3_time"] = round(elapsed3, 2)
    stats["phase3_status"] = solver3.status_name(status3)

    if status3 in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        solver_final = solver3
        final_status = status3
    else:
        warnings.add("phase2_fallback", t("phase2_fallback"))
        solver_final = solver2
        final_status = status2

    return _finalize_result(
        solver_final,
        bundle,
        tasks,
        edges,
        agents,
        compat,
        stats,
        final_status,
        warnings,
        phase2=True,
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
# Top-level entry points
# ───────────────────────────────────────────────────────────────────────


def solve_with_fixed(
    data: dict,
    fixed_assignments: dict[str, dict],
    prior_hints: dict[str, dict] | None = None,
) -> dict:
    """Solve with selected tasks pinned to a prior assignment.

    fixed_assignments: {task_id: {"agent_id": str, "start": int}}
        Tasks listed here receive equality constraints on start time and agent.
    prior_hints: {task_id: {"agent_id": str, "start": int}}
        Non-fixed tasks are seeded with these values for fast convergence.
        When None and warm_start is enabled, falls back to the heuristic.
    """
    validate_solver_input(data)
    tasks, edges, agents, config = _parse_input(data)

    _raise_if_cycle(tasks, edges)

    warnings = WarningCollector()
    for w in data.get("warnings", []) or []:
        warnings.add(
            w.get("code", "upstream"),
            w.get("message", ""),
            **w.get("context", {}),
        )

    preflight_checks(tasks, agents, warnings)

    compat = compute_compatible_agents(tasks, agents)
    p = compute_durations(tasks, agents, config.token_unit, config.stochastic_quantile)
    min_dur = compute_min_durations(len(tasks), compat, p)
    file_conflicts = build_file_conflict_groups(tasks)
    precedence_graph = _precedence_graph(len(tasks), edges, min_dur)

    task_id_to_idx = {t.id: t.index for t in tasks}
    agent_id_to_idx = {a.id: a.index for a in agents}

    fixed_constraints: dict[int, tuple[int, int]] = {}
    for task_id, assn in fixed_assignments.items():
        i = task_id_to_idx.get(task_id)
        a = agent_id_to_idx.get(assn.get("agent_id", ""))
        if i is None or a is None:
            raise ScheduleInputError(
                t(
                    "replan_fixed_missing_assignment",
                    task_id=task_id,
                    agent_id=assn.get("agent_id", ""),
                )
            )
        if a not in compat.get(i, []):
            raise ScheduleInputError(
                t("replan_fixed_incompatible", task_id=task_id, agent_id=assn.get("agent_id", ""))
            )
        fixed_constraints[i] = (a, assn["start"])

    hints: dict[int, tuple[int, int]] | None = None
    if config.warm_start:
        if prior_hints:
            hints = {}
            fixed_task_ids = set(fixed_assignments.keys())
            for task_id, assn in prior_hints.items():
                if task_id in fixed_task_ids:
                    continue
                i = task_id_to_idx.get(task_id)
                a = agent_id_to_idx.get(assn.get("agent_id", ""))
                if i is not None and a is not None and a in compat.get(i, []):
                    hints[i] = (a, assn["start"])
        else:
            hints = list_schedule_heuristic(
                tasks,
                edges,
                agents,
                compat,
                p,
                min_dur,
                file_conflicts,
                graph=precedence_graph,
            )

    result = solve(
        tasks,
        edges,
        agents,
        compat,
        p,
        min_dur,
        file_conflicts,
        config,
        warnings,
        hints,
        graph=precedence_graph,
        fixed_constraints=fixed_constraints if fixed_constraints else None,
    )

    result["stats"]["quantile_used"] = config.stochastic_quantile
    result["edges"] = [[tasks[s].id, tasks[d].id] for s, d in edges]
    result["tasks"] = [
        {
            "id": task.id,
            "phase": task.phase,
            "story_id": task.story_id,
            "story_priority": task.story_priority,
            "required_skill": task.required_skill,
        }
        for task in tasks
    ]
    return result


def solve_from_json(data: dict) -> dict:
    """Validate, build, solve. Returns the full result envelope."""
    validate_solver_input(data)
    tasks, edges, agents, config = _parse_input(data)

    _raise_if_cycle(tasks, edges)

    warnings = WarningCollector()
    for w in data.get("warnings", []) or []:
        warnings.add(
            w.get("code", "upstream"),
            w.get("message", ""),
            **w.get("context", {}),
        )

    preflight_checks(tasks, agents, warnings)

    compat = compute_compatible_agents(tasks, agents)
    p = compute_durations(tasks, agents, config.token_unit, config.stochastic_quantile)
    min_dur = compute_min_durations(len(tasks), compat, p)
    file_conflicts = build_file_conflict_groups(tasks)
    precedence_graph = _precedence_graph(len(tasks), edges, min_dur)

    hints = None
    if config.warm_start:
        hints = list_schedule_heuristic(
            tasks,
            edges,
            agents,
            compat,
            p,
            min_dur,
            file_conflicts,
            graph=precedence_graph,
        )

    result = solve(
        tasks,
        edges,
        agents,
        compat,
        p,
        min_dur,
        file_conflicts,
        config,
        warnings,
        hints,
        graph=precedence_graph,
    )

    result["stats"]["quantile_used"] = config.stochastic_quantile
    result["edges"] = [[tasks[s].id, tasks[d].id] for s, d in edges]
    result["tasks"] = [
        {
            "id": task.id,
            "phase": task.phase,
            "story_id": task.story_id,
            "story_priority": task.story_priority,
            "required_skill": task.required_skill,
        }
        for task in tasks
    ]
    return result


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="scheduler.py",
        description="Solve a multi-agent schedule from parser JSON on stdin.",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument(
        "--anytime",
        action="store_true",
        default=False,
        help="Enable anytime mode: return best incumbent on timeout with gap stats.",
    )
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

    if args.anytime:
        cfg = data.get("config") or {}
        cfg["anytime"] = True
        data["config"] = cfg

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
