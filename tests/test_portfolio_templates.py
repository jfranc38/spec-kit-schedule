"""Tests for the per-AI portfolio templates and the resolver.

Covers:

* Every bundled template (``base-portfolio.yml`` and the four per-AI
  variants) parses as YAML and validates against
  :class:`solver.config_schema.Config`.
* Per-AI templates contain NO ``REPLACE_ME`` placeholders — they ship
  realistic 2026 model identifiers and prices. Only ``base-portfolio.yml``
  may carry the placeholder strings.
* Per-AI templates have a non-empty ``agents`` list with the required
  fields (``id``, ``model``, ``skills``, ``kappa``, ``context_budget``).
* The κ / context_budget tier ordering is consistent across each
  template's tiers (frontier ≤ mid ≤ small for κ; frontier ≥ mid ≥
  small for context_budget).
* :func:`solver.portfolio_templates.template_for_integration` returns
  the per-AI template for known keys and falls back to
  ``base-portfolio.yml`` for unknown / ``None`` keys.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from solver.config_schema import Config
from solver.portfolio_templates import template_for_integration

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Per-AI template filenames (excludes ``base-portfolio.yml``).
_PER_AI_FILES: list[str] = [
    "portfolio-claude.yml",
    "portfolio-copilot.yml",
    "portfolio-cursor.yml",
    "portfolio-gemini.yml",
]

# Every bundled portfolio template (used for parametrised parsing tests).
_ALL_TEMPLATE_FILES: list[str] = ["base-portfolio.yml", *_PER_AI_FILES]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_template(filename: str) -> dict:
    body = yaml.safe_load((_TEMPLATES_DIR / filename).read_text(encoding="utf-8"))
    assert isinstance(body, dict), f"{filename} did not parse as a mapping"
    return body


# ---------------------------------------------------------------------------
# Parsing + schema validation across all bundled templates
# ---------------------------------------------------------------------------

class TestTemplateParsing:
    @pytest.mark.parametrize("filename", _ALL_TEMPLATE_FILES)
    def test_template_parses_as_yaml(self, filename: str) -> None:
        body = _load_template(filename)
        assert "agents" in body, f"{filename} is missing the agents: block"

    @pytest.mark.parametrize("filename", _ALL_TEMPLATE_FILES)
    def test_template_validates_against_config_schema(self, filename: str) -> None:
        body = _load_template(filename)
        # Raises pydantic.ValidationError on failure — pytest converts
        # to a clear failure message that names the offending field.
        Config.model_validate(body)


# ---------------------------------------------------------------------------
# Per-AI template content guarantees
# ---------------------------------------------------------------------------

class TestPerAITemplateContent:
    @pytest.mark.parametrize("filename", _PER_AI_FILES)
    def test_no_replace_me_placeholders(self, filename: str) -> None:
        """Per-AI templates must ship realistic model + provider strings."""
        text = (_TEMPLATES_DIR / filename).read_text(encoding="utf-8")
        assert "REPLACE_ME" not in text, (
            f"{filename} still contains REPLACE_ME — per-AI templates "
            "must ship realistic 2026 model identifiers."
        )

    @pytest.mark.parametrize("filename", _PER_AI_FILES)
    def test_agents_list_non_empty_and_well_formed(self, filename: str) -> None:
        body = _load_template(filename)
        agents = body["agents"]
        assert isinstance(agents, list) and agents, f"{filename}: empty agents list"
        required = {"id", "model", "skills", "kappa", "context_budget"}
        for ag in agents:
            missing = required - set(ag.keys())
            assert not missing, (
                f"{filename} agent {ag.get('id', '?')} missing fields: {missing}"
            )
            assert isinstance(ag["skills"], list) and ag["skills"], (
                f"{filename} agent {ag['id']} has empty skills"
            )

    @pytest.mark.parametrize("filename", _PER_AI_FILES)
    def test_frontier_slot_has_lowest_kappa_and_highest_context(
        self, filename: str
    ) -> None:
        """Each per-AI template names a clear frontier-tier agent.

        The agent with the lowest ``kappa`` (the frontier slot — most
        careful, lowest task cap) must also carry the largest
        ``context_budget``. This matches the κ / C tier framework in
        ``docs/formulation.md`` ("Hallucination Calibration"): top-
        tier models retain accuracy at long context AND are calibrated
        for low parallelism; the two should always co-vary in the
        frontier direction.

        We deliberately do NOT assert the inverse on the small-tier
        agent, because some templates include reasoning specialists
        (e.g. ``o3-mini`` in the Copilot template) that share the
        frontier κ value but a smaller C than mid-tier — those are
        intentionally non-tiered slots and the spine of the template
        is anchored at the frontier end.
        """
        body = _load_template(filename)
        agents = body["agents"]
        kappas = [int(a["kappa"]) for a in agents]
        contexts = [int(a["context_budget"]) for a in agents]

        min_kappa = min(kappas)
        max_context = max(contexts)
        frontier_idx = kappas.index(min_kappa)

        assert agents[frontier_idx]["context_budget"] == max_context, (
            f"{filename}: frontier-tier agent (lowest κ) does not also have "
            f"the largest context_budget — agents={agents}"
        )

    @pytest.mark.parametrize(
        "filename",
        ["portfolio-claude.yml", "portfolio-gemini.yml"],
    )
    def test_three_tier_template_has_strict_ordering(self, filename: str) -> None:
        """3-tier templates (claude, gemini) follow strict tier ordering.

        The Claude and Gemini templates are pure 3-tier (frontier /
        mid / small) without specialist slots, so declaration order
        equals tier order: κ monotone non-decreasing,
        ``context_budget`` monotone non-increasing.
        """
        body = _load_template(filename)
        agents = body["agents"]
        kappas = [int(a["kappa"]) for a in agents]
        contexts = [int(a["context_budget"]) for a in agents]
        assert kappas == sorted(kappas), (
            f"{filename}: kappa not monotone non-decreasing: {kappas}"
        )
        assert contexts == sorted(contexts, reverse=True), (
            f"{filename}: context_budget not monotone non-increasing: {contexts}"
        )


# ---------------------------------------------------------------------------
# template_for_integration resolver
# ---------------------------------------------------------------------------

class TestTemplateForIntegration:
    @pytest.mark.parametrize(
        "key, expected_filename",
        [
            ("claude", "portfolio-claude.yml"),
            ("copilot", "portfolio-copilot.yml"),
            ("cursor-agent", "portfolio-cursor.yml"),
            ("gemini", "portfolio-gemini.yml"),
        ],
    )
    def test_known_key_returns_per_ai_template(
        self, key: str, expected_filename: str
    ) -> None:
        path = template_for_integration(key)
        assert path.name == expected_filename
        assert path.is_file()

    def test_none_returns_base_template(self) -> None:
        path = template_for_integration(None)
        assert path.name == "base-portfolio.yml"
        assert path.is_file()

    def test_unknown_key_returns_base_template(self) -> None:
        path = template_for_integration("does-not-exist")
        assert path.name == "base-portfolio.yml"

    def test_empty_string_returns_base_template(self) -> None:
        # ``integration_key`` defaults to None; treat empty string the same
        # so callers passing ``os.getenv(...) or ""`` do not blow up.
        path = template_for_integration("")
        assert path.name == "base-portfolio.yml"


# ---------------------------------------------------------------------------
# Integration with solver.autodetect.load_base_portfolio_agents
# ---------------------------------------------------------------------------

class TestLoadBasePortfolioAgents:
    """Make sure the autodetect entrypoint honours the per-AI lookup.

    Covers the wiring inside :func:`solver.autodetect.detect_portfolio`
    where the resolved integration key is forwarded to
    :func:`solver.autodetect.load_base_portfolio_agents`.
    """

    def test_claude_key_loads_anthropic_template(self) -> None:
        from solver.autodetect import load_base_portfolio_agents

        agents = load_base_portfolio_agents("claude")
        ids = {a["id"] for a in agents}
        assert ids == {"opus", "sonnet", "haiku"}

    def test_unknown_key_falls_back_to_base_template(self) -> None:
        from solver.autodetect import load_base_portfolio_agents

        agents = load_base_portfolio_agents("not-a-real-key")
        ids = {a["id"] for a in agents}
        assert ids == {"frontier", "mid", "small"}

    def test_none_falls_back_to_base_template(self) -> None:
        from solver.autodetect import load_base_portfolio_agents

        agents = load_base_portfolio_agents(None)
        ids = {a["id"] for a in agents}
        assert ids == {"frontier", "mid", "small"}
