"""Tests for ``kubernetes_search_fleet`` and its two client methods.

Two layers are covered deliberately:

* the **tool** (`KubernetesSearchFleetTool.run`) — fan-out, deadline, bucket
  discipline, payload shape — with the per-cluster helpers faked;
* the **client** (`KubernetesClient.search_workload_owners` / `search_pods`) —
  namespace selection, truncation derivation and CRD degradation — with the
  Kubernetes API objects faked.

Patch targets are `integrations.kubernetes.tools.fleet_search.*`, never the
package attribute: the submodule binds `_resolve_client` into its own namespace
at import, so patching `integrations.kubernetes.tools._resolve_client` would
leave this tool untouched and the test would pass vacuously.
"""

from __future__ import annotations

import threading
import time
from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException

from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope, current_scope
from integrations.kubernetes.tools.fleet_search import (
    _FLEET_SEARCH_DEADLINE_SECONDS,
    _MAX_PARALLEL_CLUSTERS,
    KubernetesSearchFleetTool,
    _search_one_cluster_pods,
    _search_one_cluster_workloads,
)
from tests.tools.test_kubernetes_tools import _MINIMAL_KUBECONFIG, _make_client_with_apis

_MATCH_KEYS = {"cluster", "namespace", "kind", "name", "ready", "desired", "phase"}


def _conn(namespace: str = "default") -> dict[str, Any]:
    """A connection map that would really build a client."""
    return {
        "kubeconfig": _MINIMAL_KUBECONFIG,
        "kubeconfig_path": "",
        "context": "",
        "namespace": namespace,
    }


def _ok(
    cluster: str,
    matches: list[dict[str, Any]] | None = None,
    *,
    truncated_kinds: list[str] | None = None,
    unavailable_kinds: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """A successful per-cluster helper result in the helper's real shape."""
    return {
        "success": True,
        "matches": [{"cluster": cluster, **row} for row in (matches or [])],
        "truncated_kinds": truncated_kinds or [],
        "unavailable_kinds": unavailable_kinds or [],
    }


def _row(name: str, *, kind: str = "Deployment", namespace: str = "prod") -> dict[str, Any]:
    return {
        "namespace": namespace,
        "kind": kind,
        "name": name,
        "ready": 1,
        "desired": 1,
        "phase": None,
    }


@pytest.fixture
def tool() -> KubernetesSearchFleetTool:
    return KubernetesSearchFleetTool()


@pytest.fixture
def available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "integrations.kubernetes.tools.fleet_search._is_available", lambda _sources: True
    )


def _patch_workloads(monkeypatch: pytest.MonkeyPatch, fn: Any) -> None:
    monkeypatch.setattr(
        "integrations.kubernetes.tools.fleet_search._search_one_cluster_workloads", fn
    )


def _patch_pods(monkeypatch: pytest.MonkeyPatch, fn: Any) -> None:
    monkeypatch.setattr("integrations.kubernetes.tools.fleet_search._search_one_cluster_pods", fn)


# ---------------------------------------------------------------------------
# Tool: availability and cluster selection
# ---------------------------------------------------------------------------


def test_unavailable_when_no_kubeconfig(tool: KubernetesSearchFleetTool) -> None:
    """No kubeconfig at all is a tool-level unavailable, not an empty result."""
    result = tool.run(name_contains="anything")

    assert result["available"] is False
    assert "error" in result


