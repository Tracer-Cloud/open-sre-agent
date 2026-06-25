"""Domain envelope for pipeline streaming events.

``StreamEvent`` is the inter-layer event shape produced by the
orchestration pipeline (node updates + fine-grained chain/tool/LLM
callbacks) and consumed by the CLI renderer, the remote runner, and
any future surface that observes pipeline progress.

It deliberately lives in ``app.core.domain`` so that the orchestration
core does not need to import from ``app.remote`` (a transport-layer
package). The SSE parser that materializes ``StreamEvent``s from a
remote HTTP response stays in ``app.remote.stream``; the event shape
itself is domain.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StreamEvent:
    """A parsed event from the pipeline stream.

    Attributes:
        event_type: The SSE event type (e.g. "events", "metadata", "end",
            or legacy "updates").
        node_name: The pipeline node that produced this event, if applicable.
        data: The parsed JSON payload.
        timestamp: Monotonic timestamp when this event was received.
        kind: For ``events`` mode — the callback kind
            (e.g. "on_tool_start", "on_chat_model_stream").
        run_id: Run ID from event metadata.
        tags: Tags attached to the event payload.
    """

    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    node_name: str = ""
    timestamp: float = field(default_factory=time.monotonic)
    kind: str = ""
    run_id: str = ""
    tags: list[str] = field(default_factory=list)
