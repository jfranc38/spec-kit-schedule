"""Discover the user's AI-agent fleet from on-disk markdown files.

Different AI assistants store their agent / skill / command definitions
in different conventional locations. This module knows the canonical
patterns for the major spec-kit-supported integrations and falls back
to a generic ``.{key}/{skills,commands,workflows}/*.md`` scan for the
long tail.

Each discovered file is parsed for YAML frontmatter; the resulting
``DiscoveredAgent`` records are passed to ``solver.autodetect`` to
seed the schedule portfolio. Heuristic role classification flags
agents as IMPLEMENTER / REVIEWER / HYBRID so the autodetect step can
combine the user's fleet with generic base slots without
double-booking review tasks.

Best-effort everywhere: missing directories, malformed frontmatter,
and unknown integration keys all degrade gracefully to "no agents
found" rather than raising.
"""

from __future__ import annotations

__all__ = [
    "DiscoveredAgent",
    "Role",
    "classify_role",
    "discover_fleet",
    "parse_frontmatter",
]

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs by default

from ._paths import project_root

log = logging.getLogger(__name__)


Role = Literal["implementer", "reviewer", "hybrid"]


# ---------------------------------------------------------------------------
# Heuristic keyword sets
# ---------------------------------------------------------------------------

_REVIEWER_KEYWORDS = (
    "review",
    "audit",
    "verify",
    "test",
    "qa",
    "quality",
    "security",
    "lint",
)

_IMPLEMENTER_KEYWORDS = (
    "implement",
    "build",
    "develop",
    "engineer",
    "code",
    "dev",
)


# ---------------------------------------------------------------------------
# DiscoveredAgent
# ---------------------------------------------------------------------------


@dataclass
class DiscoveredAgent:
    """A user-installed AI agent / skill / command file."""

    name: str
    """Filename stem (e.g. ``code-reviewer`` from ``code-reviewer.md``)."""

    file: Path
    """Absolute path to the source markdown file."""

    description: str | None
    """``description:`` from frontmatter, when present."""

    model: str | None
    """``model:`` from frontmatter, when present."""

    tools: list[str] = field(default_factory=list)
    """``tools:`` list from frontmatter (best-effort coercion)."""

    role: Role = "hybrid"
    """Heuristic classification — see ``classify_role``."""

    raw_frontmatter: dict[str, Any] = field(default_factory=dict)
    """Verbatim parsed frontmatter dict (for callers that need other keys)."""


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<body>.*?)\n---\s*(?:\n|$)",
    flags=re.DOTALL,
)


def parse_frontmatter(content: str) -> dict[str, Any]:
    """Extract YAML frontmatter from a markdown string.

    Returns an empty dict when:

    * the content has no ``---``-delimited frontmatter block, or
    * the YAML parse fails, or
    * the parsed body is not a mapping.

    No exceptions propagate — this is a best-effort helper.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}
    try:
        parsed = yaml.safe_load(match.group("body"))
    except yaml.YAMLError:
        log.debug("could not parse YAML frontmatter")
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _coerce_tools(value: Any) -> list[str]:
    """Tools may be a list, a comma-separated string, or absent."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return []


# ---------------------------------------------------------------------------
# Role classification
# ---------------------------------------------------------------------------


def _has_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    return any(kw in text for kw in keywords)


def classify_role(name: str, description: str | None) -> Role:
    """Heuristic classification of an agent's primary role.

    Conservative when in doubt — agents matching both lists or
    matching neither fall into ``"hybrid"``. The portfolio scaffolder
    then offers reviewers as an opt-in extra rather than auto-adding
    them as implementer slots, which would mis-route impl tasks.
    """
    blob = " ".join(filter(None, (name, description))).lower()
    is_reviewer = _has_keyword(blob, _REVIEWER_KEYWORDS)
    is_impl = _has_keyword(blob, _IMPLEMENTER_KEYWORDS)
    if is_reviewer and is_impl:
        return "hybrid"
    if is_reviewer:
        return "reviewer"
    if is_impl:
        return "implementer"
    return "hybrid"


