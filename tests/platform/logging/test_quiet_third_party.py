"""Third-party log quieting keeps MCP schema warnings off the user TTY."""

from __future__ import annotations

import logging

from platform.logging import quiet_noisy_third_party_loggers


def test_quiet_noisy_third_party_loggers_hides_mcp_schema_warnings() -> None:
    mcp = logging.getLogger("mcp.client.session")
    previous = mcp.level
    try:
        quiet_noisy_third_party_loggers()
        assert mcp.level == logging.ERROR
        assert logging.getLogger("httpx").level == logging.WARNING
    finally:
        mcp.setLevel(previous)
