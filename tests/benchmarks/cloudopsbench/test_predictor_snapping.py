"""Tests for predictor vocabulary-snapping (Lever A).

The snapper rewrites LLM-emitted ``root_cause`` and ``fault_object`` tokens
onto the dataset's closed vocabulary before scoring, so near-miss
predictions (typos, missing prefixes, mixed casing) don't auto-fail the
strict exact-match scorer. These tests pin the BOUNDARIES of that snapping
so regressions get caught:

  - typo / spacing / casing recovery MUST snap
  - distinct-concept tokens MUST NOT snap (the readiness↔liveness pair was
    specifically observed colliding at difflib ratio 0.889 — above the
    global 0.8 cutoff)
  - empty / out-of-vocabulary inputs MUST fall back to the cleaned form
    so the pre-snap behavior is preserved as a floor.

Empirical motivation: 06-05 11:46 run analysis surfaced 8/77 rank-1 OOV
predictions and a 5-occurrence ``readiness_probe_incorrect_timing`` →
``liveness_probe_incorrect_timing`` cross-concept snap that would silently
rewrite a possibly-correct novel diagnosis onto a sibling. The blocklist
mechanism here is the guard.
"""

from __future__ import annotations

import pytest

from tests.benchmarks.cloudopsbench.predictor import (
    _ROOT_CAUSE_SNAP_CUTOFF,
    _crosses_blocked_concept_boundary,
    _snap_fault_object,
    _snap_root_cause,
)

# --------------------------------------------------------------------------- #
# Root-cause snapping — typo / spacing / casing recovery                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Exact match (after lower + normalization) — short-circuits the difflib path
        ("oom_killed", "oom_killed"),
        ("OOM_Killed", "oom_killed"),
        # Spacing variants — re.sub maps whitespace and dashes to underscores
        ("liveness probe incorrect port", "liveness_probe_incorrect_port"),
        ("liveness-probe-incorrect-port", "liveness_probe_incorrect_port"),
        # Observed typo from the 06-05 run — must snap to its canonical
        ("missing_secrectbinding", "missing_secret_binding"),
        # Missing-word recovery (this is the load-bearing case that JUSTIFIES
        # the 0.8 cutoff; tightening to 0.88 would break it)
        ("network_packet_loss", "node_network_packet_loss"),
    ],
)
def test_snap_root_cause_recovers_typo_and_spacing_variants(raw: str, expected: str) -> None:
    """Pin the snaps the scorer relies on. The 0.8 cutoff is sized for these
    cases — if the constant moves up, ``network_packet_loss`` breaks first
    (difflib ratio 0.884)."""
    assert _snap_root_cause(raw) == expected


# --------------------------------------------------------------------------- #
# Cross-concept guard — the readiness↔liveness regression test                 #
# --------------------------------------------------------------------------- #


def test_snap_root_cause_refuses_cross_concept_jump_readiness_to_liveness() -> None:
    """The 11:46 run emitted ``readiness_probe_incorrect_timing`` 5 times
    at ranks 2-3. There's no canonical for it (readiness_probe_* canonicals
    are port + protocol, not timing). difflib ratio against
    ``liveness_probe_incorrect_timing`` is 0.889 — above the snap cutoff,
    but cross-concept. The blocklist must keep the input unchanged so the
    case isn't silently rewritten to the wrong probe type."""
    raw = "readiness_probe_incorrect_timing"
    # Confirm the difflib score IS above cutoff — otherwise the blocklist
    # isn't actually doing the work we think it is
    import difflib

    canonical = "liveness_probe_incorrect_timing"
    ratio = difflib.SequenceMatcher(None, raw, canonical).ratio()
    assert ratio >= _ROOT_CAUSE_SNAP_CUTOFF, (
        f"Test prelude broken: {raw!r} vs {canonical!r} ratio {ratio:.3f} is "
        f"already below the {_ROOT_CAUSE_SNAP_CUTOFF} cutoff, so the blocklist "
        f"isn't being exercised. Re-check or remove this test."
    )
    # With the blocklist active, the snap must fall back to the cleaned input
    assert _snap_root_cause(raw) == raw


def test_snap_root_cause_refuses_cross_concept_jump_liveness_to_readiness() -> None:
    """Symmetric: a hallucinated ``liveness_*`` variant whose only nearby
    canonical is a ``readiness_*`` token must also stay unsnapped."""
    raw = "liveness_probe_strangely_named"
    snapped = _snap_root_cause(raw)
    # Either it stayed as the cleaned input, OR it landed on another
    # liveness_* canonical (acceptable). What it must NOT do is land on a
    # readiness_* canonical.
    assert "readiness" not in snapped.lower()


# --------------------------------------------------------------------------- #
# Legitimate readiness ↔ readiness and liveness ↔ liveness still work          #
# --------------------------------------------------------------------------- #


