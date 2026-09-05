"""Execution rounds: barrier batches derived from a solved schedule."""

from __future__ import annotations

from pathlib import Path

import pytest

from solver.parse_tasks import parse_tasks_md
from solver.result.extract import _build_waves
from solver.rounds import (
    Lane,
    Round,
    barrier_makespan,
    build_rounds,
    lane_queues,
    next_round,
    predecessor_map,
    round_to_dict,
    rounds_from_result,
)
from solver.scheduler import solve_from_json
from tests._helpers import make_agent, make_solver_input, make_task

FIXTURE = Path(__file__).parent / "fixtures" / "tasks-speckit-0.16.md"


def _assn(task_id: str, agent: str, start: int, dur: int = 1) -> dict:
    return {"task_id": task_id, "agent_id": agent, "start": start, "duration": dur}


def _round_index(rounds: list[Round]) -> dict[str, tuple[int, str, int]]:
    """task_id → (round, lane, position within lane)."""
    where: dict[str, tuple[int, str, int]] = {}
    for rnd in rounds:
        for lane in rnd.lanes:
            for pos, tid in enumerate(lane.task_ids):
                where[tid] = (rnd.index, lane.agent_id, pos)
    return where


def _assert_valid(rounds: list[Round], queues: dict[str, list[str]], preds: dict[str, set[str]]) -> None:
    where = _round_index(rounds)
    every = [tid for q in queues.values() for tid in q]
    assert sorted(where) == sorted(every), "every task placed exactly once"
    for tid, ps in preds.items():
        for p in ps:
            r_p, lane_p, pos_p = where[p]
            r_t, lane_t, pos_t = where[tid]
            assert r_p < r_t or (r_p == r_t and lane_p == lane_t and pos_p < pos_t), (
                f"{p} must complete before {tid}"
            )
    # Lane order is preserved inside each round.
    for rnd in rounds:
        for lane in rnd.lanes:
            q = queues[lane.agent_id]
            positions = [q.index(t) for t in lane.task_ids]
            assert positions == sorted(positions)


class TestNextRound:
    def test_no_cross_lane_deps_is_one_round(self) -> None:
        queues = {"a": ["T1", "T2"], "b": ["T3", "T4"]}
        preds = predecessor_map(["T1", "T2", "T3", "T4"], [["T1", "T2"], ["T3", "T4"]])
        rounds = build_rounds(queues, preds)
        assert len(rounds) == 1
        assert rounds[0].lanes == (Lane("a", ("T1", "T2")), Lane("b", ("T3", "T4")))

    def test_cross_lane_chain_is_one_task_per_round(self) -> None:
        queues = {"a": ["T1", "T3"], "b": ["T2"]}
        preds = predecessor_map(["T1", "T2", "T3"], [["T1", "T2"], ["T2", "T3"]])
        rounds = build_rounds(queues, preds)
        assert [r.task_ids for r in rounds] == [("T1",), ("T2",), ("T3",)]

    def test_segment_stops_at_first_blocked_task(self) -> None:
        # Lane a can run T1 then must wait for T2 (lane b) before T3.
        queues = {"a": ["T1", "T3", "T4"], "b": ["T2"]}
        preds = predecessor_map(["T1", "T2", "T3", "T4"], [["T2", "T3"], ["T3", "T4"]])
        rounds = build_rounds(queues, preds)
        assert rounds[0].lanes == (Lane("a", ("T1",)), Lane("b", ("T2",)))
        assert rounds[1].lanes == (Lane("a", ("T3", "T4")),)

    def test_done_tasks_are_skipped_and_unblock(self) -> None:
        queues = {"a": ["T1", "T3"], "b": ["T2"]}
        preds = predecessor_map(["T1", "T2", "T3"], [["T2", "T3"]])
        first = next_round(queues, preds, done=set())
        assert first is not None
        assert first.lanes == (Lane("a", ("T1",)), Lane("b", ("T2",)))
        # Everything but T3 done → only lane a with T3.
        after = next_round(queues, preds, done={"T1", "T2"}, index=2)
        assert after is not None and after.lanes == (Lane("a", ("T3",)),) and after.index == 2
        assert next_round(queues, preds, done={"T1", "T2", "T3"}) is None

    def test_blocked_lane_is_reported(self) -> None:
        queues = {"a": ["T1"], "b": ["T2"]}
        preds = predecessor_map(["T1", "T2"], [["T1", "T2"]])
        rnd = next_round(queues, preds, done=set())
        assert rnd is not None
        assert rnd.lanes == (Lane("a", ("T1",)),)
        assert [(b.agent_id, b.task_id, b.waiting_on) for b in rnd.blocked] == [("b", "T2", ("T1",))]

    def test_out_of_order_completion_never_deadlocks(self) -> None:
        # T2 marked done before its predecessor T1 — T1 is still emitted.
        queues = {"a": ["T1", "T2", "T3"]}
        preds = predecessor_map(["T1", "T2", "T3"], [["T1", "T2"], ["T2", "T3"]])
        rnd = next_round(queues, preds, done={"T2"})
        assert rnd is not None and rnd.lanes == (Lane("a", ("T1", "T3")),)

    def test_inconsistent_graph_raises(self) -> None:
        queues = {"a": ["T1"], "b": ["T2"]}
        preds = predecessor_map(["T1", "T2"], [["T1", "T2"], ["T2", "T1"]])
        with pytest.raises(ValueError, match="cannot make progress"):
            build_rounds(queues, preds)


