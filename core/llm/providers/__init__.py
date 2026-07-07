"""Per-provider configuration, model selection, and credential resolution.

Provider-specific knowledge (Azure endpoints, OpenAI-compatible catalog, Bedrock
model IDs, API-key resolution) that the transports and the factory read when
building a client. No client construction lives here.
"""

from __future__ import annotations
