"""Registry entrypoint for authenticated GitHub CLI tools."""

from __future__ import annotations

from tools.github_cli.tool import github_cli, github_cli_write

TOOL_MODULES = ("tool",)

__all__ = ["TOOL_MODULES", "github_cli", "github_cli_write"]
