"""Missing-key LLM failures queue ``/onboard`` on the next REPL turn."""

from __future__ import annotations

from core.agent_harness.session.terminal_access import execute_cli_onboard_on_missing_key
from surfaces.interactive_shell.session import Session

_MISSING_KEY = (
    "Missing credentials. Please pass an `api_key`, `workload_identity`, "
    "`admin_api_key`, or set the `OPENAI_API_KEY` or `OPENAI_ADMIN_KEY` "
    "environment variable."
)


def test_missing_key_queues_onboard() -> None:
    session = Session()

    text = execute_cli_onboard_on_missing_key(session, _MISSING_KEY, provider="openrouter")

    assert text is not None
    assert "No API key is set for openrouter" in text
    assert session.terminal.pending_prompt_default == "/onboard"
    assert session.terminal.pending_prompt_autosubmit is True


def test_rejected_key_does_not_queue_onboard() -> None:
    session = Session()

    text = execute_cli_onboard_on_missing_key(
        session, "401 Unauthorized: the key was rejected.", provider="openrouter"
    )

    assert text is None
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False


def test_exclusive_stdin_does_not_requeue_onboard() -> None:
    session = Session()
    session.terminal.exclusive_stdin_active = True

    text = execute_cli_onboard_on_missing_key(session, _MISSING_KEY, provider="openrouter")

    assert text is not None
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.pending_prompt_autosubmit is False
