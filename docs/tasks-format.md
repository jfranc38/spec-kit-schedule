# tasks.md format reference

The solver parses task specs from a Markdown file. The format
specifies a DAG of tasks across phases, each with a skill requirement,
optional precedence edges, and optional flags. This page is the
authoritative reference for what the parser at `solver/parse_tasks.py`
actually accepts.

For a worked example, see [`docs/example-tasks.md`](example-tasks.md)
or any of the four [`examples/`](../examples/) projects.

---

## File structure

```
# Tasks — <project name>

## <Phase Header>

- [ ] T### [P] [USn] <action> in `<path>` (depends on T###, T###) (skill: <name>)
- [ ] T### ...

## <Phase Header>

- [ ] ...
```

Anything that is not a recognised phase header or a task line is
ignored. You can interleave free-form prose, tables, or sub-headings
between phases without affecting parsing.

---

## Phase headers

The parser uses five regexes (in `solver/parse_tasks.py`) to detect
phase headers, all anchored to the heading line and case-insensitive:

| Regex constant     | Recognised keywords                                              | Resulting phase     |
|--------------------|------------------------------------------------------------------|---------------------|
| `PHASE_SETUP_RE`   | `Setup`, `Environment`, `Configuration`                          | `"Setup"`           |
| `PHASE_FOUND_RE`   | `Foundation`, `Foundational`, `Core`, `Base`                     | `"Foundational"`    |
| `PHASE_IMPL_RE`    | `Implementation`, `Implement`, `Build`, `Development`, `Develop` | `"Implementation"`  |
| `PHASE_STORY_RE`   | `User Story <N>`, `US<N>`                                        | `"User Story <N>"`  |
| `PHASE_POLISH_RE`  | `Polish`, `Cleanup`, `Final`, `Integration`                      | `"Polish"`          |

A heading also accepts an optional `Phase N:` or `N.` / `N)` prefix
before the keyword. Markdown depth (`##`, `###`, `####`) does not
matter; any of `#` through `####` works.

Examples that match:

```
## Setup Phase
## Setup
## Environment
### Foundational Phase
## Implementation Phase
## Build Phase
## Phase 1: Setup
## Phase 2: Implementation
## 2) User Story 1 (P1)
## User Story 3 — Comments (P2)
## US3
## Polish Phase
```

Examples that do **not** match (silently fall through to the most
recent recognised phase, or `"Setup"` if none seen yet):

```
## Testing Phase
## Tasks
## Notes
```

A user-story heading tags every task it covers with the matching
`story_id` (`USn`), used by C4 (per-story file scoping) and
`story_priority` (set by the trailing `(Pn)` if present, otherwise
`99`).

Phase ordering is a chain: the last task of one phase must finish
before the first task of the next, in the order
`Setup → Foundational → User Story 1, 2, 3, … → Polish`. Tasks under
`## Implementation Phase` carry the `"Implementation"` label for
display and grouping but are not part of the inter-phase precedence
chain — order them with explicit `(depends on T###)` edges as in the
shipped examples.

---

## Task lines

```
- [ ] T### [P] [USn] <action verb> <description> in `<path>` (depends on T###, T###) (skill: <name>)
```

Required:

- The leading bullet is `- [ ]`, `- [x]`, or `- [X]` — checkbox state
  is parsed but does not currently affect scheduling.
- `T###` (three or four digits, e.g. `T001`, `T1024`). Duplicates
  raise `ScheduleInputError`.
- An action verb followed by a description. The verb is the first
  word of the description after the optional flags.

Optional, in this order:

- `[P]` — parallel-safe flag (see below).
- `[USn]` — explicit user-story tag (overrides the inherited
  story from the phase header).
- `` in `<path>` `` — explicit file path (backtick-quoted). Any
  additional backticked tokens in the description that look like
  paths (i.e. contain `/` or end in a `.ext`) are also collected.
- `(depends on T###, T###, ...)` — explicit precedence edges.
- `(skill: <name>)` — explicit skill, overriding inference.

The parser tolerates any whitespace between fields and accepts the
annotations in either order at the end of the line, but both must
appear after the file path if a file path is present.

---

## Flags and tags

### `[P]` — parallel-safe

