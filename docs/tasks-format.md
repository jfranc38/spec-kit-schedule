# tasks.md format reference

The parser (`solver/parse_tasks.py`) reads the `tasks.md` that
`/speckit.tasks` generates (spec-kit 0.16 format) and also accepts the
older backticked-path style. This page is the authoritative description
of what it extracts and how the dependency graph is built.

For a worked example see [`tests/fixtures/tasks-speckit-0.16.md`](../tests/fixtures/tasks-speckit-0.16.md)
or [`docs/example-tasks.md`](example-tasks.md).

---

## Task lines

```
- [ ] T012 [P] [US1] Create User model in src/models/user.py (depends on T005, T006)
- [x] T013 [US1] Implement UserService in src/services/user_service.py (skill: backend)
```

- Checkbox `- [ ]` / `- [x]` / `- [X]`. The state is recorded as `done`
  and drives `python -m solver next`; the plan itself covers every task.
- Id `T###` (3–4 digits). Duplicates are an error.
- Tags `[P]` and `[USn]` in either order. `[P]` = parallel-safe (see
  below). `[USn]` overrides the story inherited from the phase header.
- Description: free text. The first word is the **action verb** used for
  effort estimation.
- Annotations, anywhere after the description, any order:
  `(depends on T###, T###)` explicit predecessors (unknown ids are an
  error); `(skill: name)` explicit skill (advanced portfolios).

### File paths

Paths are read from the description whether or not they are backticked:

| Recognised | Not a path |
|------------|------------|
| `src/models/user.py`, `tests/contract/test_x.py`, `frontend/src/App.tsx` | `Node.js`, `Next.js`, `Vue.js` … |
| `package.json`, `ruff.toml`, `README.md` (known extension) | `1.2.3`, `v2.0` (versions) |
| `docs/`, `backend/src/` (directory — keeps its trailing slash) | `https://…` (URLs) |
| `Makefile`, `Dockerfile`, `.env`, `.gitignore` (canonical names) | `models/entities`, `and/or` (prose slashes — no extension, no preposition) |
| `in vault.kv` — unknown extension after a preposition (`in`, `to`, `at`, `from`, `on`, …) | `e.g.`, `i.e.`, `etc.` |

Paths are normalised (`./src/a.py` = `src/a.py`) and de-duplicated.

---

## Phase headers

```
## Phase 1: Setup (Shared Infrastructure)
## Phase 2: Foundational (Blocking Prerequisites)
## Phase 3: User Story 1 - Title (Priority: P1) 🎯 MVP
### Tests for User Story 1 (OPTIONAL …)
### Implementation for User Story 1
## Phase 5: Polish & Cross-Cutting Concerns
```

| Keywords (case-insensitive, optional `Phase N:` / `N.` prefix) | Phase |
|----------------------------------------------------------------|-------|
| Setup, Environment, Configuration | `Setup` |
| Foundation, Foundational, Core, Base | `Foundational` |
| User Story N, USN | `User Story N` (story `USN`, priority from `(P1)` or `(Priority: P1)`) |
| Polish, Cleanup, Final, Integration | `Polish` |
| Implementation, Implement, Build, Development, Develop | `Implementation` (display bucket outside the phase chain) |

`### Tests for User Story N` / `### Implementation for User Story N`
sub-sections keep the story context. Any header **deeper** than the
current user-story header is treated as a sub-section too. Headers that
match nothing (`## Dependencies & Execution Order`, `## Notes`) leave
the phase unchanged; prose, tables and code blocks are ignored.

---

## Dependency graph

Edges are added in this order; the origin is kept for diagnostics.

1. **explicit** — `(depends on …)`.
2. **same-file** — within one story (or one phase for Setup /
   Foundational / Polish), tasks that are not `[P]` and mention the same
   file are ordered by declaration.
3. **tdd** — within the same scope, a test task (skill `test`) precedes
   an implementation task on the same file; and inside a user story,
   test tasks declared *before* an implementation task precede it
   (spec-kit's "write tests FIRST"). Declaration order is never reversed.
4. **Cycle resolution.** A cycle that contains a same-file or tdd edge
   is broken by dropping those edges (warning
   `heuristic_edge_dropped`); the explicit order wins. A cycle made only
   of explicit edges is an error.
5. **phase** — complete barriers: every task of Setup precedes every
   task of Foundational; Foundational precedes every user story; every
   user story precedes Polish (stories stay independent of each other).
   Implemented as sinks → sources of adjacent phases, which is
   equivalent after transitive closure.
6. A cycle at this point (an explicit note pointing backwards across
   phases) is an error naming the cycle and the edge origins.

`[P]` excludes a task from the same-file rule and from the solver's
file mutex. Two `[P]` tasks that both write the same file produce the
`parallel_write_conflict` warning.

---

## Skills and effort

- **Zero-config** (no `agents:` block): the canonical
  `DEFAULT_SKILL_RULES` tag test files as `test` (that is all the TDD
  rules need); every lane accepts every skill.
- **Advanced portfolio**: `skill_rules` (longest path-fragment match,
  ties by order, `default_skill` fallback) route tasks to agents whose
  `skills` contain the tag; `(skill: name)` overrides.
- **Effort**: the action verb maps to a bucket (`simple`, `medium`,
  `complex`, `review`; unknown → `medium`) and `token_estimates` gives
  the mean (and optional `std_dev`). Durations are
  `ceil(tokens / token_unit / speed_factor)` time units.

---

## Errors and warnings

| Code | Meaning |
|------|---------|
| `duplicate_task_id` | Two tasks share an id. |
| `unresolved_deps_summary` | `(depends on T###)` names an unknown id. |
| `cycle_detected` | Explicit/phase edges form a cycle (names the cycle and origins). |
| `no_tasks_found` | No `- [ ] T###` lines. |
| `heuristic_edge_dropped` (warning) | A same-file / tdd edge yielded to an explicit note. |
| `parallel_write_conflict` (warning) | Two `[P]` tasks write the same file. |
| `workers_raised` (warning) | `max_tasks_per_worker` was too small; lanes were added. |
