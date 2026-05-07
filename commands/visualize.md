---
description: "Render the solved schedule as a static Gantt chart and dependency DAG (PNG/SVG) and embed them into schedule.md alongside the inline Mermaid diagrams."
---

# /speckit.schedule.visualize — Render Schedule Visualization

## Purpose

Emit publication-grade PNG/SVG charts alongside the Mermaid diagrams already
embedded in `schedule.md`. Use this when the consumer (slides, printed
reports, IDEs without Mermaid) cannot render the inline diagrams, or when
the team wants a static artifact pinned to a release.

## Pre-flight: verify Python solver + visualization dependencies

The visualizer needs both the core solver stack and the `viz` extras
(matplotlib + plotly). v0.6.0+ probes the encapsulated venv first
(`.specify/extensions/schedule/.venv/bin/python`) and falls back to
the legacy `uv run` and system `python3` paths. Run the shared
preflight script in `viz` mode:

```bash
.specify/extensions/schedule/bin/check-deps.sh viz
```

If the script exits non-zero, surface its stderr message to the user
verbatim and STOP. If the check succeeds, proceed with the workflow.

## Outputs

1. **`schedule-dag.png`** — Dependency DAG with agent colouring and critical
   path highlighted (thick red edges, red-bordered nodes).
2. **`schedule-gantt.png`** — Horizontal Gantt grouped by agent with
   critical bars outlined in red.
3. **Updated `schedule.md`** — Image references injected next to the
   Mermaid blocks so both views are available in the same document.
4. **`schedule.html`** (optional) — Fully self-contained interactive HTML
   with Plotly Gantt and DAG; no server required.
   ```bash
   python -m solver.render_html out.json <feature> > schedule.html
   # offline/air-gapped: embed the ~4 MB Plotly bundle
   python -m solver.render_html out.json <feature> --inline-plotly > schedule.html
   ```

Mermaid Gantt + DAG remain in `schedule.md` for GitHub / web rendering;
this command complements them, it does not replace them.

## Requirements

The static images require the `viz` extra:

```bash
uv sync --extra viz          # or: pip install 'spec-kit-schedule[viz]'
```

This adds `matplotlib` and `pydot`. Without them, `/speckit.schedule.run`
still works but the images are not produced.

## Workflow

Use the encapsulated Python interpreter so dependencies resolve from
the extension's own venv:

```bash
PY=".specify/extensions/schedule/.venv/bin/python"
```

1. Run the solver and capture its JSON output (typically already produced
   by `/speckit.schedule.run`).
2. Invoke the visualiser against that JSON:
   ```bash
   "$PY" -m solver.visualize solver_output.json <outdir> --feature <name>
   ```
   `<outdir>` is created if missing; outputs are `<feature>-dag.<format>`
   and `<feature>-gantt.<format>` where `--format` defaults to `png`.
3. Re-render the markdown with the `--image-prefix` flag so both PNGs are
   referenced inline:
   ```bash
   "$PY" -m solver.render_schedule solver_output.json <feature> \
       --image-prefix images/<feature> > schedule.md
   ```

## Critical-Path Highlight

Both images highlight the schedule's critical chain (the
makespan-driving sequence computed via `networkx` longest-path on the
realised schedule, including same-agent and file-mutex resource arcs).
Bars on the chain have a red outline in the Gantt; nodes on the chain
have a red border and thick red arrows in the DAG.

## Usage

```
/speckit.schedule.visualize
```

Or, directly:

```bash
make schedule-all     # regenerate docs/example-schedule.md + images/* + example-schedule.html
```
