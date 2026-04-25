"""Auto-detect a project's tech stack and generate a starter schedule-config.yml.

Usage (module):
    from solver.autodetect import detect_portfolio
    config_dict = detect_portfolio(Path("."))

Usage (CLI):
    python -m solver.autodetect [--project-dir .] [--output schedule-config.yml]
                                [--force] [--dry-run] [--interactive]
                                [--provider anthropic]
"""

from __future__ import annotations

__all__ = ["detect_portfolio"]

import argparse
import contextlib
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .defaults import (
    CONTEXT_BUDGET_KTOKENS_DEFAULT,
    KAPPA_DEFAULT,
    SPEED_FACTOR_DEFAULT,
    TOKEN_ESTIMATES,
)
from .validation import ScheduleInputError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _read_package_json(project_dir: Path) -> dict:
    """Return parsed package.json or empty dict if absent/unreadable."""
    pkg = project_dir / "package.json"
    if not pkg.is_file():
        return {}
    try:
        return json.loads(pkg.read_text(encoding="utf-8"))
    except Exception:
        log.debug("Could not parse package.json in %s", project_dir)
        return {}


def _has_file(project_dir: Path, *names: str) -> bool:
    """Return True if any of *names* exist directly under project_dir."""
    return any((project_dir / name).exists() for name in names)


def _has_glob(project_dir: Path, pattern: str) -> bool:
    """Return True if at least one file matches *pattern* under project_dir."""
    try:
        return next(project_dir.rglob(pattern), None) is not None
    except Exception:
        return False


def _list_direct_dirs(project_dir: Path) -> set[str]:
    """Return names of immediate subdirectories."""
    try:
        return {p.name for p in project_dir.iterdir() if p.is_dir() and not p.name.startswith(".")}
    except Exception:
        return set()


def _detect_stacks(project_dir: Path) -> dict[str, bool]:
    """Return a dict of detected stack signals."""
    pkg = _read_package_json(project_dir)
    all_deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        all_deps.update(pkg.get(key, {}).keys())

    # Frontend frameworks
    frontend_frameworks = {"react", "vue", "svelte", "@angular/core", "next", "nuxt"}
    frontend_detected = bool(all_deps & frontend_frameworks)
    frontend_framework = (
        "react" if "react" in all_deps or "next" in all_deps
        else "vue" if "vue" in all_deps or "nuxt" in all_deps
        else "svelte" if "svelte" in all_deps
        else "react"  # default when generic frontend detected
    )

    # Node backends
    node_backend_packages = {"express", "fastify", "koa", "@nestjs/core", "@nestjs/common"}
    node_backend_detected = bool(all_deps & node_backend_packages)

    # Python backend
    python_detected = _has_file(
        project_dir, "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"
    )

    # Other stacks
    rust_detected = _has_file(project_dir, "Cargo.toml")
    go_detected = _has_file(project_dir, "go.mod")
    jvm_detected = _has_file(project_dir, "pom.xml") or bool(
        list(project_dir.glob("build.gradle")) + list(project_dir.glob("build.gradle.kts"))
    )

    dirs = _list_direct_dirs(project_dir)

    # Test detection: tests/ directory or test files
    tests_detected = "tests" in dirs or "test" in dirs or "spec" in dirs or _has_glob(
        project_dir, "*.test.*"
    )

    # DB migration detection
    migrations_detected = "migrations" in dirs or "db" in dirs and _has_glob(
        project_dir, "*.sql"
    )

    # Deploy/infra detection
    docker_detected = _has_file(project_dir, "Dockerfile") or bool(
        list(project_dir.glob("docker-compose*"))
    )

    # Docs detection
    docs_detected = "docs" in dirs or "doc" in dirs

    return {
        "frontend": frontend_detected,
        "frontend_framework": frontend_framework,
        "node_backend": node_backend_detected,
        "python": python_detected,
        "rust": rust_detected,
        "go": go_detected,
        "jvm": jvm_detected,
        "backend": python_detected or node_backend_detected or rust_detected or go_detected or jvm_detected,
        "tests": tests_detected,
        "migrations": migrations_detected,
        "docker": docker_detected,
        "docs": docs_detected,
        "dirs": dirs,
    }


def _build_backend_skills(stacks: dict) -> list[str]:
    """Build the skills list for a backend agent based on detected stacks."""
    skills = ["backend", "api", "database"]
    if stacks["python"]:
        skills.append("python")
    if stacks["node_backend"]:
        skills.append("javascript")
    if stacks["rust"]:
        skills.append("rust")
    if stacks["go"]:
        skills.append("go")
    if stacks["jvm"]:
        skills.append("java")
    return skills


