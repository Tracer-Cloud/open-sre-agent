"""Verification must name every registered instance, not only the probed one.

Regression: a deployment with eight registered Kubernetes clusters reported
"one cluster connected", because the verifier only ever sees the default
instance's config. An operator reading ``/integrations show kubernetes`` — or
an agent quoting that line back into a chat thread — then reports one cluster.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest

from integrations.probes import ProbeResult
from integrations.verification.instances import (
    MAX_LISTED_INSTANCES,
    with_instance_inventory,
)
from integrations.verify import verify_integrations


@pytest.fixture(autouse=True)
def clean_kubernetes_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Drop ambient KUBE* vars so only what a test sets reaches classification."""
    for key in list(os.environ):
        if key.startswith(("KUBE", "KUBERNETES")):
            monkeypatch.delenv(key, raising=False)
    yield


def _outcome(detail: str = "Connected.") -> dict[str, str]:
    return {
        "service": "kubernetes",
        "source": "local env",
        "status": "passed",
        "detail": detail,
    }


def _entry(*names: str) -> dict[str, Any]:
    return {
        "source": "local env",
        "config": {"kubeconfig": "kc"},
        "instances": [{"name": name, "tags": {}, "config": {}} for name in names],
    }


def test_single_instance_detail_is_untouched() -> None:
    """The common case must read exactly as it did before."""
    outcome = _outcome()

    assert with_instance_inventory(outcome, _entry("default")) == outcome
    assert with_instance_inventory(outcome, {"source": "local env", "config": {}}) == outcome


def test_multi_instance_detail_names_all_and_says_which_was_probed() -> None:
    annotated = with_instance_inventory(_outcome(), _entry("cluster-a", "cluster-b", "cluster-c"))

    detail = annotated["detail"]
    assert detail.startswith("Connected.")
    assert "3 instances registered (cluster-a, cluster-b, cluster-c)" in detail
    assert "probed 'cluster-a' only" in detail
    # Status and the rest of the row are the probe's to decide, not ours.
    assert annotated["status"] == "passed"
    assert annotated["service"] == "kubernetes"


def test_failed_probe_still_reports_the_inventory() -> None:
    """Eight registered clusters is worth knowing even when the probed one is down."""
    failure = {**_outcome("HTTP 401: unauthorized"), "status": "failed"}

    detail = with_instance_inventory(failure, _entry("cluster-a", "cluster-b"))["detail"]

    assert "unauthorized" in detail
    assert "2 instances registered" in detail


def test_long_estate_is_capped_and_says_how_many_were_dropped() -> None:
    names = [f"cluster-{index}" for index in range(MAX_LISTED_INSTANCES + 3)]

    detail = with_instance_inventory(_outcome(), _entry(*names))["detail"]

    assert f"{len(names)} instances registered" in detail
    assert "+3 more" in detail
    assert names[MAX_LISTED_INSTANCES] not in detail


def test_verify_integrations_reports_every_registered_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the instances published by classification reach the row.

    ``_verify_one`` passes only ``integration["config"]`` to the verifier, so
    without the annotation step the sibling instances are dropped between
    classification and the rendered detail — silently, with a passing probe.
    """
    from integrations.kubernetes.client import KubernetesClient

    monkeypatch.setattr("integrations.catalog.load_integrations", lambda: [])
    monkeypatch.setenv(
        "KUBERNETES_INSTANCES",
        json.dumps(
            [
                {"name": "cluster-a", "credentials": {"kubeconfig": "kc-a"}},
                {"name": "cluster-b", "credentials": {"kubeconfig": "kc-b"}},
            ]
        ),
    )
    monkeypatch.setattr(
        KubernetesClient,
        "probe_access",
        lambda _self: ProbeResult.passed("Connected to Kubernetes cluster."),
    )

    results = verify_integrations("kubernetes")

    assert len(results) == 1
    assert results[0]["status"] == "passed"
    assert "2 instances registered (cluster-a, cluster-b)" in results[0]["detail"]
