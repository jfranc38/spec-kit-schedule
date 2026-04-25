"""Tests for solver.autodetect — portfolio detection and CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from solver.autodetect import detect_portfolio, main
from solver.config_schema import Config
from solver.validation import ScheduleInputError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_package_json(deps: dict[str, str]) -> dict:
    return {"name": "test", "version": "1.0.0", "dependencies": deps}


def _agent_ids(config_dict: dict) -> set[str]:
    return {a["id"] for a in config_dict["agents"]}


def _agent_skills(config_dict: dict, agent_id: str) -> list[str]:
    for a in config_dict["agents"]:
        if a["id"] == agent_id:
            return a["skills"]
    return []


# ---------------------------------------------------------------------------
# Core detection tests
# ---------------------------------------------------------------------------

class TestDetectPortfolio:
    def test_react_only_project(self, tmp_path: Path) -> None:
        """package.json with react → frontend + architect (+ tester if tests/ present)."""
        pkg = _make_package_json({"react": "^18.0.0", "react-dom": "^18.0.0"})
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")

        result = detect_portfolio(tmp_path)

        ids = _agent_ids(result)
        assert "architect" in ids
        assert "frontend" in ids
        assert "backend" not in ids

    def test_react_with_tests(self, tmp_path: Path) -> None:
        """package.json with react + tests/ directory → frontend + tester + architect."""
        pkg = _make_package_json({"react": "^18.0.0"})
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        (tmp_path / "tests").mkdir()

        result = detect_portfolio(tmp_path)

        ids = _agent_ids(result)
        assert "architect" in ids
        assert "frontend" in ids
        assert "tester" in ids

    def test_python_only_project(self, tmp_path: Path) -> None:
        """pyproject.toml present → backend + architect, no frontend."""
        (tmp_path / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")

        result = detect_portfolio(tmp_path)

        ids = _agent_ids(result)
        assert "architect" in ids
        assert "backend" in ids
        assert "frontend" not in ids
        assert "python" in _agent_skills(result, "backend")

    def test_python_default_skill(self, tmp_path: Path) -> None:
        """Python project → default_skill = 'backend'."""
        (tmp_path / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
        result = detect_portfolio(tmp_path)
        assert result["default_skill"] == "backend"

    def test_full_stack_project(self, tmp_path: Path) -> None:
        """Full-stack: package.json + requirements.txt + tests/ + docs/ → all agents."""
        pkg = _make_package_json({"react": "^18.0.0"})
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        (tmp_path / "requirements.txt").write_text("flask\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "docs").mkdir()

        result = detect_portfolio(tmp_path)

        ids = _agent_ids(result)
        assert "architect" in ids
        assert "backend" in ids
        assert "frontend" in ids
        assert "tester" in ids
        assert "docs" in ids

    def test_empty_dir_architect_only(self, tmp_path: Path) -> None:
        """Empty directory → architect only, default_skill = 'docs'."""
        result = detect_portfolio(tmp_path)

        ids = _agent_ids(result)
        assert ids == {"architect"}
        assert result["default_skill"] == "docs"

    def test_result_validates_against_config_schema(self, tmp_path: Path) -> None:
        """Every detect_portfolio output must round-trip through Config.model_validate()."""
        (tmp_path / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
        (tmp_path / "package.json").write_text(
            json.dumps(_make_package_json({"react": "^18.0.0"})), encoding="utf-8"
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "docs").mkdir()

        result = detect_portfolio(tmp_path)
        # Should not raise
        validated = Config.model_validate(result)
        assert len(validated.agents) >= 1

    def test_rust_project(self, tmp_path: Path) -> None:
        """Cargo.toml → backend with rust skill."""
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "myapp"\n', encoding="utf-8")
        result = detect_portfolio(tmp_path)
        assert "backend" in _agent_ids(result)
        assert "rust" in _agent_skills(result, "backend")

    def test_go_project(self, tmp_path: Path) -> None:
        """go.mod → backend with go skill."""
        (tmp_path / "go.mod").write_text("module example.com/app\n", encoding="utf-8")
        result = detect_portfolio(tmp_path)
        assert "backend" in _agent_ids(result)
        assert "go" in _agent_skills(result, "backend")

    def test_node_backend_project(self, tmp_path: Path) -> None:
        """package.json with express → backend with javascript skill."""
        pkg = _make_package_json({"express": "^4.0.0"})
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        result = detect_portfolio(tmp_path)
        ids = _agent_ids(result)
        assert "backend" in ids
        assert "javascript" in _agent_skills(result, "backend")

    def test_vue_frontend(self, tmp_path: Path) -> None:
        """package.json with vue → frontend with vue skill."""
        pkg = _make_package_json({"vue": "^3.0.0"})
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        result = detect_portfolio(tmp_path)
        assert "frontend" in _agent_ids(result)
        assert "vue" in _agent_skills(result, "frontend")

    def test_svelte_frontend(self, tmp_path: Path) -> None:
        """package.json with svelte → frontend with svelte skill."""
        pkg = _make_package_json({"svelte": "^4.0.0"})
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        result = detect_portfolio(tmp_path)
        assert "frontend" in _agent_ids(result)
        assert "svelte" in _agent_skills(result, "frontend")

    def test_jvm_project(self, tmp_path: Path) -> None:
        """pom.xml → backend with java skill."""
        (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        result = detect_portfolio(tmp_path)
        assert "backend" in _agent_ids(result)
        assert "java" in _agent_skills(result, "backend")

    def test_docs_directory_detected(self, tmp_path: Path) -> None:
        """docs/ directory → docs agent emitted."""
        (tmp_path / "docs").mkdir()
        result = detect_portfolio(tmp_path)
        assert "docs" in _agent_ids(result)

    def test_migrations_adds_schema_rule(self, tmp_path: Path) -> None:
        """migrations/ directory detected → skill_rules includes migrations pattern."""
        (tmp_path / "migrations").mkdir()
        result = detect_portfolio(tmp_path)
        patterns = [r["pattern"] for r in result["skill_rules"]]
        assert "migrations/" in patterns

    def test_invalid_project_dir_raises(self, tmp_path: Path) -> None:
        """Passing a non-existent path raises ScheduleInputError."""
        with pytest.raises(ScheduleInputError, match="not a directory"):
            detect_portfolio(tmp_path / "nonexistent")

    def test_provider_propagated(self, tmp_path: Path) -> None:
        """default_provider is written into all agent provider fields."""
        (tmp_path / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
        result = detect_portfolio(tmp_path, default_provider="openai")
        for agent in result["agents"]:
            assert agent["provider"] == "openai"

    def test_token_estimates_present(self, tmp_path: Path) -> None:
        """token_estimates contains simple/medium/complex/review keys."""
        result = detect_portfolio(tmp_path)
        te = result["token_estimates"]
        for key in ("simple", "medium", "complex", "review"):
            assert key in te

    def test_skill_rules_non_empty(self, tmp_path: Path) -> None:
        """skill_rules is always a non-empty list."""
        result = detect_portfolio(tmp_path)
        assert isinstance(result["skill_rules"], list)
        assert len(result["skill_rules"]) > 0

    def test_no_magic_numbers_in_kappa(self, tmp_path: Path) -> None:
        """Kappa values match the defaults from solver.defaults (not hardcoded)."""
        from solver.defaults import KAPPA_DEFAULT
        (tmp_path / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
        result = detect_portfolio(tmp_path)
        backend_agent = next(a for a in result["agents"] if a["id"] == "backend")
        assert backend_agent["kappa"] == KAPPA_DEFAULT

    def test_invalid_package_json_handled(self, tmp_path: Path) -> None:
        """Malformed package.json falls back gracefully (no crash)."""
        (tmp_path / "package.json").write_text("{not valid json!!}", encoding="utf-8")
        # Should not raise — malformed JSON is silently ignored
        result = detect_portfolio(tmp_path)
        # No frontend detected since package.json couldn't be parsed
        assert "frontend" not in _agent_ids(result)

    def test_skill_rules_custom_dirs(self, tmp_path: Path) -> None:
        """Custom directories produce skill_rules entries with sensible defaults."""
        for d in ("api", "components", "migrations"):
            (tmp_path / d).mkdir()
        result = detect_portfolio(tmp_path)
        patterns = {r["pattern"]: r["skill"] for r in result["skill_rules"]}
        # api/ → api skill
        assert patterns.get("api/") == "api"
        # components/ → frontend skill
        assert patterns.get("components/") == "frontend"
        # migrations/ is already in the canonical set
        assert "migrations/" in patterns

    def test_unknown_dir_gets_backend_skill(self, tmp_path: Path) -> None:
        """An unrecognised directory defaults to 'backend' skill rule."""
        (tmp_path / "myservice").mkdir()
        result = detect_portfolio(tmp_path)
        patterns = {r["pattern"]: r["skill"] for r in result["skill_rules"]}
        assert patterns.get("myservice/") == "backend"

    def test_nestjs_node_backend(self, tmp_path: Path) -> None:
        """package.json with @nestjs/core → backend detected with javascript skill."""
        pkg = _make_package_json({"@nestjs/core": "^10.0.0", "@nestjs/common": "^10.0.0"})
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        result = detect_portfolio(tmp_path)
        assert "backend" in _agent_ids(result)
        assert "javascript" in _agent_skills(result, "backend")

    def test_next_js_treated_as_react(self, tmp_path: Path) -> None:
        """next.js package → frontend with 'react' skill."""
        pkg = _make_package_json({"next": "^14.0.0", "react": "^18.0.0"})
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        result = detect_portfolio(tmp_path)
        assert "frontend" in _agent_ids(result)
        assert "react" in _agent_skills(result, "frontend")

    def test_gradle_project(self, tmp_path: Path) -> None:
        """build.gradle → backend with java skill."""
        (tmp_path / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
        result = detect_portfolio(tmp_path)
        assert "backend" in _agent_ids(result)
        assert "java" in _agent_skills(result, "backend")

    def test_test_dir_variations(self, tmp_path: Path) -> None:
        """'test' (singular) directory is also detected."""
        (tmp_path / "test").mkdir()
        result = detect_portfolio(tmp_path)
        assert "tester" in _agent_ids(result)

    def test_docs_dir_in_skill_rules(self, tmp_path: Path) -> None:
        """docs/ directory is mapped to 'review' skill in skill_rules."""
        (tmp_path / "docs").mkdir()
        result = detect_portfolio(tmp_path)
        patterns = {r["pattern"]: r["skill"] for r in result["skill_rules"]}
        # docs/ gets 'review' skill from _build_skill_rules dir heuristic
        # (it is also already in the canonical set as "docs/")
        assert "docs/" in patterns

    def test_components_dir_in_skill_rules(self, tmp_path: Path) -> None:
        """components/ → 'frontend' skill in the discovered rules."""
        (tmp_path / "components").mkdir()
        result = detect_portfolio(tmp_path)
        patterns = {r["pattern"]: r["skill"] for r in result["skill_rules"]}
        assert patterns.get("components/") == "frontend"

    def test_database_dir_in_skill_rules(self, tmp_path: Path) -> None:
        """database/ → 'schema' skill."""
        (tmp_path / "database").mkdir()
        result = detect_portfolio(tmp_path)
        patterns = {r["pattern"]: r["skill"] for r in result["skill_rules"]}
        assert patterns.get("database/") == "schema"

    def test_nuxt_frontend(self, tmp_path: Path) -> None:
        """package.json with nuxt → frontend with 'vue' skill."""
        pkg = _make_package_json({"nuxt": "^3.0.0"})
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        result = detect_portfolio(tmp_path)
        assert "frontend" in _agent_ids(result)
        assert "vue" in _agent_skills(result, "frontend")

    def test_dev_dependencies_detected(self, tmp_path: Path) -> None:
        """devDependencies are also scanned for framework detection."""
        pkg = {
            "name": "test",
            "dependencies": {},
            "devDependencies": {"react": "^18.0.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
        result = detect_portfolio(tmp_path)
        assert "frontend" in _agent_ids(result)


class TestInteractiveRefine:
    def test_interactive_refine_accepts_defaults(self, tmp_path: Path, monkeypatch) -> None:
        """_interactive_refine with all-empty input keeps original values."""
        from solver.autodetect import _interactive_refine

        monkeypatch.setattr("builtins.input", lambda prompt: "")

        config_dict = detect_portfolio(tmp_path)
        original_agents = [a.copy() for a in config_dict["agents"]]
        refined = _interactive_refine(config_dict)

        for orig, new_a in zip(original_agents, refined["agents"], strict=True):
            assert new_a["id"] == orig["id"]
            assert new_a["model"] == orig["model"]
            assert new_a["kappa"] == orig["kappa"]

    def test_interactive_refine_overrides(self, tmp_path: Path, monkeypatch) -> None:
        """_interactive_refine applies user-supplied overrides."""
        from solver.autodetect import _interactive_refine

        inputs_iter = iter(["custom-id", "gpt-4o", "8", "custom"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(inputs_iter))

        (tmp_path / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
        config_dict = detect_portfolio(tmp_path)
        # Make it a single-agent config for simplicity
        config_dict["agents"] = [config_dict["agents"][0]]
        refined = _interactive_refine(config_dict)

        assert refined["agents"][0]["id"] == "custom-id"
        assert refined["agents"][0]["model"] == "gpt-4o"
        assert refined["agents"][0]["kappa"] == 8
        assert refined["default_skill"] == "custom"


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

class TestAutodetectCLI:
    def test_dry_run_prints_yaml_stdout(self, tmp_path: Path, capsys) -> None:
        """--dry-run prints valid YAML to stdout and does not write any file."""
        (tmp_path / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
        main(["--project-dir", str(tmp_path), "--dry-run"])
        captured = capsys.readouterr()
        assert captured.out.strip()
        parsed = yaml.safe_load(captured.out)
        assert "agents" in parsed
        # No file written in dry-run
        yml_files = list(tmp_path.glob("*.yml"))
        assert not yml_files

    def test_dry_run_output_round_trips_config(self, tmp_path: Path, capsys) -> None:
        """YAML from --dry-run passes Config.model_validate()."""
        (tmp_path / "package.json").write_text(
            json.dumps(_make_package_json({"react": "^18.0.0"})), encoding="utf-8"
        )
        main(["--project-dir", str(tmp_path), "--dry-run"])
        captured = capsys.readouterr()
        parsed = yaml.safe_load(captured.out)
        # Should not raise
        Config.model_validate(parsed)

    def test_output_writes_file(self, tmp_path: Path) -> None:
        """--output writes YAML file that can be read back."""
        out_file = tmp_path / "schedule-config.yml"
        main(["--project-dir", str(tmp_path), "--output", str(out_file)])
        assert out_file.exists()
        parsed = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert "agents" in parsed

    def test_output_refuses_overwrite_without_force(self, tmp_path: Path) -> None:
        """--output refuses to overwrite existing file unless --force is given."""
        out_file = tmp_path / "schedule-config.yml"
        out_file.write_text("# existing\n", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            main(["--project-dir", str(tmp_path), "--output", str(out_file)])
        assert exc_info.value.code == 2

    def test_force_allows_overwrite(self, tmp_path: Path) -> None:
        """--force allows overwriting an existing config file."""
        out_file = tmp_path / "schedule-config.yml"
        out_file.write_text("# existing\n", encoding="utf-8")
        main(["--project-dir", str(tmp_path), "--output", str(out_file), "--force"])
        parsed = yaml.safe_load(out_file.read_text(encoding="utf-8"))
        assert "agents" in parsed

    def test_provider_flag(self, tmp_path: Path, capsys) -> None:
        """--provider flag is propagated to agent entries."""
        (tmp_path / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
        main(["--project-dir", str(tmp_path), "--dry-run", "--provider", "openai"])
        captured = capsys.readouterr()
        parsed = yaml.safe_load(captured.out)
        for agent in parsed["agents"]:
            assert agent["provider"] == "openai"

    def test_no_output_no_dry_run_prints_to_stdout(self, tmp_path: Path, capsys) -> None:
        """Without --output or --dry-run, prints YAML to stdout."""
        main(["--project-dir", str(tmp_path)])
        captured = capsys.readouterr()
        parsed = yaml.safe_load(captured.out)
        assert "agents" in parsed

    def test_module_invocation(self, tmp_path: Path) -> None:
        """python -m solver.autodetect --dry-run works as a subprocess."""
        result = subprocess.run(
            [sys.executable, "-m", "solver.autodetect",
             "--project-dir", str(tmp_path), "--dry-run"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parents[1]),
        )
        assert result.returncode == 0
        parsed = yaml.safe_load(result.stdout)
        assert "agents" in parsed
