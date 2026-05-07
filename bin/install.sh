#!/usr/bin/env bash
# spec-kit-schedule local-development bootstrap.
#
# This is NOT a spec-kit extension lifecycle hook — the spec-kit
# extension manifest schema does not define install/uninstall hooks,
# and `specify extension add` will not run this script. It is invoked
# manually by:
#   - contributors after `git clone` (and by `make install`),
#   - teammates who received a shared `spec-kit-schedule.zip` and need
#     to bootstrap the Python solver dependencies the `specify` CLI
#     does not install for them,
#   - the v0.6.0+ first-run auto-bootstrap path inside
#     `commands/schedule.md`, which calls
#     `bash bin/install.sh --target ./.venv` from the encapsulated
#     extension code dir (`.specify/extensions/schedule`).
#
# What it does:
# - Ensures `uv` is available (installs to ~/.local/bin if missing).
# - Creates an isolated environment via `uv sync`, using the committed
#   `uv.lock` so every teammate ends up with the same dependency graph.
# - When `--target <dir>` is passed, the venv is created at that
#   absolute or repo-relative path (used by the encapsulated layout).
# - Falls back to `pip install -e .` if the user explicitly opts out of
#   uv with `SKIP_UV=1` (e.g., locked-down corporate machines).
# - Runs a smoke test against the bundled example to prove the pipeline
#   works end-to-end before declaring success (skip with --skip-smoke).

set -euo pipefail

here() { cd "$(dirname "$0")/.." && pwd; }
ROOT="$(here)"
cd "$ROOT"

log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[install]\033[0m %s\n' "$*" >&2; exit 1; }

target_venv=""
skip_smoke=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || die "--target requires a path argument"
      target_venv="$2"
      shift 2
      ;;
    --skip-smoke)
      skip_smoke=1
      shift
      ;;
    -h|--help)
      cat <<EOF
Usage: bin/install.sh [--target <venv-dir>] [--skip-smoke]

  --target <dir>   Create the venv at <dir> (default: uv-managed .venv).
                   Use this for the encapsulated layout
                   (.specify/extensions/schedule/.venv).
  --skip-smoke     Skip the end-to-end smoke test (faster auto-bootstrap).

Environment variables:
  SKIP_UV=1        Use pip directly instead of uv (corporate locked-down).
EOF
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    log "uv already installed: $(uv --version)"
    return
  fi
  log "uv not found; installing from astral.sh ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Refresh PATH for the rest of the script.
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null 2>&1 || die "uv installation failed; install it manually (https://docs.astral.sh/uv/) and re-run."
}

smoke_test() {
  log "running smoke test against docs/example-tasks.md ..."
  local tmp
  tmp="$(mktemp -d)"
  # trap RETURN runs on function exit; the wc -l below completes first
  # because it's part of the function body, not the trap itself, so the
  # tmpdir is still present when the line count is read.
  trap 'rm -rf "$tmp"' RETURN
  "${RUNNER[@]}" -m solver.parse_tasks docs/example-tasks.md docs/example-config.yml > "$tmp/in.json"
  "${RUNNER[@]}" -m solver.scheduler < "$tmp/in.json" > "$tmp/out.json"
  "${RUNNER[@]}" -m solver.render_schedule "$tmp/out.json" "example" > "$tmp/schedule.md"
  grep -q "Status: \*\*OPTIMAL\*\*" "$tmp/schedule.md" \
    || die "smoke test failed: OPTIMAL status not found in rendered schedule.md"
  log "smoke test OK ($(wc -l <"$tmp/schedule.md") lines generated)"
}

if [[ "${SKIP_UV:-}" == "1" ]]; then
  log "SKIP_UV=1: using pip directly (not recommended for shared environments)"
  if [[ -n "$target_venv" ]]; then
    python3 -m venv "$target_venv"
    "$target_venv/bin/python" -m pip install --upgrade pip
    "$target_venv/bin/python" -m pip install -e '.[dev]'
    RUNNER=("$target_venv/bin/python")
  else
    python3 -m pip install -e '.[dev]'
    RUNNER=(python3)
  fi
else
  ensure_uv
  if [[ -n "$target_venv" ]]; then
    log "syncing dependencies with uv into $target_venv (lockfile: uv.lock) ..."
    UV_PROJECT_ENVIRONMENT="$target_venv" uv sync --frozen --extra dev --extra viz
    # Use the venv's python directly so the smoke test cannot pick up
    # a different uv-managed env (UV_PROJECT_ENVIRONMENT does not
    # auto-export, and `uv run --project` resolves its own venv).
    RUNNER=("$target_venv/bin/python")
  else
    log "syncing dependencies with uv (lockfile: uv.lock) ..."
    uv sync --frozen --extra dev --extra viz
    RUNNER=(uv run -- python)
  fi
fi

if [[ "$skip_smoke" -eq 0 ]]; then
  smoke_test
else
  log "smoke test skipped (--skip-smoke)"
fi

log "done. Try:"
log "  uv run python -m solver.parse_tasks docs/example-tasks.md docs/example-config.yml | \\"
log "    uv run python -m solver.scheduler | \\"
log "    uv run python -m solver.render_schedule /dev/stdin my-feature"
