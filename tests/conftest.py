"""Shared pytest fixtures."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def minimal_config() -> dict:
    """A minimal, valid schedule-config.yml equivalent for unit tests."""
    return {
        "agents": [
            {
                "id": "backend",
                "model": "test",
                "skills": ["backend", "api"],
                "kappa": 10,
                "context_budget": 64,
                "speed_factor": 1.0,
            },
            {
                "id": "tester",
                "model": "test",
                "skills": ["test"],
                "kappa": 10,
                "context_budget": 64,
                "speed_factor": 1.0,
            },
        ],
        "skill_rules": [
            {"pattern": "tests/", "skill": "test"},
            {"pattern": "src/api/", "skill": "api"},
        ],
        "default_skill": "backend",
        "token_estimates": {
            "simple": 1500, "medium": 3500, "complex": 6000, "review": 2000,
        },
        "complexity_verbs": {
            "simple": ["add", "update"],
            "medium": ["implement", "create", "write"],
            "complex": ["design"],
            "review": ["review"],
        },
        "solver": {"time_limit": 10, "num_workers": 1, "warm_start": True},
    }


@pytest.fixture
def write_tasks(tmp_path: Path):
    """Factory that writes tasks.md content to a temp file and returns the path."""

    def _write(content: str) -> Path:
        path = tmp_path / "tasks.md"
        path.write_text(content, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def docs_example_config() -> dict:
    return yaml.safe_load(
        (REPO_ROOT / "docs" / "example-config.yml").read_text(encoding="utf-8")
    )


@pytest.fixture
def docs_example_tasks() -> Path:
    return REPO_ROOT / "docs" / "example-tasks.md"


@pytest.fixture
def clone_config(minimal_config):
    """Return a deep-clone helper so mutations don't leak across tests."""

    def _clone() -> dict:
        return copy.deepcopy(minimal_config)

    return _clone
