"""Force the LLM client module graph to load as one consistent snapshot.

A long-running process (the gateway, the interactive shell) caches each module
the first time it is imported and never reloads it. When some ``core.llm`` modules
load at boot and others load lazily on first use, a code change *in between* can
leave the process holding a mix of old and new modules — e.g. a freshly-imported
new client doing ``from core.llm.transport_mode import <symbol>`` against a
transport module that was cached (without that symbol) at boot. The result is a
cryptic ``ImportError`` deep inside a turn.

Importing the whole client graph at startup closes that window: the process is
then either fully-old or fully-new — both internally consistent. This does **not**
let a running process pick up code changes; restarting is still required for that.
It only removes the mixed-version failure mode.
"""

from __future__ import annotations


def preload_llm_clients() -> None:
    """Import the LLM client modules so ``core.llm.*`` resolves at one instant."""
    from core.llm import agent_llm_client, llm_client  # noqa: F401
