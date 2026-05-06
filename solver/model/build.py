"""CP-SAT model construction for spec-kit-schedule.

This module owns variable creation, constraint posting (precedence, resource,
file-mutex, κ, context budget), objective wiring (load, max_load, makespan,
optional cost) and the horizon estimator. The public entry point is
:func:`build_model`, which returns a :class:`ModelBundle`.

It also hosts the small OR-Tools wrappers (:func:`_clear_objective`,
:func:`_clear_hints`), the cost helpers (:func:`_scaled_cost`,
:func:`_cost_signals_underflowed`, :func:`_add_cost_variable`), the
agent-symmetry classifier (:func:`_symmetry_classes`), and the input
preparation pipeline shared by :func:`solve_from_json` and
:func:`solve_with_fixed`.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as _field
from typing import Any

import networkx as nx
from ortools.sat.python import cp_model

from ..defaults import OBJECTIVE_COST_AWARE, TOKENS_PER_KILOTOKEN
from ..validation import ScheduleInputError
from ..warnings_collector import WarningCollector
from .types import Agent, Durations, SolverConfig, Task


def cost_dollars(tokens: int, price_per_1k: float) -> float:
    """Convert ``tokens`` × ``price_per_1k`` into a dollar amount.

    Single source of truth for the per-task cost arithmetic that previously
    appeared in three places (model.build._scaled_cost, the agent summary
    builder, and the HTML renderer). Returns a plain ``float`` to match the
    historical JSON output.
    """
    return tokens * price_per_1k / TOKENS_PER_KILOTOKEN

# Scaling factor that converts dollar costs to integers for CP-SAT.
# Four decimal places of dollar precision: $0.0001 is 1 unit.
_COST_SCALE = 10_000

# Safe headroom under int64 (~9.22e18) for cumulative scaled cost arithmetic
# inside CP-SAT. 2**62 leaves a 2x safety margin for intermediate sums.
_COST_INT64_HEADROOM = 2**62


# ───────────────────────────────────────────────────────────────────────
# OR-Tools helpers
# ───────────────────────────────────────────────────────────────────────


def _clear_objective(model: cp_model.CpModel) -> None:
    # ortools' Python bindings still ship without proper type stubs for
    # these methods; the ignore stays until they do (verified mypy 1.x).
    model.clear_objective()  # type: ignore[no-untyped-call,unused-ignore]


def _clear_hints(model: cp_model.CpModel) -> None:
    # See note on _clear_objective.
    model.clear_hints()  # type: ignore[no-untyped-call,unused-ignore]


# ───────────────────────────────────────────────────────────────────────
# Symmetry classes
# ───────────────────────────────────────────────────────────────────────


def _symmetry_classes(agents: list[Agent]) -> dict[int, int]:
    """Map ``agent_index → class_id`` for permutation-equivalent agents.

    Two agents share a class when they have identical skill sets, κ, context
    budget, speed factor and price. Used to keep the warm-start hint aligned
    with the symmetry-breaking constraint posted in :func:`_add_objectives`
    (``L_a >= L_{a'}`` for ``a < a'`` within the same class).
    """
    classes: dict[tuple[Any, ...], int] = {}
    out: dict[int, int] = {}
    for ag in agents:
        key = (
            tuple(sorted(ag.skills)),
            ag.kappa,
            ag.context_budget,
            ag.speed_factor,
            ag.price_per_1k_tokens,
        )
        if key not in classes:
            classes[key] = len(classes)
        out[ag.index] = classes[key]
    return out


# ───────────────────────────────────────────────────────────────────────
# Bundle dataclasses
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
    """Internal bundle of CP-SAT variables produced by :func:`_build_variables`.

    ``ivs_agent`` (the per-agent list of optional interval vars) is consumed
    only inside :func:`_build_variables` to post the agent NoOverlap
    constraints, so it stays as a local there rather than leaking onto this
    bundle for the rest of the build pipeline.
    """

    start: dict[int, cp_model.IntVar]
    end: dict[int, cp_model.IntVar]
    dur: dict[int, cp_model.IntVar]
    x: dict[tuple[int, int], cp_model.IntVar]
    master_iv: dict[int, cp_model.IntervalVar]
    load: dict[int, cp_model.IntVar] = _field(default_factory=dict)


@dataclass
class _HorizonInputs:
    """Bundle of model data consumed by :func:`_horizon`.

    ``heuristic_makespan`` is the makespan of the warm-start heuristic when
    available. The heuristic produces a feasible schedule, so its makespan is
    a valid upper bound on the optimum and a much tighter UB than ``serial_ub``.
    """

    n: int
    edges: list[tuple[int, int]]
    agents: list[Agent]
    min_dur: dict[int, int]
    p: Durations
    compat: dict[int, list[int]]
    file_conflicts: dict[str, list[int]]
    graph: nx.DiGraph | None = None
    heuristic_makespan: int | None = None


@dataclass
class _PreparedInputs:
    """Bundle produced by :func:`_prepare_solve_inputs`.

    Holds everything ``solve_from_json`` and ``solve_with_fixed`` need
    between input validation and the call into :func:`solve`.
    """

    tasks: list[Task]
    edges: list[tuple[int, int]]
    agents: list[Agent]
    config: SolverConfig
    compat: dict[int, list[int]]
    p: Durations
    min_dur: dict[int, int]
    file_conflicts: dict[str, list[int]]
    graph: nx.DiGraph
    warnings: WarningCollector
    hints: dict[int, tuple[int, int]] | None = None
    # Makespan of the warm-start heuristic when it produced a complete
    # schedule. ``None`` whenever ``warm_start`` is off or the heuristic
    # could not place every task.
    heuristic_makespan: int | None = None


# ───────────────────────────────────────────────────────────────────────
# Horizon
# ───────────────────────────────────────────────────────────────────────


def _horizon(inputs: _HorizonInputs, multiplier: float) -> int:
    """Upper bound on any feasible makespan.

    Serial-UB ensures H >= OPT even when LB*multiplier underestimates.

    When the warm-start heuristic ran, its makespan is a strictly tighter
    feasible upper bound than ``serial_ub`` (the heuristic respects every
    hard constraint), so we prefer it. We still keep ``serial_ub`` as a
    fallback for the case where the heuristic returned an empty schedule.
    """
    # Local import sidesteps the model.build ↔ scheduler cycle: scheduler
    # imports ``build_model`` from this module, while ``critical_path_bound``
    # lives in scheduler.py with the heuristic helpers.
    from ..scheduler import critical_path_bound

    if inputs.n == 0:
        return 1
    cp = critical_path_bound(inputs.n, inputs.edges, inputs.min_dur, graph=inputs.graph)
    load_bound = math.ceil(sum(inputs.min_dur.values()) / max(1, len(inputs.agents)))
    mutex_bound = 0
    for idxs in inputs.file_conflicts.values():
        mutex_bound = max(mutex_bound, sum(inputs.min_dur[i] for i in idxs))
    lb_max = max(cp, load_bound, mutex_bound, 1)
    inflated_lb = int(math.ceil(lb_max * multiplier))
    serial_ub = sum(max(inputs.p[(i, a)] for a in inputs.compat[i]) for i in range(inputs.n))
    # Prefer the heuristic UB when present: it's feasible AND typically much
    # tighter than ``serial_ub``. lb_max still has to be respected — we never
    # go below the LB, even if a buggy heuristic returned a smaller value.
    heur_ub = inputs.heuristic_makespan if inputs.heuristic_makespan else serial_ub
    return max(1, lb_max, inflated_lb, min(serial_ub, heur_ub))


# ───────────────────────────────────────────────────────────────────────
# Variables
# ───────────────────────────────────────────────────────────────────────


def _build_variables(
    model: cp_model.CpModel,
    n: int,
    m: int,
    compat: dict[int, list[int]],
    p: Durations,
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
    master_iv: dict[int, cp_model.IntervalVar] = {
        i: model.new_interval_var(start[i], dur[i], end[i], f"iv_{i}") for i in range(n)
    }
    x: dict[tuple[int, int], cp_model.IntVar] = {}
    ivs_agent: dict[int, list[cp_model.IntervalVar]] = defaultdict(list)
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
    )


# ───────────────────────────────────────────────────────────────────────
# Constraints
# ───────────────────────────────────────────────────────────────────────


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
    p: Durations,
    config: SolverConfig,
    horizon: int,
) -> tuple[dict[int, cp_model.IntVar], cp_model.IntVar, cp_model.IntVar]:
    """Define load vars, makespan var, and symmetry-breaking constraints.

    Returns ``(load, max_load, makespan)`` and also stores ``load`` back onto
    *vars_* so downstream helpers (e.g. cost-aware scaling) keep their access
    pattern unchanged.
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
    if config.symmetry_breaking:
        class_of = _symmetry_classes(agents)
        groups: dict[int, list[int]] = defaultdict(list)
        for ag in agents:
            groups[class_of[ag.index]].append(ag.index)
        for group in groups.values():
            if len(group) > 1:
                ordered = sorted(group)
                for k in range(len(ordered) - 1):
                    model.add(load[ordered[k]] >= load[ordered[k + 1]])
    return load, max_load, makespan


# ───────────────────────────────────────────────────────────────────────
# Cost-aware extras
# ───────────────────────────────────────────────────────────────────────


def _scaled_cost(task: Task, agent: Agent) -> int:
    return int(round(cost_dollars(task.estimated_tokens, agent.price_per_1k_tokens) * _COST_SCALE))


def _cost_signals_underflowed(
    tasks: list[Task],
    agents: list[Agent],
    compat: dict[int, list[int]],
) -> bool:
    """Detect partial as well as total cost-scale underflow.

    Returns True whenever ANY (task, compatible-agent) pair has
    ``_scaled_cost == 0`` even though the raw inputs (tokens × price)
    are non-zero. The earlier "all zero" check missed the common
    intermediate case where a few small priced tasks underflow while
    larger ones stay above 0 — leaving the cost objective unable to
    discriminate between the under-flowed agents.
    """
    any_positive_price = any(ag.price_per_1k_tokens > 0 for ag in agents)
    if not any_positive_price:
        return False
    for i, task in enumerate(tasks):
        for a in compat.get(i, []):
            agent = agents[a]
            if (
                task.estimated_tokens * agent.price_per_1k_tokens > 0
                and _scaled_cost(task, agent) == 0
            ):
                return True
    return False


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
        cost_ia = _scaled_cost(tasks[i], agents[a])
        if cost_ia > 0:
            cost_terms.append(cost_ia * x_var)
            max_cost += cost_ia
    # Defensive int64 guard: even with config_schema caps in place, library
    # callers can bypass schema validation. 2**62 leaves comfortable headroom
    # under the int64 ceiling (~9.22e18) for downstream CP-SAT arithmetic.
    if max_cost > _COST_INT64_HEADROOM:
        raise ScheduleInputError(
            "cost-aware total exceeds safe int64 headroom: "
            f"max_cost={max_cost} > {_COST_INT64_HEADROOM}. "
            "Reduce price_per_1k_tokens, estimated_tokens, or task count."
        )
    total_cost = model.new_int_var(0, max(max_cost, 1), "total_cost")
    if cost_terms:
        model.add(total_cost == sum(cost_terms))
    else:
        model.add(total_cost == 0)
    return total_cost


# ───────────────────────────────────────────────────────────────────────
# Top-level model builder
# ───────────────────────────────────────────────────────────────────────


def build_model(
    tasks: list[Task],
    edges: list[tuple[int, int]],
    agents: list[Agent],
    compat: dict[int, list[int]],
    p: Durations,
    min_dur: dict[int, int],
    file_conflicts: dict[str, list[int]],
    config: SolverConfig,
    *,
    graph: nx.DiGraph | None = None,
    min_horizon: int = 0,
    heuristic_makespan: int | None = None,
) -> ModelBundle:
    """Orchestrate variable creation, constraint posting, and objective setup.

    ``heuristic_makespan`` (if known) tightens the horizon UB and is also
    posted as ``makespan <= H_heur`` to give CP-SAT propagation directly.
    """
    n = len(tasks)
    m = len(agents)
    model = cp_model.CpModel()
    horizon = max(
        min_horizon,
        _horizon(
            _HorizonInputs(
                n=n,
                edges=edges,
                agents=agents,
                min_dur=min_dur,
                p=p,
                compat=compat,
                file_conflicts=file_conflicts,
                graph=graph,
                heuristic_makespan=heuristic_makespan,
            ),
            config.horizon_multiplier,
        ),
    )
    vars_ = _build_variables(model, n, m, compat, p, min_dur, horizon)
    _add_precedence_constraints(model, vars_, edges)
    _add_resource_constraints(model, vars_, agents, tasks, file_conflicts)
    load, max_load, makespan = _add_objectives(model, vars_, agents, p, config, horizon)
    # Tighten propagation when we already have a feasible UB from the
    # heuristic. The variable's domain already reflects ``horizon``; this
    # extra constraint lets CP-SAT cut branches where ``makespan`` would
    # exceed the known UB without having to wait for a Phase 1 solution.
    if heuristic_makespan is not None and heuristic_makespan >= 1:
        model.add(makespan <= heuristic_makespan)
    total_cost = None
    if config.objective == OBJECTIVE_COST_AWARE:
        total_cost = _add_cost_variable(model, vars_, tasks, agents)
    return ModelBundle(
        model=model,
        start=vars_.start,
        end=vars_.end,
        dur=vars_.dur,
        x=vars_.x,
        load=load,
        max_load=max_load,
        makespan=makespan,
        horizon=horizon,
        total_cost=total_cost,
    )


# ───────────────────────────────────────────────────────────────────────
# Solve-input preparation pipeline
# ───────────────────────────────────────────────────────────────────────


def _prepare_solve_inputs(
    data: dict[str, Any],
    *,
    validate: bool = True,
) -> _PreparedInputs:
    """Validate, parse, preflight, and pre-compute everything ``solve`` needs.

    Centralises the duplicated front-half of :func:`solve_from_json` and
    :func:`solve_with_fixed`. The default ``hints`` is the warm-start
    heuristic when ``config.warm_start`` is enabled; callers may override
    the field on the returned bundle (e.g. ``solve_with_fixed`` does this
    when prior_hints are supplied).
    """
    # Local imports break the model.build ↔ scheduler cycle: scheduler
    # imports build_model + _PreparedInputs from here, while the parsing,
    # cycle-detection, preflight, compat and heuristic helpers live in
    # scheduler.py with the rest of the high-level pipeline.
    from ..scheduler import (
        _parse_input,
        _precedence_graph,
        _raise_if_cycle,
        build_file_conflict_groups,
        compute_compatible_agents,
        compute_durations,
        compute_min_durations,
        list_schedule_heuristic,
        preflight_checks,
    )
    from ..validation import validate_solver_input

    if validate:
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
    p: Durations = compute_durations(
        tasks, agents, config.token_unit, config.stochastic_quantile
    )
    min_dur = compute_min_durations(len(tasks), compat, p)
    file_conflicts = build_file_conflict_groups(tasks)
    precedence_graph = _precedence_graph(len(tasks), edges, min_dur)

    hints: dict[int, tuple[int, int]] | None = None
    heuristic_makespan: int | None = None
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
        # Compute the heuristic's makespan when it scheduled every task.
        # A partial heuristic schedule (some task dropped because no agent
        # had spare κ) cannot be used as a global UB.
        if hints and len(hints) == len(tasks):
            heuristic_makespan = max(
                (s_hint + p[(i, a_hint)] for i, (a_hint, s_hint) in hints.items()),
                default=0,
            )

    return _PreparedInputs(
        tasks=tasks,
        edges=edges,
        agents=agents,
        config=config,
        compat=compat,
        p=p,
        min_dur=min_dur,
        file_conflicts=file_conflicts,
        graph=precedence_graph,
        warnings=warnings,
        hints=hints,
        heuristic_makespan=heuristic_makespan,
    )
