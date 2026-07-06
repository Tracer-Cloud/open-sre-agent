"""Parse a free-text LLM diagnosis into structured root-cause fields.

Legacy fallback used when the diagnose stage's structured output is unavailable.
The model emits labelled sections (``ROOT_CAUSE:``, ``VALIDATED_CLAIMS:``, …) and
this reads each one back out. All the section-scanning shares two helpers:
:func:`_text_between` (the text after a label, up to the next section) and
:func:`_cleaned_bullets` (list items with ``*``/``-``/``•`` markers stripped).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from core.domain.types.root_cause_categories import VALID_ROOT_CAUSE_CATEGORIES

# Sections that can follow ROOT_CAUSE:, in the order they appear — used to bound
# where the root-cause text and each claim list end.
_SECTIONS_AFTER_ROOT_CAUSE = (
    "ROOT_CAUSE_CATEGORY:",
    "VALIDATED_CLAIMS:",
    "NON_VALIDATED_CLAIMS:",
    "CAUSAL_CHAIN:",
    "REMEDIATION_STEPS:",
)
_REMEDIATION_STOP_HEADERS = (
    "ROOT_CAUSE",
    "VALIDATED",
    "NON_VALIDATED",
    "CAUSAL",
    "ALTERNATIVE",
    "REMEDIATION_STEPS",
)


@dataclass(frozen=True)
class RootCauseResult:
    root_cause: str
    root_cause_category: str
    validated_claims: list[str]
    non_validated_claims: list[str]
    causal_chain: list[str]
    remediation_steps: list[str]


def _text_between(text: str, start: str, ends: tuple[str, ...]) -> str | None:
    """Return the text after ``start`` up to the first marker in ``ends``.

    ``None`` when ``start`` is absent; the full remainder when no ``ends`` match.
    """
    if start not in text:
        return None
    section = text.split(start, 1)[1]
    for end in ends:
        if end in section:
            return section.split(end, 1)[0]
    return section


def _cleaned_bullets(section: str, strip: str = "*-• ") -> Iterator[str]:
    """Yield each non-empty line with its leading bullet/number marker stripped."""
    for raw in section.strip().split("\n"):
        line = raw.strip().lstrip(strip).strip()
        if line:
            yield line


def _extract_category(response: str) -> str:
    """First valid category found on any line after ``ROOT_CAUSE_CATEGORY:``."""
    section = _text_between(response, "ROOT_CAUSE_CATEGORY:", ())
    if section is None:
        return "unknown"
    for raw in section.split("\n"):
        candidate = raw.strip().lower()
        if not candidate:
            continue
        if candidate in VALID_ROOT_CAUSE_CATEGORIES:
            return candidate
        for token in re.findall(r"[a-z_][a-z0-9_]*", candidate):
            if token in VALID_ROOT_CAUSE_CATEGORIES:
                return str(token)
    return "unknown"


def _claims(after: str, start: str, ends: tuple[str, ...], skip: tuple[str, ...]) -> list[str]:
    """Bullet items in the ``start`` section, dropping lines that begin with ``skip``."""
    section = _text_between(after, start, ends)
    if section is None:
        return []
    return [line for line in _cleaned_bullets(section) if not line.startswith(skip)]


def _remediation_steps(after: str) -> list[str]:
    """Numbered/bulleted steps after ``REMEDIATION_STEPS:``, stopping at the next header."""
    section = _text_between(after, "REMEDIATION_STEPS:", ())
    if section is None:
        return []
    steps: list[str] = []
    for line in _cleaned_bullets(section, strip="*-•( "):
        if line.startswith("("):
            continue
        if line.startswith(_REMEDIATION_STOP_HEADERS):
            break
        steps.append(line)
    return steps


def parse_root_cause(response: str) -> RootCauseResult:
    """Parse root cause, category, and claims from an LLM diagnosis response."""
    category = _extract_category(response)

    after = _text_between(response, "ROOT_CAUSE:", ())
    if after is None:
        return RootCauseResult("Unable to determine root cause", category, [], [], [], [])

    root_cause = after
    for end in _SECTIONS_AFTER_ROOT_CAUSE:
        if end in after:
            root_cause = after.split(end, 1)[0]
            break

    return RootCauseResult(
        root_cause=root_cause.strip(),
        root_cause_category=category,
        validated_claims=_claims(
            after,
            "VALIDATED_CLAIMS:",
            ("NON_VALIDATED_CLAIMS:", "CAUSAL_CHAIN:", "REMEDIATION_STEPS:"),
            skip=("NON_", "CAUSAL_CHAIN", "CONFIDENCE", "ROOT_CAUSE", "REMEDIATION_STEPS"),
        ),
        non_validated_claims=_claims(
            after,
            "NON_VALIDATED_CLAIMS:",
            ("ALTERNATIVE_HYPOTHESES_CONSIDERED:", "CAUSAL_CHAIN:", "REMEDIATION_STEPS:"),
            skip=("CAUSAL_CHAIN", "ALTERNATIVE", "REMEDIATION_STEPS"),
        ),
        causal_chain=_claims(
            after, "CAUSAL_CHAIN:", ("REMEDIATION_STEPS:",), skip=("ALTERNATIVE",)
        ),
        remediation_steps=_remediation_steps(after),
    )


__all__ = ["RootCauseResult", "parse_root_cause"]
