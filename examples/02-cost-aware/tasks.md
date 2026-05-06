# Tasks — Cost-Aware Demo

## Setup Phase

- [ ] T001 [P] Configure linter in `.eslintrc.json`
- [ ] T002 Define database schema in `src/models/schema.sql`

## Implementation Phase

- [ ] T003 Implement core service in `src/services/core.py` (depends on T002)
- [ ] T004 Build CRUD API in `src/api/crud.py` (depends on T003)
- [ ] T005 [P] Build admin dashboard in `src/components/AdminDashboard.tsx` (depends on T004)

## Polish Phase

- [ ] T006 Write integration tests in `tests/test_integration.py` (depends on T004)
- [ ] T007 Review architecture and update `docs/architecture.md` (depends on T005)
- [ ] T008 Optimize database indexes in `src/models/schema.sql` (depends on T006)
