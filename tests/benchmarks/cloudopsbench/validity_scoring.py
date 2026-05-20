"""Validity metrics for the CloudOpsBench adapter (Phase C of task scope).

Three deterministic, heuristic metrics that extend the paper's outcome /
process / robustness families. They measure honesty of the finding
itself — orthogonal to whether the agent identified the right root cause.

  - ``citation_grounding_rate`` — fraction of specific entities named in the
    finding that appear in some evidence_entry the agent collected. Catches
    hallucinated facts the agent invents from parametric knowledge.

  - ``entity_existence_rate`` — fraction of K8s entities (pods, services,
    deployments) named in the finding that actually exist in the State
    Snapshot. Catches names invented or hallucinated wholesale.

  - ``kubectl_actionability_rate`` — fraction of ``kubectl`` commands in
    the finding that parse syntactically into valid K8s verbs + resource
    types. Catches malformed kubectl suggestions.

All three are heuristic by design — they're rule-based regex over the
finding text + backend state, not LLM-as-judge. This keeps them
deterministic and free. Their limitations:

  - Specific-entity extraction misses entities not matching the regex
  - kubectl parser handles common shapes (kubectl [-n ns] verb resource
    [name]) — not pipelines, JSONPath, etc.
  - citation_grounding compares strings, not semantic equivalence —
    "currencyservice" and "the currency service" are different

Heuristic + 1.0 → 0.0 range means scores VARY honestly across runs.
Don't promote them to publication-grade without LLM-as-judge calibration
on a labeled sample (per integrity Mechanism 11).
"""

from __future__ import annotations

import re
from typing import Any

from tests.benchmarks.cloudopsbench.replay_backend import CloudOpsBenchReplayBackend

# --------------------------------------------------------------------------- #
# Entity extraction                                                           #
# --------------------------------------------------------------------------- #

# Match Kubernetes-style names: lowercase letters/digits/dashes.
# Common case: pod names like `currencyservice-b68d5576c-gbl9k`.
# Also matches service / deployment / namespace names.
_K8S_NAME_RE = re.compile(r"\b([a-z][a-z0-9\-]{2,62}[a-z0-9])\b")

# Common English words that look like K8s names but aren't worth checking.
# Keep this list tight; over-pruning hides real misses.
_K8S_NAME_DENYLIST = frozenset(
    {
        "the",
        "and",
        "for",
        "from",
        "with",
        "this",
        "that",
        "into",
        "your",
        "their",
        "have",
        "will",
        "would",
        "could",
        "should",
        "been",
        "they",
        "kubectl",
        "deployment",
        "service",
        "namespace",
        "pod",
        "pods",
        "container",
        "image",
        "logs",
        "log",
        "error",
        "errors",
        "status",
        "cluster",
        "node",
        "nodes",
        "label",
        "labels",
        "value",
        "values",
        "config",
        "configmap",
        "secret",
        "secrets",
        "yaml",
        "json",
        "true",
        "false",
        "null",
        "none",
        "exit",
        "code",
        "boutique",
        "investigation",
        "investigate",
        "diagnose",
        "report",
        "summary",
        "cause",
        "causes",
        "root",
        "ground",
        "truth",
        "evidence",
        "finding",
        "findings",
        "claim",
        "claims",
        # Kubernetes states / common error strings — appear in any finding
        "running",
        "ready",
        "available",
        "pending",
        "crashloopbackoff",
        "errimagepull",
        "imagepullbackoff",
        "createcontainerconfigerror",
        "oomkilled",
        "completed",
        "failed",
        "evicted",
        "terminated",
    }
)


def _extract_k8s_candidates(text: str) -> set[str]:
    """Return distinct K8s-name-shaped tokens from ``text``, minus the denylist.

    Heuristic — over-collects (anything matching the regex) then filters
    via denylist. Honest scoring matters more than coverage; rather miss
    some entities than over-claim grounding.
    """
    matches = {m.group(1) for m in _K8S_NAME_RE.finditer(text.lower())}
    return {m for m in matches if m not in _K8S_NAME_DENYLIST}


