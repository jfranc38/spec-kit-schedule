# Contributing to spec-kit-schedule

Thank you for your interest in contributing. This document explains how to get
set up, run tests, and submit changes.

## Prerequisites

- Python 3.10–3.12
- [`uv`](https://github.com/astral-sh/uv) (recommended) or pip

## Development setup

```bash
git clone https://github.com/jfranc38/spec-kit-schedule.git
cd spec-kit-schedule
make install          # creates .venv and installs all dev + viz extras
```

`make install` is equivalent to:

```bash
uv sync --extra dev --extra viz
uv run pre-commit install
```

## Running tests

```bash
make test             # uv run pytest -q
make cov              # with coverage report (term-missing)
```

Run a single test module:

```bash
uv run pytest tests/test_scheduler.py -q
```

## Linting and type-checking

```bash
make lint             # uv run ruff check solver tests
make fmt              # uv run ruff check --fix solver tests (autofixes lint issues)
make typecheck        # uv run mypy solver (non-blocking; CI runs --strict on selected modules)
```

All three must be clean before opening a pull request.

## Pre-commit hooks

Pre-commit hooks run automatically on `git commit` after `make install`.
To run all hooks manually:

```bash
uv run pre-commit run --all-files
```

Hooks include: `ruff` (lint + format), end-of-file fixer, trailing-whitespace,
YAML/TOML validation, and large-file guard.

## Adding a new test

1. Find the relevant test module under `tests/`.
2. Add your test class or function (follow existing patterns).
3. Run `uv run pytest tests/<module>.py -q` to verify it passes.
4. Ensure `uv run pytest -q` still reports 100% green.

Coverage of new modules must be ≥90% (`make cov` shows the report).

## Adding a new error or warning message

1. Add a key to `solver/i18n_catalog.py` with `en` and `es` translations.
2. Use `t("your_key", **kwargs)` at the call-site in `solver/`.
3. Add a test in `tests/test_i18n.py` (happy path + catalog completeness).

## Submitting a pull request

1. Fork the repository and create a feature branch.
2. Make your changes; run `make lint fmt typecheck test`.
3. Open a PR against `master`; fill in the PR template.
4. CI will run all checks; address any failures before requesting review.

## Releases

The release workflow (`.github/workflows/release.yml`) triggers on tags
matching `v*.*.*`. It:
- Verifies the tag matches `pyproject.toml` and `extension.yml` versions.
- Builds the extension zip via `git archive`.
- Verifies the zip contains `extension.yml`, `commands/`, `templates/`.
- Generates Sigstore attestations.
- Creates a GitHub Release with the zip attached.

PyPI distribution is on the roadmap but not yet active.

To cut a release:
1. Bump version in `pyproject.toml`, `extension.yml`, `solver/__init__.py`.
2. Update `CHANGELOG.md` with the new version + date.
3. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. The release workflow runs automatically.

## Code of conduct

This project follows the [Contributor Covenant 2.1](CODE_OF_CONDUCT.md).
