"""Execution rounds — how an AI assistant actually runs a solved schedule.

A subagent orchestrator has one synchronisation primitive: launch a batch
of subagents in parallel, wait for *all* of them, launch the next batch.
``waves`` (tasks sharing a start time) are the wrong unit for that: three
lanes with unequal durations produce a wave per distinct start time and
the barriers serialise almost everything.

A **round** instead gives every lane (agent) the longest prefix of its
remaining queue whose cross-lane predecessors are already complete. Each
subagent then works through its segment sequentially while the other
lanes do the same; the orchestrator waits once per round.

The same function drives two uses:

* :func:`build_rounds` — the static plan rendered into ``schedule.md``
  (repeatedly apply :func:`next_round`, marking each round as done).
* :func:`next_round` with the *actual* ``done`` set read from
  ``tasks.md`` checkboxes — what ``python -m solver next`` emits, so the
  loop is resumable and tolerant of tasks completed out of order.

Pure module: plain ids and dicts in, frozen dataclasses out. No solver
or filesystem access.
"""

from __future__ import annotations

__all__ = [
    "Blocked",
    "Lane",
    "Round",
    "barrier_makespan",
    "build_rounds",
    "lane_queues",
    "next_round",
    "predecessor_map",
    "round_to_dict",
    "rounds_from_result",
]

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Lane:
    """Ordered segment of tasks one subagent executes during a round."""

    agent_id: str
    task_ids: tuple[str, ...]


@dataclass(frozen=True)
class Blocked:
    """A lane that cannot start its next task yet (dynamic use only)."""

    agent_id: str
    task_id: str
    waiting_on: tuple[str, ...]


@dataclass(frozen=True)
class Round:
    """One parallel launch: every lane's segment plus lanes still blocked."""

    index: int
    lanes: tuple[Lane, ...]
    blocked: tuple[Blocked, ...] = ()

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(tid for lane in self.lanes for tid in lane.task_ids)


def lane_queues(assignments: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Group assignments by agent, ordered by solved start time (ties by id)."""
    rows = sorted(assignments, key=lambda a: (int(a["start"]), str(a["task_id"])))
    queues: dict[str, list[str]] = {}
    for a in rows:
        queues.setdefault(str(a["agent_id"]), []).append(str(a["task_id"]))
    return queues


def predecessor_map(
    task_ids: Iterable[str],
    *edge_lists: Iterable[Iterable[str]],
) -> dict[str, set[str]]:
    """Predecessors per task from any number of ``[src, dst]`` edge lists."""
    preds: dict[str, set[str]] = {tid: set() for tid in task_ids}
    for edges in edge_lists:
        for edge in edges:
            src, dst = tuple(edge)
            preds.setdefault(dst, set()).add(src)
            preds.setdefault(src, set())
    return preds


def next_round(
    queues: Mapping[str, list[str]],
    preds: Mapping[str, set[str]],
    done: set[str],
    *,
    index: int = 1,
) -> Round | None:
    """Return the next parallel batch given what is already ``done``.

    For every lane, take the longest prefix of its pending tasks such
    that each task's predecessors are either done or earlier in that
    same prefix (the subagent runs the segment in order). Predecessors
    that sit in *another* lane must be done — that is the barrier.

    Returns ``None`` when nothing is pending. A lane whose first pending
    task is blocked is reported in ``Round.blocked`` (empty in the static
    case, since a valid DAG always has a ready task per round).
    """
    lanes: list[Lane] = []
    blocked: list[Blocked] = []
    for agent_id, queue in queues.items():
        pending = [tid for tid in queue if tid not in done]
        if not pending:
            continue
        segment: list[str] = []
        in_segment: set[str] = set()
        for tid in pending:
            waiting = [p for p in preds.get(tid, ()) if p not in done and p not in in_segment]
            if waiting:
                if not segment:
                    blocked.append(Blocked(agent_id, tid, tuple(sorted(waiting))))
                break
            segment.append(tid)
            in_segment.add(tid)
        if segment:
            lanes.append(Lane(agent_id, tuple(segment)))
    if not lanes and not blocked:
        return None
    return Round(index=index, lanes=tuple(lanes), blocked=tuple(blocked))


def build_rounds(
    queues: Mapping[str, list[str]],
    preds: Mapping[str, set[str]],
    done: set[str] | None = None,
) -> list[Round]:
    """Static round plan: apply :func:`next_round` until every task is placed.

    Raises ``ValueError`` if a round makes no progress, which can only
    happen when ``preds`` is not a DAG consistent with the lane order.
    """
    done_so_far = set(done or ())
    rounds: list[Round] = []
    while True:
        rnd = next_round(queues, preds, done_so_far, index=len(rounds) + 1)
        if rnd is None:
            return rounds
        if not rnd.lanes:
            stuck = ", ".join(f"{b.task_id} ← {', '.join(b.waiting_on)}" for b in rnd.blocked)
            raise ValueError(f"round {rnd.index} cannot make progress: {stuck}")
        rounds.append(Round(index=rnd.index, lanes=rnd.lanes))
        done_so_far.update(rnd.task_ids)


def barrier_makespan(rounds: Iterable[Round], duration_of: Mapping[str, int]) -> int:
    """Wall time under barrier semantics: Σ over rounds of the longest lane."""
    total = 0
    for rnd in rounds:
        total += max(
            (sum(int(duration_of.get(t, 0)) for t in lane.task_ids) for lane in rnd.lanes),
            default=0,
        )
    return total


def round_to_dict(rnd: Round) -> dict[str, Any]:
    """JSON-serialisable form used in the result envelope / schedule.json."""
    out: dict[str, Any] = {
        "round": rnd.index,
        "lanes": [{"agent_id": lane.agent_id, "tasks": list(lane.task_ids)} for lane in rnd.lanes],
    }
    if rnd.blocked:
        out["blocked"] = [
            {"agent_id": b.agent_id, "task_id": b.task_id, "waiting_on": list(b.waiting_on)}
            for b in rnd.blocked
        ]
    return out


def rounds_from_result(
    result: Mapping[str, Any],
    done: set[str] | None = None,
) -> list[Round]:
    """Rebuild rounds from a solver result / ``schedule.json`` envelope."""
    assignments = result.get("assignments", []) or []
    task_ids = [str(a["task_id"]) for a in assignments]
    preds = predecessor_map(
        task_ids,
        result.get("edges", []) or [],
        result.get("resource_edges", []) or [],
    )
    return build_rounds(lane_queues(assignments), preds, done)