def test_an_unknown_cluster_name_lists_the_valid_ones(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan test 15.

    ``_resolve_client`` is deliberately NOT patched: the point is that the real
    resolver's contract survives the fan-out wrapper. A silent fallback to the
    default cluster would search the wrong place and report success.
    """
    searched: list[str] = []

    def _record(cluster_name: str, *_args: Any) -> dict[str, Any]:
        searched.append(cluster_name)
        return _ok(cluster_name)

    _patch_workloads(monkeypatch, _record)

    result = tool.run(
        name_contains="svc",
        cluster="nope",
        kubeconfig=_MINIMAL_KUBECONFIG,
        cluster_configs={"gke-a": _conn(), "gke-b": _conn()},
    )

    assert result["available"] is False
    assert "nope" in result["error"]
    assert "gke-a" in result["error"] and "gke-b" in result["error"]
    assert searched == []


# ---------------------------------------------------------------------------
# Tool: the D7 trap
# ---------------------------------------------------------------------------


def test_search_covers_every_namespace_not_the_configured_default(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan test 6 / D7.

    Every auto-registered cluster stores ``namespace: "default"``. Routing the
    empty argument through ``_effective_namespace`` would search one usually
    empty namespace per cluster and return a confident fleet-wide zero.
    """
    seen: list[tuple[str, str]] = []

    def _record(cluster_name: str, _conn_map: Any, _needle: str, namespace: str) -> dict[str, Any]:
        seen.append((cluster_name, namespace))
        return _ok(cluster_name)

    _patch_workloads(monkeypatch, _record)
    _patch_pods(monkeypatch, lambda *_a: {"success": True, "matches": [], "truncated_kinds": []})

    tool.run(
        name_contains="svc",
        namespace="",
        kubeconfig=_MINIMAL_KUBECONFIG,
        default_namespace="default",
        cluster_configs={"gke-a": _conn("default"), "gke-b": _conn("kube-system")},
    )

    assert sorted(seen) == [("gke-a", ""), ("gke-b", "")]


def test_an_explicit_namespace_is_still_honoured(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The D7 avoidance must not become "ignore the namespace argument"."""
    seen: list[str] = []

    def _record(cluster_name: str, _conn_map: Any, _needle: str, namespace: str) -> dict[str, Any]:
        seen.append(namespace)
        return _ok(cluster_name, [_row("svc-api", namespace="payments")])

    _patch_workloads(monkeypatch, _record)

    tool.run(
        name_contains="svc",
        namespace="  payments  ",
        kubeconfig=_MINIMAL_KUBECONFIG,
        cluster_configs={"gke-a": _conn("default")},
    )

    assert seen == ["payments"]


# ---------------------------------------------------------------------------
# Tool: deadline and bucket discipline
# ---------------------------------------------------------------------------


def test_a_timed_out_cluster_is_reported_not_dropped(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan test 7. A slow cluster must never read as "searched, nothing found"."""

    def _slow(cluster_name: str, *_args: Any) -> dict[str, Any]:
        time.sleep(3.0)
        return _ok(cluster_name)

    _patch_workloads(monkeypatch, _slow)
    monkeypatch.setattr(
        "integrations.kubernetes.tools.fleet_search._FLEET_SEARCH_DEADLINE_SECONDS", 0.1
    )

    result = tool.run(
        name_contains="svc",
        kubeconfig=_MINIMAL_KUBECONFIG,
        cluster_configs={"slow": _conn()},
    )

    assert result["clusters_searched"] == []
    assert result["clusters_failed"] == [{"cluster": "slow", "reason": "search deadline exceeded"}]
    assert result["partial"] is True


def test_the_deadline_returns_without_joining_a_wedged_worker(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deadline is a wall-clock budget, not a label on a result.

    ``shutdown(wait=False, cancel_futures=True)`` cannot interrupt a thread
    already inside a socket read, so the only thing that keeps the turn alive is
    *not waiting* for it. Turning the shutdown into a blocking join reintroduces
    the multi-minute hang the deadline exists to prevent, and no assertion on
    the payload can see it — only the clock can.
    """
    wedged = 3.0

    def _wedged(cluster_name: str, *_args: Any) -> dict[str, Any]:
        time.sleep(wedged)
        return _ok(cluster_name)

    _patch_workloads(monkeypatch, _wedged)
    monkeypatch.setattr(
        "integrations.kubernetes.tools.fleet_search._FLEET_SEARCH_DEADLINE_SECONDS", 0.1
    )

    started = time.monotonic()
    result = tool.run(
        name_contains="svc",
        kubeconfig=_MINIMAL_KUBECONFIG,
        cluster_configs={"wedged": _conn()},
    )
    elapsed = time.monotonic() - started

    assert result["partial"] is True
    # Generous margin: the deadline is 0.1s and the worker sleeps 3s, so any
    # join at all lands well past 1.5s.
    assert elapsed < 1.5, f"run() joined the wedged worker: {elapsed:.2f}s"


def test_every_selected_cluster_appears_in_exactly_one_bucket(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan test 8. The anti-silent-drop invariant, independent of failure kind."""
    selected = {"ok": _conn(), "boom": _conn(), "slow": _conn(), "raiser": _conn()}

    def _mixed(cluster_name: str, *_args: Any) -> dict[str, Any]:
        if cluster_name == "boom":
            return {
                "success": False,
                "error": "(500) Reason: Internal Server Error",
                "matches": [],
                "truncated_kinds": [],
                "unavailable_kinds": [],
            }
        if cluster_name == "slow":
            time.sleep(3.0)
        if cluster_name == "raiser":
            raise RuntimeError("worker exploded")
        return _ok(cluster_name, [_row("svc-api")])

    _patch_workloads(monkeypatch, _mixed)
    monkeypatch.setattr(
        "integrations.kubernetes.tools.fleet_search._FLEET_SEARCH_DEADLINE_SECONDS", 0.5
    )

    result = tool.run(name_contains="svc", kubeconfig=_MINIMAL_KUBECONFIG, cluster_configs=selected)

    searched = set(result["clusters_searched"])
    failed = {entry["cluster"] for entry in result["clusters_failed"]}
    assert searched.isdisjoint(failed)
    assert searched | failed == set(selected)
    assert len(result["clusters_searched"]) + len(result["clusters_failed"]) == len(selected)
    assert result["partial"] is True


# ---------------------------------------------------------------------------
# Tool: the fan-out really runs in parallel, and carries the caller's scope
# ---------------------------------------------------------------------------


def _overlapping_clusters(count: int) -> dict[str, dict[str, Any]]:
    return {f"gke-{index}": _conn() for index in range(count)}


def test_every_cluster_searches_even_when_the_workers_genuinely_overlap(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shared ``contextvars.Context`` admits one worker and fails the rest.

    ``Context.run`` marks the context entered for the duration of the call, so a
    second *concurrent* ``run`` on the same object raises ``RuntimeError: cannot
    enter context``. Live, that reported one cluster searched and sixteen failed.

    The barrier is the whole point: every other test in this file fakes helpers
    that return instantly, so the workers never actually overlap and a shared
    context is entered and exited one at a time. Sizing the barrier to the
    cluster count forces all of them to be inside ``run`` simultaneously.
    """
    selected = _overlapping_clusters(_MAX_PARALLEL_CLUSTERS)
    barrier = threading.Barrier(len(selected), timeout=10.0)

    def _rendezvous(cluster_name: str, *_args: Any) -> dict[str, Any]:
        barrier.wait()
        return _ok(cluster_name, [_row("svc-api")])

    _patch_workloads(monkeypatch, _rendezvous)

    result = tool.run(name_contains="svc", kubeconfig=_MINIMAL_KUBECONFIG, cluster_configs=selected)

    assert result["clusters_failed"] == []
    assert set(result["clusters_searched"]) == set(selected)
    assert result["partial"] is False


def test_the_callers_storage_scope_reaches_every_worker(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dropping the context propagation entirely also makes every cluster search.

    So the test above cannot stand alone: deleting ``copy_context`` would pass it
    while silently unbinding the turn's principal in every worker, which is how a
    multi-tenant read crosses tenants. The copy must also be taken on the
    *submitting* thread — copying inside the worker snapshots the pool thread's
    empty context and fails here for the same reason.
    """
    selected = _overlapping_clusters(_MAX_PARALLEL_CLUSTERS)
    barrier = threading.Barrier(len(selected), timeout=10.0)
    scope = StorageScope(principal=Principal.org("org_acme"), actor=Actor(id="U_ALICE"))
    seen: dict[str, StorageScope | None] = {}
    seen_lock = threading.Lock()

    def _record_scope(cluster_name: str, *_args: Any) -> dict[str, Any]:
        barrier.wait()
        with seen_lock:
            seen[cluster_name] = current_scope()
        return _ok(cluster_name, [_row("svc-api")])

    _patch_workloads(monkeypatch, _record_scope)

    with bound_storage_scope(scope):
        result = tool.run(
            name_contains="svc", kubeconfig=_MINIMAL_KUBECONFIG, cluster_configs=selected
        )

    assert result["clusters_failed"] == []
    assert set(seen) == set(selected)
    assert set(seen.values()) == {scope}


def test_a_phase_two_failure_moves_the_cluster_out_of_searched(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pod-listing failure is a cluster failure, not a silent empty result."""
    _patch_workloads(monkeypatch, lambda cname, *_a: _ok(cname))
    _patch_pods(
        monkeypatch,
        lambda _cname, *_a: {
            "success": False,
            "error": "(403) Reason: Forbidden",
            "matches": [],
            "truncated_kinds": [],
        },
    )

    result = tool.run(
        name_contains="svc", kubeconfig=_MINIMAL_KUBECONFIG, cluster_configs={"gke-a": _conn()}
    )

    assert result["clusters_searched"] == []
    assert result["clusters_failed"] == [{"cluster": "gke-a", "reason": "(403) Reason: Forbidden"}]
    assert result["pods_searched"] is True
    assert result["partial"] is True


# ---------------------------------------------------------------------------
# Tool: the two-phase heuristic
# ---------------------------------------------------------------------------


def test_pods_are_searched_only_when_no_owner_matches(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan test 11. Phase 2 lists whole clusters, so it must stay rare."""
    pod_calls: list[str] = []

    def _pods(cluster_name: str, *_args: Any) -> dict[str, Any]:
        pod_calls.append(cluster_name)
        return {"success": True, "matches": [], "truncated_kinds": []}

    _patch_pods(monkeypatch, _pods)

    # (a) an owner matches -> phase 2 never runs
    _patch_workloads(monkeypatch, lambda cname, *_a: _ok(cname, [_row("svc-api")]))
    hit = tool.run(
        name_contains="svc-api",
        kubeconfig=_MINIMAL_KUBECONFIG,
        cluster_configs={"gke-a": _conn()},
    )
    assert pod_calls == []
    assert hit["pods_searched"] is False

    # (b) nothing matches anywhere -> phase 2 runs
    _patch_workloads(monkeypatch, lambda cname, *_a: _ok(cname))
    miss = tool.run(
        name_contains="svc-api",
        kubeconfig=_MINIMAL_KUBECONFIG,
        cluster_configs={"gke-a": _conn()},
    )
    assert pod_calls == ["gke-a"]
    assert miss["pods_searched"] is True


def test_include_pods_forces_phase_two_even_when_an_owner_matches(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_workloads(monkeypatch, lambda cname, *_a: _ok(cname, [_row("svc-api")]))
    _patch_pods(
        monkeypatch,
        lambda cname, *_a: {
            "success": True,
            "matches": [{"cluster": cname, **_row("svc-api-6c8f-2xq", kind="Pod")}],
            "truncated_kinds": [],
        },
    )

    result = tool.run(
        name_contains="svc-api",
        include_pods=True,
        kubeconfig=_MINIMAL_KUBECONFIG,
        cluster_configs={"gke-a": _conn()},
    )

    assert result["pods_searched"] is True
    assert {match["kind"] for match in result["matches"]} == {"Deployment", "Pod"}


def test_a_full_pod_name_with_a_replicaset_hash_is_found(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan test 12.

    No owner name *contains* ``svc-api-6c8f9d7b4-2xqlp``, so phase 1 returns a
    fleet-wide zero and phase 2 has to self-heal. This is the property that
    makes replicaset-hash stripping unnecessary.
    """
    needle = "svc-api-6c8f9d7b4-2xqlp"

    def _owners(cluster_name: str, _conn_map: Any, name_contains: str, _ns: str) -> dict[str, Any]:
        rows = [_row("svc-api")] if name_contains.lower() in "svc-api" else []
        return _ok(cluster_name, rows)

    def _pods(cluster_name: str, _conn_map: Any, name_contains: str, _ns: str) -> dict[str, Any]:
        rows = [_row(needle, kind="Pod")] if name_contains in needle else []
        return {
            "success": True,
            "matches": [{"cluster": cluster_name, **row} for row in rows],
            "truncated_kinds": [],
        }

    _patch_workloads(monkeypatch, _owners)
    _patch_pods(monkeypatch, _pods)

    result = tool.run(
        name_contains=needle,
        kubeconfig=_MINIMAL_KUBECONFIG,
        cluster_configs={"gke-a": _conn()},
    )

    assert result["pods_searched"] is True
    assert [match["name"] for match in result["matches"]] == [needle]


# ---------------------------------------------------------------------------
# Tool: payload discipline
# ---------------------------------------------------------------------------


def test_matches_carry_no_labels_or_raw_objects(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan test 14. The client's row projectors emit more than the tool returns."""
    fat_row = {
        **_row("svc-api"),
        "labels": {"app": "svc-api"},
        "creation_timestamp": "2024-01-01T00:00:00Z",
        "containers": [{"name": "app", "image": "registry/app:1"}],
    }

    def _fat(cluster_name: str, *_args: Any) -> dict[str, Any]:
        return {
            "success": True,
            "matches": [
                _search_one_cluster_workloads.__globals__["_project_match"](cluster_name, fat_row)
            ],
            "truncated_kinds": [],
            "unavailable_kinds": [],
        }

    _patch_workloads(monkeypatch, _fat)

    result = tool.run(
        name_contains="svc", kubeconfig=_MINIMAL_KUBECONFIG, cluster_configs={"gke-a": _conn()}
    )

    assert result["matches"], "expected a match to inspect"
    for match in result["matches"]:
        assert set(match) == _MATCH_KEYS


def test_truncation_is_reported_per_kind_and_sets_partial(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``truncated_kinds`` names kinds, deduped across clusters — not cluster names."""
    _patch_workloads(
        monkeypatch,
        lambda cname, *_a: _ok(cname, [_row("svc-api")], truncated_kinds=["Deployment"]),
    )

    result = tool.run(
        name_contains="svc",
        kubeconfig=_MINIMAL_KUBECONFIG,
        cluster_configs={"gke-a": _conn(), "gke-b": _conn()},
    )

    assert result["truncated"] is True
    assert result["truncated_kinds"] == ["Deployment"]
    assert result["partial"] is True


def test_an_absent_crd_degrades_the_kind_and_keeps_the_answer_complete(
    tool: KubernetesSearchFleetTool, available: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan test 9 at the tool layer.

    "There are no Rollouts here" is a complete answer, so the cluster stays in
    ``clusters_searched`` and ``partial`` stays false.
    """
    _patch_workloads(
        monkeypatch,
        lambda cname, *_a: _ok(
            cname,
            [_row("svc-api")],
            unavailable_kinds=[
                {"cluster": cname, "kind": "Rollout", "reason": "Kubernetes API error 404"}
            ],
        ),
    )

    result = tool.run(
        name_contains="svc", kubeconfig=_MINIMAL_KUBECONFIG, cluster_configs={"gke-a": _conn()}
    )

    assert result["clusters_searched"] == ["gke-a"]
    assert result["clusters_failed"] == []
    assert result["partial"] is False
    assert result["unavailable_kinds"] == [
        {"cluster": "gke-a", "kind": "Rollout", "reason": "Kubernetes API error 404"}
    ]


# ---------------------------------------------------------------------------
# The per-cluster helpers
# ---------------------------------------------------------------------------


def test_a_client_that_cannot_be_built_reports_the_resolver_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason must survive.

    Collapsing every build failure into one generic string destroys the only
    thing an operator can act on.
    """

    def _refuse(_cluster: str, _configs: Any, _default: Any) -> tuple[None, dict[str, Any], str]:
        return None, {}, "connection failed: no credentials for this cluster"

    monkeypatch.setattr("integrations.kubernetes.tools.fleet_search._resolve_client", _refuse)

    workloads = _search_one_cluster_workloads("gke-a", {}, "svc", "")
    pods = _search_one_cluster_pods("gke-a", {}, "svc", "")

    assert workloads["success"] is False
    assert "connection failed" in workloads["error"]
    assert pods["success"] is False
    assert "connection failed" in pods["error"]


def test_the_helpers_project_the_clients_rows_onto_the_match_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helpers speak the *client's* contract, and drop everything else."""
    client = MagicMock()
    client.search_workload_owners.return_value = {
        "success": True,
        "workloads": [
            {
                "kind": "Deployment",
                "name": "svc-api",
                "namespace": "payments",
                "desired": 3,
                "ready": 2,
                "phase": None,
                "labels": {"app": "svc-api"},
            }
        ],
        "truncated": True,
        "truncated_kinds": ["Deployment"],
        "unavailable_kinds": [{"kind": "Rollout", "reason": "Kubernetes API error 404"}],
    }
    client.search_pods.return_value = {
        "success": True,
        "pods": [
            {
                "kind": "Pod",
                "name": "svc-api-6c8f-2xq",
                "namespace": "payments",
                "ready": 1,
                "desired": 1,
                "phase": "Running",
                "node": "node-1",
            }
        ],
        "truncated": True,
    }

    monkeypatch.setattr(
        "integrations.kubernetes.tools.fleet_search._resolve_client",
        lambda _cluster, _configs, conn: (client, conn, None),
    )

    workloads = _search_one_cluster_workloads("gke-a", {}, "svc", "")
    assert workloads["success"] is True
    assert set(workloads["matches"][0]) == _MATCH_KEYS
    assert workloads["matches"][0]["cluster"] == "gke-a"
    assert workloads["matches"][0]["namespace"] == "payments"
    assert workloads["truncated_kinds"] == ["Deployment"]
    assert workloads["unavailable_kinds"] == [
        {"cluster": "gke-a", "kind": "Rollout", "reason": "Kubernetes API error 404"}
    ]
    client.search_workload_owners.assert_called_once_with("svc", "")

    pods = _search_one_cluster_pods("gke-a", {}, "svc", "")
    assert set(pods["matches"][0]) == _MATCH_KEYS
    assert pods["matches"][0]["kind"] == "Pod"
    assert pods["truncated_kinds"] == ["Pod"]
    client.search_pods.assert_called_once_with("svc", "")


def test_a_client_error_reaches_the_caller_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.search_workload_owners.return_value = {
        "success": False,
        "error": "(401) Reason: Unauthorized",
    }
    monkeypatch.setattr(
        "integrations.kubernetes.tools.fleet_search._resolve_client",
        lambda _cluster, _configs, conn: (client, conn, None),
    )

    result = _search_one_cluster_workloads("gke-a", {}, "svc", "")

    assert result["success"] is False
    assert result["error"] == "(401) Reason: Unauthorized"


# ---------------------------------------------------------------------------
# The client methods
# ---------------------------------------------------------------------------


def _typed_item(name: str, namespace: str, **status: Any) -> Any:
    item = MagicMock()
    item.metadata.name = name
    item.metadata.namespace = namespace
    item.spec.replicas = status.get("replicas", 1)
    item.spec.suspend = status.get("suspend", False)
    item.status.ready_replicas = status.get("ready_replicas", 1)
    item.status.desired_number_scheduled = status.get("desired_number_scheduled", 1)
    item.status.number_ready = status.get("number_ready", 1)
    return item


def _typed_list(items: list[Any], *, continue_token: str = "") -> Any:
    listing = MagicMock()
    listing.items = items
    listing.metadata._continue = continue_token
    return listing


def _empty_typed_list() -> Any:
    return _typed_list([])


def test_search_workload_owners_lists_all_namespaces_and_filters_by_substring() -> None:
    """An empty namespace means every namespace, and matching is case-insensitive."""
    apps = MagicMock()
    apps.list_deployment_for_all_namespaces.return_value = _typed_list(
        [_typed_item("SVC-Api", "payments"), _typed_item("other", "kube-system")]
    )
    apps.list_stateful_set_for_all_namespaces.return_value = _empty_typed_list()
    apps.list_daemon_set_for_all_namespaces.return_value = _empty_typed_list()
    batch = MagicMock()
    batch.list_cron_job_for_all_namespaces.return_value = _empty_typed_list()
    custom = MagicMock()
    custom.list_cluster_custom_object.return_value = {"items": [], "metadata": {}}

    client = _make_client_with_apis(apps=apps, batch=batch, custom=custom)
    result = client.search_workload_owners("svc-api")

    assert result["success"] is True
    assert [row["name"] for row in result["workloads"]] == ["SVC-Api"]
    assert result["workloads"][0]["namespace"] == "payments"
    apps.list_deployment_for_all_namespaces.assert_called_once()
    apps.list_namespaced_deployment.assert_not_called()
    custom.list_cluster_custom_object.assert_called_once()
    custom.list_namespaced_custom_object.assert_not_called()


def test_search_workload_owners_narrows_when_a_namespace_is_given() -> None:
    apps = MagicMock()
    apps.list_namespaced_deployment.return_value = _typed_list([_typed_item("svc-api", "payments")])
    apps.list_namespaced_stateful_set.return_value = _empty_typed_list()
    apps.list_namespaced_daemon_set.return_value = _empty_typed_list()
    batch = MagicMock()
    batch.list_namespaced_cron_job.return_value = _empty_typed_list()
    custom = MagicMock()
    custom.list_namespaced_custom_object.return_value = {"items": [], "metadata": {}}

    client = _make_client_with_apis(apps=apps, batch=batch, custom=custom)
    result = client.search_workload_owners("svc-api", "payments")

    assert result["success"] is True
    apps.list_namespaced_deployment.assert_called_once()
    assert apps.list_namespaced_deployment.call_args.kwargs["namespace"] == "payments"
    apps.list_deployment_for_all_namespaces.assert_not_called()


def test_truncation_comes_from_the_continue_token_not_the_row_count() -> None:
    """Plan test 13 / D9.

    Fewer rows than the limit, but the server handed back a continue token: the
    page is partial and "not found" is not yet an answer.
    """
    apps = MagicMock()
    apps.list_deployment_for_all_namespaces.return_value = _typed_list(
        [_typed_item("other", "kube-system")], continue_token="eyJ2IjoibWV0YS"
    )
    apps.list_stateful_set_for_all_namespaces.return_value = _empty_typed_list()
    apps.list_daemon_set_for_all_namespaces.return_value = _empty_typed_list()
    batch = MagicMock()
    batch.list_cron_job_for_all_namespaces.return_value = _empty_typed_list()
    custom = MagicMock()
    custom.list_cluster_custom_object.return_value = {"items": [], "metadata": {}}

    client = _make_client_with_apis(apps=apps, batch=batch, custom=custom)
    result = client.search_workload_owners("svc-api")

    assert result["workloads"] == []
    assert result["truncated"] is True
    assert result["truncated_kinds"] == ["Deployment"]


def test_an_absent_rollouts_crd_is_unavailable_not_a_cluster_failure() -> None:
    """Plan test 9. Most clusters do not run Argo Rollouts."""
    apps = MagicMock()
    apps.list_deployment_for_all_namespaces.return_value = _typed_list(
        [_typed_item("svc-api", "payments")]
    )
    apps.list_stateful_set_for_all_namespaces.return_value = _empty_typed_list()
    apps.list_daemon_set_for_all_namespaces.return_value = _empty_typed_list()
    batch = MagicMock()
    batch.list_cron_job_for_all_namespaces.return_value = _empty_typed_list()
    custom = MagicMock()
    custom.list_cluster_custom_object.side_effect = ApiException(
        status=HTTPStatus.NOT_FOUND, reason="Not Found"
    )

    client = _make_client_with_apis(apps=apps, batch=batch, custom=custom)
    result = client.search_workload_owners("svc-api")

    assert result["success"] is True
    assert [row["name"] for row in result["workloads"]] == ["svc-api"]
    assert [entry["kind"] for entry in result["unavailable_kinds"]] == ["Rollout"]


@pytest.mark.parametrize(
    ("status", "expect_capture"),
    [
        (HTTPStatus.NOT_FOUND, False),
        (HTTPStatus.FORBIDDEN, False),
        (HTTPStatus.UNAUTHORIZED, True),
        (HTTPStatus.INTERNAL_SERVER_ERROR, True),
    ],
)
def test_no_sentry_error_for_an_absent_crd(status: HTTPStatus, expect_capture: bool) -> None:
    """Plan test 10 — the Sentry-error-per-turn regression.

    ``capture_service_error`` grades a non-httpx exception ``severity="error"``,
    so a fleet of mostly non-Argo clusters would file one Sentry error per
    cluster per turn, forever. A green suite does not catch this: deleting the
    ``_KIND_UNAVAILABLE_STATUSES`` guard leaves every payload assertion intact.
    """
    apps = MagicMock()
    apps.list_deployment_for_all_namespaces.return_value = _empty_typed_list()
    apps.list_stateful_set_for_all_namespaces.return_value = _empty_typed_list()
    apps.list_daemon_set_for_all_namespaces.return_value = _empty_typed_list()
    batch = MagicMock()
    batch.list_cron_job_for_all_namespaces.return_value = _empty_typed_list()
    custom = MagicMock()
    custom.list_cluster_custom_object.side_effect = ApiException(status=status, reason="nope")

    client = _make_client_with_apis(apps=apps, batch=batch, custom=custom)

    with patch("integrations.kubernetes.client.capture_service_error") as capture:
        result = client.search_workload_owners("svc-api")

    assert capture.called is expect_capture
    # A degradable status is still a successful, complete-enough answer;
    # anything else fails the whole cluster so the caller cannot mistake it
    # for "nothing here".
    assert result["success"] is not expect_capture


def test_search_pods_lists_all_namespaces_and_reports_its_continue_token() -> None:
    pod = MagicMock()
    pod.metadata.name = "svc-api-6c8f9d7b4-2xqlp"
    pod.metadata.namespace = "payments"
    pod.status.phase = "Running"
    ready = MagicMock()
    ready.name = "app"
    ready.ready = True
    ready.restart_count = 4
    pod.status.container_statuses = [ready]

    other = MagicMock()
    other.metadata.name = "unrelated"
    other.metadata.namespace = "kube-system"
    other.status.phase = "Running"
    other.status.container_statuses = []

    core = MagicMock()
    core.list_pod_for_all_namespaces.return_value = _typed_list([pod, other], continue_token="more")

    client = _make_client_with_apis(core=core)
    result = client.search_pods("SVC-API")

    assert result["success"] is True
    assert [row["name"] for row in result["pods"]] == ["svc-api-6c8f9d7b4-2xqlp"]
    assert result["pods"][0]["ready"] == 1
    assert result["pods"][0]["phase"] == "Running"
    assert result["truncated"] is True
    core.list_pod_for_all_namespaces.assert_called_once()
    core.list_namespaced_pod.assert_not_called()


# --- registry wiring -------------------------------------------------------
#
# ``run()`` is reachable from a test with any kwargs you like. Production never
# calls it that way: ``core/execution.py`` asks the tool which sources it needs
# (``is_available``) and what to inject (``extract_params``). ``BaseTool``
# defaults both to "always available, inject nothing", so a tool that forgets
# to override them looks perfect under direct-call tests and cannot work once
# registered. Both tests below fail if either override is deleted.


def test_the_tool_is_hidden_when_no_kubeconfig_is_configured() -> None:
    tool = KubernetesSearchFleetTool()

    assert tool.is_available({"kubernetes": {"kubeconfig": _MINIMAL_KUBECONFIG}}) is True
    assert tool.is_available({"kubernetes": {"kubeconfig_path": "/tmp/kubeconfig"}}) is True
    assert tool.is_available({"kubernetes": {}}) is False
    assert tool.is_available({}) is False


def test_the_connection_fields_it_declares_as_injected_are_the_ones_it_extracts() -> None:
    """Every name in ``injected_params`` has to be produced by ``extract_params``.

    A missing key is silently replaced by the ``run`` signature default — an
    empty kubeconfig — so the tool answers "kubernetes is not configured" on a
    cluster it can actually reach.
    """
    tool = KubernetesSearchFleetTool()

    extracted = tool.extract_params(
        {
            "kubernetes": {
                "kubeconfig": _MINIMAL_KUBECONFIG,
                "context": "prod",
                "namespace": "payments",
            }
        }
    )

    assert set(tool.injected_params) <= set(extracted)
    assert extracted["kubeconfig"] == _MINIMAL_KUBECONFIG
    assert extracted["context"] == "prod"
    assert extracted["default_namespace"] == "payments"
    assert isinstance(extracted["cluster_configs"], dict)


def test_the_fan_out_budget_still_fits_inside_a_gateway_turn() -> None:
    """The deadline is a tuning constant, but not a free one.

    ``_BoundedApiClient`` allows 5s connect / 60s read per request and one
    cluster issues five sequential kind calls, so a fan-out with no budget of
    its own can burn 300s — longer than the turn that is waiting for it. The
    upper bound below is what makes the deadline a deadline: raise it past the
    turn timeout and the tool is back to being killed mid-flight, with the same
    "the bot said nothing" symptom this budget exists to prevent.
    """
    from gateway.transports.slack.settings import SlackGatewaySettings

    turn_budget = SlackGatewaySettings.model_fields["turn_timeout_seconds"].default

    assert _FLEET_SEARCH_DEADLINE_SECONDS > 0.0
    # Room left for the model's follow-up call in the same turn.
    assert turn_budget / 2 >= _FLEET_SEARCH_DEADLINE_SECONDS
