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

__all__ = ["RELEASE_STAGE", "RELEASE_STAGE_BANNER"]
