"""Locate spec-kit-schedule data files (commands, templates, manifest).

Spec-kit-schedule is both a Python package and a spec-kit extension. The
extension manifest, slash-command markdown, and the schedule template ship
inside ``share/spec-kit-schedule/...`` (see ``[tool.setuptools.data-files]``
in pyproject.toml). After a wheel install they land under
``<sys.prefix>/share/spec-kit-schedule/``; in a source checkout they live
at the repository root.

This module exposes a single helper that resolves the correct base path
in either layout, so calling code does not duplicate the search logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

__all__ = ["locate_extension_root"]


def locate_extension_root() -> Path:
    """Return the directory holding ``extension.yml`` and the ``commands/``
    + ``templates/`` subtrees.

    Resolution order:

    1. Installed mode — ``<sys.prefix>/share/spec-kit-schedule`` (created
       by ``pip install`` / ``uv tool install`` from the
       ``[tool.setuptools.data-files]`` block).
    2. Editable / source-checkout mode — the repository root, two
       directories above this file (``solver/_assets.py`` →
       ``solver/`` → repo root).

    The function does not raise: callers that need to fail loudly should
    check for the manifest file themselves. This keeps unit tests that
    import the helper from a temp directory functional.
    """
    installed = Path(sys.prefix) / "share" / "spec-kit-schedule"
    if (installed / "extension.yml").is_file():
        return installed
    return Path(__file__).resolve().parent.parent
