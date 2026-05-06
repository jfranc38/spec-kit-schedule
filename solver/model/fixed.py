"""Helpers for resolving frozen-task fields shared across replan and solve_with_fixed."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..i18n import t
from ..validation import ScheduleInputError


def resolve_fixed_duration(
    assn: Mapping[str, Any],
    p_ia: int | None,
    *,
    task_id: str,
) -> int:
    """Resolve the integer duration for a frozen task assignment.

    Precedence: explicit ``duration`` field, then ``end - start`` if ``end``
    is present, then the supplied ``p_ia`` fallback (the current solver's
    duration for the (task, agent) pair). Raises ``ScheduleInputError`` if
    no source is available or if a source yielded a non-positive integer.
    This is the single point of duration-fallback logic shared by the
    replan helper and ``solve_with_fixed``.
    """
    d_raw = assn.get("duration")
    if d_raw is not None:
        d_fixed = int(d_raw)
    else:
        end_raw = assn.get("end")
        if end_raw is not None:
            s_raw = assn.get("start")
            s_fixed = int(s_raw) if s_raw is not None else 0
            d_fixed = int(end_raw) - s_fixed
        elif p_ia is not None:
            d_fixed = int(p_ia)
        else:
            d_fixed = 0
    if d_fixed <= 0:
        raise ScheduleInputError(
            t("replan_fixed_invalid_duration", tid=task_id, d=d_fixed)
        )
    return d_fixed
