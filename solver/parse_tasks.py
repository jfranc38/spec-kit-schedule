#!/usr/bin/env python3
"""spec-kit-schedule: tasks.md Parser.

Parses a spec-kit tasks.md file into the JSON graph format expected by
scheduler.py. Accepts the format ``/speckit.tasks`` generates today
(``- [ ] T012 [P] [US1] Create User model in src/models/user.py`` — bare
file paths, ``(Priority: P1)`` story headers, ``### Implementation for
User Story N`` sub-sections) as well as the backticked-path / ``(depends
on T###)`` / ``(skill: name)`` annotations documented in
``docs/tasks-format.md``.

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

import yaml  # type: ignore[import-untyped, unused-ignore]  # PyYAML ships no type stubs by default

from .config_schema import validate_config
from .defaults import (
    COMPLEXITY_VERBS,
    CONTEXT_BUDGET_KTOKENS_DEFAULT,
    DEFAULT_SKILL_RULES,
    KAPPA_DEFAULT,
    SPEED_FACTOR_DEFAULT,
    STORY_PRIORITY_DEFAULT,
    TASK_ID_PATTERN,
    TOKEN_ESTIMATES,
    ZERO_CONFIG_TIME_LIMIT_SECONDS,
    zero_config_num_workers,
)
from .i18n import t
from .i18n_catalog import (
    WARN_HEURISTIC_EDGE_DROPPED,
    WARN_PARALLEL_WRITE_CONFLICT,
    WARN_UNCLOSED_FENCE,
)
from .tasks_md import unfenced_lines
from .validation import (
    ScheduleInputError,
    find_cycle,
    normalize_path,
)
from .warnings_collector import WarningCollector
from .workers import synthesize_workers

__all__ = ["EdgeOrigin", "extract_file_paths", "parse_tasks_md", "main"]

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


# Heuristic origins may be dropped to break a cycle; explicit and phase
# edges encode user intent / spec-kit semantics and are never dropped.
_HEURISTIC_ORIGINS = frozenset({EdgeOrigin.SAME_FILE, EdgeOrigin.TDD})


# ───────────────────────────────────────────────────────────────────────
# Regex patterns for task line parsing
# ───────────────────────────────────────────────────────────────────────

# spec-kit format:  - [ ] T### [P?] [USn?] <description with file path>
# Tags may appear in either order ([P] [US1] or [US1] [P]).
TASK_RE = re.compile(
    r"^-\s+\[(?P<check>[ xX]?)\]\s+"
    rf"(?P<id>{TASK_ID_PATTERN})\b"
    r"(?P<tags>(?:\s+\[(?:P|US\d+)\])*)"
    r"\s+(?P<desc>.+?)\s*$"
)
_STORY_TAG_RE = re.compile(r"\[(US\d+)\]")
_PARALLEL_TAG_RE = re.compile(r"\[P\]")

# Trailing annotations, accepted in any order anywhere after the description.
DEPENDS_RE = re.compile(r"\(\s*depends\s+on\s+(?P<deps>[^)]+)\)", re.I)

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
PHASE_STORY_RE = re.compile(rf"^{_PHASE_PREFIX}(?:User\s+Story|US)\s*(?P<num>\d+)\b", re.I)
PHASE_POLISH_RE = re.compile(rf"^{_PHASE_PREFIX}(?:Polish|Cleanup|Final|Integration)\b", re.I)

_HEADER_RE = re.compile(r"^(?P<hashes>#{1,6})\s+\S")
_MULTISPACE_RE = re.compile(r"\s{2,}")

# ``(P1)`` (docs/example-tasks.md) and ``(Priority: P1)`` (spec-kit template).
PRIORITY_RE = re.compile(r"\((?:Priority:\s*)?P(\d+)\)", re.I)

# Backticked path-like tokens: contain an extension or a slash.
PATH_IN_BACKTICKS_RE = re.compile(r"`([^`]*(?:\.[\w]+|/[\w]+))`")
_BACKTICK_SPAN_RE = re.compile(r"`[^`]*`")
VERB_RE = re.compile(r"^(\w+)")

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


# ───────────────────────────────────────────────────────────────────────
# Bare (un-backticked) file path extraction
# ───────────────────────────────────────────────────────────────────────

_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.I)
_WIN_PATH_RE = re.compile(r"^[\w.\-@+~]+(?:\\[\w.\-@+~]+)+$")
_VERSION_RE = re.compile(r"^v?\d+(?:\.\d+)+$")
# ``name.ext`` — name may itself contain dots (``user_service.test.ts``).
_BARE_FILE_RE = re.compile(r"^[\w.\-@+~]+\.[A-Za-z][A-Za-z0-9]{0,9}$")
# Anything with a slash that looks like a relative path (``src/api/x.py``,
# ``docs/``, ``frontend/src/[component].tsx``).
_BARE_DIRPATH_RE = re.compile(r"^\.?[\w.\-@+~\[\]{}]+(?:/[\w.\-@+~\[\]{}*]*)+$")
_STRIP_LEADING = "([{<\"'"
_STRIP_TRAILING = ".,;:!?)]}>\"'"
_STRIP_CHARS = _STRIP_LEADING + _STRIP_TRAILING
# Words that commonly precede a file path in a task description.
_PATH_PREPOSITIONS = frozenset({"in", "into", "to", "at", "under", "from", "on", "within"})
# Dotted tokens that are not files (lower-cased comparison).
_NOT_PATHS = frozenset(
    {
        "e.g",
        "i.e",
        "node.js",
        "next.js",
        "nuxt.js",
        "vue.js",
        "react.js",
        "express.js",
        "nest.js",
        "three.js",
        "d3.js",
        "angular.js",
        "ember.js",
        "alpine.js",
        "chart.js",
        "moment.js",
        "socket.io",
        "asp.net",
    }
)
# Extensions accepted even when no preposition precedes the token.
_KNOWN_EXTS = frozenset(
    {
        "py", "pyi", "js", "mjs", "cjs", "ts", "tsx", "jsx", "json", "yml", "yaml",
        "toml", "ini", "cfg", "env", "md", "rst", "txt", "sql", "css", "scss", "sass",
        "less", "html", "htm", "sh", "bash", "zsh", "go", "rs", "java", "kt", "kts",
        "rb", "php", "cs", "cpp", "cc", "c", "h", "hpp", "swift", "m", "mm", "xml",
        "lock", "proto", "graphql", "gql", "vue", "svelte", "tf", "conf", "properties",
        "gradle", "csproj", "sln", "ex", "exs", "erl", "hs", "scala", "clj", "lua",
        "pl", "r", "jl", "dart", "ipynb", "csv", "sqlite", "db", "tsv",
    }
)  # fmt: skip
# Extension-less canonical filenames.
_CANONICAL_FILES = frozenset(
    {
        "Makefile", "Dockerfile", "Procfile", "Rakefile", "Gemfile", "Jenkinsfile",
        "Vagrantfile", "LICENSE", "README", "CHANGELOG", "CODEOWNERS",
        ".gitignore", ".env", ".dockerignore", ".editorconfig", ".gitattributes",
    }
)  # fmt: skip


def _bare_path_candidate(token: str, prev_word: str) -> str | None:
    """Return the path a whitespace-delimited *token* denotes, or ``None``."""
    tok = token.lstrip(_STRIP_LEADING)
    # Keep a trailing "/" (directory); strip sentence punctuation otherwise.
    keep_slash = tok.endswith("/")
    tok = tok.rstrip(_STRIP_TRAILING)
    if keep_slash and not tok.endswith("/"):
        tok = tok + "/"
    # Hand-written Windows separators (``src\models\user.py``).
    if "\\" in tok and "/" not in tok and _WIN_PATH_RE.match(tok):
        tok = tok.replace("\\", "/")
    if not tok or tok in ("/", "./", "../") or _URL_RE.match(tok):
        return None
    if tok in _CANONICAL_FILES:
        return tok
    if "/" in tok:
        if not _BARE_DIRPATH_RE.match(tok):
            return None
        # Prose also uses slashes ("models/entities", "and/or"): require a
        # file extension, a directory marker, nesting, or a preposition.
        last = tok.rsplit("/", 1)[1]
        looks_like_file = "." in last and _BARE_FILE_RE.match(last) is not None
        if (
            looks_like_file
            or tok.endswith("/")
            or tok.count("/") >= 2
            or prev_word in _PATH_PREPOSITIONS
        ):
            return tok
        return None
    if not _BARE_FILE_RE.match(tok) or _VERSION_RE.match(tok):
        return None
    if tok.lower() in _NOT_PATHS:
        return None
    ext = tok.rsplit(".", 1)[1].lower()
    if ext in _KNOWN_EXTS or prev_word in _PATH_PREPOSITIONS:
        return tok
    return None


def extract_file_paths(text: str) -> list[str]:
    """Return normalised, de-duplicated file paths mentioned in *text*.

    Backticked tokens with an extension or slash are always paths. Bare
    tokens are paths when they contain a slash, are a canonical filename
    (``Makefile``), or look like ``name.ext`` with a known source
    extension or a preceding preposition (``in``, ``to``, …). URLs,
    version numbers and framework names (``Node.js``) are excluded.
    Directory tokens keep their trailing slash so ``skill_rules``
    patterns like ``docs/`` still match.
    """
    raw: list[str] = PATH_IN_BACKTICKS_RE.findall(text)
    words = _BACKTICK_SPAN_RE.sub(" ", text).split()
    prev = ""
    for word in words:
        candidate = _bare_path_candidate(word, prev)
        if candidate is not None:
            raw.append(candidate)
        prev = word.lower().strip(_STRIP_CHARS)

    seen: set[str] = set()
    out: list[str] = []
    for fp in raw:
        normalized = normalize_path(fp)
        if fp.endswith("/") and not normalized.endswith("/"):
            normalized += "/"
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


# ───────────────────────────────────────────────────────────────────────
# Config, skills, complexity
# ───────────────────────────────────────────────────────────────────────


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
    # Zero-config (no ``agents:`` block) gets the canonical skill rules so
    # the TDD ordering rule can recognise test files. Portfolios that
    # declare agents keep an empty default: their skill vocabulary is
    # whatever the agents list, and surprising skills would fail preflight.
    if not raw.get("agents"):
        raw.setdefault("skill_rules", list(DEFAULT_SKILL_RULES))

    validated = validate_config(raw)
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

    if not isinstance(cfg.get("solver"), dict):
        cfg["solver"] = {}

    # Zero-config solves favour a fast answer: anytime mode with a short
    # limit (the warm-start incumbent is always there) and threads bounded
    # by the machine. Only fill what the user left unset.
    if validated.workers_mode:
        raw_solver = config.get("solver") or {}
        zero_config_defaults: dict[str, object] = {
            "time_limit": ZERO_CONFIG_TIME_LIMIT_SECONDS,
            "anytime": True,
            "num_workers": zero_config_num_workers(),
        }
        for key, value in zero_config_defaults.items():
            if key not in raw_solver:
                cfg["solver"][key] = value

    return cfg


# ───────────────────────────────────────────────────────────────────────
# Phase / header detection
# ───────────────────────────────────────────────────────────────────────


def _detect_phase(line: str) -> tuple[str, str | None, int] | None:
    """Return ``(phase, story_id, priority)`` or None if not a phase header."""
    if PHASE_SETUP_RE.match(line):
        return ("Setup", None, STORY_PRIORITY_DEFAULT)
    if PHASE_FOUND_RE.match(line):
        return ("Foundational", None, STORY_PRIORITY_DEFAULT)
    if PHASE_IMPL_RE.match(line):
        return ("Implementation", None, STORY_PRIORITY_DEFAULT)
    m = PHASE_STORY_RE.match(line)
    if m:
        num = m.group("num")
        pm = PRIORITY_RE.search(line)
        priority = int(pm.group(1)) if pm else STORY_PRIORITY_DEFAULT
        return (f"User Story {num}", f"US{num}", priority)
    if PHASE_POLISH_RE.match(line):
        return ("Polish", None, STORY_PRIORITY_DEFAULT)
    return None


class _PhaseTracker:
    """Track the current phase while scanning header lines top to bottom."""

    def __init__(self) -> None:
        self.phase = "Setup"
        self.story_id: str | None = None
        self.priority = STORY_PRIORITY_DEFAULT
        self.depth = 0

    def observe(self, line: str, line_num: int) -> bool:
        """Update state if *line* is a header. Returns True when it was one."""
        header = _HEADER_RE.match(line)
        if header is None:
            return False
        depth = len(header.group("hashes"))
        hit = _detect_phase(line)
        if hit is None:
            return True
        # Deeper headers under a user story are sub-sections ("### Tests",
        # "### Implementation"): they must not change the story context.
        if self.story_id is not None and depth > self.depth:
            log.debug("line %d: sub-section under %s ignored", line_num, self.phase)
            return True
        self.phase, self.story_id, self.priority = hit
        self.depth = depth
        log.debug(
            "line %d: phase → %s (story=%s, pri=%d)",
            line_num,
            self.phase,
            self.story_id,
            self.priority,
        )
        return True


# ───────────────────────────────────────────────────────────────────────
# Edge construction helpers
# ───────────────────────────────────────────────────────────────────────


# Edges in insertion order, each with the origin that first produced it.
_Edges = dict[tuple[int, int], str]


def _add_edge(edges: _Edges, src: int, dst: int, origin: str) -> None:
    if src != dst:
        edges.setdefault((src, dst), origin)


def _break_heuristic_cycles(
    edges: _Edges,
    tasks: list[dict[str, Any]],
    warnings: WarningCollector,
) -> None:
    """Drop same-file / TDD edges that close a cycle; explicit-only cycles are fatal."""
    while True:
        cycle = find_cycle(len(tasks), list(edges))
        if cycle is None:
            return
        pairs = list(zip(cycle, cycle[1:], strict=False))
        names = " → ".join(tasks[i]["id"] for i in cycle)
        heuristic = [p for p in pairs if edges.get(p) in _HEURISTIC_ORIGINS]
        if not heuristic:
            origins = [edges.get(p, "?") for p in pairs]
            raise ScheduleInputError(t("cycle_detected", names=names, origins=origins))
        # One edge per pass (the loop re-checks); prefer the one running
        # against declaration order so tasks.md keeps its stated order.
        backward = sorted(p for p in heuristic if p[0] > p[1])
        src, dst = backward[0] if backward else min(heuristic)
        origin = edges.pop((src, dst))
        warnings.add(
            WARN_HEURISTIC_EDGE_DROPPED,
            t(
                WARN_HEURISTIC_EDGE_DROPPED,
                origin=origin,
                src=tasks[src]["id"],
                dst=tasks[dst]["id"],
                names=names,
            ),
            origin=origin,
            src=tasks[src]["id"],
            dst=tasks[dst]["id"],
        )


def _phase_boundary(
    members: list[int],
    edges: _Edges,
) -> tuple[list[int], list[int]]:
    """Return ``(sources, sinks)`` of a phase w.r.t. its intra-phase edges."""
    member_set = set(members)
    has_pred = dict.fromkeys(members, False)
    has_succ = dict.fromkeys(members, False)
    for src, dst in edges:
        if src in member_set and dst in member_set:
            has_succ[src] = True
            has_pred[dst] = True
    sources = [i for i in members if not has_pred[i]]
    sinks = [i for i in members if not has_succ[i]]
    return sources, sinks


def _add_phase_barriers(
    tasks: list[dict[str, Any]],
    edges: _Edges,
) -> None:
    """Every task of phase N precedes every task of phase N+1 (transitively).

    Chain: ``Setup → Foundational → {each User Story} → Polish``. User
    stories hang off the last present prerequisite phase and are not
    ordered among themselves; Polish waits for every story (or for the
    prerequisite chain when there are no stories). ``Implementation``
    is a display bucket outside the chain — order it with explicit
    ``(depends on …)`` annotations.
    """

    def _story_index(phase_name: str) -> int:
        match = re.search(r"\d+", phase_name)
        return int(match.group()) if match else 0

    phase_tasks: dict[str, list[int]] = defaultdict(list)
    for i, td in enumerate(tasks):
        phase_tasks[td["phase"]].append(i)

    story_phases = sorted(
        (p for p in phase_tasks if p.startswith("User Story")), key=_story_index
    )
    boundary = {p: _phase_boundary(idxs, edges) for p, idxs in phase_tasks.items()}

    def _barrier(before: str, after: str) -> None:
        for src in boundary[before][1]:
            for dst in boundary[after][0]:
                _add_edge(edges, src, dst, EdgeOrigin.PHASE)

    prereq_chain = [p for p in ("Setup", "Foundational") if p in phase_tasks]
    for before, after in zip(prereq_chain, prereq_chain[1:], strict=False):
        _barrier(before, after)
    last_prereq = prereq_chain[-1] if prereq_chain else None

    for story in story_phases:
        if last_prereq is not None:
            _barrier(last_prereq, story)

    if "Polish" in phase_tasks:
        if story_phases:
            for story in story_phases:
                _barrier(story, "Polish")
        elif last_prereq is not None:
            _barrier(last_prereq, "Polish")


# ───────────────────────────────────────────────────────────────────────
# Main parser
# ───────────────────────────────────────────────────────────────────────


def _parse_task_line(
    m: re.Match[str],
    line_num: int,
    tracker: _PhaseTracker,
) -> dict[str, Any]:
    """Build the raw task record for one matched checklist line."""
    tags = m.group("tags")
    story_tag = _STORY_TAG_RE.search(tags)
    desc = m.group("desc").strip()

    explicit_deps: list[str] = []
    dep_match = DEPENDS_RE.search(desc)
    if dep_match is not None:
        explicit_deps = [
            d.strip() for d in dep_match.group("deps").split(",") if d.strip().startswith("T")
        ]
        desc = (desc[: dep_match.start()] + desc[dep_match.end() :]).strip()

    # ``(skill: <name>)`` — first match wins on multi-annotated lines
    # (deliberately rare; documented as undefined behaviour). Stripped so
    # it does not leak into verb detection or path scanning.
    explicit_skill: str | None = None
    skill_match = EXPLICIT_SKILL_RE.search(desc)
    if skill_match is not None:
        explicit_skill = skill_match.group(1)
        desc = (desc[: skill_match.start()] + desc[skill_match.end() :]).strip()
    desc = _MULTISPACE_RE.sub(" ", desc)

    vm = VERB_RE.match(desc)
    verb = vm.group(1) if vm else "implement"

    return {
        "id": m.group("id"),
        "done": m.group("check").lower() == "x",
        "phase": tracker.phase,
        "story_id": story_tag.group(1) if story_tag else tracker.story_id,
        "story_priority": tracker.priority,
        "parallel_flag": _PARALLEL_TAG_RE.search(tags) is not None,
        "file_paths": extract_file_paths(desc),
        "explicit_skill": explicit_skill,
        "explicit_deps": explicit_deps,
        "action_verb": verb,
        "description": desc,
        "source_line": line_num,
    }


def parse_tasks_md(
    tasks_path: str,
    config: dict[str, Any],
    warnings: WarningCollector | None = None,
) -> dict[str, Any]:
    """Parse tasks.md and config into solver-ready JSON.

    Raises ScheduleInputError on duplicate task ids, unknown dependency
    references, or cycles made of explicit / phase edges. Cycles that
    involve a heuristic edge (same-file order, TDD rule) are broken by
    dropping that edge with a warning — the user's explicit ``depends
    on`` order wins. The parser is otherwise strict on purpose: silent
    skips have been a recurring source of invisible schedule bugs.
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
    tracker = _PhaseTracker()
    # Fenced code (spec-kit's "Parallel Example") may contain "# Setup …"
    # comments or task-like lines — never headers or tasks.
    kept, unclosed = unfenced_lines(lines)
    if unclosed is not None:
        warnings.add(WARN_UNCLOSED_FENCE, t(WARN_UNCLOSED_FENCE, line=unclosed), line=unclosed)

    for line_num, line in kept:
        if tracker.observe(line, line_num):
            continue
        m = TASK_RE.match(line)
        if not m:
            continue

        td = _parse_task_line(m, line_num, tracker)
        if td["id"] in task_ids:
            raise ScheduleInputError(t("duplicate_task_id", task_id=td["id"], line=line_num))
        task_ids.add(td["id"])

        inferred_skill = infer_skill(td["file_paths"], skill_rules, default_skill)
        td["required_skill"] = td["explicit_skill"] or inferred_skill

        complexity = classify_complexity(td["action_verb"], complexity_verbs)
        # ``_merge_config`` normalised every estimate to ``{mean, std_dev}``.
        estimate: dict[str, int] = (
            token_est.get(complexity)
            or token_est.get("medium")
            or {"mean": TOKEN_ESTIMATES["medium"], "std_dev": 0}
        )
        td["estimated_tokens"] = int(estimate["mean"])
        td["token_std_dev"] = int(estimate["std_dev"])
        tasks.append(td)

    if not tasks:
        raise ScheduleInputError(t("no_tasks_found", path=tasks_path))

    log.info("parsed %d tasks from %s", len(tasks), tasks_path)

    # ── Build edges ───────────────────────────────────────────────────
    id_to_idx = {td["id"]: i for i, td in enumerate(tasks)}
    edges: _Edges = {}

    # (a) Explicit dependencies — fail hard on unknown references.
    missing_deps: list[tuple[str, str, int]] = []
    for i, td in enumerate(tasks):
        for dep_id in td["explicit_deps"]:
            if dep_id not in id_to_idx:
                missing_deps.append((td["id"], dep_id, td["source_line"]))
                continue
            _add_edge(edges, id_to_idx[dep_id], i, EdgeOrigin.EXPLICIT)
    if missing_deps:
        details = "; ".join(
            t("unresolved_dep", task_id=tid, line=ln, dep=dep) for tid, dep, ln in missing_deps
        )
        raise ScheduleInputError(t("unresolved_deps_summary", details=details))

    # (b) Same-file write order within a story / phase scope.
    scope_file_writers: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, td in enumerate(tasks):
        if td["parallel_flag"]:
            continue
        for fp in td["file_paths"]:
            scope_file_writers[(td["story_id"] or td["phase"], fp)].append(i)
    for writers in scope_file_writers.values():
        for k in range(len(writers) - 1):
            _add_edge(edges, writers[k], writers[k + 1], EdgeOrigin.SAME_FILE)

    # (c) TDD rule within the same scope: test tasks precede implementation
    # tasks that touch the same file. Indexed so the join is O(n).
    test_idx: dict[tuple[str, str], list[int]] = defaultdict(list)
    impl_idx: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, td in enumerate(tasks):
        bucket = test_idx if td["required_skill"] == "test" else impl_idx
        for fp in td["file_paths"]:
            bucket[(td["story_id"] or td["phase"], fp)].append(i)
    for key, test_tasks in test_idx.items():
        for impl in impl_idx.get(key, ()):
            for test in test_tasks:
                _add_edge(edges, test, impl, EdgeOrigin.TDD)

    # (c') spec-kit "tests FIRST": inside a user story, test tasks declared
    # before an implementation task precede it. Declaration order is never
    # reversed, so a trailing "write unit tests" task stays where it is.
    story_tests: dict[str, list[int]] = defaultdict(list)
    for i, td in enumerate(tasks):
        if td["story_id"] is None:
            continue
        if td["required_skill"] == "test":
            story_tests[td["story_id"]].append(i)
            continue
        for test in story_tests[td["story_id"]]:
            _add_edge(edges, test, i, EdgeOrigin.TDD)

    # Heuristic edges may contradict explicit intent — resolve before the
    # phase barriers are derived from the intra-phase structure.
    _break_heuristic_cycles(edges, tasks, warnings)

    # (d) Phase barriers: Setup → Foundational → {stories} → Polish. A
    # barrier can close a cycle with a backward heuristic edge (a
    # story-tagged test task placed under Polish); drop it like any other
    # heuristic conflict and re-derive the barriers until the graph is a
    # DAG. Explicit / phase-only cycles raise inside the breaker.
    while True:
        _add_phase_barriers(tasks, edges)
        if find_cycle(len(tasks), list(edges)) is None:
            break
        _break_heuristic_cycles(edges, tasks, warnings)

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
    # Zero-config: no ``agents:`` block → N identical subagent lanes,
    # already in the output shape (budgets in raw tokens).
    agents_out: list[dict[str, Any]] = []
    if not cfg["agents"]:
        agents_out = synthesize_workers(
            len(tasks),
            sum(td["estimated_tokens"] for td in tasks),
            workers=cfg["workers"],
            max_tasks_per_worker=cfg["max_tasks_per_worker"],
            warnings=warnings,
        )
    for ac in cfg["agents"]:
        # Declared budgets are kilotokens.
        budget = int(ac.get("context_budget", CONTEXT_BUDGET_KTOKENS_DEFAULT)) * 1000
        agent_dict: dict[str, Any] = {
            "id": ac["id"],
            "model": ac.get("model", "unknown"),
            "skills": list(ac["skills"]),
            "kappa": int(ac.get("kappa", KAPPA_DEFAULT)),
            "context_budget": budget,
            "speed_factor": float(ac.get("speed_factor", SPEED_FACTOR_DEFAULT)),
            "price_per_1k_tokens": float(ac.get("price_per_1k_tokens", 0.0)),
        }
        if ac.get("provider") is not None:
            agent_dict["provider"] = ac["provider"]
        agents_out.append(agent_dict)

    solver_cfg = dict(cfg["solver"])

    tasks_out: list[dict[str, Any]] = [
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
            "description": td["description"],
            "done": td["done"],
            "source_line": td["source_line"],
        }
        for td in tasks
    ]

    return {
        "tasks": tasks_out,
        "edges": [[tasks[s]["id"], tasks[d]["id"]] for s, d in edges],
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
