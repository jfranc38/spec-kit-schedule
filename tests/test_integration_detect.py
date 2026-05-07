"""Tests for solver.integration_detect — AI assistant marker detection."""

from __future__ import annotations

__all__: list[str] = []

import json
from pathlib import Path

from solver.integration_detect import (
    KNOWN_INTEGRATIONS,
    detect_integration,
    display_name,
)


def _setup_specify(project: Path) -> Path:
    sp = project / ".specify"
    sp.mkdir(parents=True, exist_ok=True)
    return sp


# ---------------------------------------------------------------------------
# detect_integration — primary marker
# ---------------------------------------------------------------------------


class TestDetectIntegration:
    def test_no_marker_returns_none(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        assert detect_integration(tmp_path) is None

    def test_integration_key_wins(self, tmp_path: Path) -> None:
        sp = _setup_specify(tmp_path)
        (sp / "integration.json").write_text(
            json.dumps({"integration_key": "claude"}), encoding="utf-8"
        )
        assert detect_integration(tmp_path) == "claude"

    def test_installed_integrations_fallback(self, tmp_path: Path) -> None:
        sp = _setup_specify(tmp_path)
        (sp / "integration.json").write_text(
            json.dumps({"installed_integrations": ["copilot", "claude"]}),
            encoding="utf-8",
        )
        assert detect_integration(tmp_path) == "copilot"

    def test_init_options_integration_key(self, tmp_path: Path) -> None:
        sp = _setup_specify(tmp_path)
        (sp / "init-options.json").write_text(
            json.dumps({"integration": "cursor-agent"}), encoding="utf-8"
        )
        assert detect_integration(tmp_path) == "cursor-agent"

    def test_init_options_legacy_ai_field(self, tmp_path: Path) -> None:
        sp = _setup_specify(tmp_path)
        (sp / "init-options.json").write_text(
            json.dumps({"ai": "gemini"}), encoding="utf-8"
        )
        assert detect_integration(tmp_path) == "gemini"

    def test_priority_integration_json_over_init_options(self, tmp_path: Path) -> None:
        sp = _setup_specify(tmp_path)
        (sp / "integration.json").write_text(
            json.dumps({"integration_key": "claude"}), encoding="utf-8"
        )
        (sp / "init-options.json").write_text(
            json.dumps({"integration": "copilot"}), encoding="utf-8"
        )
        # integration.json wins
        assert detect_integration(tmp_path) == "claude"

    def test_malformed_json_falls_through(self, tmp_path: Path) -> None:
        sp = _setup_specify(tmp_path)
        (sp / "integration.json").write_text("{not valid", encoding="utf-8")
        (sp / "init-options.json").write_text(
            json.dumps({"integration": "gemini"}), encoding="utf-8"
        )
        # Malformed integration.json → fall through to init-options.json
        assert detect_integration(tmp_path) == "gemini"

    def test_empty_strings_ignored(self, tmp_path: Path) -> None:
        sp = _setup_specify(tmp_path)
        (sp / "integration.json").write_text(
            json.dumps({"integration_key": ""}), encoding="utf-8"
        )
        (sp / "init-options.json").write_text(
            json.dumps({"integration": "claude"}), encoding="utf-8"
        )
        assert detect_integration(tmp_path) == "claude"

    def test_non_dict_integration_json(self, tmp_path: Path) -> None:
        sp = _setup_specify(tmp_path)
        (sp / "integration.json").write_text("[1, 2, 3]", encoding="utf-8")
        # Top-level list is rejected, falls through to None
        assert detect_integration(tmp_path) is None


# ---------------------------------------------------------------------------
# display_name
# ---------------------------------------------------------------------------


class TestDisplayName:
    def test_none_yields_generic_label(self) -> None:
        assert display_name(None) == "your AI assistant"

    def test_known_key(self) -> None:
        assert display_name("claude") == "Claude Code"
        assert display_name("copilot") == "GitHub Copilot"

    def test_unknown_key_title_cased(self) -> None:
        # "my-custom-ai" → "My Custom Ai"
        assert display_name("my-custom-ai") == "My Custom Ai"

    def test_known_integrations_table_has_claude(self) -> None:
        assert "claude" in KNOWN_INTEGRATIONS
        assert "copilot" in KNOWN_INTEGRATIONS
        assert "cursor-agent" in KNOWN_INTEGRATIONS
        assert "gemini" in KNOWN_INTEGRATIONS
