"""Deterministic synthetic problem generator for benchmarks.

Problem parameters:

| size         | n_tasks | n_agents | density | skill_complexity |
|--------------|---------|----------|---------|-----------------|
| tiny         | 10      | 2        | 0.8     | 2 skills        |
| small        | 25      | 3        | 1.2     | 3 skills        |
| medium       | 75      | 5        | 1.5     | 4 skills        |
| large        | 200     | 8        | 2.0     | 5 skills        |
| xl           | 400     | 10       | 2.5     | 6 skills        |

Real-world shapes exercise distinct constraint regimes via specific
skill-ratio and file-mutex density configurations.
"""

from __future__ import annotations

import random
from typing import Any

__all__ = ["generate", "SIZES", "REAL_WORLD_SHAPES"]

# ---------------------------------------------------------------------------
# Size parameter table — single source of truth, no magic numbers elsewhere
# ---------------------------------------------------------------------------

SIZES: dict[str, dict[str, Any]] = {
    "tiny":   {"n_tasks": 10,  "n_agents": 2,  "density": 0.8, "n_skills": 2},
    "small":  {"n_tasks": 25,  "n_agents": 3,  "density": 1.2, "n_skills": 3},
    "medium": {"n_tasks": 75,  "n_agents": 5,  "density": 1.5, "n_skills": 4},
    "large":  {"n_tasks": 200, "n_agents": 8,  "density": 2.0, "n_skills": 5},
    "xl":     {"n_tasks": 400, "n_agents": 10, "density": 2.5, "n_skills": 6},
}

# Real-world shape definitions:
# skill_ratios: fraction of tasks assigned to each skill (must sum to ~1).
# mutex_density: expected file-mutex group size relative to n_tasks.
REAL_WORLD_SHAPES: dict[str, dict[str, Any]] = {
    "frontend_heavy": {
        "n_tasks": 40, "n_agents": 4, "density": 1.3, "n_skills": 3,
        "skill_ratios": [0.5, 0.3, 0.2],
        "mutex_density": 0.15,
        "seed": 101,
    },
    "backend_heavy": {
        "n_tasks": 40, "n_agents": 4, "density": 1.5, "n_skills": 3,
        "skill_ratios": [0.2, 0.6, 0.2],
        "mutex_density": 0.25,
        "seed": 102,
    },
    "balanced_tdd": {
        "n_tasks": 36, "n_agents": 4, "density": 1.2, "n_skills": 3,
        "skill_ratios": [0.33, 0.33, 0.34],
        "mutex_density": 0.20,
        "seed": 103,
    },
    "migration": {
        "n_tasks": 50, "n_agents": 5, "density": 2.0, "n_skills": 4,
        "skill_ratios": [0.15, 0.35, 0.35, 0.15],
        "mutex_density": 0.30,
        "seed": 104,
    },
    "greenfield": {
        "n_tasks": 30, "n_agents": 3, "density": 0.9, "n_skills": 3,
        "skill_ratios": [0.40, 0.35, 0.25],
        "mutex_density": 0.08,
        "seed": 105,
    },
}

_ALL_SKILLS = ["backend", "frontend", "test", "infra", "data", "devops"]
_COMPLEXITIES = ["simple", "medium", "complex", "review"]
_COMPLEXITY_TOKENS = {"simple": 1500, "medium": 3500, "complex": 6000, "review": 2000}
_KAPPA = 12
_CONTEXT_BUDGET = 128_000
_SPEED_FACTOR = 1.0


def _skill_pool(n_skills: int) -> list[str]:
    return _ALL_SKILLS[:n_skills]


def _make_dag_edges(
    n_tasks: int,
    density: float,
    rng: random.Random,
) -> list[tuple[str, str]]:
    """Generate a DAG with approximately `density * n_tasks` edges.

    Edges only go from lower to higher index, guaranteeing acyclicity.
    """
    target_edges = int(density * n_tasks)
    candidates: list[tuple[int, int]] = []
    for i in range(n_tasks):
        for j in range(i + 1, min(i + 6, n_tasks)):
            candidates.append((i, j))
    rng.shuffle(candidates)
    selected = candidates[:target_edges]
    return [(f"T{s:03d}", f"T{d:03d}") for s, d in sorted(selected)]


