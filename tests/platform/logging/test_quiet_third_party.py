"""Third-party log quieting keeps MCP schema warnings off the user TTY."""

from __future__ import annotations

import logging

from platform.logging import quiet_noisy_third_party_loggers


def test_quiet_noisy_third_party_loggers_hides_mcp_schema_warnings() -> None:
    mcp = logging.getLogger("mcp.client.session")
    client = logging.getLogger("client")
    previous_mcp = mcp.level
    previous_client = client.level
    try:
        quiet_noisy_third_party_loggers()
        assert mcp.level == logging.ERROR
        assert client.level == logging.ERROR
        assert logging.getLogger("httpx").level == logging.WARNING
        # Bare ``client`` logger (mcp SDK) must drop the schema-cache miss.
        assert any(type(f).__name__ == "_DropMcpSchemaValidationNoise" for f in client.filters)
        record = logging.LogRecord(
            name="client",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="Tool execute-sql not listed by server, cannot validate any structured content",
            args=(),
            exc_info=None,
        )
        assert not client.filters[0].filter(record)
    finally:
        mcp.setLevel(previous_mcp)
        client.setLevel(previous_client)
