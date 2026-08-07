"""Response-contract needle matching for the live turn oracles.

Live models paraphrase, so a scenario asserting *meaning* rather than wording
opts a needle into regex with an ``re:`` prefix. Plain needles keep exact
substring semantics — every pre-existing fixture depends on that.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.core.agent._oracle_normalize import normalize_response_text
from tests.core.agent._oracle_runtime import contains_all, contains_any

_SCENARIOS = Path(__file__).parent / "scenarios"


def _normalized(text: str) -> str:
    """Contracts are evaluated against the normalized response, not raw text."""
    return normalize_response_text(text)


def _scenario_600_must_contain_any() -> list[str]:
    """The shipped 600 contract, so tests pin what actually ships."""
    raw = (_SCENARIOS / "follow_up" / "600-follow-up-why-failed.yml").read_text(encoding="utf-8")
    return list(yaml.safe_load(raw)["response_contract"]["must_contain_any"])


def test_plain_needles_stay_exact_substring_matches() -> None:
    # Arrange
    haystack = _normalized("The disk was full on orders-api.")

    # Assert: a literal phrase that is not present must not match.
    assert contains_any(haystack, ["disk full"]) is False
    assert contains_any(haystack, ["disk was full"]) is True


def test_empty_needles_are_vacuously_satisfied() -> None:
    # Arrange / Act / Assert: an unconstrained contract passes.
    assert contains_any(_normalized("anything"), []) is True
    assert contains_all(_normalized("anything"), []) is True


@pytest.mark.parametrize(
    "response",
    [
        "the disk was full on orders-api",
        "that filled-up disk is what caused the failure",
        "root cause: disk full on orders-api",
        "the full disk on orders-api",
        "orders-api ran out of disk space",
        "the filesystem was full, so writes started failing",
    ],
)
def test_shipped_600_contract_absorbs_paraphrase(response: str) -> None:
    """The 600 flake: correct answers that word the root cause differently.

    Reads the shipped fixture so the contract and this expectation cannot drift.
    """
    # Arrange
    needles = _scenario_600_must_contain_any()

    # Act / Assert
    assert contains_any(_normalized(response), needles) is True


@pytest.mark.parametrize(
    "non_answer",
    [
        # The failure mode the scenario exists to catch: no prior RCA used.
        "I don't have enough context. Please paste the alert or run an investigation.",
        # Echoes the question's own wording without diagnosing anything.
        "I'm not sure why it failed; can you share more detail?",
        # Names the service but never states the cause.
        "I looked at orders-api but could not find a matching incident.",
        # Uses the phrase "root cause" while admitting it has none.
        "The root cause is not something I can determine from this session.",
    ],
)
def test_shipped_600_contract_rejects_non_answers(non_answer: str) -> None:
    """Broader matching must not become a rubber stamp.

    Each of these would satisfy a weaker alternative (``failed``, ``orders-api``,
    ``root cause``) while telling the user nothing — the contract must require
    the diagnosis itself.
    """
    # Arrange / Act / Assert
    assert contains_any(_normalized(non_answer), _scenario_600_must_contain_any()) is False


def test_bare_regex_prefix_is_rejected_rather_than_matching_everything() -> None:
    """A fixture typo must fail loudly, not silently pass every response."""
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match="empty regex needle"):
        contains_any(_normalized("any response at all"), ["re:"])

    with pytest.raises(ValueError, match="empty regex needle"):
        contains_all(_normalized("any response at all"), ["re:   "])


@pytest.mark.parametrize(
    "response", ["why did it fail", "it failed", "the failure", "it keeps failing"]
)
def test_regex_needle_matches_a_word_stem(response: str) -> None:
    # Arrange / Act / Assert
    assert contains_any(_normalized(response), [r"re:fail(ed|ure|ing)?\b"]) is True


@pytest.mark.parametrize(
    "response",
    ["use local llm setup", "connect a local model", "local llama is not an integration"],
)
def test_regex_needle_covers_the_local_model_phrasings(response: str) -> None:
    """The 500 flake: guidance may say llm, llama, or model."""
    # Arrange / Act / Assert
    assert contains_any(_normalized(response), ["re:local (llm|llama|model)"]) is True


def test_contains_all_honours_regex_needles() -> None:
    # Arrange
    haystack = _normalized("The disk was full, so the service failed.")

    # Act / Assert: both patterns must match for contains_all to pass.
    assert contains_all(haystack, [r"re:disk\b[^.]{0,30}\bfull", r"re:fail(ed|ure)?\b"]) is True
    assert contains_all(haystack, [r"re:disk\b[^.]{0,30}\bfull", "sentry"]) is False


def test_blank_needles_are_ignored_rather_than_matching_everything() -> None:
    # Arrange: a stray empty entry must not silently satisfy the contract.
    haystack = _normalized("unrelated answer")

    # Act / Assert
    assert contains_any(haystack, ["   ", "sentry"]) is False
