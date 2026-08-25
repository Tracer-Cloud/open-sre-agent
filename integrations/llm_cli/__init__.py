"""Subprocess-backed LLM providers (Codex CLI, Claude Code, and other CLIs).

Other tiers import these through this module, not the files inside it. The base
types load eagerly; each provider adapter is re-exported lazily through
``__getattr__`` so importing the package does not pull every provider's
dependencies until a caller selects that provider.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from integrations.llm_cli.base import CLIInvocation, CLIProbe, LLMCLIAdapter
from integrations.llm_cli.errors import CLIAuthenticationRequired
from integrations.llm_cli.runner import CLIBackedLLMClient

#: Provider adapter name -> the submodule that defines it, imported on first access.
_LAZY_EXPORTS: dict[str, str] = {
    "AntigravityCLIAdapter": "integrations.llm_cli.antigravity_cli",
    "ClaudeCodeAdapter": "integrations.llm_cli.claude_code",
    "CodexAdapter": "integrations.llm_cli.codex",
    "CopilotAdapter": "integrations.llm_cli.copilot",
    "CursorAdapter": "integrations.llm_cli.cursor",
    "GeminiCLIAdapter": "integrations.llm_cli.gemini_cli",
    "GrokCLIAdapter": "integrations.llm_cli.grok_cli",
    "KimiAdapter": "integrations.llm_cli.kimi",
    "OpenCodeAdapter": "integrations.llm_cli.opencode",
    "PiAdapter": "integrations.llm_cli.pi_cli",
}


def __getattr__(name: str) -> object:
    """Resolve a lazily re-exported adapter to its submodule attribute (PEP 562).

    Resolved on every access rather than cached in module globals, so a test that
    patches the owning submodule's attribute is reflected here.
    """
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_path), name)


if TYPE_CHECKING:
    from integrations.llm_cli.antigravity_cli import AntigravityCLIAdapter
    from integrations.llm_cli.claude_code import ClaudeCodeAdapter
    from integrations.llm_cli.codex import CodexAdapter
    from integrations.llm_cli.copilot import CopilotAdapter
    from integrations.llm_cli.cursor import CursorAdapter
    from integrations.llm_cli.gemini_cli import GeminiCLIAdapter
    from integrations.llm_cli.grok_cli import GrokCLIAdapter
    from integrations.llm_cli.kimi import KimiAdapter
    from integrations.llm_cli.opencode import OpenCodeAdapter
    from integrations.llm_cli.pi_cli import PiAdapter


__all__ = [
    "AntigravityCLIAdapter",
    "CLIAuthenticationRequired",
    "CLIBackedLLMClient",
    "CLIInvocation",
    "CLIProbe",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "CopilotAdapter",
    "CursorAdapter",
    "GeminiCLIAdapter",
    "GrokCLIAdapter",
    "KimiAdapter",
    "LLMCLIAdapter",
    "OpenCodeAdapter",
    "PiAdapter",
]
