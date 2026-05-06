#!/usr/bin/env python3
"""spec-kit-schedule: tasks.md Parser.

Parses a spec-kit tasks.md file into the JSON graph format expected by
scheduler.py. Handles both the core tasks.md format and the Explicit
Task Dependencies preset format.

Usage:
    python parse_tasks.py <tasks.md> <schedule-config.yml> [--verbose]
        > solver_input.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Allow `python solver/parse_tasks.py ...` as well as `python -m solver.parse_tasks`.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "solver"  # noqa: A001

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs by default

from .config_schema import Config
from .defaults import (
    COMPLEXITY_VERBS,
    CONTEXT_BUDGET_KTOKENS_DEFAULT,
    KAPPA_DEFAULT,
    SPEED_FACTOR_DEFAULT,
    STORY_PRIORITY_DEFAULT,
    TOKEN_ESTIMATES,
)
from .i18n import t
from .i18n_catalog import WARN_PARALLEL_WRITE_CONFLICT
from .validation import (
    ScheduleInputError,
    find_cycle,
    normalize_path,
)
from .warnings_collector import WarningCollector

__all__ = ["EdgeOrigin", "parse_tasks_md", "main"]

log = logging.getLogger(__name__)


class EdgeOrigin:
    """String constants tagging why an edge was inserted.

    Kept as simple class attributes (not StrEnum) so the values serialise
    cleanly in error messages and future JSON output without `.value`.
    """

    EXPLICIT = "explicit"
    PHASE = "phase"
    SAME_FILE = "same-file"
    TDD = "tdd"


# ───────────────────────────────────────────────────────────────────────
# Regex patterns for task line parsing
# ───────────────────────────────────────────────────────────────────────

# Core format:  - [ ] T### [P] [USn] <action> in <path>
# Extended:     - [ ] T### [P] [USn] <action> in <path> (depends on T###, T###)
TASK_RE = re.compile(
    r"^-\s+\[[ xX]?\]\s+"
    r"(?P<id>T\d{3,4})\s+"
    r"(?:\[P\]\s+)?"
    r"(?:\[(?P<story>US\d+)\]\s+)?"
    r"(?P<desc>.+?)"
    r"(?:\s+in\s+`(?P<path>[^`]+)`)?"
    r"(?:\s+\(depends\s+on\s+(?P<deps>[^)]+)\))?"
    r"\s*$"
)

PARALLEL_RE = re.compile(r"\[P\]")

# Inline skill annotation: ``(skill: <name>)``. Lowercase identifier matches the
# existing skill-naming convention used in skill_rules / agent skills lists.
# When present, this overrides the auto-inferred skill from file paths so users
# can be explicit when the action_verb + path heuristic guesses wrong.
EXPLICIT_SKILL_RE = re.compile(r"\(skill:\s*([a-z][a-z0-9_-]*)\s*\)")

# Phase headers — keywords anchored to heading body (after optional
# "Phase N:" / "N." prefix). Matching the whole heading avoids
# "Advanced Setup Instructions" being read as a Setup phase.
_PHASE_PREFIX = r"#{1,4}\s+(?:Phase\s+\d+[:.\-]?\s+|\d+[.)]\s+)?"
PHASE_SETUP_RE = re.compile(rf"^{_PHASE_PREFIX}(?:Setup|Environment|Configuration)\b", re.I)
PHASE_FOUND_RE = re.compile(rf"^{_PHASE_PREFIX}(?:Foundation|Foundational|Core|Base)\b", re.I)
PHASE_IMPL_RE = re.compile(
    rf"^{_PHASE_PREFIX}(?:Implementation|Implement|Build|Development|Develop)\b", re.I
)
PHASE_STORY_RE = re.compile(rf"^{_PHASE_PREFIX}(?:User\s+Story|US)\s*(\d+)\b", re.I)
PHASE_POLISH_RE = re.compile(rf"^{_PHASE_PREFIX}(?:Polish|Cleanup|Final|Integration)\b", re.I)

PRIORITY_RE = re.compile(r"\(P(\d+)\)")
PATH_IN_BACKTICKS_RE = re.compile(r"`([^`]*(?:\.[\w]+|/[\w]+))`")
VERB_RE = re.compile(r"^(?:T\d{3,4}\s+(?:\[P\]\s+)?(?:\[US\d+\]\s+)?)?(\w+)", re.I)

# Action verbs that denote a write on the target file. Used to spot
# parallel-flag misuse (two [P] tasks writing the same file).
_WRITE_VERBS = {
    "implement",
    "create",
    "write",
    "build",
    "refactor",
    "add",
    "update",
    "design",
    "architect",
    "integrate",
    "migrate",
    "optimize",
}


def _lower_verbs(verbs_map: dict[str, list[str]]) -> dict[str, list[str]]:
    return {k: [v.lower() for v in vs] for k, vs in verbs_map.items()}


def infer_skill(
    file_paths: list[str],
    rules: list[dict[str, object]],
    default: str,
) -> str:
    """Return the required skill using longest-pattern-match precedence.

    Longest pattern wins when multiple rules match the same path, so a
    specific marker like `test_` beats a broad prefix like `src/`. Ties
    fall back to the order in the config so user intent still holds.
    """
    best_match: tuple[int, int, str] | None = None
    for fp in file_paths:
        for rank, rule in enumerate(rules):
            pattern = rule.get("pattern", "")
            skill = rule.get("skill")
            if not isinstance(pattern, str) or not isinstance(skill, str):
                continue
            if not pattern or not skill:
                continue
            if pattern in fp:
                candidate = (len(pattern), -rank, skill)
                if best_match is None or candidate > best_match:
                    best_match = candidate
    return best_match[2] if best_match else default


def classify_complexity(
    verb: str,
    verbs_map: dict[str, list[str]],
) -> str:
    verb_lower = verb.lower()
    for complexity, verb_list in verbs_map.items():
        if verb_lower in verb_list:
            return complexity
    return "medium"


def _detect_phase(line: str) -> tuple[str, str | None, int] | None:
    """Return (phase, story_id, priority) or None if line is not a header."""
    if PHASE_SETUP_RE.match(line):
        return ("Setup", None, STORY_PRIORITY_DEFAULT)
    if PHASE_FOUND_RE.match(line):
        return ("Foundational", None, STORY_PRIORITY_DEFAULT)
    if PHASE_IMPL_RE.match(line):
        return ("Implementation", None, STORY_PRIORITY_DEFAULT)
    m = PHASE_STORY_RE.match(line)
    if m:
        num = m.group(1)
        pm = PRIORITY_RE.search(line)
        priority = int(pm.group(1)) if pm else STORY_PRIORITY_DEFAULT
        return (f"User Story {num}", f"US{num}", priority)
    if PHASE_POLISH_RE.match(line):
        return ("Polish", None, STORY_PRIORITY_DEFAULT)
    return None


def _merge_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate config via pydantic, apply defaults, and return a plain dict.

    The pydantic Config model is the single source of validation truth.
    After validation, we convert back to a dict so the rest of the parser
    can continue using plain dict accessors unchanged.
    """
    # Apply legacy defaults that may be absent in minimal user configs
    # before handing off to pydantic, so pydantic field defaults layer on top.
    raw = dict(config)
    raw.setdefault("token_estimates", dict(TOKEN_ESTIMATES))
    raw.setdefault("complexity_verbs", COMPLEXITY_VERBS)

    validated: Config = Config.model_validate(raw)
    cfg = validated.model_dump(mode="python")

    # Normalise token_estimates to plain dictionaries so downstream code can
    # preserve both the deterministic mean and stochastic std_dev.
    from .config_schema import TokenEstimate  # local import; module already loaded

    te: dict[str, object] = {}
    for k, v in (cfg.get("token_estimates") or {}).items():
        if isinstance(v, dict) and "mean" in v:
            te[k] = {
                "mean": int(v["mean"]),
                "std_dev": int(v.get("std_dev", 0)),
            }
        elif isinstance(v, TokenEstimate):
            te[k] = {
                "mean": v.mean,
                "std_dev": v.std_dev,
            }
        else:
            te[k] = {
                "mean": int(v),
                "std_dev": 0,
            }
    cfg["token_estimates"] = te

    # Flatten solver sub-dict so existing consumers can do cfg["solver"]["time_limit"].
    if isinstance(cfg.get("solver"), dict):
        pass  # already a dict after model_dump
    else:
        cfg["solver"] = {}

    return cfg


