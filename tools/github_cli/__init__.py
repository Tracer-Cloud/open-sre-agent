"""Registry entrypoint for the authenticated GitHub CLI tool."""

from __future__ import annotations

from tools.github_cli.tool import github_cli

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "github_cli"]
