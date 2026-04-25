"""Tests for solver.config_schema — pydantic-backed schedule-config validation."""

from __future__ import annotations

__all__: list[str] = []

from pathlib import Path

import pytest
from pydantic import ValidationError

from solver.config_schema import (
    AgentConfig,
    Config,
    SolverOptions,
    TokenEstimate,
    load_config,
)
from solver.validation import ScheduleInputError

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_agent(**overrides) -> dict:
    base = {
        "id": "worker",
        "skills": ["backend"],
        "kappa": 5,
        "context_budget": 16,
    }
    base.update(overrides)
    return base


def _minimal_config(**overrides) -> dict:
    base = {"agents": [_minimal_agent()]}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# TokenEstimate
# ---------------------------------------------------------------------------

class TestTokenEstimate:
    def test_plain_int_mean(self):
        te = TokenEstimate(mean=1500)
        assert te.mean == 1500
        assert te.std_dev == 0

    def test_with_std_dev(self):
        te = TokenEstimate(mean=3500, std_dev=500)
        assert te.std_dev == 500

    def test_mean_must_be_positive(self):
        with pytest.raises(ValidationError):
            TokenEstimate(mean=0)

    def test_std_dev_non_negative(self):
        with pytest.raises(ValidationError):
            TokenEstimate(mean=100, std_dev=-1)


# ---------------------------------------------------------------------------
# AgentConfig
# ---------------------------------------------------------------------------

class TestAgentConfig:
    def test_minimal_valid(self):
        ac = AgentConfig.model_validate(_minimal_agent())
        assert ac.id == "worker"
        assert ac.price_per_1k_tokens == 0.0
        assert ac.speed_factor == 1.0

    def test_missing_id_raises(self):
        with pytest.raises(ValidationError, match="id"):
            AgentConfig.model_validate({"skills": ["x"], "kappa": 1, "context_budget": 16})

    def test_empty_id_raises(self):
        with pytest.raises(ValidationError):
            AgentConfig.model_validate(_minimal_agent(id=""))

    def test_empty_skills_raises(self):
        with pytest.raises(ValidationError, match="skills"):
            AgentConfig.model_validate(_minimal_agent(skills=[]))

    def test_missing_skills_raises(self):
        agent = _minimal_agent()
        del agent["skills"]
        with pytest.raises(ValidationError, match="skills"):
            AgentConfig.model_validate(agent)

    def test_kappa_zero_raises(self):
        with pytest.raises(ValidationError, match="kappa"):
            AgentConfig.model_validate(_minimal_agent(kappa=0))

    def test_kappa_negative_raises(self):
        with pytest.raises(ValidationError, match="kappa"):
            AgentConfig.model_validate(_minimal_agent(kappa=-1))

    def test_context_budget_zero_raises(self):
        with pytest.raises(ValidationError, match="context_budget"):
            AgentConfig.model_validate(_minimal_agent(context_budget=0))

    def test_speed_factor_zero_raises(self):
        with pytest.raises(ValidationError, match="speed_factor"):
            AgentConfig.model_validate(_minimal_agent(speed_factor=0.0))

    def test_speed_factor_negative_raises(self):
        with pytest.raises(ValidationError, match="speed_factor"):
            AgentConfig.model_validate(_minimal_agent(speed_factor=-1.0))

    def test_price_per_1k_tokens_default(self):
        ac = AgentConfig.model_validate(_minimal_agent())
        assert ac.price_per_1k_tokens == 0.0

    def test_price_per_1k_tokens_positive(self):
        ac = AgentConfig.model_validate(_minimal_agent(price_per_1k_tokens=0.003))
        assert ac.price_per_1k_tokens == pytest.approx(0.003)

    def test_price_per_1k_tokens_negative_raises(self):
        with pytest.raises(ValidationError, match="price_per_1k_tokens"):
            AgentConfig.model_validate(_minimal_agent(price_per_1k_tokens=-0.001))

    def test_provider_optional(self):
        ac = AgentConfig.model_validate(_minimal_agent(provider="anthropic"))
        assert ac.provider == "anthropic"

    def test_typo_field_raises(self):
        """extra='forbid' must catch typos like kapp instead of kappa."""
        with pytest.raises(ValidationError):
            AgentConfig.model_validate(_minimal_agent(kapp=5))

    def test_unknown_field_raises(self):
        with pytest.raises(ValidationError):
            AgentConfig.model_validate(_minimal_agent(nonexistent_field="oops"))


