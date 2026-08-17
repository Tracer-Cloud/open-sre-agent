"""The default adapters a host extends or reuses (reporter, prompt provider, session store)."""

from __future__ import annotations

from core.agent_harness.error_reporting import DefaultErrorReporter
from core.agent_harness.prompts.grounding import DefaultPromptContextProvider
from core.agent_harness.session import default_session_repo, default_session_store
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore

__all__ = [
    "DefaultErrorReporter",
    "DefaultPromptContextProvider",
    "JsonlSessionStore",
    "default_session_repo",
    "default_session_store",
]
