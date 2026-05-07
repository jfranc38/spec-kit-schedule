"""Detect which AI assistant the user installed spec-kit for.

The ``specify init`` CLI writes a small marker JSON identifying the
chosen assistant (Claude Code, Cursor, Copilot, Gemini, …). v0.6.0
reads this marker so the portfolio scaffolder can target the right
fleet-discovery layout (``.claude/agents/*.md``,
``.github/agents/*.agent.md``, …) without guessing.

Resolution order — first hit wins:

1. ``.specify/integration.json`` ``integration_key``
2. ``.specify/integration.json`` ``installed_integrations[0]``
3. ``.specify/init-options.json`` ``integration``
4. ``.specify/init-options.json`` ``ai`` (legacy alias)
5. ``None`` — caller treats this as "generic / unknown"

The list mirrors the keys spec-kit itself uses; we never invent new
keys here. Unknown values pass through as opaque strings so a future
spec-kit release that adds a new assistant does not break us.

Known canonical keys (spec-kit upstream):

  ``claude``, ``cursor-agent``, ``copilot``, ``gemini``, ``codex``,
  ``opencode``, ``windsurf``, ``aider``, ``q``, ``qwen``, ``zed``,
  ``kilocode``, ``rovo``, ``moonshot``, ``saoki``, ``warp``,
  ``codebuddy``, ``cline``, ``continue``, ``goose``, ``smol``,
  ``tabnine``, ``replit``, ``mentat``, ``aider-chat``, ``open-interpreter``,
  ``llama-index``, ``crewai``, ``autogen``, ``langchain``
"""

from __future__ import annotations

__all__ = [
    "KNOWN_INTEGRATIONS",
    "detect_integration",
    "display_name",
]

import json
import logging
from pathlib import Path
from typing import Any

from ._paths import project_root

log = logging.getLogger(__name__)


# Known canonical keys → human-readable display labels.
# This is purely cosmetic: it powers the "from {AI display name}"
# string in the portfolio scaffolder. Keys we do not list here still
# work — they fall through to ``key.title()``.
KNOWN_INTEGRATIONS: dict[str, str] = {
    "claude": "Claude Code",
    "cursor-agent": "Cursor",
    "copilot": "GitHub Copilot",
    "gemini": "Gemini CLI",
    "codex": "OpenAI Codex",
    "opencode": "opencode",
    "windsurf": "Windsurf",
    "aider": "Aider",
    "q": "Amazon Q",
    "qwen": "Qwen Code",
    "zed": "Zed",
    "kilocode": "Kilo Code",
    "rovo": "Rovo Dev",
    "moonshot": "Moonshot Kimi",
    "saoki": "Saoki",
    "warp": "Warp",
    "codebuddy": "CodeBuddy",
    "cline": "Cline",
    "continue": "Continue",
    "goose": "Goose",
    "smol": "Smol Developer",
    "tabnine": "Tabnine",
    "replit": "Replit Agent",
    "mentat": "Mentat",
    "open-interpreter": "Open Interpreter",
    "llama-index": "LlamaIndex",
    "crewai": "CrewAI",
    "autogen": "AutoGen",
    "langchain": "LangChain",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.debug("could not read %s", path)
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _str_or_none(value: object) -> str | None:
    """Return the stripped string if ``value`` is a non-empty string, else None."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def detect_integration(project: Path | None = None) -> str | None:
    """Return the canonical integration key, or None if not detectable.

    Parameters
    ----------
    project:
        Project root. If ``None``, walks up from cwd looking for
        ``.specify/`` (see ``solver._paths.project_root``).

    Returns
    -------
    str | None
        The integration key (e.g. ``"claude"``, ``"copilot"``) or
        ``None`` when no marker is present. Callers should fall back to
        a generic detection path (user-prompt-only) when this returns
        ``None``.
    """
    root = project_root(project)

    # Source 1+2: .specify/integration.json
    integration_json = _read_json(root / ".specify" / "integration.json")
    if integration_json is not None:
        key = _str_or_none(integration_json.get("integration_key"))
        if key is not None:
            return key
        installed = integration_json.get("installed_integrations")
        if isinstance(installed, list) and installed:
            first = _str_or_none(installed[0])
            if first is not None:
                return first

    # Source 3+4: .specify/init-options.json
    init_options = _read_json(root / ".specify" / "init-options.json")
    if init_options is not None:
        key = _str_or_none(init_options.get("integration"))
        if key is not None:
            return key
        legacy = _str_or_none(init_options.get("ai"))
        if legacy is not None:
            return legacy

    return None


def display_name(integration_key: str | None) -> str:
    """Human-readable label for ``integration_key``.

    Returns ``"your AI assistant"`` for ``None`` (graceful fallback so
    user-facing strings stay sensible without special-casing every
    call site). Unknown keys are title-cased so they still read
    naturally in prompts.
    """
    if not integration_key:
        return "your AI assistant"
    if integration_key in KNOWN_INTEGRATIONS:
        return KNOWN_INTEGRATIONS[integration_key]
    return integration_key.replace("-", " ").title()
