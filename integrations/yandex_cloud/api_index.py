"""Lookup over every readable Yandex Cloud REST endpoint.

The data is generated from the protobuf definitions in yandex-cloud/cloudapi,
where each method carries its REST binding as a ``google.api.http`` option — so
this is what the API actually exposes rather than a hand-kept list that drifts.
Regenerate with ``build_api_index.py`` next to this file after Yandex ships new
services;
:func:`provenance` reports which cloudapi commit the current data came from.

Only read bindings are present. The agent has no mutating path at all, so an
index that also listed writes would just invite it to attempt one.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, NamedTuple

logger = logging.getLogger(__name__)

_INDEX_FILE: Final = Path(__file__).with_name("api_index.json")
#: Enough to show every shape of a resource without flooding the context.
DEFAULT_LIMIT: Final = 25


class ApiEndpoint(NamedTuple):
    """One readable REST endpoint, ready to hand to ``execute_yc_operation``."""

    service: str
    path: str
    rpc: str
    resource: str
    #: Query parameters beyond the path, each ``{"name", "required"?, "values"?}``.
    params: tuple[dict[str, Any], ...] = ()

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "service": self.service,
            "path": self.path,
            "rpc": self.rpc,
        }
        if self.params:
            # A path without its parameters is half an answer: the cluster-logs
            # endpoint answers "unknown service type" until serviceType is passed,
            # and nothing about the path says so.
            payload["params"] = [dict(param) for param in self.params]
        return payload


@lru_cache(maxsize=1)
def _raw_index() -> dict[str, Any]:
    try:
        loaded: dict[str, Any] = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # An empty index is indistinguishable from "Yandex exposes nothing", and
        # the tool then reports no endpoints instead of a broken install. The
        # usual cause is a packaging regression that leaves the data file out of
        # the artifact, so say which file is missing rather than degrade quietly.
        logger.warning("Yandex Cloud API index unavailable at %s: %s", _INDEX_FILE, exc)
        return {}
    return loaded


def provenance() -> dict[str, str]:
    """Return which cloudapi commit produced the index, and when it was built.

    An index generated from protobuf definitions is only as current as the
    checkout it came from, and nothing about the endpoints themselves says how
    old they are. Keys: ``source``, ``commit``, ``commit_date``, ``generated``.
    """
    return {key: str(value) for key, value in _raw_index().items() if key != "endpoints"}


@lru_cache(maxsize=1)
def _endpoints() -> tuple[ApiEndpoint, ...]:
    raw = _raw_index()
    return tuple(
        ApiEndpoint(
            service=str(item.get("service", "")),
            path=str(item.get("path", "")),
            rpc=str(item.get("rpc", "")),
            resource=str(item.get("resource", "")),
            params=tuple(item.get("params") or ()),
        )
        for item in raw.get("endpoints", ())
    )


_WORD_BOUNDARY = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")

#: What operators say versus what the API calls it. Without these, "k8s nodes"
#: and "vm" find nothing at all, which reads as "the API cannot do that".
_ALIASES: Final[dict[str, str]] = {
    "k8s": "kubernetes",
    "kube": "kubernetes",
    "vm": "instance",
    "vms": "instances",
    "pg": "postgresql",
    "postgres": "postgresql",
    "ch": "clickhouse",
    "mongo": "mongodb",
    "lb": "loadbalancer",
    "balancer": "loadbalancers",
    "sg": "security",
    "bucket": "buckets",
    "func": "functions",
    "lambda": "functions",
    "registry": "registries",
    "secret": "secrets",
    "cert": "certificates",
    "certs": "certificates",
}


def _tokenize(text: str) -> frozenset[str]:
    """Split on punctuation and camelCase: ``instanceGroups`` → {instance, groups}."""
    return frozenset(part.lower() for part in _WORD_BOUNDARY.split(text) if part)


@lru_cache(maxsize=1)
def _haystacks() -> tuple[tuple[ApiEndpoint, frozenset[str]], ...]:
    """Endpoints paired with their searchable tokens, split once rather than per query.

    Tokens rather than raw text because substring matching quietly lies: a search
    for ``logs`` matches ``/v1/catalogs``, and the agent then calls a data-catalog
    endpoint believing it asked for logs.
    """
    return tuple(
        (endpoint, _tokenize(f"{endpoint.service} {endpoint.path} {endpoint.rpc}"))
        for endpoint in _endpoints()
    )


@lru_cache(maxsize=1)
def known_services() -> tuple[str, ...]:
    """Every service the index can read from, in stable order."""
    return tuple(sorted({endpoint.service for endpoint in _endpoints()}))


def endpoint_count() -> int:
    return len(_endpoints())


def _term_hits(term: str, tokens: frozenset[str]) -> bool:
    """Whether *term* names one of *tokens*, allowing for plurals and shorthand."""
    if term in tokens:
        return True
    return any(
        token.startswith(term) or (len(token) >= 3 and term.startswith(token)) for token in tokens
    )


def _matches(tokens: frozenset[str], terms: list[str]) -> bool:
    return all(_term_hits(term, tokens) for term in terms)


def _normalize(terms: list[str]) -> list[str]:
    return [_ALIASES.get(term, term) for term in terms]


@lru_cache(maxsize=1)
def _service_sizes() -> dict[str, int]:
    """How much of the API each service owns, used only to break ties."""
    sizes: dict[str, int] = {}
    for endpoint in _endpoints():
        sizes[endpoint.service] = sizes.get(endpoint.service, 0) + 1
    return sizes


def _rank(endpoint: ApiEndpoint, terms: list[str]) -> tuple[Any, ...]:
    """Order candidates so the first one is the one worth calling.

    Several services own a resource of the same name — ``instances`` exists in
    both ``compute`` and ``gitlab`` — and the terms alone cannot separate them.
    The tie is broken by how much of the API a service owns, which stands in for
    how central it is: asked about instances with nothing else to go on, compute
    is the better guess than a managed GitLab nobody mentioned.
    """
    resource_tokens = _tokenize(endpoint.resource)
    service_tokens = _tokenize(endpoint.service)
    return (
        # A term naming the service is the strongest signal there is.
        not any(_term_hits(term, service_tokens) for term in terms),
        # Then an exact resource hit, over a term merely appearing in the path.
        not any(_term_hits(term, resource_tokens) for term in terms),
        # A read needing an id is useless until a list call has supplied one.
        "{" in endpoint.path,
        -_service_sizes().get(endpoint.service, 0),
        len(endpoint.path),
        endpoint.path,
    )


def search(
    query: str = "",
    *,
    service: str = "",
    limit: int = DEFAULT_LIMIT,
) -> list[ApiEndpoint]:
    """Return endpoints matching every whitespace-separated term in *query*."""
    terms = _normalize([term for term in _tokenize(query) if term])
    wanted_service = service.strip().lower()

    found = [
        endpoint
        for endpoint, tokens in _haystacks()
        if (not wanted_service or endpoint.service == wanted_service) and _matches(tokens, terms)
    ]
    found.sort(key=lambda endpoint: _rank(endpoint, terms))
    return found[: max(1, limit)]


def _segment_matches(template: str, actual: str) -> bool:
    """Whether one path segment of *actual* is the templated *template* segment.

    Yandex binds some reads as an action on a resource
    (``{instance_id}:serialPortOutput``). The placeholder and the action suffix
    have to match independently, otherwise a list path would also accept the
    action and the other way around.
    """
    template_name, _, template_action = template.partition(":")
    actual_name, _, actual_action = actual.partition(":")
    if template_action != actual_action:
        return False
    if template_name == actual_name:
        return True
    return (
        template_name.startswith("{")
        and template_name.endswith("}")
        and bool(actual_name)
        and "/" not in actual_name
    )


def _path_matches(template: str, actual: str) -> bool:
    actual = actual.split("?", 1)[0].rstrip("/") or "/"
    template = template.rstrip("/") or "/"
    template_parts = [part for part in template.split("/") if part]
    actual_parts = [part for part in actual.split("/") if part]
    if len(template_parts) != len(actual_parts):
        return False
    return all(
        _segment_matches(template_part, actual_part)
        for template_part, actual_part in zip(template_parts, actual_parts, strict=True)
    )


#: Where a tool names a service differently from the index. The tools say "alb",
#: while the protos - and so the index built from them - say "apploadbalancer".
#: Without this the gate sees an unknown service and waves the path through.
_SERVICE_ALIASES: Final[dict[str, str]] = {"alb": "apploadbalancer"}


def canonical_service(service: str) -> str:
    """Return the name the index knows this service by.

    Case-folded because the index is lowercase throughout while host resolution
    lowercases what it is given: a caller saying "COMPUTE" would otherwise read
    as an unknown service to the gate and as the real host to the client.
    """
    wanted = service.strip().lower()
    return _SERVICE_ALIASES.get(wanted, wanted)


def lookup(service: str, path: str) -> ApiEndpoint | None:
    """Return the indexed read that *path* is, or ``None`` if it is not one.

    The generic reader may call only what this index lists. The index is built
    from GET bindings, so matching it is the read-only allowlist, not merely a
    convenience for discovery. A concrete path such as
    ``/compute/v1/instances/abc`` matches ``/compute/v1/instances/{instance_id}``.
    """
    wanted = canonical_service(service)
    if not wanted or not path:
        return None
    for endpoint in _endpoints():
        if endpoint.service == wanted and _path_matches(endpoint.path, path):
            return endpoint
    return None
