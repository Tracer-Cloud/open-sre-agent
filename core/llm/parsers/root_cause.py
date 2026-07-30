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


#: Label / markdown / confidence fluff around a sole category on a verdict line
#: (``category -> name``, ``- **name**``, ``name (high confidence)``).
#: Stripped before the whole-line taxonomy check — never used to dig a name out
#: of a mid-sentence mention.
_CATEGORY_LINE_DECORATION = re.compile(
    r"(?:"
    r"root_cause_category|category|cause|"
    r"high|medium|low|confidence|likely|probable|"
    # Affirmative qualifiers. Unlike the rejection cues, omissions here only
    # cost recall (the line yields no verdict), never correctness.
    r"confirmed|verified|certain|definite|definitive|primary|conclusion|"
    r"[\s*_#\-•.:→>,;()\[\]\"'`]+"
    r")+",
    re.IGNORECASE,
)


def _whole_line_category_form(line: str) -> str:
    """Collapse punctuation/spacing on the whole line to an underscored form."""
    return re.sub(r"[^a-z0-9]+", "_", line).strip("_")


def _category_from_verdict_line(line: str) -> str | None:
    """Return a taxonomy category only when the *whole line* is that verdict.

    Mid-sentence matches are never trusted. ``pod_oomkilled is incorrect`` and
    ``cannot be resource_exhaustion`` keep residual English after decoration is
    stripped, so they cannot resolve to a category — regardless of wording.
    """
    if line in VALID_ROOT_CAUSE_CATEGORIES:
        return line
    whole = _whole_line_category_form(line)
    if whole in VALID_ROOT_CAUSE_CATEGORIES:
        return whole
    stripped = _CATEGORY_LINE_DECORATION.sub(" ", line).strip()
    if not stripped or stripped == line:
        return None
    form = _whole_line_category_form(stripped)
    if form in VALID_ROOT_CAUSE_CATEGORIES:
        return form
    return None


def _extract_category(response: str) -> str:
    """First whole-line category verdict after ``ROOT_CAUSE_CATEGORY:``.

    Never selects a taxonomy name merely mentioned inside a sentence.
    """
    section = _text_between(response, "ROOT_CAUSE_CATEGORY:", ())
    if section is None:
        return "unknown"
    for raw in section.split("\n"):
        candidate = raw.strip().lower()
        if not candidate:
            continue
        category = _category_from_verdict_line(candidate)
        if category is not None:
            return category
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
