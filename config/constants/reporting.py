"""Constants shared across the investigation reporting package."""

from __future__ import annotations

import re
from typing import Final

# Matches Slack-style links: <url|label> or <url>. Shared between the WhatsApp
# formatter and the terminal plain-text renderer so both stay in step.
SLACK_LINK_RE: Final[re.Pattern[str]] = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")

__all__ = ["SLACK_LINK_RE"]
