#!/usr/bin/env bash
# Verify a wheel/zip archive contains the spec-kit extension manifest, commands/, and templates/.
# Usage: verify-zip-assets.sh <archive-path>
#   The archive may have files at the root or under 'share/spec-kit-schedule/...' (setuptools data-files).
set -euo pipefail

archive="${1:?archive path required}"

if [ ! -f "$archive" ]; then
  echo "::error::archive not found: $archive"
  exit 2
fi

contents=$(unzip -l "$archive")
echo "$contents"

missing=()
echo "$contents" | grep -E "(^|/)(share/spec-kit-schedule/)?extension\.yml" >/dev/null || missing+=("extension.yml")
echo "$contents" | grep -E "(^|/)(share/spec-kit-schedule/)?commands/" >/dev/null || missing+=("commands/")
echo "$contents" | grep -E "(^|/)(share/spec-kit-schedule/)?templates/" >/dev/null || missing+=("templates/")

if [ "${#missing[@]}" -ne 0 ]; then
  echo "::error::archive is missing required artifacts: ${missing[*]}"
  exit 1
fi
echo "archive packaging OK: $archive"
