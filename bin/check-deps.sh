#!/usr/bin/env bash
# Preflight check for the Python solver dependencies.
# Used by /speckit.schedule.* command files BEFORE invoking the solver.
#
# v0.6.0 probe order (matches the encapsulated-state layout):
#   1. .specify/extensions/schedule/.venv/bin/python  (encapsulated venv,
#      created by `bin/install.sh --target ./.venv` from inside the
#      extension code dir).
#   2. uv run --no-sync python                         (legacy contributor
#      checkout where the venv lives at the repo root).
#   3. python3                                         (last-resort fallback
#      for pip-only environments).
#
# Caching:
#   On a successful encapsulated-venv probe we touch a per-mode
#   sentinel (`.venv/.deps-ok-<mode>`). On the next call we skip the
#   import probe when the sentinel is newer than `pyvenv.cfg` — the
#   sentinel is invalidated automatically whenever the venv is
#   recreated by `bin/install.sh`. The system-python tier has no
#   reliable invalidation key, so it always reruns the probe.
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

# Walk up from cwd to find an ancestor that contains .specify/.
project_root=""
search_dir="$(pwd)"
while [[ "$search_dir" != "/" ]]; do
  if [[ -d "$search_dir/.specify" ]]; then
    project_root="$search_dir"
    break
  fi
  search_dir="$(dirname "$search_dir")"
done

probe_cmd=()
# Path to the venv's pyvenv.cfg; populated when the encapsulated layout
# is in use so we can short-circuit the import probe via a sentinel
# file that invalidates whenever the venv is recreated.
venv_dir=""

# 1. Encapsulated venv (v0.6.0+).
if [[ -n "$project_root" ]]; then
  encapsulated_python="$project_root/.specify/extensions/schedule/.venv/bin/python"
  if [[ -x "$encapsulated_python" ]]; then
    probe_cmd=("$encapsulated_python" -c)
    venv_dir="$project_root/.specify/extensions/schedule/.venv"
  fi
fi

# 2. uv-managed venv at repo root (legacy contributor layout).
if [[ ${#probe_cmd[@]} -eq 0 ]]; then
  if command -v uv >/dev/null 2>&1 && [ -f "$repo_root/uv.lock" ]; then
    probe_cmd=(uv run --no-sync --project "$repo_root" python -c)
  fi
fi

# 3. System python3 (last resort — pip-only / corporate env).
if [[ ${#probe_cmd[@]} -eq 0 ]]; then
  probe_cmd=(python3 -c)
fi

# Per-mode sentinel name: solver and viz each verify a different module
# set, so they cannot share one sentinel.
sentinel=""
pyvenv_cfg=""
if [[ -n "$venv_dir" ]]; then
  pyvenv_cfg="$venv_dir/pyvenv.cfg"
  # Only cache when pyvenv.cfg exists — system-python tier (no venv)
  # has no reliable invalidation key and must stay on the slow path.
  if [[ -f "$pyvenv_cfg" ]]; then
    sentinel="$venv_dir/.deps-ok-$mode"
    # Cached: deps verified after the last venv update.
    if [[ -f "$sentinel" && "$sentinel" -nt "$pyvenv_cfg" ]]; then
      exit 0
    fi
  fi
fi

if ! "${probe_cmd[@]}" "import $modules" >/dev/null 2>&1; then
  cat <<EOF >&2
ERROR: Python solver dependencies are not installed.

This extension's solver is a Python package separate from the spec-kit
command registration. Bootstrap it once via the encapsulated layout
(v0.6.0+ default — keeps state inside .specify/):

  cd $repo_root
  bash bin/install.sh --target .specify/extensions/schedule/.venv

Or, for a contributor checkout:

  ./bin/install.sh        # bootstraps uv + repo-root venv + smoke test
  # OR
  uv sync $extras_hint    # if you already have uv

After bootstrap, re-run the /speckit.schedule.* command.
EOF
  exit 1
fi

# Probe succeeded — refresh the sentinel so the next /run skips the
# import cost. Best-effort: a touch failure (read-only FS, race) just
# means the next call repeats the probe.
if [[ -n "$sentinel" ]]; then
  touch "$sentinel" 2>/dev/null || true
fi
