"""Numerical-scale guards: schema-level upper bounds and cost-scale underflow."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from solver.config_schema import AgentConfig, TokenEstimate
from solver.i18n_catalog import WARN_COST_SCALE_UNDERFLOW
from solver.scheduler import solve_from_json
from tests._helpers import make_agent, make_solver_input, make_task


def _agent(**overrides) -> dict:
    base = {
        "id": "worker",
        "skills": ["backend"],
        "kappa": 5,
        "context_budget": 16,
    }
    base.update(overrides)
    return base


class TestPriceUpperBound:
    def test_price_above_cap_rejected(self):
        with pytest.raises(ValidationError, match="price_per_1k_tokens"):
            AgentConfig.model_validate(_agent(price_per_1k_tokens=1e7))

    def test_price_at_cap_accepted(self):
        ac = AgentConfig.model_validate(_agent(price_per_1k_tokens=1e6))
        assert ac.price_per_1k_tokens == pytest.approx(1e6)


class TestTokenUpperBound:
    def test_estimated_tokens_above_cap_rejected(self):
        with pytest.raises(ValidationError):
            TokenEstimate(mean=int(1e9))

    def test_estimated_tokens_zero_rejected(self):
        with pytest.raises(ValidationError):
            TokenEstimate(mean=0)

    def test_estimated_tokens_at_cap_accepted(self):
        te = TokenEstimate(mean=100_000_000)
        assert te.mean == 100_000_000

    def test_token_std_dev_negative_rejected(self):
        with pytest.raises(ValidationError):
            TokenEstimate(mean=100, std_dev=-1)

    def test_token_std_dev_above_cap_rejected(self):
        with pytest.raises(ValidationError):
            TokenEstimate(mean=100, std_dev=int(1e9))


def _cost_aware_data(price: float, tokens: int = 1000) -> dict:
    return make_solver_input(
        tasks=[make_task("T001", file_paths=["a.py"], estimated_tokens=tokens)],
        agents=[make_agent("A0", context_budget=20_000, price_per_1k_tokens=price)],
        config={"objective": "cost_aware", "time_limit": 10},
    )


class TestCostScaleUnderflow:
    def test_warning_emitted_when_all_costs_underflow(self):
        # tokens(1) * price(1e-5) / 1000 * _COST_SCALE(10_000) = 1e-7 → int(0).
        data = _cost_aware_data(price=1e-5, tokens=1)
        result = solve_from_json(data)
        codes = [w["code"] for w in result["warnings"]]
        assert WARN_COST_SCALE_UNDERFLOW in codes

    def test_warning_not_emitted_for_normal_prices(self):
        data = _cost_aware_data(price=2.0, tokens=1000)
        result = solve_from_json(data)
        codes = [w["code"] for w in result["warnings"]]
        assert WARN_COST_SCALE_UNDERFLOW not in codes

    def test_warning_not_emitted_when_no_pricing(self):
        data = _cost_aware_data(price=0.0, tokens=1000)
        result = solve_from_json(data)
        codes = [w["code"] for w in result["warnings"]]
        assert WARN_COST_SCALE_UNDERFLOW not in codes
