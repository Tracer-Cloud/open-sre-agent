"""The ``project[/cluster]`` grammar bounding GKE registration."""

from __future__ import annotations

import pytest

from integrations.gcp.gke.scope import (
    ANY,
    ClusterScope,
    ScopeSpec,
    parse_scopes,
    scopes_from_cluster_names,
)

# --- parsing -----------------------------------------------------------------


def test_a_bare_project_admits_every_cluster_in_it() -> None:
    spec = parse_scopes("prod-a")

    assert spec.scopes == (ClusterScope("prod-a"),)
    assert spec.admits("prod-a", "anything")
    assert not spec.admits("prod-b", "anything")


def test_a_qualified_entry_admits_only_that_cluster() -> None:
    spec = parse_scopes("prod-a/checkout")

    assert spec.admits("prod-a", "checkout")
    assert not spec.admits("prod-a", "billing")
    assert not spec.admits("prod-b", "checkout")


def test_entries_are_a_union_not_an_intersection() -> None:
    """``prod-a/checkout,prod-b`` reads as it looks: one cluster, then all of a project."""
    spec = parse_scopes("prod-a/checkout,prod-b")

    assert spec.admits("prod-a", "checkout")
    assert not spec.admits("prod-a", "billing")
    assert spec.admits("prod-b", "billing")


def test_a_wildcard_project_finds_the_cluster_wherever_it_lives() -> None:
    spec = parse_scopes("*/checkout")

    assert spec.admits("prod-a", "checkout")
    assert spec.admits("prod-b", "checkout")
    assert not spec.admits("prod-a", "billing")


def test_matching_ignores_case_and_padding() -> None:
    spec = parse_scopes("  Prod-A / Checkout ")

    assert spec.admits("PROD-A", "  checkout ")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("prod-a/", ClusterScope("prod-a")),
        ("/checkout", ClusterScope(ANY, "checkout")),
    ],
)
def test_a_blank_half_widens_rather_than_narrows(raw: str, expected: ClusterScope) -> None:
    """Guessing wide is the safe direction for a filter, and guessing is unavoidable here.

    This is parsed on a daemon thread at process start, so raising would surface
    nowhere. Widening at worst registers what an unqualified value already would;
    narrowing would silently register nothing at all.
    """
    assert parse_scopes(raw).scopes == (expected,)


@pytest.mark.parametrize("raw", ["", "   ", ",,", " , , "])
def test_nothing_usable_parses_to_an_unscoped_spec(raw: str) -> None:
    """Unscoped is "no filter", which is what registration has always defaulted to.

    Refusing to register anything here would turn a value that parsed to nothing
    into a silent no-op — the failure this grammar exists to prevent.
    """
    spec = parse_scopes(raw)

    assert spec.scopes == ()
    assert spec.admits("any-project", "any-cluster")
    assert spec.project_selector == ""


def test_duplicate_entries_collapse() -> None:
    assert parse_scopes("prod-a,prod-a,prod-a").scopes == (ClusterScope("prod-a"),)


# --- project selector --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("prod-a", "prod-a"),
        ("prod-a/checkout", "prod-a"),
        ("prod-a/checkout,prod-b/billing", "prod-a,prod-b"),
        ("prod-b,prod-a", "prod-b,prod-a"),
        ("*", "*"),
        ("*/checkout", "*"),
        ("prod-a,*/checkout", "*"),
    ],
)
def test_project_selector_is_derived_from_the_scopes(raw: str, expected: str) -> None:
    """Discovery and filtering must not be able to disagree about projects.

    A caller passing its own project list alongside a spec could sweep projects
    the filter then rejects wholesale, and the only symptom is "registered 0"
    with nothing in the log explaining it.
    """
    assert parse_scopes(raw).project_selector == expected


def test_a_project_named_once_per_cluster_is_swept_once() -> None:
    assert parse_scopes("prod-a/checkout,prod-a/billing").project_selector == "prod-a"


# --- CLI flags ---------------------------------------------------------------


def test_cluster_flags_do_not_choose_projects() -> None:
    """``--project`` already did; deriving them again would give two answers."""
    spec = scopes_from_cluster_names(("checkout", "billing"))

    assert all(scope.project == ANY for scope in spec.scopes)
    assert spec.admits("whatever-project", "checkout")
    assert not spec.admits("whatever-project", "payments")


def test_a_slash_in_a_cluster_flag_is_a_typo_not_a_qualifier() -> None:
    """Reinterpreting it would point the command at a project nobody named."""
    spec = scopes_from_cluster_names(("prod-a/checkout",))

    assert spec.scopes == (ClusterScope(ANY, "prod-a/checkout"),)
    assert not spec.admits("prod-a", "checkout")


def test_blank_cluster_flags_are_dropped() -> None:
    assert scopes_from_cluster_names(("", "  ")).scopes == ()


def test_names_clusters_reports_whether_anything_is_narrowed() -> None:
    assert parse_scopes("prod-a/checkout").names_clusters
    assert not parse_scopes("prod-a").names_clusters
    assert not ScopeSpec().names_clusters
