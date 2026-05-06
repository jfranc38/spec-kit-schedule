#!/usr/bin/env bash
# Preflight check for the Python solver dependencies.
# Used by /speckit.schedule.* command files BEFORE invoking the solver.
#
# Usage:
#   bin/check-deps.sh           # core solver only
#   bin/check-deps.sh viz       # core + viz extras (matplotlib + plotly)

set -euo pipefail

mode="${1:-solver}"

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
    echo "ERROR: bin/check-deps.sh: unknown mode $mode (expected: solver | viz)" >&2
    exit 1
    ;;
esac

if ! python3 -c "import $modules" 2>/dev/null; then
  cat <<EOF >&2
ERROR: Python solver dependencies are not installed.

This extension's solver is a Python package separate from the spec-kit
command registration. Install it once with:

  cd <path-to-spec-kit-schedule-clone>
  ./bin/install.sh        # bootstraps uv + venv + deps + smoke test
  # OR
  uv sync $extras_hint    # if you already have uv

After bootstrap, re-run the /speckit.schedule.* command.
EOF
  exit 1
fi
