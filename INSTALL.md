# Installation — v0.7.2

`spec-kit-schedule` is a spec-kit extension. Install it with the
`specify` CLI; the Python solver environment bootstraps itself the first
time a command runs.

## Prerequisites

- [spec-kit](https://github.com/github/spec-kit) (`specify` CLI, 0.4+)
- Python 3.10–3.12
- `uv` (recommended; the bootstrap installs it if missing). Set
  `SKIP_UV=1` to use `pip` instead in locked-down environments.

## 1. From a tagged release (recommended)

```bash
specify extension add schedule --from https://github.com/jfranc38/spec-kit-schedule/archive/refs/tags/v0.7.2.zip
```

Then, in a feature that already has a `tasks.md`:

```
/speckit-schedule-run
```

The first invocation creates `.specify/extensions/schedule/.venv/` from
the committed `uv.lock` (core solver + charting extras, about a minute)
and plans. Nothing else to configure.

To bootstrap ahead of time (e.g. in CI or an image):

```bash
bash .specify/extensions/schedule/bin/install.sh --target .specify/extensions/schedule/.venv
```

## 2. Local development checkout

```bash
git clone https://github.com/jfranc38/spec-kit-schedule
cd spec-kit-schedule
make install                       # uv + venv (dev+viz extras) + smoke test
specify extension add schedule --dev .
```

Inside the checkout the CLI works directly:

```bash
uv run python -m solver plan tests/fixtures/tasks-speckit-0.16.md --out /tmp/demo
uv run python -m solver next /tmp/demo/schedule.json tests/fixtures/tasks-speckit-0.16.md
```

## 3. Sharing a zip

`make package` produces `dist/spec-kit-schedule.zip`; a teammate runs
`specify extension add schedule --from <path-or-url>` (or unzips and
uses `--dev <dir>`).

`--dev` copies the checkout verbatim — `.venv/` and `.git/` included.
The copied interpreter no longer runs from its new location; the wrapper
notices and rebuilds the private environment on the first command
(about a minute). Delete `.venv/` before installing to keep the copy small.

## Verifying

```
/speckit-schedule-status
```

reports `healthy`, `first-run-pending` (nothing wrong — the environment
bootstraps on first run), or `needs-attention` with hints. From a
checkout, `make smoke` runs the full pipeline against the bundled
fixtures.

## Requirements

| Tool       | Version           | Notes                                   |
|------------|-------------------|-----------------------------------------|
| Python     | 3.10 – 3.12       | Enforced by `pyproject.toml`            |
| uv         | ≥ 0.4             | `install.sh` installs it if absent      |
| ortools    | ≥ 9.9, < 10       | Core (CP-SAT)                           |
| networkx   | ≥ 3.1, < 4        | Core (graphs, critical path)            |
| pydantic   | ≥ 2, < 3          | Core (config validation)                |
| PyYAML     | ≥ 6, < 7          | Core                                    |
| matplotlib | ≥ 3.7, < 4        | `viz` extra — `--images` PNGs           |
| plotly     | ≥ 5, < 7          | `viz` extra — `python -m solver.render_html` |

PyPI distribution is on the roadmap; `specify extension add` is the
supported path today.