def _build_skill_rules(stacks: dict) -> list[dict]:
    """Build skill_rules list based on detected project layout."""
    dirs: set[str] = stacks.get("dirs", set())

    # Start from the canonical template set
    rules: list[dict[str, str]] = [
        {"pattern": "tests/", "skill": "test"},
        {"pattern": "test_", "skill": "test"},
        {"pattern": "_test.py", "skill": "test"},
        {"pattern": ".test.", "skill": "test"},
        {"pattern": "spec/", "skill": "test"},
        {"pattern": "src/models/", "skill": "schema"},
        {"pattern": "migrations/", "skill": "schema"},
        {"pattern": "src/api/", "skill": "api"},
        {"pattern": "src/services/", "skill": "backend"},
        {"pattern": "src/components/", "skill": "frontend"},
        {"pattern": "src/pages/", "skill": "frontend"},
        {"pattern": "src/hooks/", "skill": "frontend"},
        {"pattern": ".css", "skill": "frontend"},
        {"pattern": ".tsx", "skill": "frontend"},
        {"pattern": ".jsx", "skill": "frontend"},
        {"pattern": "docs/", "skill": "review"},
        {"pattern": "README", "skill": "review"},
        {"pattern": ".md", "skill": "review"},
    ]

    # Add project-specific directories discovered
    seen_patterns = {r["pattern"] for r in rules}
    for d in sorted(dirs):
        pattern = f"{d}/"
        if pattern not in seen_patterns and d not in {
            "src", "node_modules", ".git", "__pycache__", "dist", "build",
            "target", "vendor", ".venv", "venv", "env",
        }:
            # Heuristically assign a skill
            if d in {"tests", "test", "spec", "__tests__"}:
                skill = "test"
            elif d in {"migrations", "db", "database"}:
                skill = "schema"
            elif d in {"api", "routes", "handlers", "controllers"}:
                skill = "api"
            elif d in {"docs", "doc", "documentation"}:
                skill = "review"
            elif d in {"components", "pages", "views", "layouts", "styles"}:
                skill = "frontend"
            else:
                skill = "backend"
            rules.append({"pattern": pattern, "skill": skill})

    return rules


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_portfolio(
    project_dir: Path,
    *,
    default_provider: str = "anthropic",
) -> dict:
    """Inspect files under project_dir and return a schedule-config.yml dict.

    The returned dict is validated against solver.config_schema.Config before
    being returned. Raises ScheduleInputError if the result fails validation
    (which would indicate a bug in this function).

    Parameters
    ----------
    project_dir:
        Directory to scan. Non-recursive for manifests; shallow scan for layout.
    default_provider:
        Provider string written into every agent's ``provider`` field.

    Returns
    -------
    dict
        A fully-formed, Config-validated schedule-config.yml dictionary.
    """
    project_dir = Path(project_dir).resolve()
    if not project_dir.is_dir():
        raise ScheduleInputError(f"project_dir is not a directory: {project_dir}")

    stacks = _detect_stacks(project_dir)
    log.debug("Detected stacks in %s: %s", project_dir, stacks)

    agents: list[dict] = []

    # Always emit architect
    agents.append({
        "id": "architect",
        "provider": default_provider,
        "model": "claude-opus-4",
        "skills": ["design", "review", "schema", "architecture"],
        "kappa": KAPPA_DEFAULT // 2 or 5,  # 5 — careful, thorough agent
        "context_budget": CONTEXT_BUDGET_KTOKENS_DEFAULT * 2,  # 32k
        "speed_factor": 0.8,
        "price_per_1k_tokens": 0.0,
    })

    # Backend agent
    if stacks["backend"]:
        agents.append({
            "id": "backend",
            "provider": default_provider,
            "model": "claude-sonnet-4",
            "skills": _build_backend_skills(stacks),
            "kappa": KAPPA_DEFAULT,
            "context_budget": CONTEXT_BUDGET_KTOKENS_DEFAULT,
            "speed_factor": SPEED_FACTOR_DEFAULT,
            "price_per_1k_tokens": 0.0,
        })

    # Frontend agent
    if stacks["frontend"]:
        framework = stacks["frontend_framework"]
        skills = ["frontend", framework, "css", "html", "javascript"]
        agents.append({
            "id": "frontend",
            "provider": default_provider,
            "model": "claude-sonnet-4",
            "skills": skills,
            "kappa": KAPPA_DEFAULT,
            "context_budget": CONTEXT_BUDGET_KTOKENS_DEFAULT,
            "speed_factor": SPEED_FACTOR_DEFAULT,
            "price_per_1k_tokens": 0.0,
        })

    # Tester agent
    if stacks["tests"]:
        agents.append({
            "id": "tester",
            "provider": default_provider,
            "model": "claude-haiku-4.5",
            "skills": ["test", "unit-test", "e2e", "contract-test"],
            "kappa": KAPPA_DEFAULT + 5,  # 15
            "context_budget": CONTEXT_BUDGET_KTOKENS_DEFAULT // 2,  # 8k
            "speed_factor": 1.5,
            "price_per_1k_tokens": 0.0,
        })

    # Docs agent
    if stacks["docs"]:
        agents.append({
            "id": "docs",
            "provider": default_provider,
            "model": "claude-sonnet-4",
            "skills": ["docs", "review"],
            "kappa": KAPPA_DEFAULT,
            "context_budget": CONTEXT_BUDGET_KTOKENS_DEFAULT,
            "speed_factor": SPEED_FACTOR_DEFAULT,
            "price_per_1k_tokens": 0.0,
        })

    # default_skill
    default_skill = "backend" if stacks["backend"] else "docs"

    # token_estimates: use defaults from solver.defaults
    token_estimates = {k: {"mean": v, "std_dev": 0} for k, v in TOKEN_ESTIMATES.items()}
    # Give medium a meaningful std_dev
    token_estimates["medium"] = {"mean": TOKEN_ESTIMATES["medium"], "std_dev": 500}

    config_dict = {
        "agents": agents,
        "skill_rules": _build_skill_rules(stacks),
        "default_skill": default_skill,
        "token_estimates": token_estimates,
        "complexity_verbs": {
            "simple": ["add", "update", "rename", "move", "import", "export", "configure"],
            "medium": ["implement", "create", "write", "build", "refactor"],
            "complex": ["design", "architect", "integrate", "migrate", "optimize"],
            "review": ["review", "validate", "verify", "analyze", "audit"],
        },
        "solver": {
            "objective": "lexicographic",
            "makespan_weight": 100,
            "cost_weight": 0,
            "time_limit": 60,
            "num_workers": 8,
            "symmetry_breaking": True,
            "warm_start": True,
            "horizon_multiplier": 1.5,
            "token_unit": 100,
            "stochastic_quantile": 0.5,
            "anytime": False,
        },
    }

    # Validate before returning
    try:
        from .config_schema import Config
        Config.model_validate(config_dict)
    except Exception as exc:
        raise ScheduleInputError(
            f"autodetect produced an invalid config (bug): {exc}"
        ) from exc

    return config_dict


