# Tasks: Team Notes

**Input**: Design documents from `/specs/004-team-notes/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Tests were requested in the feature specification (TDD).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each user story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Single project**: `src/`, `tests/` at repository root

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [ ] T001 Create project structure per implementation plan
- [ ] T002 Initialize Python project with FastAPI dependencies in pyproject.toml
- [ ] T003 [P] Configure linting and formatting tools in ruff.toml

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T004 Setup database schema and migrations framework in src/db/base.py
- [ ] T005 [P] Implement authentication middleware in src/middleware/auth.py
- [ ] T006 [P] Setup API routing and middleware structure in src/api/router.py
- [ ] T007 Create base models/entities that all stories depend on in src/models/base.py
- [ ] T008 Configure error handling and logging infrastructure in src/core/logging.py

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Create and list notes (Priority: P1) 🎯 MVP

**Goal**: Users can create a note and see their notes.

**Independent Test**: Create a note via the API and list it back.

### Tests for User Story 1 (OPTIONAL - only if tests requested) ⚠️

> **NOTE: Write these tests FIRST, ensure they FAIL before implementation**

- [ ] T009 [P] [US1] Contract test for POST /notes in tests/contract/test_notes_post.py
- [ ] T010 [P] [US1] Integration test for note creation journey in tests/integration/test_create_note.py

### Implementation for User Story 1

- [ ] T011 [P] [US1] Create Note model in src/models/note.py
- [ ] T012 [P] [US1] Create Tag model in src/models/tag.py
- [ ] T013 [US1] Implement NoteService in src/services/note_service.py (depends on T011, T012)
- [ ] T014 [US1] Implement POST /notes and GET /notes endpoints in src/api/notes.py
- [ ] T015 [US1] Add validation and error handling in src/api/notes.py
- [ ] T016 [US1] Add logging for user story 1 operations

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Share a note with a teammate (Priority: P2)

**Goal**: Users can share a note with another user.

**Independent Test**: Share a note and read it as the recipient.

### Tests for User Story 2 (OPTIONAL - only if tests requested) ⚠️

- [ ] T017 [P] [US2] Contract test for POST /notes/{id}/share in tests/contract/test_notes_share.py

### Implementation for User Story 2

- [ ] T018 [P] [US2] Create Share model in src/models/share.py
- [ ] T019 [US2] Implement ShareService in src/services/share_service.py
- [ ] T020 [US2] Implement POST /notes/{id}/share endpoint in src/api/notes.py
- [ ] T021 [US2] Integrate with User Story 1 components (if needed)

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T022 [P] Documentation updates in docs/
- [ ] T023 Code cleanup and refactoring
- [ ] T024 [P] Additional unit tests in tests/unit/test_models.py
- [ ] T025 Run quickstart.md validation

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3+)**: All depend on Foundational phase completion
- **Polish (Final Phase)**: Depends on all desired user stories being complete

### Parallel Opportunities

- All Setup tasks marked [P] can run in parallel
- Once Foundational phase completes, all user stories can start in parallel

---

## Parallel Example: User Story 1

```bash
# Launch all tests for User Story 1 together:
Task: "Contract test for POST /notes in tests/contract/test_notes_post.py"
Task: "Integration test for note creation journey in tests/integration/test_create_note.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Test User Story 1 independently

## Notes

- [P] tasks = different files, no dependencies
- Commit after each task or logical group
