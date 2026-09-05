"""Execution rounds: barrier batches derived from a solved schedule."""

from __future__ import annotations

import pytest

from solver.rounds import (
    Lane,
    Round,
    barrier_makespan,
    build_rounds,
    lane_queues,
    next_round,
    predecessor_map,
    round_to_dict,
)
from solver.scheduler import solve_from_json
from tests._helpers import make_agent, make_solver_input, make_task


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
    def test_result_carries_rounds_and_speedup(self, speckit_solved: tuple[dict, dict]) -> None:
        _, result = speckit_solved
        assert result["rounds"], "rounds present in result envelope"
        stats = result["stats"]
        assert stats["total_rounds"] == len(result["rounds"])
        assert stats["barrier_makespan"] >= stats["makespan"]
        assert stats["sequential_duration"] >= stats["barrier_makespan"]
        assert stats["speedup"] >= 1.0
        assert stats["speedup"] == round(stats["sequential_duration"] / stats["barrier_makespan"], 2)
        assert result["speedup"] == stats["speedup"]
        assert result["barrier_makespan"] == stats["barrier_makespan"]

    def test_rounds_respect_realised_dag(self, speckit_solved: tuple[dict, dict]) -> None:
        parsed, result = speckit_solved
        queues = lane_queues(result["assignments"])
        preds = predecessor_map(
            [t["id"] for t in parsed["tasks"]], result["edges"], result["resource_edges"]
        )
        rounds = build_rounds(queues, preds)
        _assert_valid(rounds, queues, preds)
        # Same rounds the solver stored.
        assert [round_to_dict(r) for r in rounds] == result["rounds"]

    def test_rounds_never_more_than_waves(self, speckit_solved: tuple[dict, dict]) -> None:
        _, result = speckit_solved
        assert len(result["rounds"]) <= len(result["waves"])

    def test_tasks_carry_description_and_done(self, speckit_solved: tuple[dict, dict]) -> None:
        _, result = speckit_solved
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



def test_lane_stops_at_its_first_blocked_task() -> None:
    # B waits on another lane; C is free but comes after B in its own lane,
    # so the segment is the prefix [A], not [A, C].
    queues = {"w1": ["A", "B", "C"], "w2": ["X"]}
    preds = predecessor_map(["A", "B", "C", "X"], [["X", "B"]])
    rnd = next_round(queues, preds, set())
    assert rnd is not None
    assert {lane.agent_id: lane.task_ids for lane in rnd.lanes} == {"w1": ("A",), "w2": ("X",)}
