"""Tests for solver.fleet_discover — AI fleet discovery and role heuristics."""

from __future__ import annotations

__all__: list[str] = []

from pathlib import Path

from solver.fleet_discover import (
    DiscoveredAgent,
    classify_role,
    discover_fleet,
    parse_frontmatter,
)


def _setup_specify(project: Path) -> None:
    (project / ".specify").mkdir(parents=True, exist_ok=True)


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    def test_no_frontmatter_returns_empty(self) -> None:
        assert parse_frontmatter("# Hello\n\nBody only.") == {}

    def test_simple_frontmatter(self) -> None:
        body = "---\ndescription: A reviewer\n---\n\nBody"
        assert parse_frontmatter(body) == {"description": "A reviewer"}

    def test_multiple_keys(self) -> None:
        body = (
            "---\n"
            "description: My agent\n"
            "model: opus\n"
            "tools: [Read, Edit]\n"
            "---\nBody"
        )
        result = parse_frontmatter(body)
        assert result["description"] == "My agent"
        assert result["model"] == "opus"
        assert result["tools"] == ["Read", "Edit"]

    def test_malformed_yaml_returns_empty(self) -> None:
        body = "---\nthis is: : not valid: yaml\n---\nBody"
        # yaml.safe_load raises → returns {}
        assert parse_frontmatter(body) == {}

    def test_non_dict_yaml_returns_empty(self) -> None:
        body = "---\n- item\n- item2\n---\nBody"
        # Top-level list, not a dict → returns {}
        assert parse_frontmatter(body) == {}


# ---------------------------------------------------------------------------
# classify_role
# ---------------------------------------------------------------------------


class TestClassifyRole:
    def test_reviewer_by_name(self) -> None:
        # Pure reviewer name with no impl-keyword overlap
        assert classify_role("pr-review", None) == "reviewer"

    def test_reviewer_by_description(self) -> None:
        assert (
            classify_role("checker", "Performs a security audit before commit")
            == "reviewer"
        )

    def test_implementer_by_name(self) -> None:
        assert classify_role("backend-implementer", None) == "implementer"

    def test_implementer_by_description(self) -> None:
        assert (
            classify_role("worker", "Builds the feature end-to-end")
            == "implementer"
        )

    def test_hybrid_when_both_match(self) -> None:
        # "code-reviewer" has 'review' (reviewer) AND 'code' (impl) → hybrid
        assert classify_role("code-reviewer", None) == "hybrid"

    def test_hybrid_when_neither_matches(self) -> None:
        # Generic agent with no signal words
        assert classify_role("helper", "An agent that helps with stuff") == "hybrid"

    def test_qa_classified_as_reviewer(self) -> None:
        assert classify_role("qa-bot", "QA assistant") == "reviewer"


# ---------------------------------------------------------------------------
# discover_fleet — claude
# ---------------------------------------------------------------------------


