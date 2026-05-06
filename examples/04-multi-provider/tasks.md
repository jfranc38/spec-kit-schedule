# Tasks — Multi-Provider Demo (URL Shortener)

A small URL-shortener web app that exercises every layer of a typical
spec-kit project: architecture decisions, schema, backend services,
REST API, React frontend, tests, and docs. Each task is annotated with
file paths so the skill rules in `config.yml` resolve the right
required-skill (and therefore the right eligible agents).

## Setup Phase

- [ ] T001 [P] Design service architecture in `docs/ADR-001-architecture.md`
- [ ] T002 [P] Design URL-encoding scheme in `docs/ADR-002-encoding.md`
- [ ] T003 Design database schema in `src/models/schema.sql`

## Implementation Phase

- [ ] T004 Implement Link model in `src/models/link.py` (depends on T003)
- [ ] T005 Implement encoder service in `src/services/encoder.py` (depends on T002, T004)
- [ ] T006 Implement redirect service in `src/services/redirect.py` (depends on T004)
- [ ] T007 Build shortener API endpoint in `src/api/shorten.py` (depends on T005)
- [ ] T008 Build redirect API endpoint in `src/api/redirect.py` (depends on T006)
- [ ] T009 [P] Build LinkForm component in `src/components/LinkForm.tsx` (depends on T007)
- [ ] T010 [P] Build LinkList component in `src/components/LinkList.tsx` (depends on T007)

## Polish Phase

- [ ] T011 Write unit tests for encoder in `tests/test_encoder.py` (depends on T005)
- [ ] T012 Write unit tests for redirect in `tests/test_redirect.py` (depends on T006)
- [ ] T013 Write end-to-end tests in `tests/test_e2e.py` (depends on T009, T010)
- [ ] T014 Review architecture and update `docs/architecture.md` (depends on T013)
