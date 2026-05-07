"""Models for the notification system."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class NotificationEvent:
    """Represents a standardized notification payload."""
    
    title: str
    body: str
    severity: str = "info"  # info, warning, critical
    investigation_id: Optional[str] = None
    investigation_url: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def format_text(self) -> str:
        """Standard text fallback for simple providers (WhatsApp/SMS)."""
        prefix = f"[{self.severity.upper()}] " if self.severity != "info" else ""
        text = f"{prefix}{self.title}\n\n{self.body}"
        if self.investigation_url:
            text += f"\n\nView details: {self.investigation_url}"
        return text
