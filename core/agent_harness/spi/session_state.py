"""Session state a host reads and sets around a turn.

Background and trust mode, the terminal, auto-command and turn-outcome hints,
resume notes from an interrupted turn, and transcript compaction.
"""

from __future__ import annotations

from core.agent_harness.session.persistence.wal_recovery import format_recovery_note
from core.agent_harness.session.terminal_access import (
    background_investigations,
    background_mode_enabled,
    background_notification_channels,
    clear_pending_autosubmit,
    pop_turn_outcome_hint,
    session_terminal,
    set_auto_command,
    set_turn_outcome_hint,
    trust_mode_enabled,
)
from core.agent_harness.turns.transcript_compaction import compact_session_branch

__all__ = [
    "background_investigations",
    "background_mode_enabled",
    "background_notification_channels",
    "clear_pending_autosubmit",
    "compact_session_branch",
    "format_recovery_note",
    "pop_turn_outcome_hint",
    "session_terminal",
    "set_auto_command",
    "set_turn_outcome_hint",
    "trust_mode_enabled",
]