def test_snap_root_cause_keeps_readiness_variants_within_concept() -> None:
    """The blocklist must not block snaps WITHIN the same concept — a
    ``readiness_*`` typo should still land on a ``readiness_*`` canonical."""
    # The master vocab has both readiness_probe_incorrect_protocol and
    # readiness_probe_incorrect_port — typo recovery within concept is fine
    assert _snap_root_cause("readiness_probe_incorrect_port") == "readiness_probe_incorrect_port"
    assert (
        _snap_root_cause("readiness_probe_incorrect_protocol")
        == "readiness_probe_incorrect_protocol"
    )


# --------------------------------------------------------------------------- #
# Namespace_* admission tokens pass through                                    #
# --------------------------------------------------------------------------- #


def test_snap_root_cause_passes_through_namespace_admission_tokens() -> None:
    """``namespace_<anything>`` is the open admission-fault family — the
    scorer maps the whole family to Admission_Fault. Snapping these onto a
    nearest canonical would lose the descriptive suffix."""
    for raw in [
        "namespace_false_alert",
        "namespace_quota_exceeded",
        "namespace_some_brand_new_reason",
    ]:
        assert _snap_root_cause(raw) == raw


# --------------------------------------------------------------------------- #
# Fallback path — totally novel tokens stay as cleaned input                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    [
        "completely_unrelated_token",
        "foo_bar_baz",
        "x",  # too short to match anything by ratio
    ],
)
def test_snap_root_cause_falls_back_to_cleaned_input_when_no_close_match(raw: str) -> None:
    """The whole-system invariant: snapping NEVER regresses a prediction
    that wasn't already wrong. If no canonical is close enough, the cleaned
    input is returned verbatim."""
    assert _snap_root_cause(raw) == raw


def test_snap_root_cause_empty_input_returns_empty() -> None:
    """Defensive: predictor LLM emitted a blank root_cause — caller decides
    what to do with it. Snapper must not crash or invent a value."""
    assert _snap_root_cause("") == ""
    assert _snap_root_cause("   ") == ""


# --------------------------------------------------------------------------- #
# _crosses_blocked_concept_boundary — direct unit                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("predicted", "snapped", "should_block"),
    [
        ("readiness_probe_incorrect_timing", "liveness_probe_incorrect_timing", True),
        ("liveness_probe_strange", "readiness_probe_strange", True),
        # Same concept — must NOT block
        ("readiness_probe_typo", "readiness_probe_incorrect_port", False),
        ("liveness_probe_typo", "liveness_probe_incorrect_port", False),
        # Neither concept involved — must NOT block
        ("oom_killed", "oom_killed", False),
        ("missing_secret_binding", "missing_secret_binding", False),
    ],
)
def test_blocked_concept_boundary_check(predicted: str, snapped: str, should_block: bool) -> None:
    assert _crosses_blocked_concept_boundary(predicted, snapped) is should_block


# --------------------------------------------------------------------------- #
# fault_object snapping                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Already canonical
        ("app/paymentservice", "app/paymentservice"),
        ("node/worker-01", "node/worker-01"),
        ("namespace/boutique", "namespace/boutique"),
        # Missing prefix — inferred from known node / namespace / service names
        ("worker-01", "node/worker-01"),
        ("boutique", "namespace/boutique"),
        ("paymentservice", "app/paymentservice"),
        # Unknown name — defaults to "app" prefix (the common case)
        ("some-novel-service", "app/some-novel-service"),
        # Casing normalization on known tokens
        ("APP/PaymentService", "app/paymentservice"),
        # Whitespace handling
        ("  app / paymentservice  ", "app/paymentservice"),
    ],
)
def test_snap_fault_object_normalizes_prefix_and_casing(raw: str, expected: str) -> None:
    """fault_object snapping is more conservative than root_cause — it only
    canonicalizes on EXACT normalized match (no difflib). The 11:46
    analysis showed object localization was a separate failure mode from
    label drift, so aggressive snapping here could mask real localization
    errors. Document the boundary explicitly."""
    assert _snap_fault_object(raw) == expected


def test_snap_fault_object_does_not_fuzzy_snap_novel_service_name() -> None:
    """``some-novel-service`` could be similar to a real service by
    difflib ratio — but fault_object snapping refuses fuzzy matches. The
    novel name stays under the inferred ``app/`` prefix so localization
    accuracy reflects the agent's actual choice."""
    snapped = _snap_fault_object("paymentservice-typo")
    # Should NOT silently rewrite to "app/paymentservice" — keeps the typo
    # so the localization error is visible in the score
    assert snapped == "app/paymentservice-typo"


def test_snap_fault_object_empty_input_returns_empty() -> None:
    assert _snap_fault_object("") == ""
    assert _snap_fault_object("   ") == ""
