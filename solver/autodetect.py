"""Auto-detect a project's tech stack and generate a starter schedule-config.yml.

Usage (module):
    from solver.autodetect import detect_portfolio
    config_dict = detect_portfolio(Path("."))

Usage (CLI):
    python -m solver.autodetect [--project-dir .] [--output schedule-config.yml]
                                [--force] [--dry-run] [--interactive]
                                [--provider anthropic] [--detect-ai]

v0.6.0 adds AI-fleet awareness: when ``--detect-ai`` is set (or the
library caller passes ``integration_key``), the autodetect step reads
``.specify/integration.json`` to identify the user's AI assistant,
discovers the on-disk agent fleet (e.g. ``.claude/agents/*.md``), and
combines stack-derived agents with discovered implementers plus
generic ``frontier`` / ``mid`` / ``small`` slots from
``templates/base-portfolio.yml`` for any uncovered roles. Reviewer-
shaped agents are surfaced in ``discovered_reviewers`` and hybrid
agents (matched neither / both keyword sets) in ``discovered_hybrid``
rather than auto-routed as scheduler agents — see
``commands/portfolio.md``.
"""

from __future__ import annotations

__all__ = ["base_portfolio_path", "detect_portfolio", "load_base_portfolio_agents"]

import argparse
import contextlib
import json
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs by default

from .defaults import (
    ANYTIME_DEFAULT,
    CONTEXT_BUDGET_KTOKENS_DEFAULT,
    COST_WEIGHT_DEFAULT,
    HORIZON_MULTIPLIER,
    KAPPA_DEFAULT,
    MAKESPAN_WEIGHT,
    NUM_WORKERS,
    OBJECTIVE,
    SPEED_FACTOR_DEFAULT,
    STOCHASTIC_QUANTILE_DEFAULT,
    TIME_LIMIT_SECONDS,
    TOKEN_ESTIMATES,
    TOKEN_UNIT,
)
from .fleet_discover import DiscoveredAgent, discover_fleet
from .i18n import t
from .integration_detect import detect_integration, display_name
from .portfolio_templates import template_for_integration
from .validation import ScheduleInputError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base portfolio template loading
# ---------------------------------------------------------------------------


def base_portfolio_path() -> Path:
    """Return path to the bundled ``templates/base-portfolio.yml`` skeleton.

    Always returns the generic ``base-portfolio.yml`` regardless of the
    detected integration. For integration-aware lookups use
    :func:`solver.portfolio_templates.template_for_integration`.
    """
    return Path(__file__).resolve().parent.parent / "templates" / "base-portfolio.yml"


def load_base_portfolio_agents(
    integration_key: str | None = None,
) -> list[dict[str, Any]]:
    """Read a portfolio template and return its ``agents:`` list.

    Parameters
    ----------
    integration_key:
        Canonical AI integration key (``"claude"``, ``"copilot"``,
        ``"cursor-agent"``, ``"gemini"``). When the key matches a
        per-AI template that ships realistic 2026 model identifiers
        and prices, that template is loaded instead of the generic
        ``base-portfolio.yml``. ``None`` (the default) preserves the
        v0.5.x behaviour and reads ``base-portfolio.yml``.

    Returns
    -------
    list[dict[str, Any]]
        The template's ``agents:`` list, or ``[]`` if the file is
        missing or unparseable — callers should fall back to inline
        defaults.
    """
    path = template_for_integration(integration_key)
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        log.debug("portfolio template unavailable at %s", path)
        return []
    raw_agents = body.get("agents", [])
    if not isinstance(raw_agents, list):
        return []
    return [a for a in raw_agents if isinstance(a, dict)]


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _read_package_json(project_dir: Path) -> dict[str, Any]:
    """Return parsed package.json or empty dict if absent/unreadable."""
    pkg = project_dir / "package.json"
    if not pkg.is_file():
        return {}
    try:
        # ``json.loads`` returns ``Any``; the explicit cast tells mypy we
        # only ever consume top-level mapping shapes from package.json.
        parsed: dict[str, Any] = json.loads(pkg.read_text(encoding="utf-8"))
        return parsed
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


