"""Pydantic-backed schema for schedule-config.yml.

Validates agent portfolios and solver options at load time, converting raw
YAML dictionaries into typed models with actionable error messages.
"""

from __future__ import annotations

__all__ = [
    "TokenEstimate",
    "TokenEstimateLike",
    "AgentConfig",
    "SkillRule",
    "SolverOptions",
    "Config",
    "load_config",
]

import logging
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, field_validator

from .defaults import (
    DEFAULT_SKILL,
    HORIZON_MULTIPLIER,
    MAKESPAN_WEIGHT,
    NUM_WORKERS,
    OBJECTIVE,
    TIME_LIMIT_SECONDS,
    TOKEN_UNIT,
)
from .validation import ScheduleInputError

log = logging.getLogger(__name__)

# Pydantic v2 uses Annotated for constraints on primitive types.
NonNegativeFloat = Annotated[float, Field(ge=0.0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class TokenEstimate(BaseModel):
    """Token estimate with optional standard deviation for stochastic mode."""

    mean: PositiveInt
    std_dev: NonNegativeInt = 0


# Accept either a bare integer or a {mean, std_dev} mapping.
TokenEstimateLike = PositiveInt | TokenEstimate


class AgentConfig(BaseModel):
    """A single agent entry in the agents: list."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    provider: str | None = None
    model: str = "unknown"
    skills: list[str] = Field(min_length=1)
    kappa: PositiveInt
    context_budget: PositiveInt
    speed_factor: PositiveFloat = 1.0
    price_per_1k_tokens: NonNegativeFloat = 0.0


class SkillRule(BaseModel):
    """Pattern → skill mapping used by the task parser."""

    pattern: str = Field(min_length=1)
    skill: str = Field(min_length=1)


class SolverOptions(BaseModel):
    """CP-SAT solver tuning parameters."""

    model_config = ConfigDict(extra="forbid")

    objective: Literal["lexicographic", "weighted", "cost_aware"] = OBJECTIVE
    makespan_weight: PositiveInt = MAKESPAN_WEIGHT
    cost_weight: NonNegativeInt = 0
    time_limit: PositiveInt = TIME_LIMIT_SECONDS
    num_workers: PositiveInt = NUM_WORKERS
    symmetry_breaking: bool = True
    warm_start: bool = True
    horizon_multiplier: PositiveFloat = HORIZON_MULTIPLIER
    token_unit: PositiveInt = TOKEN_UNIT
    stochastic_quantile: float = Field(0.5, ge=0.0, le=1.0)
    anytime: bool = False


class Config(BaseModel):
    """Top-level schedule-config.yml schema.

    extra="allow" preserves forward compatibility with unknown top-level
    keys (e.g. the `output:` block) and keys added by future agents without
    breaking existing installs.
    """

    model_config = ConfigDict(extra="allow")

    agents: list[AgentConfig] = Field(min_length=1)
    skill_rules: list[SkillRule] = []
    default_skill: str = DEFAULT_SKILL
    token_estimates: dict[str, TokenEstimateLike] = {}
    complexity_verbs: dict[str, list[str]] = {}
    solver: SolverOptions = Field(default_factory=SolverOptions)

    @field_validator("token_estimates", mode="before")
    @classmethod
    def _coerce_token_estimates(
        cls, raw: dict[str, object]
    ) -> dict[str, TokenEstimate | int]:
        """Accept plain ints as well as {mean, std_dev} dicts."""
        if not isinstance(raw, dict):
            return raw
        out: dict[str, object] = {}
        for key, val in raw.items():
            if isinstance(val, int):
                out[key] = TokenEstimate(mean=val)
            elif isinstance(val, dict):
                out[key] = TokenEstimate(**val)
            else:
                out[key] = val
        return out


def load_config(path: str | Path) -> Config:
    """Load and validate a schedule-config.yml file.

    Parameters
    ----------
    path:
        Filesystem path to the YAML configuration file.

    Returns
    -------
    Config
        Fully validated configuration model.

    Raises
    ------
    ScheduleInputError
        If the YAML is malformed or any field fails validation.
    FileNotFoundError
        If the file does not exist (propagated as-is).
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    try:
        config = Config.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError
        # Extract per-field errors and re-raise with actionable messages.
        errors = getattr(exc, "errors", None)
        if errors is not None:
            parts = []
            for err in errors():
                loc = ".".join(str(s) for s in err["loc"]) if err.get("loc") else "?"
                msg = err.get("msg", str(err))
                parts.append(f"config error at '{loc}': {msg}")
            raise ScheduleInputError("; ".join(parts)) from exc
        raise ScheduleInputError(f"config error: {exc}") from exc

    log.debug("loaded config from %s: %d agents", path, len(config.agents))
    return config
