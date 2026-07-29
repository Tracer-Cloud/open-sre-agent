"""Placeholder labels must uphold the one-pass unmask token invariant.

The single-scan unmask matches ``<[^<>]+>``. That is only correct if every
generated placeholder is exactly one bracket pair — so a label taken from user
``extra_patterns`` config must be sanitized before it is interpolated. A raw
label like ``tok>x`` would mint ``<TOK>X_0>``, which the scan can never match:
the secret would stay masked in user-facing output.
"""

from __future__ import annotations

import re

from platform.masking.context import MaskingContext
from platform.masking.policy import MaskingPolicy

_SINGLE_TOKEN = re.compile(r"^<[A-Z0-9_]+>$")

PLANTED_SECRET = "value-that-must-round-trip-7d1"


def _context_with_extra(label: str) -> MaskingContext:
    policy = MaskingPolicy(
        enabled=True,
        extra_patterns={label: re.escape(PLANTED_SECRET)},
    )
    return MaskingContext(policy=policy)


def test_hostile_label_still_mints_a_single_bracket_token() -> None:
    # Arrange
    context = _context_with_extra("tok>x")

    # Act
    masked = context.mask(f"before {PLANTED_SECRET} after")

    # Assert: whatever the placeholder is, it is one clean token.
    (token,) = re.findall(r"<[^ ]*>", masked)
    assert _SINGLE_TOKEN.match(token), f"label leaked into token: {token!r}"
    assert PLANTED_SECRET not in masked


def test_hostile_label_round_trips_through_unmask() -> None:
    """The user-facing path: mask then unmask must restore the original."""
    # Arrange
    context = _context_with_extra("tok>x")
    original = f"the value is {PLANTED_SECRET}."

    # Act
    restored = context.unmask(context.mask(original))

    # Assert
    assert restored == original


def test_angle_brackets_and_spaces_in_labels_are_neutralized() -> None:
    # Arrange / Act / Assert — a few shapes users actually type.
    for label in ("<already>", "two words", "a<b>c", "trailing>"):
        context = _context_with_extra(label)
        original = f"x {PLANTED_SECRET} y"
        assert context.unmask(context.mask(original)) == original, (
            f"label {label!r} broke the mask/unmask round trip"
        )
