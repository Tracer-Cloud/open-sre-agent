"""Provider-name → :class:`TokenMeter` lookup table.

Unknown names fall back to :data:`null_meter` so a new provider on
the developer's machine cannot crash the dashboard.
"""

from __future__ import annotations

from tools.system.fleet_monitoring.meters import NullMeter, TokenMeter, null_meter
from tools.system.fleet_monitoring.meters.claude_code import ClaudeCodeMeter
from tools.system.fleet_monitoring.meters.codex import CodexMeter
from tools.system.fleet_monitoring.provider_ids import FleetAgentProvider

TOKEN_METER_REGISTRY: dict[str, TokenMeter] = {
    FleetAgentProvider.CLAUDE_CODE: ClaudeCodeMeter(),
    FleetAgentProvider.CODEX: CodexMeter(),
    FleetAgentProvider.CURSOR: NullMeter(),
    FleetAgentProvider.AIDER: NullMeter(),
    FleetAgentProvider.GEMINI_CLI: NullMeter(),
    FleetAgentProvider.ANTIGRAVITY_CLI: NullMeter(),
    FleetAgentProvider.OPENCODE: NullMeter(),
    FleetAgentProvider.KIMI: NullMeter(),
    FleetAgentProvider.COPILOT: NullMeter(),
}


def get_token_meter(provider: str) -> TokenMeter:
    return TOKEN_METER_REGISTRY.get(provider, null_meter)


__all__ = ["TOKEN_METER_REGISTRY", "get_token_meter"]
