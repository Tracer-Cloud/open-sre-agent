"""Characterization tests for the root-cause text parser.

These lock the exact extraction behavior so the parser can be refactored for
readability without changing what it produces.
"""

from __future__ import annotations

from core.domain.types.root_cause_categories import VALID_ROOT_CAUSE_CATEGORIES
from core.llm.parsers.root_cause import _extract_category, parse_root_cause

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


class TestCategoryProseForms:
    """A category written in prose must still resolve to its canonical name.

    The model is asked for a canonical category but often writes it the way it
    reads in the report ("resource exhaustion"). Dropping that to ``unknown``
    loses the diagnosis label while the narrative still names the cause, so
    incident records, dashboards, and memory all see ``unknown``.
    """

    def test_space_separated_category_resolves(self) -> None:
        # Arrange / Act
        category = _extract_category("ROOT_CAUSE_CATEGORY:\nresource exhaustion")

        # Assert
        assert category == "resource_exhaustion"

    def test_multi_word_narrow_category_resolves(self) -> None:
        assert _extract_category("ROOT_CAUSE_CATEGORY:\npod oomkilled") == "pod_oomkilled"

    def test_longest_match_wins_over_a_contained_one(self) -> None:
        """``dual resource exhaustion`` must not degrade to ``resource_exhaustion``."""
        category = _extract_category("ROOT_CAUSE_CATEGORY:\ndual resource exhaustion")
        assert category == "dual_resource_exhaustion"

    def test_hyphenated_category_resolves(self) -> None:
        assert _extract_category("ROOT_CAUSE_CATEGORY:\nresource-exhaustion") == (
            "resource_exhaustion"
        )

    def test_underscored_form_still_wins(self) -> None:
        """Existing behavior is preserved for the canonical spelling."""
        assert _extract_category("ROOT_CAUSE_CATEGORY:\npod_oomkilled") == "pod_oomkilled"

    def test_unrecognized_wording_stays_unknown(self) -> None:
        """No fuzzy guessing — an unmapped name must not be invented."""
        assert _extract_category("ROOT_CAUSE_CATEGORY:\ngremlins in the rack") == "unknown"


class TestCategoryNegationSafety:
    """A rejected or hypothetical category must never become the verdict.

    The prompt asks for exactly one category name, so a prose form is only
    trustworthy when the whole line *is* that name. Matching a category
    mentioned inside a sentence lets an explicitly ruled-out cause be persisted
    as canonical data.
    """

    def test_negated_category_is_not_selected(self) -> None:
        # Arrange / Act
        category = _extract_category("ROOT_CAUSE_CATEGORY:\nThis is not resource exhaustion.")

        # Assert
        assert category == "unknown"

    def test_ruled_out_alternative_is_not_selected(self) -> None:
        category = _extract_category(
            "ROOT_CAUSE_CATEGORY:\nresource exhaustion was ruled out by the metrics"
        )
        assert category == "unknown"

    def test_hypothetical_phrasing_is_not_selected(self) -> None:
        category = _extract_category(
            "ROOT_CAUSE_CATEGORY:\nif it were pod oomkilled we would see exit 137"
        )
        assert category == "unknown"

    def test_bare_prose_category_still_resolves(self) -> None:
        """The whole line being the name stays supported — that is the fix's point."""
        assert _extract_category("ROOT_CAUSE_CATEGORY:\nresource exhaustion") == (
            "resource_exhaustion"
        )

    def test_decorated_line_still_resolves(self) -> None:
        """Markdown and bullets are formatting, not a sentence."""
        assert _extract_category("ROOT_CAUSE_CATEGORY:\n- **pod oomkilled**") == ("pod_oomkilled")


class TestCanonicalNameNegationSafety:
    """Negation must also be respected when the model uses canonical names.

    The prompt asks for underscored taxonomy names, so ``Not pod_oomkilled;
    this is a code_defect`` is the *likely* phrasing. Mid-sentence taxonomy
    tokens are never trusted — only a whole-line verdict resolves.
    """

    def test_leading_negation_with_canonical_names(self) -> None:
        assert (
            _extract_category("ROOT_CAUSE_CATEGORY:\nNot pod_oomkilled; this is a code_defect")
            == "unknown"
        )

    def test_ruled_out_with_canonical_names(self) -> None:
        assert (
            _extract_category("ROOT_CAUSE_CATEGORY:\npod_oomkilled ruled out; actual: code_defect")
            == "unknown"
        )

    def test_trailing_qualifier_still_resolves(self) -> None:
        """A clean verdict with a parenthetical must keep working."""
        assert (
            _extract_category("ROOT_CAUSE_CATEGORY:\ncpu_saturation (high confidence)")
            == "cpu_saturation"
        )

    def test_category_whose_name_contains_not_still_resolves(self) -> None:
        """``node_not_ready`` must not be mistaken for a negated line."""
        assert _extract_category("ROOT_CAUSE_CATEGORY:\nnode_not_ready") == "node_not_ready"

    def test_verdict_plus_parenthetical_rejection_yields_unknown(self) -> None:
        """Accepted trade-off: recall lost, correctness kept.

        ``code_defect (not pod_oomkilled)`` is not a sole whole-line verdict
        after decoration strip (``not`` remains), so it yields ``unknown``.
        Storing ``unknown`` is recoverable; storing a mid-sentence guess is not.
        """
        assert (
            _extract_category("ROOT_CAUSE_CATEGORY:\ncode_defect (not pod_oomkilled)") == "unknown"
        )

    def test_unrecognized_rejection_wording_does_not_persist_category(self) -> None:
        """Mid-sentence matches are never trusted — wording does not matter."""
        assert _extract_category("ROOT_CAUSE_CATEGORY:\npod_oomkilled is incorrect") == "unknown"
        assert _extract_category("ROOT_CAUSE_CATEGORY:\ncannot be resource_exhaustion") == "unknown"
        assert _extract_category("ROOT_CAUSE_CATEGORY:\nThis cannot be pod_oomkilled") == "unknown"
        assert (
            _extract_category("ROOT_CAUSE_CATEGORY:\nresource_exhaustion is wrong; code_defect")
            == "unknown"
        )
        assert _extract_category("ROOT_CAUSE_CATEGORY:\npod_oomkilled is bogus here") == "unknown"