# ---------------------------------------------------------------------------
# Discovery layout per integration
# ---------------------------------------------------------------------------


# Each entry: (directory_relative_to_root, glob_pattern).
# Directories that don't exist are silently skipped.
_LAYOUTS: dict[str, list[tuple[str, str]]] = {
    "claude": [
        (".claude/agents", "*.md"),
        (".claude/skills", "*/SKILL.md"),
    ],
    "copilot": [
        (".github/agents", "*.agent.md"),
        (".github/agents", "*.md"),
    ],
    "cursor-agent": [
        (".cursor/skills", "*/SKILL.md"),
        (".cursor/commands", "*.md"),
    ],
    "gemini": [
        (".gemini/commands", "*.md"),
    ],
}


def _generic_layout(integration_key: str) -> list[tuple[str, str]]:
    """Fallback layout for less-common integrations.

    Scans ``.{key}/skills/``, ``.{key}/commands/``, and
    ``.{key}/workflows/`` for ``*.md`` and ``*/SKILL.md`` files.
    """
    base = f".{integration_key}"
    return [
        (f"{base}/skills", "*.md"),
        (f"{base}/skills", "*/SKILL.md"),
        (f"{base}/commands", "*.md"),
        (f"{base}/workflows", "*.md"),
        (f"{base}/agents", "*.md"),
    ]


def _layout_for(integration_key: str) -> list[tuple[str, str]]:
    if integration_key in _LAYOUTS:
        return _LAYOUTS[integration_key]
    # Best-effort generic fallback. Empty key short-circuits to "nothing".
    if not integration_key:
        return []
    return _generic_layout(integration_key)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _agent_from_path(path: Path) -> DiscoveredAgent | None:
    """Construct a DiscoveredAgent from a markdown file path.

    Returns None if the file cannot be read. Frontmatter is optional —
    agents without it still get a record with empty fields plus a
    classification based on filename alone.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        log.debug("could not read agent file %s", path)
        return None

    fm = parse_frontmatter(content)
    description = fm.get("description")
    if description is not None and not isinstance(description, str):
        description = str(description)
    model = fm.get("model")
    if model is not None and not isinstance(model, str):
        model = str(model)
    tools = _coerce_tools(fm.get("tools"))

    # SKILL.md files take their identity from the parent directory,
    # not the (always-identical) filename.
    if path.name.upper() == "SKILL.MD":
        name = path.parent.name
    else:
        name = path.stem
        # Strip trailing .agent in copilot's `*.agent.md` convention.
        if name.endswith(".agent"):
            name = name[: -len(".agent")]

    role = classify_role(name, description)

    return DiscoveredAgent(
        name=name,
        file=path.resolve(),
        description=description,
        model=model,
        tools=tools,
        role=role,
        raw_frontmatter=fm,
    )


def discover_fleet(
    integration_key: str | None,
    project: Path | None = None,
) -> list[DiscoveredAgent]:
    """Discover the user's agent fleet for the given AI integration.

    Parameters
    ----------
    integration_key:
        Canonical key from ``solver.integration_detect.detect_integration``.
        ``None`` or empty returns an empty list.
    project:
        Project root. If ``None``, walks up from cwd looking for ``.specify``.

    Returns
    -------
    list[DiscoveredAgent]
        One record per discovered ``*.md`` (or ``SKILL.md``) file.
        Order: by directory layout entry, then ``Path.glob`` order
        (filesystem-dependent — callers that need stable order should
        sort by ``.name``).
    """
    if not integration_key:
        return []
    root = project_root(project)

    discovered: list[DiscoveredAgent] = []
    seen: set[Path] = set()
    for relative_dir, pattern in _layout_for(integration_key):
        base = root / relative_dir
        if not base.is_dir():
            continue
        for match in sorted(base.glob(pattern)):
            if not match.is_file():
                continue
            resolved = match.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            agent = _agent_from_path(match)
            if agent is not None:
                discovered.append(agent)

    log.info(
        "discover_fleet: integration=%s, found=%d",
        integration_key,
        len(discovered),
    )
    return discovered