class TestDiscoverFleetClaude:
    def test_no_integration_key_yields_empty(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        assert discover_fleet(None, tmp_path) == []
        assert discover_fleet("", tmp_path) == []

    def test_missing_dirs_yield_empty(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        assert discover_fleet("claude", tmp_path) == []

    def test_claude_agents_directory(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        _write(
            tmp_path / ".claude" / "agents" / "pr-review.md",
            "---\ndescription: Reviews PRs for quality issues.\n---\n\nReviewer body.",
        )
        _write(
            tmp_path / ".claude" / "agents" / "implementer.md",
            "---\ndescription: Implements features.\nmodel: opus\n---\n\nBody.",
        )

        fleet = discover_fleet("claude", tmp_path)
        names = {a.name for a in fleet}
        assert names == {"pr-review", "implementer"}

        roles = {a.name: a.role for a in fleet}
        assert roles["pr-review"] == "reviewer"
        assert roles["implementer"] == "implementer"

        impl = next(a for a in fleet if a.name == "implementer")
        assert impl.model == "opus"
        assert impl.description == "Implements features."

    def test_claude_skills_directory(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        _write(
            tmp_path / ".claude" / "skills" / "test-runner" / "SKILL.md",
            "---\ndescription: Runs the project test suite.\n---\nBody",
        )
        fleet = discover_fleet("claude", tmp_path)
        assert len(fleet) == 1
        # SKILL.md uses parent directory name as the agent name
        assert fleet[0].name == "test-runner"
        assert fleet[0].role == "reviewer"  # 'test' keyword

    def test_claude_combines_agents_and_skills(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        _write(
            tmp_path / ".claude" / "agents" / "builder.md",
            "---\ndescription: Builds things.\n---\n",
        )
        _write(
            tmp_path / ".claude" / "skills" / "qa-bot" / "SKILL.md",
            "---\ndescription: Quality assurance.\n---\n",
        )
        fleet = discover_fleet("claude", tmp_path)
        names = {a.name for a in fleet}
        assert names == {"builder", "qa-bot"}


# ---------------------------------------------------------------------------
# discover_fleet — copilot, cursor-agent, gemini
# ---------------------------------------------------------------------------


class TestDiscoverFleetOtherIntegrations:
    def test_copilot_agent_files(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        _write(
            tmp_path / ".github" / "agents" / "reviewer.agent.md",
            "---\ndescription: Reviews PRs.\n---\n",
        )
        fleet = discover_fleet("copilot", tmp_path)
        assert len(fleet) == 1
        # ".agent" suffix stripped
        assert fleet[0].name == "reviewer"
        assert fleet[0].role == "reviewer"

    def test_cursor_skill_files(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        _write(
            tmp_path / ".cursor" / "skills" / "implementer" / "SKILL.md",
            "---\ndescription: Implements features.\n---\n",
        )
        fleet = discover_fleet("cursor-agent", tmp_path)
        assert len(fleet) == 1
        assert fleet[0].name == "implementer"

    def test_gemini_command_files(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        _write(
            tmp_path / ".gemini" / "commands" / "review.md",
            "---\ndescription: Review changes.\n---\n",
        )
        fleet = discover_fleet("gemini", tmp_path)
        assert len(fleet) == 1
        assert fleet[0].name == "review"


# ---------------------------------------------------------------------------
# discover_fleet — generic fallback for unknown integrations
# ---------------------------------------------------------------------------


class TestDiscoverFleetGenericFallback:
    def test_unknown_integration_uses_generic_layout(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        _write(
            tmp_path / ".aider" / "skills" / "tester.md",
            "---\ndescription: Runs tests.\n---\n",
        )
        fleet = discover_fleet("aider", tmp_path)
        assert len(fleet) == 1
        assert fleet[0].name == "tester"
        assert fleet[0].role == "reviewer"

    def test_no_matching_files_returns_empty(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        # .opencode/ exists but is empty
        (tmp_path / ".opencode").mkdir()
        assert discover_fleet("opencode", tmp_path) == []


# ---------------------------------------------------------------------------
# Frontmatter edge cases
# ---------------------------------------------------------------------------


class TestFrontmatterEdgeCases:
    def test_no_frontmatter_still_yields_record(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        _write(tmp_path / ".claude" / "agents" / "naked.md", "# Just a header\n\nNo frontmatter.")
        fleet = discover_fleet("claude", tmp_path)
        assert len(fleet) == 1
        assert fleet[0].name == "naked"
        assert fleet[0].description is None
        assert fleet[0].model is None
        assert fleet[0].tools == []

    def test_tools_as_csv_string(self, tmp_path: Path) -> None:
        _setup_specify(tmp_path)
        _write(
            tmp_path / ".claude" / "agents" / "tooled.md",
            "---\ntools: Read, Edit, Bash\n---\n",
        )
        fleet = discover_fleet("claude", tmp_path)
        assert fleet[0].tools == ["Read", "Edit", "Bash"]

    def test_dataclass_fields_present(self, tmp_path: Path) -> None:
        # Smoke test that DiscoveredAgent has the documented fields
        a = DiscoveredAgent(
            name="x",
            file=Path("/tmp/x.md"),
            description="d",
            model="m",
        )
        assert a.tools == []
        assert a.raw_frontmatter == {}
        assert a.role == "hybrid"
