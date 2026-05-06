"""Unit tests for solver.parse_tasks."""

from __future__ import annotations

import pytest

from solver.parse_tasks import (
    classify_complexity,
    infer_skill,
    parse_tasks_md,
)
from solver.scheduler import solve_from_json
from solver.validation import ScheduleInputError
from solver.warnings_collector import WarningCollector


class TestInferSkill:
    def test_longest_pattern_wins(self):
        rules = [
            {"pattern": "src/", "skill": "backend"},
            {"pattern": "src/components/", "skill": "frontend"},
        ]
        assert infer_skill(["src/components/Button.tsx"], rules, "x") == "frontend"

    def test_default_when_no_match(self):
        assert infer_skill(["foo.txt"], [], "default") == "default"

    def test_tie_breaks_by_config_order(self):
        rules = [
            {"pattern": "test", "skill": "first"},
            {"pattern": "test", "skill": "second"},
        ]
        # Equal length → earliest listed wins.
        assert infer_skill(["test_thing"], rules, "x") == "first"


class TestClassifyComplexity:
    def test_known_verb_classified_to_simple(self):
        verbs = {"simple": ["add"], "medium": ["implement"], "complex": [], "review": []}
        assert classify_complexity("Add", verbs) == "simple"

    def test_fallback_medium(self):
        assert (
            classify_complexity(
                "unknown", {"simple": [], "medium": [], "complex": [], "review": []}
            )
            == "medium"
        )


class TestParseHappyPath:
    def test_single_task(self, write_tasks, minimal_config):
        p = write_tasks("## Setup Phase\n" "- [ ] T001 Implement feature in `src/api/foo.py`\n")
        result = parse_tasks_md(str(p), minimal_config)
        assert len(result["tasks"]) == 1
        t = result["tasks"][0]
        assert t["id"] == "T001"
        assert t["required_skill"] == "api"
        assert t["file_paths"] == ["src/api/foo.py"]
        assert t["phase"] == "Setup"

    def test_parallel_flag_detected(self, write_tasks, minimal_config):
        p = write_tasks("## Setup Phase\n" "- [ ] T001 [P] Add config in `src/api/foo.py`\n")
        result = parse_tasks_md(str(p), minimal_config)
        assert result["tasks"][0]["parallel_flag"] is True

    def test_depends_on_parsed(self, write_tasks, minimal_config):
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Implement thing in `src/api/a.py`\n"
            "- [ ] T002 Implement other in `src/api/b.py` (depends on T001)\n"
        )
        result = parse_tasks_md(str(p), minimal_config)
        assert ["T001", "T002"] in result["edges"]

    def test_story_priority_captured(self, write_tasks, minimal_config):
        p = write_tasks(
            "## User Story 1 (P1)\n" "- [ ] T001 [US1] Implement thing in `src/api/a.py`\n"
        )
        result = parse_tasks_md(str(p), minimal_config)
        t = result["tasks"][0]
        assert t["story_id"] == "US1"
        assert t["story_priority"] == 1

    def test_path_normalization(self, write_tasks, minimal_config):
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Implement x in `./src/api/a.py`\n"
            "- [ ] T002 Implement y in `src/api/a.py`\n"
        )
        result = parse_tasks_md(str(p), minimal_config)
        # Both tasks on the same normalised file → sequential edge added.
        assert ["T001", "T002"] in result["edges"]

    def test_checkbox_variants(self, write_tasks, minimal_config):
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Implement a in `src/api/a.py`\n"
            "- [x] T002 Implement b in `src/api/b.py`\n"
            "- [X] T003 Implement c in `src/api/c.py`\n"
        )
        result = parse_tasks_md(str(p), minimal_config)
        assert len(result["tasks"]) == 3

    def test_agent_price_is_preserved(self, write_tasks, clone_config):
        cfg = clone_config()
        cfg["agents"][0]["price_per_1k_tokens"] = 3.25
        p = write_tasks("## Setup Phase\n" "- [ ] T001 Implement feature in `src/api/foo.py`\n")

        result = parse_tasks_md(str(p), cfg)

        assert result["agents"][0]["price_per_1k_tokens"] == 3.25

    def test_token_std_dev_is_preserved(self, write_tasks, clone_config):
        cfg = clone_config()
        cfg["token_estimates"]["medium"] = {"mean": 3500, "std_dev": 700}
        p = write_tasks("## Setup Phase\n" "- [ ] T001 Implement feature in `src/api/foo.py`\n")

        result = parse_tasks_md(str(p), cfg)

        assert result["tasks"][0]["estimated_tokens"] == 3500
        assert result["tasks"][0]["token_std_dev"] == 700