class TestHelpers:
    def test_lane_queues_orders_by_start_then_id(self) -> None:
        queues = lane_queues(
            [_assn("T2", "a", 5), _assn("T1", "a", 0), _assn("T9", "b", 0), _assn("T3", "b", 0)]
        )
        assert queues == {"a": ["T1", "T2"], "b": ["T3", "T9"]}

    def test_barrier_makespan_sums_longest_lane_per_round(self) -> None:
        rounds = [
            Round(1, (Lane("a", ("T1", "T2")), Lane("b", ("T3",)))),
            Round(2, (Lane("b", ("T4",)),)),
        ]
        dur = {"T1": 3, "T2": 4, "T3": 5, "T4": 2}
        assert barrier_makespan(rounds, dur) == 7 + 2

    def test_round_to_dict(self) -> None:
        rnd = next_round({"a": ["T1"], "b": ["T2"]}, predecessor_map(["T1", "T2"], [["T1", "T2"]]), set())
        assert rnd is not None
        assert round_to_dict(rnd) == {
            "round": 1,
            "lanes": [{"agent_id": "a", "tasks": ["T1"]}],
            "blocked": [{"agent_id": "b", "task_id": "T2", "waiting_on": ["T1"]}],
        }


class TestSolverIntegration:
    @pytest.fixture(scope="class")
    def solved(self) -> tuple[dict, dict]:
        parsed = parse_tasks_md(str(FIXTURE), {"solver": {"time_limit": 3, "num_workers": 1}})
        return parsed, solve_from_json(parsed)

    def test_result_carries_rounds_and_speedup(self, solved: tuple[dict, dict]) -> None:
        _, result = solved
        assert result["rounds"], "rounds present in result envelope"
        stats = result["stats"]
        assert stats["total_rounds"] == len(result["rounds"])
        assert stats["barrier_makespan"] >= stats["makespan"]
        assert stats["sequential_duration"] >= stats["barrier_makespan"]
        assert stats["speedup"] >= 1.0
        assert result["speedup"] == stats["speedup"]
        assert result["barrier_makespan"] == stats["barrier_makespan"]

    def test_rounds_respect_realised_dag(self, solved: tuple[dict, dict]) -> None:
        parsed, result = solved
        rounds = rounds_from_result(result)
        queues = lane_queues(result["assignments"])
        preds = predecessor_map(
            [t["id"] for t in parsed["tasks"]], result["edges"], result["resource_edges"]
        )
        _assert_valid(rounds, queues, preds)
        # Same rounds the solver stored.
        assert [round_to_dict(r) for r in rounds] == result["rounds"]

    def test_rounds_never_more_than_waves(self, solved: tuple[dict, dict]) -> None:
        _, result = solved
        assert len(result["rounds"]) <= len(_build_waves(result["assignments"]))

    def test_tasks_carry_description_and_done(self, solved: tuple[dict, dict]) -> None:
        _, result = solved
        by_id = {t["id"]: t for t in result["tasks"]}
        assert by_id["T011"]["description"] == "Create Note model in src/models/note.py"
        assert by_id["T011"]["file_paths"] == ["src/models/note.py"]
        assert by_id["T011"]["parallel_flag"] is True
        assert by_id["T011"]["done"] is False

    def test_independent_tasks_on_separate_workers_form_one_round(self) -> None:
        tasks = [make_task(f"T{i}", file_paths=[f"src/{i}.py"]) for i in range(1, 4)]
        agents = [make_agent(f"w{i}", skills=["*"]) for i in range(1, 4)]
        result = solve_from_json(make_solver_input(tasks, agents))
        assert result["stats"]["total_rounds"] == 1
        assert result["stats"]["speedup"] == pytest.approx(3.0)


hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402


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
