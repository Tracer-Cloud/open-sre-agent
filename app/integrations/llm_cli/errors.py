"""Errors raised by subprocess-backed LLM CLI adapters."""

from __future__ import annotations


class CLIAuthenticationRequired(RuntimeError):
    """CLI probe reported the user is definitely not authenticated (`logged_in=False`).

    Investigation / streaming entrypoints map this to :class:`OpenSREError` so the
    CLI prints a short message and suggestion instead of a traceback.
    """

    def __init__(self, *, provider: str, auth_hint: str, detail: str) -> None:
        self.provider = provider
        self.auth_hint = auth_hint
        self.detail = detail
        super().__init__(f"{provider} is not authenticated. {auth_hint} ({detail})")