class TestParseFailFast:
    def test_duplicate_task_id_raises(self, write_tasks, minimal_config):
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Implement a in `src/api/a.py`\n"
            "- [ ] T001 Implement b in `src/api/b.py`\n"
        )
        with pytest.raises(ScheduleInputError, match="Duplicate task id 'T001'"):
            parse_tasks_md(str(p), minimal_config)

    def test_unknown_dependency_raises(self, write_tasks, minimal_config):
        p = write_tasks(
            "## Setup Phase\n" "- [ ] T001 Implement a in `src/api/a.py` (depends on T999)\n"
        )
        with pytest.raises(ScheduleInputError, match="Unresolved dependencies"):
            parse_tasks_md(str(p), minimal_config)

    def test_cycle_raises(self, write_tasks, minimal_config):
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Implement a in `src/api/a.py` (depends on T002)\n"
            "- [ ] T002 Implement b in `src/api/b.py` (depends on T001)\n"
        )
        with pytest.raises(ScheduleInputError, match="cycle"):
            parse_tasks_md(str(p), minimal_config)

    def test_empty_file_raises(self, write_tasks, minimal_config):
        p = write_tasks("# Header only\n\n")
        with pytest.raises(ScheduleInputError, match="No tasks"):
            parse_tasks_md(str(p), minimal_config)


class TestPhaseRegex:
    def test_phase_keyword_anchored(self, write_tasks, minimal_config):
        p = write_tasks("## Advanced Planning Notes\n" "- [ ] T001 Implement a in `src/api/a.py`\n")
        result = parse_tasks_md(str(p), minimal_config)
        assert result["tasks"][0]["phase"] == "Setup"

    def test_phase_with_number_prefix(self, write_tasks, minimal_config):
        p = write_tasks(
            "## Phase 1: Setup\n"
            "- [ ] T001 Implement a in `src/api/a.py`\n"
            "## Phase 2: Foundational\n"
            "- [ ] T002 Implement b in `src/api/b.py`\n"
        )
        result = parse_tasks_md(str(p), minimal_config)
        by_id = {t["id"]: t for t in result["tasks"]}
        assert by_id["T001"]["phase"] == "Setup"
        assert by_id["T002"]["phase"] == "Foundational"

    def test_phase_implementation_recognized(self, write_tasks, minimal_config):
        """`## Implementation Phase` maps to the Implementation bucket, not Setup."""
        p = write_tasks(
            "## Implementation Phase\n"
            "- [ ] T001 Implement endpoint in `src/api/a.py`\n"
        )
        result = parse_tasks_md(str(p), minimal_config)
        assert result["tasks"][0]["phase"] == "Implementation"

    def test_phase_build_synonym(self, write_tasks, minimal_config):
        """`## Build Phase` is a recognized synonym for Implementation."""
        p = write_tasks(
            "## Build Phase\n"
            "- [ ] T001 Implement endpoint in `src/api/a.py`\n"
        )
        result = parse_tasks_md(str(p), minimal_config)
        assert result["tasks"][0]["phase"] == "Implementation"

    def test_phase_develop_synonym(self, write_tasks, minimal_config):
        """`## Develop Phase` is a recognized synonym for Implementation."""
        p = write_tasks(
            "## Develop Phase\n"
            "- [ ] T001 Implement endpoint in `src/api/a.py`\n"
        )
        result = parse_tasks_md(str(p), minimal_config)
        assert result["tasks"][0]["phase"] == "Implementation"


class TestParallelWriteWarning:
    def test_double_parallel_write_emits_warning(self, write_tasks, minimal_config):
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 [P] Implement x in `src/api/shared.py`\n"
            "- [ ] T002 [P] Implement y in `src/api/shared.py`\n"
        )
        warnings = WarningCollector()
        result = parse_tasks_md(str(p), minimal_config, warnings=warnings)
        codes = {w["code"] for w in result["warnings"]}
        assert "parallel_write_conflict" in codes