def _assign_skills(
    n_tasks: int,
    skills: list[str],
    skill_ratios: list[float] | None,
    rng: random.Random,
) -> list[str]:
    if skill_ratios is not None:
        result: list[str] = []
        cumulative = 0.0
        boundaries = []
        for r in skill_ratios[:-1]:
            cumulative += r
            boundaries.append(int(cumulative * n_tasks))
        boundaries.append(n_tasks)
        prev = 0
        for skill, boundary in zip(skills, boundaries, strict=False):
            result.extend([skill] * (boundary - prev))
            prev = boundary
        rng.shuffle(result)
        return result
    return [rng.choice(skills) for _ in range(n_tasks)]


def _make_file_paths(
    task_idx: int,
    skill: str,
    n_tasks: int,
    mutex_density: float,
    rng: random.Random,
) -> list[str]:
    """Assign file paths so mutex_density * n_tasks tasks share files."""
    primary = f"src/{skill}/module_{task_idx % max(1, int(n_tasks * mutex_density))}.py"
    if rng.random() < 0.3:
        return [primary, f"tests/test_{skill}_{task_idx}.py"]
    return [primary]


def generate(*, size: str, seed: int = 42) -> dict:
    """Return a solver-input dict (same shape as parse_tasks output).

    Parameters
    ----------
    size:
        One of the keys in ``SIZES`` or ``REAL_WORLD_SHAPES``.
    seed:
        Random seed for reproducibility. Real-world shapes override this
        with their own fixed seed so each named shape is always identical.
    """
    if size in REAL_WORLD_SHAPES:
        shape = REAL_WORLD_SHAPES[size]
        params: dict[str, Any] = {**shape}
        effective_seed = shape.get("seed", seed)
        skill_ratios: list[float] | None = shape.get("skill_ratios")
        mutex_density: float = shape.get("mutex_density", 0.15)
    elif size in SIZES:
        params = {**SIZES[size]}
        effective_seed = seed
        skill_ratios = None
        mutex_density = 0.15
    else:
        valid = sorted(list(SIZES) + list(REAL_WORLD_SHAPES))
        raise ValueError(f"Unknown size {size!r}. Valid: {valid}")

    n_tasks: int = params["n_tasks"]
    n_agents: int = params["n_agents"]
    density: float = params["density"]
    n_skills: int = params["n_skills"]

    rng = random.Random(effective_seed)
    skills = _skill_pool(n_skills)
    task_skills = _assign_skills(n_tasks, skills, skill_ratios, rng)

    tasks = []
    for i in range(n_tasks):
        skill = task_skills[i]
        complexity = rng.choice(_COMPLEXITIES)
        tokens = _COMPLEXITY_TOKENS[complexity] + rng.randint(-200, 200)
        file_paths = _make_file_paths(i, skill, n_tasks, mutex_density, rng)
        tasks.append({
            "id": f"T{i:03d}",
            "phase": "Implementation",
            "story_id": f"S{i // 5 + 1:02d}",
            "story_priority": (i // 5) + 1,
            "parallel_flag": False,
            "file_paths": file_paths,
            "required_skill": skill,
            "estimated_tokens": max(500, tokens),
            "action_verb": "implement",
        })

    edges = _make_dag_edges(n_tasks, density, rng)

    agents = []
    for j in range(n_agents):
        agent_skills = list(skills)
        if n_skills > 2:
            # Each agent covers a primary skill plus a random secondary
            primary = skills[j % len(skills)]
            secondary = rng.choice([s for s in skills if s != primary])
            agent_skills = sorted({primary, secondary})
        agents.append({
            "id": f"agent_{j}",
            "model": "benchmark-model",
            "skills": agent_skills,
            "kappa": _KAPPA,
            "context_budget": _CONTEXT_BUDGET,
            "speed_factor": _SPEED_FACTOR,
            "provider": "local",
            "price_per_1k_tokens": 0.0,
        })

    return {
        "tasks": tasks,
        "edges": [[s, d] for s, d in edges],
        "agents": agents,
        "config": {
            "time_limit": 60,
            "num_workers": 1,
            "objective": "lexicographic",
            "warm_start": True,
        },
        "warnings": [],
        "_meta": {"size": size, "seed": effective_seed, "n_tasks": n_tasks, "n_agents": n_agents},
    }
