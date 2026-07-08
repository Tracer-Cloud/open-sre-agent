"""Internal LLM infrastructure — not part of the public LLM API.

Startup warm-up (:mod:`preload`) and the factory's singleton cache key
(:mod:`client_cache_key`). Callers outside ``core.llm`` should not import these.
"""

from __future__ import annotations
