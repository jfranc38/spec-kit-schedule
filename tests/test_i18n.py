"""Tests for solver.i18n and solver.i18n_catalog."""

from __future__ import annotations

import pytest

from solver.i18n import detect_lang, t
from solver.i18n_catalog import (
    MESSAGES,
    WARN_ANYTIME_TIMEOUT,
    WARN_COST_SCALE_UNDERFLOW,
    WARN_PARALLEL_WRITE_CONFLICT,
    WARN_PHASE2_FALLBACK,
    WARN_PHASE3_FALLBACK,
)

# ── t() happy path ────────────────────────────────────────────────────────────


class TestTranslate:
    def test_known_key_en(self):
        msg = t("empty_agents")
        assert "agents" in msg.lower()

    def test_known_key_es(self):
        msg = t("empty_agents", lang="es")
        assert "agente" in msg.lower()

    def test_placeholder_interpolation(self):
        msg = t("duplicate_task_id", task_id="T001", line=42)
        assert "T001" in msg
        assert "42" in msg

    def test_placeholder_interpolation_es(self):
        msg = t("duplicate_task_id", lang="es", task_id="T099", line=7)
        assert "T099" in msg
        assert "7" in msg

    def test_budget_exceeded_interpolation(self):
        msg = t("budget_exceeded", total=50_000, budget=30_000)
        assert "50000" in msg
        assert "30000" in msg

    def test_kappa_exceeded_interpolation(self):
        msg = t("kappa_exceeded", count=5, skill="backend", kappa=3)
        assert "5" in msg
        assert "backend" in msg
        assert "3" in msg

    def test_skill_budget_exceeded_interpolation(self):
        msg = t("skill_budget_exceeded", skill="frontend", required=10_000, have=5_000)
        assert "frontend" in msg
        assert "10000" in msg

    def test_no_kwargs_returns_template(self):
        msg = t("no_tasks_found", path="/some/path.md")
        assert "/some/path.md" in msg

    def test_phase2_fallback_no_placeholders(self):
        msg = t("phase2_fallback")
        assert len(msg) > 10

    def test_phase3_fallback_no_placeholders(self):
        msg = t("phase3_fallback")
        assert len(msg) > 10
        assert msg != t("phase2_fallback")

    def test_cost_scale_underflow_no_placeholders(self):
        msg = t("cost_scale_underflow")
        assert len(msg) > 10

    def test_parallel_write_conflict_interpolation(self):
        msg = t("parallel_write_conflict", file="src/main.py", task_ids=["T001", "T002"])
        assert "src/main.py" in msg


# ── Missing key fallback ──────────────────────────────────────────────────────


class TestMissingKey:
    def test_missing_key_returns_key_string(self):
        result = t("nonexistent_key_xyz")
        assert result == "nonexistent_key_xyz"
        # Warning should be emitted to stderr via logging
        # (captured only if logging is set up; we just verify no exception)

    def test_missing_lang_falls_back_to_en(self):
        result = t("empty_agents", lang="zh")
        en_result = t("empty_agents", lang="en")
        assert result == en_result

    def test_missing_key_with_missing_lang(self):
        result = t("nonexistent_key_xyz", lang="fr")
        assert result == "nonexistent_key_xyz"


# ── detect_lang() ─────────────────────────────────────────────────────────────


class TestDetectLang:
    def test_default_is_en(self, monkeypatch):
        for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
            monkeypatch.delenv(var, raising=False)
        assert detect_lang() == "en"

    def test_lang_es_es_utf8(self, monkeypatch):
        for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("LANG", "es_ES.UTF-8")
        assert detect_lang() == "es"

    def test_lang_posix_falls_back_to_en(self, monkeypatch):
        monkeypatch.setenv("LANG", "C")
        for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES"):
            monkeypatch.delenv(var, raising=False)
        assert detect_lang() == "en"

    def test_lc_all_overrides(self, monkeypatch):
        monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
        monkeypatch.delenv("LANGUAGE", raising=False)
        assert detect_lang() == "fr"

    def test_language_env_var(self, monkeypatch):
        monkeypatch.setenv("LANGUAGE", "de:en")
        assert detect_lang() == "de"


# ── Catalog completeness ──────────────────────────────────────────────────────


REQUIRED_KEYS = [
    "duplicate_task_id",
    "unresolved_dep",
    "cycle_detected",
    "skill_uncovered",
    "budget_exceeded",
    "skill_budget_exceeded",
    "kappa_exceeded",
    "parallel_write_conflict",
    "phase2_fallback",
    "phase3_fallback",
    "cost_scale_underflow",
    "no_tasks_found",
    "empty_agents",
    "task_no_skill",
    "phase1_infeasible_proven",
    "phase1_infeasible_lb_exceeds_horizon",
    "phase1_infeasible_timeout",
    "replan_fixed_invalid_duration",
    # validation.py
    "validation_must_be_positive",
    "validation_agent_config_errors",
    "validation_agent_config_generic",
    "validation_solver_config_errors",
    "validation_solver_config_generic",
    "validation_input_not_object",
    "validation_input_missing_keys",
    "validation_input_tasks_not_list",
    "validation_input_edges_not_list",
    "validation_input_agents_not_list",
    "validation_input_config_not_object",
    "validation_task_missing_id",
    "validation_duplicate_task_id_input",
    "validation_malformed_edge",
    "validation_edge_unknown_task",
    # wave_executor.py
    "wave_exec_no_tasks_in_wave",
    "wave_exec_wave_count_mismatch",
    "wave_exec_no_agents",
    "wave_exec_unknown_agent",
]

# WARN_* constants must each map to a present catalog key.
EMITTED_WARN_CODES = [
    WARN_ANYTIME_TIMEOUT,
    WARN_COST_SCALE_UNDERFLOW,
    WARN_PARALLEL_WRITE_CONFLICT,
    WARN_PHASE2_FALLBACK,
    WARN_PHASE3_FALLBACK,
]


class TestCatalogCompleteness:
    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_key_has_en(self, key):
        assert key in MESSAGES, f"Missing key {key!r} in MESSAGES"
        assert "en" in MESSAGES[key], f"Key {key!r} missing 'en' translation"
        assert MESSAGES[key]["en"], f"Key {key!r} has empty 'en' translation"

    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_key_has_es(self, key):
        assert key in MESSAGES, f"Missing key {key!r} in MESSAGES"
        assert "es" in MESSAGES[key], f"Key {key!r} missing 'es' translation"
        assert MESSAGES[key]["es"], f"Key {key!r} has empty 'es' translation"

    def test_all_keys_are_snake_case(self):
        import re
        snake_re = re.compile(r"^[a-z][a-z0-9_]*$")
        for key in MESSAGES:
            assert snake_re.match(key), f"Key {key!r} is not snake_case"

    @pytest.mark.parametrize("code", EMITTED_WARN_CODES)
    def test_warn_constant_has_catalog_entry(self, code):
        assert code in MESSAGES, f"WARN constant {code!r} has no catalog entry"
