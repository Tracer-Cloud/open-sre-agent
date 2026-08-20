"""Product-level statements shown to users in more than one place.

Release maturity appears on the README badge, in the README callout, and in the
CLI banner. Held here so those cannot drift apart — they did, with the README
saying "Public Alpha" while the CLI said "Public Beta".
"""

from __future__ import annotations

from typing import Final

#: Release maturity, as users see it. Keep in step with the README badge.
RELEASE_STAGE: Final[str] = "Public Alpha"

#: The one-line maturity banner printed before the landing page.
RELEASE_STAGE_BANNER: Final[str] = (
    f"🚧 OpenSRE is in {RELEASE_STAGE} — core workflows are usable, "
    "and APIs and integrations may still change."
)

#: Overrides the GitHub releases endpoint the update check reads (tests, mirrors).
RELEASES_API_URL_ENV: Final[str] = "OPENSRE_RELEASES_API_URL"

#: Set by ``uv run`` on child processes; its presence marks a development checkout.
UV_RUN_RECURSION_DEPTH_ENV: Final[str] = "UV_RUN_RECURSION_DEPTH"

__all__ = [
    "RELEASES_API_URL_ENV",
    "RELEASE_STAGE",
    "RELEASE_STAGE_BANNER",
    "UV_RUN_RECURSION_DEPTH_ENV",
]
