"""Characterization tests for the root-cause text parser.

These lock the exact extraction behavior so the parser can be refactored for
readability without changing what it produces.
"""

from __future__ import annotations

from core.domain.types.root_cause_categories import VALID_ROOT_CAUSE_CATEGORIES
from core.llm.parsers.root_cause import parse_root_cause

_CATEGORY = sorted(VALID_ROOT_CAUSE_CATEGORIES)[0]  # a deterministic valid category


def test_extracts_every_field_from_a_full_response() -> None:
    response = f"""ROOT_CAUSE_CATEGORY: {_CATEGORY}
ROOT_CAUSE: The database ran out of connections under load.
VALIDATED_CLAIMS:
* connection pool maxed at 100
- CPU hit 95 percent
NON_VALIDATED_CLAIMS:
* possible memory leak
CAUSAL_CHAIN:
* traffic spike
* pool exhausted
REMEDIATION_STEPS:
1. increase pool size
2. add read replica
"""
    result = parse_root_cause(response)

    assert result.root_cause_category == _CATEGORY
    assert result.root_cause == "The database ran out of connections under load."
    assert result.validated_claims == ["connection pool maxed at 100", "CPU hit 95 percent"]
    assert result.non_validated_claims == ["possible memory leak"]
    assert result.causal_chain == ["traffic spike", "pool exhausted"]
    assert result.remediation_steps == ["1. increase pool size", "2. add read replica"]


def test_defaults_when_no_markers_present() -> None:
    result = parse_root_cause("free text with no markers")

    assert result.root_cause == "Unable to determine root cause"
    assert result.root_cause_category == "unknown"
    assert result.validated_claims == []
    assert result.non_validated_claims == []
    assert result.causal_chain == []
    assert result.remediation_steps == []


def test_root_cause_text_stops_at_the_next_section() -> None:
    result = parse_root_cause("ROOT_CAUSE: the cause\nVALIDATED_CLAIMS:\n* c1")

    assert result.root_cause == "the cause"
    assert result.validated_claims == ["c1"]


def test_category_extracted_when_not_the_first_token() -> None:
    result = parse_root_cause(
        "ROOT_CAUSE_CATEGORY:\nroot_cause_category: agent_hang\nROOT_CAUSE: test"
    )
    assert result.root_cause_category == "agent_hang"


def test_category_extracted_from_arrow_format() -> None:
    result = parse_root_cause("ROOT_CAUSE_CATEGORY:\ncategory -> delivery_hang\nROOT_CAUSE: test")
    assert result.root_cause_category == "delivery_hang"


def test_remediation_stops_at_a_trailing_section_header() -> None:
    result = parse_root_cause(
        "ROOT_CAUSE: x\nREMEDIATION_STEPS:\n1. do this\nALTERNATIVE: not a step"
    )

    assert result.remediation_steps == ["1. do this"]
