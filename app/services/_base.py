from __future__ import annotations


class ServiceClientUnavailable(RuntimeError):
    """Raised when an integration is configured but the client fails to construct."""

    integration: str
    reason: str

    def __init__(
        self, integration: str, reason: str, original: BaseException | None = None
    ) -> None:
        super().__init__(f"{integration}: {reason}")
        self.integration = integration
        self.reason = reason
        self.__cause__ = original
