"""Core-owned default run-record factory for the shared agent harness."""

from __future__ import annotations

from typing import Any

from core.agent_harness.accounting.token_accounting import LlmRunInfo, build_llm_run_info
from core.llm.types import StreamingReasoningClient


class DefaultRunRecordFactory:
    """:class:`core.agent_harness.ports.RunRecordFactory` producing ``LlmRunInfo``."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def bind_session(self, session: Any) -> None:
        """Point run records at a freshly resolved session (gateway reuse)."""
        self._session = session

    def build(
        self,
        *,
        client: StreamingReasoningClient | None,
        prompt: str,
        response_text: str,
        started: float,
    ) -> LlmRunInfo:
        return build_llm_run_info(
            session=self._session,
            prompt=prompt,
            response_text=response_text,
            started=started,
            client=client,
        )
