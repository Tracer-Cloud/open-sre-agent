from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from core.agent_harness.session import ReplSession
from gateway.agent.gateway_agent_adapters import (
    GatewayErrorReporter,
    GatewayPromptContextProvider,
    GatewayRunRecordFactory,
)


def test_gateway_prompt_context_provider_reads_grounding() -> None:
    session = ReplSession()
    session.grounding.cli.build_text = MagicMock(return_value="CLI help")  # type: ignore[method-assign]
    session.grounding.agents_md.build_text = MagicMock(return_value="AGENTS")  # type: ignore[method-assign]
    session.grounding.log_cache_diagnostics = MagicMock()  # type: ignore[method-assign]

    provider = GatewayPromptContextProvider(session)

    assert provider.cli_reference() == "CLI help"
    assert provider.agents_md() == "AGENTS"
    assert provider.investigation_flow()
    provider.log_diagnostics("test")
    session.grounding.log_cache_diagnostics.assert_called_once_with("test")  # type: ignore[attr-defined]


def test_gateway_run_record_factory_records_token_usage() -> None:
    session = ReplSession()
    factory = GatewayRunRecordFactory(session)

    record = factory.build(
        client=MagicMock(),
        prompt="hello " * 20,
        response_text="world " * 20,
        started=0.0,
    )

    assert record.response_text.startswith("world")
    assert session.token_usage.get("input_estimated", 0) > 0
    assert session.token_usage.get("output_estimated", 0) > 0


def test_gateway_error_reporter_logs_expected_errors_at_debug() -> None:
    test_logger = logging.getLogger("gateway.tests.error_reporter")
    reporter = GatewayErrorReporter(test_logger)

    with patch.object(test_logger, "debug") as mock_debug:
        reporter.report(ValueError("boom"), context="gateway.test", expected=True)

    mock_debug.assert_called_once()
