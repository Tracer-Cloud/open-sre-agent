"""What this deployment's agent calls itself.

Operators rename the bot on the surface they run it on — the Slack app carries
one name, the Discord bot another — and the agent then introduces itself with a
name nobody in the channel recognizes. ``OPENSRE_AGENT_NAME``
gives one deployment one name, used for the agent's identity in prompts, the
Slack provenance footer, and the wake word that lets a thread reply reach the
bot without an @mention.

This is the *agent's* name, not the product's: references to the OpenSRE CLI,
its commands, and its docs stay OpenSRE whatever this is set to.

Deliberately not read from the chat vendor. A Slack bot user's profile keeps
the old name until the app is reinstalled, so auto-detection reports a stale
name as confidently as a current one; and one gateway process can serve Slack,
Discord and Telegram at once, each with its own vendor-side name.
"""

from __future__ import annotations

import os
from typing import Final

#: Names the agent for every surface this deployment serves.
AGENT_NAME_ENV: Final[str] = "OPENSRE_AGENT_NAME"

#: Used when the deployment does not rename the agent.
DEFAULT_AGENT_NAME: Final[str] = "OpenSRE"


def agent_name() -> str:
    """The name this deployment's agent goes by, or ``OpenSRE`` when unset."""
    return (os.getenv(AGENT_NAME_ENV) or "").strip() or DEFAULT_AGENT_NAME


__all__ = ["AGENT_NAME_ENV", "DEFAULT_AGENT_NAME", "agent_name"]
