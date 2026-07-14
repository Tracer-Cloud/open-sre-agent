"""Tests for Sentry issue clustering helpers."""

from __future__ import annotations

from integrations.sentry.issue_clustering import (
    structural_cluster_key_for_issue,
    structural_cluster_label,
)


def test_structural_cluster_key_uses_integration_package() -> None:
    assert (
        structural_cluster_key_for_issue(
            {"culprit": "integrations.datadog.client in list_monitors"}
        )
        == "integrations.datadog.client"
    )
    assert (
        structural_cluster_key_for_issue(
            {"culprit": "integrations.eks.eks_k8s_client in build_k8s_clients"}
        )
        == "integrations.eks.eks_k8s_client"
    )


def test_structural_cluster_key_uses_title_theme_before_project() -> None:
    assert (
        structural_cluster_key_for_issue(
            {
                "title": "[cloudtrail] lookup_events failed region=us-east-1",
                "project": {"slug": "python"},
                "culprit": "",
            }
        )
        == "title-theme:cloudtrail"
    )


def test_structural_cluster_key_uses_issue_group_prefix() -> None:
    assert (
        structural_cluster_key_for_issue({"shortId": "TRACER-CLIENT-4C", "culprit": ""})
        == "issue-group:tracer-client"
    )


def test_structural_cluster_label_uses_generic_integration_name() -> None:
    assert structural_cluster_label("integrations.datadog.client") == "Datadog integration errors"


def test_structural_cluster_label_uses_prefix_override_for_eks() -> None:
    assert structural_cluster_label("integrations.eks.eks_k8s_client") == "EKS / Kubernetes errors"


def test_structural_cluster_label_includes_sample_title() -> None:
    label = structural_cluster_label(
        "core.llm",
        sample_titles=("LLMSettings has no attribute ollama_toolcall_model",),
    )
    assert label.startswith("LLM runtime / provider errors — e.g.")
