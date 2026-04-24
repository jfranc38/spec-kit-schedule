"""Unit tests for solver.parse_tasks."""
from __future__ import annotations

import pytest

from solver.parse_tasks import (
    classify_complexity,
    infer_skill,
    parse_tasks_md,
)
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
    def test_happy_path(self):
        verbs = {"simple": ["add"], "medium": ["implement"], "complex": [], "review": []}
        assert classify_complexity("Add", verbs) == "simple"

    def test_fallback_medium(self):
        assert classify_complexity("unknown", {"simple": [], "medium": [], "complex": [], "review": []}) == "medium"


class TestParseHappyPath:
    def test_single_task(self, write_tasks, minimal_config):
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 Implement feature in `src/api/foo.py`\n"
        )
        result = parse_tasks_md(str(p), minimal_config)
        assert len(result["tasks"]) == 1
        t = result["tasks"][0]
        assert t["id"] == "T001"
        assert t["required_skill"] == "api"
        assert t["file_paths"] == ["src/api/foo.py"]
        assert t["phase"] == "Setup"

    def test_parallel_flag_detected(self, write_tasks, minimal_config):
        p = write_tasks(
            "## Setup Phase\n"
            "- [ ] T001 [P] Add config in `src/api/foo.py`\n"
        )
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
            "## User Story 1 (P1)\n"
            "- [ ] T001 [US1] Implement thing in `src/api/a.py`\n"
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
            "## Setup Phase\n"
            "- [ ] T001 Implement a in `src/api/a.py` (depends on T999)\n"
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
        p = write_tasks(
            "## Advanced Planning Notes\n"
            "- [ ] T001 Implement a in `src/api/a.py`\n"
        )
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
