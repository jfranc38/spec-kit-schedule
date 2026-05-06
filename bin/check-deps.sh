#!/usr/bin/env bash
# Preflight check for the Python solver dependencies.
# Used by /speckit.schedule.* command files BEFORE invoking the solver.
# Probes the SAME Python the slash commands invoke (uv venv when present),
# avoiding false positives from system-Python deps that aren't in the
# project venv.
#
# Usage:
#   bin/check-deps.sh           # core solver only
#   bin/check-deps.sh viz       # core + viz extras (matplotlib + plotly)

set -euo pipefail

mode="${1:-solver}"

# Resolve repo root from script location (bin/check-deps.sh -> repo root).
script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

case "$mode" in
  solver)
    modules='solver, ortools, networkx, pydantic, yaml'
    extras_hint='--extra dev'
    ;;
  viz)
    modules='solver, ortools, networkx, pydantic, yaml, matplotlib, plotly'
    extras_hint='--extra dev --extra viz'
    ;;
  *)
    echo "ERROR: bin/check-deps.sh: unknown mode '$mode' (expected: solver | viz)" >&2
    exit 1
    ;;
esac

# Pick the right Python:
# - If uv is available AND we have a uv.lock, use 'uv run --no-sync python'
#   (matches how slash commands invoke the solver via 'uv run python -m
#   solver.scheduler'). --no-sync is critical: without it, uv would auto-
#   install missing deps and the preflight would be meaningless.
# - Otherwise fall back to system python3 (allows non-uv install paths,
#   e.g. user installed via pip into their own venv).
if command -v uv >/dev/null 2>&1 && [ -f "$repo_root/uv.lock" ]; then
  probe_cmd=(uv run --no-sync --project "$repo_root" python -c)
else
  probe_cmd=(python3 -c)
fi

if ! "${probe_cmd[@]}" "import $modules" >/dev/null 2>&1; then
  cat <<EOF >&2
ERROR: Python solver dependencies are not installed.

This extension's solver is a Python package separate from the spec-kit
command registration. Install it once with:

  cd $repo_root
  ./bin/install.sh        # bootstraps uv + venv + deps + smoke test
  # OR
  uv sync $extras_hint    # if you already have uv

After bootstrap, re-run the /speckit.schedule.* command.
EOF
  exit 1
fi