def parse_tasks_md(
    tasks_path: str,
    config: dict[str, Any],
    warnings: WarningCollector | None = None,
) -> dict[str, Any]:
    """Parse tasks.md and config into solver-ready JSON.

    Raises ScheduleInputError on duplicate task ids, unknown dependency
    references, or cycles in the resulting DAG. The parser is strict
    on purpose: silent skips have been a recurring source of invisible
    schedule bugs.
    """
    warnings = warnings or WarningCollector()
    cfg = _merge_config(config)

    skill_rules = cfg["skill_rules"]
    default_skill = cfg["default_skill"]
    token_est = cfg["token_estimates"]
    complexity_verbs = _lower_verbs(cfg["complexity_verbs"])

    text = Path(tasks_path).read_text(encoding="utf-8")
    lines = text.splitlines()

    tasks: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    current_phase = "Setup"
    current_story_id: str | None = None
    current_priority = STORY_PRIORITY_DEFAULT

    for line_num, line in enumerate(lines, start=1):
        phase_hit = _detect_phase(line)
        if phase_hit is not None:
            current_phase, current_story_id, current_priority = phase_hit
            log.debug(
                "line %d: phase → %s (story=%s, pri=%d)",
                line_num,
                current_phase,
                current_story_id,
                current_priority,
            )
            continue

        m = TASK_RE.match(line)
        if not m:
            continue

        task_id = m.group("id")
        if task_id in task_ids:
            raise ScheduleInputError(t("duplicate_task_id", task_id=task_id, line=line_num))
        task_ids.add(task_id)

        story = m.group("story") or current_story_id
        desc = m.group("desc").strip()
        explicit_path = m.group("path")
        deps_str = m.group("deps")
        parallel = bool(PARALLEL_RE.search(line))

        # Pull any inline ``(skill: <name>)`` annotation BEFORE the rest of the
        # description-driven extraction. The first match wins on multi-annotated
        # lines (deliberately rare; documented as undefined behaviour). The
        # annotation is stripped from ``desc`` so it doesn't leak into verb
        # detection or backtick-path scanning.
        explicit_skill: str | None = None
        skill_match = EXPLICIT_SKILL_RE.search(desc)
        if skill_match is not None:
            explicit_skill = skill_match.group(1)
            desc = (desc[: skill_match.start()] + desc[skill_match.end() :]).strip()

        # File paths: explicit + any backticked paths in the description.
        raw_paths: list[str] = []
        if explicit_path:
            raw_paths.append(explicit_path)
        raw_paths.extend(PATH_IN_BACKTICKS_RE.findall(desc))
        seen: set[str] = set()
        file_paths: list[str] = []
        for fp in raw_paths:
            normalized = normalize_path(fp)
            if normalized not in seen:
                seen.add(normalized)
                file_paths.append(normalized)

        inferred_skill = infer_skill(file_paths, skill_rules, default_skill)
        skill = explicit_skill if explicit_skill is not None else inferred_skill

        vm = VERB_RE.match(desc)
        verb = vm.group(1) if vm else "implement"
        complexity = classify_complexity(verb, complexity_verbs)
        estimate = token_est.get(
            complexity,
            token_est.get("medium", {"mean": TOKEN_ESTIMATES["medium"], "std_dev": 0}),
        )
        if isinstance(estimate, dict):
            tokens = int(estimate["mean"])
            token_std_dev = int(estimate.get("std_dev", 0))
        else:
            tokens = int(estimate)
            token_std_dev = 0

        explicit_deps: list[str] = []
        if deps_str:
            explicit_deps = [d.strip() for d in deps_str.split(",") if d.strip().startswith("T")]

        tasks.append(
            {
                "id": task_id,
                "phase": current_phase,
                "story_id": story,
                "story_priority": current_priority,
                "parallel_flag": parallel,
                "file_paths": file_paths,
                "required_skill": skill,
                "estimated_tokens": tokens,
                "token_std_dev": token_std_dev,
                "action_verb": verb,
                "explicit_deps": explicit_deps,
                "description": desc,
                "source_line": line_num,
            }
        )

    if not tasks:
        raise ScheduleInputError(t("no_tasks_found", path=tasks_path))

    log.info("parsed %d tasks from %s", len(tasks), tasks_path)

    # ── Build edges ───────────────────────────────────────────────────
    id_to_idx = {td["id"]: i for i, td in enumerate(tasks)}
    edges: list[list[str]] = []
    edge_set: set[tuple[int, int]] = set()
    edge_origins: dict[tuple[int, int], str] = {}

    def add_edge(src_idx: int, dst_idx: int, origin: str) -> None:
        if src_idx == dst_idx:
            return
        key = (src_idx, dst_idx)
        if key in edge_set:
            return
        edges.append([tasks[src_idx]["id"], tasks[dst_idx]["id"]])
        edge_set.add(key)
        edge_origins[key] = origin

    # (a) Explicit dependencies — fail hard on unknown references.
    missing_deps: list[tuple[str, str, int]] = []
    for i, td in enumerate(tasks):
        for dep_id in td["explicit_deps"]:
            if dep_id not in id_to_idx:
                missing_deps.append((td["id"], dep_id, td["source_line"]))
                continue
            add_edge(id_to_idx[dep_id], i, EdgeOrigin.EXPLICIT)
    if missing_deps:
        details = "; ".join(
            t("unresolved_dep", task_id=tid, line=ln, dep=dep) for tid, dep, ln in missing_deps
        )
        raise ScheduleInputError(t("unresolved_deps_summary", details=details))

    # (b) Phase ordering: last task of phase N → first task of phase N+1.
    def _user_story_index(phase_name: str) -> int:
        match = re.search(r"\d+", phase_name)
        if match is None:
            return 0
        return int(match.group())

    story_phases = sorted(
        {td["phase"] for td in tasks if td["phase"].startswith("User Story")},
        key=_user_story_index,
    )
    phase_order = ["Setup", "Foundational", *story_phases, "Polish"]

    phase_tasks: dict[str, list[int]] = defaultdict(list)
    for i, td in enumerate(tasks):
        phase_tasks[td["phase"]].append(i)

    for phase in phase_order:
        if phase not in phase_tasks:
            continue
        idxs = phase_tasks[phase]
        if phase == "Foundational" and "Setup" in phase_tasks:
            add_edge(phase_tasks["Setup"][-1], idxs[0], EdgeOrigin.PHASE)
        elif phase in story_phases and "Foundational" in phase_tasks:
            add_edge(phase_tasks["Foundational"][-1], idxs[0], EdgeOrigin.PHASE)
        elif phase == "Polish":
            for sp in story_phases:
                if sp in phase_tasks:
                    add_edge(phase_tasks[sp][-1], idxs[0], EdgeOrigin.PHASE)
            if story_phases == [] and "Foundational" in phase_tasks:
                add_edge(phase_tasks["Foundational"][-1], idxs[0], EdgeOrigin.PHASE)

    # (c) Same-file write order within a story scope.
    story_file_writers: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, td in enumerate(tasks):
        if td["parallel_flag"]:
            continue
        for fp in td["file_paths"]:
            key = (td["story_id"] or td["phase"], fp)
            story_file_writers[key].append(i)

    for writers in story_file_writers.values():
        for k in range(len(writers) - 1):
            add_edge(writers[k], writers[k + 1], EdgeOrigin.SAME_FILE)

    # (d) TDD rule: index tasks by (story, file, is_test) so the join is
    # O(n) instead of O(n²) for large projects with many test+impl pairs.
    test_idx: dict[tuple[str | None, str], list[int]] = defaultdict(list)
    impl_idx: dict[tuple[str | None, str], list[int]] = defaultdict(list)
    for i, td in enumerate(tasks):
        bucket = test_idx if td["required_skill"] == "test" else impl_idx
        for fp in td["file_paths"]:
            bucket[(td["story_id"], fp)].append(i)
    for key, test_tasks in test_idx.items():
        for impl in impl_idx.get(key, ()):
            for test in test_tasks:
                add_edge(test, impl, EdgeOrigin.TDD)

    # ── Cycle check ───────────────────────────────────────────────────
    cycle = find_cycle(len(tasks), edge_set)
    if cycle is not None:
        names = " → ".join(tasks[i]["id"] for i in cycle)
        origins = [edge_origins.get((a, b), "?") for a, b in zip(cycle, cycle[1:], strict=False)]
        raise ScheduleInputError(t("cycle_detected", names=names, origins=origins))

    # ── Parallel-flag sanity: two [P] tasks writing the same file ─────
    parallel_writers: dict[str, list[int]] = defaultdict(list)
    for i, td in enumerate(tasks):
        if not td["parallel_flag"] or td["action_verb"].lower() not in _WRITE_VERBS:
            continue
        for fp in td["file_paths"]:
            parallel_writers[fp].append(i)
    for fp, idxs in parallel_writers.items():
        if len(idxs) > 1:
            ids = [tasks[i]["id"] for i in idxs]
            warnings.add(
                WARN_PARALLEL_WRITE_CONFLICT,
                t(WARN_PARALLEL_WRITE_CONFLICT, file=fp, task_ids=ids),
                file=fp,
                task_ids=ids,
            )

    # ── Agents ────────────────────────────────────────────────────────
    agents_out: list[dict[str, Any]] = []
    for ac in cfg["agents"]:
        agent_dict: dict[str, Any] = {
            "id": ac["id"],
            "model": ac.get("model", "unknown"),
            "skills": list(ac["skills"]),
            "kappa": int(ac.get("kappa", KAPPA_DEFAULT)),
            "context_budget": int(ac.get("context_budget", CONTEXT_BUDGET_KTOKENS_DEFAULT) * 1000),
            "speed_factor": float(ac.get("speed_factor", SPEED_FACTOR_DEFAULT)),
            "price_per_1k_tokens": float(ac.get("price_per_1k_tokens", 0.0)),
        }
        if ac.get("provider") is not None:
            agent_dict["provider"] = ac["provider"]
        agents_out.append(agent_dict)

    if not agents_out:
        raise ScheduleInputError(t("empty_agents"))

    solver_cfg = dict(cfg["solver"])

    tasks_out: list[dict[str, Any]] = []
    for td in tasks:
        tasks_out.append(
            {
                "id": td["id"],
                "phase": td["phase"],
                "story_id": td["story_id"],
                "story_priority": td["story_priority"],
                "parallel_flag": td["parallel_flag"],
                "file_paths": td["file_paths"],
                "required_skill": td["required_skill"],
                "estimated_tokens": td["estimated_tokens"],
                "token_std_dev": td["token_std_dev"],
                "action_verb": td["action_verb"],
            }
        )

    return {
        "tasks": tasks_out,
        "edges": edges,
        "agents": agents_out,
        "config": solver_cfg,
        "warnings": warnings.as_list(),
    }


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="parse_tasks.py",
        description="Parse a tasks.md into solver-ready JSON.",
    )
    ap.add_argument("tasks_md", help="Path to tasks.md")
    ap.add_argument("config_yml", help="Path to schedule-config.yml")
    ap.add_argument("-v", "--verbose", action="store_true", help="Enable DEBUG logging")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    with open(args.config_yml, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    try:
        result = parse_tasks_md(args.tasks_md, config)
    except ScheduleInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
