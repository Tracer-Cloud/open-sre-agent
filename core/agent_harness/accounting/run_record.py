"""Core-owned default run-record factory for the shared agent harness."""

from __future__ import annotations

from typing import Any

from core.agent_harness.accounting.token_accounting import build_llm_run_info


class DefaultRunRecordFactory:
    """:class:`core.agent_harness.ports.RunRecordFactory` producing ``LlmRunInfo``."""

    def __init__(self, session: Any) -> None:
        self._session = session

    def build(self, *, client: Any, prompt: str, response_text: str, started: float) -> Any:
        return build_llm_run_info(
            session=self._session,
            prompt=prompt,
            response_text=response_text,
            started=started,
            client=client,
        )
