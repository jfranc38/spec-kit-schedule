"""Single source of truth for solver defaults.

Any caller that reads a user-supplied config should merge these as the
baseline. Duplicating these literals in other modules has caused config
and parser defaults to drift in the past — do not reintroduce that.
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = [
    "TOKEN_ESTIMATES",
    "COMPLEXITY_VERBS",
    "DEFAULT_SKILL",
    "TOKEN_UNIT",
    "HORIZON_MULTIPLIER",
    "TIME_LIMIT_SECONDS",
    "NUM_WORKERS",
    "KAPPA_DEFAULT",
    "CONTEXT_BUDGET_KTOKENS_DEFAULT",
    "SPEED_FACTOR_DEFAULT",
    "MAKESPAN_WEIGHT",
    "OBJECTIVE",
    "AGENT_COLORS",
    "CRITICAL_COLOR",
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
    MappingProxyType({
        "simple": 1500,
        "medium": 3500,
        "complex": 6000,
        "review": 2000,
    })
)

COMPLEXITY_VERBS: dict[str, list[str]] = {
    "simple": [
        "add", "update", "rename", "move", "import", "export", "configure",
        "adds", "updates", "renames", "moves", "imports", "exports", "configures",
        "adding", "updating", "renaming", "moving", "importing", "exporting", "configuring",
    ],
    "medium": [
        "implement", "create", "write", "build", "refactor",
        "implements", "creates", "writes", "builds", "refactors",
        "implementing", "creating", "writing", "building", "refactoring",
    ],
    "complex": [
        "design", "architect", "integrate", "migrate", "optimize",
        "designs", "architects", "integrates", "migrates", "optimizes",
        "designing", "architecting", "integrating", "migrating", "optimizing",
    ],
    "review": [
        "review", "validate", "verify", "analyze", "audit",
        "reviews", "validates", "verifies", "analyzes", "audits",
        "reviewing", "validating", "verifying", "analyzing", "auditing",
    ],
}

DEFAULT_SKILL = "backend"

TOKEN_UNIT = 100
HORIZON_MULTIPLIER = 1.5

TIME_LIMIT_SECONDS = 60
NUM_WORKERS = 8
MAKESPAN_WEIGHT = 100
OBJECTIVE = "lexicographic"

KAPPA_DEFAULT = 10
CONTEXT_BUDGET_KTOKENS_DEFAULT = 16
SPEED_FACTOR_DEFAULT = 1.0
