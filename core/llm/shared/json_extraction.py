"""Recover a JSON value from free-form model text.

Two shapes have to be handled, and neither needs guessing:

* **Wrapped JSON** — the model prefixes prose, wraps the payload in a
  ``` fence, or adds a closing remark. That is a *parse offset* problem:
  :func:`json.JSONDecoder.raw_decode` reports where the value ends, so trailing
  prose (including stray braces) cannot over-capture the way a greedy
  ``\\{.*\\}`` regex does.
* **Truncated JSON** — the completion hit the model's ``max_tokens`` ceiling and
  stops mid-value. Nothing is malformed; containers are just still open. Cutting
  back to the last position that is provably a value boundary and closing those
  containers recovers every field the model finished writing.

The second case is the one that matters in practice: a structured-output prompt
that asks for a dozen fields over a long investigation transcript routinely
exceeds a 4k output budget, and a regex cascade turns a payload that is 95%
complete into a total loss.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_VALUE_STARTS = "{["
_CLOSERS = {"{": "}", "[": "]"}

#: Repair scans from a candidate start to the end of the text, so attempting it
#: at every brace in a long document is quadratic. A payload starts at the front
#: of the message, give or take a sentence of preamble that happens to contain a
#: brace, so a handful of leading candidates is enough.
_MAX_REPAIR_STARTS = 8


def extract_json_payload(text: str) -> Any:
    """First JSON object/array in ``text``, repairing a truncated tail.

    Raises ``ValueError`` when no candidate yields a value, even after repair.
    """
    for candidate in _candidate_texts(text):
        value = _first_value(candidate)
        if value is not None:
            return value
    raise ValueError(f"LLM did not return valid JSON payload ({len(text)} chars returned)")


def _first_value(candidate: str) -> Any:
    """The value at the earliest start that parses, complete or repaired.

    Complete-then-repair is decided **per start**, not in two passes over the
    whole text. A truncated outer object contains complete nested values, so a
    global "any complete value first" pass returns the first nested array and
    silently answers the wrong question.
    """
    repairs = 0
    for start in _value_starts(candidate):
        value = _decode_at(candidate, start)
        if value is not None:
            return value
        if repairs >= _MAX_REPAIR_STARTS:
            continue
        repairs += 1
        value = _repaired_at(candidate, start)
        if value is not None:
            return value
    return None


def _repaired_at(candidate: str, start: int) -> Any:
    """Close the containers a truncated value left open; ``None`` if not recoverable."""
    repaired = _closable_prefix(candidate, start)
    if repaired is None:
        return None
    body, closers = repaired
    value = _decode_at(body + closers, 0)
    if value is None:
        return None
    logger.warning(
        "Recovered a truncated JSON payload: kept %d of %d characters and closed %d "
        "open container(s). The completion most likely hit its max_tokens ceiling; "
        "fields after the cut are missing.",
        len(body),
        len(candidate) - start,
        len(closers),
    )
    return value


def _candidate_texts(text: str) -> list[str]:
    """The text bodies worth searching, most explicitly delimited first."""
    cleaned = text.strip()
    candidates: list[str] = []
    for candidate in (_fenced_body(cleaned), cleaned):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _fenced_body(text: str) -> str:
    """Contents of the first ``` fence, or "" when the text carries no fence.

    An unterminated fence still yields its body — a truncated completion loses
    its closing fence along with the rest of the payload.
    """
    opened = text.find("```")
    if opened == -1:
        return ""
    body_start = text.find("\n", opened)
    if body_start == -1:
        return ""
    closed = text.find("```", body_start)
    body = text[body_start:] if closed == -1 else text[body_start:closed]
    return body.strip()


def _value_starts(text: str) -> list[int]:
    """Every offset where a JSON object or array could begin."""
    return [index for index, char in enumerate(text) if char in _VALUE_STARTS]


def _decode_at(text: str, start: int) -> Any:
    """Decode one complete JSON value at ``start``; ``None`` when it does not parse.

    ``None`` is unambiguous here because decoding only ever starts on ``{`` or
    ``[``, so a successful decode is always a dict or a list. ``strict=False``
    tolerates the raw control characters models leave inside string values.
    """
    try:
        value, _end = json.JSONDecoder(strict=False).raw_decode(text, start)
    except (json.JSONDecodeError, ValueError):
        return None
    return value


def _closable_prefix(text: str, start: int) -> tuple[str, str] | None:
    """``(body, closers)`` cutting ``text`` back to the last provable value boundary.

    Returns ``None`` when the scan finds no such boundary, or when the value at
    ``start`` closes on its own — that value is malformed rather than truncated,
    and repairing it would be a guess.
    """
    stack: list[str] = []
    # Per-object frame: are we past the ``:`` of a member (a value position)? A
    # closing quote completes a value there, but is a *key* when it is not.
    in_value: list[bool] = []
    in_string = False
    escaped = False
    cut: int | None = None
    cut_stack: tuple[str, ...] = ()

    for index, char in enumerate(text[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                if _at_value_position(stack, in_value):
                    cut, cut_stack = index + 1, tuple(stack)
            continue

        if char == '"':
            in_string = True
        elif char in _VALUE_STARTS:
            stack.append(char)
            if char == "{":
                in_value.append(False)
        elif char in "}]":
            if not stack:
                return None
            if stack.pop() == "{":
                in_value.pop()
            if not stack:
                return None
            cut, cut_stack = index + 1, tuple(stack)
        elif char == ":" and stack and stack[-1] == "{":
            in_value[-1] = True
        elif char == "," and stack:
            cut, cut_stack = index, tuple(stack)
            if stack[-1] == "{":
                in_value[-1] = False

    if cut is None:
        return None
    return text[start:cut], "".join(_CLOSERS[frame] for frame in reversed(cut_stack))


def _at_value_position(stack: list[str], in_value: list[bool]) -> bool:
    """True when the token just completed is an array element or a member value."""
    if not stack:
        return False
    if stack[-1] == "[":
        return True
    return bool(in_value) and in_value[-1]


__all__ = ["extract_json_payload"]
