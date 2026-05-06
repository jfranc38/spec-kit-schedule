"""Domain dataclasses shared across the parser, scheduler, and replan flows.

These types describe the inputs to ``solve_from_json`` (and ``solve_with_fixed``);
they are deliberately solver-agnostic and contain no CP-SAT references.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

from ..defaults import (
    ANYTIME_DEFAULT,
    COST_WEIGHT_DEFAULT,
    HORIZON_MULTIPLIER,
    MAKESPAN_WEIGHT,
    NUM_WORKERS,
    OBJECTIVE,
    RANDOM_SEED_DEFAULT,
    STOCHASTIC_QUANTILE_DEFAULT,
    TIME_LIMIT_SECONDS,
    TOKEN_UNIT,
    ObjectiveMode,
)

# Per-(task_index, agent_index) processing time in time units.
# Defined as a NewType so callers cannot accidentally pass an arbitrary
# ``dict[tuple[int, int], int]`` (e.g. a raw token map) where a duration
# table is expected. The runtime value is just a dict; the wrapper is
# free at runtime.
Durations = NewType("Durations", dict[tuple[int, int], int])


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
    objective: ObjectiveMode = OBJECTIVE
    makespan_weight: int = MAKESPAN_WEIGHT
    cost_weight: int = COST_WEIGHT_DEFAULT
    time_limit: int = TIME_LIMIT_SECONDS
    num_workers: int = NUM_WORKERS
    symmetry_breaking: bool = True
    warm_start: bool = True
    horizon_multiplier: float = HORIZON_MULTIPLIER
    token_unit: int = TOKEN_UNIT
    stochastic_quantile: float = STOCHASTIC_QUANTILE_DEFAULT
    anytime: bool = ANYTIME_DEFAULT
    # Seeded for reproducibility across runs of the same model. Override only
    # if you intentionally want exploration-style variance between runs.
    random_seed: int = RANDOM_SEED_DEFAULT
    # When True, CP-SAT's per-iteration search log is forwarded to stderr.
    # Off by default because the log is verbose; turn on with ``--verbose``.
    verbose: bool = False
