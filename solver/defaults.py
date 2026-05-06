"""Single source of truth for solver defaults.

Any caller that reads a user-supplied config should merge these as the
baseline. Duplicating these literals in other modules has caused config
and parser defaults to drift in the past — do not reintroduce that.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType
from typing import Any, Final, Literal

__all__ = [
    "TOKEN_ESTIMATES",
    "COMPLEXITY_VERBS",
    "DEFAULT_SKILL",
    "TOKEN_UNIT",
    "TOKENS_PER_KILOTOKEN",
    "HORIZON_MULTIPLIER",
    "TIME_LIMIT_SECONDS",
    "NUM_WORKERS",
    "KAPPA_DEFAULT",
    "CONTEXT_BUDGET_KTOKENS_DEFAULT",
    "SPEED_FACTOR_DEFAULT",
    "MAKESPAN_WEIGHT",
    "COST_WEIGHT_DEFAULT",
    "OBJECTIVE",
    "OBJECTIVE_LEXICOGRAPHIC",
    "OBJECTIVE_WEIGHTED",
    "OBJECTIVE_COST_AWARE",
    "ObjectiveMode",
    "STATUS_OPTIMAL",
    "STATUS_FEASIBLE",
    "STATUS_INFEASIBLE",
    "STATUS_UNKNOWN",
    "STOCHASTIC_QUANTILE_DEFAULT",
    "ANYTIME_DEFAULT",
    "RANDOM_SEED_DEFAULT",
    "STORY_PRIORITY_DEFAULT",
    "AGENT_COLORS",
    "CRITICAL_COLOR",
    "CRITICAL_HATCH",
    "palette_for",
]


# Agent palette shared by Mermaid rendering and matplotlib visualisation
# so both outputs agree visually when viewed side-by-side.
#
# Critical-path red (`#D0021B`) is intentionally EXCLUDED from this
# palette: the visualiser uses it for the chain highlight, and a bar
# whose fill is the same red as its critical border would be
# indistinguishable from a non-critical bar on the same agent.
AGENT_COLORS = (
    "#4A90D9",  # blue
    "#7BC67E",  # green
    "#F5A623",  # amber
    "#9B59B6",  # purple
    "#1ABC9C",  # teal
    "#E67E22",  # orange
    "#8E44AD",  # violet
    "#3498DB",  # sky
)
CRITICAL_COLOR = "#D0021B"
# Redundant visual cue used on top of colour so critical bars/nodes stay
# distinguishable even on colour-blind displays or when the renderer
# ignores edge colours (older Mermaid, some SVG viewers).
CRITICAL_HATCH = "///"


TOKEN_ESTIMATES: dict[str, int] = dict(
    MappingProxyType(
        {
            "simple": 1500,
            "medium": 3500,
            "complex": 6000,
            "review": 2000,
        }
    )
)

COMPLEXITY_VERBS: dict[str, list[str]] = {
    "simple": [
        "add",
        "update",
        "rename",
        "move",
        "import",
        "export",
        "configure",
        "adds",
        "updates",
        "renames",
        "moves",
        "imports",
        "exports",
        "configures",
        "adding",
        "updating",
        "renaming",
        "moving",
        "importing",
        "exporting",
        "configuring",
    ],
    "medium": [
        "implement",
        "create",
        "write",
        "build",
        "refactor",
        "implements",
        "creates",
        "writes",
        "builds",
        "refactors",
        "implementing",
        "creating",
        "writing",
        "building",
        "refactoring",
    ],
    "complex": [
        "design",
        "architect",
        "integrate",
        "migrate",
        "optimize",
        "designs",
        "architects",
        "integrates",
        "migrates",
        "optimizes",
        "designing",
        "architecting",
        "integrating",
        "migrating",
        "optimizing",
    ],
    "review": [
        "review",
        "validate",
        "verify",
        "analyze",
        "audit",
        "reviews",
        "validates",
        "verifies",
        "analyzes",
        "audits",
        "reviewing",
        "validating",
        "verifying",
        "analyzing",
        "auditing",
    ],
}

DEFAULT_SKILL = "backend"

TOKEN_UNIT = 100
# Conversion factor between raw tokens and the per-1k pricing units used by
# agent ``price_per_1k_tokens``. A small constant, but giving it a name keeps
# cost arithmetic readable everywhere it appears.
TOKENS_PER_KILOTOKEN = 1000
HORIZON_MULTIPLIER = 1.5

TIME_LIMIT_SECONDS = 60
NUM_WORKERS = 8
MAKESPAN_WEIGHT = 100
# Weight on the cost term when the objective references it. Default is 0
# because the canonical objective is lexicographic; cost-aware enables the
# term explicitly.
COST_WEIGHT_DEFAULT = 0

# Objective-mode literals. Constants stay paired with the Literal type so
# both producers (``SolverOptions`` schema in ``config_schema``) and consumers
# (the ``solve``/``build_model`` mode-dispatch branches) read from a single
# source of truth.
ObjectiveMode = Literal["lexicographic", "weighted", "cost_aware"]
OBJECTIVE_LEXICOGRAPHIC: Final[ObjectiveMode] = "lexicographic"
OBJECTIVE_WEIGHTED: Final[ObjectiveMode] = "weighted"
OBJECTIVE_COST_AWARE: Final[ObjectiveMode] = "cost_aware"
OBJECTIVE: ObjectiveMode = OBJECTIVE_LEXICOGRAPHIC

# Solver status strings. Mirror the four CP-SAT outcomes the result envelope
# can carry; centralising the spellings keeps the renderer, the orchestration
# envelope, and ``_finalize_result`` from drifting.
STATUS_OPTIMAL: Final[str] = "OPTIMAL"
STATUS_FEASIBLE: Final[str] = "FEASIBLE"
STATUS_INFEASIBLE: Final[str] = "INFEASIBLE"
STATUS_UNKNOWN: Final[str] = "UNKNOWN"

KAPPA_DEFAULT = 10
CONTEXT_BUDGET_KTOKENS_DEFAULT = 16
SPEED_FACTOR_DEFAULT = 1.0
STOCHASTIC_QUANTILE_DEFAULT = 0.5
ANYTIME_DEFAULT = False
# CP-SAT random seed. Constant by default so reruns of the same model are
# reproducible; callers that want exploration can override.
RANDOM_SEED_DEFAULT = 42
# Sentinel priority for tasks not pinned to a user story. Higher = lower
# priority in the heuristic's tiebreak order.
STORY_PRIORITY_DEFAULT = 99


def palette_for(
    assignments: Iterable[dict[str, Any]],
) -> tuple[list[str], dict[str, str]]:
    """Return ``(agents_sorted, color_by_agent)`` for the renderers.

    Shared by ``solver.visualize`` (matplotlib DAG + Gantt) and
    ``solver.render_html`` (Plotly figures) so both views colour each
    agent identically when a schedule is rendered to multiple formats.

    The agent list is alphabetically sorted because the renderers using
    this helper iterate over agents in row order; sort keeps that order
    deterministic and stable across runs.
    """
    agents_sorted = sorted({a["agent_id"] for a in assignments})
    color_by_agent = {
        ag: AGENT_COLORS[i % len(AGENT_COLORS)] for i, ag in enumerate(agents_sorted)
    }
    return agents_sorted, color_by_agent
