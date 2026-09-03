"""Restore-path message assembly from a persisted session branch."""

from __future__ import annotations


def test_messages_for_branch_uses_session_summary_prefix() -> None:
    from core.agent_harness.session.persistence.jsonl_repo import _messages_for_branch
    from core.state.transcript_window import SESSION_SUMMARY_PREFIX

    branch = [
        {"type": "compaction", "summary": "All checks passed."},
    ]
    messages = _messages_for_branch(branch)
    assert len(messages) == 1
    assert messages[0] == ("assistant", f"{SESSION_SUMMARY_PREFIX}All checks passed.")
