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

# Extract just the filename column from `unzip -l`'s tabular output. This
# avoids brittle whitespace handling in the matching regex below — both the
# git-archive zip (no prefix) and the wheel (share/spec-kit-schedule/ prefix)
# round-trip cleanly through this pipeline.
echo "--- unzip -l $archive ---"
unzip -l "$archive"

filenames=$(unzip -l "$archive" | awk 'NR>3 && NF>=4 {print $NF}')

missing=()
echo "$filenames" | grep -E "(^|/)(share/spec-kit-schedule/)?extension\.yml$" >/dev/null || missing+=("extension.yml")
echo "$filenames" | grep -E "(^|/)(share/spec-kit-schedule/)?commands/" >/dev/null || missing+=("commands/")
echo "$filenames" | grep -E "(^|/)(share/spec-kit-schedule/)?templates/" >/dev/null || missing+=("templates/")

if [ "${#missing[@]}" -ne 0 ]; then
  echo "::error::archive is missing required artifacts: ${missing[*]}"
  exit 1
fi
echo "archive packaging OK: $archive"
