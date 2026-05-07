"""Central path constants for the encapsulated extension state.

Layout convention (v0.6.0+):

::

    .specify/extensions/schedule/   # extension code (installed by specify CLI)
        ├── bin/, commands/, solver/, templates/, ...
        └── .venv/                  # encapsulated Python venv (G1)
    .specify/schedule/              # extension RUNTIME state (G1)
        └── schedule-config.yml     # user portfolio

The convention is ``.specify/extensions/<id>/`` for code shipped by the
extension installer (``specify``) and ``.specify/<id>/`` for state the
extension writes at runtime. Two separate trees keep state out of the
source-controlled extension payload and let users wipe state without
re-installing the extension.

The migration helper ``migrate_legacy_config`` moves a pre-0.6.0
``./schedule-config.yml`` at the project root into the encapsulated
``.specify/schedule/schedule-config.yml`` location. It is a one-shot,
idempotent helper — calling it again after the move is a no-op.
"""

from __future__ import annotations

__all__ = [
    "EXTENSION_ID",
    "encapsulated_venv_python",
    "extension_code_dir",
    "extension_state_dir",
    "legacy_config_path",
    "migrate_legacy_config",
    "project_root",
    "runs_dir",
    "schedule_config_path",
]

import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

EXTENSION_ID = "schedule"


def project_root(start: Path | None = None) -> Path:
    """Walk up from *start* (or cwd) looking for a ``.specify/`` marker.

    Returns the first ancestor (inclusive) that contains a ``.specify``
    directory. If no marker is found, returns the resolved start path
    so callers can still construct paths relative to "the place we
    were called from".

    The caller may pass ``Path.cwd()`` explicitly when invoked from a
    subprocess that has already chdir'd. Callers that need to honour a
    pre-set environment variable should resolve it themselves before
    calling this function.
    """
    here = Path(start) if start is not None else Path.cwd()
    here = here.resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".specify").is_dir():
            return candidate
    return here


def extension_code_dir(start: Path | None = None) -> Path:
    """Where the spec-kit extension installer drops the extension payload.

    Returns ``<project_root>/.specify/extensions/schedule``. The directory
    may not exist yet (e.g. local development checkout that has not run
    ``specify extension add``); callers should not assume existence.
    """
    return project_root(start) / ".specify" / "extensions" / EXTENSION_ID


def extension_state_dir(start: Path | None = None) -> Path:
    """Where the extension writes runtime state (configs, caches, …).

    Returns ``<project_root>/.specify/schedule``. Created on demand by
    callers that write to it (we deliberately do not auto-create here so
    importing the module has no side effects).
    """
    return project_root(start) / ".specify" / EXTENSION_ID


def schedule_config_path(start: Path | None = None) -> Path:
    """Encapsulated path for ``schedule-config.yml`` (v0.6.0+).

    Returns ``<project_root>/.specify/schedule/schedule-config.yml``.
    """
    return extension_state_dir(start) / "schedule-config.yml"


def runs_dir(start: Path | None = None) -> Path:
    """Directory holding plan/actual artefacts for the calibration loop.

    Returns ``<project_root>/.specify/schedule/runs/``. Created on
    demand by :func:`solver.run_log.record_plan` and friends — the
    accessor itself is a pure path constructor with no side effects.
    """
    return extension_state_dir(start) / "runs"


def encapsulated_venv_python(start: Path | None = None) -> Path:
    """Path to the Python interpreter inside the encapsulated venv.

    The venv lives under ``extension_code_dir() / ".venv"`` so it is
    co-located with the rest of the installed extension payload and
    can be removed in one ``rm -rf`` together with the extension.

    Returns the POSIX-style path. Windows users running this codebase
    will need ``Scripts/python.exe`` instead — see
    ``bin/install.sh`` for the cross-platform bootstrap.
    """
    return extension_code_dir(start) / ".venv" / "bin" / "python"


def legacy_config_path(start: Path | None = None) -> Path:
    """Pre-0.6.0 location: ``./schedule-config.yml`` at project root."""
    return project_root(start) / "schedule-config.yml"


def migrate_legacy_config(project: Path | None = None) -> Path | None:
    """Move ``./schedule-config.yml`` to the encapsulated location.

    Behaviour:

    * If only the legacy path exists → move it to the new path,
      creating ``.specify/schedule/`` if needed, and return the new path.
    * If the new path already exists → leave the legacy file in place
      (the user may have edited the new one) and return ``None``. We
      do NOT silently overwrite their current config.
    * If neither exists → return ``None``.

    The function is intentionally conservative: it never deletes
    anything other than the legacy file it just successfully copied.
    """
    legacy = legacy_config_path(project)
    new_path = schedule_config_path(project)
    if not legacy.is_file():
        return None
    if new_path.exists():
        log.warning(
            "migrate_legacy_config: both %s and %s exist; leaving legacy in place",
            legacy,
            new_path,
        )
        return None
    new_path.parent.mkdir(parents=True, exist_ok=True)
    # ``Path.replace`` is atomic on the same filesystem; ``shutil.move``
    # falls back to copy+remove across filesystems. Use ``shutil.move``
    # because users sometimes have ``.specify`` symlinked elsewhere.
    shutil.move(str(legacy), str(new_path))
    log.info("migrated legacy config %s -> %s", legacy, new_path)
    return new_path
