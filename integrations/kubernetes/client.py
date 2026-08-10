"""Kubernetes API client.

Supports two auth paths:
- File path (``kubeconfig_path``): uses ``load_kube_config`` for a single file.
  For colon-separated multi-file values (e.g. ``~/.kube/config:~/.kube/dev``),
  ``config_file=`` is omitted so the SDK reads ``KUBECONFIG`` from the
  environment and performs the merge natively.
- Inline YAML (``kubeconfig``): uses ``load_kube_config_from_dict`` for configs
  stored as strings (e.g. from a secrets manager).

``kubeconfig_path`` takes precedence when both are present.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import yaml
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException

from config.constants.kubernetes import (
    ARGO_ROLLOUTS_GROUP,
    ARGO_ROLLOUTS_PLURAL,
    ARGO_ROLLOUTS_VERSION,
    KUBERNETES_MAX_LOG_CHARS,
    KUBERNETES_MAX_LOG_LINE_CHARS,
    KUBERNETES_MAX_LOG_TAIL_LINES,
)
from integrations.config_models import KubernetesIntegrationConfig
from integrations.probes import ProbeResult
from platform.observability.errors.service import capture_service_error

logger = logging.getLogger(__name__)

_DEFAULT_TAIL_LINES = 100
_DEFAULT_LIMIT = 50
_LOG_LINES_DROPPED_NOTE = "only the newest {kept} of {requested} lines are shown"
_LOG_LINES_CLIPPED_NOTE = "{clipped} over-long line(s) were clipped to {limit} characters"
_LOG_TRUNCATION_NOTE_TEMPLATE = "Log output was capped at {cap} characters: {reasons}."
_LOG_CLIP_MARKER = "…[clipped]"
# Truncating a namespace list at 50 defeats a discovery tool, and namespace
# objects are small.
_NAMESPACE_LIST_LIMIT = 200
#: Fleet search lists whole clusters, so ``_DEFAULT_LIMIT`` (50) would truncate
#: nearly every cluster and make "not found" meaningless. Truncation past this
#: is reported, never hidden — see ``_list_was_truncated``.
_FLEET_SEARCH_LIMIT = 500
#: Statuses that mean "this credential may not enumerate namespaces cluster-wide"
#: rather than "the cluster is broken". Both degrade the tool instead of failing it.
_NAMESPACE_LIST_DENIED_STATUSES = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})

#: Statuses meaning "this kind cannot be listed here with this credential" —
#: the CRD is not installed (404) or RBAC does not grant it (403) — rather than
#: "the cluster is broken". Same reasoning as _NAMESPACE_LIST_DENIED_STATUSES:
#: capture_service_error grades a non-httpx exception severity="error", so
#: reporting the 404 would file one Sentry error per turn, forever, on every
#: cluster that simply does not run Argo. 401 and 5xx keep telemetry.
_KIND_UNAVAILABLE_STATUSES = frozenset({HTTPStatus.NOT_FOUND, HTTPStatus.FORBIDDEN})

# Bounds on every request the SDK issues. A control plane that does not
# authorize this host — GKE master-authorized-networks, a closed security
# group, a down VPN — drops the packets rather than refusing the connection, so
# an unbounded connect only ends when the OS gives up, minutes later. Clusters
# are probed one at a time during registration, so one unreachable control
# plane otherwise stalls every cluster queued behind it.
_CONNECT_TIMEOUT_SECONDS = 5.0
_READ_TIMEOUT_SECONDS = 60.0
_REQUEST_TIMEOUT: tuple[float, float] = (_CONNECT_TIMEOUT_SECONDS, _READ_TIMEOUT_SECONDS)

# Resource types that carry env vars and require value redaction before returning to the LLM.
_WORKLOAD_TYPES: frozenset[str] = frozenset(
    {
        "pod",
        "pods",
        "deployment",
        "deployments",
        "statefulset",
        "statefulsets",
        "daemonset",
        "daemonsets",
        "replicaset",
        "replicasets",
        "rollout",
        "rollouts",
    }
)


@dataclass(frozen=True, slots=True)
class _TypedResource:
    """A resource served by one of the generated typed API clients."""

    api: str  # "core" | "apps" | "networking"
    method: str
    cluster_scoped: bool = False


@dataclass(frozen=True, slots=True)
class _CustomResource:
    """A CRD served by ``CustomObjectsApi`` rather than a generated client.

    The typed clients only know the built-in API groups, so a Rollout — the
    object that actually owns the pods on an Argo cluster — is unreachable
    through them. ``method`` is carried so the read-only audit over this table
    covers CRDs the same way it covers built-ins.
    """

    group: str
    version: str
    plural: str
    method: str = "get_namespaced_custom_object"
    cluster_scoped: bool = False


# Maps resource_type string -> typed or custom resource
_RESOURCE_DISPATCH: dict[str, _TypedResource | _CustomResource] = {
    "pod": _TypedResource("core", "read_namespaced_pod"),
    "pods": _TypedResource("core", "read_namespaced_pod"),
    "deployment": _TypedResource("apps", "read_namespaced_deployment"),
    "deployments": _TypedResource("apps", "read_namespaced_deployment"),
    "statefulset": _TypedResource("apps", "read_namespaced_stateful_set"),
    "statefulsets": _TypedResource("apps", "read_namespaced_stateful_set"),
    "daemonset": _TypedResource("apps", "read_namespaced_daemon_set"),
    "daemonsets": _TypedResource("apps", "read_namespaced_daemon_set"),
    "service": _TypedResource("core", "read_namespaced_service"),
    "services": _TypedResource("core", "read_namespaced_service"),
    "configmap": _TypedResource("core", "read_namespaced_config_map"),
    "configmaps": _TypedResource("core", "read_namespaced_config_map"),
    "ingress": _TypedResource("networking", "read_namespaced_ingress"),
    "ingresses": _TypedResource("networking", "read_namespaced_ingress"),
    "replicaset": _TypedResource("apps", "read_namespaced_replica_set"),
    "replicasets": _TypedResource("apps", "read_namespaced_replica_set"),
    "persistentvolumeclaim": _TypedResource("core", "read_namespaced_persistent_volume_claim"),
    "persistentvolumeclaims": _TypedResource("core", "read_namespaced_persistent_volume_claim"),
    "pvc": _TypedResource("core", "read_namespaced_persistent_volume_claim"),
    "node": _TypedResource("core", "read_node", cluster_scoped=True),
    "nodes": _TypedResource("core", "read_node", cluster_scoped=True),
    "rollout": _CustomResource(ARGO_ROLLOUTS_GROUP, ARGO_ROLLOUTS_VERSION, ARGO_ROLLOUTS_PLURAL),
    "rollouts": _CustomResource(ARGO_ROLLOUTS_GROUP, ARGO_ROLLOUTS_VERSION, ARGO_ROLLOUTS_PLURAL),
}


# kubectl writes the full applied manifest -- including literal env var
# values -- into this annotation on `kubectl apply`. It must be stripped
# wherever annotations are returned, or it silently reintroduces the
# credentials that _redact_env_values and the env-name-only projection in
# describe_pod were written to remove.
_LAST_APPLIED_CONFIG_ANNOTATION = "kubectl.kubernetes.io/last-applied-configuration"


def _redact_annotations(annotations: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``annotations`` with the last-applied-configuration key removed."""
    return {k: v for k, v in (annotations or {}).items() if k != _LAST_APPLIED_CONFIG_ANNOTATION}


