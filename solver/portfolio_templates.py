"""Per-AI portfolio templates resolution.

When :func:`solver.integration_detect.detect_integration` returns a
known AI key, we ship a realistic starter portfolio for that AI
instead of the generic ``REPLACE_ME``-laden ``base-portfolio.yml``.
This eliminates the friction of looking up valid model strings and
list prices manually for the most common cases (Claude Code,
GitHub Copilot, Cursor, Gemini CLI).

Resolution rule:

* Known integration key with a per-AI template on disk → that template.
* Unknown / ``None`` integration key → ``base-portfolio.yml`` (the
  pre-existing generic fallback with ``REPLACE_ME`` placeholders).

The mapping is intentionally narrow — only the four most common AI
assistants ship per-AI templates. Adding more is a one-line change to
``_PER_AI_TEMPLATES`` plus the matching YAML file under
``templates/``; the ``base-portfolio.yml`` fallback keeps spec-kit
extensions for less-common assistants working with the
``REPLACE_ME``-prompt path.
"""

from __future__ import annotations

__all__ = ["template_for_integration"]

from pathlib import Path
from typing import Final

# Map of integration_key → template filename (relative to ``templates/``).
# Keys must match canonical spec-kit integration keys
# (see ``solver.integration_detect.KNOWN_INTEGRATIONS``).
_PER_AI_TEMPLATES: Final[dict[str, str]] = {
    "claude": "portfolio-claude.yml",
    "copilot": "portfolio-copilot.yml",
    "cursor-agent": "portfolio-cursor.yml",
    "gemini": "portfolio-gemini.yml",
}


def template_for_integration(integration_key: str | None) -> Path:
    """Return the appropriate portfolio template path for ``integration_key``.

    Parameters
    ----------
    integration_key:
        Canonical integration key from
        :func:`solver.integration_detect.detect_integration`. ``None``
        or any unknown key falls through to the generic
        ``base-portfolio.yml``.

    Returns
    -------
    Path
        Absolute path to the template file. The returned path always
        falls back to ``base-portfolio.yml`` when the per-AI candidate
        is missing on disk, so callers can rely on a usable template
        existing as long as the package was installed correctly.
    """
    templates_dir = Path(__file__).resolve().parent.parent / "templates"
    if integration_key and integration_key in _PER_AI_TEMPLATES:
        candidate = templates_dir / _PER_AI_TEMPLATES[integration_key]
        if candidate.is_file():
            return candidate
    return templates_dir / "base-portfolio.yml"