# ---------------------------------------------------------------------------
# YAML serialisation
# ---------------------------------------------------------------------------

_HEADER_TEMPLATE = """\
# =============================================================================
# spec-kit-schedule — Generated Configuration
# =============================================================================
# Generated by solver.autodetect at {ts} from {project_dir}.
# Tune kappa/context_budget/speed_factor against your real runs, then feed
# live logs to solver.calibrate to improve estimates automatically.
# Run `python -m solver.autodetect --help` for options.
# =============================================================================

"""


def _to_yaml(config_dict: dict, project_dir: Path) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    header = _HEADER_TEMPLATE.format(ts=ts, project_dir=project_dir)
    body = yaml.dump(config_dict, sort_keys=False, default_flow_style=False, allow_unicode=True)
    return header + body


# ---------------------------------------------------------------------------
# Interactive prompt
# ---------------------------------------------------------------------------

def _interactive_refine(config_dict: dict) -> dict:
    """Prompt the user to review/override each inferred agent."""
    print("\nInteractive mode: review each detected agent (press Enter to keep default).\n")
    new_agents = []
    for agent in config_dict["agents"]:
        print(f"  Agent: {agent['id']}")
        agent_id = input(f"    id [{agent['id']}]: ").strip() or agent["id"]
        model = input(f"    model [{agent['model']}]: ").strip() or agent["model"]
        kappa_str = input(f"    kappa [{agent['kappa']}]: ").strip()
        kappa = int(kappa_str) if kappa_str else agent["kappa"]
        new_agents.append({**agent, "id": agent_id, "model": model, "kappa": kappa})

    default_skill_input = (
        input(f"\ndefault_skill [{config_dict['default_skill']}]: ").strip()
        or config_dict["default_skill"]
    )
    return {**config_dict, "agents": new_agents, "default_skill": default_skill_input}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m solver.autodetect",
        description="Auto-detect project stack and generate schedule-config.yml.",
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        type=Path,
        help="Directory to scan (default: current directory).",
    )
    parser.add_argument(
        "--output",
        default=None,
        type=Path,
        help="Write generated YAML to this file (omit for stdout unless --interactive).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite --output if it already exists.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print YAML to stdout; do not write any file.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt for each inferred agent's id, model, and kappa.",
    )
    parser.add_argument(
        "--provider",
        default="anthropic",
        help="Default provider tag written into each agent (default: anthropic).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point — catches ScheduleInputError and exits with code 2."""
    args = _parse_args(argv)

    try:
        config_dict = detect_portfolio(
            args.project_dir,
            default_provider=args.provider,
        )

        if args.interactive:
            config_dict = _interactive_refine(config_dict)
            # Re-validate after interactive edits
            try:
                from .config_schema import Config
                Config.model_validate(config_dict)
            except Exception as exc:
                raise ScheduleInputError(
                    f"Interactive edits produced an invalid config: {exc}"
                ) from exc

        yaml_text = _to_yaml(config_dict, args.project_dir.resolve())

        if args.dry_run:
            print(yaml_text)
            return

        output_path: Path | None = args.output
        if output_path is None:
            # Neither --dry-run nor --output: print to stdout as a convenience.
            print(yaml_text)
            return

        if output_path.exists() and not args.force:
            raise ScheduleInputError(
                f"{output_path} already exists. Use --force to overwrite."
            )

        # Atomic write via tempfile + os.replace
        output_path = output_path.resolve()
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=output_path.parent, prefix=".autodetect_tmp_"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(yaml_text)
            os.replace(tmp_path, output_path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

        print(f"Written: {output_path}", file=sys.stderr)

    except ScheduleInputError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":  # pragma: no cover
    main()
