"""One shape for a history row, whichever session records it.

``SessionCore`` and the headless adapter both append rows that tools read back
— ``propose_scheduled_delivery`` checks them for evidence that a fetch ran. The
two used to build the dict independently with the same conditional-insert
block, so a field added to one would silently be missing from the other.
"""

from __future__ import annotations

from core.agent_harness.session.history_entry import build_history_entry


def test_optional_fields_are_absent_rather_than_empty() -> None:
    """Readers check membership; a blank value would read as a recorded reply."""
    # Act
    entry = build_history_entry("shell", "curl wttr.in", ok=True)

    # Assert
    assert entry == {"type": "shell", "text": "curl wttr.in", "ok": True}


def test_supplied_optional_fields_are_kept() -> None:
    """Arrange / Act / Assert on the full row so a dropped field fails here."""
    # Act
    entry = build_history_entry(
        "slash",
        "/cron list",
        ok=False,
        response_text="No scheduled tasks configured.",
        slash_outcome="unknown_command",
    )

    # Assert
    assert entry == {
        "type": "slash",
        "text": "/cron list",
        "ok": False,
        "response_text": "No scheduled tasks configured.",
        "slash_outcome": "unknown_command",
    }


def test_both_session_kinds_record_the_same_shape() -> None:
    """The headless adapter mirrors SessionCore; prove it rather than comment it."""
    # Arrange
    from core.agent_harness.turns.headless_adapters import InMemorySessionStore

    headless = InMemorySessionStore()

    # Act
    headless.record("shell", "curl wttr.in", ok=True, response_text="Amsterdam: +20C")

    # Assert
    assert headless.history[-1] == build_history_entry(
        "shell", "curl wttr.in", ok=True, response_text="Amsterdam: +20C"
    )
