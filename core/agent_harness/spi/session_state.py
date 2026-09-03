"""Session state a host reads and sets around a turn.

Trust mode, the terminal, auto-command and turn-outcome hints, resume notes
from an interrupted turn, transcript compaction, and the capabilities a host
withholds.
"""

from __future__ import annotations

from core.agent_harness.session.capabilities import withhold_capabilities
from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.session.pending_offer import (
    PendingScheduleOffer,
    clear_competing_pending_offers,
)
from core.agent_harness.session.persistence.wal_recovery import format_recovery_note
from core.agent_harness.session.terminal_access import (
    clear_pending_autosubmit,
    exclusive_stdin_active,
    pop_turn_outcome_hint,
    session_terminal,
    set_auto_command,
    set_turn_outcome_hint,
    trust_mode_enabled,
)
from core.agent_harness.turns.transcript_compaction import compact_session_branch

__all__ = [
    "PendingScheduleOffer",
    "PendingUserChoice",
    "clear_competing_pending_offers",
    "clear_pending_autosubmit",
    "compact_session_branch",
    "exclusive_stdin_active",
    "format_recovery_note",
    "pop_turn_outcome_hint",
    "session_terminal",
    "set_auto_command",
    "set_turn_outcome_hint",
    "trust_mode_enabled",
    "withhold_capabilities",
]
