"""Tiny formatting helpers shared by the markdown and HTML renderers.

The two renderers consume the same solver result envelope and compose the
same display labels (task short label, agent model/provider line); keeping
the formatters here means the two outputs can't drift cosmetically when
the schema evolves.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = ["task_label", "format_agent_model_label"]


def task_label(task: dict[str, Any]) -> str:
    """Display label for a task: ``story_id`` if set, else ``phase``, else ``"?"``.

    Centralised because both renderers walk the same field-priority order;
    callers rendering the per-agent task list and the wave table use this so
    the two views always agree on what to show.
    """
    label = task.get("story_id") or task.get("phase", "?")
    return str(label)


def format_agent_model_label(
    agent: dict[str, Any],
    *,
    sep: str = "·",
    esc: Callable[[Any], str] = str,
    missing: str = "?",
) -> str:
    """Format ``"<model> · <provider>"`` (or just ``<model>`` if no provider).

    ``esc`` is the escaping function the caller wants applied to each field
    (e.g. :func:`html.escape` for the HTML renderer; ``str`` — the default —
    for plain markdown). The separator can be overridden when callers need
    a different glyph (e.g. ``"&middot;"`` for HTML entities). ``missing``
    is the placeholder used when ``agent`` carries no ``model`` key.
    """
    model = esc(agent.get("model", missing))
    provider = agent.get("provider")
    if provider:
        return f"{model} {sep} {esc(provider)}"
    return model