def _cap_log_lines(lines: list[str]) -> tuple[list[str], str | None]:
    """Bound pod-log output, keeping the newest lines.

    Returns the capped lines and a note describing what was lost, or ``None``
    when everything fit.

    Two caps, applied in that order, and the order is load-bearing. A single
    line can be a stack trace or a base64 blob larger than the whole character
    budget; clipping each line first means one such line costs only itself,
    where a naive budget walk would return *nothing* because the newest line
    alone overflowed. Within a line the head is kept — that is where the
    timestamp, level and message are.

    The budget walk then runs newest-first, because ``tail_lines`` means "from
    the end of the log" and a crash loop's cause is in the last lines.
    """
    clipped = 0
    bounded: list[str] = []
    for line in lines:
        if len(line) > KUBERNETES_MAX_LOG_LINE_CHARS:
            head = KUBERNETES_MAX_LOG_LINE_CHARS - len(_LOG_CLIP_MARKER)
            bounded.append(line[:head] + _LOG_CLIP_MARKER)
            clipped += 1
        else:
            bounded.append(line)

    budget = KUBERNETES_MAX_LOG_CHARS
    kept: list[str] = []
    for line in reversed(bounded):
        cost = len(line) + 1
        if cost > budget:
            break
        budget -= cost
        kept.append(line)
    kept.reverse()

    reasons: list[str] = []
    if len(kept) < len(bounded):
        reasons.append(_LOG_LINES_DROPPED_NOTE.format(kept=len(kept), requested=len(bounded)))
    if clipped:
        reasons.append(
            _LOG_LINES_CLIPPED_NOTE.format(clipped=clipped, limit=KUBERNETES_MAX_LOG_LINE_CHARS)
        )
    if not reasons:
        return kept, None
    note = _LOG_TRUNCATION_NOTE_TEMPLATE.format(
        cap=KUBERNETES_MAX_LOG_CHARS, reasons=" and ".join(reasons)
    )
    return kept, note


def _list_was_truncated(list_obj: Any) -> bool:
    """True when the API returned a partial page.

    Every existing lister reports ``total: len(items)`` and drops the API's
    continue token, so a truncated list is indistinguishable from a complete
    one — an agent answering "does X exist" from a truncated page answers
    wrongly. The boolean, not the token, is what changes the answer: there is
    no resume input on these tools, so returning an opaque token the model
    cannot spend would be noise.
    """
    if hasattr(list_obj, "metadata"):
        # Typed model (V1ListMeta)
        metadata = list_obj.metadata
        return bool(metadata and getattr(metadata, "_continue", ""))
    else:
        # Dict from CRD API
        metadata = list_obj.get("metadata", {}) or {}
        return bool(metadata.get("continue", ""))


def _redact_env_values(resource_dict: dict[str, Any]) -> None:
    """Strip env var values from a serialized workload dict in-place.

    sanitize_for_serialization() returns the full Kubernetes object JSON
    including env[].value for literal env vars. Removing values keeps
    credential redaction consistent with describe_pod (returns only key
    names) and prevents tokens/DB URLs/API keys from reaching the LLM.

    Handles two layouts:
    - Pod: spec.containers[]/initContainers[]/ephemeralContainers[]
    - Workload controllers (Deployment/StatefulSet/DaemonSet/ReplicaSet):
      spec.template.spec.containers[]/initContainers[]/ephemeralContainers[]
    """

    def _strip_env(spec: dict[str, Any]) -> None:
        for key in ("containers", "initContainers", "ephemeralContainers"):
            for container in spec.get(key) or []:
                for env_entry in container.get("env") or []:
                    env_entry.pop("value", None)

    spec = resource_dict.get("spec") or {}
    _strip_env(spec)
    # Pod template inside workload controllers
    template_spec = ((spec.get("template") or {}).get("spec")) or {}
    if template_spec:
        _strip_env(template_spec)


