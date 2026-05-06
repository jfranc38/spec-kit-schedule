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
from collections import defaultdict
from statistics import NormalDist
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "solver"  # noqa: A001

import networkx as nx
from ortools.sat.python import cp_model

from .defaults import (
    ANYTIME_DEFAULT,
    CONTEXT_BUDGET_KTOKENS_DEFAULT,
    COST_WEIGHT_DEFAULT,
    HORIZON_MULTIPLIER,
    KAPPA_DEFAULT,
    MAKESPAN_WEIGHT,
    NUM_WORKERS,
    OBJECTIVE,
    OBJECTIVE_COST_AWARE,
    OBJECTIVE_WEIGHTED,
    RANDOM_SEED_DEFAULT,
    SPEED_FACTOR_DEFAULT,
    STATUS_INFEASIBLE,
    STOCHASTIC_QUANTILE_DEFAULT,
    STORY_PRIORITY_DEFAULT,
    TIME_LIMIT_SECONDS,
    TOKEN_ESTIMATES,
    TOKEN_UNIT,
)
from .i18n import t
from .model.build import (
    _prepare_solve_inputs,
    _PreparedInputs,
    _symmetry_classes,
    build_model,
)
from .model.fixed import resolve_fixed_duration
from .model.types import Agent, Durations, SolverConfig, Task
from .orchestration import runner
from .orchestration.cost_aware import _solve_cost_aware
from .orchestration.lex import _solve_lexicographic
from .result.extract import (
    _apply_fixed_constraints,
    _apply_hints,
    _build_node_weighted_graph,
    _finalize_result,
    _node_weighted_longest_path_length,
)
from .validation import (
    ScheduleInputError,
    find_cycle,
)
from .warnings_collector import WarningCollector

__all__ = ["solve_from_json", "solve_with_fixed", "main"]

log = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────
# Parser
# ───────────────────────────────────────────────────────────────────────


