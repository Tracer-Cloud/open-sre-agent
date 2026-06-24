#!/usr/bin/env python3
"""Build the Discord release announcement JSON payload.

Reads from environment variables set by the GitHub Actions step:
  RELEASE_TAG, RELEASE_URL,
  DISCORD_RELEASES_ROLE_ID, DISCORD_RELEASE_LOGO_EMOJI, DISCORD_RELEASE_LOGO_URL,
  CHANGELOG_FILE  — path to DISCORD_NARRATIVE.md or GENERATED_CHANGELOG.md
"""
from __future__ import annotations

import json
import os
import sys

MAX_CHANGELOG_CHARS = 1400

tag = os.environ["RELEASE_TAG"]
url = os.environ["RELEASE_URL"]
role_id = os.environ.get("DISCORD_RELEASES_ROLE_ID", "")
logo_emoji = os.environ.get("DISCORD_RELEASE_LOGO_EMOJI", "")
logo_url = os.environ.get("DISCORD_RELEASE_LOGO_URL", "")
changelog_file = os.environ.get("CHANGELOG_FILE", "")

if changelog_file and os.path.isfile(changelog_file):
    with open(changelog_file) as f:
        changelog = f.read().replace("\r", "").strip()
else:
    changelog = "No changelog available."

if len(changelog) > MAX_CHANGELOG_CHARS:
    changelog = changelog[: MAX_CHANGELOG_CHARS - 3] + "..."

mention = f"<@&{role_id}>\n" if role_id else ""
logo_prefix = f"{logo_emoji} " if logo_emoji else ""
bt = "`"

content = (
    mention
    + logo_prefix
    + f"🚀 **opensre {bt}{tag}{bt} is live**\n"
    + f"🔗 {url}\n\n"
    + changelog
)

allowed_mentions: dict = {"parse": []}
if role_id:
    allowed_mentions["roles"] = [role_id]

payload: dict = {"content": content, "allowed_mentions": allowed_mentions}
if logo_url:
    payload["username"] = "OpenSRE"
    payload["avatar_url"] = logo_url

json.dump(payload, sys.stdout)
