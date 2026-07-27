"""Synthetic-run failure grounding block.

This is the only prompt input that reads from disk, so the failure paths matter:
a missing or oversized observation must degrade to no block, never raise into
the turn.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.agent_harness.prompts import synthetic_failure


class _Snapshot:
    """Minimal turn snapshot: only the two fields the block reads."""

    def __init__(self, *, text: str, observation_path: str | None) -> None:
        self.text = text
        self.last_synthetic_observation_path = observation_path


@pytest.mark.parametrize(
    "message",
    ["why did it fail?", "What went wrong?", "why does the run keep failing"],
)
def test_recognizes_a_failure_question(message: str) -> None:
    # Arrange / Act / Assert
    assert synthetic_failure.requests_explanation(message) is True


@pytest.mark.parametrize("message", ["", "   ", "list my integrations", "what happened?"])
def test_ignores_messages_that_are_not_about_a_failure(message: str) -> None:
    # Arrange / Act / Assert
    assert synthetic_failure.requests_explanation(message) is False


def test_matches_the_suggested_prompt_verbatim() -> None:
    # Arrange: the prompt OpenSRE itself offers after a failed run.
    suggested = "Explain the synthetic failure"

    # Act / Assert: trailing punctuation must not defeat the match.
    assert synthetic_failure.requests_explanation(f"{suggested}?", suggested) is True


def test_unreadable_observation_yields_no_text(tmp_path: Path) -> None:
    """A deleted or unreadable observation must not raise into the turn."""
    # Arrange
    missing = tmp_path / "does-not-exist.json"

    # Act
    text = synthetic_failure.load_observation_text(str(missing))

    # Assert
    assert text == ""


def test_oversized_observation_is_truncated_with_a_marker(tmp_path: Path) -> None:
    # Arrange: an observation larger than the cap, with a tail that must be cut.
    obs = tmp_path / "latest.json"
    obs.write_text("A" * 50 + "TAIL-MUST-BE-TRUNCATED", encoding="utf-8")

    # Act
    text = synthetic_failure.load_observation_text(str(obs), max_chars=50)

    # Assert
    assert "TAIL-MUST-BE-TRUNCATED" not in text
    assert "truncated for prompt size" in text


def test_block_is_empty_without_an_observation_path() -> None:
    # Arrange
    snapshot = _Snapshot(text="why did it fail?", observation_path=None)

    # Act / Assert
    assert synthetic_failure.build_block(snapshot) == ""  # type: ignore[arg-type]


def test_block_is_empty_when_the_question_is_unrelated(tmp_path: Path) -> None:
    """An observation on disk must not leak into an unrelated turn."""
    # Arrange
    obs = tmp_path / "latest.json"
    obs.write_text('{"scenario": "leak-marker"}', encoding="utf-8")
    snapshot = _Snapshot(text="list my integrations", observation_path=str(obs))

    # Act
    block = synthetic_failure.build_block(snapshot)  # type: ignore[arg-type]

    # Assert
    assert block == ""
    assert "leak-marker" not in block


def test_block_is_empty_when_the_observation_file_is_empty(tmp_path: Path) -> None:
    # Arrange
    obs = tmp_path / "latest.json"
    obs.write_text("", encoding="utf-8")
    snapshot = _Snapshot(text="why did it fail?", observation_path=str(obs))

    # Act / Assert
    assert synthetic_failure.build_block(snapshot) == ""  # type: ignore[arg-type]


def test_block_carries_the_observation_and_forbids_claiming_no_run(tmp_path: Path) -> None:
    # Arrange
    obs = tmp_path / "latest.json"
    obs.write_text('{"scenario": "005-failover", "passed": false}', encoding="utf-8")
    snapshot = _Snapshot(text="why did it fail?", observation_path=str(obs))

    # Act
    block = synthetic_failure.build_block(snapshot)  # type: ignore[arg-type]

    # Assert
    assert "005-failover" in block
    assert str(obs) in block
    assert "Do not say nothing ran" in block