def _parse_input(
    data: dict[str, Any],
) -> tuple[list[Task], list[tuple[int, int]], list[Agent], SolverConfig]:
    # Bound matches solver.config_schema._MAX_TOKENS — keep cumulative scaled
    # cost (n × tokens × price × _COST_SCALE) inside int64 even when callers
    # bypass schema validation by handing dicts straight to ``solve_from_json``.
    from .config_schema import _MAX_TOKENS

    tasks: list[Task] = []
    id_to_idx: dict[str, int] = {}
    for i, t_raw in enumerate(data["tasks"]):
        tokens = int(t_raw.get("estimated_tokens", TOKEN_ESTIMATES["medium"]))
        if tokens <= 0 or tokens > _MAX_TOKENS:
            raise ScheduleInputError(
                f"task {t_raw.get('id', f'index {i}')!r}.estimated_tokens={tokens} "
                f"out of range (1..{_MAX_TOKENS})"
            )
        task = Task(
            id=t_raw["id"],
            phase=t_raw.get("phase", "Setup"),
            story_id=t_raw.get("story_id"),
            story_priority=int(t_raw.get("story_priority", STORY_PRIORITY_DEFAULT)),
            parallel_flag=bool(t_raw.get("parallel_flag", False)),
            file_paths=list(t_raw.get("file_paths", [])),
            required_skill=t_raw.get("required_skill", "backend"),
            estimated_tokens=tokens,
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
        cost_weight=int(cfg.get("cost_weight", COST_WEIGHT_DEFAULT)),
        time_limit=int(cfg.get("time_limit", TIME_LIMIT_SECONDS)),
        num_workers=int(cfg.get("num_workers", NUM_WORKERS)),
        symmetry_breaking=bool(cfg.get("symmetry_breaking", True)),
        warm_start=bool(cfg.get("warm_start", True)),
        horizon_multiplier=float(cfg.get("horizon_multiplier", HORIZON_MULTIPLIER)),
        token_unit=int(cfg.get("token_unit", TOKEN_UNIT)),
        stochastic_quantile=float(cfg.get("stochastic_quantile", STOCHASTIC_QUANTILE_DEFAULT)),
        anytime=bool(cfg.get("anytime", ANYTIME_DEFAULT)),
        random_seed=int(cfg.get("random_seed", RANDOM_SEED_DEFAULT)),
        verbose=bool(cfg.get("verbose", False)),
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
    """Quantile of Normal(mean, std_dev) with left-truncation at 0.

    ``q`` must lie in the open interval ``(0, 1)``. ``Φ⁻¹(0)`` and
    ``Φ⁻¹(1)`` are unbounded and ``statistics.NormalDist.inv_cdf`` raises
    ``StatisticsError`` for them. The schema permits the closed interval
    for backwards compatibility, so we re-validate here at the boundary
    that actually matters for the math.
    """
    if not (0.0 < q < 1.0):
        raise ScheduleInputError(
            f"stochastic_quantile must be in (0, 1); got {q}. "
            "0 and 1 produce unbounded quantiles."
        )
    return max(0.0, NormalDist(mu=mean, sigma=std_dev).inv_cdf(q))


def compute_durations(
    tasks: list[Task],
    agents: list[Agent],
    token_unit: int,
    stochastic_quantile: float = STOCHASTIC_QUANTILE_DEFAULT,
) -> Durations:
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
    return Durations(p)


def compute_min_durations(
    n: int,
    compat: dict[int, list[int]],
    p: Durations,
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


def _precedence_graph(
    n: int,
    edges: list[tuple[int, int]],
    min_dur: dict[int, int],
) -> nx.DiGraph:
    """Precedence DAG weighted by per-task minimum duration."""
    return _build_node_weighted_graph(range(n), edges, min_dur)


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
    p: Durations,
    min_dur: dict[int, int],
    file_conflicts: dict[str, list[int]],
    *,
    graph: nx.DiGraph | None = None,
) -> dict[int, tuple[int, int]]:
    """Greedy list scheduling that respects every hard constraint.

    The heuristic mirrors the CP-SAT model constraints (precedence, κ, C,
    file-mutex, symmetry-class load ordering) so every hint it produces is
    already feasible; CP-SAT can then start from a valid incumbent instead
    of discarding the hint.

    When a task has no agent with spare κ AND budget AND skills, it is
    OMITTED from the returned dict rather than pinned to an arbitrary
    compatible agent. CP-SAT supports partial hints, and an infeasible
    pin would be silently discarded anyway — dropping the task keeps the
    rest of the warm-start valid.
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

    class_of = _symmetry_classes(agents)
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

        # Symmetry-canonical: pick the lowest-index feasible peer per class
        # to keep the hint consistent with ``L_a >= L_{a'}`` posted in C12.
        best_a: int | None = None
        best_start = float("inf")
        seen_class_feasible: set[int] = set()
        for a_idx in compat[i]:  # ascending agent index
            cls = class_of[a_idx]
            if cls in seen_class_feasible:
                continue
            ag = agents[a_idx]
            if task_count[a_idx] >= ag.kappa:
                continue
            if token_used[a_idx] + task.estimated_tokens > ag.context_budget:
                continue
            seen_class_feasible.add(cls)
            start = max(earliest, agent_avail[a_idx])
            if start < best_start:
                best_start = start
                best_a = a_idx

        if best_a is None:
            # Partial hints: omit instead of pinning — CP-SAT silently drops infeasible hints.
            continue

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
# Orchestration
# ───────────────────────────────────────────────────────────────────────


def solve(
    tasks: list[Task],
    edges: list[tuple[int, int]],
    agents: list[Agent],
    compat: dict[int, list[int]],
    p: Durations,
    min_dur: dict[int, int],
    file_conflicts: dict[str, list[int]],
    config: SolverConfig,
    warnings: WarningCollector,
    hints: dict[int, tuple[int, int]] | None = None,
    *,
    graph: nx.DiGraph | None = None,
    fixed_constraints: dict[int, tuple[int, int, int]] | None = None,
    heuristic_makespan: int | None = None,
) -> dict[str, Any]:
    # Return shape is documented by ``solver.model.result_types.ScheduleResult``
    # (TypedDict, ``total=False``). The runtime annotation stays as
    # ``dict[str, Any]`` because mypy's TypedDict variance rules block
    # passing the literal envelopes ({"status": "INFEASIBLE", "stats": ...,
    # "warnings": ...}) through without unsafe narrowing — the inner
    # ``stats`` dict is built with ``dict[str, Any]`` and rejected against
    # the ``Stats`` TypedDict slot. Keep ``ScheduleResult`` as a schema
    # doc until ``_finalize_result`` and downstream warning records adopt
    # the typed shape end-to-end.
    min_horizon = 0
    if fixed_constraints:
        for i, (a_fixed, s_fixed, d_fixed) in fixed_constraints.items():
            if i < 0 or i >= len(tasks) or a_fixed < 0 or a_fixed >= len(agents):
                raise ScheduleInputError(t("replan_fixed_missing"))
            if a_fixed not in compat.get(i, []) or (i, a_fixed) not in p:
                raise ScheduleInputError(
                    t("replan_fixed_incompatible", task_id=tasks[i].id, agent_id=agents[a_fixed].id)
                )
            min_horizon = max(min_horizon, s_fixed + d_fixed)
            # Realign ``p[(i, a_fixed)]`` to the externally-pinned duration so
            # the duration-channel constraint posted in ``_build_variables``
            # (``dur[i] == p[i,a]`` only_enforce_if x[i,a]) agrees with the
            # ``dur[i] == d_fixed`` pin added later in ``_apply_fixed_constraints``.
            # Without this, a recalibration that changes ``p[i, a_fixed]`` (e.g.
            # speed_factor / token_unit drift) makes the model INFEASIBLE
            # whenever ``d_fixed != p[i, a_fixed]``. Update ``min_dur[i]`` too
            # so the heuristic's ESTs and the horizon estimator stay consistent.
            p[(i, a_fixed)] = d_fixed
            min_dur[i] = min(p[(i, a)] for a in compat[i])
        # Frozen tasks may have been re-priced; the heuristic makespan was
        # computed against the old ``p[i, a_fixed]`` and is no longer a valid
        # UB.  Drop it rather than fight a stale bound.
        heuristic_makespan = None

    log.info(
        "solve start: n=%d m=%d horizon_seed=%d hints=%d",
        len(tasks),
        len(agents),
        min_horizon,
        len(hints) if hints else 0,
    )
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
        heuristic_makespan=heuristic_makespan,
    )
    stats: dict[str, Any] = {"horizon": bundle.horizon}
    if heuristic_makespan is not None:
        stats["heuristic_makespan"] = heuristic_makespan

    if fixed_constraints:
        _apply_fixed_constraints(bundle, fixed_constraints, compat)

    if hints and config.warm_start:
        _apply_hints(bundle, hints, compat, p=p, n_agents=len(agents))
        log.info("warm-start: hinted %d task placements", len(hints))

    # Weighted is the only single-phase objective and is short enough to inline.
    # Lex/cost_aware delegate to dedicated orchestration modules below.
    if config.objective == OBJECTIVE_WEIGHTED:
        bundle.model.minimize(config.makespan_weight * bundle.makespan + bundle.max_load)
        solver, status, elapsed = runner._run_solver(bundle.model, config)
        stats["solve_time"] = round(elapsed, 2)
        runner._record_phase_status(stats, 1, solver, status)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return {
                "status": STATUS_INFEASIBLE,
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
        )

    if config.objective == OBJECTIVE_COST_AWARE:
        assert bundle.total_cost is not None, "cost_aware requires total_cost variable in bundle"
        return _solve_cost_aware(bundle, tasks, edges, agents, compat, config, stats, warnings)

    # Lexicographic (default): Phase 1 minimises makespan.
    return _solve_lexicographic(bundle, tasks, edges, agents, compat, config, stats, warnings)


# ───────────────────────────────────────────────────────────────────────
# Top-level entry points
# ───────────────────────────────────────────────────────────────────────


def _decorate_result(
    result: dict[str, Any],
    prepared: _PreparedInputs,
) -> dict[str, Any]:
    """Decorate ``result`` in place AND return it.

    Appends the trailing ``quantile_used`` / ``edges`` / ``tasks`` block
    shared by :func:`solve_from_json` and :func:`solve_with_fixed`. Both
    call sites immediately return the value, so the mutation is
    effectively unobservable — the dual contract simply spares them an
    explicit ``return result`` after the call.
    """
    tasks = prepared.tasks
    result["stats"]["quantile_used"] = prepared.config.stochastic_quantile
    result["edges"] = [[tasks[s].id, tasks[d].id] for s, d in prepared.edges]
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


def solve_with_fixed(
    data: dict[str, Any],
    fixed_assignments: dict[str, dict[str, Any]],
    prior_hints: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Solve with selected tasks pinned to a prior assignment.

    fixed_assignments: {task_id: {"agent_id": str, "start": int}}
        Tasks listed here receive equality constraints on start time and agent.
    prior_hints: {task_id: {"agent_id": str, "start": int}}
        Non-fixed tasks are seeded with these values for fast convergence.
        When None and warm_start is enabled, falls back to the heuristic.
    """
    prepared = _prepare_solve_inputs(data)
    tasks = prepared.tasks
    agents = prepared.agents
    compat = prepared.compat
    p = prepared.p

    task_id_to_idx = {task.id: task.index for task in tasks}
    agent_id_to_idx = {ag.id: ag.index for ag in agents}

    fixed_constraints: dict[int, tuple[int, int, int]] = {}
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
        # Pin the duration recorded in the prior assignment so calibration
        # changes (speed_factor / token_unit) don't shift the frozen task.
        # ``resolve_fixed_duration`` falls back to the current p[i,a] when
        # the prior record carries neither ``duration`` nor ``end``.
        s_fixed = int(assn["start"])
        d_fixed = resolve_fixed_duration(assn, p[(i, a)], task_id=task_id)
        fixed_constraints[i] = (a, s_fixed, d_fixed)

    if prepared.config.warm_start and prior_hints:
        # Override the heuristic-derived hints with the supplied prior assignments,
        # filtered to non-fixed, skill-compatible (task, agent) pairs.
        overridden: dict[int, tuple[int, int]] = {}
        fixed_task_ids = set(fixed_assignments.keys())
        for task_id, assn in prior_hints.items():
            if task_id in fixed_task_ids:
                continue
            i = task_id_to_idx.get(task_id)
            a = agent_id_to_idx.get(assn.get("agent_id", ""))
            if i is not None and a is not None and a in compat.get(i, []):
                overridden[i] = (a, assn["start"])
        prepared.hints = overridden

    result = solve(
        tasks,
        prepared.edges,
        agents,
        compat,
        p,
        prepared.min_dur,
        prepared.file_conflicts,
        prepared.config,
        prepared.warnings,
        prepared.hints,
        graph=prepared.graph,
        fixed_constraints=fixed_constraints if fixed_constraints else None,
        heuristic_makespan=prepared.heuristic_makespan,
    )

    return _decorate_result(result, prepared)


def solve_from_json(data: dict[str, Any]) -> dict[str, Any]:
    """Validate, build, solve. Returns the full result envelope.

    Shape is documented by :class:`solver.model.result_types.ScheduleResult`
    (see the docstring on ``solve`` for why the runtime annotation stays
    as ``dict[str, Any]``).
    """
    prepared = _prepare_solve_inputs(data)

    result = solve(
        prepared.tasks,
        prepared.edges,
        prepared.agents,
        prepared.compat,
        prepared.p,
        prepared.min_dur,
        prepared.file_conflicts,
        prepared.config,
        prepared.warnings,
        prepared.hints,
        graph=prepared.graph,
        heuristic_makespan=prepared.heuristic_makespan,
    )

    return _decorate_result(result, prepared)


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

    if args.verbose:
        # End-to-end verbose: enables CP-SAT's per-iteration log via
        # ``runner._run_solver`` in addition to flipping Python logging.
        cfg = data.get("config") or {}
        cfg["verbose"] = True
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
