# Tasks — Replan Demo

## Setup Phase

- [ ] T001 Define API contract in `src/api/spec.yml`

## Implementation Phase

- [ ] T002 Implement core service in `src/services/core.py` (depends on T001)
- [ ] T003 Implement worker pool in `src/services/workers.py` (depends on T001)
- [ ] T004 Build CLI entry point in `src/cli.py` (depends on T002, T003)

## Polish Phase

- [ ] T005 [P] Write unit tests in `tests/test_core.py` (depends on T002)
- [ ] T006 Write integration tests in `tests/test_cli.py` (depends on T004)