class TestUnenumeratedRejectionWording:
    """Any residual English on the line blocks a category verdict.

    No rejection-word dictionary and no mid-sentence token scan — prose with
    one or two category names yields ``unknown``.
    """

    def test_prose_with_alternative_stays_unknown(self) -> None:
        """Safety over recall: the conclusion is lost, but nothing wrong is stored."""
        assert (
            _extract_category(
                "ROOT_CAUSE_CATEGORY:\npod_oomkilled is incorrect; actual code_defect"
            )
            == "unknown"
        )

    def test_unenumerated_prose_stays_unknown(self) -> None:
        assert (
            _extract_category("ROOT_CAUSE_CATEGORY:\npod_oomkilled superseded by code_defect")
            == "unknown"
        )

    def test_two_category_prose_stays_unknown(self) -> None:
        assert (
            _extract_category("ROOT_CAUSE_CATEGORY:\npod_oomkilled ; final answer code_defect")
            == "unknown"
        )

    def test_label_prefixed_line_still_resolves(self) -> None:
        """Pre-existing shape: a label followed by the name."""
        assert _extract_category("ROOT_CAUSE_CATEGORY:\ncategory -> delivery_hang") == (
            "delivery_hang"
        )

    def test_trailing_qualifier_still_resolves(self) -> None:
        assert (
            _extract_category("ROOT_CAUSE_CATEGORY:\ncpu_saturation (high confidence)")
            == "cpu_saturation"
        )


def test_affirmative_qualifier_keeps_the_verdict() -> None:
    """``confirmed`` affirms the category — it must not block the verdict."""
    assert _extract_category("ROOT_CAUSE_CATEGORY:\nnode_not_ready (confirmed)") == (
        "node_not_ready"
    )


def test_affirmative_qualifier_does_not_rescue_a_second_category() -> None:
    """Two categories on one line still yield nothing, qualifier or not."""
    assert (
        _extract_category("ROOT_CAUSE_CATEGORY:\npod_oomkilled confirmed, code_defect") == "unknown"
    )


class TestMarkdownHeadings:
    """The conclusion format the investigation prompt actually asks for.

    ``ROOT_CAUSE:`` appears in no prompt in the repo — the investigation asks
    for ``**Root cause**:``. Before this, every fallback parse of a real
    conclusion matched nothing and the report said "Unable to determine root
    cause" over a complete, correct diagnosis.
    """

    def test_prompt_format_conclusion_parses_into_every_field(self) -> None:
        # Arrange
        response = """Triage complete: 2 pods restarting in namespace `acme-web`.

**Root cause**: The `example-api` deployment caps memory at 256Mi while the
container's steady-state footprint is ~310Mi, so the kubelet kills it on warm-up.

**Root cause category**: pod_oomkilled

**Evidence**: kubernetes_describe_pod, kubernetes_list_deployments

**Validated claims**:
- the last termination reports exit code 137
- the deployment spec caps memory at 256Mi

**Non-validated claims**:
- a recent image change raised the baseline footprint

**Remediation steps**:
1. raise `resources.limits.memory` to 512Mi
2. re-run the load profile

**Validity score**: 0.9
"""

        # Act
        result = parse_root_cause(response)

        # Assert
        assert result.root_cause_category == "pod_oomkilled"
        assert result.root_cause.startswith("The `example-api` deployment caps memory")
        assert result.validated_claims == [
            "the last termination reports exit code 137",
            "the deployment spec caps memory at 256Mi",
        ]
        assert result.non_validated_claims == [
            "a recent image change raised the baseline footprint"
        ]
        # "Validity score" is not a result field, but it must still terminate the
        # remediation list rather than becoming the last step.
        assert result.remediation_steps == [
            "1. raise `resources.limits.memory` to 512Mi",
            "2. re-run the load profile",
        ]

    def test_atx_heading_without_a_colon_is_recognized(self) -> None:
        """``## Root cause`` on its own line is a heading; mid-prose text is not."""
        response = (
            "## Root cause\nthe node ran out of memory\n\n## Root cause category\npod_oomkilled\n"
        )

        result = parse_root_cause(response)

        assert result.root_cause == "the node ran out of memory"
        assert result.root_cause_category == "pod_oomkilled"

    def test_a_sentence_mentioning_a_section_name_is_not_a_heading(self) -> None:
        """Only a heading line is rewritten — prose must not open a section."""
        result = parse_root_cause("We could not establish the root cause from the evidence.")

        assert result.root_cause == "Unable to determine root cause"
