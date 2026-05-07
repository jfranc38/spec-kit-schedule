---
description: "Self-diagnose the schedule extension's installation state. Reports five checks (extension files, hook registration, solver deps, portfolio config, run history) and distinguishes real problems from expected pre-first-run state."
---

# /speckit.schedule.status — Diagnose Installation

## Purpose

Adoption-time pain: real users (and audit agents) see
`.specify/schedule/schedule-config.yml` missing immediately after
`specify extension add schedule` and assume the extension is broken.
Reality: the portfolio is **idempotently bootstrapped** on the first
`/speckit.schedule.run`. This command tells you definitively whether
the extension is healthy, just-not-yet-bootstrapped, or actually
broken.

## What it checks

1. **Extension files installed** — does `.specify/extensions/schedule/extension.yml` exist?
2. **Hook registered** — is `after_tasks -> speckit.schedule.run` wired into `.specify/extensions.yml`?
3. **Solver deps bootstrapped** — does the encapsulated venv at `.specify/extensions/schedule/.venv/` run?
4. **Portfolio configured** — does `.specify/schedule/schedule-config.yml` exist and list agents?
5. **Run history** — how many `*-plan.json` files are under `.specify/schedule/runs/`?

For each, the command reports one of:

| Glyph | State              | Meaning                                                       |
|-------|--------------------|---------------------------------------------------------------|
| `✓`   | `ok`               | Working as expected.                                          |
| `—`   | `expected-missing` | Bootstraps automatically on first run — no action required.   |
| `✗`   | `missing`          | Real problem; user must fix.                                  |
| `⚠`   | `stale` / `unknown`| File present but unparseable, or venv refuses to launch.      |

The overall verdict is:

* **`healthy`** — every check is `ok`.
* **`first-run-pending`** — only `expected-missing` items remain (the
  quantkit case: extension installed, never run yet).
* **`needs-attention`** — at least one `missing` / `stale` / `unknown`
  item; the report lists every hint in dependency order.

## Workflow

The Python module is defensive — it works even when solver deps are
missing — so we DO NOT call `bin/check-deps.sh` first. The command
must function exactly when the rest of the extension does not.

Prefer the encapsulated venv when it exists; fall back to the system
interpreter so the command works on a brand-new install where the
venv has not yet been bootstrapped:

```bash
EXT_DIR=".specify/extensions/schedule"
if [ -x "$EXT_DIR/.venv/bin/python" ]; then
    PY="$EXT_DIR/.venv/bin/python"
else
    PY="python3"
fi
"$PY" -m solver.status
```

Surface the printed report verbatim to the user. The exit code is:

* `0` — `healthy` or `first-run-pending` (no user action required).
* `1` — `needs-attention` (real problems flagged).

If the verdict is `needs-attention`, gently offer to execute the
single most-actionable hint listed at the top of the "Address the
following item(s)" section. Do NOT auto-execute — the user may have
intentionally skipped a step (e.g. running ad-hoc against a checkout
without `specify extension add`).

## Usage

```
/speckit.schedule.status
```

Run any time. The check is read-only — no files are created or
modified, no network calls, no solver invocations.

## When to recommend it

* User reports "schedule-config.yml missing" after install — point
  them here first; the report will reframe the false alarm as
  expected pre-first-run state.
* Audit agents (quantkit conductor, spec-kit doctor) want a
  programmatic readout of installation health.
* Before opening a bug report — copy the output verbatim into the
  issue.