# --------------------------------------------------------------------------- #
# kubectl parser                                                              #
# --------------------------------------------------------------------------- #

# Verbs kubectl accepts (subset). Restricting to known verbs catches obvious
# hallucinations like "kubectl pull" or "kubectl fix" without false positives.
_KUBECTL_VERBS = frozenset(
    {
        "get",
        "describe",
        "logs",
        "apply",
        "delete",
        "create",
        "patch",
        "edit",
        "rollout",
        "scale",
        "exec",
        "port-forward",
        "expose",
        "label",
        "annotate",
        "wait",
        "explain",
        "set",
        "top",
        "taint",
        "cordon",
        "uncordon",
        "drain",
        "version",
        "config",
        "cluster-info",
    }
)

# Resources kubectl knows about. Standard k8s objects only.
_KUBECTL_RESOURCES = frozenset(
    {
        "pod",
        "pods",
        "po",
        "service",
        "services",
        "svc",
        "deployment",
        "deployments",
        "deploy",
        "namespace",
        "namespaces",
        "ns",
        "configmap",
        "configmaps",
        "cm",
        "secret",
        "secrets",
        "node",
        "nodes",
        "no",
        "event",
        "events",
        "ev",
        "replicaset",
        "replicasets",
        "rs",
        "statefulset",
        "statefulsets",
        "sts",
        "daemonset",
        "daemonsets",
        "ds",
        "job",
        "jobs",
        "cronjob",
        "cronjobs",
        "cj",
        "ingress",
        "ingresses",
        "ing",
        "persistentvolumeclaim",
        "persistentvolumeclaims",
        "pvc",
        "persistentvolume",
        "persistentvolumes",
        "pv",
        "serviceaccount",
        "serviceaccounts",
        "sa",
        "role",
        "roles",
        "rolebinding",
        "rolebindings",
        "clusterrole",
        "clusterroles",
        "clusterrolebinding",
        "clusterrolebindings",
        "networkpolicy",
        "networkpolicies",
        "netpol",
        "storageclass",
        "storageclasses",
        "sc",
        "endpoint",
        "endpoints",
        "ep",
    }
)

_KUBECTL_LINE_RE = re.compile(r"(?:^|\s)kubectl\s+([^\n`]+)", re.MULTILINE)


def _parse_kubectl_command(args: str) -> tuple[str | None, str | None]:
    """Crude kubectl parser → (verb, resource_type) tuple or (None, None).

    Skips `-n <ns>` and other flags. Handles common forms:
        kubectl get pods
        kubectl -n boutique describe pod my-pod
        kubectl rollout status deploy/foo

    Compound forms (rollout status, top pod, etc.) treat the SECOND token
    after the verb as the resource if the first is a known sub-action.
    """
    tokens = [t for t in args.strip().split() if not t.startswith("-")]
    if not tokens:
        return None, None
    verb = tokens[0]
    if verb not in _KUBECTL_VERBS:
        return None, None
    # Skip flag values (very crude; misses cases like --namespace=foo)
    rest = tokens[1:]
    if not rest:
        return verb, None
    # Handle "rollout status deploy/foo" — second token is sub-action, third is resource
    sub_actions = {"status", "history", "undo", "pause", "resume", "restart"}
    if verb == "rollout" and rest and rest[0] in sub_actions:
        rest = rest[1:]
    if not rest:
        return verb, None
    target = rest[0]
    # Strip resource/name combo "deploy/foo" → resource is "deploy"
    if "/" in target:
        target = target.split("/")[0]
    return verb, target


# --------------------------------------------------------------------------- #
# Backend universe — what really exists in the State Snapshot                  #
# --------------------------------------------------------------------------- #


