"""Pydantic-backed schema for schedule-config.yml.

Validates agent portfolios and solver options at load time, converting raw
YAML dictionaries into typed models with actionable error messages.

v0.6.0+: ``default_config_path()`` returns the encapsulated path
(``.specify/schedule/schedule-config.yml``). ``resolve_config_path()``
adds a one-shot migration of any pre-0.6.0 ``./schedule-config.yml`` at
the project root.
"""

from __future__ import annotations

__all__ = [
    "AgentConfig",
    "Config",
    "SkillRule",
    "SolverOptions",
    "TokenEstimate",
    "TokenEstimateLike",
    "default_config_path",
    "load_config",
    "resolve_config_path",
]

import logging
from pathlib import Path
from typing import Annotated

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs by default
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, field_validator

from ._paths import migrate_legacy_config, schedule_config_path
from .defaults import (
    COST_WEIGHT_DEFAULT,
    DEFAULT_SKILL,
    HORIZON_MULTIPLIER,
    MAKESPAN_WEIGHT,
    NUM_WORKERS,
    OBJECTIVE,
    RANDOM_SEED_DEFAULT,
    TIME_LIMIT_SECONDS,
    TOKEN_UNIT,
    ObjectiveMode,
)
from .validation import ScheduleInputError

log = logging.getLogger(__name__)

# Pydantic v2 uses Annotated for constraints on primitive types.
NonNegativeInt = Annotated[int, Field(ge=0)]

# Bound chosen so that n × _MAX_TOKENS × _MAX_PRICE × _COST_SCALE / 1000
# stays well within int64 across realistic project sizes; see
# solver/scheduler.py:_add_cost_variable for the runtime guard.
_MAX_PRICE_PER_1K_TOKENS = 1e6
_MAX_TOKENS = 100_000_000


class TokenEstimate(BaseModel):
    """Token estimate with optional standard deviation for stochastic mode."""

    mean: Annotated[int, Field(gt=0, le=_MAX_TOKENS)]
    std_dev: Annotated[int, Field(ge=0, le=_MAX_TOKENS)] = 0


# Accept either a bare integer or a {mean, std_dev} mapping.
TokenEstimateLike = Annotated[int, Field(gt=0, le=_MAX_TOKENS)] | TokenEstimate


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
    price_per_1k_tokens: Annotated[float, Field(ge=0.0, le=_MAX_PRICE_PER_1K_TOKENS)] = 0.0


class SkillRule(BaseModel):
    """Pattern → skill mapping used by the task parser."""

    pattern: str = Field(min_length=1)
    skill: str = Field(min_length=1)


class SolverOptions(BaseModel):
    """CP-SAT solver tuning parameters."""

    model_config = ConfigDict(extra="forbid")

    # ``ObjectiveMode`` is the canonical Literal; ``OBJECTIVE`` is one of the
    # three values, so the assignment type-checks without a cast.
    objective: ObjectiveMode = OBJECTIVE
    makespan_weight: PositiveInt = MAKESPAN_WEIGHT
    cost_weight: NonNegativeInt = COST_WEIGHT_DEFAULT
    time_limit: PositiveInt = TIME_LIMIT_SECONDS
    num_workers: PositiveInt = NUM_WORKERS
    symmetry_breaking: bool = True
    warm_start: bool = True
    horizon_multiplier: PositiveFloat = HORIZON_MULTIPLIER
    token_unit: PositiveInt = TOKEN_UNIT
    # Schema accepts the closed interval [0, 1]; runtime in
    # ``_quantile_tokens`` rejects exactly 0 and 1 because ``Φ⁻¹(0)`` and
    # ``Φ⁻¹(1)`` are unbounded and crash ``statistics.NormalDist.inv_cdf``.
    # The closed-interval cap stays for backwards compatibility with configs
    # that already serialise these boundary values; the runtime guard is the
    # authoritative check.
    stochastic_quantile: float = Field(0.5, ge=0.0, le=1.0)
    anytime: bool = False
    # Determinism: same model + same seed → same incumbent across runs.
    random_seed: NonNegativeInt = RANDOM_SEED_DEFAULT
    # Forwards CP-SAT's search log to stderr. Off by default because the log
    # is verbose; the CLI ``--verbose`` flag flips this on.
    verbose: bool = False


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
    # Pydantic's generated stubs trip mypy on ``Field(default_factory=Type)``
    # because the type tracker doesn't see ``Type()`` as zero-arg-callable
    # equivalent (every field default has its own type). The constructor is
    # zero-arg at runtime — pydantic fills the unset fields from each field's
    # own default. The cast keeps the code path unchanged.
    solver: SolverOptions = Field(default_factory=SolverOptions)  # type: ignore[arg-type]

    @field_validator("token_estimates", mode="before")
    @classmethod
    def _coerce_token_estimates(
        cls, raw: object
    ) -> object:
        """Accept plain ints as well as {mean, std_dev} dicts.

        Returns ``object`` to match pydantic's pre-validator contract: the
        validator hands back a value pydantic re-validates against
        ``dict[str, TokenEstimateLike]``.
        """
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


def default_config_path(project: Path | None = None) -> Path:
    """Return the v0.6.0+ encapsulated schedule-config path.

    ``<project_root>/.specify/schedule/schedule-config.yml``. The
    file may not exist yet — callers checking presence should still
    do their own ``Path.is_file()`` check after this.
    """
    return schedule_config_path(project)


def resolve_config_path(
    path: str | Path | None = None,
    project: Path | None = None,
) -> Path:
    """Resolve a user-supplied config path with v0.5.x → v0.6.0 migration.

    Behaviour:

    * If ``path`` is provided AND exists → return it.
    * Otherwise migrate any legacy ``./schedule-config.yml`` to the
      encapsulated location (one-shot, see
      :func:`solver._paths.migrate_legacy_config`) and return the
      encapsulated path. The caller decides whether to error on
      ``not is_file()``.
    """
    if path is not None:
        candidate = Path(path)
        if candidate.is_file():
            return candidate
    migrate_legacy_config(project)
    return default_config_path(project)


def load_config(path: str | Path | None = None, *, project: Path | None = None) -> Config:
    """Load and validate a schedule-config.yml file.

    Parameters
    ----------
    path:
        Filesystem path to the YAML configuration file. ``None`` (the
        v0.6.0+ default) resolves to the encapsulated path
        ``<project_root>/.specify/schedule/schedule-config.yml`` after
        a one-shot legacy-config migration.
    project:
        Project root override forwarded to ``solver._paths``.

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
    resolved = resolve_config_path(path, project=project)
    raw = yaml.safe_load(Path(resolved).read_text(encoding="utf-8")) or {}
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

    log.debug("loaded config from %s: %d agents", resolved, len(config.agents))
    return config
