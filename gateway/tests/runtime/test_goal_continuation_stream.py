"""Multi-turn goal continuation must not reuse one finished transport stream.

A gateway sink is one chat message: the transport streams edits into it and
finalizes once. ``run_until_session_goal`` runs several turns per inbound
message, so continuing on a sink that cannot open a second response drops or
overwrites the earlier reply. Continuation is therefore opt-in per sink.
"""

from __future__ import annotations

import logging
from typing import Any

from gateway.core.runtime.turn_handler import sink_supports_continuation


class _SingleResponseSink:
    """What every chat transport does today: one response per inbound message."""

    def finalize(self, text: str) -> None:
        _ = text

    def set_tool_status(self, text: str) -> None:
        _ = text


class _MultiResponseSink(_SingleResponseSink):
    """A sink that can open a fresh response for each continuation turn."""

    supports_continuation = True


def test_a_single_response_sink_does_not_get_continuation() -> None:
    # Arrange / Act / Assert.
    assert sink_supports_continuation(_SingleResponseSink()) is False


def test_a_sink_that_opts_in_gets_continuation() -> None:
    # Arrange / Act / Assert.
    assert sink_supports_continuation(_MultiResponseSink()) is True


def test_absent_attribute_is_treated_as_no_support() -> None:
    """Defaulting to "supported" would corrupt every existing transport."""
    # Arrange / Act / Assert.
    assert sink_supports_continuation(object()) is False
    _ = logging.getLogger(__name__)
    _unused: Any = None
    assert _unused is None
