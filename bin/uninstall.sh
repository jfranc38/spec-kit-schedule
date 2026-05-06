#!/usr/bin/env bash
# spec-kit-schedule local-development uninstaller — reverses bin/install.sh.
#
# This is NOT a spec-kit extension lifecycle hook (the manifest schema
# does not define one). It is invoked manually by contributors and by
# teammates who used bin/install.sh to bootstrap a local environment.
# To remove the extension registration itself, use the `specify` CLI:
#   specify extension remove schedule
#
# bin/install.sh chooses one of two paths:
#   1. uv-based install (default): runs `uv sync --frozen --extra dev --extra viz`
#      which materialises a project venv at ./.venv/.
#   2. pip fallback (SKIP_UV=1): runs `python3 -m pip install -e '.[dev]'`
#      which registers an editable install in the active interpreter's
#      site-packages but does NOT create a local .venv/.
#
# This script:
#   - Detects which path was used and reverses it.
#   - Defaults to a dry-run; requires --yes to actually remove anything.
#   - Only removes ./.venv if --purge is passed AND .venv exists.
#   - Never touches the user's source files, config, or git state.

set -euo pipefail

PROG="$(basename "$0")"

usage() {
  cat <<EOF
Usage: $PROG [--yes] [--purge] [--help]

Reverse the install performed by bin/install.sh.

Options:
  --yes      Actually perform removals. Without this flag the script
             prints what it would do and exits (dry-run).
  --purge    Also remove the local ./.venv/ directory if present.
             No-op without --yes.
  --help     Show this message and exit.

Behaviour:
  - If 'spec-kit-schedule' is registered as a uv tool, it is removed
    via 'uv tool uninstall spec-kit-schedule'.
  - Otherwise the script falls back to 'pip uninstall -y spec-kit-schedule'
    using the first available interpreter (python3, then python).
  - With --purge, ./.venv (created by 'uv sync' inside install.sh) is
    deleted. Other directories are left untouched.

Exit code:
  0 on success (including a clean dry-run).
  Non-zero if a removal step fails or if no removal path is available.
EOF
}

DO_IT=0
PURGE=0

for arg in "$@"; do
  case "$arg" in
    --yes)   DO_IT=1 ;;
    --purge) PURGE=1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      printf '%s: unknown option: %s\n' "$PROG" "$arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

log()  { printf '[uninstall] %s\n' "$*"; }
warn() { printf '[uninstall] %s\n' "$*" >&2; }

# Locate the project root (parent of bin/) so --purge .venv is unambiguous.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PKG="spec-kit-schedule"

# Mode prefix used in dry-run logging.
if [ "$DO_IT" -eq 1 ]; then
  MODE="executing"
else
  MODE="would run"
fi

run() {
  # Print the command, then run it only if --yes was passed.
  log "$MODE: $*"
  if [ "$DO_IT" -eq 1 ]; then
    "$@"
  fi
}

uninstall_via_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    return 1
  fi
  # `uv tool list` prints one tool per line. Match the package name at
  # the start of a line to avoid false positives from descriptions.
  if ! uv tool list 2>/dev/null | awk 'NR>0 {print $1}' | grep -qx "$PKG"; then
    return 1
  fi
  log "found uv tool registration for $PKG"
  run uv tool uninstall "$PKG"
  return 0
}

uninstall_via_pip() {
  PYTHON=""
  if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
  else
    warn "no python interpreter on PATH; cannot run pip uninstall"
    return 1
  fi
  # Quietly check whether pip knows about the package before invoking
  # uninstall, so we exit cleanly on systems where it was never installed.
  if ! "$PYTHON" -m pip show "$PKG" >/dev/null 2>&1; then
    log "$PKG is not registered with $($PYTHON --version 2>&1)"
    return 1
  fi
  log "found pip registration for $PKG via $($PYTHON --version 2>&1)"
  run "$PYTHON" -m pip uninstall -y "$PKG"
  return 0
}

removed_something=0

if uninstall_via_uv; then
  removed_something=1
fi

if [ "$removed_something" -eq 0 ]; then
  if uninstall_via_pip; then
    removed_something=1
  fi
fi

if [ "$PURGE" -eq 1 ]; then
  VENV_DIR="$ROOT/.venv"
  if [ -d "$VENV_DIR" ]; then
    log "$MODE: rm -rf $VENV_DIR"
    if [ "$DO_IT" -eq 1 ]; then
      rm -rf "$VENV_DIR"
    fi
    removed_something=1
  else
    log "no .venv directory at $VENV_DIR; nothing to purge"
  fi
fi

if [ "$removed_something" -eq 0 ]; then
  warn "nothing to remove: $PKG is not registered via uv or pip, and --purge was not requested"
  warn "if you used a different installation method, remove it manually"
  exit 1
fi

if [ "$DO_IT" -eq 0 ]; then
  log "dry-run complete; re-run with --yes to apply"
fi

exit 0