class TestExplicitSkillAnnotation:
    """Inline ``(skill: <name>)`` overrides the path/verb-based inference."""

    def test_explicit_skill_overrides_inference(self, write_tasks, clone_config):
        cfg = clone_config()
        # Add an extra agent + skill so 'docs' is part of the portfolio.
        cfg["agents"].append(
            {
                "id": "doc-writer",
                "model": "test",
                "skills": ["docs"],
                "kappa": 5,
                "context_budget": 32,
                "speed_factor": 1.0,
            }
        )
        # Without (skill: docs) the path src/api/foo.py would infer 'api'.
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Implement payment gateway in `src/api/foo.py` (skill: docs)\n"
        )
        result = parse_tasks_md(str(p), cfg)
        assert result["tasks"][0]["required_skill"] == "docs"

    def test_explicit_skill_stripped_from_action_verb(self, write_tasks, clone_config):
        """The annotation is removed from the desc before verb extraction."""
        cfg = clone_config()
        cfg["agents"].append(
            {
                "id": "designer",
                "model": "test",
                "skills": ["design"],
                "kappa": 5,
                "context_budget": 32,
                "speed_factor": 1.0,
            }
        )
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Design schema in `src/api/foo.py` (skill: design)\n"
        )
        result = parse_tasks_md(str(p), cfg)
        task = result["tasks"][0]
        assert task["required_skill"] == "design"
        # ``action_verb`` is the first word of the cleaned desc — must not be
        # 'skill' or anything from the annotation.
        assert task["action_verb"].lower() == "design"

    def test_no_explicit_skill_falls_back_to_inference(
        self, write_tasks, minimal_config
    ):
        """Without ``(skill: X)`` the existing path-based inference is unchanged."""
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Implement payment gateway in `src/api/foo.py`\n"
        )
        result = parse_tasks_md(str(p), minimal_config)
        # ``src/api/`` rule maps to 'api' in the minimal_config fixture.
        assert result["tasks"][0]["required_skill"] == "api"

    def test_explicit_skill_with_depends_on(self, write_tasks, clone_config):
        """Annotation co-exists with ``(depends on T###)`` on the same line."""
        cfg = clone_config()
        cfg["agents"].append(
            {
                "id": "doc-writer",
                "model": "test",
                "skills": ["docs"],
                "kappa": 5,
                "context_budget": 32,
                "speed_factor": 1.0,
            }
        )
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Implement first in `src/api/a.py`\n"
            "- [ ] T002 Implement second in `src/api/b.py` (skill: docs)"
            " (depends on T001)\n"
        )
        result = parse_tasks_md(str(p), cfg)
        by_id = {t["id"]: t for t in result["tasks"]}
        assert by_id["T002"]["required_skill"] == "docs"
        assert ["T001", "T002"] in result["edges"]

    def test_first_explicit_skill_wins_when_multiple(self, write_tasks, clone_config):
        """First ``(skill: X)`` annotation wins on the rare multi-annotation line."""
        cfg = clone_config()
        for sid in ("docs", "design"):
            cfg["agents"].append(
                {
                    "id": f"agent-{sid}",
                    "model": "test",
                    "skills": [sid],
                    "kappa": 5,
                    "context_budget": 32,
                    "speed_factor": 1.0,
                }
            )
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Implement a in `src/api/foo.py` (skill: docs) (skill: design)\n"
        )
        result = parse_tasks_md(str(p), cfg)
        assert result["tasks"][0]["required_skill"] == "docs"

    def test_unknown_explicit_skill_raises_via_solver(self, write_tasks, clone_config):
        """``(skill: X)`` with no agent providing X triggers ``skill_uncovered``."""
        cfg = clone_config()
        # Portfolio: only backend/api/test agents (no 'nonexistent').
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Do thing in `src/api/foo.py` (skill: nonexistent)\n"
        )
        parsed = parse_tasks_md(str(p), cfg)
        assert parsed["tasks"][0]["required_skill"] == "nonexistent"

        solver_input = {
            "tasks": parsed["tasks"],
            "edges": parsed["edges"],
            "agents": parsed["agents"],
            "config": parsed["config"],
        }
        with pytest.raises(ScheduleInputError, match="nonexistent"):
            solve_from_json(solver_input)

    def test_explicit_skill_does_not_match_depends_on(self, write_tasks, minimal_config):
        """Sanity: ``(depends on T###)`` is NOT picked up by the skill regex."""
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Implement a in `src/api/a.py`\n"
            "- [ ] T002 Implement b in `src/api/b.py` (depends on T001)\n"
        )
        result = parse_tasks_md(str(p), minimal_config)
        # Skill is path-inferred, not an extracted dep token.
        assert result["tasks"][1]["required_skill"] == "api"
