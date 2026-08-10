"""Progress tracker that relays pipeline stages to the chat thread that asked.

The notifier, target and investigation id are constructor arguments rather than
things this class looks up. Both lookups it could have done are unavailable where
it actually runs: ``ProgressReporter.start`` takes no ``**kwargs``, so no stage in
the pipeline can hand it an investigation id, and the worker thread reconstructs
its delivery target from the stored record instead of binding the contextvar. A
tracker that resolved either one itself would silently post nothing — which is
indistinguishable from a run that simply had no stages.
"""

from __future__ import annotations

import logging

from config.constants.investigation_stages import DEFAULT_STAGE_LABEL, INVESTIGATION_STAGE_LABELS
from gateway.core.chat import ChatDeliveryTarget, ChatNotifier
from platform.observability.render.progress import ProgressReporter

logger = logging.getLogger(__name__)


class StageNotifyingProgressTracker(ProgressReporter):
    """Post one chat stage update per distinct pipeline stage."""

    def __init__(
        self,
        *,
        notifier: ChatNotifier,
        target: ChatDeliveryTarget,
        investigation_id: str,
    ) -> None:
        self._notifier = notifier
        self._target = target
        self._investigation_id = investigation_id
        self._seen_stages: set[str] = set()

    def start(self, node_name: str, message: str | None = None) -> None:
        """Announce ``node_name``, once, under its reader-facing label.

        ``message`` is deliberately dropped. Stage captions are written for a
        terminal and several name internals; the label table is the vocabulary
        this surface publishes.
        """
        _ = message
        if node_name in self._seen_stages:
            return
        self._seen_stages.add(node_name)
        self._update(INVESTIGATION_STAGE_LABELS.get(node_name, DEFAULT_STAGE_LABEL))

    def error(self, node_name: str, message: str) -> None:
        """Report that one stage failed, naming the stage but never the cause.

        ``message`` carries exception detail and this is an external surface
        (CWE-209). The run may still finish — the terminal outcome is delivered
        by the worker, not here — so this says the stage failed, not the
        investigation.
        """
        _ = message
        label = INVESTIGATION_STAGE_LABELS.get(node_name, DEFAULT_STAGE_LABEL)
        self._update(f"{label} — failed")

    def complete(
        self,
        node_name: str,
        fields_updated: list[str] | None = None,
        message: str | None = None,
    ) -> None:
        """Ignored: ``start`` of the next stage is the completion signal."""
        _ = (node_name, fields_updated, message)

    def record_tool_start(
        self,
        tool_name: str,
        tool_input: object = None,
        *,
        event_key: str | None = None,
    ) -> None:
        """Ignored: an investigation runs dozens of tools per stage."""
        _ = (tool_name, tool_input, event_key)

    def record_tool_end(
        self,
        tool_name: str,
        output: object = None,
        *,
        event_key: str | None = None,
        tool_input: object = None,
    ) -> None:
        """Ignored: an investigation runs dozens of tools per stage."""
        _ = (tool_name, output, event_key, tool_input)

    def stop(self) -> None:
        """Nothing to tear down: there is no live display behind this tracker."""

    def _update(self, label: str) -> None:
        """Send one stage update, absorbing anything the transport raises.

        A progress update is not worth failing a run over: the report is the
        deliverable and it is posted by the worker on a separate call.
        """
        try:
            self._notifier.update_stage(self._target, label, self._investigation_id)
        except Exception:
            logger.warning(
                "[chat-investigations] stage update failed for %s", self._investigation_id
            )
