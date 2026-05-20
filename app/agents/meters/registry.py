"""Provider-name → :class:`TokenMeter` lookup table.

Unknown names fall back to :data:`null_meter` so a new provider on
the developer's machine cannot crash the dashboard.
"""

from __future__ import annotations

from app.agents.meters import NullMeter, TokenMeter, null_meter
from app.agents.meters.claude_code import ClaudeCodeMeter
from app.agents.meters.codex import CodexMeter

TOKEN_METER_REGISTRY: dict[str, TokenMeter] = {
    "claude-code": ClaudeCodeMeter(),
    "codex": CodexMeter(),
    "cursor": NullMeter(),
    "aider": NullMeter(),
    "gemini-cli": NullMeter(),
    "opencode": NullMeter(),
    "kimi": NullMeter(),
    "copilot": NullMeter(),
}


def get_token_meter(provider: str) -> TokenMeter:
    return TOKEN_METER_REGISTRY.get(provider, null_meter)


__all__ = ["TOKEN_METER_REGISTRY", "get_token_meter"]
