# Installation — v0.5.0

`spec-kit-schedule` is distributed as a spec-kit extension and as a
[PyPI package](https://pypi.org/project/spec-kit-schedule/). Pick the flow
that matches how you received the code.

---

## 1. You have the `.zip` a teammate shared

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

After installation the pipeline stages are regular Python modules:

```bash
uv run python -m solver.parse_tasks tasks.md schedule-config.yml > in.json
uv run python -m solver.scheduler    < in.json > out.json
uv run python -m solver.visualize    out.json images/ --feature my-feature
uv run python -m solver.render_schedule out.json my-feature \
    --image-prefix images/my-feature > schedule.md
```

To wire it into spec-kit as an extension:

```bash
specify extension add --from /path/to/spec-kit-schedule
```

`extension.yml` declares `bin/install.sh` as the install hook, so
`specify extension add` runs the bootstrap automatically.

---

## 2. Install from PyPI

```bash
pip install spec-kit-schedule           # core only
pip install 'spec-kit-schedule[viz]'    # + matplotlib/pydot for PNG images
```

Or with `uv`:

```bash
uv pip install spec-kit-schedule
uv pip install 'spec-kit-schedule[viz]'
```

Entry points installed: `speckit-schedule-parse`, `speckit-schedule-solve`,
`speckit-schedule-render`.

---

## 3. You are the author / contributor

```bash
git clone <repo>
cd spec-kit-schedule
make install        # bin/install.sh via Make (dev + viz extras)
make test           # pytest
make smoke          # end-to-end pipeline check
make schedule-all   # regenerate docs/example-schedule.md + docs/images/* + docs/example-schedule.html
make package        # build dist/spec-kit-schedule.zip for teammates
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
| Python     | 3.10 – 3.13      | Enforced by `pyproject.toml`                 |
| uv         | ≥ 0.4 (recommended) | `install.sh` will install it for you      |
| ortools    | ≥ 9.9, < 10      | Core; installed transitively                 |
| PyYAML     | ≥ 6, < 7         | Core; installed transitively                 |
| networkx   | ≥ 3.1, < 4       | Core; graph algorithms                       |
| pydantic   | ≥ 2, < 3         | Core; config schema validation               |
| matplotlib | ≥ 3.7, < 4       | Optional (`viz` extra) — PNG rendering       |
| pydot      | ≥ 2, < 3         | Optional (`viz` extra) — DOT export          |
| plotly     | ≥ 5, < 6         | Optional (`viz` extra) — interactive HTML    |

`matplotlib` / `pydot` are only needed for `python -m solver.visualize`.
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
