"""Provider-name → :class:`TokenSource` lookup table.

Mirrors :mod:`app.agents.meters.registry`. Unknown names fall back
to :data:`null_token_source` so a new provider on the developer's
machine cannot crash the dashboard.
"""

from __future__ import annotations

from app.agents.token_sources import NullTokenSource, TokenSource, null_token_source
from app.agents.token_sources.claude_code import ClaudeCodeJsonlSource
from app.agents.token_sources.codex import CodexRolloutSource

TOKEN_SOURCE_REGISTRY: dict[str, TokenSource] = {
    "claude-code": ClaudeCodeJsonlSource(),
    "codex": CodexRolloutSource(),
    "cursor": NullTokenSource(),
    "aider": NullTokenSource(),
    "gemini-cli": NullTokenSource(),
    "opencode": NullTokenSource(),
    "kimi": NullTokenSource(),
    "copilot": NullTokenSource(),
}


def get_token_source(provider: str) -> TokenSource:
    return TOKEN_SOURCE_REGISTRY.get(provider, null_token_source)


__all__ = ["TOKEN_SOURCE_REGISTRY", "get_token_source"]
