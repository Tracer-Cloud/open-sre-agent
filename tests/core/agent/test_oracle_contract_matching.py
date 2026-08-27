"""Response-contract needle matching for the live turn oracles.

Live models paraphrase, so a scenario asserting *meaning* rather than wording
opts a needle into regex with an ``re:`` prefix. Plain needles keep exact
substring semantics — every pre-existing fixture depends on that.
"""

from __future__ import annotations

import pytest

from tests.core.agent._oracle_normalize import normalize_response_text
from tests.core.agent._oracle_runtime import contains_all, contains_any


def _normalized(text: str) -> str:
    """Contracts are evaluated against the normalized response, not raw text."""
    return normalize_response_text(text)


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
