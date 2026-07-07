"""The two LLM transports OpenSRE can route a hosted provider through.

A *transport* is how a client actually talks to a hosted model. There are two,
selected per provider by ``core.llm.transport_mode`` and dispatched in
``core.llm.factory``:

- ``sdk``     — native vendor SDK clients (the default path).
- ``litellm`` — clients built on the LiteLLM library (used when
  ``OPENSRE_LLM_TRANSPORT=litellm``, and always for Azure OpenAI).

Both build the same client contracts (``AgentLLMClient`` for tool-calling, the
streaming client for reasoning) so callers never depend on the transport chosen.
"""

from __future__ import annotations
