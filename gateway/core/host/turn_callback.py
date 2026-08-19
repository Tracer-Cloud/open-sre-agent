"""The per-message entry the host exposes to chat transports.

Signature: ``(text, session, output, logger)``. Every inbound chat message ends
in one call to this. Transports depend on this contract; this module must not
import a transport.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from core.agent_harness import SessionCore
from gateway.core.host.turn_output import GatewayOutputSink

GatewayAgentCallback = Callable[[str, SessionCore, GatewayOutputSink, logging.Logger], None]
