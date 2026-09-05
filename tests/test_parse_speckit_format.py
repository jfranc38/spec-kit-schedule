"""Parser compatibility with the tasks.md that spec-kit 0.16 actually generates.

The official ``/speckit.tasks`` format is::

    - [ ] T012 [P] [US1] Create User model in src/models/user.py

bare file paths (no backticks), ``(Priority: P1)`` story headers, and
``### Tests for User Story N`` / ``### Implementation for User Story N``
sub-sections. ``tests/fixtures/tasks-speckit-0.16.md`` mirrors the
upstream ``tasks-template.md`` with realistic names.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from solver.defaults import DEFAULT_SKILL_RULES
from solver.parse_tasks import extract_file_paths, parse_tasks_md
from solver.validation import ScheduleInputError
from tests._helpers import SPECKIT_FIXTURE

# A portfolio wide enough that every inferred skill is covered.
_WIDE_AGENT = {
    "id": "worker",
    "model": "test",
    "skills": ["backend", "test", "api", "schema", "frontend", "review"],
    "kappa": 100,
    "context_budget": 1000,
}


def _parse(path: Path, **overrides: object) -> dict:
    # Declaring ``agents`` switches the default skill rules off (advanced
    # mode keeps the user's vocabulary), so pass the canonical list explicitly.
    cfg: dict = {
        "agents": [_WIDE_AGENT],
        "skill_rules": [dict(r) for r in DEFAULT_SKILL_RULES],
        "solver": {"time_limit": 5, "num_workers": 1},
    }
    cfg.update(overrides)
    return parse_tasks_md(str(path), cfg)


def _reachable(edges: list[list[str]], src: str, dst: str) -> bool:
    g = nx.DiGraph()
    g.add_edges_from((a, b) for a, b in edges)
    return src in g and dst in g and nx.has_path(g, src, dst)


# ── Bare path extraction ────────────────────────────────────────────────


class TestExtractFilePaths:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Create User model in src/models/user.py", ["src/models/user.py"]),
            ("Configure linting in ruff.toml", ["ruff.toml"]),
            ("Update package.json and Makefile", ["package.json", "Makefile"]),
            ("Documentation updates in docs/", ["docs/"]),
            ("Move helpers to backend/src/ then frontend/src/App.tsx",
             ["backend/src/", "frontend/src/App.tsx"]),
            ("Wire `src/api/x.py` and tests/test_x.py", ["src/api/x.py", "tests/test_x.py"]),
            ("Contract test for POST /notes/{id}/share in tests/contract/test_share.py",
             ["tests/contract/test_share.py"]),
        ],
    )
    def test_paths_extracted(self, text: str, expected: list[str]) -> None:
        assert extract_file_paths(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "Initialize Node.js project with Next.js and Vue.js",
            "Bump version to 1.2.3 (v2.0)",
            "See https://example.com/docs/guide.md for details",
            "Create base models/entities that all stories depend on",
            "Refactor the read/write path and/or the cache",
            "Add logging for user story 1 operations, e.g. request ids",
            "Run the quickstart validation i.e. the smoke flow",
        ],
    )
    def test_prose_is_not_a_path(self, text: str) -> None:
        assert extract_file_paths(text) == []

    def test_preposition_enables_unknown_extension(self) -> None:
        assert extract_file_paths("Store secrets in vault.kv") == ["vault.kv"]
        assert extract_file_paths("Store secrets vault.kv") == []

    def test_normalised_and_deduplicated(self) -> None:
        assert extract_file_paths("Edit ./src/a.py and src/./a.py") == ["src/a.py"]


# ── Fixture end-to-end ──────────────────────────────────────────────────


class TestSpeckitFixture:
    def test_every_task_parsed(self) -> None:
        result = _parse(SPECKIT_FIXTURE)
        ids = [t["id"] for t in result["tasks"]]
        assert ids == [f"T{i:03d}" for i in range(1, 26)]

    def test_bare_paths_and_story_context(self) -> None:
        by_id = {t["id"]: t for t in _parse(SPECKIT_FIXTURE)["tasks"]}
        assert by_id["T011"]["file_paths"] == ["src/models/note.py"]
        assert by_id["T002"]["file_paths"] == ["pyproject.toml"]
        assert by_id["T022"]["file_paths"] == ["docs/"]
        assert by_id["T001"]["file_paths"] == []
        # Story context survives the "### Implementation for User Story 1" sub-header.
        for tid in ("T009", "T010", "T011", "T016"):
            assert by_id[tid]["phase"] == "User Story 1"
            assert by_id[tid]["story_id"] == "US1"
        assert by_id["T020"]["story_id"] == "US2"

    def test_priority_from_priority_colon_form(self) -> None:
        by_id = {t["id"]: t for t in _parse(SPECKIT_FIXTURE)["tasks"]}
        assert by_id["T011"]["story_priority"] == 1
        assert by_id["T018"]["story_priority"] == 2
        assert by_id["T004"]["story_priority"] == 99

    def test_tags_and_checkbox(self) -> None:
        by_id = {t["id"]: t for t in _parse(SPECKIT_FIXTURE)["tasks"]}
        assert by_id["T003"]["parallel_flag"] is True
        assert by_id["T004"]["parallel_flag"] is False
        assert all(not t["done"] for t in by_id.values())
        assert by_id["T013"]["description"] == (
            "Implement NoteService in src/services/note_service.py"
        )
        assert by_id["T013"]["source_line"] == 64

    def test_explicit_depends_on_prose(self) -> None:
        edges = _parse(SPECKIT_FIXTURE)["edges"]
        assert ["T011", "T013"] in edges
        assert ["T012", "T013"] in edges

    def test_phase_barriers_are_complete(self) -> None:
        edges = _parse(SPECKIT_FIXTURE)["edges"]
        # Every Setup task precedes every Foundational task (transitively).
        for s in ("T001", "T002", "T003"):
            for f in ("T004", "T005", "T006", "T007", "T008"):
                assert _reachable(edges, s, f), f"{s} must precede {f}"
        # Every Foundational task precedes every story task.
        for f in ("T005", "T006"):
            for st in ("T009", "T011", "T017", "T018"):
                assert _reachable(edges, f, st)
        # Stories are independent of each other …
        assert not _reachable(edges, "T016", "T017")
        assert not _reachable(edges, "T021", "T009")
        # … and Polish waits for every story.
        for st in ("T016", "T021"):
            for p in ("T022", "T023", "T024", "T025"):
                assert _reachable(edges, st, p)

    def test_same_file_order_within_story(self) -> None:
        edges = _parse(SPECKIT_FIXTURE)["edges"]
        # T014 and T015 both write src/api/notes.py inside US1.
        assert ["T014", "T015"] in edges
        # T020 writes the same file in US2 — different scope, no cross-story edge.
        assert ["T015", "T020"] not in edges

    def test_tests_first_within_story(self) -> None:
        edges = _parse(SPECKIT_FIXTURE)["edges"]
        # Test tasks declared first precede the story's implementation tasks.
        assert _reachable(edges, "T009", "T011")
        assert _reachable(edges, "T010", "T013")
        assert _reachable(edges, "T017", "T018")
        # Polish unit tests are not TDD: no edge to Polish impl tasks.
        assert not _reachable(edges, "T024", "T023")

    def test_dag_is_acyclic(self) -> None:
        result = _parse(SPECKIT_FIXTURE)
        g = nx.DiGraph()
        g.add_nodes_from(t["id"] for t in result["tasks"])
        g.add_edges_from((a, b) for a, b in result["edges"])
        assert nx.is_directed_acyclic_graph(g)
        assert result["warnings"] == []


# ── Header handling ─────────────────────────────────────────────────────


class TestHeaders:
    def test_deeper_header_under_story_is_a_subsection(self, write_tasks) -> None:
        p = write_tasks(
            "## Phase 3: User Story 1 - Login (Priority: P1)\n"
            "### Implementation\n"
            "- [ ] T001 [US1] Implement login in src/auth.py\n"
            "### Tests\n"
            "- [ ] T002 [US1] Test login in tests/test_auth.py\n"
        )
        by_id = {t["id"]: t for t in _parse(p)["tasks"]}
        assert by_id["T001"]["phase"] == "User Story 1"
        assert by_id["T002"]["story_id"] == "US1"

    def test_same_depth_header_switches_phase(self, write_tasks) -> None:
        p = write_tasks(
            "## Phase 3: User Story 1 (Priority: P1)\n"
            "- [ ] T001 [US1] Implement a in src/a.py\n"
            "## Phase 4: Polish & Cross-Cutting Concerns\n"
            "- [ ] T002 Clean up src/a.py\n"
        )
        by_id = {t["id"]: t for t in _parse(p)["tasks"]}
        assert by_id["T002"]["phase"] == "Polish"
        assert by_id["T002"]["story_id"] is None

    def test_top_level_implementation_phase_still_recognised(self, write_tasks) -> None:
        p = write_tasks(
            "## Setup\n"
            "- [ ] T001 Add config in src/a.py\n"
            "## Implementation Phase\n"
            "- [ ] T002 Implement b in src/b.py\n"
        )
        by_id = {t["id"]: t for t in _parse(p)["tasks"]}
        assert by_id["T002"]["phase"] == "Implementation"

    def test_story_tag_order_agnostic(self, write_tasks) -> None:
        p = write_tasks(
            "## User Story 2 (P2)\n"
            "- [ ] T001 [US2] [P] Build card in src/components/Card.tsx\n"
            "- [ ] T002 [P] [US2] Build list in src/components/List.tsx\n"
        )
        tasks = _parse(p)["tasks"]
        assert all(t["parallel_flag"] and t["story_id"] == "US2" for t in tasks)
        assert tasks[0]["action_verb"] == "Build"

    def test_annotations_in_any_order(self, write_tasks) -> None:
        p = write_tasks(
            "## Setup\n"
            "- [ ] T001 Implement a in src/a.py\n"
            "- [ ] T002 Implement b in src/b.py (depends on T001) (skill: review)\n"
            "- [ ] T003 Implement c in src/c.py (skill: review) (depends on T002)\n"
        )
        result = _parse(p)
        by_id = {t["id"]: t for t in result["tasks"]}
        assert ["T001", "T002"] in result["edges"]
        assert ["T002", "T003"] in result["edges"]
        assert by_id["T002"]["required_skill"] == "review"
        assert by_id["T003"]["description"] == "Implement c in src/c.py"

    def test_fenced_code_blocks_are_ignored(self, write_tasks) -> None:
        p = write_tasks(
            "## Phase 3: User Story 1 (Priority: P1)\n"
            "- [ ] T001 [US1] Implement a in src/a.py\n"
            "```bash\n"
            "# Setup the environment first\n"
            "- [ ] T999 not a task\n"
            "```\n"
            "- [ ] T002 [US1] Implement b in src/b.py\n"
        )
        tasks = _parse(p)["tasks"]
        assert [t["id"] for t in tasks] == ["T001", "T002"]
        assert tasks[1]["phase"] == "User Story 1"

    def test_done_checkbox_recorded(self, write_tasks) -> None:
        p = write_tasks(
            "## Setup\n"
            "- [x] T001 Implement a in src/a.py\n"
            "- [X] T002 Implement b in src/b.py\n"
            "- [ ] T003 Implement c in src/c.py\n"
        )
        done = {t["id"]: t["done"] for t in _parse(p)["tasks"]}
        assert done == {"T001": True, "T002": True, "T003": False}


# ── Cycle handling ──────────────────────────────────────────────────────


class TestCycles:
    def test_same_file_edge_dropped_when_it_contradicts_explicit_order(
        self, write_tasks
    ) -> None:
        # Declaration order says T001 → T002 on the shared file, but the
        # user explicitly wants T002 first. The heuristic edge yields.
        p = write_tasks(
            "## Setup\n"
            "- [ ] T001 Update schema in src/schema.sql (depends on T002)\n"
            "- [ ] T002 Design schema in src/schema.sql\n"
        )
        result = _parse(p)
        assert ["T002", "T001"] in result["edges"]
        assert ["T001", "T002"] not in result["edges"]
        codes = [w["code"] for w in result["warnings"]]
        assert codes == ["heuristic_edge_dropped"]
        assert result["warnings"][0]["context"]["origin"] == "same-file"

    def test_tdd_edge_dropped_when_it_contradicts_explicit_order(self, write_tasks) -> None:
        p = write_tasks(
            "## User Story 1 (P1)\n"
            "- [ ] T001 [US1] Write tests in tests/test_a.py (depends on T002)\n"
            "- [ ] T002 [US1] Implement a in src/a.py\n"
        )
        result = _parse(p)
        assert ["T002", "T001"] in result["edges"]
        assert ["T001", "T002"] not in result["edges"]
        assert {w["code"] for w in result["warnings"]} == {"heuristic_edge_dropped"}

    def test_explicit_cycle_is_fatal(self, write_tasks) -> None:
        p = write_tasks(
            "## Setup\n"
            "- [ ] T001 Implement a in src/a.py (depends on T002)\n"
            "- [ ] T002 Implement b in src/b.py (depends on T001)\n"
        )
        with pytest.raises(ScheduleInputError, match="cycle"):
            _parse(p)

    def test_explicit_dependency_against_phase_order_is_fatal(self, write_tasks) -> None:
        p = write_tasks(
            "## Setup\n"
            "- [ ] T001 Configure tooling in ruff.toml (depends on T002)\n"
            "## Foundational\n"
            "- [ ] T002 Create base model in src/models/base.py\n"
        )
        with pytest.raises(ScheduleInputError, match="cycle"):
            _parse(p)


# ── Zero-config skill rules ─────────────────────────────────────────────


class TestDefaultSkillRules:
    def test_declared_agents_switch_default_rules_off(self, write_tasks) -> None:
        p = write_tasks(
            "## Setup\n"
            "- [ ] T001 Write tests in tests/test_a.py\n"
            "- [ ] T002 Implement a in src/a.py\n"
        )
        with_agents = parse_tasks_md(str(p), {"agents": [_WIDE_AGENT]})
        assert [t["required_skill"] for t in with_agents["tasks"]] == ["backend", "backend"]


class TestCycleResolutionAcrossPhases:
    """A story-tagged test task placed under Polish is a real spec-kit shape."""

    STORY_IMPL = (
        "## Phase 3: User Story 1 (Priority: P1)\n"
        "- [ ] T001 [US1] Implement service in src/svc.py\n"
        "## Phase 4: Polish\n"
    )

    def test_edges_are_unique_after_cycle_breaking(self, write_tasks) -> None:
        # same-file T001→T002 is dropped by the breaker, then re-derived as a
        # phase barrier: the output must still list the pair once.
        p = write_tasks(
            self.STORY_IMPL
            + "- [ ] T002 [US1] Add unit tests for service in tests/test_svc.py and src/svc.py\n"
        )
        edges = _parse(p)["edges"]
        assert ["T001", "T002"] in edges
        assert len(edges) == len({tuple(e) for e in edges})

    def test_backward_tdd_edge_across_phases_is_dropped_not_fatal(self, write_tasks) -> None:
        # [P] removes the same-file edge; only tdd (Polish→US1) + the phase
        # barrier (US1→Polish) remain, and a heuristic edge never makes a
        # cycle fatal.
        p = write_tasks(
            self.STORY_IMPL
            + "- [ ] T002 [P] [US1] Add unit tests for service in tests/test_svc.py and src/svc.py\n"
        )
        result = _parse(p)
        assert result["edges"] == [["T001", "T002"]]
        assert [w["code"] for w in result["warnings"]] == ["heuristic_edge_dropped"]


class TestEdgeRulesWithoutFixtureCoverage:
    def test_test_task_precedes_impl_sharing_its_file_in_the_same_scope(self, write_tasks) -> None:
        # [P] on the test keeps the same-file rule out; only the TDD join remains.
        p = write_tasks(
            "## Phase 3: User Story 1 (Priority: P1)\n"
            "- [ ] T001 [US1] Implement service in src/svc.py\n"
            "- [ ] T002 [P] [US1] Write unit tests for service in tests/test_svc.py and src/svc.py\n"
        )
        assert _parse(p)["edges"] == [["T002", "T001"]]

    def test_polish_waits_for_the_prerequisite_chain_when_there_are_no_stories(self, write_tasks) -> None:
        p = write_tasks(
            "## Phase 1: Setup\n- [ ] T001 Init project in pyproject.toml\n"
            "## Phase 2: Foundational\n- [ ] T002 Add base model in src/base.py\n"
            "## Phase 4: Polish\n- [ ] T003 Add docs in docs/README.md\n"
        )
        assert _parse(p)["edges"] == [["T001", "T002"], ["T002", "T003"]]
