"""Hypothesis property test: rounds are a valid barrier plan for any lane-consistent DAG."""

from __future__ import annotations

import pytest

# Hypothesis is a dev-only dependency; skip only this module without it.
pytest.importorskip("hypothesis")

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from solver.rounds import build_rounds, predecessor_map  # noqa: E402
from tests.test_rounds import _assert_valid  # noqa: E402


@st.composite
def _dag_with_lanes(draw: st.DrawFn) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    n = draw(st.integers(min_value=1, max_value=12))
    n_lanes = draw(st.integers(min_value=1, max_value=4))
    ids = [f"T{i:02d}" for i in range(n)]
    # Random DAG: edges only from lower to higher index.
    edges: list[list[str]] = []
    for j in range(1, n):
        for i in range(j):
            if draw(st.booleans()) and draw(st.integers(0, 3)) == 0:
                edges.append([ids[i], ids[j]])
    # Lane assignment in index order keeps lane order consistent with the DAG.
    queues: dict[str, list[str]] = {f"w{k}": [] for k in range(n_lanes)}
    for tid in ids:
        queues[f"w{draw(st.integers(0, n_lanes - 1))}"].append(tid)
    queues = {k: v for k, v in queues.items() if v}
    # Same-lane consecutive arcs, as the realised schedule graph would have.
    for q in queues.values():
        edges.extend([a, b] for a, b in zip(q, q[1:], strict=False))
    return queues, predecessor_map(ids, edges)


@given(_dag_with_lanes())
@settings(max_examples=150, deadline=None)
def test_property_rounds_valid(case: tuple[dict[str, list[str]], dict[str, set[str]]]) -> None:
    queues, preds = case
    rounds = build_rounds(queues, preds)
    _assert_valid(rounds, queues, preds)
    assert len(rounds) <= sum(len(q) for q in queues.values())
