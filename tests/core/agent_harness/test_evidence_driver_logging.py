"""The gather pass must say why it produced nothing.

``gather_tool_evidence`` has several exits that return ``None`` and let the turn
fall back to a text-only answer. Live, that reads as "the bot ignored its tools"
with nothing in the log to distinguish a cancelled host from an unreachable LLM
from a swallowed exception — and the gateway pins logging to INFO, so anything
emitted below that level is unreachable in a running deployment no matter how
carefully it was written.

These tests assert at INFO specifically. Asserting at DEBUG would pass against
exactly the configuration that made the problem undiagnosable.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.agent_harness.turns import evidence_driver


class _Call:
    """A tool call in the shape ``_format_observation`` and the log line read."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.input: dict[str, Any] = {}


def _session() -> MagicMock:
    session = MagicMock()
    session.cli_agent_messages = []
    session.session_id = "sess-1"
    return session


def test_a_cancelled_host_says_so_at_info(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=evidence_driver.__name__):
        result = evidence_driver.gather_tool_evidence(
            "why is checkout down?", _session(), is_cancelled=lambda: True
        )

    assert result is None
    assert "gather_evidence skip: host cancelled" in caplog.text


def test_a_swallowed_failure_is_not_silent(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The broad-catch boundary reported to Sentry but logged nothing at all.

    An ``ErrorReporter`` is optional, so on the gateway this exit could end a
    turn with no local trace whatsoever. This is a server log, not an external
    surface, so it carries the traceback — the redaction rule applies to HTTP
    responses and chat messages, and stripping detail here would leave the exit
    as undiagnosable as the silence it replaces.
    """

    def _explode(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("integration resolution blew up")

    monkeypatch.setattr(evidence_driver, "_resolve_gather_integrations", _explode)

    with caplog.at_level(logging.INFO, logger=evidence_driver.__name__):
        result = evidence_driver.gather_tool_evidence("why is checkout down?", _session())

    assert result is None
    swallowed = [record for record in caplog.records if "swallowed" in record.getMessage()]
    assert len(swallowed) == 1
    assert swallowed[0].levelno == logging.WARNING
    assert "RuntimeError" in swallowed[0].getMessage()
    assert swallowed[0].exc_info is not None


def test_the_executed_tool_names_reach_the_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A count alone cannot answer "did it even try the fleet search?".

    The action path logs every call it makes; before this, a gather-only turn
    left no record of which evidence the model actually reached for.
    """
    executed = [
        (_Call("kubernetes_search_fleet"), {"total": 0}),
        (_Call("gcp_logging_query"), "{}"),
    ]

    monkeypatch.setattr(
        evidence_driver, "_resolve_gather_integrations", lambda *_a, **_k: {"gcp": {}}
    )
    monkeypatch.setattr(evidence_driver, "_has_usable_gather_tools", lambda _tools: True)
    monkeypatch.setattr(evidence_driver, "_load_gather_llm_or_none", lambda _reporter: MagicMock())
    monkeypatch.setattr(evidence_driver, "persist_turn_system_prompt", lambda *_a, **_k: None)
    monkeypatch.setattr(
        evidence_driver,
        "run_react_agent_with_telemetry",
        lambda *_a, **_k: MagicMock(executed=executed, final_system_prompt="sys"),
    )

    with caplog.at_level(logging.INFO, logger=evidence_driver.__name__):
        result = evidence_driver.gather_tool_evidence(
            "why is checkout down?",
            _session(),
            agent_factory=lambda **_kwargs: MagicMock(),
        )

    assert result is not None
    assert "tools_executed=2" in caplog.text
    assert "kubernetes_search_fleet" in caplog.text
    assert "gcp_logging_query" in caplog.text