Excludes the task from the same-file precedence chain (C7). Use only
for tasks that genuinely do not write the same file. The parser
warns (`WARN_PARALLEL_WRITE_CONFLICT`) when two `[P]` tasks share a
file path *and* both have a write-style verb (`implement`, `create`,
`write`, `build`, `refactor`, `add`, `update`, `design`, `architect`,
`integrate`, `migrate`, `optimize`).

### `[USn]` — user-story tag

Assigns the task to user story `n` (e.g. `[US3]`). Without this tag,
a task inherits the story of its enclosing `## User Story N` phase
header, or `null` outside a story phase.

---

## Annotations

### `(depends on T###, T###, ...)`

Adds explicit precedence edges. Comma-separated; whitespace
flexible. Unknown task ids raise `ScheduleInputError`. Cycles are
detected after all edges (explicit + phase + same-file + TDD) are
inserted, with a `cycle_detected` error naming the cycle and edge
origins.

```
- [ ] T005 Implement endpoint in `src/api/users.py` (depends on T001, T002)
```

### `(skill: <name>)`

Sets the required skill explicitly, overriding the auto-inferred
value from `skill_rules`. The name must be lowercase
(`[a-z][a-z0-9_-]*`). Use this when the path-pattern heuristic
guesses wrong:

```
- [ ] T009 Update DB index in `src/models/schema.sql` (skill: backend)
```

Without the annotation, T009 would inherit whatever skill matches
`src/models/` in `skill_rules` (typically `schema`). The annotation
takes precedence and routes the task to a `backend`-skilled agent.

---

## Skill inference

When `(skill: <name>)` is **not** present, the parser infers the skill
from the task's file paths using the `skill_rules` block in
`config.yml`:

```yaml
skill_rules:
  - pattern: "tests/"
    skill: "test"
  - pattern: "src/services/"
    skill: "backend"
  - pattern: "src/components/"
    skill: "frontend"
```

Resolution rules (`infer_skill` in `solver/parse_tasks.py`):

1. Each pattern is a plain substring match against every file path
   on the task (no glob, no regex).
2. The **longest** matching pattern wins, so a specific marker
   (`test_`) beats a broad prefix (`src/`).
3. Ties on length break by config order (earlier rules win).
4. If no rule matches, `default_skill` is used (default
   `"backend"`).

The action verb is **not** part of skill inference — only file paths
are. (The verb is used separately for token-estimate complexity
bucketing.)

---

## Token estimation

The task's first action verb maps to a complexity bucket via
`complexity_verbs`, which then indexes into `token_estimates`:

| Bucket    | Default mean tokens | Default verbs (English, plus -s / -ing forms) |
|-----------|---------------------|-----------------------------------------------|
| `simple`  | 1,500               | add, update, rename, move, import, export, configure |
| `medium`  | 3,500               | implement, create, write, build, refactor    |
| `complex` | 6,000               | design, architect, integrate, migrate, optimize |
| `review`  | 2,000               | review, validate, verify, analyze, audit     |

Verbs not in any bucket fall back to `medium`. Both maps are
overridable in `config.yml`. Each `token_estimates` value can be a
plain integer or `{mean, std_dev}` for stochastic mode.

---

## Validation errors

| Error                                | Cause                                                                  |
|--------------------------------------|------------------------------------------------------------------------|
| `duplicate_task_id`                  | Two tasks share an id (e.g. two `T005` lines).                         |
| `unresolved_deps_summary`            | A `(depends on T###)` references an unknown id.                        |
| `cycle_detected`                     | The combined explicit+phase+same-file+TDD edges form a cycle.          |
| `no_tasks_found`                     | The file contains no recognisable task lines.                          |
| `WARN_PARALLEL_WRITE_CONFLICT` (warn)| Two `[P]` tasks with write verbs share a file path.                    |

---

## See also

- [`docs/example-tasks.md`](example-tasks.md) — canonical sample
- [`docs/architecture.md`](architecture.md) — full data flow
- [`docs/portfolio-design.md`](portfolio-design.md) — agent / skill
  setup
- [`docs/calibration.md`](calibration.md) — refining
  `token_estimates` and `speed_factor` from real runs
- [`solver/parse_tasks.py`](../solver/parse_tasks.py) — source of
  truth (regexes near the top of the file)
