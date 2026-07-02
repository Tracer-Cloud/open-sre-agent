"""Parse a Sentry issue URL into its organization + issue id.

Supports the common Sentry issue URL shapes (SaaS and self-hosted):

- ``https://<org>.sentry.io/issues/<id>/``
- ``https://<org>.sentry.io/issues/<id>/events/<event>/``
- ``https://sentry.io/organizations/<org>/issues/<id>/``
- ``https://sentry.example.com/organizations/<org>/issues/<id>/`` (self-hosted)

The ``issue_id`` is what the Sentry API (``get_sentry_issue``) needs; ``org`` is
returned when present in the URL so callers can cross-check it against config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# /issues/<id>  — id is alphanumeric (Sentry short ids) or numeric.
_ISSUE_ID_RE = re.compile(r"/issues/([A-Za-z0-9_-]+)")
# /organizations/<org>/  — explicit org segment (sentry.io SaaS + self-hosted).
_ORG_PATH_RE = re.compile(r"/organizations/([A-Za-z0-9_.-]+)")


@dataclass(frozen=True)
class SentryIssueRef:
    """A Sentry issue identified from a URL."""

    issue_id: str
    organization_slug: str = ""


def parse_sentry_issue_url(url: str | None) -> SentryIssueRef | None:
    """Return the issue id (+ org if present) for a Sentry issue URL, else ``None``."""
    raw = (url or "").strip()
    if not raw:
        return None

    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if "sentry" not in parsed.netloc.lower():
        return None

    issue_match = _ISSUE_ID_RE.search(parsed.path)
    if not issue_match:
        return None
    issue_id = issue_match.group(1)

    org = ""
    org_match = _ORG_PATH_RE.search(parsed.path)
    if org_match:
        org = org_match.group(1)
    else:
        # ``<org>.sentry.io`` subdomain form.
        host = parsed.netloc.lower()
        if host.endswith(".sentry.io"):
            subdomain = host.removesuffix(".sentry.io")
            if subdomain and subdomain != "www":
                org = subdomain

    return SentryIssueRef(issue_id=issue_id, organization_slug=org)
