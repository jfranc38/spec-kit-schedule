"""Structured warnings surfaced to the user through schedule.md.

Each solver stage can accumulate warnings (non-fatal issues) that would
otherwise disappear into stderr. The renderer reads `stats["warnings"]`
and prints a dedicated section so the user never has to grep logs to
understand a surprising schedule.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field


@dataclass
class Warning_:
    code: str
    message: str
    context: dict = field(default_factory=dict)


class WarningCollector:
    def __init__(self) -> None:
        self._warnings: list[Warning_] = []

    def add(self, code: str, message: str, **context) -> None:
        self._warnings.append(Warning_(code=code, message=message, context=context))
        # Always echo to stderr so users see it in real time too.
        print(f"WARN [{code}] {message}", file=sys.stderr)

    def extend(self, others: WarningCollector) -> None:
        self._warnings.extend(others._warnings)

    def as_list(self) -> list[dict]:
        return [asdict(w) for w in self._warnings]

    def __len__(self) -> int:
        return len(self._warnings)

    def __iter__(self):
        return iter(self._warnings)
