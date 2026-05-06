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
#     does not install for them.
#
# What it does:
# - Ensures `uv` is available (installs to ~/.local/bin if missing).
# - Creates an isolated environment via `uv sync`, using the committed
#   `uv.lock` so every teammate ends up with the same dependency graph.
# - Falls back to `pip install -e .` if the user explicitly opts out of
#   uv with `SKIP_UV=1` (e.g., locked-down corporate machines).
# - Runs a smoke test against the bundled example to prove the pipeline
#   works end-to-end before declaring success.

set -euo pipefail

here() { cd "$(dirname "$0")/.." && pwd; }
ROOT="$(here)"
cd "$ROOT"

log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[install]\033[0m %s\n' "$*" >&2; exit 1; }

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
  "${RUNNER[@]}" python -m solver.parse_tasks docs/example-tasks.md docs/example-config.yml > "$tmp/in.json"
  "${RUNNER[@]}" python -m solver.scheduler < "$tmp/in.json" > "$tmp/out.json"
  "${RUNNER[@]}" python -m solver.render_schedule "$tmp/out.json" "example" > "$tmp/schedule.md"
  grep -q "Status: \*\*OPTIMAL\*\*" "$tmp/schedule.md" \
    || die "smoke test failed: OPTIMAL status not found in rendered schedule.md"
  log "smoke test OK ($(wc -l <"$tmp/schedule.md") lines generated)"
}

if [[ "${SKIP_UV:-}" == "1" ]]; then
  log "SKIP_UV=1: using pip directly (not recommended for shared environments)"
  python3 -m pip install -e '.[dev]'
  RUNNER=()
else
  ensure_uv
  log "syncing dependencies with uv (lockfile: uv.lock) ..."
  uv sync --frozen --extra dev --extra viz
  RUNNER=(uv run --)
fi

smoke_test

log "done. Try:"
log "  uv run python -m solver.parse_tasks docs/example-tasks.md docs/example-config.yml | \\"
log "    uv run python -m solver.scheduler | \\"
log "    uv run python -m solver.render_schedule /dev/stdin my-feature"