class _BoundedApiClient(k8s_client.ApiClient):
    """``ApiClient`` that applies :data:`_REQUEST_TIMEOUT` to every request.

    The SDK has no client-wide timeout setting: each generated method carries
    its own ``_request_timeout`` and defaults it to ``None``. Overriding the
    single funnel every one of them routes through bounds the whole integration
    in one place. A caller that passes its own timeout still wins.
    """

    def call_api(self, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get("_request_timeout") is None:
            kwargs["_request_timeout"] = _REQUEST_TIMEOUT
        return super().call_api(*args, **kwargs)


class KubernetesClient:
    """Kubernetes API client built from a kubeconfig file path or inline YAML."""

    def __init__(self, config: KubernetesIntegrationConfig) -> None:
        self.config = config
        self._core_v1: k8s_client.CoreV1Api | None = None
        self._apps_v1: k8s_client.AppsV1Api | None = None
        self._networking_v1: k8s_client.NetworkingV1Api | None = None
        self._custom_objects: k8s_client.CustomObjectsApi | None = None
        self._batch_v1: k8s_client.BatchV1Api | None = None
        self._api_client: k8s_client.ApiClient | None = None

    def _build_clients(
        self,
    ) -> tuple[k8s_client.CoreV1Api, k8s_client.AppsV1Api, k8s_client.NetworkingV1Api]:
        api_config = k8s_client.Configuration()
        context = self.config.context or None
        if self.config.kubeconfig_path:
            # config_file= is forwarded verbatim to KubeConfigMerger, which
            # already splits on the OS path separator (":" on non-Windows) and
            # merges each file itself — the same mechanism the SDK uses when
            # reading the KUBECONFIG env var. Passing the configured value
            # directly (rather than falling back to config_file=None) keeps
            # DB-stored integrations from silently reading the process
            # environment's KUBECONFIG instead of the configured paths.
            k8s_config.load_kube_config(
                config_file=self.config.kubeconfig_path,
                client_configuration=api_config,
                context=context,
            )
        else:
            kubeconfig_dict = yaml.safe_load(self.config.kubeconfig)
            k8s_config.load_kube_config_from_dict(
                config_dict=kubeconfig_dict,
                client_configuration=api_config,
                context=context,
            )
        api_client = _BoundedApiClient(api_config)
        self._api_client = api_client
        return (
            k8s_client.CoreV1Api(api_client),
            k8s_client.AppsV1Api(api_client),
            k8s_client.NetworkingV1Api(api_client),
        )

    def _get_clients(
        self,
    ) -> tuple[k8s_client.CoreV1Api, k8s_client.AppsV1Api, k8s_client.NetworkingV1Api]:
        if self._core_v1 is None or self._apps_v1 is None or self._networking_v1 is None:
            self._core_v1, self._apps_v1, self._networking_v1 = self._build_clients()
        return self._core_v1, self._apps_v1, self._networking_v1

    def _get_custom_objects(self) -> k8s_client.CustomObjectsApi:
        """``CustomObjectsApi`` bound to the same ApiClient as the typed clients.

        Added as its own accessor rather than a fourth element of
        ``_get_clients()``: twelve call sites unpack that tuple as
        ``core_v1, _, _`` and widening it would churn every one of them.
        """
        if self._custom_objects is None:
            self._get_clients()  # builds _api_client
            self._custom_objects = k8s_client.CustomObjectsApi(self._api_client)
        return self._custom_objects

    def _get_batch(self) -> k8s_client.BatchV1Api:
        """``BatchV1Api`` for CronJobs, bound to the shared ApiClient."""
        if self._batch_v1 is None:
            self._get_clients()
            self._batch_v1 = k8s_client.BatchV1Api(self._api_client)
        return self._batch_v1

    def close(self) -> None:
        """Close the underlying ApiClient connection pool."""
        if self._api_client is not None:
            self._api_client.close()

    def __enter__(self) -> KubernetesClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def is_configured(self) -> bool:
        return self.config.is_configured

    def probe_access(self) -> ProbeResult:
        """Validate Kubernetes connectivity against the configured namespace.

        Uses list_namespaced_pod (namespace-scoped) rather than list_namespace
        (cluster-wide). Cluster-wide calls require a ClusterRole binding which
        is unavailable on GKE Workload Identity, AKS Managed Identity, on-prem
        Role bindings, and k3s restricted configs — all typical targets for this
        integration. Every namespaced tool is namespace-scoped, so a
        namespace-scoped probe reflects the access those tools need;
        kubernetes_list_namespaces needs a cluster-wide read that this probe
        deliberately does not require, and degrades on its own when it is absent.
        """
        if not self.is_configured:
            return ProbeResult.missing("Missing kubeconfig or kubeconfig_path.")
        try:
            namespace = self.config.namespace or "default"
            core_v1, _, _ = self._get_clients()
            pod_list = core_v1.list_namespaced_pod(namespace=namespace, limit=1)
            pod_count = len(pod_list.items or [])
            return ProbeResult.passed(
                f"Connected to Kubernetes cluster; namespace '{namespace}' accessible "
                f"({pod_count} pod(s) visible).",
                namespace=namespace,
            )
        except ConfigException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="probe_access"
            )
            return ProbeResult.failed(str(exc))
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="probe_access"
            )
            return ProbeResult.failed(f"Kubernetes API error {exc.status}: {str(exc.reason)}")
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="probe_access"
            )
            return ProbeResult.failed(str(exc))

    def list_pods(
        self,
        namespace: str = "default",
        label_selector: str = "",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List pods in a namespace with optional label selector filter."""
        try:
            core_v1, _, _ = self._get_clients()
            kwargs: dict[str, Any] = {"limit": limit}
            if label_selector:
                kwargs["label_selector"] = label_selector
            pod_list = core_v1.list_namespaced_pod(namespace=namespace, **kwargs)
            pods = []
            for pod in pod_list.items or []:
                meta = pod.metadata
                status = pod.status
                containers = [
                    {"name": c.name, "ready": c.ready, "restart_count": c.restart_count}
                    for c in (status.container_statuses or [])
                ]
                pods.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "phase": status.phase,
                        "conditions": [
                            {"type": c.type, "status": c.status} for c in (status.conditions or [])
                        ],
                        "containers": containers,
                        "node_name": spec.node_name if (spec := pod.spec) else None,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                    }
                )
            return {"success": True, "pods": pods, "total": len(pods)}
        except ApiException as exc:
            capture_service_error(exc, logger=logger, integration="kubernetes", method="list_pods")
            return {
                "success": False,
                "error": f"Kubernetes API error {exc.status}: {exc.reason}",
            }
        except Exception as exc:
            capture_service_error(exc, logger=logger, integration="kubernetes", method="list_pods")
            return {"success": False, "error": str(exc)}

    def get_pod_logs(
        self,
        namespace: str,
        pod_name: str,
        container: str = "",
        tail_lines: int = _DEFAULT_TAIL_LINES,
    ) -> dict[str, Any]:
        """Fetch recent log lines from a pod container."""
        try:
            core_v1, _, _ = self._get_clients()
            # Bound the top only: a caller asking for fewer lines still gets
            # exactly what it asked for, including the SDK's own reading of 0.
            kwargs: dict[str, Any] = {"tail_lines": min(tail_lines, KUBERNETES_MAX_LOG_TAIL_LINES)}
            if container:
                kwargs["container"] = container
            logs = core_v1.read_namespaced_pod_log(name=pod_name, namespace=namespace, **kwargs)
            requested = logs.splitlines() if logs else []
            lines, note = _cap_log_lines(requested)

            result: dict[str, Any] = {
                "success": True,
                "pod_name": pod_name,
                "namespace": namespace,
                "container": container or None,
                "lines": lines,
                "total": len(lines),
                "truncated": note is not None,
            }
            if note is not None:
                result["truncation_note"] = note
            return result

        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="get_pod_logs"
            )
            error = f"Kubernetes API error {exc.status}: {exc.reason}"
            if exc.status == HTTPStatus.NOT_FOUND:
                # A guessed pod name is indistinguishable from a wrong cluster
                # or namespace at this call — the 404 alone does not say which.
                # Point at the tool built for that ambiguity instead of leaving
                # the model to keep guessing single-cluster names one at a time.
                error += (
                    f" — pod {pod_name!r} not found in namespace {namespace!r} on this "
                    "cluster; if you do not know which cluster or namespace runs it, "
                    "use kubernetes_search_fleet to locate it across all registered "
                    "clusters instead of guessing another name here"
                )
            return {
                "success": False,
                "error": error,
                "truncated": False,
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="get_pod_logs"
            )
            return {
                "success": False,
                "error": str(exc),
                "truncated": False,
            }

    def list_deployments(
        self,
        namespace: str = "default",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List deployments and their replica status in a namespace."""
        try:
            _, apps_v1, _ = self._get_clients()
            dep_list = apps_v1.list_namespaced_deployment(namespace=namespace, limit=limit)
            deployments = []
            for dep in dep_list.items or []:
                meta = dep.metadata
                spec = dep.spec
                status = dep.status
                deployments.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "desired": spec.replicas if spec else None,
                        "ready": status.ready_replicas,
                        "available": status.available_replicas,
                        "unavailable": status.unavailable_replicas,
                        "updated": status.updated_replicas,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                    }
                )
            return {"success": True, "deployments": deployments, "total": len(deployments)}
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_deployments"
            )
            return {
                "success": False,
                "error": f"Kubernetes API error {exc.status}: {exc.reason}",
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_deployments"
            )
            return {"success": False, "error": str(exc)}

    def get_events(
        self,
        namespace: str = "default",
        field_selector: str = "",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List Kubernetes events for a namespace, useful for crash loops and OOM kills."""
        try:
            core_v1, _, _ = self._get_clients()
            kwargs: dict[str, Any] = {"limit": limit}
            if field_selector:
                kwargs["field_selector"] = field_selector
            event_list = core_v1.list_namespaced_event(namespace=namespace, **kwargs)
            events = []
            for ev in event_list.items or []:
                meta = ev.metadata
                events.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "reason": ev.reason,
                        "message": ev.message,
                        "type": ev.type,
                        "count": ev.count,
                        "involved_object": {
                            "kind": ev.involved_object.kind,
                            "name": ev.involved_object.name,
                            "namespace": ev.involved_object.namespace,
                        }
                        if ev.involved_object
                        else {},
                        "first_timestamp": (
                            ev.first_timestamp.isoformat() if ev.first_timestamp else None
                        ),
                        "last_timestamp": (
                            ev.last_timestamp.isoformat() if ev.last_timestamp else None
                        ),
                    }
                )
            return {"success": True, "events": events, "total": len(events)}
        except ApiException as exc:
            capture_service_error(exc, logger=logger, integration="kubernetes", method="get_events")
            return {
                "success": False,
                "error": f"Kubernetes API error {exc.status}: {exc.reason}",
            }
        except Exception as exc:
            capture_service_error(exc, logger=logger, integration="kubernetes", method="get_events")
            return {"success": False, "error": str(exc)}

    def describe_pod(self, namespace: str, pod_name: str) -> dict[str, Any]:
        """Return full pod spec, status, and container states for a single pod."""
        try:
            core_v1, _, _ = self._get_clients()
            pod = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
            meta = pod.metadata
            spec = pod.spec
            status = pod.status

            def _container_spec(c: Any) -> dict[str, Any]:
                return {
                    "name": c.name,
                    "image": c.image,
                    "ports": [
                        {"container_port": p.container_port, "protocol": p.protocol}
                        for p in (c.ports or [])
                    ],
                    "resources": {
                        "requests": dict(c.resources.requests or {}) if c.resources else {},
                        "limits": dict(c.resources.limits or {}) if c.resources else {},
                    },
                    # Return only env var names, not values, regardless of source
                    # (literal value vs. valueFrom secretKeyRef/configMapKeyRef).
                    # Literal values may contain credentials (tokens, DB URLs, API
                    # keys) that must not be forwarded to the LLM or stored in
                    # investigation reports.
                    "env": [e.name for e in (c.env or [])],
                }

            def _container_status(cs: Any) -> dict[str, Any]:
                state: dict[str, Any] = {}
                if cs.state:
                    if cs.state.running:
                        state = {
                            "running": {
                                "started_at": cs.state.running.started_at.isoformat()
                                if cs.state.running.started_at
                                else None
                            }
                        }
                    elif cs.state.waiting:
                        state = {
                            "waiting": {
                                "reason": cs.state.waiting.reason,
                                "message": cs.state.waiting.message,
                            }
                        }
                    elif cs.state.terminated:
                        state = {
                            "terminated": {
                                "reason": cs.state.terminated.reason,
                                "exit_code": cs.state.terminated.exit_code,
                            }
                        }
                return {
                    "name": cs.name,
                    "image": cs.image,
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "state": state,
                }

            return {
                "success": True,
                "name": meta.name,
                "namespace": meta.namespace,
                "labels": dict(meta.labels or {}),
                "annotations": _redact_annotations(meta.annotations),
                "creation_timestamp": meta.creation_timestamp.isoformat()
                if meta.creation_timestamp
                else None,
                "owner_references": [
                    {"kind": o.kind, "name": o.name} for o in (meta.owner_references or [])
                ],
                "spec": {
                    "node_name": spec.node_name if spec else None,
                    "service_account_name": spec.service_account_name if spec else None,
                    "node_selector": dict(spec.node_selector or {}) if spec else {},
                    "containers": [_container_spec(c) for c in (spec.containers or [])]
                    if spec
                    else [],
                    "init_containers": [_container_spec(c) for c in (spec.init_containers or [])]
                    if spec
                    else [],
                    "volumes": [{"name": v.name} for v in (spec.volumes or [])] if spec else [],
                },
                "status": {
                    "phase": status.phase if status else None,
                    "host_ip": status.host_ip if status else None,
                    "pod_ip": status.pod_ip if status else None,
                    "conditions": [
                        {"type": c.type, "status": c.status, "reason": c.reason}
                        for c in (status.conditions or [])
                    ]
                    if status
                    else [],
                    "container_statuses": [
                        _container_status(cs) for cs in (status.container_statuses or [])
                    ]
                    if status
                    else [],
                    "init_container_statuses": [
                        _container_status(cs) for cs in (status.init_container_statuses or [])
                    ]
                    if status
                    else [],
                },
            }
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="describe_pod"
            )
            return {
                "success": False,
                "error": f"Kubernetes API error {exc.status}: {exc.reason}",
                "not_found": exc.status == HTTPStatus.NOT_FOUND,
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="describe_pod"
            )
            return {"success": False, "error": str(exc)}

    def list_nodes(self, limit: int = _DEFAULT_LIMIT) -> dict[str, Any]:
        """List cluster nodes with conditions and capacity."""
        try:
            core_v1, _, _ = self._get_clients()
            node_list = core_v1.list_node(limit=limit)
            nodes = []
            for node in node_list.items or []:
                meta = node.metadata
                status = node.status
                spec = node.spec
                nodes.append(
                    {
                        "name": meta.name,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                        "taints": [
                            {"key": t.key, "effect": t.effect, "value": t.value}
                            for t in (spec.taints or [])
                        ]
                        if spec
                        else [],
                        "conditions": [
                            {
                                "type": c.type,
                                "status": c.status,
                                "reason": c.reason,
                                "message": c.message,
                            }
                            for c in (status.conditions or [])
                        ]
                        if status
                        else [],
                        "capacity": dict(status.capacity or {}) if status else {},
                        "allocatable": dict(status.allocatable or {}) if status else {},
                    }
                )
            return {"success": True, "nodes": nodes, "total": len(nodes)}
        except ApiException as exc:
            capture_service_error(exc, logger=logger, integration="kubernetes", method="list_nodes")
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(exc, logger=logger, integration="kubernetes", method="list_nodes")
            return {"success": False, "error": str(exc)}

    def list_namespaces(self, limit: int = _NAMESPACE_LIST_LIMIT) -> dict[str, Any]:
        """List namespaces in the cluster."""
        try:
            core_v1, _, _ = self._get_clients()
            namespace_list = core_v1.list_namespace(limit=limit)
            namespaces = []
            for ns in namespace_list.items or []:
                meta = ns.metadata
                status = ns.status
                namespaces.append(
                    {
                        "name": meta.name,
                        "status": status.phase if status else None,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                    }
                )
            return {"success": True, "namespaces": namespaces, "total": len(namespaces)}
        except ApiException as exc:
            # A 403 here is the *expected* answer on a namespace-scoped RBAC
            # binding — cluster-wide list is precisely what probe_access
            # deliberately does not require. capture_service_error classifies a
            # non-httpx exception as severity="error", so reporting it would
            # file one Sentry error per turn, forever, for a case the tool
            # already degrades on. 401 is a real auth failure and keeps
            # telemetry, as every other method here does.
            forbidden = exc.status in _NAMESPACE_LIST_DENIED_STATUSES
            if exc.status != HTTPStatus.FORBIDDEN:
                capture_service_error(
                    exc, logger=logger, integration="kubernetes", method="list_namespaces"
                )
            return {
                "success": False,
                "error": f"Kubernetes API error {exc.status}: {exc.reason}",
                **({"forbidden": True} if forbidden else {}),
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_namespaces"
            )
            return {"success": False, "error": str(exc)}

    def list_services(
        self,
        namespace: str = "default",
        label_selector: str = "",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List services with their type, clusterIP, ports, and selector."""
        try:
            core_v1, _, _ = self._get_clients()
            kwargs: dict[str, Any] = {"limit": limit}
            if label_selector:
                kwargs["label_selector"] = label_selector
            svc_list = core_v1.list_namespaced_service(namespace=namespace, **kwargs)
            services = []
            for svc in svc_list.items or []:
                meta = svc.metadata
                spec = svc.spec
                services.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                        "type": spec.type if spec else None,
                        "cluster_ip": spec.cluster_ip if spec else None,
                        "external_ips": list(spec.external_ips or []) if spec else [],
                        "selector": dict(spec.selector or {}) if spec else {},
                        "ports": [
                            {
                                "name": p.name,
                                "port": p.port,
                                "target_port": str(p.target_port) if p.target_port else None,
                                "protocol": p.protocol,
                                "node_port": p.node_port,
                            }
                            for p in (spec.ports or [])
                        ]
                        if spec
                        else [],
                    }
                )
            return {"success": True, "services": services, "total": len(services)}
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_services"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_services"
            )
            return {"success": False, "error": str(exc)}

    def list_statefulsets(
        self,
        namespace: str = "default",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List StatefulSets with replica status."""
        try:
            _, apps_v1, _ = self._get_clients()
            sts_list = apps_v1.list_namespaced_stateful_set(namespace=namespace, limit=limit)
            statefulsets = []
            for sts in sts_list.items or []:
                meta = sts.metadata
                spec = sts.spec
                status = sts.status
                statefulsets.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                        "desired": spec.replicas if spec else None,
                        "ready": status.ready_replicas if status else None,
                        "current": status.current_replicas if status else None,
                        "updated": status.updated_replicas if status else None,
                    }
                )
            return {"success": True, "statefulsets": statefulsets, "total": len(statefulsets)}
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_statefulsets"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_statefulsets"
            )
            return {"success": False, "error": str(exc)}

    def list_daemonsets(
        self,
        namespace: str = "default",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List DaemonSets with desired/ready/available counts."""
        try:
            _, apps_v1, _ = self._get_clients()
            ds_list = apps_v1.list_namespaced_daemon_set(namespace=namespace, limit=limit)
            daemonsets = []
            for ds in ds_list.items or []:
                meta = ds.metadata
                status = ds.status
                daemonsets.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                        "desired": status.desired_number_scheduled if status else None,
                        "current": status.current_number_scheduled if status else None,
                        "ready": status.number_ready if status else None,
                        "up_to_date": status.updated_number_scheduled if status else None,
                        "available": status.number_available if status else None,
                    }
                )
            return {"success": True, "daemonsets": daemonsets, "total": len(daemonsets)}
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_daemonsets"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_daemonsets"
            )
            return {"success": False, "error": str(exc)}

    def list_ingresses(
        self,
        namespace: str = "default",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List Ingress resources with rules, hosts, and TLS configuration."""
        try:
            _, _, networking_v1 = self._get_clients()
            ing_list = networking_v1.list_namespaced_ingress(namespace=namespace, limit=limit)
            ingresses = []
            for ing in ing_list.items or []:
                meta = ing.metadata
                spec = ing.spec
                status = ing.status
                rules = []
                for rule in (spec.rules or []) if spec else []:
                    paths = []
                    if rule.http:
                        for path in rule.http.paths or []:
                            backend = path.backend
                            svc_name = None
                            svc_port = None
                            if backend and backend.service:
                                svc_name = backend.service.name
                                svc_port = (
                                    backend.service.port.number if backend.service.port else None
                                )
                            paths.append(
                                {
                                    "path": path.path,
                                    "path_type": path.path_type,
                                    "service_name": svc_name,
                                    "service_port": svc_port,
                                }
                            )
                    rules.append({"host": rule.host, "paths": paths})
                lb_ingress = []
                if status and status.load_balancer and status.load_balancer.ingress:
                    lb_ingress = [
                        {"ip": i.ip, "hostname": i.hostname} for i in status.load_balancer.ingress
                    ]
                ingresses.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                        "ingress_class": spec.ingress_class_name if spec else None,
                        "rules": rules,
                        "tls": [
                            {"hosts": list(t.hosts or []), "secret_name": t.secret_name}
                            for t in (spec.tls or [])
                        ]
                        if spec
                        else [],
                        "load_balancer": lb_ingress,
                    }
                )
            return {"success": True, "ingresses": ingresses, "total": len(ingresses)}
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_ingresses"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_ingresses"
            )
            return {"success": False, "error": str(exc)}

    def list_configmaps(
        self,
        namespace: str = "default",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List ConfigMaps with their key-value data."""
        try:
            core_v1, _, _ = self._get_clients()
            cm_list = core_v1.list_namespaced_config_map(namespace=namespace, limit=limit)
            configmaps = []
            for cm in cm_list.items or []:
                meta = cm.metadata
                configmaps.append(
                    {
                        "name": meta.name,
                        "namespace": meta.namespace,
                        "labels": dict(meta.labels or {}),
                        "creation_timestamp": (
                            meta.creation_timestamp.isoformat() if meta.creation_timestamp else None
                        ),
                        "data": dict(cm.data or {}),
                        "data_keys": list((cm.data or {}).keys()),
                    }
                )
            return {"success": True, "configmaps": configmaps, "total": len(configmaps)}
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_configmaps"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_configmaps"
            )
            return {"success": False, "error": str(exc)}

    def get_resource(
        self,
        resource_type: str,
        name: str,
        namespace: str = "default",
    ) -> dict[str, Any]:
        """Fetch a single named Kubernetes resource by type and name."""
        rt = (
            resource_type.lower().rstrip("s")
            if resource_type.lower() not in _RESOURCE_DISPATCH
            else resource_type.lower()
        )
        # normalize plural/singular
        entry = _RESOURCE_DISPATCH.get(rt) or _RESOURCE_DISPATCH.get(resource_type.lower())
        if entry is None:
            supported = sorted({k.rstrip("s") for k in _RESOURCE_DISPATCH})
            return {
                "success": False,
                "error": (
                    f"Unsupported resource_type '{resource_type}'. Supported types: {supported}"
                ),
            }
        resource_dict: dict[str, Any]
        try:
            if isinstance(entry, _CustomResource):
                # CRD path: CustomObjectsApi returns decoded JSON (plain dicts,
                # lists and scalars), not a generated model, so it needs neither
                # sanitize_for_serialization nor the ApiClient that method hangs
                # off — asking for one here would couple this branch to a client
                # it never calls.
                custom_api = self._get_custom_objects()
                if entry.cluster_scoped:
                    resource_dict = custom_api.get_cluster_custom_object(
                        group=entry.group, version=entry.version, plural=entry.plural, name=name
                    )
                else:
                    resource_dict = custom_api.get_namespaced_custom_object(
                        group=entry.group,
                        version=entry.version,
                        namespace=namespace,
                        plural=entry.plural,
                        name=name,
                    )
            else:
                # Typed resource path: use generated clients
                core_v1, apps_v1, networking_v1 = self._get_clients()
                api_map: dict[str, Any] = {
                    "core": core_v1,
                    "apps": apps_v1,
                    "networking": networking_v1,
                }
                api = api_map[entry.api]
                method = getattr(api, entry.method)
                if entry.cluster_scoped:
                    obj = method(name=name)
                else:
                    obj = method(name=name, namespace=namespace)
                # _get_clients() above builds _api_client, so it is set here.
                api_client = self._api_client
                if api_client is None:  # pragma: no cover - defensive
                    raise RuntimeError("Kubernetes ApiClient was not initialised")
                resource_dict = api_client.sanitize_for_serialization(obj)
            # Redact env var values from pod and workload resources to prevent
            # credential leakage to the LLM. Workload controllers (Deployment,
            # StatefulSet, DaemonSet, ReplicaSet) embed a pod template that also
            # carries env vars. Consistent with describe_pod (keys only).
            if rt in _WORKLOAD_TYPES:
                _redact_env_values(resource_dict)
            metadata = resource_dict.get("metadata")
            if isinstance(metadata, dict):
                metadata["annotations"] = _redact_annotations(metadata.get("annotations"))
            return {
                "success": True,
                "resource_type": resource_type,
                "name": name,
                "resource": resource_dict,
            }
        except ApiException as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="get_resource"
            )
            return {
                "success": False,
                "error": f"Kubernetes API error {exc.status}: {exc.reason}",
                "not_found": exc.status == HTTPStatus.NOT_FOUND,
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="get_resource"
            )
            return {"success": False, "error": str(exc)}

    def list_rollouts(
        self,
        namespace: str = "default",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List Argo Rollouts with strategy, phase, step, and revision state."""
        try:
            custom_api = self._get_custom_objects()
            rollouts_list = custom_api.list_namespaced_custom_object(
                group=ARGO_ROLLOUTS_GROUP,
                version=ARGO_ROLLOUTS_VERSION,
                plural=ARGO_ROLLOUTS_PLURAL,
                namespace=namespace,
                limit=limit,
            )

            rollouts = []
            for item in rollouts_list.get("items", []):
                spec = item.get("spec", {})
                status = item.get("status", {})
                metadata = item.get("metadata", {})

                # Extract strategy
                strategy = None
                strategy_spec = spec.get("strategy", {})
                if strategy_spec:
                    if "canary" in strategy_spec:
                        strategy = "canary"
                    elif "blueGreen" in strategy_spec:
                        strategy = "blueGreen"

                # Calculate total steps for canary
                total_steps = None
                if strategy == "canary":
                    steps = strategy_spec.get("canary", {}).get("steps", [])
                    total_steps = len(steps) if steps else None

                # Calculate paused state
                paused = bool(spec.get("paused", False))
                pause_conditions = status.get("pauseConditions", [])
                if pause_conditions:
                    paused = True

                # Extract pause reasons
                pause_reasons = [cond.get("reason", "") for cond in pause_conditions]

                # Calculate awaiting promotion
                current_revision = status.get("currentPodHash")
                stable_revision = status.get("stableRS")
                awaiting_promotion = (
                    paused and bool(current_revision) and current_revision != stable_revision
                )

                rollout = {
                    "name": metadata.get("name"),
                    "namespace": metadata.get("namespace"),
                    "labels": metadata.get("labels", {}),
                    "creation_timestamp": metadata.get("creationTimestamp"),
                    "strategy": strategy,
                    "phase": status.get("phase"),
                    "message": status.get("message"),
                    "desired": spec.get("replicas"),
                    "current": status.get("replicas"),
                    "ready": status.get("readyReplicas"),
                    "updated": status.get("updatedReplicas"),
                    "available": status.get("availableReplicas"),
                    "current_step_index": status.get("currentStepIndex"),
                    "total_steps": total_steps,
                    "paused": paused,
                    "pause_reasons": pause_reasons,
                    "current_revision": current_revision,
                    "stable_revision": stable_revision,
                    "awaiting_promotion": awaiting_promotion,
                }
                rollouts.append(rollout)

            return {
                "success": True,
                "rollouts": rollouts,
                "total": len(rollouts),
                "truncated": _list_was_truncated(rollouts_list),
            }
        except ApiException as exc:
            if exc.status in _KIND_UNAVAILABLE_STATUSES:
                return {
                    "success": False,
                    "error": f"Kubernetes API error {exc.status}: {exc.reason}",
                    "kind_unavailable": True,
                }
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_rollouts"
            )
            return {"success": False, "error": f"Kubernetes API error {exc.status}: {exc.reason}"}
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_rollouts"
            )
            return {"success": False, "error": str(exc)}

    def list_workloads(
        self,
        namespace: str = "default",
        limit: int = _DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List every workload kind in a namespace as one kind-agnostic table."""

        @dataclass(frozen=True, slots=True)
        class _KindPage:
            rows: tuple[dict[str, Any], ...]
            truncated: bool
            unavailable_reason: str | None

        def _collect_kind(
            call: Callable[[], Any],
            project: Callable[[Any], dict[str, Any]],
            items_of: Callable[[Any], list[Any]],
        ) -> _KindPage:
            """Run one list call, degrading that kind alone on 404/403."""
            try:
                result = call()
                items = items_of(result)
                rows = tuple(project(item) for item in items)
                return _KindPage(
                    rows=rows,
                    truncated=_list_was_truncated(result),
                    unavailable_reason=None,
                )
            except ApiException as exc:
                if exc.status in _KIND_UNAVAILABLE_STATUSES:
                    return _KindPage(
                        rows=(),
                        truncated=False,
                        unavailable_reason=f"Kubernetes API error {exc.status}: {exc.reason}",
                    )
                raise  # Re-raise non-degradable exceptions

        def _deployment_workload_row(item: Any) -> dict[str, Any]:
            spec = getattr(item, "spec", None) or {}
            status = getattr(item, "status", None) or {}
            metadata = getattr(item, "metadata", None) or {}
            return {
                "kind": "Deployment",
                "name": getattr(metadata, "name", None),
                "namespace": getattr(metadata, "namespace", None),
                "desired": getattr(spec, "replicas", None),
                "ready": getattr(status, "ready_replicas", None),
                "updated": getattr(status, "updated_replicas", None),
                "available": getattr(status, "available_replicas", None),
                "phase": None,
                "labels": getattr(metadata, "labels", None) or {},
                "creation_timestamp": getattr(metadata, "creation_timestamp", None),
            }

        def _statefulset_workload_row(item: Any) -> dict[str, Any]:
            spec = getattr(item, "spec", None) or {}
            status = getattr(item, "status", None) or {}
            metadata = getattr(item, "metadata", None) or {}
            return {
                "kind": "StatefulSet",
                "name": getattr(metadata, "name", None),
                "namespace": getattr(metadata, "namespace", None),
                "desired": getattr(spec, "replicas", None),
                "ready": getattr(status, "ready_replicas", None),
                "updated": getattr(status, "updated_replicas", None),
                "available": None,
                "phase": None,
                "labels": getattr(metadata, "labels", None) or {},
                "creation_timestamp": getattr(metadata, "creation_timestamp", None),
            }

        def _daemonset_workload_row(item: Any) -> dict[str, Any]:
            status = getattr(item, "status", None) or {}
            metadata = getattr(item, "metadata", None) or {}
            return {
                "kind": "DaemonSet",
                "name": getattr(metadata, "name", None),
                "namespace": getattr(metadata, "namespace", None),
                "desired": getattr(status, "desired_number_scheduled", None),
                "ready": getattr(status, "number_ready", None),
                "updated": getattr(status, "updated_number_scheduled", None),
                "available": getattr(status, "number_available", None),
                "phase": None,
                "labels": getattr(metadata, "labels", None) or {},
                "creation_timestamp": getattr(metadata, "creation_timestamp", None),
            }

        def _cronjob_workload_row(item: Any) -> dict[str, Any]:
            spec = getattr(item, "spec", None) or {}
            metadata = getattr(item, "metadata", None) or {}
            phase = "Suspended" if getattr(spec, "suspend", False) else "Active"
            return {
                "kind": "CronJob",
                "name": getattr(metadata, "name", None),
                "namespace": getattr(metadata, "namespace", None),
                "desired": None,
                "ready": None,
                "updated": None,
                "available": None,
                "phase": phase,
                "labels": getattr(metadata, "labels", None) or {},
                "creation_timestamp": getattr(metadata, "creation_timestamp", None),
            }

        def _rollout_workload_row(item: Any) -> dict[str, Any]:
            spec = item.get("spec", {})
            status = item.get("status", {})
            metadata = item.get("metadata", {})
            return {
                "kind": "Rollout",
                "name": metadata.get("name"),
                "namespace": metadata.get("namespace"),
                "desired": spec.get("replicas"),
                "ready": status.get("readyReplicas"),
                "updated": status.get("updatedReplicas"),
                "available": status.get("availableReplicas"),
                "phase": status.get("phase"),
                "labels": metadata.get("labels", {}) or {},
                "creation_timestamp": metadata.get("creationTimestamp"),
            }

        try:
            core_v1, apps_v1, networking_v1 = self._get_clients()
            batch_v1 = self._get_batch()
            custom_api = self._get_custom_objects()

            # Collect each kind sequentially
            deployment_page = _collect_kind(
                lambda: apps_v1.list_namespaced_deployment(namespace=namespace, limit=limit),
                _deployment_workload_row,
                lambda result: getattr(result, "items", []),
            )

            statefulset_page = _collect_kind(
                lambda: apps_v1.list_namespaced_stateful_set(namespace=namespace, limit=limit),
                _statefulset_workload_row,
                lambda result: getattr(result, "items", []),
            )

            daemonset_page = _collect_kind(
                lambda: apps_v1.list_namespaced_daemon_set(namespace=namespace, limit=limit),
                _daemonset_workload_row,
                lambda result: getattr(result, "items", []),
            )

            cronjob_page = _collect_kind(
                lambda: batch_v1.list_namespaced_cron_job(namespace=namespace, limit=limit),
                _cronjob_workload_row,
                lambda result: getattr(result, "items", []),
            )

            rollout_page = _collect_kind(
                lambda: custom_api.list_namespaced_custom_object(
                    group=ARGO_ROLLOUTS_GROUP,
                    version=ARGO_ROLLOUTS_VERSION,
                    plural=ARGO_ROLLOUTS_PLURAL,
                    namespace=namespace,
                    limit=limit,
                ),
                _rollout_workload_row,
                lambda result: result.get("items", []),
            )

            # Combine all rows and sort by (kind, name)
            all_rows: list[dict[str, Any]] = []
            all_rows.extend(deployment_page.rows)
            all_rows.extend(statefulset_page.rows)
            all_rows.extend(daemonset_page.rows)
            all_rows.extend(cronjob_page.rows)
            all_rows.extend(rollout_page.rows)

            all_rows.sort(key=lambda row: (row["kind"], row["name"] or ""))

            # Collect truncation and unavailable status
            pages = [deployment_page, statefulset_page, daemonset_page, cronjob_page, rollout_page]
            kind_names = ["Deployment", "StatefulSet", "DaemonSet", "CronJob", "Rollout"]

            truncated_kinds = []
            unavailable_kinds = []

            for page, kind_name in zip(pages, kind_names):
                if page.truncated:
                    truncated_kinds.append(kind_name)
                if page.unavailable_reason:
                    unavailable_kinds.append(
                        {
                            "kind": kind_name,
                            "reason": page.unavailable_reason,
                        }
                    )

            return {
                "success": True,
                "workloads": all_rows,
                "total": len(all_rows),
                "truncated": any(page.truncated for page in pages),
                "truncated_kinds": sorted(truncated_kinds),
                "unavailable_kinds": unavailable_kinds,
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="list_workloads"
            )
            return {"success": False, "error": str(exc)}

    def search_workload_owners(
        self, name_contains: str, namespace: str = "", limit: int = _FLEET_SEARCH_LIMIT
    ) -> dict[str, Any]:
        """Search for workload owners by name substring across namespaces.

        Args:
            name_contains: Case-insensitive substring of the workload name to find
            namespace: Optional namespace to search in. Empty means all namespaces
            limit: Maximum items to return per kind

        Returns:
            Dict with success, workloads list, total, truncated status, and unavailable kinds
        """

        @dataclass(frozen=True, slots=True)
        class _KindPage:
            rows: tuple[dict[str, Any], ...]
            truncated: bool
            unavailable_reason: str | None

        def _collect_kind(
            call: Callable[[], Any],
            project: Callable[[Any], dict[str, Any]],
            items_of: Callable[[Any], list[Any]],
        ) -> _KindPage:
            """Run one list call, degrading that kind alone on 404/403."""
            try:
                result = call()
                items = items_of(result)
                # Apply name filtering
                filtered_items = []
                for item in items:
                    projected = project(item)
                    item_name = projected.get("name") or ""
                    if name_contains.strip().lower() in item_name.lower():
                        filtered_items.append(projected)

                return _KindPage(
                    rows=tuple(filtered_items),
                    truncated=_list_was_truncated(result),
                    unavailable_reason=None,
                )
            except ApiException as exc:
                if exc.status in _KIND_UNAVAILABLE_STATUSES:
                    return _KindPage(
                        rows=(),
                        truncated=False,
                        unavailable_reason=f"Kubernetes API error {exc.status}: {exc.reason}",
                    )
                raise  # Re-raise non-degradable exceptions

        def _deployment_workload_row(item: Any) -> dict[str, Any]:
            spec = getattr(item, "spec", None) or {}
            status = getattr(item, "status", None) or {}
            metadata = getattr(item, "metadata", None) or {}
            return {
                "kind": "Deployment",
                "name": getattr(metadata, "name", None),
                "namespace": getattr(metadata, "namespace", None),
                "desired": getattr(spec, "replicas", None),
                "ready": getattr(status, "ready_replicas", None),
                "phase": None,
            }

        def _statefulset_workload_row(item: Any) -> dict[str, Any]:
            spec = getattr(item, "spec", None) or {}
            status = getattr(item, "status", None) or {}
            metadata = getattr(item, "metadata", None) or {}
            return {
                "kind": "StatefulSet",
                "name": getattr(metadata, "name", None),
                "namespace": getattr(metadata, "namespace", None),
                "desired": getattr(spec, "replicas", None),
                "ready": getattr(status, "ready_replicas", None),
                "phase": None,
            }

        def _daemonset_workload_row(item: Any) -> dict[str, Any]:
            status = getattr(item, "status", None) or {}
            metadata = getattr(item, "metadata", None) or {}
            return {
                "kind": "DaemonSet",
                "name": getattr(metadata, "name", None),
                "namespace": getattr(metadata, "namespace", None),
                "desired": getattr(status, "desired_number_scheduled", None),
                "ready": getattr(status, "number_ready", None),
                "phase": None,
            }

        def _cronjob_workload_row(item: Any) -> dict[str, Any]:
            spec = getattr(item, "spec", None) or {}
            metadata = getattr(item, "metadata", None) or {}
            phase = "Suspended" if getattr(spec, "suspend", False) else "Active"
            return {
                "kind": "CronJob",
                "name": getattr(metadata, "name", None),
                "namespace": getattr(metadata, "namespace", None),
                "desired": None,
                "ready": None,
                "phase": phase,
            }

        def _rollout_workload_row(item: Any) -> dict[str, Any]:
            spec = item.get("spec", {})
            status = item.get("status", {})
            metadata = item.get("metadata", {})
            return {
                "kind": "Rollout",
                "name": metadata.get("name"),
                "namespace": metadata.get("namespace"),
                "desired": spec.get("replicas"),
                "ready": status.get("readyReplicas"),
                "phase": status.get("phase"),
            }

        try:
            core_v1, apps_v1, networking_v1 = self._get_clients()
            batch_v1 = self._get_batch()
            custom_api = self._get_custom_objects()

            # Determine which API calls to make based on namespace parameter
            if namespace:
                # Search specific namespace
                deployment_page = _collect_kind(
                    lambda: apps_v1.list_namespaced_deployment(namespace=namespace, limit=limit),
                    _deployment_workload_row,
                    lambda result: getattr(result, "items", []),
                )

                statefulset_page = _collect_kind(
                    lambda: apps_v1.list_namespaced_stateful_set(namespace=namespace, limit=limit),
                    _statefulset_workload_row,
                    lambda result: getattr(result, "items", []),
                )

                daemonset_page = _collect_kind(
                    lambda: apps_v1.list_namespaced_daemon_set(namespace=namespace, limit=limit),
                    _daemonset_workload_row,
                    lambda result: getattr(result, "items", []),
                )

                cronjob_page = _collect_kind(
                    lambda: batch_v1.list_namespaced_cron_job(namespace=namespace, limit=limit),
                    _cronjob_workload_row,
                    lambda result: getattr(result, "items", []),
                )

                rollout_page = _collect_kind(
                    lambda: custom_api.list_namespaced_custom_object(
                        group=ARGO_ROLLOUTS_GROUP,
                        version=ARGO_ROLLOUTS_VERSION,
                        plural=ARGO_ROLLOUTS_PLURAL,
                        namespace=namespace,
                        limit=limit,
                    ),
                    _rollout_workload_row,
                    lambda result: result.get("items", []),
                )
            else:
                # Search all namespaces
                deployment_page = _collect_kind(
                    lambda: apps_v1.list_deployment_for_all_namespaces(limit=limit),
                    _deployment_workload_row,
                    lambda result: getattr(result, "items", []),
                )

                statefulset_page = _collect_kind(
                    lambda: apps_v1.list_stateful_set_for_all_namespaces(limit=limit),
                    _statefulset_workload_row,
                    lambda result: getattr(result, "items", []),
                )

                daemonset_page = _collect_kind(
                    lambda: apps_v1.list_daemon_set_for_all_namespaces(limit=limit),
                    _daemonset_workload_row,
                    lambda result: getattr(result, "items", []),
                )

                cronjob_page = _collect_kind(
                    lambda: batch_v1.list_cron_job_for_all_namespaces(limit=limit),
                    _cronjob_workload_row,
                    lambda result: getattr(result, "items", []),
                )

                rollout_page = _collect_kind(
                    lambda: custom_api.list_cluster_custom_object(
                        group=ARGO_ROLLOUTS_GROUP,
                        version=ARGO_ROLLOUTS_VERSION,
                        plural=ARGO_ROLLOUTS_PLURAL,
                        limit=limit,
                    ),
                    _rollout_workload_row,
                    lambda result: result.get("items", []),
                )

            # Combine all rows and sort by (kind, namespace, name)
            all_rows: list[dict[str, Any]] = []
            all_rows.extend(deployment_page.rows)
            all_rows.extend(statefulset_page.rows)
            all_rows.extend(daemonset_page.rows)
            all_rows.extend(cronjob_page.rows)
            all_rows.extend(rollout_page.rows)

            all_rows.sort(key=lambda row: (row["kind"], row["namespace"] or "", row["name"] or ""))

            # Collect truncation and unavailable status
            pages = [deployment_page, statefulset_page, daemonset_page, cronjob_page, rollout_page]
            kind_names = ["Deployment", "StatefulSet", "DaemonSet", "CronJob", "Rollout"]

            truncated_kinds = []
            unavailable_kinds = []

            for page, kind_name in zip(pages, kind_names):
                if page.truncated:
                    truncated_kinds.append(kind_name)
                if page.unavailable_reason:
                    unavailable_kinds.append(
                        {
                            "kind": kind_name,
                            "reason": page.unavailable_reason,
                        }
                    )

            return {
                "success": True,
                "workloads": all_rows,
                "total": len(all_rows),
                "truncated": any(page.truncated for page in pages),
                "truncated_kinds": sorted(truncated_kinds),
                "unavailable_kinds": unavailable_kinds,
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="search_workload_owners"
            )
            return {"success": False, "error": str(exc)}

    def search_pods(
        self, name_contains: str, namespace: str = "", limit: int = _FLEET_SEARCH_LIMIT
    ) -> dict[str, Any]:
        """Search for pods by name substring across namespaces.

        Args:
            name_contains: Case-insensitive substring of the pod name to find
            namespace: Optional namespace to search in. Empty means all namespaces
            limit: Maximum pods to return

        Returns:
            Dict with success, pods list, total, and truncated status
        """
        try:
            core_v1, _, _ = self._get_clients()

            # Determine which API call to make based on namespace parameter
            if namespace:
                pod_list = core_v1.list_namespaced_pod(namespace=namespace, limit=limit)
            else:
                pod_list = core_v1.list_pod_for_all_namespaces(limit=limit)

            pods = []
            for pod in pod_list.items or []:
                meta = pod.metadata
                status = pod.status
                pod_name = meta.name or ""

                # Apply name filtering
                if name_contains.strip().lower() in pod_name.lower():
                    containers = [
                        {"name": c.name, "ready": c.ready, "restart_count": c.restart_count}
                        for c in (status.container_statuses or [])
                    ]

                    pods.append(
                        {
                            "kind": "Pod",
                            "name": meta.name,
                            "namespace": meta.namespace,
                            "ready": sum(1 for c in containers if c["ready"]),
                            "desired": len(containers),
                            "phase": status.phase,
                        }
                    )

            # Sort by (namespace, name)
            pods.sort(key=lambda pod: (pod["namespace"] or "", pod["name"] or ""))

            return {
                "success": True,
                "pods": pods,
                "total": len(pods),
                "truncated": _list_was_truncated(pod_list),
            }
        except Exception as exc:
            capture_service_error(
                exc, logger=logger, integration="kubernetes", method="search_pods"
            )
            return {"success": False, "error": str(exc)}
