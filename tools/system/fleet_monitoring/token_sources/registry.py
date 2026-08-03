"""Provider-name → :class:`TokenSource` lookup table.

Mirrors :mod:`tools.system.fleet_monitoring.meters.registry`. Unknown names fall back
to :data:`null_token_source` so a new provider on the developer's
machine cannot crash the dashboard.
"""

from __future__ import annotations

from tools.system.fleet_monitoring.provider_ids import FleetAgentProvider
from tools.system.fleet_monitoring.token_sources import (
    NullTokenSource,
    TokenSource,
    null_token_source,
)
from tools.system.fleet_monitoring.token_sources.claude_code import ClaudeCodeJsonlSource
from tools.system.fleet_monitoring.token_sources.codex import CodexRolloutSource

TOKEN_SOURCE_REGISTRY: dict[str, TokenSource] = {
    FleetAgentProvider.CLAUDE_CODE: ClaudeCodeJsonlSource(),
    FleetAgentProvider.CODEX: CodexRolloutSource(),
    FleetAgentProvider.CURSOR: NullTokenSource(),
    FleetAgentProvider.AIDER: NullTokenSource(),
    FleetAgentProvider.GEMINI_CLI: NullTokenSource(),
    FleetAgentProvider.ANTIGRAVITY_CLI: NullTokenSource(),
    FleetAgentProvider.OPENCODE: NullTokenSource(),
    FleetAgentProvider.KIMI: NullTokenSource(),
    FleetAgentProvider.COPILOT: NullTokenSource(),
}


def get_token_source(provider: str) -> TokenSource:
    return TOKEN_SOURCE_REGISTRY.get(provider, null_token_source)


__all__ = ["TOKEN_SOURCE_REGISTRY", "get_token_source"]
