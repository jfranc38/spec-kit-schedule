"""Structured warnings surfaced to the user through schedule.md.

Each solver stage can accumulate warnings (non-fatal issues) that would
otherwise disappear into stderr. The renderer reads ``stats["warnings"]``
and prints a dedicated section so the user never has to grep logs to
understand a surprising schedule.

:class:`WarningCollector` is a :class:`logging.Handler` subclass so it can
be attached to any Python :class:`logging.Logger` via
``logger.addHandler(collector)``.  The :meth:`WarningCollector.add`
shortcut is the direct API for callers that want to emit a structured
warning without configuring a logger.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = ["Warning_", "WarningCollector"]


@dataclass
class Warning_:
    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)


class WarningCollector(logging.Handler):
    """Captures user-facing warnings as structured records.

    Usable as both a :class:`logging.Handler` (attach to any logger) and via
    the :meth:`add` shortcut for direct callers.
    """

    def __init__(self, *, level: int = logging.WARNING) -> None:
        super().__init__(level=level)
        self._warnings: list[Warning_] = []

    # ── logging.Handler interface ──────────────────────────────────────

    def emit(self, record: logging.LogRecord) -> None:
        """Store the log record as a :class:`Warning_` and echo to stderr."""
        code: str = getattr(record, "code", record.levelname)
        message: str = self.format(record) if self.formatter else record.getMessage()
        context: dict[str, Any] = getattr(record, "context", {})
        self._warnings.append(Warning_(code=code, message=message, context=context))
        print(f"WARN [{code}] {message}", file=sys.stderr)

    # ── Legacy shortcut ───────────────────────────────────────────────

    def add(self, code: str, message: str, **context: object) -> None:
        """Create a :class:`logging.LogRecord` with *code* and delegate to
        :meth:`emit` so both storage paths converge.
        """
        record = logging.LogRecord(
            name=__name__,
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        # Extra attributes code/context are not on LogRecord's public type;
        # setattr is used so static checkers don't trip on attr-defined.
        record.code = code
        record.context = context
        self.handle(record)

    # ── Aggregation helpers ───────────────────────────────────────────

    def extend(self, other: WarningCollector) -> None:
        """Merge all warnings from *other* into this collector."""
        self._warnings.extend(other._warnings)

    def as_list(self) -> list[dict[str, Any]]:
        """Return warnings as plain dicts (JSON-serialisable)."""
        return [asdict(w) for w in self._warnings]

    def __len__(self) -> int:
        return len(self._warnings)

    def __iter__(self) -> Iterator[Warning_]:
        return iter(self._warnings)
