# Installation — v0.6.2

`spec-kit-schedule` is distributed as a spec-kit extension. The
canonical install paths use the `specify` CLI; PyPI distribution is on
the roadmap and documented at the bottom of this file as a future
target.

---

## Prerequisites

- Python 3.10–3.12
- `uv` (recommended) — install via `pipx install uv` or `brew install uv` or
  `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- For the `specify extension add ...` flows below, install
  [spec-kit](https://github.com/github/spec-kit) first (follow spec-kit's
  own install docs).

---

## 1. Install from a tagged release (recommended)

```bash
specify extension add schedule --from https://github.com/jfranc38/spec-kit-schedule/archive/refs/tags/v0.6.2.zip
```

`specify extension add <id> --from` accepts any HTTPS URL pointing at a zip
of the extension. The URL above is GitHub's auto-generated source
archive for the `v0.6.2` tag.

The `specify` CLI does not install Python packages. **v0.6.0+ users
do not need to bootstrap manually** — the first invocation of
`/speckit.schedule.run` auto-bootstraps the encapsulated Python venv
at `.specify/extensions/schedule/.venv/` (and the portfolio config
at `.specify/schedule/schedule-config.yml`) inline.

If you'd prefer to bootstrap the venv ahead of time:

```bash
cd <your-project>
bash .specify/extensions/schedule/bin/install.sh \
    --target .specify/extensions/schedule/.venv
```

Or, for a contributor checkout, the legacy repo-root layout still works:

```bash
git clone https://github.com/jfranc38/spec-kit-schedule
cd spec-kit-schedule
uv sync --extra dev --extra viz
# OR
./bin/install.sh
```

---

## 2. Local development install

For contributors, or when you have a checkout of the repo:

```bash
git clone https://github.com/jfranc38/spec-kit-schedule
cd spec-kit-schedule
./bin/install.sh             # uv + sync (dev+viz) + smoke test
specify extension add schedule --dev .
```

`specify extension add --dev` registers the working tree directly so
edits to `commands/`, `templates/`, or `extension.yml` are picked up
without re-installing.

After installation the pipeline stages are regular Python modules:

```bash
uv run python -m solver.parse_tasks tasks.md schedule-config.yml > in.json
uv run python -m solver.scheduler    < in.json > out.json
uv run python -m solver.visualize    out.json images/ --feature my-feature
uv run python -m solver.render_schedule out.json my-feature \
    --image-prefix images/my-feature > schedule.md
```

---

## 3. Zip-sharing flow

If a teammate shared a `spec-kit-schedule.zip`:

```bash
unzip spec-kit-schedule.zip
cd spec-kit-schedule
./bin/install.sh
```

That script:

1. Installs `uv` (https://docs.astral.sh/uv/) if it's not already on your `PATH`.
2. Materialises a reproducible virtualenv from `uv.lock` with
   `uv sync --frozen --extra dev --extra viz` (includes matplotlib so the
   PNG visualiser works out of the box).
3. Runs an end-to-end smoke test against `docs/example-tasks.md` and
   fails loudly if anything is wrong.

You can also point `specify` at the unpacked directory directly:

```bash
specify extension add schedule --dev /path/to/spec-kit-schedule
```

---

## 4. Corporate / locked-down environment without `uv`

```bash
SKIP_UV=1 ./bin/install.sh
```

Falls back to `python3 -m pip install -e '.[dev]'` against the currently
active interpreter. You lose the reproducible lockfile guarantee, so
pin your dependencies explicitly if reproducibility matters.

---

## Requirements

| Tool       | Version          | Notes                                        |
|------------|------------------|----------------------------------------------|
| Python     | 3.10 – 3.12      | Enforced by `pyproject.toml`                 |
| uv         | ≥ 0.4 (recommended) | `install.sh` will install it for you      |
| ortools    | ≥ 9.9, < 10      | Core; installed transitively                 |
| PyYAML     | ≥ 6, < 7         | Core; installed transitively                 |
| networkx   | ≥ 3.1, < 4       | Core; graph algorithms                       |
| pydantic   | ≥ 2, < 3         | Core; config schema validation               |
| matplotlib | ≥ 3.7, < 4       | Optional (`viz` extra) — PNG rendering       |
| plotly     | ≥ 5, < 6         | Optional (`viz` extra) — interactive HTML    |

`matplotlib` is only needed for `python -m solver.visualize`.
`plotly` is only needed for `python -m solver.render_html`.
The Mermaid Gantt + DAG blocks in `schedule.md` render fine without them.

## Verifying the install

```bash
uv run python -m solver.parse_tasks --help
uv run python -m solver.scheduler --help
uv run python -m solver.render_schedule --help
uv run python -m solver.visualize --help
uv run python -m solver.render_html --help
make smoke
```

If `make smoke` prints `smoke OK (... lines)` the extension is ready.

---

## Future: PyPI distribution

PyPI publishing is on the roadmap but not yet active. Once the package
is published, the install will be:

```bash
pip install spec-kit-schedule           # core only
pip install 'spec-kit-schedule[viz]'    # + matplotlib/plotly for PNG and HTML images
```

Or with `uv`:

```bash
uv pip install spec-kit-schedule
uv pip install 'spec-kit-schedule[viz]'
```

The wheel will ship `extension.yml`, `commands/`, and `templates/`
under `<sys.prefix>/share/spec-kit-schedule/` so the same artifact can
register as a spec-kit extension. Until then, install via the `specify`
CLI as shown above.
