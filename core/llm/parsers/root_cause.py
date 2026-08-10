"""Parse a free-text LLM diagnosis into structured root-cause fields.

Fallback used when the diagnose stage's structured output is unavailable. It
reads labelled sections (``ROOT_CAUSE:``, ``VALIDATED_CLAIMS:``, …) back out of
the conclusion. All the section-scanning shares two helpers:
:func:`_text_between` (the text after a label, up to the next section) and
:func:`_cleaned_bullets` (list items with ``*``/``-``/``•`` markers stripped).

The investigation system prompt asks for those sections as **markdown headings**
(``**Root cause**:``), not as the screaming-snake labels, so
:func:`_normalize_section_labels` rewrites the heading forms to the canonical
labels first. Without that step this parser matches nothing on a real
conclusion and every fallback returns "Unable to determine root cause" — which
is exactly what a failed structured parse used to produce.
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
    "EVIDENCE:",
    "VALIDATED_CLAIMS:",
    "NON_VALIDATED_CLAIMS:",
    "CAUSAL_CHAIN:",
    "REMEDIATION_STEPS:",
)
_REMEDIATION_STOP_HEADERS = (
    "ROOT_CAUSE",
    "EVIDENCE",
    "VALIDATED",
    "NON_VALIDATED",
    "CAUSAL",
    "ALTERNATIVE",
    "REMEDIATION_STEPS",
    "VALIDITY_SCORE",
)


#: Heading text the investigation prompt asks for, mapped to the canonical
#: label this parser scans for. Keys are the heading lowercased with every run
#: of non-alphanumeric characters collapsed to ``_``, so ``Non-validated
#: claims``, ``NON VALIDATED CLAIMS`` and ``non_validated_claims`` all land on
#: the same entry. Kept to the headings the prompt actually requests — a looser
#: table (bare ``category``, bare ``remediation``) would capture prose.
#:
#: ``Evidence`` and ``Validity score`` have no field on
#: :class:`RootCauseResult`, but the prompt puts them between and after the
#: sections that do, so they must be recognised as boundaries or their contents
#: are appended to whichever list precedes them.
_HEADING_LABELS = {
    "root_cause": "ROOT_CAUSE:",
    "root_cause_category": "ROOT_CAUSE_CATEGORY:",
    "evidence": "EVIDENCE:",
    "validated_claims": "VALIDATED_CLAIMS:",
    "non_validated_claims": "NON_VALIDATED_CLAIMS:",
    "causal_chain": "CAUSAL_CHAIN:",
    "remediation_steps": "REMEDIATION_STEPS:",
    "alternative_hypotheses_considered": "ALTERNATIVE_HYPOTHESES_CONSIDERED:",
    "validity_score": "VALIDITY_SCORE:",
}

#: A colon-terminated heading at the start of a line: optional list bullet,
#: optional ATX marker, then the heading text and its colon in any emphasis
#: arrangement (``**Root cause**:``, ``**Root cause:**``, ``## Root cause:``,
#: ``Root cause:``). The colon is what separates a heading from a sentence, so
#: a line with content after it is only rewritten when the colon is present.
_HEADING_LINE = re.compile(
    r"^[ \t]{0,3}(?:[-*+][ \t]+)?(?:#{1,6}[ \t]+)?"
    r"(?P<heading>[*_ \t]*[A-Za-z][A-Za-z0-9 \-]*[*_ \t]*:[*_]*)"
)

#: Characters that are pure markdown decoration around a heading.
_HEADING_DECORATION = "#*_-• \t:"


def _canonical_label(heading: str) -> str | None:
    """The canonical ``LABEL:`` for a heading, or ``None`` when it is not one."""
    bare = heading.strip(_HEADING_DECORATION)
    return _HEADING_LABELS.get(re.sub(r"[^a-z0-9]+", "_", bare.lower()).strip("_"))


def _normalized_line(line: str) -> str:
    """Rewrite one markdown heading line to canonical form, or return it as-is."""
    match = _HEADING_LINE.match(line)
    if match is not None:
        label = _canonical_label(match.group("heading"))
        if label is not None:
            return f"{label}{line[match.end() :]}"
        return line
    # A heading with no colon is only trusted when the whole line is the
    # heading (``## Root cause``, ``**Root cause**``) — never mid-prose.
    label = _canonical_label(line)
    return label if label is not None else line


def _normalize_section_labels(response: str) -> str:
    """Rewrite markdown section headings to the canonical ``LABEL:`` form.

    The investigation prompt asks the model for ``**Root cause**:`` and friends,
    never for ``ROOT_CAUSE:``, so without this every section scan below misses
    and the caller reports no root cause at all. Lines whose heading is not in
    :data:`_HEADING_LABELS` — and lines already in canonical form — are returned
    untouched.
    """
    return "\n".join(_normalized_line(line) for line in response.split("\n"))


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
    """Parse root cause, category, and claims from an LLM diagnosis response.

    Accepts both the canonical ``ROOT_CAUSE:`` labels and the markdown headings
    the investigation prompt asks for (``**Root cause**:``).
    """
    response = _normalize_section_labels(response)
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