def _detect_stacks(project_dir: Path) -> dict[str, Any]:
    """Return a dict of detected stack signals.

    Mostly bool flags, plus the ``frontend_framework`` key (str) and
    ``dirs`` (``set[str]``) used downstream by ``_build_skill_rules``.
    """
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
    # Either an explicit `migrations/` dir, or a `db/` dir that also contains
    # `.sql` files (a bare `db/` could just be a config helper).
    migrations_detected = "migrations" in dirs or (
        "db" in dirs and _has_glob(project_dir, "*.sql")
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


def _build_backend_skills(stacks: dict[str, Any]) -> list[str]:
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


def _unique_agent_id(base: str, existing: set[str]) -> str:
    """Return ``base`` if free, else ``base-2``, ``base-3``, … until unique."""
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


def _build_skill_rules(stacks: dict[str, Any]) -> list[dict[str, str]]:
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
# Skill inference for discovered implementer agents
# ---------------------------------------------------------------------------

# Default skills assigned when frontmatter ``tools:`` and ``description:``
# yield no recognised hints. Wide enough that the discovered implementer
# can plausibly run any task in a typical mixed feature graph.
_DEFAULT_IMPLEMENTER_SKILLS: list[str] = ["impl", "backend", "frontend", "python", "test"]

# Keyword → skill hits used by ``_skills_from_frontmatter``. Each entry
# matches against lowercase ``tools`` items and ``description`` words.
_SKILL_KEYWORDS: dict[str, str] = {
    # Test runners / frameworks
    "pytest": "test",
    "jest": "test",
    "vitest": "test",
    "mocha": "test",
    "rspec": "test",
    "test": "test",
    # Infra / deploy
    "docker": "infra",
    "kubectl": "infra",
    "kubernetes": "infra",
    "terraform": "infra",
    "infra": "infra",
    "deploy": "infra",
    # Languages / stacks
    "python": "python",
    "javascript": "javascript",
    "typescript": "javascript",
    "rust": "rust",
    "go": "go",
    "java": "java",
    # Roles
    "backend": "backend",
    "frontend": "frontend",
    "api": "api",
    "database": "database",
    "schema": "schema",
    "design": "design",
    "review": "review",
    "docs": "docs",
}


def _skills_from_frontmatter(agent: DiscoveredAgent) -> list[str]:
    """Derive a skills list from the agent's parsed frontmatter.

    Falls back to ``_DEFAULT_IMPLEMENTER_SKILLS`` when neither
    ``tools`` nor ``description`` yields any recognised keyword. The
    returned list preserves discovery order and contains no duplicates.
    """
    found: list[str] = []
    seen: set[str] = set()

    def _record(skill: str) -> None:
        if skill not in seen:
            seen.add(skill)
            found.append(skill)

    # 1) tools entries are normally compact tokens — exact-match each one.
    for raw in agent.tools:
        token = raw.strip().lower()
        skill = _SKILL_KEYWORDS.get(token)
        if skill is not None:
            _record(skill)

    # 2) description text is fuzzier — substring-match each keyword.
    description = (agent.description or "").lower()
    if description:
        for keyword, skill in _SKILL_KEYWORDS.items():
            if keyword in description:
                _record(skill)

    if not found:
        return list(_DEFAULT_IMPLEMENTER_SKILLS)
    # Always include the generic ``impl`` marker so the scheduler can
    # match implementer agents to bare implementation tasks even when
    # frontmatter only mentioned a specific stack.
    if "impl" not in seen:
        found.append("impl")
    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_portfolio(
    project_dir: Path,
    *,
    default_provider: str = "anthropic",
    integration_key: str | None = None,
    auto_detect_integration: bool = False,
    use_base_portfolio: bool = False,
) -> dict[str, Any]:
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
    integration_key:
        Explicit AI integration key (e.g. ``"claude"``, ``"copilot"``).
        ``None`` (default) skips fleet discovery — preserves the v0.5.x
        behaviour for callers that don't opt in. Pass with
        ``auto_detect_integration=False`` to override the marker without
        reading it.
    auto_detect_integration:
        When True, read ``.specify/integration.json`` for the AI key
        when ``integration_key`` is None. Caller-driven so library users
        can opt out.
    use_base_portfolio:
        When True, append generic ``frontier``/``mid``/``small`` slots from
        the base-portfolio template for any role coverage gaps. Auto-set
        whenever fleet discovery returns implementers (so the fleet's
        ``REPLACE_ME`` placeholders are visible to the user).

    .. note::
        The three integration-related flags
        (``integration_key``, ``auto_detect_integration``,
        ``use_base_portfolio``) may be consolidated into a single
        ``FleetOptions`` dataclass in v0.7. Until then, callers should
        pass them as keyword arguments explicitly.

    Returns
    -------
    dict
        A fully-formed, Config-validated schedule-config.yml dictionary.
        Includes the extra top-level keys:

        * ``discovered_reviewers`` — only ``role == "reviewer"`` agents
          (description matches the reviewer keyword set and not the
          implementer set).
        * ``discovered_hybrid`` — agents that matched both keyword
          sets, neither, or otherwise could not be classified
          confidently. Surfaced separately so the portfolio command
          can prompt the user honestly rather than misfiling them
          under reviewers.
    """
    project_dir = Path(project_dir).resolve()
    if not project_dir.is_dir():
        raise ScheduleInputError(t("not_a_directory", path=project_dir))

    stacks = _detect_stacks(project_dir)
    log.debug("Detected stacks in %s: %s", project_dir, stacks)

    agents: list[dict[str, Any]] = []

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

    # ── AI fleet discovery (v0.6.0+, opt-in) ──────────────────────────
    resolved_key: str | None = integration_key
    if resolved_key is None and auto_detect_integration:
        resolved_key = detect_integration(project_dir)

    discovered_implementers: list[DiscoveredAgent] = []
    discovered_reviewers: list[DiscoveredAgent] = []
    discovered_hybrid: list[DiscoveredAgent] = []
    if resolved_key:
        fleet = discover_fleet(resolved_key, project_dir)
        for ag in fleet:
            if ag.role == "implementer":
                discovered_implementers.append(ag)
            elif ag.role == "reviewer":
                discovered_reviewers.append(ag)
            else:
                # ``role == "hybrid"`` — surfaced separately so the
                # portfolio command can disambiguate with the user.
                discovered_hybrid.append(ag)

    # Add discovered IMPLEMENTERs as scheduler agents.
    existing_ids = {a["id"] for a in agents}
    for ag in discovered_implementers:
        agent_id = _unique_agent_id(ag.name, existing_ids)
        existing_ids.add(agent_id)
        agents.append({
            "id": agent_id,
            "provider": default_provider,
            "model": ag.model or "REPLACE_ME",
            "skills": _skills_from_frontmatter(ag),
            "kappa": KAPPA_DEFAULT,
            "context_budget": CONTEXT_BUDGET_KTOKENS_DEFAULT,
            "speed_factor": SPEED_FACTOR_DEFAULT,
            "price_per_1k_tokens": 0.0,
        })

    # When fleet returned implementers OR caller explicitly asked, also
    # surface the generic frontier/mid/small slots so the user has a
    # known-good starting point. Distinct ids prevent collision with
    # stack-derived agents. The per-AI template is preferred when the
    # integration key matches one of the bundled portfolios (Claude,
    # Copilot, Cursor, Gemini); otherwise we fall through to the
    # generic ``REPLACE_ME`` placeholders.
    add_base = use_base_portfolio or bool(discovered_implementers)
    if add_base:
        for base in load_base_portfolio_agents(resolved_key):
            base_id = _unique_agent_id(str(base.get("id", "agent")), existing_ids)
            existing_ids.add(base_id)
            agents.append({**base, "id": base_id})

    # default_skill
    default_skill = "backend" if stacks["backend"] else "docs"

    # token_estimates: use defaults from solver.defaults
    token_estimates = {k: {"mean": v, "std_dev": 0} for k, v in TOKEN_ESTIMATES.items()}
    # Give medium a meaningful std_dev
    token_estimates["medium"] = {"mean": TOKEN_ESTIMATES["medium"], "std_dev": 500}

    config_dict: dict[str, Any] = {
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
            "objective": OBJECTIVE,
            "makespan_weight": MAKESPAN_WEIGHT,
            "cost_weight": COST_WEIGHT_DEFAULT,
            "time_limit": TIME_LIMIT_SECONDS,
            "num_workers": NUM_WORKERS,
            "symmetry_breaking": True,
            "warm_start": True,
            "horizon_multiplier": HORIZON_MULTIPLIER,
            "token_unit": TOKEN_UNIT,
            "stochastic_quantile": STOCHASTIC_QUANTILE_DEFAULT,
            "anytime": ANYTIME_DEFAULT,
        },
    }

    # Surface reviewers and hybrid agents as metadata only — they are NOT
    # scheduler agents. ``Config.model_validate`` accepts unknown top-level
    # keys (extra="allow"), so /speckit.schedule.portfolio can show them to
    # the user without routing impl tasks to them.
    def _agent_summary(ag: DiscoveredAgent) -> dict[str, Any]:
        return {
            "name": ag.name,
            "file": str(ag.file),
            "description": ag.description,
            "role": ag.role,
        }

    if discovered_reviewers:
        config_dict["discovered_reviewers"] = [
            _agent_summary(ag) for ag in discovered_reviewers
        ]
    if discovered_hybrid:
        config_dict["discovered_hybrid"] = [
            _agent_summary(ag) for ag in discovered_hybrid
        ]
    # Always record the resolved integration when the caller opted in,
    # even when discovery returned only implementers — downstream UI
    # uses ``integration_display_name`` for "from {AI display name}".
    if resolved_key:
        config_dict["integration_key"] = resolved_key
        config_dict["integration_display_name"] = display_name(resolved_key)

    # Validate before returning
    try:
        from .config_schema import Config
        Config.model_validate(config_dict)
    except Exception as exc:
        raise ScheduleInputError(
            t("autodetect_invalid_config", error=exc)
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


def _to_yaml(config_dict: dict[str, Any], project_dir: Path) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    header = _HEADER_TEMPLATE.format(ts=ts, project_dir=project_dir)
    body: str = yaml.dump(
        config_dict, sort_keys=False, default_flow_style=False, allow_unicode=True
    )
    return header + body


# ---------------------------------------------------------------------------
# Interactive prompt
# ---------------------------------------------------------------------------

def _interactive_refine(config_dict: dict[str, Any]) -> dict[str, Any]:
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
    parser.add_argument(
        "--detect-ai",
        action="store_true",
        help=(
            "Read .specify/integration.json and discover the user's AI fleet "
            "(.claude/agents/*.md, .github/agents/*.agent.md, etc.). Adds "
            "discovered implementers as scheduler agents and surfaces "
            "reviewer-shaped agents under discovered_reviewers and "
            "ambiguous (hybrid) agents under discovered_hybrid."
        ),
    )
    parser.add_argument(
        "--integration-key",
        default=None,
        help=(
            "Override the integration key (e.g. claude, copilot, gemini, "
            "cursor-agent). Implies --detect-ai."
        ),
    )
    parser.add_argument(
        "--with-base-portfolio",
        action="store_true",
        help=(
            "Always append the generic frontier/mid/small slots from "
            "templates/base-portfolio.yml. Auto-enabled when --detect-ai "
            "discovers implementer agents."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point — catches ScheduleInputError and exits with code 2."""
    args = _parse_args(argv)

    try:
        config_dict = detect_portfolio(
            args.project_dir,
            default_provider=args.provider,
            integration_key=args.integration_key,
            auto_detect_integration=bool(args.detect_ai or args.integration_key),
            use_base_portfolio=bool(args.with_base_portfolio),
        )

        if args.interactive:
            config_dict = _interactive_refine(config_dict)
            # Re-validate after interactive edits
            try:
                from .config_schema import Config
                Config.model_validate(config_dict)
            except Exception as exc:
                raise ScheduleInputError(
                    t("interactive_invalid_config", error=exc)
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
                t("output_exists_use_force", path=output_path)
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
