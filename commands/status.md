---
description: "Self-diagnose the schedule extension: files, hook, solver environment, optional config, run history. Distinguishes real problems from expected first-run state."
---

# /speckit.schedule.status — Diagnose the installation

## What it checks

1. **Extension files** — `.specify/extensions/schedule/extension.yml` present?
2. **Hook registered** — `after_tasks → speckit.schedule.run` in `.specify/extensions.yml`?
3. **Solver environment** — does the private venv under the extension directory run?
   (It is created automatically the first time `/speckit-schedule-run` executes.)
4. **Config** — optional; the report names the file in use (`.specify/schedule/schedule-config.yml`
   wins over the scaffolded `.specify/extensions/schedule/schedule-config.yml`) and whether it
   declares `workers` or an `agents:` portfolio.
5. **Run history** — how many plan snapshots exist under `.specify/schedule/runs/`.

Verdicts: `healthy`, `first-run-pending` (nothing wrong — the environment
bootstraps on first run), or `needs-attention` (real problems, listed with
hints in dependency order).

## Steps

Run this and surface the report verbatim (it never needs the solver
environment, so it works on a fresh install too):

```bash
EXT=".specify/extensions/schedule"; [ -x "$EXT/bin/speckit-schedule" ] || EXT="."
if [ -x "$EXT/.venv/bin/python" ]; then PY="$EXT/.venv/bin/python"; else PY="python3"; fi
(cd "$EXT" && "$PY" -m solver.status)
```

Exit code 0 = `healthy` or `first-run-pending`; 1 = `needs-attention`.
If it is 1, offer to carry out the first hint; do not act without asking.

## Usage

```
/speckit-schedule-status
```

Read-only: no files are created or changed.