def _backend_universe(backend: CloudOpsBenchReplayBackend, namespace: str) -> set[str]:
    """Collect all entity names that exist in the snapshot for ``namespace``.

    Best-effort: pulls pod names, deployment names, service names from
    the replay backend. Returns the union.

    Errors from backend calls are swallowed (snapshot may not include all
    resource types per case) — better to under-claim than to false-positive.
    """
    universe: set[str] = set()
    for getter in (backend.list_pods, backend.list_deployments):
        try:
            result = getter(namespace=namespace)
        except Exception:
            continue
        for item in _iter_resource_names(result):
            universe.add(item.lower())
    return universe


def _iter_resource_names(payload: Any) -> list[str]:
    """Best-effort: pull resource names from a backend method's return shape.

    Replay backend returns dicts of various shapes. Look in common spots.
    """
    out: list[str] = []
    if not isinstance(payload, dict):
        return out
    items = payload.get("items") or payload.get("data") or []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        # Look in metadata.name (standard K8s shape) or top-level name
        meta = item.get("metadata") or {}
        name = (meta.get("name") if isinstance(meta, dict) else None) or item.get("name")
        if isinstance(name, str) and name:
            out.append(name)
    return out


def _evidence_corpus(evidence_entries: list[dict[str, Any]]) -> str:
    """Concatenate everything the agent saw into a single searchable string."""
    parts: list[str] = []
    for entry in evidence_entries:
        for value in entry.values():
            parts.append(repr(value))
    return " ".join(parts).lower()


# --------------------------------------------------------------------------- #
# The three metrics                                                           #
# --------------------------------------------------------------------------- #


def compute_citation_grounding(finding_text: str, evidence_entries: list[dict[str, Any]]) -> float:
    """Fraction of specific entities in the finding that appear in evidence.

    Heuristic: extract K8s-name-shaped tokens from finding; check each
    against the concatenation of all the agent's tool outputs. If the
    agent invented a name from parametric knowledge, it won't be in the
    evidence corpus → score drops.

    Returns 1.0 when finding has no entities (vacuously grounded);
    callers should weight scoring accordingly.
    """
    candidates = _extract_k8s_candidates(finding_text)
    if not candidates:
        return 1.0
    corpus = _evidence_corpus(evidence_entries)
    grounded = sum(1 for c in candidates if c in corpus)
    return grounded / len(candidates)


def compute_entity_existence(
    finding_text: str, backend: CloudOpsBenchReplayBackend, namespace: str
) -> float:
    """Fraction of K8s entity names in the finding that exist in the snapshot.

    Stronger than citation_grounding — checks against the real cluster
    state, not just what the agent retrieved. Catches names the agent
    invents AND missed during investigation.

    Returns 1.0 when finding has no entity candidates (vacuously valid).
    """
    candidates = _extract_k8s_candidates(finding_text)
    if not candidates:
        return 1.0
    universe = _backend_universe(backend, namespace)
    if not universe:
        # If we can't determine the universe, don't penalize the finding.
        # This conservative default protects against backend-API gaps.
        return 1.0
    real = sum(1 for c in candidates if c in universe)
    return real / len(candidates)


def compute_kubectl_actionability(finding_text: str) -> float:
    """Fraction of ``kubectl`` commands in the finding that parse + target
    a valid K8s resource type.

    v1 is syntactic only — doesn't verify resource names exist (that's
    closer to entity_existence's job). Catches things like:
        - "kubectl fix-cluster pods" → unknown verb
        - "kubectl get widgets" → unknown resource

    Returns 1.0 when finding has no kubectl commands (vacuously valid).
    """
    commands = _KUBECTL_LINE_RE.findall(finding_text)
    if not commands:
        return 1.0
    actionable = 0
    for cmd in commands:
        verb, resource = _parse_kubectl_command(cmd)
        if verb is None:
            continue
        # Verbs that don't need a resource type (e.g., version, cluster-info)
        if verb in {"version", "cluster-info", "config", "explain"}:
            actionable += 1
            continue
        if resource is not None and resource.lower() in _KUBECTL_RESOURCES:
            actionable += 1
    return actionable / len(commands)