# ---------------------------------------------------------------------------
# SolverOptions
# ---------------------------------------------------------------------------

class TestSolverOptions:
    def test_defaults(self):
        so = SolverOptions()
        assert so.objective == "lexicographic"
        assert so.cost_weight == 0
        assert so.stochastic_quantile == 0.5
        assert so.anytime is False

    def test_invalid_objective_raises(self):
        with pytest.raises(ValidationError, match="objective"):
            SolverOptions.model_validate({"objective": "random"})

    def test_cost_aware_objective_accepted(self):
        so = SolverOptions.model_validate({"objective": "cost_aware"})
        assert so.objective == "cost_aware"

    def test_stochastic_quantile_too_high_raises(self):
        with pytest.raises(ValidationError, match="stochastic_quantile"):
            SolverOptions.model_validate({"stochastic_quantile": 1.5})

    def test_stochastic_quantile_negative_raises(self):
        with pytest.raises(ValidationError, match="stochastic_quantile"):
            SolverOptions.model_validate({"stochastic_quantile": -0.1})

    def test_stochastic_quantile_boundary_valid(self):
        so = SolverOptions.model_validate({"stochastic_quantile": 0.0})
        assert so.stochastic_quantile == 0.0
        so = SolverOptions.model_validate({"stochastic_quantile": 1.0})
        assert so.stochastic_quantile == 1.0

    def test_anytime_flag(self):
        so = SolverOptions.model_validate({"anytime": True})
        assert so.anytime is True

    def test_cost_weight_default(self):
        assert SolverOptions().cost_weight == 0

    def test_cost_weight_non_negative(self):
        so = SolverOptions.model_validate({"cost_weight": 10})
        assert so.cost_weight == 10

    def test_time_limit_zero_raises(self):
        with pytest.raises(ValidationError, match="time_limit"):
            SolverOptions.model_validate({"time_limit": 0})

    def test_num_workers_zero_raises(self):
        with pytest.raises(ValidationError, match="num_workers"):
            SolverOptions.model_validate({"num_workers": 0})

    def test_typo_field_raises(self):
        with pytest.raises(ValidationError):
            SolverOptions.model_validate({"timelimit": 30})


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_minimal_valid(self):
        cfg = Config.model_validate(_minimal_config())
        assert len(cfg.agents) == 1
        assert cfg.default_skill == "backend"
        assert cfg.solver.objective == "lexicographic"

    def test_no_agents_raises(self):
        with pytest.raises(ValidationError, match="agents"):
            Config.model_validate({"agents": []})

    def test_missing_agents_raises(self):
        with pytest.raises(ValidationError, match="agents"):
            Config.model_validate({})

    def test_extra_top_level_key_allowed(self):
        """output: block and future keys must not raise (extra='allow')."""
        cfg = Config.model_validate({
            **_minimal_config(),
            "output": {"schedule_file": "schedule.md"},
            "future_key": 42,
        })
        assert cfg.agents

    def test_token_estimates_int_form(self):
        cfg = Config.model_validate({
            **_minimal_config(),
            "token_estimates": {"simple": 1500, "medium": 3500},
        })
        assert isinstance(cfg.token_estimates["simple"], TokenEstimate)
        assert cfg.token_estimates["simple"].mean == 1500
        assert cfg.token_estimates["simple"].std_dev == 0

    def test_token_estimates_dict_form(self):
        cfg = Config.model_validate({
            **_minimal_config(),
            "token_estimates": {"medium": {"mean": 3500, "std_dev": 500}},
        })
        assert cfg.token_estimates["medium"].mean == 3500
        assert cfg.token_estimates["medium"].std_dev == 500

    def test_token_estimates_mixed_forms(self):
        cfg = Config.model_validate({
            **_minimal_config(),
            "token_estimates": {
                "simple": 1500,
                "medium": {"mean": 3500, "std_dev": 500},
            },
        })
        assert cfg.token_estimates["simple"].mean == 1500
        assert cfg.token_estimates["medium"].std_dev == 500

    def test_solver_new_fields_defaults(self):
        cfg = Config.model_validate(_minimal_config())
        assert cfg.solver.cost_weight == 0
        assert cfg.solver.stochastic_quantile == 0.5
        assert cfg.solver.anytime is False

    def test_skill_rules_optional(self):
        cfg = Config.model_validate(_minimal_config())
        assert cfg.skill_rules == []

    def test_default_skill_overridable(self):
        cfg = Config.model_validate({**_minimal_config(), "default_skill": "frontend"})
        assert cfg.default_skill == "frontend"


