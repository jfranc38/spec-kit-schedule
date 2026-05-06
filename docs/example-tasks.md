<!--
Canonical sample tasks.md.
For the full format spec (phase headers, annotations, skill
inference, complexity bucketing), see docs/tasks-format.md.
-->

# Tasks — Create Taskify

## Setup Phase

- [ ] T001 [P] Configure project scaffolding in `package.json`
- [ ] T002 [P] Create environment configuration in `.env`
- [ ] T003 Setup database connection pool in `src/config/db.ts`

## Foundational Phase

- [ ] T004 Design database schema and migrations in `src/models/schema.sql`
- [ ] T005 Create base model types and interfaces in `src/models/types.ts`
- [ ] T006 [P] Write unit tests for model validation in `tests/models/validation.test.ts`
- [ ] T007 Implement shared middleware stack in `src/middleware/index.ts`

## User Story 1 — Project Management (P1)

**Goal**: Users can create, view, and manage projects.

- [ ] T008 [US1] Implement Project model in `src/models/project.ts` (depends on T005)
- [ ] T009 [US1] Write contract tests for Project API in `tests/api/project.test.ts` (depends on T008)
- [ ] T010 [US1] Implement Project CRUD service in `src/services/project.ts` (depends on T008)
- [ ] T011 [US1] Create Project API endpoints in `src/api/projects.ts` (depends on T010)
- [ ] T012 [US1] [P] Build ProjectList component in `src/components/ProjectList.tsx` (depends on T011)
- [ ] T013 [US1] [P] Build ProjectCard component in `src/components/ProjectCard.tsx` (depends on T011)

## User Story 2 — Task Board (P1)

**Goal**: Users can view and interact with Kanban task boards.

- [ ] T014 [US2] Implement Task model in `src/models/task.ts` (depends on T005)
- [ ] T015 [US2] Write contract tests for Task API in `tests/api/task.test.ts` (depends on T014)
- [ ] T016 [US2] Implement Task CRUD service in `src/services/task.ts` (depends on T014)
- [ ] T017 [US2] Implement drag-and-drop state manager in `src/services/board.ts` (depends on T016)
- [ ] T018 [US2] Create Task API endpoints in `src/api/tasks.ts` (depends on T016)
- [ ] T019 [US2] [P] Build KanbanBoard component in `src/components/KanbanBoard.tsx` (depends on T017, T018)
- [ ] T020 [US2] [P] Build TaskCard component in `src/components/TaskCard.tsx` (depends on T018)

## User Story 3 — Comments (P2)

**Goal**: Users can add, edit, and delete comments on tasks.

- [ ] T021 [US3] Implement Comment model in `src/models/comment.ts` (depends on T014)
- [ ] T022 [US3] Write contract tests for Comment API in `tests/api/comment.test.ts` (depends on T021)
- [ ] T023 [US3] Implement Comment service in `src/services/comment.ts` (depends on T021)
- [ ] T024 [US3] Create Comment API endpoints in `src/api/comments.ts` (depends on T023)
- [ ] T025 [US3] [P] Build CommentThread component in `src/components/CommentThread.tsx` (depends on T024)

## Polish Phase

- [ ] T026 Review API contracts and update documentation in `docs/api-spec.json`
- [ ] T027 Write integration test suite in `tests/integration/full-flow.test.ts`
- [ ] T028 Optimize database queries and add indexes in `src/models/schema.sql`
