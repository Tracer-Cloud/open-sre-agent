"""Chat notification protocol for detached investigation delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from gateway.core.chat.delivery_target import ChatDeliveryTarget


@dataclass(frozen=True)
class DetachedInvestigationRequest:
    """Request to launch a detached investigation from chat."""

    alert_text: str
    delivery_target: ChatDeliveryTarget
    investigation_id: str
    context_overrides: dict[str, Any] | None = None


@dataclass(frozen=True)
class DetachedInvestigationAck:
    """Acknowledgment that investigation was queued."""

    investigation_id: str
    message: str


class ChatNotifier(Protocol):
    """Protocol for posting investigation updates back to chat platforms.

    Implementations handle platform-specific message delivery after investigations
    complete on background workers, when the original turn context is gone.
    """

    def post_ack(self, target: ChatDeliveryTarget, ack: DetachedInvestigationAck) -> str | None:
        """Post the acknowledgment; return the platform message id to edit for stage updates."""

    def update_stage(self, target: ChatDeliveryTarget, stage: str, investigation_id: str) -> None:
        """Edit the acknowledgment in place to show the current pipeline stage."""

    def deliver_final(self, target: ChatDeliveryTarget, report: str, investigation_id: str) -> bool:
        """Post the final report; return whether it reached the thread."""

    def report_failure(
        self, target: ChatDeliveryTarget, error_summary: str, investigation_id: str
    ) -> None:
        """Report investigation failure with generic error message (no exception details)."""

    def mark_origin_complete(self, target: ChatDeliveryTarget) -> None:
        """Signal success on the message that asked; best-effort, never raises."""

    def mark_origin_failed(self, target: ChatDeliveryTarget) -> None:
        """Signal failure on the message that asked; best-effort, never raises."""