# ---------------------------------------------------------------------------
# load_config integration
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_example_config_loads(self):
        cfg = load_config(REPO_ROOT / "docs" / "example-config.yml")
        assert len(cfg.agents) >= 1
        # price_per_1k_tokens must be present (non-negative float)
        for agent in cfg.agents:
            assert agent.price_per_1k_tokens >= 0.0

    def test_example_config_mixed_loads(self):
        cfg = load_config(REPO_ROOT / "docs" / "example-config-mixed.yml")
        assert len(cfg.agents) >= 1
        for agent in cfg.agents:
            assert agent.price_per_1k_tokens >= 0.0

    def test_example_config_token_estimates_normalise(self):
        """Both int and dict forms in docs/example-config.yml become TokenEstimate."""
        cfg = load_config(REPO_ROOT / "docs" / "example-config.yml")
        for v in cfg.token_estimates.values():
            assert isinstance(v, TokenEstimate)

    def test_missing_file_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yml")

    def test_invalid_yaml_raises_schedule_input_error(self, tmp_path):
        bad = tmp_path / "bad.yml"
        bad.write_text("agents:\n  - kappa: 0\n    skills: []\n    id: x\n", encoding="utf-8")
        with pytest.raises(ScheduleInputError):
            load_config(bad)

    def test_field_name_in_error_message(self, tmp_path):
        bad = tmp_path / "bad.yml"
        bad.write_text("agents:\n  - id: x\n    skills: [backend]\n    kappa: -1\n    context_budget: 16\n", encoding="utf-8")
        with pytest.raises(ScheduleInputError, match="kappa"):
            load_config(bad)

    def test_typo_in_agent_raises_with_field_name(self, tmp_path):
        bad = tmp_path / "bad.yml"
        content = (
            "agents:\n"
            "  - id: x\n"
            "    skills: [backend]\n"
            "    kapp: 5\n"       # typo: kapp instead of kappa
            "    context_budget: 16\n"
        )
        bad.write_text(content, encoding="utf-8")
        with pytest.raises(ScheduleInputError):
            load_config(bad)

    def test_config_template_loads(self):
        cfg = load_config(REPO_ROOT / "config-template.yml")
        assert len(cfg.agents) >= 1

    def test_price_per_1k_tokens_in_example_configs(self):
        for name in ("example-config.yml", "example-config-mixed.yml"):
            cfg = load_config(REPO_ROOT / "docs" / name)
            for agent in cfg.agents:
                assert hasattr(agent, "price_per_1k_tokens")

    def test_solver_new_fields_from_template(self):
        cfg = load_config(REPO_ROOT / "config-template.yml")
        assert cfg.solver.cost_weight == 0
        assert cfg.solver.stochastic_quantile == 0.5
        assert cfg.solver.anytime is False


# ---------------------------------------------------------------------------
# Retrocompat: minimal_config fixture dict must still load
# ---------------------------------------------------------------------------

class TestRetrocompat:
    def test_minimal_config_fixture_loads(self, minimal_config):
        """conftest.py minimal_config must parse without errors."""
        cfg = Config.model_validate(minimal_config)
        assert len(cfg.agents) == 2

    def test_solver_subdict_without_new_keys_loads(self, minimal_config):
        """Old configs without cost_weight/stochastic_quantile/anytime still parse."""
        cfg = Config.model_validate(minimal_config)
        assert cfg.solver.cost_weight == 0
        assert cfg.solver.stochastic_quantile == 0.5
        assert cfg.solver.anytime is False
