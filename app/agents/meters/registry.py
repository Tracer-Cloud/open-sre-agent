"""Provider-name → ``TokenMeter`` lookup table.

The registry is the single point where the dashboard wiring layer
(#1490) translates a tracked agent's provider identifier into a
parser instance. Provider names match the existing CLI-adapter
registry in ``app/integrations/llm_cli/registry.py`` so the two
stay aligned (``claude-code``, ``codex``, ``cursor``, ``gemini-cli``,
``opencode``, ``kimi``); ``aider`` is added here because it's a
locally-monitored agent even though it has no LLM CLI adapter.

Unknown names fall back to ``null_meter`` rather than raising — a new
provider on the developer's machine should not crash the dashboard
just because we haven't shipped a parser for it yet.
"""

from __future__ import annotations

from app.agents.meters import NullMeter, TokenMeter, null_meter
from app.agents.meters.claude_code import ClaudeCodeMeter

TOKEN_METER_REGISTRY: dict[str, TokenMeter] = {
    "claude-code": ClaudeCodeMeter(),
    # Stubs — real parsers ship as follow-up issues.
    "codex": NullMeter(),
    "cursor": NullMeter(),
    "aider": NullMeter(),
    "gemini-cli": NullMeter(),
    "opencode": NullMeter(),
    "kimi": NullMeter(),
}


def get_token_meter(provider: str) -> TokenMeter:
    """Return the meter for *provider*, or ``null_meter`` for unknown providers.

    The lookup is case-sensitive — provider identifiers in this codebase
    are lowercase-with-hyphen by convention (``claude-code`` not
    ``Claude_Code``). Callers should normalize before lookup.
    """
    return TOKEN_METER_REGISTRY.get(provider, null_meter)


__all__ = ["TOKEN_METER_REGISTRY", "get_token_meter"]
