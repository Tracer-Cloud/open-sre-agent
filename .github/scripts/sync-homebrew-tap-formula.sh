#!/usr/bin/env bash
# Sync Tracer-Cloud/homebrew-tap Formula/opensre.rb version and archive SHAs.
#
# Usage (CI):
#   HOMEBREW_TAP_GITHUB_TOKEN=... VERSION=2026.5.29 ASSET_DIR=release-assets ./sync-homebrew-tap-formula.sh
#
# Local dry-run (no push):
#   DRY_RUN=1 VERSION=2026.5.29 ASSET_DIR=/path/to/sha256-files ./sync-homebrew-tap-formula.sh

set -euo pipefail

VERSION="${VERSION:-}"
ASSET_DIR="${ASSET_DIR:-release-assets}"
DRY_RUN="${DRY_RUN:-0}"

if [ -z "$VERSION" ]; then
  echo "VERSION is required (e.g. 2026.5.29)" >&2
  exit 1
fi

for platform in linux-x64 linux-arm64 darwin-x64 darwin-arm64; do
  sha_file="${ASSET_DIR}/opensre_${VERSION}_${platform}.tar.gz.sha256"
  if [ ! -f "$sha_file" ]; then
    echo "Missing SHA256 file: $sha_file" >&2
    exit 1
  fi
done

linux_x64_sha="$(awk '{print $1}' "${ASSET_DIR}/opensre_${VERSION}_linux-x64.tar.gz.sha256")"
linux_arm64_sha="$(awk '{print $1}' "${ASSET_DIR}/opensre_${VERSION}_linux-arm64.tar.gz.sha256")"
darwin_x64_sha="$(awk '{print $1}' "${ASSET_DIR}/opensre_${VERSION}_darwin-x64.tar.gz.sha256")"
darwin_arm64_sha="$(awk '{print $1}' "${ASSET_DIR}/opensre_${VERSION}_darwin-arm64.tar.gz.sha256")"

tap_dir="$(mktemp -d)/homebrew-tap"
if [ -n "${HOMEBREW_TAP_GITHUB_TOKEN:-}" ]; then
  git clone "https://x-access-token:${HOMEBREW_TAP_GITHUB_TOKEN}@github.com/Tracer-Cloud/homebrew-tap.git" "$tap_dir"
else
  git clone "https://github.com/Tracer-Cloud/homebrew-tap.git" "$tap_dir"
fi

python3 - "$tap_dir/Formula/opensre.rb" "$VERSION" "$darwin_arm64_sha" "$darwin_x64_sha" "$linux_arm64_sha" "$linux_x64_sha" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

formula_path = Path(sys.argv[1])
version = sys.argv[2]
darwin_arm64_sha = sys.argv[3]
darwin_x64_sha = sys.argv[4]
linux_arm64_sha = sys.argv[5]
linux_x64_sha = sys.argv[6]

text = formula_path.read_text()
text = re.sub(r'version "[^"]+"', f'version "{version}"', text, count=1)
replacements = {
    r'(opensre_#\{version\}_darwin-arm64\.tar\.gz"\n\s*sha256 ")[^"]+(")': darwin_arm64_sha,
    r'(opensre_#\{version\}_darwin-x64\.tar\.gz"\n\s*sha256 ")[^"]+(")': darwin_x64_sha,
    r'(opensre_#\{version\}_linux-arm64\.tar\.gz"\n\s*sha256 ")[^"]+(")': linux_arm64_sha,
    r'(opensre_#\{version\}_linux-x64\.tar\.gz"\n\s*sha256 ")[^"]+(")': linux_x64_sha,
}
for pattern, sha in replacements.items():
    text, count = re.subn(pattern, rf'\g<1>{sha}\g<2>', text, count=1)
    if count != 1:
        raise SystemExit(f"Failed to update checksum with pattern: {pattern}")
formula_path.write_text(text)
PY

cd "$tap_dir"
if git diff --quiet -- Formula/opensre.rb; then
  echo "Homebrew tap formula already up to date for version ${VERSION}."
  exit 0
fi

echo "=== Formula diff ==="
git diff Formula/opensre.rb
echo "===================="

if [ "$DRY_RUN" = "1" ]; then
  echo "DRY_RUN=1 — not committing or pushing."
  exit 0
fi

if [ -z "${HOMEBREW_TAP_GITHUB_TOKEN:-}" ]; then
  echo "HOMEBREW_TAP_GITHUB_TOKEN is required to push." >&2
  exit 1
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add Formula/opensre.rb
git commit -m "chore: update opensre formula to ${VERSION}"
git push origin HEAD:main
echo "Pushed homebrew-tap update for version ${VERSION}."
