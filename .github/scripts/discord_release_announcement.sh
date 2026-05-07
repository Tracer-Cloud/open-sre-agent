#!/usr/bin/env bash
# Posts a Discord webhook message for a GitHub release. Used by the Release and
# Release Announcements workflows. Requires curl, jq, and gh when RELEASE_BODY
# is not pre-filled.
set -euo pipefail

if [ -z "${DISCORD_WEBHOOK_URL:-}" ]; then
  echo "DISCORD_WEBHOOK_URL is not set; skipping Discord announcement."
  exit 0
fi

RELEASE_TAG="${RELEASE_TAG:?RELEASE_TAG is required}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

if [ -z "${RELEASE_BODY:-}" ] && [ -n "${GITHUB_TOKEN:-}" ]; then
  RELEASE_BODY="$(gh release view "$RELEASE_TAG" --repo "$GITHUB_REPOSITORY" --json body -q .body)"
fi
RELEASE_BODY="${RELEASE_BODY:-No release notes provided.}"

RELEASE_URL="${RELEASE_URL:-https://github.com/${GITHUB_REPOSITORY}/releases/tag/${RELEASE_TAG}}"
REPO_STARS="${REPO_STARS:-0}"
REPO_FORKS="${REPO_FORKS:-0}"

role_mention=""
if [ -n "${DISCORD_RELEASES_ROLE_ID:-}" ]; then
  role_mention="<@&${DISCORD_RELEASES_ROLE_ID}>"$'\n'
fi

NOTES="$(printf '%s' "$RELEASE_BODY" | tr -d '\r')"
MAX_NOTES_LENGTH=1700
if [ "${#NOTES}" -gt "$MAX_NOTES_LENGTH" ]; then
  NOTES="${NOTES:0:$((MAX_NOTES_LENGTH - 3))}..."
fi

payload="$(jq -n \
  --arg tag "$RELEASE_TAG" \
  --arg notes "$NOTES" \
  --arg url "$RELEASE_URL" \
  --arg stars "$REPO_STARS" \
  --arg forks "$REPO_FORKS" \
  --arg mention "$role_mention" \
  '{
    content: ($mention + "🚀 **New opensre release: `" + $tag + "`**\n\n📝 **Release Notes**\n" + $notes + "\n\n📊 **Repo Stats**\n⭐ Stars: " + $stars + "\n🍴 Forks: " + $forks + "\n\n🔗 **Release URL:** " + $url)
  }'
)"

curl --fail --silent --show-error --max-time 30 \
  -X POST \
  -H "Content-Type: application/json" \
  -d "$payload" \
  "$DISCORD_WEBHOOK_URL"
